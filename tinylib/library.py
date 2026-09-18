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

IMAGE_EXTENSIONS = {'.exr', '.hdr', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.dpx'}


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
    def __init__(self, root, name=None, legacy_roots=()):
        self.root = Path(root)
        self.name = name or self.root.name
        self.legacy_roots = [str(p).replace('\\', '/').rstrip('/') for p in legacy_roots]
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

    def load(self):
        database = self.root / 'data.json'
        if not database.exists():
            if not self.root.is_dir():
                raise ValueError('Library is unavailable: ' + str(self.root))
            self.assets = self.scan()
            return self.assets
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
        self.assets = [self.normalize(r) for r in records]
        return self.assets

    def normalize(self, record):
        result = copy.deepcopy(record)
        for key in ('main', 'thumb', 'proxy', 'filmstrip', 'highres'):
            result[key] = self.resolve(result.get(key, ''))
        asset_dir = Path(result['main']).parent.parent
        for key in ('thumb', 'proxy', 'filmstrip', 'highres'):
            if not result[key]:
                folder = asset_dir / key
                if folder.is_dir():
                    result[key] = next((str(p).replace('\\', '/') for p in sorted(folder.iterdir()) if p.is_file()), '')
        result.setdefault('tags', [])
        result.setdefault('metadata', {})
        result.setdefault('colorspace', 'ACEScg')
        result.setdefault('kind', 'still')
        result['library'] = self.name
        result['library_root'] = str(self.root)
        return result

    def scan(self):
        records = []
        # Supports category/asset and the supplied flat HDRI library.
        folders = list(self.root.glob('*/main')) + list(self.root.glob('*/*/main'))
        for folder in sorted(folders):
            if any(part.startswith('.') for part in folder.relative_to(self.root).parts):
                continue
            sources = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
            if not sources:
                continue
            source = detect_sequence(sources[0])
            main, first, last = split_sequence(source)
            asset = folder.parent
            relative = asset.relative_to(self.root)
            category = relative.parts[0] if len(relative.parts) > 1 else 'HDRI'
            records.append(self.normalize(dict(id=relative.as_posix(), name=asset.name,
                category=category, main=main, first=first, last=last,
                kind='footage' if first is not None else 'still',
                tags=[t.lower() for t in re.split(r'[_\s-]+', asset.name) if t], metadata={})))
        return records

    def portable(self, record):
        result = copy.deepcopy(record)
        for key in list(result):
            if key.startswith('_'):
                result.pop(key)
        for key in ('library', 'library_root'):
            result.pop(key, None)
        for key in ('main', 'thumb', 'proxy', 'filmstrip', 'highres'):
            if result.get(key):
                try:
                    result[key] = Path(result[key]).relative_to(self.root).as_posix()
                except ValueError:
                    pass
        return result

    def update_asset(self, original, changes):
        """Patch changed metadata under lock; reject stale writes to the same field."""
        from .asset_edit import validate_changes
        validate_changes(original, changes)
        with library_lock(self.root):
            self.load()
            matches = [asset for asset in self.assets if asset['id'] == original['id'] and asset['main'] == original['main']]
            if len(matches) != 1:
                raise ValueError('Asset changed or disappeared. Refresh the library before editing.')
            current = matches[0]
            for field in changes:
                if current.get(field) != original.get(field):
                    raise ValueError('Another user changed %s. Refresh before saving.' % field)
            if not changes:
                return current
            current.update(copy.deepcopy(changes))
            validate_changes(current, {})
            database = self.root / 'data.json'
            document = read_json(database) if database.exists() else {}
            if document and document.get('schema_version') != 3:
                backup = self.root / 'data.legacy.backup.json'
                if not backup.exists():
                    with open(backup, 'xb') as stream:
                        stream.write(database.read_bytes())
                document = {}
            document.update(schema_version=3, assets=[self.portable(asset) for asset in self.assets])
            atomic_json(database, document)
            return current

    def publish(self, record, staging=None):
        """Reread under lock, preserve legacy DB backup, publish completed assets only."""
        with library_lock(self.root):
            self.load()
            if any(a['id'] == record['id'] for a in self.assets):
                raise ValueError('Asset already exists in database: ' + record['id'])
            database = self.root / 'data.json'
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
                atomic_json(database, {'schema_version': 3,
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
