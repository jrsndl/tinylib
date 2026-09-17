"""Per-user ratings and named asset collections, independent of library databases."""
import copy
import json
import os
import uuid
from pathlib import Path
from .library import atomic_json, library_lock, read_json


def asset_key(asset):
    # Media identity survives legacy database migration and library display-name changes.
    root = str(asset.get('library_root', '')).replace('\\', '/').rstrip('/')
    main = str(asset.get('main', asset.get('id', ''))).replace('\\', '/')
    return json.dumps([os.path.normcase(root), os.path.normcase(main)], ensure_ascii=False)


def reference(asset):
    return {key: asset.get(key, '') for key in
            ('name', 'library', 'library_root', 'main', 'id', 'kind', 'category', 'thumb')}


class Preferences:
    def __init__(self, path=None):
        base = Path(os.environ.get('APPDATA') or Path.home() / '.config')
        self.path = Path(path or os.environ.get('TINYLIB_USER_PREFS') or base / 'TinyLib' / 'preferences.json')
        self.reload()

    def reload(self):
        data = read_json(self.path) if self.path.exists() else {'version': 1, 'ratings': {}, 'collections': []}
        if data.get('version') != 1 or not isinstance(data.get('ratings'), dict) or not isinstance(data.get('collections'), list):
            raise ValueError('Invalid user preferences: ' + str(self.path))
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
        return next(c for c in self.collections if c['id'] == identifier)

    def picked_keys(self):
        return {key for c in self.collections for key in c['assets']}

    def rating(self, asset):
        return self.data['ratings'].get(asset_key(asset), max(0, min(5, int(asset.get('stars', 0) or 0))))

    def rate(self, assets, stars):
        if stars not in range(6):
            raise ValueError('Rating must be 0–5 stars.')
        self.change(lambda data: data['ratings'].update({asset_key(a): stars for a in assets}))

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
        def add(data):
            self.collection(identifier)['assets'].update({asset_key(a): reference(a) for a in assets})
        self.change(add)

    def remove_assets(self, identifier, keys):
        def remove(data):
            entries = self.collection(identifier)['assets']
            for key in keys:
                entries.pop(key, None)
        self.change(remove)
