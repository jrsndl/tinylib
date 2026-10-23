"""Per-user ratings, in/out points and named asset collections, independent of library databases."""
import copy
import json
import os
import time
import uuid
from pathlib import Path
from .library import atomic_json, library_lock, read_json
from .trim import clean_trim, stored_trim
from .path_format import FORMAT_IDS
from .tile_text import DEFAULT_TEMPLATE, validate_template

ASSET_MIME = 'application/x-tinylib-asset-keys'
COLLECTION_MIME = 'application/x-tinylib-collection-selection'


def asset_key(asset):
    # Media identity survives legacy database migration and library display-name changes.
    root = str(asset.get('library_root', '')).replace('\\', '/').rstrip('/')
    main = str(asset.get('main', asset.get('id', ''))).replace('\\', '/')
    return json.dumps([os.path.normcase(root), os.path.normcase(main)], ensure_ascii=False)


def adjacent_asset(assets, asset, step):
    """The asset ``step`` places from ``asset`` in ``assets``, wrapping at both ends.

    An asset no longer in the list (filtered out, removed) steps to the first (``step`` > 0)
    or the last asset. None for an empty list.
    """
    if not assets:
        return None
    keys = [asset_key(item) for item in assets]
    key = asset_key(asset)
    if key in keys:
        return assets[(keys.index(key) + step) % len(assets)]
    return assets[0] if step > 0 else assets[-1]


def reference(asset):
    return {key: asset.get(key, '') for key in
            ('name', 'library', 'library_root', 'main', 'id', 'kind', 'category', 'thumb')}


# Display preferences (the Preferences window): key in the "display" section -> default.
DISPLAY_DEFAULTS = {
    'path_notation': 'nuke',
    'start_properties': True,
    'start_collections': False,
    'remember_category': True,
    'tile_info': True,
}


def display_value(display, key):
    """A display preference, or its default when unset or of the wrong type."""
    default = DISPLAY_DEFAULTS[key]
    value = display.get(key, default)
    if key == 'path_notation':
        return value if value in FORMAT_IDS else default
    return value if isinstance(value, bool) else default


def tile_template(display, settings):
    """The user's tile text, else the studio's tile_template, else the default; invalid ones fall back."""
    for template in (display.get('tile_template'), settings.get('tile_template')):
        if template is None:
            continue
        try:
            validate_template(template)
            return template
        except ValueError:
            continue
    return DEFAULT_TEMPLATE


