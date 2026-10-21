import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tinylib import storage
from tinylib.library import Library, atomic_json, library_lock, matches, read_json
from tinylib.media_probe import ToolTimeout, pixel_aspect, run_tool
from tinylib.processing import footage_timing
from tinylib.search import parse_query
from tinylib.settings import SettingsError, load_settings
from tinylib.thumbnail_cache import INDEX_CACHE_VERSION, prune_cache


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root / storage.LOCK_FILENAME

    def test_atomic_json_retries_while_target_is_open_elsewhere(self):
        real = os.replace
        calls = []

        def flaky(source, target):
            calls.append(source)
            if len(calls) < 3:
                raise PermissionError(13, 'used by another process')
            return real(source, target)
        with patch('tinylib.storage.os.replace', side_effect=flaky), patch('tinylib.storage.time.sleep'):
            atomic_json(self.root / 'data.json', {'a': 1})
        self.assertEqual(read_json(self.root / 'data.json'), {'a': 1})
        self.assertEqual(len(calls), 3)
        self.assertEqual([p.name for p in self.root.iterdir()], ['data.json'])

    def test_lock_records_owner_and_is_removed(self):
        with library_lock(self.root):
            info = storage.read_lock(self.lock)
            self.assertEqual(info['pid'], os.getpid())
            self.assertEqual(info['host'], socket.gethostname())
        self.assertFalse(self.lock.exists())

    def test_lock_of_dead_local_process_is_broken_immediately(self):
        self.lock.write_text(json.dumps({'host': socket.gethostname(), 'pid': 999999, 'token': 'x'}))
        with patch('tinylib.storage.pid_alive', return_value=False):
            with library_lock(self.root, timeout=1):
                self.assertNotEqual(storage.read_lock(self.lock)['token'], 'x')
        self.assertFalse(self.lock.exists())

    def test_old_lock_from_another_computer_is_broken_by_age(self):
        self.lock.write_text('PID 123\n')
        old = time.time() - storage.STALE_LOCK_SECONDS - 5
        os.utime(self.lock, (old, old))
        with library_lock(self.root, timeout=1):
            pass
        self.assertFalse(self.lock.exists())

    def test_fresh_lock_from_another_computer_times_out_with_owner(self):
        self.lock.write_text(json.dumps({'host': 'render-07', 'pid': 42, 'token': 'x'}))
        with self.assertRaisesRegex(RuntimeError, 'render-07'):
            with library_lock(self.root, timeout=.2):
                pass
        self.assertTrue(self.lock.exists())

    def test_lock_taken_over_by_another_writer_is_not_deleted(self):
        with library_lock(self.root):
            self.lock.write_text(json.dumps({'host': 'other', 'pid': 1, 'token': 'theirs'}))
        self.assertEqual(storage.read_lock(self.lock)['token'], 'theirs')

    def test_second_breaker_does_not_remove_the_new_live_lock(self):
        self.lock.write_text('PID 123\n')
        old = time.time() - storage.STALE_LOCK_SECONDS - 5
        os.utime(self.lock, (old, old))
        seen_by_both = storage.read_lock(self.lock)
        self.assertTrue(storage._break_lock(self.lock, seen_by_both))  # Process A breaks it...
        self.lock.write_text(json.dumps({'host': 'a', 'pid': 1, 'token': 'live'}))  # ...and takes it.
        self.assertFalse(storage._break_lock(self.lock, seen_by_both))  # Process B is late.
        self.assertEqual(storage.read_lock(self.lock)['token'], 'live')
        self.assertEqual([p.name for p in self.root.iterdir()], [storage.LOCK_FILENAME])

    def test_unreadable_lock_is_never_broken(self):
        self.lock.write_text('x')
        self.assertFalse(storage._break_lock(self.lock, {'raw': None}))
        self.assertTrue(self.lock.exists())

    def test_release_retries_sharing_violation(self):
        self.lock.write_text(json.dumps({'token': 'mine'}))
        real_unlink = Path.unlink
        calls = []

        def flaky(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError(32, 'used by another process')
            return real_unlink(path, *args, **kwargs)
        with patch('tinylib.storage.time.sleep'), patch.object(Path, 'unlink', flaky):
            storage._release(self.lock, 'mine')
        self.assertFalse(self.lock.exists())
        self.assertEqual(len(calls), 2)

    def test_pid_alive_for_self(self):
        self.assertTrue(storage.pid_alive(os.getpid()))


class PublishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'lib'
        self.root.mkdir()
        self.cache = Path(temporary.name) / 'cache'
        atomic_json(self.root / 'tinylib_data.json', {
            'schema_version': 3, 'asset_type_schema_version': 1, 'studio_note': 'keep me',
            'assets': [{'id': 'fx/One', 'name': 'One', 'category': 'fx', 'kind': 'still',
                        'main': 'fx/One/main/one.exr', 'custom': {'raw': True}}]})

    def record(self, name):
        return {'id': 'fx/' + name, 'name': name, 'category': 'fx', 'kind': 'still',
                'main': str(self.root / 'fx' / name / 'main' / 'x.exr'), 'tags': [], 'metadata': {}}

    def test_publish_patches_current_document_and_ignores_stale_cache(self):
        library = Library(self.root, 'Lib', index_cache_root=self.cache)
        library.load()  # Builds the local index cache.
        # Another user's edit that keeps the same size and timestamp would reuse the cache.
        document = read_json(self.root / 'tinylib_data.json')
        document['assets'][0]['tags'] = ['edited']
        atomic_json(self.root / 'tinylib_data.json', document)
        with patch('tinylib.library.Library.load', side_effect=AssertionError('must not reload')):
            library.publish(self.record('Two'))
        saved = read_json(self.root / 'tinylib_data.json')
        self.assertEqual(saved['studio_note'], 'keep me')
        self.assertEqual(saved['assets'][0]['tags'], ['edited'])
        self.assertEqual(saved['assets'][0]['custom'], {'raw': True})
        self.assertNotIn('library_root', saved['assets'][0])
        self.assertEqual([a['id'] for a in saved['assets']], ['fx/One', 'fx/Two'])
        self.assertEqual(saved['assets'][1]['main'], 'fx/Two/main/x.exr')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            library.publish(self.record('Two'))

    def test_legacy_fps_is_mapped_to_frame_rate(self):
        document = read_json(self.root / 'tinylib_data.json')
        document['assets'][0]['metadata'] = {'FPS': '24000/1001'}
        atomic_json(self.root / 'tinylib_data.json', document)
        asset = Library(self.root, 'Lib', index_cache_root=self.cache).load()[0]
        self.assertAlmostEqual(asset['metadata']['frame_rate'], 24000 / 1001)
        self.assertNotIn('FPS', asset['metadata'])
        self.assertEqual(asset['ingest_date'], '')

    def test_index_cache_version_is_used(self):
        Library(self.root, 'Lib', index_cache_root=self.cache).load()
        cached = next(self.cache.rglob('*.json'))
        self.assertEqual(read_json(cached)['index_cache_version'], INDEX_CACHE_VERSION)


class ProbeAndParsingTests(unittest.TestCase):
    def test_pixel_aspect(self):
        self.assertEqual(pixel_aspect('0:1'), 1.0)
        self.assertEqual(pixel_aspect('N/A'), 1.0)
        self.assertEqual(pixel_aspect('2:1'), 2.0)
        self.assertAlmostEqual(pixel_aspect('4:3'), 4 / 3)

    def test_sequence_timing_ignores_single_frame_probe(self):
        job = {'fps': 24.0}
        probe = {'avg_frame_rate': '25/1', 'duration': '0.040000', 'nb_frames': '1'}
        self.assertEqual(footage_timing(job, probe, 1001, 1100), (24.0, 100, 100 / 24))
        container = {'avg_frame_rate': '24000/1001', 'duration': '5.005', 'nb_frames': 'N/A'}
        rate, frames, seconds = footage_timing(job, container, None, None)
        self.assertAlmostEqual(rate, 24000 / 1001)
        self.assertEqual(frames, 120)
        self.assertAlmostEqual(seconds, 5.005)

    def test_run_tool_timeout_and_missing_tool(self):
        with self.assertRaises(ToolTimeout):
            run_tool([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=.2)
        with self.assertRaisesRegex(RuntimeError, 'Tool not found'):
            run_tool(['tinylib-no-such-tool-xyz'])

    def test_unbalanced_field_quote_does_not_raise(self):
        parse_query.cache_clear()
        self.assertEqual(parse_query("{name}:'it\\'s'")[1][0][0], 'name')
        asset = {'name': "it's", 'category': 'fx', 'tags': []}
        self.assertIsInstance(matches(asset, "{name}:'it\\'s'"), bool)


class SettingsValidationTests(unittest.TestCase):
    def test_clear_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'studio.json'
            for bad, message in (({'libraries': [{'name': 'x'}]}, 'needs a non-empty "root"'),
                                 ({'tools': {'ffmpeg': 3}}, 'tools.ffmpeg'),
                                 ({'action_roots': 'actions'}, 'action_roots')):
                atomic_json(config, bad)
                with self.assertRaisesRegex(SettingsError, message):
                    load_settings(config, install=Path(folder) / 'missing.ini')


class DamagedPreferencesTests(unittest.TestCase):
    def test_damaged_file_is_set_aside(self):
        from tinylib.preferences import Preferences
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            path.write_text('{broken', encoding='utf-8')
            prefs = Preferences(path)
            self.assertIn('reset', prefs.warning)
            self.assertEqual(len(list(Path(folder).glob('preferences.damaged-*.json'))), 1)
            prefs.rate([{'library_root': 'r', 'main': 'm'}], 2)
            self.assertEqual(Preferences(path).warning, None)


class CachePruneTests(unittest.TestCase):
    def test_prunes_old_files_and_old_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            current = root / ('indexes-v%s' % INDEX_CACHE_VERSION)
            current.mkdir()
            old, fresh = current / 'old.json', current / 'fresh.json'
            old.write_text('{}')
            fresh.write_text('{}')
            stamp = time.time() - 90 * 86400
            os.utime(old, (stamp, stamp))
            (root / 'indexes-v1').mkdir()
            (root / 'indexes-v1' / 'x.json').write_text('{}')
            (root / 'unrelated').mkdir()
            self.assertEqual(prune_cache(root, max_age_days=60), 1)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertFalse((root / 'indexes-v1').exists())
            self.assertTrue((root / 'unrelated').exists())


if __name__ == '__main__':
    unittest.main()
