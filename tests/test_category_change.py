"""Category change: asset folders move on disk and records follow, all or nothing."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tinylib.edit_lock import LibraryEditLocked, acquire_edit_lock, release_edit_lock
from tinylib.library import DATABASE_FILENAME, Library
from tinylib.preferences import Preferences, asset_key


class CategoryChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'library'
        self.outside = Path(self.temp.name) / 'elsewhere'
        self.outside.mkdir()
        records = [
            self.asset('fx', 'smoke', 'footage', highres='fx/smoke/highres/smoke_hr.####.exr',
                       extra={'main-original': 'fx/smoke/main/smoke.mov', 'first': 1001, 'last': 1002,
                              'proxy': 'fx/smoke/proxy/smoke.mp4'}),
            self.asset('fx', 'fire', 'still'),
            self.asset('refs', 'sky', 'still'),
        ]
        self.write(records)

    def asset(self, category, name, kind, highres='', extra=None):
        folder = self.root / category / name
        main = 'main/%s.####.exr' % name if kind == 'footage' else 'main/%s.exr' % name
        files = [main.replace('####', '1001'), main.replace('####', '1002'), 'thumb/%s.jpg' % name]
        for key, value in dict(extra or {}, highres=highres).items():
            if isinstance(value, str) and value.startswith(category + '/' + name + '/'):
                relative = value[len(category + '/' + name + '/'):]
                files += [relative.replace('####', '1001'), relative.replace('####', '1002')]
        for relative in set(files):
            (folder / relative).parent.mkdir(parents=True, exist_ok=True)
            (folder / relative).write_bytes(relative.encode())
        record = {'id': category + '/' + name, 'name': name, 'category': category, 'kind': kind,
                  'main': '%s/%s/%s' % (category, name, main), 'thumb': '%s/%s/thumb/%s.jpg' % (category, name, name),
                  'tags': [], 'metadata': {}}
        if highres:
            record['highres'] = highres
        record.update(extra or {})
        return record

    def write(self, records):
        self.root.mkdir(exist_ok=True)
        (self.root / DATABASE_FILENAME).write_text(json.dumps({'schema_version': 3, 'assets': records}))

    def database(self):
        return json.loads((self.root / DATABASE_FILENAME).read_text())

    def loaded(self, *names):
        library = Library(self.root)
        library.load(use_cache=False)
        assets = {asset['name']: dict(asset, library_root=str(self.root)) for asset in library.assets}
        return library, [assets[name] for name in names]

    def tree(self):
        return sorted(str(path.relative_to(self.root)).replace('\\', '/') for path in self.root.rglob('*')
                      if path.is_file() and path.name != DATABASE_FILENAME and '.lock' not in path.name)

    def test_folder_and_record_move_together(self):
        library, (smoke,) = self.loaded('smoke')
        moves = library.change_category([smoke], 'blood')
        self.assertEqual(len(moves), 1)
        self.assertFalse((self.root / 'fx' / 'smoke').exists())
        self.assertTrue((self.root / 'blood' / 'smoke' / 'main' / 'smoke.1001.exr').is_file())
        self.assertTrue((self.root / 'fx' / 'fire').is_dir())  # the rest of the category stays
        record = next(r for r in self.database()['assets'] if r['name'] == 'smoke')
        self.assertEqual((record['id'], record['category']), ('blood/smoke', 'blood'))
        self.assertEqual(record['main'], 'blood/smoke/main/smoke.####.exr')
        self.assertEqual(record['main-original'], 'blood/smoke/main/smoke.mov')
        self.assertEqual(record['highres'], 'blood/smoke/highres/smoke_hr.####.exr')
        self.assertEqual(record['proxy'], 'blood/smoke/proxy/smoke.mp4')
        self.assertEqual((record['first'], record['last']), (1001, 1002))
        new = moves[0][1]
        self.assertEqual(Path(new['main']), self.root / 'blood' / 'smoke' / 'main' / 'smoke.####.exr')
        reloaded = self.loaded('smoke')[1][0]
        self.assertEqual(reloaded['category'], 'blood')
        for role in ('main-original', 'thumb', 'proxy'):
            self.assertTrue(Path(reloaded[role]).is_file(), role)

    def test_existing_category_ranges_absolute_paths_become_relative_and_empty_category_folder(self):
        records = self.database()['assets']
        records[2]['thumb'] = str(self.root / 'refs' / 'sky' / 'thumb' / 'sky.jpg').replace('\\', '/')
        records[2]['highres'] = 'refs/sky/main/sky.exr 1-1'
        self.write(records)
        library, (sky,) = self.loaded('sky')
        library.change_category([sky], 'fx')
        record = next(r for r in self.database()['assets'] if r['name'] == 'sky')
        self.assertEqual(record['thumb'], 'fx/sky/thumb/sky.jpg')  # written relative, as ingest does
        self.assertEqual(record['highres'], 'fx/sky/main/sky.exr 1-1')
        self.assertFalse((self.root / 'refs').exists())  # left empty, removed

    def test_several_assets_and_unchanged_ones_skipped(self):
        library, (smoke, fire, sky) = self.loaded('smoke', 'fire', 'sky')
        moves = library.change_category([smoke, fire, sky], 'refs')
        self.assertEqual(sorted(old['name'] for old, _ in moves), ['fire', 'smoke'])
        self.assertEqual(sorted(r['id'] for r in self.database()['assets']), ['refs/fire', 'refs/sky', 'refs/smoke'])
        self.assertFalse((self.root / 'fx').exists())
        self.assertEqual(library.change_category(self.loaded('sky')[1], 'refs'), [])

    def assert_nothing_moved(self, before_tree, before_database):
        self.assertEqual(self.tree(), before_tree)
        self.assertEqual(self.database(), before_database)

    def refused(self, assets, category, message, library=None):
        library = library or Library(self.root)
        before = (self.tree(), self.database())
        with self.assertRaisesRegex((ValueError, LibraryEditLocked), message):
            library.change_category(assets, category)
        self.assert_nothing_moved(*before)

    def test_refusals_change_nothing(self):
        library, (smoke, fire) = self.loaded('smoke', 'fire')
        (self.root / 'blood' / 'smoke').mkdir(parents=True)
        self.refused([smoke], 'blood', 'already exists')
        self.refused([smoke], 'bad/name', 'Invalid folder name')
        self.refused([smoke], '..', 'Invalid folder name')
        self.refused([smoke], '.tinylib-staging', 'dot')
        self.refused([dict(smoke, main=smoke['main'] + 'x')], 'other', 'changed or disappeared')
        records = self.database()['assets']
        records[0]['highres'] = str(self.outside / 'hr.exr').replace('\\', '/')
        self.write(records)
        library, (smoke,) = self.loaded('smoke')
        self.refused([smoke, fire], 'other', 'outside its folder')

    def test_same_name_twice_into_one_category_is_refused(self):
        records = self.database()['assets']
        records.append(self.asset('refs', 'fire', 'still'))
        self.write(records)
        library = Library(self.root)
        library.load(use_cache=False)
        fires = [dict(a, library_root=str(self.root)) for a in library.assets if a['name'] == 'fire']
        self.refused(fires, 'other', 'already exists')

    def test_a_shared_folder_is_refused(self):
        records = self.database()['assets']
        records.append(dict(self.asset('fx', 'smoke', 'still'), id='fx/smoke-still', name='smoke-still',
                            main='fx/smoke/main/smoke.1001.exr'))
        self.write(records)
        library, (smoke,) = self.loaded('smoke')
        self.refused([smoke], 'other', 'has files in')

    def test_another_assets_thumbnail_or_scene_in_the_folder_is_refused(self):
        records = self.database()['assets']
        records[1]['thumb'] = 'fx/smoke/thumb/smoke.jpg'  # fire shows smoke's thumbnail
        self.write(records)
        library, (smoke,) = self.loaded('smoke')
        self.refused([smoke], 'other', 'has files in')

    @unittest.skipIf(os.name == 'nt', 'symbolic links need extra rights on Windows')
    def test_another_asset_under_another_spelling_is_refused(self):
        alias = Path(self.temp.name) / 'alias'
        os.symlink(self.root, alias, target_is_directory=True)
        records = self.database()['assets']
        records.append(dict(self.asset('refs', 'cloud', 'still'),
                            main=str(alias / 'fx' / 'smoke' / 'main' / 'smoke.1001.exr').replace('\\', '/')))
        self.write(records)
        library, (smoke,) = self.loaded('smoke')
        self.refused([smoke], 'other', 'under another path')

    def test_a_category_differing_only_in_case_is_refused(self):
        library, (sky,) = self.loaded('sky')
        self.refused([sky], 'FX', "already exists as 'fx'")

    def test_a_database_write_that_landed_despite_an_error_keeps_the_move(self):
        library, (smoke,) = self.loaded('smoke')
        real = __import__('tinylib.library', fromlist=['atomic_json']).atomic_json

        def lands_then_fails(path, data):
            real(path, data)
            raise OSError('The specified network name is no longer available')
        with patch('tinylib.library.atomic_json', side_effect=lands_then_fails):
            moves = library.change_category([smoke], 'blood')
        self.assertEqual(len(moves), 1)
        self.assertTrue((self.root / 'blood' / 'smoke').is_dir())
        self.assertEqual(self.database()['assets'][0]['category'], 'blood')

    def test_another_users_edit_lock_is_respected(self):
        library, (smoke,) = self.loaded('smoke')
        handle = acquire_edit_lock(self.root, 'someone')
        self.addCleanup(release_edit_lock, handle)
        self.refused([smoke], 'other', 'editing the library')
        mine = acquire_edit_lock(self.root, 'me', override=True)
        self.addCleanup(release_edit_lock, mine)
        library.change_category([smoke], 'other', edit_token=mine['token'])
        self.assertTrue((self.root / 'other' / 'smoke').is_dir())

    def test_a_failed_rename_moves_the_others_back(self):
        library, (smoke, fire) = self.loaded('smoke', 'fire')
        before = (self.tree(), self.database())
        real, calls = os.rename, []

        def second_fails(source, target):
            calls.append((source, target))
            if len(calls) == 2:
                raise PermissionError(32, 'The process cannot access the file because it is being used')
            return real(source, target)
        with patch('tinylib.library.os.rename', side_effect=second_fails):
            with self.assertRaises(PermissionError):
                library.change_category([smoke, fire], 'blood')
        self.assert_nothing_moved(*before)
        self.assertFalse((self.root / 'blood').exists())  # the new category folder goes again
        self.assertEqual(len(calls), 3)  # smoke moved, fire failed, smoke moved back

    def test_a_failed_database_write_moves_everything_back(self):
        library, (smoke, fire) = self.loaded('smoke', 'fire')
        before = (self.tree(), self.database())
        with patch('tinylib.library.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                library.change_category([smoke, fire], 'blood')
        self.assert_nothing_moved(*before)

    def test_folders_that_cannot_be_moved_back_are_named(self):
        library, (smoke,) = self.loaded('smoke')
        real = os.rename
        state = {'calls': 0}

        def only_first(source, target):
            state['calls'] += 1
            if state['calls'] > 1:
                raise PermissionError(5, 'Access is denied')
            return real(source, target)
        with patch('tinylib.library.os.rename', side_effect=only_first), \
                patch('tinylib.library.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'move them back by hand') as caught:
                library.change_category([smoke], 'blood')
        self.assertIn(str(self.root / 'blood' / 'smoke'), str(caught.exception))
        self.assertEqual(self.database()['assets'][0]['category'], 'fx')  # database untouched

    @unittest.skipIf(os.name == 'nt', 'symbolic links need extra rights on Windows')
    def test_linked_folders_are_refused(self):
        real = self.root / 'refs'
        linked = Path(self.temp.name) / 'refs-real'
        real.rename(linked)
        os.symlink(linked, real, target_is_directory=True)
        library, (sky,) = self.loaded('sky')
        self.refused([sky], 'other', 'link or junction')
        library, (smoke,) = self.loaded('smoke')
        os.symlink(self.outside, self.root / 'linkedcat', target_is_directory=True)
        self.refused([smoke], 'linkedcat', 'link or junction')


class PreferencesFollowTests(unittest.TestCase):
    def test_ratings_trims_and_collection_entries_follow(self):
        with tempfile.TemporaryDirectory() as folder:
            preferences = Preferences(Path(folder) / 'preferences.json')
            old = {'library_root': 'L:/lib', 'main': 'L:/lib/fx/smoke/main/smoke.####.exr', 'name': 'smoke'}
            new = dict(old, main='L:/lib/blood/smoke/main/smoke.####.exr', category='blood', id='blood/smoke')
            other = {'library_root': 'L:/lib', 'main': 'L:/lib/fx/fire/main/fire.exr', 'name': 'fire'}
            preferences.rate([old, other], 4)
            preferences.set_trim(old, (1001, 1010))
            collection = preferences.create_collection('mine')
            preferences.add_assets(collection, [old])
            preferences.set_trim(old, (1002, 1003), collection)
            preferences.move_assets([(old, new)])
            again = Preferences(Path(folder) / 'preferences.json')
            self.assertEqual((again.rating(new), again.rating(other)), (4, 4))
            self.assertEqual(again.trim(new), (1001, 1010))
            entries = again.collection(collection)['assets']
            self.assertEqual(list(entries), [asset_key(new)])
            self.assertEqual(entries[asset_key(new)]['main'], new['main'])
            self.assertEqual(again.trim(new, collection), (1002, 1003))
            self.assertNotIn(asset_key(old), again.data['ratings'])


if __name__ == '__main__':
    unittest.main()
