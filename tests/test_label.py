import tempfile
import unittest
from pathlib import Path
from tinylib.access import AccessControl
from tinylib.asset_edit import save_asset
from tinylib.asset_fields import display_name, validate_label
from tinylib.library import Library, atomic_json, matches, read_json
from tinylib.tile_text import render_template


class LabelTests(unittest.TestCase):
    """An asset's label is shown instead of its name; the name stays and names the folder."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = {'_config_path': str(self.root / 'studio.json'),
                       'libraries': [{'name': 'Test', 'root': str(self.root)}]}
        self.access = AccessControl(self.config, identity='test', persist=False)
        record = {'id': 'smoke/smoke_puff_09b', 'name': 'smoke_puff_09b', 'category': 'smoke', 'kind': 'footage',
                  'tags': ['smoke'], 'main': 'smoke/smoke_puff_09b/main/smoke_puff_09b.####.exr',
                  'first': 1001, 'last': 1010, 'colorspace': 'ACEScg', 'metadata': {}}
        atomic_json(self.root / 'tinylib_data.json', {'schema_version': 3, 'assets': [record]})
        self.asset = Library(self.root, 'Test').load(use_cache=False)[0]

    def test_display_name_falls_back_to_the_name(self):
        self.assertEqual(display_name({'name': 'a'}), 'a')  # older assets have no label
        self.assertEqual(display_name({'name': 'a', 'label': '  '}), 'a')
        self.assertEqual(display_name({'name': 'a', 'label': 'Big puff'}), 'Big puff')

    def test_label_is_saved_and_name_paths_and_id_stay(self):
        saved = save_asset(self.config, self.access, self.asset, {'label': 'Big smoke puff'})
        self.assertEqual(display_name(saved), 'Big smoke puff')
        stored = read_json(self.root / 'tinylib_data.json')['assets'][0]
        self.assertEqual(stored['label'], 'Big smoke puff')
        for key in ('name', 'id', 'main'):
            self.assertEqual(stored[key], {'name': 'smoke_puff_09b', 'id': 'smoke/smoke_puff_09b',
                                           'main': 'smoke/smoke_puff_09b/main/smoke_puff_09b.####.exr'}[key])
        cleared = save_asset(self.config, self.access, saved, {'label': ''})
        self.assertEqual(display_name(cleared), 'smoke_puff_09b')

    def test_label_must_be_one_short_line(self):
        validate_label('Big puff')
        for bad in ('two\nlines', 'x' * 201, 5):
            with self.assertRaises(ValueError):
                validate_label(bad)
        with self.assertRaises(ValueError):
            save_asset(self.config, self.access, self.asset, {'label': 'a\nb'})

    def test_tile_tokens_and_search(self):
        asset = dict(self.asset, label='Big puff')
        self.assertEqual(render_template('{name}|{ingest_name}', asset), 'Big puff|smoke_puff_09b')
        self.assertEqual(render_template('{name}', self.asset), 'smoke_puff_09b')
        self.assertTrue(matches(asset, 'big'))
        self.assertTrue(matches(asset, 'smoke_puff_09b'))


if __name__ == '__main__':
    unittest.main()
