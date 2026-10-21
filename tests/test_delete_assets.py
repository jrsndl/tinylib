import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch
from tinylib.access import AccessControl, root_key
from tinylib.asset_edit import DeleteStopped, delete_assets
from tinylib.edit_lock import LibraryEditLocked, acquire_edit_lock
from tinylib.library import Library, atomic_json, read_json


class DeleteAssetsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'library'
        records = []
        for name in ('Fire', 'Smoke', 'Water'):
            folder = self.root / 'fx' / name
            (folder / 'main').mkdir(parents=True)
            (folder / 'main' / (name + '.1001.exr')).write_bytes(b'x')
            (folder / 'thumb').mkdir()
            (folder / 'thumb' / (name + '.jpg')).write_bytes(b'x')
            records.append({'id': 'fx/' + name, 'name': name, 'category': 'fx', 'kind': 'footage',
                            'main': 'fx/%s/main/%s.####.exr' % (name, name), 'first': 1001, 'last': 1001,
                            'thumb': 'fx/%s/thumb/%s.jpg' % (name, name), 'tags': [], 'colorspace': 'ACEScg',
                            'metadata': {}})
        atomic_json(self.root / 'tinylib_data.json',
                    {'schema_version': 3, 'custom_key': 'kept', 'assets': records})
        self.library = Library(self.root)
        self.assets = {asset['name']: asset for asset in self.library.load(use_cache=False)}
        self.lock = acquire_edit_lock(self.root, 'tester')

    def names(self):
        return [record['name'] for record in read_json(self.root / 'tinylib_data.json')['assets']]

    def test_database_only_keeps_files_and_other_keys(self):
        problems = self.library.delete_assets([self.assets['Fire']], edit_token=self.lock['token'])
        self.assertEqual(problems, [])
        self.assertEqual(self.names(), ['Smoke', 'Water'])
        self.assertEqual(read_json(self.root / 'tinylib_data.json')['custom_key'], 'kept')
        self.assertTrue((self.root / 'fx' / 'Fire' / 'main').is_dir())

    def test_storage_and_database_removes_only_the_asset_folder(self):
        problems = self.library.delete_assets([self.assets['Fire'], self.assets['Smoke']],
                                              edit_token=self.lock['token'], remove_files=True)
        self.assertEqual(problems, [])
        self.assertEqual(self.names(), ['Water'])
        self.assertFalse((self.root / 'fx' / 'Fire').exists())
        self.assertFalse((self.root / 'fx' / 'Smoke').exists())
        self.assertTrue((self.root / 'fx' / 'Water' / 'main').is_dir())
        self.assertTrue((self.root / 'fx').is_dir())

    def test_files_outside_the_ingest_layout_are_kept(self):
        fire = dict(self.assets['Fire'], name='Renamed')  # folder no longer <category>/<name>
        data = read_json(self.root / 'tinylib_data.json')
        data['assets'][0]['name'] = 'Renamed'
        atomic_json(self.root / 'tinylib_data.json', data)
        problems = self.library.delete_assets([fire], edit_token=self.lock['token'], remove_files=True)
        self.assertEqual(len(problems), 1)
        self.assertIn('files kept', problems[0])
        self.assertNotIn('Renamed', self.names())
        self.assertTrue((self.root / 'fx' / 'Fire' / 'main').is_dir())

    def test_folder_shared_with_another_asset_is_kept(self):
        data = read_json(self.root / 'tinylib_data.json')
        data['assets'][1]['main'] = 'fx/Fire/main/Fire.####.exr'  # Smoke points into Fire's folder
        atomic_json(self.root / 'tinylib_data.json', data)
        problems = self.library.delete_assets([self.assets['Fire']], edit_token=self.lock['token'],
                                              remove_files=True)
        self.assertIn('another asset', problems[0])
        self.assertTrue((self.root / 'fx' / 'Fire').is_dir())

    def test_symlinked_asset_folder_is_never_followed(self):
        # fx/Ghost -> fx/Smoke: deleting Ghost's files must not remove Smoke's.
        (self.root / 'fx' / 'Ghost').symlink_to(self.root / 'fx' / 'Smoke', target_is_directory=True)
        data = read_json(self.root / 'tinylib_data.json')
        data['assets'].append(dict(data['assets'][1], id='fx/Ghost', name='Ghost',
                                   main='fx/Ghost/main/Smoke.####.exr'))
        atomic_json(self.root / 'tinylib_data.json', data)
        ghost = next(a for a in Library(self.root).load(use_cache=False) if a['name'] == 'Ghost')
        problems = self.library.delete_assets([ghost], edit_token=self.lock['token'], remove_files=True)
        self.assertIn('link', problems[0])
        self.assertTrue((self.root / 'fx' / 'Smoke' / 'main' / 'Smoke.1001.exr').is_file())

    def test_kept_asset_spelled_through_a_link_protects_the_folder(self):
        # Water's main points into Fire's folder through a link elsewhere in the library.
        (self.root / 'alias').symlink_to(self.root / 'fx' / 'Fire', target_is_directory=True)
        data = read_json(self.root / 'tinylib_data.json')
        data['assets'][2]['main'] = 'alias/main/Fire.####.exr'
        atomic_json(self.root / 'tinylib_data.json', data)
        problems = self.library.delete_assets([self.assets['Fire']], edit_token=self.lock['token'],
                                              remove_files=True)
        self.assertIn('another asset', problems[0])
        self.assertTrue((self.root / 'fx' / 'Fire').is_dir())

    def test_folder_asset_whose_main_is_the_folder_protects_it(self):
        data = read_json(self.root / 'tinylib_data.json')
        data['assets'][2].update(kind='folder', main='fx/Fire')
        atomic_json(self.root / 'tinylib_data.json', data)
        problems = self.library.delete_assets([self.assets['Fire']], edit_token=self.lock['token'],
                                              remove_files=True)
        self.assertIn('another asset', problems[0])
        self.assertTrue((self.root / 'fx' / 'Fire').is_dir())

    def test_library_reached_through_a_link_reports_nothing_kept(self):
        linked = self.root.parent / 'linked'
        linked.symlink_to(self.root, target_is_directory=True)
        library = Library(linked)
        fire = next(a for a in library.load(use_cache=False) if a['name'] == 'Fire')
        problems = library.delete_assets([fire], edit_token=self.lock['token'], remove_files=True)
        self.assertEqual(problems, [])
        self.assertFalse((self.root / 'fx' / 'Fire').exists())

    def test_unsafe_names_keep_files(self):
        for name, category in (('..', 'fx'), ('Fire', '..'), ('', 'fx'), ('a/b', 'fx')):
            folder, reason = self.library.asset_folder(dict(self.assets['Fire'], name=name, category=category))
            self.assertIsNone(folder, (name, category))
            self.assertTrue(reason)

    def test_library_without_database_is_refused(self):
        (self.root / 'tinylib_data.json').unlink()
        scanned = Library(self.root)
        assets = scanned.load(use_cache=False)
        with self.assertRaisesRegex(ValueError, 'no tinylib_data.json'):
            scanned.delete_assets(assets[:1], edit_token=self.lock['token'])
        self.assertFalse((self.root / 'tinylib_data.json').exists())

    def test_legacy_database_is_backed_up_and_converted(self):
        entries = [{'source': '%s/fx/%s/main/%s.####.exr 1001-1001' % (self.root.as_posix(), name, name), 'tags': []}
                   for name in ('Fire', 'Smoke', 'Water')]
        atomic_json(self.root / 'tinylib_data.json', {'data': {'lib': {'fx': entries}}})
        library = Library(self.root)
        fire = next(a for a in library.load(use_cache=False) if a['name'] == 'Fire')
        library.delete_assets([fire], edit_token=self.lock['token'])
        data = read_json(self.root / 'tinylib_data.json')
        self.assertEqual(data['schema_version'], 3)
        self.assertEqual([a['name'] for a in data['assets']], ['Smoke', 'Water'])
        self.assertEqual(len(read_json(self.root / 'data.legacy.backup.json')['data']['lib']['fx']), 3)

    def test_failed_file_deletion_is_reported_after_the_database_write(self):
        with patch('tinylib.library.shutil.rmtree'):  # deletes nothing
            problems = self.library.delete_assets([self.assets['Fire']], edit_token=self.lock['token'],
                                                  remove_files=True)
        self.assertIn('could not delete', problems[0])
        self.assertNotIn('Fire', self.names())

    def test_duplicates_are_named(self):
        data = read_json(self.root / 'tinylib_data.json')
        data['assets'].append(dict(data['assets'][0]))
        atomic_json(self.root / 'tinylib_data.json', data)
        with self.assertRaisesRegex(ValueError, 'times'):
            self.library.delete_assets([self.assets['Fire']], edit_token=self.lock['token'])

    def test_changed_asset_writes_nothing(self):
        stale = dict(self.assets['Fire'], main=str(self.root / 'fx/Fire/main/Other.####.exr'))
        with self.assertRaisesRegex(ValueError, 'changed or disappeared'):
            self.library.delete_assets([self.assets['Smoke'], stale], edit_token=self.lock['token'],
                                       remove_files=True)
        self.assertEqual(self.names(), ['Fire', 'Smoke', 'Water'])
        self.assertTrue((self.root / 'fx' / 'Smoke').is_dir())

    def test_needs_the_edit_lock_token(self):
        with self.assertRaises(LibraryEditLocked):
            self.library.delete_assets([self.assets['Fire']], edit_token='wrong')
        self.assertEqual(len(self.names()), 3)

    def test_permission_checked_wrapper(self):
        library = {'name': 'Library', 'root': str(self.root)}
        settings = {'libraries': [library], '_config_path': str(self.root / 'studio.json')}
        access = AccessControl(settings, identity='tester', persist=False)
        token = {root_key(str(self.root)): self.lock['token']}
        problems = delete_assets(settings, access, [self.assets['Water']], token, remove_files=True)
        self.assertEqual(problems, [])
        self.assertEqual(self.names(), ['Fire', 'Smoke'])
        settings['libraries'][0]['read_only'] = True
        with self.assertRaises(PermissionError):
            delete_assets(settings, access, [self.assets['Fire']], token)

    def test_only_administrators_can_delete(self):
        library = {'name': 'Library', 'root': str(self.root)}
        settings = {'libraries': [library], '_config_path': str(self.root / 'studio.json')}
        access = AccessControl(settings, identity='tester', persist=False)
        token = {root_key(str(self.root)): self.lock['token']}
        with patch.object(type(access), 'is_admin', new_callable=PropertyMock, return_value=False):
            with self.assertRaisesRegex(PermissionError, 'administrators'):
                delete_assets(settings, access, [self.assets['Water']], token, remove_files=True)
        self.assertEqual(self.names(), ['Fire', 'Smoke', 'Water'])
        self.assertTrue((self.root / 'fx' / 'Water').is_dir())

    def test_stop_in_a_second_library_reports_what_was_deleted(self):
        other_root = self.root.parent / 'other'
        other_root.mkdir()
        atomic_json(other_root / 'tinylib_data.json', {'schema_version': 3, 'assets': []})
        libraries = [{'name': 'Library', 'root': str(self.root)}, {'name': 'Other', 'root': str(other_root)}]
        settings = {'libraries': libraries, '_config_path': str(self.root / 'studio.json')}
        access = AccessControl(settings, identity='tester', persist=False)
        missing = dict(self.assets['Smoke'], id='x/Gone', library_root=str(other_root))
        tokens = {root_key(str(self.root)): self.lock['token']}
        with self.assertRaises(DeleteStopped) as caught:
            delete_assets(settings, access, [self.assets['Fire'], missing], tokens)
        self.assertEqual([a['name'] for a in caught.exception.deleted], ['Fire'])
        self.assertNotIn('Fire', self.names())


if __name__ == '__main__':
    unittest.main()
