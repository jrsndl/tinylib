import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import HAVE_QT, requires_qt

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if HAVE_QT:
    from tinylib.qt import QtCore, QtGui, QtWidgets
    from tinylib.ingest_ui import IngestDialog
from tinylib.metadata_scan import scan_source_metadata
from tinylib.processing import validate_media


@requires_qt
class IngestMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = {'libraries': [{'name': 'Test', 'root': str(self.root)}],
                         'tools': {'oiiotool': 'studio-oiiotool', 'ffprobe': 'studio-ffprobe'}}
        self.main = self.root / 'main.mov'
        self.main.write_bytes(b'main')
        self.proxy = self.root / 'proxy.mp4'
        self.proxy.write_bytes(b'proxy')

    def wait_scans(self, dialog):
        deadline = time.monotonic() + 5
        while dialog._scans and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertFalse(dialog._scans)

    def test_form_opens_before_slow_category_scan_and_keeps_gui_scope(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        caller = threading.get_ident()
        scan_threads = []
        class Folder:
            name = 'Smoke'
            def is_dir(self):
                return True
        def slow_folders(path):
            scan_threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
            finished.set()
            return iter([Folder()])
        assets = [{'library_root': str(self.root), 'category': 'Fire/Dense'}] * 5000
        with patch('tinylib.ingest_ui.Path.iterdir', slow_folders), \
                patch('tinylib.ingest_ui.Path.resolve', side_effect=AssertionError('Network path resolution on opening')):
            dialog = IngestDialog(self.settings, assets, library_root=str(self.root), category='Smoke/Dense')
            try:
                dialog.show()
                self.app.processEvents()
                self.assertTrue(dialog.isVisible())
                self.assertTrue(entered.wait(1))
                self.assertNotEqual(scan_threads[0], caller)
                self.assertFalse(finished.is_set())
                self.assertEqual(dialog.category.currentText(), 'Fire')
            finally:
                release.set()
                self.assertTrue(finished.wait(1))
        deadline = time.monotonic() + 1
        while dialog.category.findText('Smoke') < 0 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertEqual(dialog.category.currentText(), 'Smoke')
        dialog.close()

    def test_late_category_results_keep_user_choice_and_ignore_old_library(self):
        with patch('tinylib.ingest_ui.CategoryScan.start'):
            dialog = IngestDialog(self.settings, [])
            self.addCleanup(dialog.close)
            dialog.category.addItems(['Fire', 'Water'])
            dialog.category.setCurrentText('Water')
            dialog.categories_loaded((dialog._category_request, {'Smoke'}, ''))
            self.assertEqual(dialog.category.currentText(), 'Water')
            dialog.categories_loaded((dialog._category_request - 1, {'Stale'}, ''))
            self.assertEqual(dialog.category.findText('Stale'), -1)

    def test_folder_batches_all_sources_with_automatic_names_metadata_and_previews(self):
        source_root = self.root / 'multiple'
        for name in ('puff_a', 'puff_b'):
            nested = source_root / name / 'main'
            nested.mkdir(parents=True)
            for frame in (1001, 1002):
                (nested / ('%s.%d.exr' % (name, frame))).write_bytes(b'x')
        (source_root / 'puff_c.mov').write_bytes(b'container')
        self.settings['profiles'] = {'Batch preview': {}}
        from tinylib.access import AccessControl
        self.settings['_config_path'] = str(self.root / 'studio.json')
        self.settings['_access'] = AccessControl(self.settings, identity='test', persist=False)
        dialog = IngestDialog(self.settings, [])
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.new_category.setChecked(True)
        dialog.new_category_name.setText('puff')
        dialog.highres.edit.setText('ignored-highres.exr')
        dialog.media['proxy'][0].setCurrentText('supply')
        dialog.metadata.setPlainText('ignored invalid metadata')
        dialog.source_selected(str(source_root))
        dialog.preview_source.setCurrentText('proxy')
        self.assertEqual(dialog._batch_folder, str(source_root))
        self.assertTrue(dialog.name.isHidden())
        self.assertTrue(dialog.highres.isHidden())
        self.assertTrue(dialog.media_rows['filmstrip'][0].isHidden())
        self.assertTrue(dialog.profile.isVisible())
        jobs = []
        def metadata(source, settings):
            return {'frame_rate': 30 if 'puff_c' in source else 25,
                    'width': 1920, 'height': 1080, 'duration_frames': 2}
        def submit(job, settings, progress=None):
            jobs.append(job)
            return 'job-%d' % len(jobs), self.root / (job['job_id'] + '.json')
        with patch('tinylib.metadata_scan.scan_source_metadata', side_effect=metadata), \
                patch('tinylib.ingest_ui.submit_deadline', side_effect=submit):
            dialog.submit()
            self.assertTrue(dialog.submit_thread.wait(5000))
            self.app.processEvents()
        window = dialog.submission_window
        self.assertEqual(window.state, 'submitted', window.status.text())
        self.assertEqual(len(jobs), 3)
        self.assertEqual([job['name'] for job in jobs], ['puff_a', 'puff_b', 'puff_c'])
        self.assertEqual([job['fps'] for job in jobs], [25, 25, 30])
        self.assertEqual(jobs[0]['tags'], ['a', 'puff'])
        self.assertEqual(len({job['job_id'] for job in jobs}), 3)
        for job in jobs:
            self.assertEqual(job['highres'], '')
            self.assertEqual(job['category'], 'puff')
            self.assertEqual(job['preview_source'], 'proxy')
            self.assertEqual({key: spec['mode'] for key, spec in job['media'].items()},
                             {'proxy': 'generate', 'thumb': 'generate', 'filmstrip': 'generate'})
        self.assertEqual(len(window.confirmed_jobs), 3)
        self.assertTrue(dialog.isVisible())
        window.close()
        with patch('tinylib.ingest_ui.scan_source_metadata', return_value={'frame_rate': 24}):
            dialog.source_selected(str(self.main))
            self.wait_scans(dialog)
        self.assertIsNone(dialog._batch_folder)
        self.assertFalse(dialog.name.isHidden())

    def test_folder_partial_submission_keeps_confirmed_job_ids_and_stops(self):
        from tinylib.ingest_ui import SubmissionWindow
        dialog = IngestDialog(self.settings, [])
        self.addCleanup(dialog.close)
        args = (self.settings, self.settings['libraries'][0], 'batch', 'cat', str(self.root), [],
                'ACEScg', 'preview', {}, 24, '', 'main', 'footage', {})
        manifests = [{'name': name} for name in ('First', 'Second', 'Third')]
        window = SubmissionWindow(dialog, args, batch=True)
        with patch('tinylib.ingest_ui.make_folder_manifests', return_value=manifests), \
                patch('tinylib.ingest_ui.submit_deadline', side_effect=[('job-first', self.root), RuntimeError('Deadline timeout')]) as submit:
            window.worker.start()
            self.assertTrue(window.worker.wait(3000))
            self.app.processEvents()
        self.assertEqual(submit.call_count, 2)
        self.assertEqual(window.state, 'partial')
        self.assertEqual(window.confirmed_jobs[0]['job_id'], 'job-first')
        self.assertIn('job-first', window.log.toPlainText())
        self.assertIn('1 job(s) confirmed', window.status.text())
        window.close()

    def test_empty_name_is_reported_before_starting_submission(self):
        dialog = IngestDialog(self.settings, [])
        self.addCleanup(dialog.close)
        dialog.submit()
        self.assertIn('Enter an asset name', dialog.status.text())
        self.assertEqual(dialog._submissions, [])

    def test_sequence_uses_oiio_and_maps_fps_to_schema(self):
        for frame in (1001, 1002):
            (self.root / ('plate.%d.exr' % frame)).write_bytes(b'frame')
        xml = '<ImageSpec><width>2048</width><height>1024</height><attrib name="FramesPerSecond">24/1</attrib></ImageSpec>'
        with patch('tinylib.metadata_scan._run', return_value=xml) as run:
            metadata = scan_source_metadata(str(self.root / 'plate.1001.exr'), self.settings)
        self.assertEqual(run.call_args.args[0][0], 'studio-oiiotool')
        self.assertEqual(metadata['frame_rate'], 24)
        self.assertEqual(metadata['frame_start'], 1001)
        self.assertEqual(metadata['duration_frames'], 2)
        self.assertEqual(metadata['file_size_main'], 10)
        self.assertAlmostEqual(metadata['duration_seconds'], 2 / 24)
        self.assertNotIn('FPS', metadata)

    def test_container_uses_ffprobe(self):
        payload = {'streams': [{'width': 1920, 'height': 1080, 'avg_frame_rate': '24000/1001',
                                'nb_frames': '120', 'duration': '5.005'}]}
        with patch('tinylib.metadata_scan._run', return_value=json.dumps(payload)) as run:
            metadata = scan_source_metadata(str(self.main), self.settings)
        self.assertEqual(run.call_args.args[0][0], 'studio-ffprobe')
        self.assertAlmostEqual(metadata['frame_rate'], 24000 / 1001)
        self.assertEqual(metadata['frame_end'], 119)

    def test_dialog_fills_metadata_and_warns_without_blocking(self):
        dialog = IngestDialog(self.settings, [])
        self.addCleanup(dialog.close)
        def scan(source, settings):
            return {'frame_rate': 25 if 'proxy' in source else 24,
                    'width': 1920, 'height': 1080, 'duration_frames': 120}
        with patch('tinylib.ingest_ui.scan_source_metadata', side_effect=scan):
            dialog.source_selected(str(self.main))
            self.wait_scans(dialog)
            self.assertEqual(dialog.fps.value(), 24)
            metadata = json.loads(dialog.metadata.toPlainText())
            self.assertEqual(metadata['width'], 1920)
            self.assertEqual(metadata['frame_rate'], 24)
            dialog.preview_source.setCurrentText('proxy')
            dialog.media['proxy'][0].setCurrentText('supply')
            dialog.media['proxy'][1].edit.setText(str(self.proxy))
            dialog.media['proxy'][1].edit.editingFinished.emit()
            self.wait_scans(dialog)
            self.assertIn('differs', dialog.proxy_warning.text())
            self.assertTrue(dialog.submit_button.isEnabled())
            with patch('tinylib.ingest_ui.make_manifest', return_value={'ok': True}):
                self.assertEqual(dialog.manifest(), {'ok': True})
            dialog.fps.setValue(25)
            self.assertEqual(dialog.proxy_warning.text(), '')
            dialog.fps.setValue(24)
            self.assertIn('differs', dialog.proxy_warning.text())
            dialog.preview_source.setCurrentText('main')
            self.assertEqual(dialog.proxy_warning.text(), '')
            dialog.scan_finished('main', -1, ({'frame_rate': 60}, ''))
            self.assertEqual(dialog.fps.value(), 24)

    def test_scan_failure_is_reported_and_does_not_block(self):
        dialog = IngestDialog(self.settings, [])
        self.addCleanup(dialog.close)
        with patch('tinylib.ingest_ui.scan_source_metadata', side_effect=RuntimeError('tool unavailable')):
            dialog.source_selected(str(self.main))
            self.wait_scans(dialog)
        self.assertIn('tool unavailable', dialog.status.text())
        self.assertFalse(dialog._main_scan_pending)

    def test_reuse_open_form_during_concurrent_submissions(self):
        dialog = IngestDialog(self.settings, [])
        self.addCleanup(dialog.close)
        entered, release = threading.Event(), threading.Event()
        caller_thread = threading.get_ident()
        dialog.name.setText('First asset')
        dialog.media['filmstrip'][0].setCurrentText('supply')
        dialog.media['filmstrip'][1].edit.setText('first-strip.jpg')
        dialog.show()
        self.app.processEvents()
        def build(*args):
            self.assertNotEqual(threading.get_ident(), caller_thread)
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test timed out waiting for UI')
            return {'name': args[2]}
        with patch('tinylib.ingest_ui.make_manifest', side_effect=build), \
                patch('tinylib.ingest_ui.submit_deadline', return_value=('abc', self.root)) as submit:
            try:
                dialog.submit()
                self.assertTrue(entered.wait(1))
                first = dialog.submission_window
                self.assertTrue(dialog.submit_button.isEnabled())
                self.assertTrue(dialog.isVisible())
                self.assertFalse(first.isModal())
                self.assertTrue(dialog.media['filmstrip'][0].isVisible())
                self.assertEqual(dialog.media['filmstrip'][0].currentText(), 'supply')
                dialog.submit()
                self.assertEqual(len(dialog._submissions), 1)
                self.assertIn('already has', dialog.status.text())
                dialog.name.setText('Next asset')
                dialog.media['filmstrip'][1].edit.setText('next-strip.jpg')
                self.assertEqual(first.worker.arguments[2], 'First asset')
                self.assertEqual(first.worker.arguments[8]['filmstrip']['path'], 'first-strip.jpg')
                dialog.submit()
                self.assertEqual(len(dialog._submissions), 2)
                self.assertTrue(dialog.isVisible())
                self.assertTrue(dialog.media['filmstrip'][0].isVisible())
            finally:
                release.set()
                for window in dialog._submissions:
                    self.assertTrue(window.worker.wait(3000))
                self.app.processEvents()
            self.assertEqual(submit.call_count, 2)
            for window in dialog._submissions:
                self.assertIn('Submitted job abc', window.status.text())
                window.close()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog.name.text(), 'Next asset')
            self.assertEqual(dialog.media['filmstrip'][0].currentText(), 'supply')
            self.assertTrue(dialog.submit_button.isEnabled())

    def test_form_can_close_or_escape_while_submission_continues(self):
        owner = QtWidgets.QWidget()
        dialog = IngestDialog(self.settings, [], owner)
        dialog.name.setText('Background asset')
        dialog.show()
        entered, release = threading.Event(), threading.Event()
        def pending_submission(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test timed out')
            return 'background-job', self.root
        with patch('tinylib.ingest_ui.make_manifest', return_value={'job_id': 'test'}), \
                patch('tinylib.ingest_ui.submit_deadline', side_effect=pending_submission):
            try:
                dialog.submit()
                self.assertTrue(entered.wait(1))
                progress = dialog.submission_window
                self.assertIs(progress.parentWidget(), owner)
                self.assertIn(progress, owner._ingest_submissions)
                self.assertTrue(dialog.close())
                self.assertFalse(dialog.isVisible())
                self.assertTrue(progress.worker.isRunning())
                progress.edit_inputs()
                self.assertTrue(dialog.isVisible())
                self.assertEqual(dialog.name.text(), 'Background asset')
                dialog.reject()
                self.assertFalse(dialog.isVisible())
                self.assertTrue(progress.worker.isRunning())
            finally:
                release.set()
                self.assertTrue(dialog.submit_thread.wait(3000))
                self.app.processEvents()
            self.assertEqual(progress.state, 'submitted')
            self.assertIn('background-job', progress.status.text())
            self.assertFalse(dialog.isVisible())
            progress.close()
        owner.close()

    def test_scrolling_does_not_change_type_or_media_options(self):
        dialog = IngestDialog(self.settings, [])
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        for combo in (dialog.kind, dialog.media['filmstrip'][0]):
            initial = combo.currentIndex()
            event = QtGui.QWheelEvent(QtCore.QPointF(5, 5), QtCore.QPointF(5, 5),
                                     QtCore.QPoint(), QtCore.QPoint(0, -120),
                                     QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                                     QtCore.Qt.NoScrollPhase, False)
            self.app.sendEvent(combo, event)
            self.assertEqual(combo.currentIndex(), initial)
        self.assertEqual(dialog.kind.currentData(), 'footage')
        self.assertTrue(dialog.media['filmstrip'][0].isVisible())
        dialog.kind.setCurrentIndex(dialog.kind.findData('still'))
        self.assertTrue(dialog.media_rows['filmstrip'][0].isHidden())
        self.assertTrue(dialog.media_rows['filmstrip'][1].isHidden())
        dialog.kind.setCurrentIndex(dialog.kind.findData('footage'))
        self.assertTrue(dialog.media['filmstrip'][0].isVisible())
        self.assertTrue(dialog.media_rows['filmstrip'][1].isVisible())

    def test_failed_submission_preserves_inputs_for_editing(self):
        dialog = IngestDialog(self.settings, [])
        dialog.name.setText('Preserved asset')
        with patch('tinylib.ingest_ui.make_manifest', side_effect=RuntimeError('Invalid source')):
            dialog.submit()
            self.assertTrue(dialog.submit_thread.wait(3000))
            self.app.processEvents()
        window = dialog.submission_window
        self.assertIn('Invalid source', window.status.text())
        self.assertFalse(window.edit_button.isHidden())
        window.edit_inputs()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.name.text(), 'Preserved asset')
        self.assertTrue(dialog.submit_button.isEnabled())
        dialog.close()
        window.close()

    def test_worker_accepts_fps_mismatch_but_keeps_frame_count_validation(self):
        job = {'kind': 'footage', 'tools': {}, 'source': 'plate.####.exr 1001-1002', 'fps': 24}
        info = {'width': 1920, 'height': 1080, 'nb_read_frames': '2', 'avg_frame_rate': '25/1'}
        with patch('tinylib.processing.probe', return_value=info), patch('builtins.print') as log:
            validate_media({'proxy': self.proxy}, job)
            self.assertIn('Warning', log.call_args.args[0])
            info['nb_read_frames'] = '3'
            with self.assertRaisesRegex(ValueError, 'frame count'):
                validate_media({'proxy': self.proxy}, job)


if __name__ == '__main__':
    unittest.main()
