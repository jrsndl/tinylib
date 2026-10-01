import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tinylib.thumbnail_cache import cache_root, cached_image_path
from tinylib.library import Library, atomic_json


class ThumbnailCachePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_prefers_existing_nuke_temp_dir(self):
        nuke_temp = self.root / 'nuke-temp'
        shared = self.root / 'shared-temp'
        nuke_temp.mkdir(); shared.mkdir()
        selected = cache_root({'NUKE_TEMP_DIR': str(nuke_temp)}, shared_candidates=[shared])
        self.assertEqual(selected, nuke_temp / 'tinylib')
        self.assertTrue(selected.is_dir())

    def test_missing_nuke_temp_uses_shared_before_user_fallback(self):
        shared = self.root / 'shared-temp'
        fallback = self.root / 'user-temp'
        shared.mkdir()
        selected = cache_root({'NUKE_TEMP_DIR': str(self.root / 'missing')}, fallback=fallback,
                              shared_candidates=[shared])
        self.assertEqual(selected, shared / 'tinylib')

    def test_cache_key_changes_with_source_identity(self):
        source = self.root / 'thumb.jpg'
        source.write_bytes(b'one')
        first = cached_image_path(self.root / 'cache', source)
        source.write_bytes(b'two-two')
        second = cached_image_path(self.root / 'cache', source)
        self.assertNotEqual(first, second)
        self.assertEqual(first.suffix, '.jpg')

    def test_normalized_library_index_is_reused_and_invalidated(self):
        library_root = self.root / 'library'
        library_root.mkdir()
        database = library_root / 'data.json'
        record = {'id': 'cat/One', 'name': 'One', 'category': 'cat',
                  'main': 'cat/One/main/One.exr', 'thumb': 'cat/One/thumb/One.jpg',
                  'kind': 'still'}
        atomic_json(database, {'schema_version': 3, 'assets': [record]})
        disk = self.root / 'cache'
        first = Library(library_root, 'Test', index_cache_root=disk)
        self.assertEqual(len(first.load()), 1)
        self.assertFalse(first.index_cache_hit)
        second = Library(library_root, 'Test', index_cache_root=disk)
        with patch.object(Library, 'normalize', side_effect=AssertionError('normalization should be cached')):
            self.assertEqual(len(second.load()), 1)
        self.assertTrue(second.index_cache_hit)

        atomic_json(database, {'schema_version': 3, 'assets': [record, dict(record, id='cat/Two', name='Two')]})
        third = Library(library_root, 'Test', index_cache_root=disk)
        self.assertEqual(len(third.load()), 2)
        self.assertFalse(third.index_cache_hit)


if __name__ == '__main__':
    unittest.main()
