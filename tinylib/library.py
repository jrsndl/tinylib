"""Portable library records, legacy database adapter, and safe JSON publication."""
import copy
import os
import re
import shlex
import shutil
import stat
import sys
from pathlib import Path
from .search import parse_query, matches_fields
# Storage helpers live in storage.py; re-exported because many modules import them from here.
from .storage import atomic_json, library_lock, read_json  # noqa: F401
from .thumbnail_cache import INDEX_CACHE_VERSION, cache_root, cached_library_index_path

DATABASE_FILENAME = 'tinylib_data.json'

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


def _legacy_frame_rate(metadata):
    """Old databases stored FPS (sometimes as '24000/1001'); the schema key is frame_rate."""
    if not isinstance(metadata, dict) or 'frame_rate' in metadata or 'FPS' not in metadata:
        return None
    from fractions import Fraction
    try:
        value = float(Fraction(str(metadata['FPS'])))
    except (ValueError, ZeroDivisionError):
        return None
    return value if 0 < value < 1000 else None


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
                local_root = self.index_cache_root or cache_root()
                cache_path = cached_library_index_path(local_root, database, self.root,
                                                       self.name, self.legacy_roots)
                if cache_path.is_file():
                    cached = read_json(cache_path)
                    if cached.get('index_cache_version') == INDEX_CACHE_VERSION:
                        try:
                            os.utime(cache_path)  # Mark as used for the cache prune.
                        except OSError:
                            pass
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
                local_root = self.index_cache_root or cache_root()
                cache_path = cached_library_index_path(local_root, database, self.root,
                                                       self.name, self.legacy_roots)
            except OSError:
                cache_path = None
        if cache_path:
            try:
                atomic_json(cache_path, {'index_cache_version': INDEX_CACHE_VERSION, 'assets': self.assets})
            except OSError:
                pass
        return self.assets

    def normalize(self, record, discover_media=True):
        from .asset_types import canonical_type
        from .asset_fields import apply_defaults
        result = copy.deepcopy(record)
        apply_defaults(result)
        for key in ('main', 'main-original', 'thumb', 'proxy', 'filmstrip', 'highres', 'scene'):
            result[key] = self.resolve(result.get(key, ''))
        asset_dir = Path(result['main']).parent if result.get('kind') == 'folder' else Path(result['main']).parent.parent
        missing = [key for key in ('thumb', 'proxy', 'filmstrip', 'highres', 'scene') if not result[key]]
        folders = {}
        if missing and discover_media:
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
        legacy_rate = _legacy_frame_rate(result['metadata'])
        if legacy_rate is not None:
            # Migrate rather than duplicate: the properties panel would otherwise show both.
            result['metadata']['frame_rate'] = legacy_rate
            result['metadata'].pop('FPS', None)
        # Older ingests also stored the color space in metadata; the asset field is the one
        # that is edited and used, so the copy is dropped (and leaves the database on the next write).
        legacy_space = result['metadata'].pop('colorspace', None)
        if not result.get('colorspace') and isinstance(legacy_space, str) and legacy_space.strip():
            result['colorspace'] = legacy_space.strip()
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
            # Older rescans wrote <name>_meta.json sidecars into main/; they are not media.
            sources = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() != '.json')
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
        from .asset_fields import apply_defaults
        result = copy.deepcopy(record)
        apply_defaults(result)
        for key in list(result):
            if key.startswith('_'):
                result.pop(key)
        for key in ('library', 'library_root'):
            result.pop(key, None)
        for key in ('main', 'main-original', 'thumb', 'proxy', 'filmstrip', 'highres', 'scene'):
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
            database = self.root / DATABASE_FILENAME
            document = read_json(database) if database.exists() else {}
            if document.get('schema_version') == 3:
                return self._update_json_assets(document, edits)
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

    def _update_json_assets(self, document, edits):
        """Patch current JSON records without probing unrelated media folders.

        The caller holds both the write lock and the validated edit token.
        """
        from .asset_edit import validate_changes
        from .asset_fields import apply_defaults
        records = document['assets']
        lookup = {}
        for record in records:
            lookup.setdefault((record['id'], self.resolve(record.get('main', ''))), []).append(record)
        updated, changed = [], False
        for original, changes in edits:
            matches = lookup.get((original['id'], original['main']), [])
            if len(matches) != 1:
                raise ValueError('Asset changed or disappeared. Refresh the library before editing.')
            record = matches[0]
            current = self.normalize(record, discover_media=False)
            for field in changes:
                if current.get(field) != original.get(field):
                    raise ValueError('Another user changed %s. Refresh before saving.' % field)
            # Retain already-discovered preview paths in the returned GUI record.
            for role in ('thumb', 'proxy', 'filmstrip', 'highres', 'scene'):
                if not current.get(role):
                    current[role] = original.get(role, '')
            if changes:
                current.update(copy.deepcopy(changes))
                validate_changes(current, {})
                record.update(copy.deepcopy(changes))
                apply_defaults(record)
                changed = True
            updated.append(current)
        if changed:
            document['asset_type_schema_version'] = 1
            atomic_json(self.root / DATABASE_FILENAME, document)
        return updated

    def asset_folder(self, asset):
        """The folder TinyLib ingest made for ``asset``: ``<root>/<category>/<name>``.

        Returns ``(folder, '')`` with the folder as spelled under the library root, or
        ``(None, reason)`` when deleting it could reach anything else: a name or category
        that is empty or leaves the library, a folder or category that is a symbolic link
        or junction, or an asset whose main is not in exactly that folder (older or
        hand-made layouts).
        """
        name, category = str(asset.get('name', '')), str(asset.get('category', ''))
        if not name.strip() or not category.strip() or {name, category} & {'.', '..'} \
                or re.search(r'[\\/]', name + category):
            return None, 'its name or category is not a plain folder name'
        folder = self.root / category / name
        for item in (folder.parent, folder):
            if is_link(item):
                return None, '%s is a link or junction' % item
        root = _resolved(self.root)
        resolved = _resolved(folder)
        main = Path(str(asset.get('main', '')))
        expected = _resolved(main.parent if asset.get('kind') == 'folder' else main.parent.parent)
        if resolved.parent.parent != root or expected != resolved:
            return None, 'its files are not in %s/<category>/<name>' % self.root
        return folder, ''

    def delete_assets(self, originals, edit_token=None, remove_files=False):
        """Remove assets from the database in one locked write; optionally their folders.

        Every asset must still be in the database exactly as loaded, or nothing is
        written. Files are deleted only after the database write succeeded, and only an
        asset's own ``<root>/<category>/<name>`` folder, when no asset that stays has its
        main in, at or above that folder (compared after resolving links, drive mappings
        and short names). Returns messages about files that were kept or not deleted.
        """
        database = self.root / DATABASE_FILENAME
        if not database.exists():
            raise ValueError('%s has no %s: it lists the asset folders on disk, so there is no database to '
                             'delete from. Move or delete the folders instead.' % (self.root, DATABASE_FILENAME))
        problems, folders = [], []
        with library_lock(self.root):
            from .edit_lock import assert_edit_write_allowed
            assert_edit_write_allowed(self.root, edit_token)
            document = read_json(database)
            if document.get('schema_version') == 3:
                records = document['assets']
                keys = [(record['id'], self.resolve(record.get('main', ''))) for record in records]
            else:
                # Legacy database: read fresh, never from the local index cache.
                records = [self.portable(asset) for asset in self.load(use_cache=False)]
                keys = [(asset['id'], asset['main']) for asset in self.assets]
            doomed = set()
            for original in originals:
                rows = [row for row, key in enumerate(keys) if key == (original['id'], original['main'])]
                if len(rows) > 1:
                    raise ValueError('%s is in the database %d times; fix the duplicates first.'
                                     % (original.get('name', original['id']), len(rows)))
                if not rows:
                    raise ValueError('%s changed or disappeared. Refresh the library before deleting.'
                                     % original.get('name', original['id']))
                doomed.add(rows[0])
            if remove_files:
                kept = [_resolved(key[1]) for row, key in enumerate(keys) if row not in doomed and key[1]]
                for original in originals:
                    folder, reason = self.asset_folder(original)
                    if folder is not None:
                        resolved = _resolved(folder)
                        # A kept main in the folder, the folder itself, or an ancestor of it.
                        if any(main == resolved or resolved in main.parents or main in resolved.parents
                               for main in kept):
                            folder, reason = None, 'another asset is stored there'
                    if folder is None:
                        problems.append('%s: files kept, %s.' % (original['name'], reason))
                        continue
                    folders.append((original, folder))
                    for role in ('main-original', 'highres', 'proxy', 'thumb', 'filmstrip', 'scene'):
                        path = original.get(role)
                        if path and resolved not in _resolved(path).parents:
                            problems.append('%s: %s kept, it is outside the asset folder: %s'
                                            % (original['name'], role, path))
            remaining = [record for row, record in enumerate(records) if row not in doomed]
            if document.get('schema_version') != 3:
                backup = self.root / 'data.legacy.backup.json'
                if not backup.exists():
                    with open(backup, 'xb') as stream:
                        stream.write(database.read_bytes())
                document = {'schema_version': 3, 'asset_type_schema_version': 1}
            document['assets'] = remaining
            atomic_json(database, document)
        for original, folder in folders:
            failed = remove_tree(folder)
            if failed:
                problems.append('%s: could not delete %d item(s) in %s, e.g. %s'
                                % (original['name'], len(failed), folder, failed[0]))
        return problems

    def publish(self, record, staging=None, edit_token=None):
        """Reread under lock and append one completed asset to the current database.

        A schema 3 database is patched in place, so other records and unknown
        top-level keys are kept exactly as stored. A legacy database is backed up
        once and converted.
        """
        with library_lock(self.root):
            from .edit_lock import assert_edit_write_allowed
            assert_edit_write_allowed(self.root, edit_token)
            database = self.root / DATABASE_FILENAME
            document = read_json(database) if database.exists() else None
            if document is not None and document.get('schema_version') == 3:
                existing = document['assets']
            else:
                # Legacy or missing database: read fresh, never from the local index cache.
                existing = [self.portable(asset) for asset in self.load(use_cache=False)]
            if any(asset.get('id') == record['id'] for asset in existing):
                raise ValueError('Asset already exists in database: ' + record['id'])
            if document is not None and document.get('schema_version') != 3:
                backup = self.root / 'data.legacy.backup.json'
                if not backup.exists():
                    with open(backup, 'xb') as stream:
                        stream.write(database.read_bytes())
            if document is None or document.get('schema_version') != 3:
                document = {'schema_version': 3, 'asset_type_schema_version': 1, 'assets': existing}
            document['assets'] = list(existing) + [self.portable(record)]
            document.setdefault('asset_type_schema_version', 1)
            destination = self.root / record['category'] / record['name']
            if staging:
                if destination.exists():
                    raise ValueError('Asset folder already exists: ' + str(destination))
                destination.parent.mkdir(parents=True, exist_ok=True)
                Path(staging).rename(destination)
            try:
                atomic_json(database, document)
            except Exception:
                if staging:
                    destination.rename(staging)
                raise


