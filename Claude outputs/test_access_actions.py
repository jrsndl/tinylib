import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.access import AccessControl, DEFAULT_GROUPS
from tinylib.actions import ActionRegistry
from tinylib.library import atomic_json
from tinylib.preferences import Preferences


class AccessActionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'studio.json'
        self.library = {'root': str(self.root / 'library'), 'name': 'Library'}
        atomic_json(self.path, {'libraries': [self.library], 'tools': {'ffplay': 'ffplay'},
                                'access': {'groups': list(DEFAULT_GROUPS),
                                           'users': {'studio\\first': list(DEFAULT_GROUPS)}}})
        self.admin = AccessControl({'_config_path': str(self.path)}, identity='studio\\first')

    def user(self, name='studio\\artist'):
        return AccessControl({'_config_path': str(self.path)}, identity=name)

    def grant(self):
        libraries = copy.deepcopy(self.admin.data['libraries'])
        libraries[0].setdefault('permissions', {})['actions'] = ['users']
        self.admin.save({'studio\\first': DEFAULT_GROUPS, 'studio\\artist': ['users', 'restricted']}, DEFAULT_GROUPS, libraries)

    def plugin(self):
        folder = self.root / 'actions/test'
        folder.mkdir(parents=True)
        atomic_json(folder / 'manifest.json', {'id': 'test.action', 'name': 'Test', 'version': '1', 'category': 'Test',
                                             'asset_filter': {'extensions': ['.EXR'], 'requires': ['main']}})
        atomic_json(folder / 'config.json', {'value': 42})
        (folder / 'action.py').write_text("from pathlib import Path\nPath(__file__).with_name('executed').touch()\ndef run(assets, context, config):\n    return [len(assets), context['identity'], config['value']]\n")
        return ActionRegistry([folder.parent]), folder

    def test_group_union(self):
        self.assertEqual(self.admin.groups, set(DEFAULT_GROUPS))
        self.assertIsNone(self.admin.error)
        self.assertTrue(self.admin.can('unlisted', 'action', 'anything'))
        self.assertFalse(self.user().can(self.library['root'], 'view'))
        self.grant()
        user = self.user('STUDIO\\ARTIST')
        self.assertTrue(user.can(self.library['root'], 'view'))
        self.assertTrue(user.can(self.library['root'], 'action', 'test.action'))
        self.assertFalse(user.can(self.library['root'], 'ingest'))
        self.assertFalse(user.can('unlisted', 'view'))
        self.assertEqual(json.loads(self.path.read_text())['tools'], {'ffplay': 'ffplay'})

    def test_only_admin_can_assign_and_last_admin_remains(self):
        with self.assertRaises(PermissionError):
            self.user().save({}, DEFAULT_GROUPS, [])
        with self.assertRaises(ValueError):
            self.admin.save({'artist': ['users']}, DEFAULT_GROUPS, [])
        self.assertTrue(self.user('studio\\first').is_admin)

    def test_denied_plugin_never_imported_and_mixed_libraries_denied(self):
        registry, folder = self.plugin()
        asset = {'name': 'Asset', 'main': 'C:/space here/test.####.exr 1001-1003', 'library_root': self.library['root']}
        self.assertFalse((folder / 'executed').exists())
        with self.assertRaises(PermissionError):
            registry.run('test.action', [asset], self.user())
        self.grant()
        with self.assertRaises(PermissionError):
            registry.run('test.action', [asset, dict(asset, library_root='unlisted')], self.user())
        self.assertFalse((folder / 'executed').exists())
        self.assertEqual(registry.run('test.action', [asset], self.user()), [1, 'studio\\artist', 42])

    def test_a_whole_view_checks_each_library_once(self):
        registry, _ = self.plugin()
        self.grant()
        user = self.user()
        asset = {'name': 'Asset', 'main': 'C:/a/test.exr', 'library_root': self.library['root']}
        calls = []
        original = user.can
        user.can = lambda root, *args: calls.append(root) or original(root, *args)
        self.assertTrue(registry.allowed(registry.actions['test.action'], [asset] * 500, user))
        self.assertEqual(len(calls), 2)  # view and action, once for the one library
        self.assertFalse(registry.allowed(registry.actions['test.action'], [asset, dict(asset, library_root='unlisted')], user))

    def test_refresh_revokes_grants_and_malformed_assignment_fails_closed(self):
        self.grant()
        user = self.user()
        self.admin.save({'studio\\first': ['admins'], 'studio\\artist': ['restricted']}, DEFAULT_GROUPS, self.admin.data['libraries'])
        user.refresh()
        self.assertFalse(user.can(self.library['root'], 'view'))
        data = json.loads(self.path.read_text())
        data['access']['users']['studio\\first'] = 'admins'
        atomic_json(self.path, data)
        self.admin.refresh()
        self.assertFalse(self.admin.is_admin)
        self.assertIn('Invalid studio security file', self.admin.error)
        self.assertFalse(self.admin.can(self.library['root'], 'view'))

    def test_empty_user_table_fails_closed_and_is_never_written(self):
        atomic_json(self.path, {'libraries': [self.library]})
        before = self.path.read_bytes()
        first = self.user('studio\\whoever')
        self.assertFalse(first.is_admin)
        self.assertFalse(first.can(self.library['root'], 'view'))
        self.assertIn('No users are configured', first.error)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_install_fails_closed(self):
        settings = {'_config_path': str(self.path), '_security_path': None,
                    '_security_error': 'tinylib.ini is missing'}
        access = AccessControl(settings, identity='studio\\first')
        self.assertFalse(access.is_admin)
        self.assertEqual(access.error, 'tinylib.ini is missing')
        self.assertFalse(access.can(self.library['root'], 'view'))
        with self.assertRaisesRegex(PermissionError, 'tinylib.ini is missing'):
            access.save({'studio\\first': ['admins']}, DEFAULT_GROUPS, [])

    def test_missing_security_file_fails_closed(self):
        access = AccessControl({'_security_path': str(self.root / 'absent.json')}, identity='studio\\first')
        self.assertFalse(access.is_admin)
        self.assertIn('not found', access.error)

    def test_unknown_account_gets_message(self):
        self.assertIn('has no TinyLib access', self.user('studio\\stranger').error)

    def test_windows_denied_write_is_reported(self):
        denied = PermissionError(13, 'Access is denied')
        with patch('tinylib.access.atomic_json', side_effect=denied):
            with self.assertRaisesRegex(PermissionError, 'Windows denied writing'):
                self.admin.save({'studio\\first': DEFAULT_GROUPS}, DEFAULT_GROUPS, self.admin.data['libraries'])
        artist = {'studio\\first': DEFAULT_GROUPS, 'studio\\artist': ['users']}
        with self.assertRaisesRegex(PermissionError, 'Only admins'):
            self.user().save(artist, DEFAULT_GROUPS, [])

    def test_security_read_only_wins(self):
        data = json.loads(self.path.read_text())
        data['libraries'][0]['read_only'] = True
        atomic_json(self.path, data)
        self.admin.refresh()
        self.assertTrue(self.admin.read_only(self.library['root']))
        self.assertFalse(self.admin.read_only(self.root / 'unlisted'))

    def test_collection_round_trip_and_invalid_import_is_atomic(self):
        prefs = Preferences(self.root / 'preferences.json')
        identifier = prefs.create_collection('Picks')
        asset = {'name': 'Asset', 'main': 'C:/asset.exr', 'library_root': self.library['root']}
        prefs.add_assets(identifier, [asset, asset])
        exported = self.root / 'picks.json'
        prefs.export_collection(identifier, exported)
        imported = prefs.import_collection(exported)
        self.assertEqual(prefs.collection(imported)['name'], 'Picks (2)')
        self.assertEqual(prefs.collection(imported)['assets'], prefs.collection(identifier)['assets'])
        before = prefs.path.read_bytes()
        atomic_json(exported, {'format': 'tinylib.collection', 'version': 1, 'name': 'Bad', 'assets': [{'main': 3}]})
        with self.assertRaises(ValueError):
            prefs.import_collection(exported)
        self.assertEqual(prefs.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
