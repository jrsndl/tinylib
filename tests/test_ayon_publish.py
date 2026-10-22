import csv
import datetime
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tinylib.actions import Action

ACTION = Path(__file__).resolve().parents[1] / 'actions' / 'ayon_publish'


def load(name):
    key = '_tinylib_ayon_publish_' + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, ACTION / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


pub = load('publish_csv')
cred = load('credentials')
query = load('ayon_query')


def footage(**values):
    asset = {'name': 'smoke_puff 01', 'library': 'FX', 'kind': 'footage', 'colorspace': 'ACEScg',
             'main': 'L:\\lib\\fx\\smoke\\main\\smoke.####.exr', 'first': 1001, 'last': 1100,
             'proxy': 'L:\\lib\\fx\\smoke\\proxy\\smoke.mp4', 'thumb': 'L:\\lib\\fx\\smoke\\thumb\\thumb.jpg',
             'metadata': {'frame_rate': 24.0}}
    asset.update(values)
    return asset


def still(**values):
    asset = {'name': 'sky', 'library': 'Refs', 'kind': 'still', 'colorspace': 'sRGB',
             'main': 'L:/lib/refs/sky/main/sky.exr', 'proxy': 'L:/lib/refs/sky/proxy/sky.jpg',
             'thumb': 'L:/lib/refs/sky/thumb/thumb.jpg', 'metadata': {}}
    asset.update(values)
    return asset


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, item):
        return self.store.get((service, item))

    def set_password(self, service, item, value):
        self.store[(service, item)] = value

    def delete_password(self, service, item):
        self.store.pop((service, item), None)


