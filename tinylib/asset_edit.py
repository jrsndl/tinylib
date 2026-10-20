"""Permission-checked, optimistic metadata updates; media paths remain stable."""
import copy
import math
from .access import root_key
from .library import Library
from .asset_types import validate_metadata, relative_paths
from .asset_fields import DEFAULT_FIELDS, validate_fields

EDITABLE = {'colorspace', 'tags', 'metadata'} | set(DEFAULT_FIELDS)


def writable_library(settings, access, asset):
    library = next((item for item in settings['libraries'] if root_key(item['root']) == root_key(asset['library_root'])), None)
    if not library or library.get('read_only'):
        raise PermissionError('This library is read-only.')
    access.require(library['root'], 'edit')
    return library


def validate_changes(asset, changes):
    if set(changes) - EDITABLE:
        raise ValueError('Library, type, identity and media paths cannot be edited.')
    merged = dict(asset, **copy.deepcopy(changes))
    validate_fields(merged)
    for field in ('name', 'category', 'colorspace'):
        if not isinstance(merged.get(field), str) or not merged[field].strip():
            raise ValueError(field + ' must not be empty.')
    if not isinstance(merged.get('tags'), list) or not all(isinstance(tag, str) and tag.strip() for tag in merged['tags']):
        raise ValueError('Keywords must be a list of non-empty strings.')
    metadata = merged.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('Metadata must be an object.')
    for field in ('width', 'height', 'FPS'):
        if field in metadata:
            try:
                value = float(metadata[field])
            except (ValueError, TypeError):
                raise ValueError(field + ' must be a positive number.')
            if not math.isfinite(value) or value <= 0 or (field != 'FPS' and not value.is_integer()):
                raise ValueError(field + ' must be a positive ' + ('number.' if field == 'FPS' else 'integer.'))
    validate_metadata(merged.get('kind', 'still'), metadata)
    if merged.get('kind') == 'model' and 'textures' in metadata:
        relative_paths(metadata['textures'])
    if merged.get('kind') == 'folder' and 'files' in metadata:
        relative_paths(metadata['files'])
    if merged.get('kind') == 'footage':
        first, last = merged.get('first'), merged.get('last')
        if type(first) is not int or type(last) is not int or first > last:
            raise ValueError('Footage needs an integer first/last frame range with first ≤ last.')
    return changes


def save_asset(settings, access, original, changes, edit_token=None):
    access.refresh()
    library = writable_library(settings, access, original)
    for key, value in changes.items():
        if not access.field_allowed(library['root'], key):
            raise PermissionError('Your groups cannot edit ' + key + '.')
        if key == 'metadata':
            old = original.get('metadata', {})
            for field in set(old) | set(value):
                if old.get(field) != value.get(field) and not access.field_allowed(library['root'], field):
                    raise PermissionError('Your groups cannot edit ' + field + '.')
    validate_changes(original, changes)
    database = Library(library['root'], library['name'], library.get('legacy_roots', []))
    return database.update_asset(original, changes, edit_token=edit_token)


def save_assets(settings, access, edits, edit_tokens):
    """Validate all multi-edit patches, then write each locked library once."""
    access.refresh()
    groups = {}
    for original, changes in edits:
        library = writable_library(settings, access, original)
        for key, value in changes.items():
            if not access.field_allowed(library['root'], key):
                raise PermissionError('Your groups cannot edit ' + key + '.')
            if key == 'metadata':
                old = original.get('metadata', {})
                for field in set(old) | set(value):
                    if old.get(field) != value.get(field) and not access.field_allowed(library['root'], field):
                        raise PermissionError('Your groups cannot edit ' + field + '.')
        validate_changes(original, changes)
        key = root_key(library['root'])
        groups.setdefault(key, (library, []))[1].append((original, changes))
    updated = []
    for key, (library, patches) in groups.items():
        database = Library(library['root'], library['name'], library.get('legacy_roots', []))
        updated.extend(database.update_assets(patches, edit_token=edit_tokens[key]))
    return updated
