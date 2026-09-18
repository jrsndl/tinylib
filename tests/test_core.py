import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.library import Library, atomic_json, matches, sequence_files, detect_sequence, safe_component
from tinylib.ingest import make_manifest, submit_deadline
from tinylib.processing import sample_indices, process
from tinylib.settings import load_settings
from tinylib.access import AccessControl


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_legacy_paths_tags_and_media_roles(self):
        atomic_json(self.root / 'data.json', {'data': {'elements': {'root|fire': [{
            'source': 'X:/legacy-assets/fire/Test/main/Test.####.exr 1001-1002',
            'proxy': 'X:/legacy-assets/fire/Test/thumb/Test.jpg', 'tags': ['smoke', 'Day']}]}}})
        library = Library(self.root, 'Demo', ['X:/legacy-assets'])
        asset = library.load()[0]
        self.assertEqual(asset['thumb'], (self.root / 'fire/Test/thumb/Test.jpg').as_posix())
        self.assertEqual(asset['first'], 1001)
        self.assertTrue(matches(asset, 'FIRE tag:day -night'))
        self.assertTrue(matches(asset, '"test" smoke'))
        self.assertFalse(matches(asset, '-tag:day'))
        self.assertFalse(matches(asset, 'fire missing'))

    def test_sequence_detection_and_missing_frames(self):
        for frame in (7, 8, 9):
            (self.root / ('a.%04d.exr' % frame)).touch()
        value = detect_sequence(self.root / 'a.0007.exr')
        self.assertTrue(value.endswith('a.####.exr 7-9'))
        self.assertEqual(len(sequence_files(value)), 3)
        self.assertEqual(len(sequence_files(str(self.root / 'a.0007.exr'))), 1)
        (self.root / 'a.0008.exr').unlink()
        with self.assertRaises(ValueError):
            sequence_files(value)
        with self.assertRaises(ValueError):
            detect_sequence(self.root / 'a.0007.exr')

    def test_flat_hdri_scan(self):
        main = self.root / 'indoor/main'
        main.mkdir(parents=True)
        (main / 'indoor.exr').touch()
        (main.parent / 'highres').mkdir()
        (main.parent / 'highres/indoor.exr').touch()
        asset = Library(self.root).load()[0]
        self.assertEqual(asset['kind'], 'still')
        self.assertEqual(asset['category'], 'HDRI')
        self.assertTrue(asset['highres'].endswith('highres/indoor.exr'))
        self.assertFalse((self.root / 'data.json').exists())

    def test_scan_excludes_pending_ingests(self):
        main = self.root / '.tinylib-staging/job/main'
        main.mkdir(parents=True)
        (main / 'pending.exr').touch()
        self.assertEqual(Library(self.root).load(), [])

    def test_publish_staging_and_backup(self):
        atomic_json(self.root / 'data.json', {'data': {}})
        stage = self.root / '.tinylib-staging/job'
        stage.mkdir(parents=True)
        record = dict(id='fire/Test', category='fire', name='Test', main=str(self.root / 'fire/Test/main/a.exr'))
        library = Library(self.root)
        library.publish(record, stage)
        self.assertTrue((self.root / 'fire/Test').exists())
        self.assertFalse(stage.exists())
        self.assertTrue((self.root / 'data.legacy.backup.json').exists())
        database = json.loads((self.root / 'data.json').read_text())
        self.assertEqual(database['assets'][0]['main'], 'fire/Test/main/a.exr')
        with self.assertRaises(ValueError):
            library.publish(record)

    def test_publish_rolls_back_on_database_error(self):
        atomic_json(self.root / 'data.json', {'schema_version': 3, 'assets': []})
        stage = self.root / '.tinylib-staging/job'
        stage.mkdir(parents=True)
        record = dict(id='fire/Test', category='fire', name='Test')
        with patch('tinylib.library.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                Library(self.root).publish(record, stage)
        self.assertTrue(stage.exists())
        self.assertFalse((self.root / 'fire/Test').exists())

    def test_sampling_and_folder_validation(self):
        self.assertEqual(sample_indices(1), [0] * 24)
        self.assertEqual(len(sample_indices(3)), 24)
        self.assertEqual(sample_indices(100)[-1], 99)
        for invalid in ('../oops', 'A/B', 'CON', 'name.', 'a\nb'):
            with self.assertRaises(ValueError):
                safe_component(invalid)

    def test_failed_ingest_not_published(self):
        source = self.root / 'source.exr'
        source.touch()
        job = dict(manifest_version=1, job_id='testjob', library={'root': str(self.root)},
                   name='Asset', category='still', source=str(source), kind='still', media={}, tools={}, profile={})
        path = self.root / 'job.json'
        atomic_json(path, job)
        with patch('tinylib.processing.Processor.generate', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                process(path, access=AccessControl({'_config_path': str(self.root / 'studio.json')}, identity='test', persist=False))
        self.assertFalse((self.root / 'still/Asset').exists())
        self.assertFalse((self.root / 'data.json').exists())
        self.assertEqual(json.loads(path.with_suffix('.status.json').read_text())['state'], 'failed')

    def test_deadline_job_and_safe_arguments(self):
        worker = self.root / 'worker script.py'
        worker.touch()
        job = dict(job_id='jobid', name='Nice asset', library={'root': str(self.root)})
        settings = {'deadline': {'worker_script': str(worker), 'spool_root': str(self.root)}}
        settings['_access'] = AccessControl({'_config_path': str(self.root / 'studio.json')}, identity='test', persist=False)
        with patch('tinylib.ingest.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = 'Result=Success\nJobID=123abc\n'
            run.return_value.stderr = ''
            identifier, path = submit_deadline(job, settings)
            self.assertEqual(identifier, '123abc')
            self.assertIn('ScriptFile=' + str(worker), path.with_suffix('.plugin').read_text())
            self.assertIn('Frames=0', path.with_suffix('.job').read_text())
            self.assertIsInstance(run.call_args.args[0], list)


if __name__ == '__main__':
    unittest.main()