class RowTests(unittest.TestCase):
    def setUp(self):
        self.opts = pub.options(json.loads((ACTION / 'config.json').read_text()))

    def test_manifest_is_valid_and_offers_footage_and_stills_in_nuke_and_standalone(self):
        action = Action(ACTION)
        self.assertEqual(action.manifest['name'], 'Publish to AYON')
        self.assertTrue(action.accepts([footage(), still()], 'nuke'))
        self.assertTrue(action.accepts([footage()], 'standalone'))
        self.assertFalse(action.accepts([footage(kind='model')], 'standalone'))
        self.assertFalse(action.accepts([footage(thumb='')], 'standalone'))

    def test_footage_sequence_is_a_render_with_main_and_proxy_components(self):
        rows = pub.asset_rows(footage(), '/shots/010/sh010', 'comp', 'smoke_puff_01', self.opts, '4', 'note',
                              ftrack=True)
        self.assertEqual(len(rows), 2)
        main, proxy = rows
        self.assertEqual(main['File Path'], 'L:/lib/fx/smoke/main/smoke.####.exr')
        self.assertEqual((main['Representation'], main['Representation Tags']), ('exr', 'passing'))
        self.assertEqual(main['Representation Colorspace'], 'ACEScg')
        self.assertEqual((main['Product Type'], main['Variant'], main['Version']), ('render', 'smoke_puff_01', '4'))
        self.assertEqual((main['Frame Start'], main['Frame End'], main['FPS']), ('1001', '1100', '24'))
        self.assertEqual(main['Version Thumbnail'], 'L:/lib/fx/smoke/thumb/thumb.jpg')
        self.assertEqual((main['Folder Path'], main['Task Name'], main['Version Comment']),
                         ('/shots/010/sh010', 'comp', 'note'))
        self.assertEqual(proxy['File Path'], 'L:/lib/fx/smoke/proxy/smoke.mp4')
        self.assertEqual((proxy['Representation'], proxy['Representation Tags']),
                         ('mp4', 'passing;ftrackreview;webreview'))
        self.assertEqual(proxy['Representation Colorspace'], 'Output - Rec.709')

    def test_without_ftrack_credentials_the_ftrack_tag_is_left_out(self):
        rows = pub.asset_rows(footage(), '/a', 't', 'Main', self.opts, '1')
        self.assertEqual(rows[1]['Representation Tags'], 'passing;webreview')

    def test_components_can_be_enabled_renamed_and_highres_added(self):
        parts = pub.components([
            {'component': 'main', 'enabled': False},
            {'component': 'highres', 'enabled': True, 'representation': '{extension}_highres',
             'colorspace': '{colorspace}'}])
        self.assertEqual([part['component'] for part in parts], ['main', 'proxy', 'highres'])
        asset = footage(highres='L:/h/smoke_hr.####.exr', highres_first=1001, highres_last=1100)
        rows = pub.asset_rows(asset, '/a', 't', 'Main', pub.options({'colorspace_map': {'ACEScg': 'ACES - ACEScg'}}),
                              '1', parts=parts)
        self.assertEqual([row['Representation'] for row in rows], ['mp4', 'exr_highres'])
        self.assertEqual(rows[1]['File Path'], 'L:/h/smoke_hr.####.exr')
        self.assertEqual(rows[1]['Representation Colorspace'], 'ACES - ACEScg')
        # Highres enabled but the asset has none: no row.
        self.assertEqual(len(pub.asset_rows(footage(), '/a', 't', 'Main', self.opts, '1', parts=parts)), 1)
        same = pub.components([{'component': 'highres', 'enabled': True}])
        with self.assertRaises(ValueError):  # main and highres would both be "exr"
            pub.asset_rows(asset, '/a', 't', 'Main', self.opts, '1', parts=same)
        with self.assertRaises(ValueError):
            pub.components([{'component': 'filmstrip'}])
        with self.assertRaises(ValueError):
            pub.asset_rows(footage(proxy=''), '/a', 't', 'Main', self.opts, '1',
                           parts=pub.components([{'component': 'main', 'enabled': False}]))

    def test_container_footage_uses_metadata_frames_and_printf_padding_becomes_hashes(self):
        rows = pub.asset_rows(footage(main='L:/m/clip.mov', first=None, last=None,
                                      metadata={'frame_rate': 23.976, 'frame_start': 0, 'frame_end': 99}),
                              '/a', 't', 'Clip', self.opts)
        self.assertEqual((rows[0]['Frame Start'], rows[0]['Frame End'], rows[0]['FPS']), ('0', '99', '23.976'))
        self.assertEqual(rows[0]['Representation'], 'mov')
        self.assertEqual(pub.sequence('a/b.%04d.exr 1-5'), ('a/b.####.exr', 1, 5))

    def test_still_is_an_image_and_a_jpeg_proxy_of_a_jpeg_main_joins_the_main(self):
        rows = pub.asset_rows(still(), '/a', 't', 'Sky', self.opts)
        self.assertEqual([row['Representation'] for row in rows], ['exr', 'jpg'])
        self.assertEqual(rows[0]['Product Type'], 'image')
        self.assertEqual((rows[0]['Frame Start'], rows[0]['Frame End'], rows[0]['FPS']), ('1', '1', '25'))
        same = pub.asset_rows(still(main='L:/m/sky.jpg'), '/a', 't', 'Sky', self.opts, ftrack=True)
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]['Representation Tags'], 'passing;ftrackreview;webreview')
        self.assertEqual(len(pub.asset_rows(still(proxy=''), '/a', 't', 'Sky', self.opts)), 1)

    def test_defaults_variant_from_name_version_from_record_and_comment(self):
        self.assertEqual(pub.default_variant(footage(label='Nice label')), 'smoke_puff_01')
        self.assertEqual(pub.default_variant({'name': 'a-b c'}), 'a_b_c')
        self.assertEqual(pub.default_variant({'name': '--'}), 'Main')
        self.assertEqual(pub.default_version(footage(version_number=7)), 7)
        self.assertEqual(pub.default_version(footage()), 1)
        self.assertEqual(pub.default_version(footage(version_number='')), 1)
        self.assertEqual(pub.default_comment(footage(), self.opts), 'TinyLib: FX / smoke_puff 01')

    def test_variants_and_versions_are_checked(self):
        with self.assertRaises(ValueError):
            pub.asset_rows(footage(), '/a', 't', 'bad name', self.opts)
        with self.assertRaises(ValueError):
            pub.asset_rows(footage(), '/a', 't', 'Main', self.opts, 'v2')
        with self.assertRaises(ValueError):
            pub.check_variants([(footage(), 'Smoke'), (footage(name='other'), 'smoke')])
        pub.check_variants([(footage(), 'Smoke'), (still(), 'Smoke')])  # render vs image: two products
        with self.assertRaises(ValueError):
            pub.asset_rows(footage(kind='model'), '/a', 't', 'Main', self.opts)

    def test_csv_has_the_ingest_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pub.csv_path(pub.options({'log_folder': folder}), datetime.datetime(2026, 10, 22, 18, 0, 5))
            self.assertEqual(path.name, 'tinylib_ayon_20261022_180005.csv')
            pub.write_csv(pub.asset_rows(footage(), '/a', 't', 'Main', self.opts), path)
            with open(path, newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows[0]), pub.COLUMNS)
        self.assertEqual(rows[1]['Representation'], 'mp4')

    def test_executable_command_and_environment(self):
        env = {'AYON_EXECUTABLE': 'C:/AYON 1.6.3/ayon.exe', 'PYTHONPATH': 'nuke', 'AYON_HOST_NAME': 'nuke',
               'AYON_TASK_NAME': 'comp', 'PATH': 'p'}
        found = pub.find_executable(self.opts, {}, env, exists=lambda path: True)
        self.assertEqual(found.replace('\\', '/'), 'C:/AYON 1.6.3/ayon_console.exe')
        found = pub.find_executable(self.opts, {'tools': {'ayon_console': 'D:/ayon_console.exe'}}, env,
                                    exists=lambda path: True)
        self.assertEqual(found, 'D:/ayon_console.exe')
        with self.assertRaises(FileNotFoundError):
            pub.find_executable(self.opts, {}, {}, exists=lambda path: False)
        args = pub.command('ayon_console.exe', 'C:/l/x.csv', 'Proj', '/shots/sh010', 'comp', self.opts)
        self.assertEqual(args, ['ayon_console.exe', 'addon', 'traypublisher', 'ingestcsv', '--filepath', 'C:/l/x.csv',
                                '--project', 'Proj', '--folder-path', '/shots/sh010', '--task', 'comp',
                                '--preset', 'Default', '--ignore-validators'])
        args = pub.command('a', 'x.csv', 'P', '/shots/sh010', 'comp',
                           pub.options({'csv_folder_path': '/edit/csvingest', 'csv_task': 'csvingest'}))
        self.assertEqual(args[args.index('--folder-path') + 1], '/edit/csvingest')
        self.assertEqual(args[args.index('--task') + 1], 'csvingest')
        credentials = {'ayon_url': 'http://ayon:5000', 'ayon_key': 'k', 'ftrack_url': 'https://f',
                       'ftrack_user': 'jiri', 'ftrack_key': 'fk'}
        result = pub.environment(credentials, self.opts, env)
        self.assertNotIn('PYTHONPATH', result)
        self.assertNotIn('AYON_HOST_NAME', result)
        self.assertNotIn('AYON_TASK_NAME', result)
        self.assertEqual((result['PATH'], result['AYON_SERVER_URL'], result['AYON_API_KEY']), ('p', 'http://ayon:5000', 'k'))
        self.assertEqual((result['FTRACK_SERVER'], result['FTRACK_API_USER'], result['FTRACK_API_KEY']),
                         ('https://f', 'jiri', 'fk'))
        result = pub.environment(dict(credentials, ftrack_key=''), self.opts, env)
        self.assertNotIn('FTRACK_API_KEY', result)


class BatchAndDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.opts = pub.options(json.loads((ACTION / 'config.json').read_text()))
        self.items = [(footage(), [{'a': 1}, {'a': 2}]), (still(), [{'a': 3}])]
        self.credentials = {'ayon_url': 'http://ayon:5000', 'ayon_key': 'k', 'ftrack_url': 'https://f',
                            'ftrack_user': 'jiri', 'ftrack_key': 'fk'}

    def test_one_csv_for_all_or_one_per_asset(self):
        self.assertEqual(pub.batches(self.items, False), [('2 assets', [{'a': 1}, {'a': 2}, {'a': 3}])])
        self.assertEqual(pub.batches(self.items, True),
                         [('smoke_puff 01', [{'a': 1}, {'a': 2}]), ('sky', [{'a': 3}])])
        self.assertEqual(pub.batches(self.items[:1], False)[0][0], 'smoke_puff 01')
        path = pub.csv_path(self.opts, datetime.datetime(2026, 1, 2, 3, 4, 5), folder='F', suffix='01_smoke puff')
        self.assertEqual(path, Path('F') / 'tinylib_ayon_20260102_030405_01_smoke_puff.csv')

    def test_deadline_folder_is_the_config_or_the_spool(self):
        self.assertEqual(pub.deadline_folder(self.opts, {'deadline': {'spool_root': '//farm/spool'}}),
                         Path('//farm/spool') / 'ayon_publish')
        self.assertEqual(pub.deadline_folder(dict(self.opts, deadline_csv_folder='//farm/ayon'), {}), Path('//farm/ayon'))
        with self.assertRaises(ValueError):
            pub.deadline_folder(self.opts, {})

    def test_deadline_job_runs_the_ingest_command_with_credentials(self):
        args = pub.command('C:/AYON 1.6.3/ayon_console.exe', '//farm/ayon/x.csv', 'Proj', '/shots/sh010', 'comp', self.opts)
        environment = pub.deadline_environment(self.credentials, self.opts)
        self.assertEqual(environment['AYON_API_KEY'], 'k')
        self.assertEqual(environment['FTRACK_API_KEY'], 'fk')
        job_info, plugin_info = pub.deadline_job('AYON publish', args, environment, self.opts,
                                                 {'deadline': {'pool': 'comp', 'group': 'win', 'priority': 60}})
        self.assertEqual((job_info['Plugin'], job_info['Pool'], job_info['Group'], job_info['Priority']),
                         ('CommandLine', 'comp', 'win', '60'))
        values = [value for key, value in job_info.items() if key.startswith('EnvironmentKeyValue')]
        self.assertIn('AYON_SERVER_URL=http://ayon:5000', values)
        self.assertIn('FTRACK_API_USER=jiri', values)
        self.assertEqual(plugin_info['Executable'], 'C:/AYON 1.6.3/ayon_console.exe')
        self.assertTrue(plugin_info['Arguments'].startswith('addon traypublisher ingestcsv --filepath //farm/ayon/x.csv'))
        opts = dict(self.opts, deadline_send_credentials=False, deadline_ayon_executable='A:/ayon_console.exe',
                    deadline={'pool': 'ayon'}, deadline_environment={'AYON_SITE_ID': 'farm'})
        environment = pub.deadline_environment(self.credentials, opts)
        self.assertEqual(environment, {'AYON_SERVER_URL': 'http://ayon:5000', 'AYON_SITE_ID': 'farm'})
        job_info, plugin_info = pub.deadline_job('n', args, environment, opts, {'deadline': {'pool': 'comp'}})
        self.assertEqual((job_info['Pool'], plugin_info['Executable']), ('ayon', 'A:/ayon_console.exe'))


