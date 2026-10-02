import tempfile
import unittest
from pathlib import Path

from tinylib.edit_lock import (LibraryEditLocked, acquire_edit_lock,
                               locked_message, read_edit_locks, release_edit_lock)
from tinylib.library import Library, atomic_json, read_json


class EditLockTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.record = {
            'id': 'fire/test', 'name': 'Test', 'category': 'fire', 'kind': 'footage',
            'main': 'fire/test/main/test.####.exr', 'thumb': 'fire/test/thumb/test.jpg',
            'tags': ['fire'], 'colorspace': 'ACEScg', 'first': 1, 'last': 2,
            'metadata': {'width': 1920, 'height': 1080, 'FPS': 24},
        }
        atomic_json(self.root / 'tinylib_data.json', {'schema_version': 3, 'assets': [self.record]})

    def test_lock_records_identity_time_and_owner_can_write(self):
        handle = acquire_edit_lock(self.root, r'STUDIO\jiri')
        self.addCleanup(lambda: release_edit_lock(handle))
        locks = read_edit_locks(self.root)
        self.assertEqual(locks[0]['identity'], r'STUDIO\jiri')
        self.assertIn('T', locks[0]['created_at'])
        self.assertEqual(len(list(self.root.glob('lock.*.txt'))), 1)

        library = Library(self.root, 'Test')
        asset = library.load()[0]
        with self.assertRaises(LibraryEditLocked):
            library.update_asset(asset, {'tags': ['blocked']})
        library.update_asset(asset, {'tags': ['owner']}, edit_token=handle['token'])
        self.assertEqual(read_json(self.root / 'tinylib_data.json')['assets'][0]['tags'], ['owner'])

    def test_second_user_is_blocked_and_admin_override_replaces_lock(self):
        first = acquire_edit_lock(self.root, 'second.user')
        with self.assertRaises(LibraryEditLocked) as caught:
            acquire_edit_lock(self.root, 'second.user')
        self.assertEqual(caught.exception.locks[0]['identity'], 'second.user')
        self.assertIn('User second.user is editing the library (', locked_message(caught.exception.locks))
        self.assertIn('edit is not possible', locked_message(caught.exception.locks))

        second = acquire_edit_lock(self.root, 'second.user', override=True)
        self.assertEqual(read_edit_locks(self.root)[0]['identity'], 'second.user')
        with self.assertRaises(RuntimeError):
            release_edit_lock(first)
        release_edit_lock(second)
        self.assertFalse(list(self.root.glob('lock.*.txt')))

    def test_ingest_publication_is_blocked_while_library_is_edited(self):
        handle = acquire_edit_lock(self.root, 'editor')
        new = dict(self.record, id='fire/new', name='New', main='fire/new/main/new.####.exr')
        with self.assertRaises(LibraryEditLocked):
            Library(self.root, 'Test').publish(new)
        self.assertEqual(len(read_json(self.root / 'tinylib_data.json')['assets']), 1)
        release_edit_lock(handle)


if __name__ == '__main__':
    unittest.main()
