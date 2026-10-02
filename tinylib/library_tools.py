"""Administrative library integrity checks and downloadable text reports."""
import os
import re
from datetime import datetime
from pathlib import Path

from .asset_types import ASSET_TYPES, canonical_type
from .library import (DATABASE_FILENAME, Library, frame_path, read_json,
                      split_sequence)


class CrosscheckCancelled(RuntimeError):
    pass


def download_folder():
    folder = Path.home() / 'Downloads'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _safe_filename(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(value)).strip(' ._')
    return value or 'library'


def _asset_label(record):
    category = str(record.get('category', '')).strip('/\\')
    name = str(record.get('name') or record.get('id') or '<unnamed>')
    return (category + '/' + name).strip('/')


def _asset_folder(root, record):
    return Path(root) / str(record.get('category', '')).replace('/', os.sep) / str(record.get('name', ''))


def _listed_paths(root, record):
    """Yield (field label, concrete path) for every path explicitly stored in a record."""
    library = Library(root)
    kind = canonical_type(record.get('kind', 'still'))
    roles = list(ASSET_TYPES.get(kind, {}).get('representations', {}))
    for role in ('main', 'highres', 'proxy', 'thumb', 'filmstrip', 'scene'):
        if role not in roles:
            roles.append(role)
    for role in roles:
        value = record.get(role)
        if not value:
            continue
        pattern, explicit_first, explicit_last = split_sequence(value)
        resolved = library.resolve(pattern)
        token = re.search(r'#+|%0?\d*d', resolved)
        first = explicit_first if explicit_first is not None else record.get('first')
        last = explicit_last if explicit_last is not None else record.get('last')
        if token and type(first) is int and type(last) is int and first <= last:
            for frame in range(first, last + 1):
                yield '%s frame %d' % (role, frame), Path(frame_path(resolved, frame))
        else:
            yield role, Path(resolved)

    asset_folder = _asset_folder(root, record)
    metadata = record.get('metadata', {})
    if isinstance(metadata, dict):
        for field in ('textures', 'files'):
            values = metadata.get(field, [])
            if isinstance(values, list):
                for index, value in enumerate(values):
                    if isinstance(value, str) and value:
                        yield '%s[%d]' % (field, index), asset_folder / Path(value)


def _storage_asset_folders(root):
    folders = []
    try:
        categories = sorted(path for path in Path(root).iterdir()
                            if path.is_dir() and not path.name.startswith('.'))
    except OSError:
        return folders
    for category in categories:
        try:
            folders.extend(sorted(path for path in category.iterdir()
                                  if path.is_dir() and not path.name.startswith('.')))
        except OSError:
            continue
    return folders


def crosscheck_library(root, name, output_dir=None, progress=None, cancelled=None, now=None):
    """Check JSON references and category/asset folders, then write a text report."""
    root = Path(root)
    database = root / DATABASE_FILENAME
    document = read_json(database)
    if document.get('schema_version') != 3 or not isinstance(document.get('assets'), list):
        raise ValueError('Library crosscheck requires a schema_version 3 tinylib_data.json.')
    records = document['assets']
    folders = _storage_asset_folders(root)
    total = len(records) + len(folders)
    done = 0
    missing_records = []

    def update(detail):
        if cancelled and cancelled():
            raise CrosscheckCancelled('Library crosscheck cancelled.')
        if progress:
            progress(done, total, detail)

    for record in records:
        label = _asset_label(record)
        problems = []
        update('JSON record: ' + label)
        for field, path in _listed_paths(root, record):
            update('%s · %s · %s' % (label, field, path))
            try:
                exists = path.exists()
            except OSError:
                exists = False
            if not exists:
                problems.append((field, str(path)))
        if problems:
            missing_records.append((label, problems))
        done += 1
        update('Checked JSON record: ' + label)

    record_keys = {(str(record.get('category', '')).replace('\\', '/').strip('/') + '/' +
                    str(record.get('name', '')).strip('/')).casefold().strip('/')
                   for record in records}
    orphan_folders = []
    for folder in folders:
        relative = folder.relative_to(root).as_posix()
        update('Storage folder: ' + relative)
        if relative.casefold() not in record_keys:
            orphan_folders.append(str(folder))
        done += 1
        update('Checked storage folder: ' + relative)

    timestamp = now or datetime.now().astimezone()
    output_dir = Path(output_dir) if output_dir else download_folder()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / ('%s_ccrosscheck_%s.log' %
                             (_safe_filename(name), timestamp.strftime('%Y%m%d_%H%M%S')))
    lines = [
        'TinyLib library crosscheck',
        'Library: %s' % name,
        'Root: %s' % root,
        'Database: %s' % database,
        'Started: %s' % timestamp.isoformat(timespec='seconds'),
        '',
        'JSON RECORDS WITH MISSING FILES (%d)' % len(missing_records),
        '=' * 72,
    ]
    if not missing_records:
        lines.append('None')
    for label, problems in missing_records:
        lines.append('[%s]' % label)
        lines.extend('  %s: %s' % problem for problem in problems)
    lines.extend(['', 'ASSET FOLDERS WITHOUT JSON RECORDS (%d)' % len(orphan_folders), '=' * 72])
    lines.extend(orphan_folders or ['None'])
    missing_paths = sum(len(problems) for _, problems in missing_records)
    lines.extend(['', 'SUMMARY', '=' * 72,
                  'JSON records checked: %d' % len(records),
                  'Missing referenced paths: %d' % missing_paths,
                  'Storage asset folders checked: %d' % len(folders),
                  'Asset folders without records: %d' % len(orphan_folders), ''])
    log_path.write_text('\n'.join(lines), encoding='utf-8')
    return {'log_path': str(log_path), 'records': len(records),
            'missing_records': len(missing_records), 'missing_paths': missing_paths,
            'folders': len(folders), 'orphan_folders': len(orphan_folders)}
