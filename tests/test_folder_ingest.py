import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.access import AccessControl
from tinylib.ingest import discover_footage_sources, make_folder_manifests
from helpers import requires_qt


class FolderIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.library = {'name': 'Batch library', 'root': str(self.root)}
        self.settings = {'_config_path': str(self.root / 'studio.json'),
                         'libraries': [self.library], 'profiles': {'preview': {}}, 'tools': {}}
        self.settings['_access'] = AccessControl(self.settings, identity='test', persist=False)
        for name in ('BlueSmoke', 'FireBurst'):
            folder = self.source / name / 'frames'
            folder.mkdir(parents=True)
            for frame in (1001, 1002):
                (folder / ('%s.%d.exr' % (name, frame))).write_bytes(b'x')
        (self.source / 'Water.mov').write_bytes(b'container')
        (self.source / 'poster.jpg').write_bytes(b'not footage')

    def prepare(self):
        return make_folder_manifests(self.settings, self.library, 'effects', str(self.source),
                                     'ACEScg', 'preview', 'proxy')

    def test_discovers_each_sequence_and_container_once_excluding_single_images(self):
        sources = discover_footage_sources(self.source, self.settings)
        self.assertEqual(len(sources), 3)
        self.assertEqual(sum('####' in source for source in sources), 2)
        self.assertEqual(sum(source.endswith('.mov') for source in sources), 1)

    def test_each_manifest_has_automatic_values_and_forced_generated_outputs(self):
        with patch('tinylib.metadata_scan.scan_source_metadata',
                   return_value={'frame_rate': 24000 / 1001, 'width': 2048, 'height': 1024}):
            jobs = self.prepare()
        self.assertEqual([job['name'] for job in jobs], ['BlueSmoke', 'FireBurst', 'Water'])
        self.assertEqual(jobs[0]['tags'], ['blue', 'smoke'])
        for job in jobs:
            self.assertAlmostEqual(job['fps'], 24000 / 1001)
            self.assertEqual(job['metadata']['width'], 2048)
            self.assertEqual(job['preview_source'], 'main')
            self.assertEqual(job['highres'], '')
            self.assertEqual(set(job['media']), {'proxy', 'thumb', 'filmstrip'})
            self.assertTrue(all(spec['mode'] == 'generate' for spec in job['media'].values()))
        self.assertEqual(len({job['job_id'] for job in jobs}), 3)

    def test_duplicate_automatic_names_are_reported_before_metadata_scanning(self):
        (self.source / 'BlueSmoke.mov').write_bytes(b'duplicate stem')
        with patch('tinylib.metadata_scan.scan_source_metadata') as scan:
            with self.assertRaisesRegex(ValueError, 'Duplicate automatic asset name'):
                self.prepare()
            scan.assert_not_called()

    def test_missing_fps_and_existing_destination_fail_preparation(self):
        with patch('tinylib.metadata_scan.scan_source_metadata', return_value={'width': 2048}):
            with self.assertRaisesRegex(ValueError, 'No valid FPS'):
                self.prepare()
        (self.root / 'effects' / 'BlueSmoke').mkdir(parents=True)
        with patch('tinylib.metadata_scan.scan_source_metadata', return_value={'frame_rate': 24}):
            with self.assertRaisesRegex(ValueError, 'Asset folder already exists'):
                self.prepare()

    @requires_qt
    def test_worker_prepares_every_source_before_submitting_any_job(self):
        from tinylib.ingest_ui import Submission
        args = (self.settings, self.library, 'source', 'effects', str(self.source), [],
                'ACEScg', 'preview', {}, 99, 'ignored', 'proxy', 'footage', {})
        worker = Submission(args, self.settings, None, batch=True)
        errors = []
        worker.failed.connect(errors.append)
        with patch('tinylib.metadata_scan.scan_source_metadata', side_effect=[{'frame_rate': 24}, RuntimeError('bad metadata')]), \
                patch('tinylib.ingest_ui.submit_deadline') as submit:
            worker.run()
        submit.assert_not_called()
        self.assertTrue(errors)
        self.assertIn('0 job(s) confirmed', errors[0])
        self.assertIn('bad metadata', errors[0])


