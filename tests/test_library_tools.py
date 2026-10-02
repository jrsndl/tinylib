import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tinylib.library import atomic_json
from tinylib.library_tools import crosscheck_library


class LibraryCrosscheckTests(unittest.TestCase):
    def test_missing_json_paths_and_orphan_asset_folders_are_logged(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'library'
        output = Path(temporary.name) / 'downloads'
        present = root / 'fire' / 'Present'
        (present / 'main').mkdir(parents=True)
        (present / 'thumb').mkdir()
        (present / 'main' / 'plate.1001.exr').write_bytes(b'x')
        (present / 'thumb' / 'thumb.jpg').write_bytes(b'x')
        (root / 'fire' / 'Orphan' / 'main').mkdir(parents=True)
        records = [{
            'id': 'fire/Present', 'name': 'Present', 'category': 'fire', 'kind': 'footage',
            'main': 'fire/Present/main/plate.####.exr', 'first': 1001, 'last': 1002,
            'thumb': 'fire/Present/thumb/thumb.jpg', 'proxy': 'fire/Present/proxy/missing.mp4',
            'tags': [], 'colorspace': 'ACEScg',
            'metadata': {'files': ['textures/missing.tx']},
        }]
        atomic_json(root / 'tinylib_data.json', {'schema_version': 3, 'assets': records})
        events = []
        result = crosscheck_library(root, 'Elements Library', output, lambda *args: events.append(args),
                                    now=datetime(2026, 10, 2, 12, 34, 56, tzinfo=timezone.utc))
        log = Path(result['log_path'])
        self.assertEqual(log.name, 'Elements Library_ccrosscheck_20261002_123456.log')
        text = log.read_text(encoding='utf-8')
        self.assertIn('[fire/Present]', text)
        self.assertIn('plate.1002.exr', text)
        self.assertIn('proxy/missing.mp4', text.replace('\\', '/'))
        self.assertIn('textures/missing.tx', text.replace('\\', '/'))
        self.assertIn(str(root / 'fire' / 'Orphan'), text)
        self.assertEqual(result['missing_records'], 1)
        self.assertEqual(result['missing_paths'], 3)
        self.assertEqual(result['orphan_folders'], 1)
        self.assertTrue(events and events[-1][0] == events[-1][1])


if __name__ == '__main__':
    unittest.main()
