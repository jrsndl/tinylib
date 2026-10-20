import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.access import AccessControl
from tinylib.ingest import discover_footage_sources, make_folder_manifests


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
            self.assertEqual(job['preview_source'], 'proxy')
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
