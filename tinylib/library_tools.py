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
    for role in ('main', 'main-original', 'highres', 'proxy', 'thumb', 'filmstrip', 'scene'):
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


def require_admin(access):
    """Admin-only features (library tools, access rights, configuration): re-read the security file to be sure."""
    access.refresh()
    if not access.is_admin:
        raise PermissionError('Only administrators can use this.')


def crosscheck_library(root, name, output_dir=None, progress=None, cancelled=None, now=None, access=None):
    """Check JSON references and category/asset folders, then write a text report."""
    if access is not None:
        require_admin(access)
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
    log_path = output_dir / ('%s_crosscheck_%s.log' %
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


def sort_library(settings, access, library, override=False, progress=None):
    """Persist category/name order while holding the standard edit and write locks."""
    from .asset_edit import writable_library
    from .edit_lock import acquire_edit_lock, assert_edit_write_allowed, release_edit_lock
    from .library import atomic_json, library_lock
    report = progress or (lambda message: None)
    report('Checking library write access…')
    require_admin(access)
    config = writable_library(settings, access, {'library_root': library['root']})
    report('Acquiring library edit lock…')
    handle = acquire_edit_lock(config['root'], getattr(access, 'actor', access.identity), override=override)
    try:
        with library_lock(config['root']):
            assert_edit_write_allowed(config['root'], handle['token'])
            report('Reading latest library database…')
            database = Path(config['root']) / DATABASE_FILENAME
            document = read_json(database)
            if document.get('schema_version') != 3 or not isinstance(document.get('assets'), list):
                raise ValueError('Library sort requires a schema_version 3 tinylib_data.json.')
            records = document['assets']
            if any(not isinstance(record, dict) or not isinstance(record.get('name'), str)
                   or not isinstance(record.get('category'), str) for record in records):
                raise ValueError('Every asset must have a category and name to sort the library.')
            report('Sorting %d assets alphabetically…' % len(records))
            ordered = sorted(records, key=lambda record: (
                record['category'].replace('\\', '/').casefold(), record['name'].casefold()))
            changed = records != ordered
            if changed:
                document['assets'] = ordered
                report('Saving sorted library database…')
                atomic_json(database, document)
            return {'assets': len(records), 'changed': changed}
    finally:
        release_edit_lock(handle)


LABEL_REPLACEMENTS = 3


def label_from_name(name, separators=True, camel_case=True, replacements=()):
    """A display label made from an asset name.

    Applied in this order, as the Labels from names tool previews it:

    1. ``camel_case``: a space before a capital that follows a lowercase letter or digit, and
       that capital in lowercase: ``abandonedBakery`` -> ``abandoned bakery``. An acronym is
       kept: ``HDRISky`` -> ``HDRI sky``, ``skyHDRI`` -> ``sky HDRI``.
    2. ``separators``: underscores and dashes become spaces.
    3. ``replacements``: (search, replace) pairs of plain text, case-sensitive, in order;
       an empty search is skipped.

    Runs of spaces become one, and the ends are trimmed.
    """
    label = str(name)
    if camel_case:
        label = re.sub(r'([a-z0-9])([A-Z])(?=[a-z])', lambda m: m[1] + ' ' + m[2].lower(), label)
        label = re.sub(r'([A-Z]{2,})([A-Z])(?=[a-z])', lambda m: m[1] + ' ' + m[2].lower(), label)
        label = re.sub(r'([a-z0-9])(?=[A-Z]{2,})', r'\1 ', label)
    if separators:
        label = re.sub(r'[_-]+', ' ', label)
    for search, replace in replacements:
        if search:
            label = label.replace(search, replace)
    return re.sub(r'\s+', ' ', label).strip()


def plan_labels(records, only_empty=True, **rules):
    """[(record, new label)] for every record whose label the tool would change.

    With ``only_empty``, records that already have a label are left alone. A label equal to
    the name is not written: an empty label already shows the name.
    """
    from .asset_fields import validate_label
    plan = []
    for record in records:
        current = record.get('label') if isinstance(record.get('label'), str) else ''
        if only_empty and current.strip():
            continue
        name = str(record.get('name', ''))
        label = label_from_name(name, **rules)
        if label == name:
            label = ''
        if label == current.strip():
            continue
        validate_label(label)
        plan.append((record, label))
    return plan


def label_library(settings, access, library, only_empty=True, rules=None, override=False, progress=None):
    """Write labels made from asset names into one library, under the standard edit and write locks.

    ``rules`` are the keyword arguments of ``label_from_name``. Returns the number of assets
    and the ``[(asset id, old label, new label)]`` changes.
    """
    from .asset_edit import writable_library
    from .edit_lock import acquire_edit_lock, assert_edit_write_allowed, release_edit_lock
    from .library import atomic_json, library_lock
    report = progress or (lambda message: None)
    report('Checking library write access…')
    require_admin(access)
    config = writable_library(settings, access, {'library_root': library['root']})
    report('Acquiring library edit lock…')
    handle = acquire_edit_lock(config['root'], getattr(access, 'actor', access.identity), override=override)
    try:
        with library_lock(config['root']):
            assert_edit_write_allowed(config['root'], handle['token'])
            report('Reading latest library database…')
            database = Path(config['root']) / DATABASE_FILENAME
            document = read_json(database)
            if document.get('schema_version') != 3 or not isinstance(document.get('assets'), list):
                raise ValueError('Labels from names requires a schema_version 3 tinylib_data.json.')
            records = [record for record in document['assets'] if isinstance(record, dict)]
            plan = plan_labels(records, only_empty, **(rules or {}))
            changes = []
            for record, label in plan:
                changes.append((record.get('id', ''), record.get('label', ''), label))
                if label:
                    record['label'] = label
                else:
                    record.pop('label', None)
            if changes:
                report('Saving %d labels…' % len(changes))
                atomic_json(database, document)
            return {'assets': len(records), 'changes': changes}
    finally:
        release_edit_lock(handle)


KEYWORD_REPLACEMENTS = 3
NUMBER_KEYWORD = re.compile(r'^\d+([.,]\d+)?$')


def keyword_list(text):
    """Comma-separated keywords as a list: trimmed, lowercase, without empty ones."""
    return [part.strip().lower() for part in str(text or '').split(',') if part.strip()]


def clean_keywords(tags, remove_numbers=True, replacements=(), add=()):
    """A cleaned keyword list, in the original order.

    1. ``remove_numbers``: keywords that are only a number (``01``, ``2525``, ``1.5``) go.
    2. ``replacements``: (search, replace) pairs, in order. ``search`` matches a whole keyword,
       ignoring case. An empty ``replace`` removes the keyword; a comma-separated one becomes
       several keywords (``abandonedbakery`` -> ``abandoned, bakery``). An empty search is skipped.
    3. ``add``: keywords added at the end (lowercase), unless the asset already has them.

    Keywords are trimmed, empty ones dropped, and the same keyword (ignoring case) kept once.
    """
    result = [str(tag).strip() for tag in tags if str(tag).strip()]
    if remove_numbers:
        result = [tag for tag in result if not NUMBER_KEYWORD.match(tag)]
    for search, replace in replacements:
        search = str(search).strip().casefold()
        if not search:
            continue
        replaced = []
        for tag in result:
            if tag.casefold() == search:
                replaced.extend(keyword_list(replace))
            else:
                replaced.append(tag)
        result = replaced
    result += [str(tag).strip().lower() for tag in add if str(tag).strip()]
    unique, seen = [], set()
    for tag in result:
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            unique.append(tag)
    return unique


def plan_keywords(records, remove_numbers=True, replacements=(), add=()):
    """[(record, new keyword list)] for every record whose keywords the cleaner would change."""
    plan = []
    for record in records:
        tags = record.get('tags') if isinstance(record.get('tags'), list) else []
        cleaned = clean_keywords(tags, remove_numbers, replacements, add)
        if cleaned != tags:
            plan.append((record, cleaned))
    return plan


def clean_library_keywords(settings, access, library, asset_ids=None, remove_numbers=True, replacements=(),
                           override=False, progress=None, add=()):
    """Clean the keywords of one library, or only of the assets with ``asset_ids`` in it, under
    the standard edit and write locks. Returns the number of assets in scope and the
    ``[(asset id, old keywords, new keywords)]`` changes.
    """
    from .asset_edit import writable_library
    from .edit_lock import acquire_edit_lock, assert_edit_write_allowed, release_edit_lock
    from .library import atomic_json, library_lock
    report = progress or (lambda message: None)
    report('Checking library write access…')
    require_admin(access)
    config = writable_library(settings, access, {'library_root': library['root']})
    report('Acquiring library edit lock…')
    handle = acquire_edit_lock(config['root'], getattr(access, 'actor', access.identity), override=override)
    try:
        with library_lock(config['root']):
            assert_edit_write_allowed(config['root'], handle['token'])
            report('Reading latest library database…')
            database = Path(config['root']) / DATABASE_FILENAME
            document = read_json(database)
            if document.get('schema_version') != 3 or not isinstance(document.get('assets'), list):
                raise ValueError('Keyword cleaner requires a schema_version 3 tinylib_data.json.')
            records = [record for record in document['assets'] if isinstance(record, dict)]
            if asset_ids is not None:
                wanted = set(asset_ids)
                records = [record for record in records if record.get('id') in wanted]
            changes = []
            for record, tags in plan_keywords(records, remove_numbers, replacements, add):
                changes.append((record.get('id', ''), list(record.get('tags') or []), tags))
                record['tags'] = tags
            if changes:
                report('Saving keywords of %d assets…' % len(changes))
                atomic_json(database, document)
            return {'assets': len(records), 'changes': changes}
    finally:
        release_edit_lock(handle)


def create_new_library(settings, access, name, root, template_root=None, read_only=None, progress=None):
    """Initialize an empty database and register it in studio/security config."""
    import copy
    from contextlib import ExitStack
    from .access import root_key
    from .asset_types import SCHEMA_VERSION
    from .library import atomic_json, library_lock
    from .edit_lock import assert_edit_write_allowed
    report = progress or (lambda message: None)
    require_admin(access)
    name, root_text = name.strip(), str(root).strip()
    if not name or not root_text:
        raise ValueError('Enter a library name and root folder.')
    root = Path(os.path.expandvars(root_text)).resolve()
    config_path = Path(settings['_config_path']).resolve()
    security_path = access.path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = root / DATABASE_FILENAME

    def entry_root(entry, path):
        value = Path(os.path.expandvars(entry['root']))
        return root_key((value if value.is_absolute() else path.parent / value).resolve())

    report('Locking library and studio configuration…')
    folders = {root_key(root): root, root_key(config_path.parent): config_path.parent}
    if access.persist:
        folders[root_key(security_path.parent)] = security_path.parent
    with ExitStack() as stack:
        for _, folder in sorted(folders.items()):
            stack.enter_context(library_lock(folder))
        access.refresh()
        if not access.is_admin:
            raise PermissionError('Only admins can create libraries.')
        assert_edit_write_allowed(root)
        if database.exists():
            raise ValueError('This folder already contains ' + DATABASE_FILENAME + '. Choose a different root.')
        document = read_json(config_path)
        security = document if config_path == security_path and access.persist else copy.deepcopy(access.data)
        for data, path in ((document, config_path), (security, security_path)):
            for library in data.get('libraries', []):
                if entry_root(library, path) == root_key(root):
                    raise ValueError('This root is already registered as a library.')
                if library['name'].strip().casefold() == name.casefold():
                    raise ValueError('A library with this name already exists.')
        if template_root:
            template = next((item for item in document.get('libraries', [])
                             if entry_root(item, config_path) == root_key(Path(template_root).resolve())), None)
            if template is None:
                raise ValueError('The settings source library no longer exists.')
            library = copy.deepcopy(template)
            policy = next((item for item in security.get('libraries', [])
                           if entry_root(item, security_path) == root_key(Path(template_root).resolve())), None)
            if policy is not None:
                library['permissions'] = copy.deepcopy(policy.get('permissions', {}))
        else:
            library = copy.deepcopy(document.get('library_defaults', {}))
            library.setdefault('read_only', False)
            library.setdefault('permissions', {})  # Inherit studio permission defaults.
        library.update(name=name, root=str(root))
        if read_only is not None:
            library['read_only'] = bool(read_only)
        before_config = copy.deepcopy(document)
        before_security = copy.deepcopy(security)
        document.setdefault('libraries', []).append(copy.deepcopy(library))
        if security is not document:
            security.setdefault('libraries', []).append(copy.deepcopy(library))
        written = []
        try:
            report('Creating empty library database…')
            atomic_json(database, {'schema_version': 3, 'asset_type_schema_version': SCHEMA_VERSION, 'assets': []})
            report('Registering new library…')
            atomic_json(config_path, document)
            written.append((config_path, before_config))
            if access.persist and security_path != config_path:
                atomic_json(security_path, security)
                written.append((security_path, before_security))
            if not access.persist:
                access._memory = copy.deepcopy(security)
            access.refresh()
        except Exception as error:
            rollback_errors = []
            for path, original in reversed(written):
                try:
                    atomic_json(path, original)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            if not rollback_errors and database.exists():
                database.unlink()
            if rollback_errors:
                raise RuntimeError('Library registration failed: %s. Configuration rollback failed: %s'
                                   % (error, '; '.join(rollback_errors))) from error
            raise
        return library
