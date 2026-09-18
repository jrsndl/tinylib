import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.access import AccessControl
from tinylib.asset_edit import save_asset
from tinylib.library import Library, atomic_json, read_json
from tinylib.tile_text import render_template, validate_template


class AssetEditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = {'_config_path': str(self.root / 'studio.json'),
                       'libraries': [{'name': 'Test', 'root': str(self.root)}]}
        self.access = AccessControl(self.config, identity='test', persist=False)
        self.record = {'id': 'fire/test', 'name': 'Test', 'category': 'fire', 'kind': 'footage', 'tags': ['fire'],
                       'main': 'fire/test/main/test.####.exr', 'first': 1001, 'last': 1100,
                       'colorspace': 'ACEScg', 'metadata': {'width': 1920, 'height': 1080, 'FPS': 24}}
        atomic_json(self.root / 'data.json', {'schema_version': 3, 'studio_extra': True, 'assets': [self.record]})
        self.library = Library(self.root, 'Test')
        self.asset = self.library.load()[0]

    def test_metadata_edit_preserves_paths_identity_and_other_top_level_fields(self):
        saved = save_asset(self.config, self.access, self.asset, {'colorspace': 'ACES2065-1', 'tags': ['hot']})
        self.assertEqual(saved['colorspace'], 'ACES2065-1')
        for field in ('main', 'id', 'kind', 'library_root'):
            self.assertEqual(saved[field], self.asset[field])
        self.assertTrue(read_json(self.root / 'data.json')['studio_extra'])
        self.assertEqual(read_json(self.root / 'data.json')['assets'][0]['main'], self.record['main'])

    def test_stale_same_field_rejected_other_field_preserved(self):
        save_asset(self.config, self.access, self.asset, {'tags': ['new']})
        with self.assertRaises(ValueError):
            save_asset(self.config, self.access, self.asset, {'tags': ['lost update']})
        saved = save_asset(self.config, self.access, self.asset, {'colorspace': 'sRGB'})
        self.assertEqual(saved['tags'], ['new'])

    def test_readonly_denied_and_immutable_fields_rejected(self):
        before = (self.root / 'data.json').read_bytes()
        self.config['libraries'][0]['read_only'] = True
        with self.assertRaises(PermissionError):
            save_asset(self.config, self.access, self.asset, {'colorspace': 'No'})
        self.config['libraries'][0]['read_only'] = False
        denied = copy.deepcopy(self.config)
        denied['access'] = {'groups': ['restricted'], 'users': {'test': ['restricted']}}
        with self.assertRaises(PermissionError):
            save_asset(self.config, AccessControl(denied, identity='test', persist=False), self.asset, {'colorspace': 'No'})
        for field in ('name', 'category', 'first', 'last', 'main', 'kind', 'library', 'id'):
            with self.assertRaises(ValueError):
                save_asset(self.config, self.access, self.asset, {field: 'No'})
        self.assertEqual((self.root / 'data.json').read_bytes(), before)

    def test_invalid_fields_and_failed_write_leave_database_unchanged(self):
        before = (self.root / 'data.json').read_bytes()
        for change in ({'colorspace': ''}, {'metadata': {'width': -1}}, {'tags': 'text'}):
            with self.assertRaises(ValueError):
                save_asset(self.config, self.access, self.asset, change)
        with patch('tinylib.library.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                save_asset(self.config, self.access, self.asset, {'colorspace': 'Failed'})
        self.assertEqual((self.root / 'data.json').read_bytes(), before)

    def test_legacy_edit_keeps_backup(self):
        legacy = {'data': {'elements': {'root|fire': [{'source': str(self.root / 'fire/Test/main/Test.####.exr') + ' 1-10', 'tags': []}]}}}
        atomic_json(self.root / 'data.json', legacy)
        before = (self.root / 'data.json').read_bytes()
        asset = self.library.load()[0]
        save_asset(self.config, self.access, asset, {'tags': ['new']})
        self.assertEqual((self.root / 'data.legacy.backup.json').read_bytes(), before)
        self.assertEqual(self.library.load()[0]['tags'], ['new'])

    def test_tile_tokens_and_literal_newlines(self):
        self.assertEqual(render_template(r'{name}\n{width} × {height}', self.asset), 'Test\n1920 × 1080')
        self.assertEqual(render_template('{length}', self.asset), '4.17')
        self.assertEqual(render_template('{name}', dict(self.asset, name='two\nlines')), 'two lines')
        for template in ('{unknown}', '{name.__class__}', '{name!r}', '{width:999999}', '\n' * 6):
            with self.assertRaises(ValueError):
                validate_template(template)


if __name__ == '__main__':
    unittest.main()