class QueryTests(unittest.TestCase):
    def test_folder_tree_nests_folders_and_tasks(self):
        class Connection:
            def get_folders(self, project, fields):
                assert project == 'Proj' and 'path' in fields
                return [{'id': 'f2', 'name': 'sh010', 'path': '/shots/sh010', 'parentId': 'f1'},
                        {'id': 'f1', 'name': 'shots', 'path': '/shots', 'parentId': None},
                        {'id': 'f3', 'name': 'assets', 'path': '/assets', 'parentId': None}]

            def get_tasks(self, project, fields):
                return [{'id': 't1', 'name': 'comp', 'folderId': 'f2'}, {'id': 't2', 'name': 'x', 'folderId': 'gone'}]

        roots = query.folder_tree(Connection(), 'Proj')
        self.assertEqual(sorted(root['name'] for root in roots), ['assets', 'shots'])
        shots = next(root for root in roots if root['name'] == 'shots')
        self.assertEqual(shots['children'][0]['path'], '/shots/sh010')
        self.assertEqual([task['name'] for task in shots['children'][0]['tasks']], ['comp'])


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.keyring = FakeKeyring()
        cred.set_keyring_backend(self.keyring)
        self.addCleanup(cred.set_keyring_backend, None)

    def test_environment_wins_as_in_nuke_started_from_ayon(self):
        env = {'AYON_SERVER_URL': 'http://192.168.100.21:5000/', 'AYON_API_KEY': '4f7',
               'FTRACK_SERVER': 'https://bcvfx.ftrackapp.com', 'FTRACK_API_USER': 'jiri.sindelar',
               'FTRACK_API_KEY': 'NjN'}
        self.assertTrue(cred.ayon_from_environment(env))
        result = cred.resolve({}, env)
        self.assertEqual(result['sources'], {'ayon': 'environment', 'ftrack': 'environment'})
        self.assertEqual((result['ayon_url'], result['ayon_key']), ('http://192.168.100.21:5000', '4f7'))
        self.assertTrue(cred.has_ftrack(result))
        self.assertTrue(cred.ensure_ayon(result, check=lambda url, token: self.fail('env tokens are not checked')))

    def test_standalone_uses_the_keyring_and_drops_an_expired_token(self):
        config = {'ayon_server_url': 'http://ayon:5000', 'ftrack_server': 'https://f.ftrackapp.com'}
        result = cred.resolve(config, {})
        self.assertEqual(result['sources'], {'ayon': None, 'ftrack': None})
        self.assertFalse(cred.has_ftrack(result))
        cred.save_ayon('http://ayon:5000', 'jiri', 'token1')
        cred.save_ftrack('https://f.ftrackapp.com', 'jiri.sindelar', 'key')
        self.assertIn(('TinyLib/ayon/ayon', 'token'), self.keyring.store)
        result = cred.resolve(config, {})
        self.assertEqual(result['sources'], {'ayon': 'keyring', 'ftrack': 'keyring'})
        self.assertEqual((result['ayon_user'], result['ftrack_user']), ('jiri', 'jiri.sindelar'))
        self.assertTrue(cred.ensure_ayon(result, check=lambda url, token: True))
        self.assertFalse(cred.ensure_ayon(result, check=lambda url, token: False))
        self.assertEqual(cred.get_ayon('http://ayon:5000'), ('jiri', None))  # The user name pre-fills the sign-in.
        cred.clear_ftrack('https://f.ftrackapp.com')
        self.assertFalse(cred.has_ftrack(cred.resolve(config, {})))

    def test_typed_server_url_is_remembered_and_nothing_breaks_without_a_keyring(self):
        cred.save_server_url(cred.AYON, 'http://typed:5000')
        self.assertEqual(cred.resolve({}, {})['ayon_url'], 'http://typed:5000')
        cred.set_keyring_backend(None)
        result = cred.resolve({}, {})
        self.assertEqual((result['ayon_url'], result['ayon_key']), ('', ''))
        self.assertFalse(cred.save_ayon('http://a', 'u', 't'))
        self.assertEqual(cred.hostname('https://Studio.ftrackapp.com/'), 'studio.ftrackapp.com')


if __name__ == '__main__':
    unittest.main()