class PairingTests(unittest.TestCase):
    """Folder ingest pairing of proxies, highres, thumbnails and filmstrips with their mains."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ingest = self.root / 'ingest' / 'fire'
        self.library = {'name': 'Library', 'root': str(self.root / 'library')}
        Path(self.library['root']).mkdir()
        self.settings = {'_config_path': str(self.root / 'studio.json'),
                         'libraries': [self.library], 'profiles': {'preview': {}}, 'tools': {}}
        self.settings['_access'] = AccessControl(self.settings, identity='test', persist=False)

    def sequence(self, folder, name, frames=(1001, 1002)):
        folder.mkdir(parents=True, exist_ok=True)
        for frame in frames:
            (folder / ('%s.%d.exr' % (name, frame))).write_bytes(b'x')

    def touch(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')
        return path

    def prepare(self, pairing, category='fire'):
        with patch('tinylib.metadata_scan.scan_source_metadata', return_value={'frame_rate': 25}):
            return make_folder_manifests(self.settings, self.library, category, str(self.main_folder),
                                         'ACEScg', 'preview', pairing=pairing)

    def test_clean_file_name_drops_folder_extension_counter_and_separator(self):
        from tinylib.ingest import clean_file_name
        self.assertEqual(clean_file_name('c:/x/assetA.####.exr 1001-1100'), 'assetA')
        self.assertEqual(clean_file_name('c:/x/assetA_####.exr 1-9'), 'assetA')
        self.assertEqual(clean_file_name('c:/x/assetA.mp4'), 'assetA')
        self.assertEqual(clean_file_name('c:/x/assetA_v002.mp4'), 'assetA_v002')

    def test_separate_folders_pair_exact_and_prefix_names(self):
        self.main_folder = self.ingest / 'mains'
        self.sequence(self.main_folder, 'assetA')
        self.sequence(self.main_folder / 'deeper', 'assetB')
        proxy = self.touch(self.ingest / 'proxies' / 'assetA.mp4')
        self.sequence(self.ingest / 'highres', 'assetA_8K')
        self.touch(self.ingest / 'thumbs' / 'assetB_thumb.jpg')
        jobs = self.prepare({'mode': 'separate', 'proxy': str(self.ingest / 'proxies'),
                             'highres': str(self.ingest / 'highres'), 'thumb': str(self.ingest / 'thumbs')})
        a, b = jobs
        self.assertEqual((a['name'], b['name']), ('assetA', 'assetB'))
        # Scenario 2 for A: supplied proxy, previews from it; highres taken as it is.
        self.assertEqual(a['media']['proxy'], {'mode': 'supply', 'path': str(proxy.resolve())})
        self.assertEqual(a['media']['thumb']['mode'], 'generate')
        self.assertEqual(a['preview_source'], 'proxy')
        self.assertTrue(a['highres'].endswith('assetA_8K.####.exr 1001-1002'))
        # B: no proxy (scenario 1/3), a supplied thumbnail.
        self.assertEqual(b['media']['proxy']['mode'], 'generate')
        self.assertEqual(b['media']['thumb']['mode'], 'supply')
        self.assertEqual(b['preview_source'], 'main')
        self.assertEqual(b['highres'], '')

    def test_same_folder_uses_regexes_and_components_are_not_mains(self):
        self.main_folder = self.ingest
        self.sequence(self.ingest, 'assetA')
        self.sequence(self.ingest / 'hi', 'assetA_8K')
        self.touch(self.ingest / 'assetA.mp4')
        jobs = self.prepare({'mode': 'same', 'proxy': r'^.*\.mp4$', 'highres': r'^.*_8K.*.exr'})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['name'], 'assetA')
        self.assertEqual(jobs[0]['media']['proxy']['mode'], 'supply')
        self.assertIn('assetA_8K', jobs[0]['highres'])

    def test_longest_matching_main_wins(self):
        self.main_folder = self.ingest / 'mains'
        self.sequence(self.main_folder, 'fire')
        self.sequence(self.main_folder, 'fire_big')
        self.touch(self.ingest / 'proxies' / 'fire_big.mp4')
        self.touch(self.ingest / 'proxies' / 'fire.mp4')
        jobs = {job['name']: job for job in self.prepare({'proxy': str(self.ingest / 'proxies')})}
        self.assertTrue(jobs['fire_big']['media']['proxy']['path'].endswith('fire_big.mp4'))
        self.assertTrue(jobs['fire']['media']['proxy']['path'].endswith('fire.mp4'))

    def test_unpaired_and_ambiguous_components_are_errors(self):
        self.main_folder = self.ingest / 'mains'
        self.sequence(self.main_folder, 'assetA')
        self.touch(self.ingest / 'proxies' / 'other.mp4')
        with self.assertRaisesRegex(ValueError, 'match no main'):
            self.prepare({'proxy': str(self.ingest / 'proxies')})
        (self.ingest / 'proxies' / 'other.mp4').unlink()
        self.touch(self.ingest / 'proxies' / 'assetA_a.mp4')
        self.touch(self.ingest / 'proxies' / 'assetA_b.mp4')
        with self.assertRaisesRegex(ValueError, 'two proxy candidates'):
            self.prepare({'proxy': str(self.ingest / 'proxies')})

    def test_invalid_regex_is_reported(self):
        self.main_folder = self.ingest
        self.sequence(self.ingest, 'assetA')
        with self.assertRaisesRegex(ValueError, 'Invalid proxy pairing regex'):
            self.prepare({'mode': 'same', 'proxy': '('})

    def test_category_from_parent_folder_per_main(self):
        from tinylib.ingest import category_for
        source = str(self.ingest / 'mains' / 'assetA.####.exr') + ' 1001-1002'
        self.assertEqual(category_for({'parent': 1}, source), 'mains')
        self.assertEqual(category_for({'parent': 2}, source), 'fire')
        self.assertEqual(category_for('picked', source), 'picked')
        with self.assertRaises(ValueError):
            category_for({'parent': 4}, source)
        self.main_folder = self.root / 'ingest'
        self.sequence(self.root / 'ingest' / 'fire', 'flame')
        self.sequence(self.root / 'ingest' / 'smoke', 'puff')
        jobs = self.prepare({}, category={'parent': 1})
        self.assertEqual({job['name']: job['category'] for job in jobs}, {'flame': 'fire', 'puff': 'smoke'})

    def test_same_name_in_different_categories_is_allowed(self):
        self.main_folder = self.root / 'ingest'
        self.sequence(self.root / 'ingest' / 'fire', 'big')
        self.sequence(self.root / 'ingest' / 'smoke', 'big')
        jobs = self.prepare({}, category={'parent': 1})
        self.assertEqual(sorted(job['category'] for job in jobs), ['fire', 'smoke'])
        with self.assertRaisesRegex(ValueError, 'Duplicate automatic asset name'):
            self.prepare({}, category='one')
