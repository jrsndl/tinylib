import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.preferences import Preferences, asset_key
from tinylib.filters import accepts, duration


def asset(name='Fire', root='E:/lib'):
    return dict(name=name, library='Elements', library_root=root, main=root + '/fire/' + name + '.exr',
                id=name, category='fire', kind='footage', tags=['fire', 'smoke'],
                metadata={'FPS': '24000/1001', 'Frame(s)': '240', 'width': 2048})


class FilterTests(unittest.TestCase):
    def test_combined_filters_and_inversion(self):
        value = asset()
        self.assertAlmostEqual(duration(value), 10.01)
        self.assertTrue(accepts(value, 'fire', False, ['smoke'], ('>', 10), ('>', 1920), ('>', 3), 4))
        self.assertFalse(accepts(value, 'fire', True))
        self.assertTrue(accepts(value, 'rain', True, ['smoke']))
        self.assertFalse(accepts(value, 'rain', True, ['missing']))
        self.assertTrue(accepts(value, '', True))
        self.assertFalse(accepts(value, width=('<', 2048)))
        self.assertFalse(accepts(value, stars=('>', 4), rating=4))
        self.assertTrue(accepts(value, stars=('=', 0), rating=0))

    def test_stills_and_missing_metadata(self):
        value = asset()
        value['metadata'] = {}
        self.assertIsNone(duration(value))
        self.assertFalse(accepts(value, length=('<', 10)))
        self.assertFalse(accepts(value, width=('<', 100)))
        self.assertTrue(accepts(value))
        value['kind'] = 'hdri'
        self.assertEqual(duration(value), 0)
        self.assertTrue(accepts(value, length=('<', 1)))


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'preferences.json'
        self.prefs = Preferences(self.path)

    def test_collection_lifecycle_and_restart(self):
        first = self.prefs.create_collection()
        second = self.prefs.create_collection()
        self.assertEqual([c['name'] for c in self.prefs.collections], ['collection01', 'collection02'])
        self.prefs.add_assets(first, [asset(), asset(), asset('Smoke')])
        self.assertEqual(len(self.prefs.collection(first)['assets']), 2)
        self.prefs.add_assets(second, [asset()])
        self.prefs.rename_collection(first, 'Shot 010')
        self.prefs = Preferences(self.path)
        self.assertEqual(self.prefs.collection(first)['name'], 'Shot 010')
        self.prefs.remove_assets(first, [asset_key(asset())])
        self.assertIn(asset_key(asset()), self.prefs.picked_keys())
        self.prefs.delete_collection(second)
        self.assertNotIn(asset_key(asset()), self.prefs.picked_keys())
        self.assertIn(asset_key(asset('Smoke')), self.prefs.picked_keys())

    def test_identity_and_ratings(self):
        self.assertNotEqual(asset_key(asset()), asset_key(asset(root='F:/lib')))
        migrated = asset()
        migrated.update(id='new-schema-id', library='Renamed library')
        self.assertEqual(asset_key(asset()), asset_key(migrated))
        self.prefs.rate([asset(), asset('Smoke')], 5)
        self.assertEqual(Preferences(self.path).rating(migrated), 5)
        self.prefs.rate([asset()], 0)
        self.assertEqual(self.prefs.rating(asset()), 0)
        self.assertEqual(self.prefs.rating(asset('Smoke')), 5)

    def test_independent_instances_preserve_edits(self):
        other = Preferences(self.path)
        self.prefs.create_collection('First')
        other.create_collection('Second')
        self.prefs.rate([asset()], 3)
        self.assertEqual([c['name'] for c in self.prefs.collections], ['First', 'Second'])

    def test_failed_save_rolls_back_and_invalid_json_is_not_overwritten(self):
        with patch('tinylib.preferences.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.prefs.create_collection()
        self.assertEqual(self.prefs.collections, [])
        self.path.write_text('invalid', encoding='utf-8')
        with self.assertRaises(ValueError):
            Preferences(self.path)
        self.assertEqual(self.path.read_text(), 'invalid')


if __name__ == '__main__':
    unittest.main()
