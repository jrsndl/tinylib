"""Portable library records, legacy database adapter, and safe JSON publication."""
import copy
import json
import os
import re
import shlex
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

DATABASE_FILENAME = 'tinylib_data.json'

def read_json(path):
    with open(path, encoding='utf-8-sig') as stream:
        return json.load(stream)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def library_lock(root, timeout=15):
    """Exclusive create works across processes and SMB; never break another writer's lock."""
    lock = Path(root) / '.tinylib-write.lock'
    end = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= end:
                raise RuntimeError('Library is locked by another writer: ' + str(lock))
            time.sleep(.1)
    try:
        os.write(fd, ('PID %s\n' % os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink()


def split_sequence(value):
    match = re.match(r'^(.*?)\s+(-?\d+)-(-?\d+)$', str(value))
    if match:
        return match[1], int(match[2]), int(match[3])
    return str(value), None, None


def frame_path(pattern, frame):
    if re.search(r'#+', pattern):
        return re.sub(r'#+', lambda m: str(frame).zfill(len(m[0])), pattern)
    return re.sub(r'%0?(\d*)d', lambda m: str(frame).zfill(int(m[1] or 1)), pattern)


def sequence_files(value):
    """Accept explicit ####/%04d ranges, a still, or a member of a sequence."""
    pattern, first, last = split_sequence(value)
    token = re.search(r'#+|%0?\d*d', pattern)
    if token:
        if first is None or last is None or last < first:
            raise ValueError('Sequences need a frame range, e.g. smoke.####.exr 1001-1100')
        files = [Path(frame_path(pattern, frame)) for frame in range(first, last + 1)]
    else:
        path = Path(pattern)
        if not path.is_file():
            raise ValueError('Source file does not exist: ' + pattern)
        # A single explicitly selected file remains a still. GUI offers sequence detection.
        files = [path]
    missing = next((p for p in files if not p.is_file()), None)
    if missing:
        raise ValueError('Missing source frame: ' + str(missing))
    return files


def detect_sequence(filename):
    path = Path(filename)
    match = re.match(r'^(.*?)(\d+)(\.[^.]+)$', path.name)
    if not match:
        return str(path)
    expression = re.compile(re.escape(match[1]) + r'(\d{' + str(len(match[2])) + r'})' + re.escape(match[3]) + '$')
    frames = sorted(int(m[1]) for p in path.parent.iterdir() if (m := expression.match(p.name)))
    if len(frames) < 2:
        return str(path)
    if frames != list(range(frames[0], frames[-1] + 1)):
        raise ValueError('Sequence has missing frames; supply an explicit contiguous range.')
    return str(path.with_name(match[1] + '#' * len(match[2]) + match[3])) + ' %s-%s' % (frames[0], frames[-1])


def safe_component(value):
    value = value.strip()
    if (not value or value in {'.', '..'} or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value.endswith(('.', ' ')) or value.split('.')[0].upper() in
            {'CON', 'PRN', 'AUX', 'NUL', *('COM%d' % i for i in range(1, 10)), *('LPT%d' % i for i in range(1, 10))}):
        raise ValueError('Invalid folder name: ' + repr(value))
    return value


class Library:
    def __init__(self, root, name=None, legacy_roots=(), index_cache_root=None):
        self.root = Path(root)
        self.name = name or self.root.name
        self.legacy_roots = [str(p).replace('\\', '/').rstrip('/') for p in legacy_roots]
        self.index_cache_root = index_cache_root
        self.index_cache_hit = False
        self.assets = []

    def resolve(self, value):
        if not value:
            return ''
        value = str(value).replace('\\', '/')
        for old in self.legacy_roots:
            if value.casefold().startswith(old.casefold() + '/'):
                return str(self.root / value[len(old) + 1:]).replace('\\', '/')
        if re.match(r'^[A-Za-z]:/|^/', value):
            return value
        return str(self.root / value).replace('\\', '/')

    def load(self, progress=None, use_cache=True):
        self.index_cache_hit = False
        database = self.root / DATABASE_FILENAME
        if not database.exists():
            if not self.root.is_dir():
                raise ValueError('Library is unavailable: ' + str(self.root))
            self.assets = self.scan()
            return self.assets
        cache_path = None
        if use_cache:
            try:
                from .thumbnail_cache import cache_root, cached_library_index_path
                local_root = self.index_cache_root or cache_root()
                cache_path = cached_library_index_path(local_root, database, self.root,
                                                       self.name, self.legacy_roots)
                if cache_path.is_file():
                    cached = read_json(cache_path)
                    if cached.get('index_cache_version') == 1:
                        self.assets = cached['assets']
                        self.index_cache_hit = True
                        return self.assets
            except (OSError, ValueError, KeyError, TypeError):
                cache_path = None
        data = read_json(database)
        if data.get('schema_version') == 3:
            records = data['assets']
        elif 'data' in data:
            records = []
            for group, categories in data['data'].items():
                for category, entries in categories.items():
                    for entry in entries:
                        record = copy.deepcopy(entry)
                        source, first, last = split_sequence(record.get('source', ''))
                        record.update(main=source, first=first, last=last,
                                      category=category.replace('root|', '').replace('|', '/'))
                        record['name'] = source.replace('\\', '/').split('/main/')[0].split('/')[-1]
                        record['kind'] = 'footage' if first is not None else 'still'
                        record['thumb'] = record.pop('proxy', '')
                        record['colorspace'] = record.get('colorspace', 'ACEScg')
                        record['id'] = group + '/' + category + '/' + record['name']
                        records.append(record)
        else:
            raise ValueError('Unsupported database schema: ' + str(database))
        self.assets = []
        total = len(records)
        for index, record in enumerate(records, 1):
            self.assets.append(self.normalize(record))
            if progress and (index == 1 or index == total or index % 50 == 0):
                progress(index, total)
        if cache_path is None:
            try:
                from .thumbnail_cache import cache_root, cached_library_index_path
                local_root = self.index_cache_root or cache_root()
                cache_path = cached_library_index_path(local_root, database, self.root,
                                                       self.name, self.legacy_roots)
            except OSError:
                cache_path = None
        if cache_path:
            try:
                atomic_json(cache_path, {'index_cache_version': 1, 'assets': self.assets})
            except OSError:
                pass
        return self.assets

    def normalize(self, record):
        from .asset_types import canonical_type
        result = copy.deepcopy(record)
        for key in ('main', 'thumb', 'proxy', 'filmstrip', 'highres', 'scene'):
            result[key] = self.resolve(result.get(key, ''))
        asset_dir = Path(result['main']).parent if result.get('kind') == 'folder' else Path(result['main']).parent.parent
        missing = [key for key in ('thumb', 'proxy', 'filmstrip', 'highres', 'scene') if not result[key]]
        folders = {}
        if missing:
            try:
                folders = {item.name.casefold(): item for item in asset_dir.iterdir() if item.is_dir()}
            except OSError:
                pass
        for key in missing:
            folder = folders.get(key)
            if folder:
                try:
                    if result['kind'] == 'folder' and key in ('proxy', 'highres'):
                        result[key] = str(folder).replace('\\', '/')
                    else:
                        result[key] = next((str(p).replace('\\', '/') for p in sorted(folder.iterdir()) if p.is_file()), '')
                except OSError:
                    pass
        result.setdefault('tags', [])
        result.setdefault('kind', 'still')
        result['kind'] = canonical_type(result['kind'])
        result.setdefault('metadata', {})
        result.setdefault('colorspace', 'ACEScg')
        result['library'] = self.name
        result['library_root'] = str(self.root)
        return result

    def scan(self):
        from .asset_types import detect_type
        records = []
        # Supports category/asset and a one-level flat library.
        folders = list(self.root.glob('*/main')) + list(self.root.glob('*/*/main'))
        for folder in sorted(folders):
            if any(part.startswith('.') for part in folder.relative_to(self.root).parts):
                continue
            sources = sorted(p for p in folder.iterdir() if p.is_file())
            if not sources:
                continue
            try:
                kind = detect_type(sources[0])
                source = detect_sequence(sources[0]) if kind == 'still' else str(sources[0])
                if split_sequence(source)[1] is not None:
                    kind = detect_type(source, is_sequence=True)
            except ValueError:
                continue
            main, first, last = split_sequence(source)
            asset = folder.parent
            relative = asset.relative_to(self.root)
            category = relative.parts[0] if len(relative.parts) > 1 else 'HDRI'
            records.append(self.normalize(dict(id=relative.as_posix(), name=asset.name,
                category=category, main=main, first=first, last=last,
                kind=kind,
                tags=[t.lower() for t in re.split(r'[_\s-]+', asset.name) if t], metadata={})))
        return records

    def portable(self, record):
        result = copy.deepcopy(record)
        for key in list(result):
            if key.startswith('_'):
                result.pop(key)
        for key in ('library', 'library_root'):
            result.pop(key, None)
        for key in ('main', 'thumb', 'proxy', 'filmstrip', 'highres', 'scene'):
            if result.get(key):
                try:
                    result[key] = Path(result[key]).relative_to(self.root).as_posix()
                except ValueError:
                    pass
        return result

    def update_asset(self, original, changes, edit_token=None):
        """Patch changed metadata under lock; reject stale writes to the same field."""
        return self.update_assets([(original, changes)], edit_token=edit_token)[0]

    def update_assets(self, edits, edit_token=None):
        """Apply multiple optimistic patches in one locked, atomic database write."""
        from .asset_edit import validate_changes
        for original, changes in edits:
            validate_changes(original, changes)
        with library_lock(self.root):
            from .edit_lock import assert_edit_write_allowed
            assert_edit_write_allowed(self.root, edit_token)
            self.load(use_cache=False)
            updated = []
            changed = False
            for original, changes in edits:
                matches = [asset for asset in self.assets
                           if asset['id'] == original['id'] and asset['main'] == original['main']]
                if len(matches) != 1:
                    raise ValueError('Asset changed or disappeared. Refresh the library before editing.')
                current = matches[0]
                for field in changes:
                    if current.get(field) != original.get(field):
                        raise ValueError('Another user changed %s. Refresh before saving.' % field)
                if changes:
                    current.update(copy.deepcopy(changes))
                    validate_changes(current, {})
                    changed = True
                updated.append(current)
            if not changed:
                return updated
            database = self.root / DATABASE_FILENAME
            document = read_json(database) if database.exists() else {}
            if document and document.get('schema_version') != 3:
                backup = self.root / 'data.legacy.backup.json'
                if not backup.exists():
                    with open(backup, 'xb') as stream:
                        stream.write(database.read_bytes())
                document = {}
            document.update(schema_version=3, asset_type_schema_version=1,
                            assets=[self.portable(asset) for asset in self.assets])
            atomic_json(database, document)
            return updated

    def publish(self, record, staging=None, edit_token=None):
        """Reread under lock, preserve legacy DB backup, publish completed assets only."""
        with library_lock(self.root):
            from .edit_lock import assert_edit_write_allowed
            assert_edit_write_allowed(self.root, edit_token)
            self.load()
            if any(a['id'] == record['id'] for a in self.assets):
                raise ValueError('Asset already exists in database: ' + record['id'])
            database = self.root / DATABASE_FILENAME
            if database.exists() and read_json(database).get('schema_version') != 3:
                backup = self.root / 'data.legacy.backup.json'
                if not backup.exists():
                    with open(backup, 'xb') as stream:
                        stream.write(database.read_bytes())
            destination = self.root / record['category'] / record['name']
            if staging:
                if destination.exists():
                    raise ValueError('Asset folder already exists: ' + str(destination))
                destination.parent.mkdir(parents=True, exist_ok=True)
                Path(staging).rename(destination)
            try:
                atomic_json(database, {'schema_version': 3, 'asset_type_schema_version': 1,
                    'assets': [self.portable(a) for a in self.assets] + [self.portable(record)]})
            except Exception:
                if staging:
                    destination.rename(staging)
                raise


def matches(asset, query='', category='', kind='', tag=''):
    if category and not (asset['category'] == category or asset['category'].startswith(category + '/')):
        return False
    if kind and asset.get('kind') != kind:
        return False
    tags = [str(t).casefold() for t in asset.get('tags', [])]
    if tag and tag.casefold() not in tags:
        return False
    haystack = ' '.join([asset.get('name', ''), asset.get('category', ''), asset.get('library', ''), *tags]).casefold()
    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()
    for token in tokens:
        excluded = token.startswith('-') and len(token) > 1
        term = token[1:] if excluded else token
        found = term[4:].casefold() in tags if term.lower().startswith('tag:') else term.casefold() in haystack
        if found == excluded:
            return False
    return True