def _resolved(path):
    """``path`` with links, junctions, drive mappings and short names resolved, as far as it exists."""
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(path))


def is_link(path):
    """A symbolic link, or on Windows a junction or other reparse point."""
    path = Path(path)
    try:
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            return True
        attributes = getattr(os.lstat(path), 'st_file_attributes', 0)
    except OSError:
        return False
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def remove_tree(folder):
    """Delete ``folder`` (never following links); returns the paths that could not be deleted.

    Read-only files, common on Windows shares, are made writable and retried once.
    """
    failed = []

    def retry(function, path, error):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if os.path.isdir(path) else 0))
            function(path)
        except OSError:
            failed.append(str(path))
    if sys.version_info >= (3, 12):
        shutil.rmtree(folder, onexc=retry)
    else:
        shutil.rmtree(folder, onerror=lambda function, path, info: retry(function, path, info[1]))
    if not failed and Path(folder).exists():
        failed.append(str(folder))
    return failed


def matches(asset, query='', category='', kind='', tag=''):
    if category and not (asset['category'] == category or asset['category'].startswith(category + '/')):
        return False
    if kind and asset.get('kind') != kind:
        return False
    tags = [str(t).casefold() for t in asset.get('tags', [])]
    if tag and tag.casefold() not in tags:
        return False
    haystack = ' '.join([asset.get('name', ''), str(asset.get('label') or ''), asset.get('category', ''), asset.get('library', ''), *tags]).casefold()
    query, field_clauses = parse_query(query)
    if not matches_fields(asset, field_clauses):
        return False
    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()
    for token in tokens:
        excluded = token.startswith(('-', '!')) and len(token) > 1
        term = token[1:] if excluded else token
        found = term[4:].casefold() in tags if term.lower().startswith('tag:') else term.casefold() in haystack
        if found == excluded:
            return False
    return True
