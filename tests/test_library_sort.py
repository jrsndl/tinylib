import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tinylib.access import AccessControl
from tinylib.edit_lock import LibraryEditLocked, acquire_edit_lock, read_edit_locks, release_edit_lock
from tinylib.library import atomic_json, read_json
from tinylib.library_tools import sort_library
from helpers import requires_qt


class LibrarySortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = {'name': 'Test', 'root': str(self.root)}
        self.settings = {'libraries': [self.library], '_config_path': str(self.root / 'studio.json')}
        self.access = AccessControl(self.settings, identity='test/admin', persist=False)
        self.database = self.root / 'tinylib_data.json'
        self.records = [
            {'id': 'smoke/z', 'category': 'Smoke', 'name': 'Zulu', 'metadata': {'custom': 2}},
            {'id': 'fire/z', 'category': 'fire', 'name': 'zebra', 'tags': ['keep']},
            {'id': 'fire/b', 'category': 'Fire', 'name': 'Bravo', 'main': 'unchanged.exr'},
            {'id': 'fire/a', 'category': 'fire', 'name': 'alpha', 'custom': [1, 2]},
        ]
        self.document = {'schema_version': 3, 'custom_header': {'keep': True}, 'assets': self.records}
        atomic_json(self.database, self.document)

    def sort(self, **kwargs):
        return sort_library(self.settings, self.access, self.library, **kwargs)

    def test_alphabetical_order_preserves_all_document_and_record_fields(self):
        with patch('tinylib.library_tools.Library.normalize', side_effect=AssertionError('No media scans needed')):
            result = self.sort()
        saved = read_json(self.database)
        self.assertEqual([r['name'] for r in saved['assets']], ['alpha', 'Bravo', 'zebra', 'Zulu'])
        self.assertEqual(saved['custom_header'], self.document['custom_header'])
        self.assertEqual({r['id']: r for r in saved['assets']}, {r['id']: r for r in self.records})
        self.assertEqual(result, {'assets': 4, 'changed': True})
        self.assertFalse(read_edit_locks(self.root))
        self.assertFalse((self.root / '.tinylib-write.lock').exists())

    def test_existing_edit_lock_blocks_sort_without_changing_data(self):
        handle = acquire_edit_lock(self.root, 'other/editor')
        try:
            before = self.database.read_bytes()
            with self.assertRaises(LibraryEditLocked):
                self.sort()
            self.assertEqual(self.database.read_bytes(), before)
            self.assertEqual(read_edit_locks(self.root)[0]['token'], handle['token'])
        finally:
            release_edit_lock(handle)

    def test_admin_override_uses_standard_lock_takeover(self):
        acquire_edit_lock(self.root, 'other/editor')
        self.assertTrue(self.sort(override=True)['changed'])
        self.assertFalse(read_edit_locks(self.root))

    def test_write_is_protected_by_both_locks_and_failure_releases_them(self):
        def fail_write(path, document):
            self.assertTrue((self.root / '.tinylib-write.lock').is_file())
            self.assertEqual(read_edit_locks(self.root)[0]['identity'], self.access.identity)
            raise OSError('Disk unavailable')
        before = self.database.read_bytes()
        with patch('tinylib.library.atomic_json', side_effect=fail_write):
            with self.assertRaisesRegex(OSError, 'Disk unavailable'):
                self.sort()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(read_edit_locks(self.root))
        self.assertFalse((self.root / '.tinylib-write.lock').exists())

    def test_no_rewrite_when_already_sorted_and_latest_records_are_included(self):
        self.sort()
        with patch('tinylib.library.atomic_json') as write:
            self.assertFalse(self.sort()['changed'])
            write.assert_not_called()
        document = read_json(self.database)
        document['assets'].append({'id': 'fire/added', 'category': 'fire', 'name': 'Added'})
        atomic_json(self.database, document)
        self.assertEqual(self.sort()['assets'], 5)
        self.assertEqual(read_json(self.database)['assets'][0]['name'], 'Added')

    def test_read_only_and_non_admin_are_rejected(self):
        self.library['read_only'] = True
        with self.assertRaises(PermissionError):
            self.sort()
        self.library['read_only'] = False
        self.access._memory['access']['users'][self.access.identity] = ['users']
        with self.assertRaises(PermissionError):
            self.sort()
        self.assertFalse(read_edit_locks(self.root))

    def test_unsupported_database_is_unchanged_and_unlocks(self):
        atomic_json(self.database, {'data': {'legacy': {}}})
        before = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, 'schema_version 3'):
            self.sort()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(read_edit_locks(self.root))

    @requires_qt
    def test_background_dialog_sorts_and_reports_completion(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from tinylib.qt import QtWidgets
        from tinylib.library_tools_ui import LibrarySortDialog
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        dialog = LibrarySortDialog(self.library, self.settings, self.access)
        dialog.show()
        deadline = time.monotonic() + 3
        while dialog.isVisible() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        self.assertTrue(dialog.worker.wait(1000))
        app.processEvents()
        self.assertEqual(dialog.sort_result, {'assets': 4, 'changed': True})
        self.assertFalse(dialog.isVisible())
        self.assertFalse(read_edit_locks(self.root))

    @requires_qt
    def test_browser_action_sorts_selected_library_and_reloads_without_cache(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from unittest.mock import Mock
        from tinylib.qt import QtCore, QtWidgets
        from tinylib.ui import Browser
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        host = QtWidgets.QWidget()
        host.access, host.settings, host.edit_session = self.access, self.settings, None
        host.categories = QtWidgets.QTreeWidget(host)
        category = QtWidgets.QTreeWidgetItem(['Fire'])
        category.setData(0, QtCore.Qt.UserRole, (str(self.root), 'fire'))
        host.categories.addTopLevelItem(category)
        host.categories.setCurrentItem(category)
        host.reload, host.show_error = Mock(), Mock()
        Browser.library_sort(host)
        host.show_error.assert_not_called()
        host.reload.assert_called_once_with(use_index_cache=False)
        self.assertEqual([r['name'] for r in read_json(self.database)['assets']],
                         ['alpha', 'Bravo', 'zebra', 'Zulu'])
        host.close()


if __name__ == '__main__':
    unittest.main()
