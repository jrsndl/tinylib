import copy
import tempfile
import unittest
from pathlib import Path
from tinylib.access import AccessControl, default_permissions
from tinylib.actions import ActionRegistry
from tinylib.asset_edit import save_asset
from tinylib.asset_fields import DEFAULT_FIELDS, validate_fields
from tinylib.library import Library, atomic_json, read_json


class CommonFieldTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = {'_config_path': str(self.root / 'studio.json'),
                         'libraries': [{'name': 'Test', 'root': str(self.root)}],
                         'access': {'groups': ['admins', 'managers', 'users', 'restricted'],
                                    'users': {'admin': ['admins'], 'manager': ['managers'],
                                              'user': ['users'], 'restricted': ['restricted']}}}
        atomic_json(self.root / 'tinylib_data.json', {'schema_version': 3, 'assets': [{
            'id': 'test/asset', 'name': 'Asset', 'category': 'test', 'kind': 'still',
            'main': 'test/asset/main/file.exr', 'colorspace': 'ACEScg', 'tags': [],
            'metadata': {'width': 10, 'ingest_secret': 'hidden'}}]})
        self.library = Library(self.root, 'Test')
        self.asset = self.library.load()[0]

    def access(self, identity, settings=None):
        return AccessControl(settings or self.settings, identity=identity, persist=False)

    def test_defaults_and_library_overrides(self):
        user, manager = self.access('user'), self.access('manager')
        for right in ('view', 'actions'):
            self.assertTrue(user.can(self.root, right))
        for right in ('ingest', 'edit', 'version_fields', 'ingest_fields', 'acquire_fields'):
            self.assertFalse(user.can(self.root, right))
        self.assertTrue(manager.can(self.root, 'edit'))
        self.assertFalse(manager.can(self.root, 'ingest'))
        self.assertTrue(manager.can(self.root, 'acquire_fields'))
        self.assertFalse(manager.can(self.root, 'version_fields'))
        self.assertFalse(self.access('restricted').can(self.root, 'view'))
        settings = copy.deepcopy(self.settings)
        settings['access']['defaults'] = {'edit': ['users']}
        self.assertTrue(self.access('user', settings).can(self.root, 'edit'))
        settings['libraries'][0]['permissions'] = {'edit': [], 'acquire_fields': ['users']}
        self.assertFalse(self.access('user', settings).can(self.root, 'edit'))
        self.assertTrue(self.access('user', settings).field_allowed(self.root, 'acquire_price'))

    def test_common_fields_persist_and_restricted_updates_fail(self):
        for key, default in DEFAULT_FIELDS.items():
            self.assertEqual(self.asset[key], default)
        manager = self.access('manager')
        save_asset(self.settings, manager, self.asset, {'category_1': 'Secondary',
                   'ingest_note': 'First line\nSecond line', 'acquire_price': 'usd 25.50'})
        stored = read_json(self.root / 'tinylib_data.json')['assets'][0]
        self.assertTrue(set(DEFAULT_FIELDS).issubset(stored))
        self.assertEqual(stored['ingest_note'], 'First line\nSecond line')
        before = (self.root / 'tinylib_data.json').read_bytes()
        with self.assertRaises(PermissionError):
            save_asset(self.settings, manager, self.asset, {'version_number': 2})
        settings = copy.deepcopy(self.settings)
        settings['libraries'][0]['permissions'] = {'edit': ['users']}
        user = self.access('user', settings)
        with self.assertRaises(PermissionError):
            save_asset(settings, user, self.asset, {'ingest_user': 'spoofed'})
        with self.assertRaises(PermissionError):
            save_asset(settings, user, self.asset, {'metadata': {'ingest_secret': 'changed'}})
        self.assertEqual((self.root / 'tinylib_data.json').read_bytes(), before)

    def test_action_allow_only_restricts_admin_and_every_library(self):
        folder = self.root / 'actions/restricted'
        folder.mkdir(parents=True)
        manifest = {'id': 'test', 'name': 'Test', 'version': '1', 'category': 'Test',
                    'allow_only': ['managers']}
        atomic_json(folder / 'manifest.json', manifest)
        atomic_json(folder / 'config.json', {})
        (folder / 'action.py').write_text('def run(assets, context, config):\n    return 42\n')
        registry = ActionRegistry([folder.parent])
        self.assertFalse(registry.allowed(registry.actions['test'], [self.asset], self.access('admin')))
        with self.assertRaises(PermissionError):
            registry.run('test', [self.asset], self.access('user'))
        self.assertEqual(registry.run('test', [self.asset], self.access('manager')), 42)
        settings = copy.deepcopy(self.settings)
        settings['libraries'][0]['permissions'] = {'actions': []}
        self.assertFalse(registry.allowed(registry.actions['test'], [self.asset], self.access('manager', settings)))
        manifest['allow_only'] = []
        atomic_json(folder / 'manifest.json', manifest)
        with self.assertRaises(PermissionError):
            registry.run('test', [self.asset], self.access('admin'))

    def test_validation(self):
        for fields in ({'version_to_delete': 1}, {'version_number': True}, {'ingest_date': '20250230'},
                       {'acquire_price': '25 eur'}, {'ingest_note': []}):
            with self.assertRaises(ValueError):
                validate_fields(fields)


if __name__ == '__main__':
    unittest.main()