class Preferences:
    def __init__(self, path=None):
        base = Path(os.environ.get('APPDATA') or Path.home() / '.config')
        self.path = Path(path or os.environ.get('TINYLIB_USER_PREFS') or base / 'TinyLib' / 'preferences.json')
        self.warning = None
        try:
            self.reload()
        except OSError as error:
            # Temporarily unreadable (another session replacing it, a network hiccup): never
            # rename a good file. Start empty in memory; change() rereads before every save.
            self.warning = 'Your TinyLib preferences could not be read right now (%s). Ratings and collections ' \
                           'will appear after a restart.' % error
            self.data = {'version': 1, 'ratings': {}, 'collections': []}
        except ValueError as error:
            # A damaged file must not stop TinyLib from starting; keep it for inspection.
            aside = self.path.with_name('%s.damaged-%s%s' % (self.path.stem, time.strftime('%Y%m%d-%H%M%S'),
                                                             self.path.suffix))
            try:
                os.replace(self.path, aside)
                self.warning = 'Your TinyLib preferences were damaged and were reset. The old file is %s' % aside
            except OSError:
                self.warning = 'Your TinyLib preferences could not be read (%s); changes will not be saved.' % error
            self.data = {'version': 1, 'ratings': {}, 'collections': []}

    def reload(self):
        data = read_json(self.path) if self.path.exists() else {'version': 1, 'ratings': {}, 'collections': []}
        if data.get('version') != 1 or not isinstance(data.get('ratings'), dict) or not isinstance(data.get('collections'), list):
            raise ValueError('Invalid user preferences: ' + str(self.path))
        if not isinstance(data.get('trims', {}), dict):
            raise ValueError('Invalid in/out points in user preferences: ' + str(self.path))
        self.data = data

    def change(self, operation):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with library_lock(self.path.parent):
            self.reload()
            previous = copy.deepcopy(self.data)
            try:
                result = operation(self.data)
                atomic_json(self.path, self.data)
                return result
            except Exception:
                self.data = previous
                raise

    @property
    def collections(self):
        return self.data['collections']

    def collection(self, identifier):
        for item in self.collections:
            if item['id'] == identifier:
                return item
        raise ValueError('This collection no longer exists; it may have been deleted in another TinyLib window.')

    def find_collection(self, identifier):
        return next((c for c in self.collections if c['id'] == identifier), None)

    def picked_keys(self):
        return {key for c in self.collections for key in c['assets']}

    def move_assets(self, moves):
        """Assets moved to another path (Category change): their ratings, in/out points and
        collection entries follow them. ``moves`` is ``[(old asset, new asset)]``."""
        def move(data):
            for old, new in moves:
                old_key, new_key = asset_key(old), asset_key(new)
                if old_key == new_key:
                    continue
                for section in ('ratings', 'trims'):
                    values = data.setdefault(section, {})
                    if old_key in values:
                        values[new_key] = values.pop(old_key)
                for collection in data['collections']:
                    entry = collection['assets'].pop(old_key, None)
                    if entry is not None:
                        moved = reference(new)
                        moved.update({k: entry[k] for k in ('in', 'out') if k in entry})
                        collection['assets'][new_key] = moved
        self.change(move)

    def rating(self, asset):
        return self.data['ratings'].get(asset_key(asset), max(0, min(5, int(asset.get('stars', 0) or 0))))

    def rate(self, assets, stars):
        if stars not in range(6):
            raise ValueError('Rating must be 0–5 stars.')
        self.change(lambda data: data['ratings'].update({asset_key(a): stars for a in assets}))

    def trim(self, asset, collection=None):
        """The user's in/out points for an asset, or those stored with it in ``collection``."""
        key = asset_key(asset)
        if collection:
            found = self.find_collection(collection)
            entry = found['assets'].get(key, {}) if found else {}
            return clean_trim((entry.get('in'), entry.get('out')))
        return clean_trim(self.data.get('trims', {}).get(key))

    def set_trim(self, asset, trim, collection=None):
        """Store in/out points (None clears them) for the user, or for the asset's entry in ``collection``."""
        if trim is not None and clean_trim(trim) is None:
            raise ValueError('In and out points must be whole frame numbers with in before out.')
        value, key = stored_trim(trim), asset_key(asset)

        def store(data):
            if collection:
                entry = self.collection(collection)['assets'].get(key)
                if entry is None:
                    raise ValueError('This asset is no longer in the collection.')
                entry.pop('in', None)
                entry.pop('out', None)
                if value:
                    entry.update(value)
            elif value:
                data.setdefault('trims', {})[key] = value
            else:
                data.setdefault('trims', {}).pop(key, None)
        self.change(store)

    def set_display(self, **values):
        self.change(lambda data: data.setdefault('display', {}).update(values))

    def create_collection(self, name=None):
        def create(data):
            existing = {c['name'].casefold() for c in data['collections']}
            number = data.get('next_collection_number', 1)
            while 'collection%02d' % number in existing:
                number += 1
            chosen = (name or 'collection%02d' % number).strip()
            if not chosen or chosen.casefold() in existing:
                raise ValueError('Choose a non-empty, unique collection name.')
            identifier = uuid.uuid4().hex
            data['collections'].append({'id': identifier, 'name': chosen, 'assets': {}})
            if name is None:
                data['next_collection_number'] = number + 1
            return identifier
        return self.change(create)

    def rename_collection(self, identifier, name):
        def rename(data):
            chosen = name.strip()
            if not chosen or any(c['name'].casefold() == chosen.casefold() and c['id'] != identifier for c in data['collections']):
                raise ValueError('Choose a non-empty, unique collection name.')
            self.collection(identifier)['name'] = chosen
        self.change(rename)

    def delete_collection(self, identifier):
        self.change(lambda data: data.update(collections=[c for c in data['collections'] if c['id'] != identifier]))

    def add_assets(self, identifier, assets):
        """Add assets to a collection; each takes the user's current in/out points with it."""
        def add(data):
            entries = self.collection(identifier)['assets']
            for asset in assets:
                key = asset_key(asset)
                entry = reference(asset)
                trim = stored_trim(data.get('trims', {}).get(key))
                if trim:
                    entry.update(trim)
                elif key in entries:
                    # Already collected: keep the in/out points the entry has.
                    entry.update({k: entries[key][k] for k in ('in', 'out') if k in entries[key]})
                entries[key] = entry
        self.change(add)

    def remove_assets(self, identifier, keys):
        def remove(data):
            entries = self.collection(identifier)['assets']
            for key in keys:
                entries.pop(key, None)
        self.change(remove)

    def export_collection(self, identifier, path):
        collection = self.collection(identifier)
        # Version 2 adds optional in/out points per asset.
        atomic_json(path, {'format': 'tinylib.collection', 'version': 2, 'name': collection['name'],
                           'assets': list(collection['assets'].values())})

    def import_collection(self, path):
        if Path(path).stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Collection JSON exceeds 16 MB.')
        data = read_json(path)
        if (data.get('format') != 'tinylib.collection' or data.get('version') not in (1, 2)
                or not isinstance(data.get('name'), str) or not data['name'].strip()
                or not isinstance(data.get('assets'), list)):
            raise ValueError('Not a TinyLib collection JSON file.')
        refs = {}
        for item in data['assets']:
            if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key] for key in ('name', 'main', 'library_root')):
                raise ValueError('Collection contains an invalid asset reference.')
            clean = reference(item)
            if any(not isinstance(value, str) for value in clean.values()):
                raise ValueError('Asset reference fields must be text.')
            if 'in' in item or 'out' in item:
                trim = stored_trim((item.get('in'), item.get('out')))
                if not trim:
                    raise ValueError('Collection contains invalid in/out points for %s.' % item['name'])
                clean.update(trim)
            refs[asset_key(clean)] = clean
        def create(current):
            name = data['name'].strip()
            existing = {c['name'].casefold() for c in current['collections']}
            candidate, suffix = name, 2
            while candidate.casefold() in existing:
                candidate = '%s (%d)' % (name, suffix)
                suffix += 1
            identifier = uuid.uuid4().hex
            current['collections'].append({'id': identifier, 'name': candidate, 'assets': refs})
            return identifier
        return self.change(create)
