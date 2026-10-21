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

    def test_json_field_filters_combine_with_plain_search(self):
        value = asset()
        value.update(acquire_price='usd 25.50', ingest_note='Shot in slow motion')
        self.assertTrue(accepts(value, 'fire {acquire price}:usd'))
        self.assertTrue(accepts(value, 'FIRE {ACQUIRE_PRICE}:USD'))
        self.assertFalse(accepts(value, 'rain {acquire price}:usd'))
        self.assertFalse(accepts(value, 'fire {acquire price}:eur'))
        self.assertTrue(accepts(value, '{acquire price}:usd {ingest note}:"slow motion"'))
        self.assertTrue(accepts(value, 'fire -{acquire price}:eur'))
        self.assertFalse(accepts(value, 'fire -{acquire price}:usd'))
        self.assertFalse(accepts(value, '{missing field}:usd'))
        self.assertTrue(accepts(value, 'fire {acquire price}:eur', invert=True))
        self.assertFalse(accepts(value, 'fire {acquire price}:usd', invert=True))
        self.assertTrue(accepts(value, 'fire {acquire price}:usd tag:smoke -night'))

    def test_field_filters_search_nested_json_and_non_text_values(self):
        value = asset()
        value['metadata'].update(is_raw=True, extras=[{'camera_model': 'ARRI Alexa'}])
        self.assertTrue(accepts(value, '{metadata.width}:2048 {is raw}:true'))
        self.assertTrue(accepts(value, '{width}:2048 {camera model}:alexa'))
        self.assertTrue(accepts(value, '{metadata.extras.camera model}:arri'))
        self.assertTrue(accepts(value, '{tags}:smoke'))
        self.assertFalse(accepts(value, '{metadata.width}:1920'))
        # Incomplete clauses remain ordinary search text and never raise.
        self.assertFalse(accepts(value, 'fire {acquire price}:'))
        self.assertFalse(accepts(value, 'fire {ingest note}:"unfinished'))

    def test_exclamation_excludes_words_and_quoted_phrases(self):
        value = asset(name='Big Burst Fire')
        self.assertFalse(accepts(value, '!burst'))
        self.assertTrue(accepts(value, '!rain'))
        self.assertFalse(accepts(value, '!"big burst"'))
        self.assertTrue(accepts(value, '!"small burst"'))
        self.assertFalse(accepts(value, 'fire !smoke'))  # Keywords count too.
        self.assertTrue(accepts(value, 'fire !rain !"small burst"'))
        self.assertTrue(accepts(value, '!burst', invert=True))

    def test_exclamation_excludes_json_field_values(self):
        value = asset()
        value['ingest_note'] = 'Big burst at night'
        self.assertFalse(accepts(value, '{ingest_note}!burst'))
        self.assertFalse(accepts(value, '{INGEST NOTE}!"big burst"'))
        self.assertTrue(accepts(value, '{ingest note}!rain'))
        self.assertTrue(accepts(value, '{ingest note}!"small burst"'))
        self.assertTrue(accepts(value, '{missing field}!burst'))
        self.assertTrue(accepts(value, 'fire !rain {ingest note}!day {metadata.width}:2048'))
        self.assertFalse(accepts(value, 'fire {ingest note}:night {ingest note}!burst'))
        self.assertFalse(accepts(value, '{metadata.width}!2048'))
        self.assertTrue(accepts(value, '{metadata.width}!1920'))

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
        # Startup survives a damaged file; its content is kept aside, never overwritten.
        recovered = Preferences(self.path)
        self.assertIn('reset', recovered.warning)
        aside = list(self.path.parent.glob('preferences.damaged-*.json'))
        self.assertEqual([item.read_text() for item in aside], ['invalid'])


class TrimTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'preferences.json'
        self.prefs = Preferences(self.path)

    def test_user_trims_are_per_user_and_survive_restart(self):
        self.assertIsNone(self.prefs.trim(asset()))
        self.prefs.set_trim(asset(), (1010, 1050))
        self.assertEqual(Preferences(self.path).trim(asset()), (1010, 1050))
        self.assertIsNone(self.prefs.trim(asset('Smoke')))
        self.prefs.set_trim(asset(), None)
        self.assertIsNone(Preferences(self.path).trim(asset()))
        for bad in ((1050, 1010), (1.5, 1060), ('1', '2')):
            with self.assertRaises(ValueError):
                self.prefs.set_trim(asset(), bad)

    def test_collections_hold_their_own_trims_and_share_them(self):
        self.prefs.set_trim(asset(), (1010, 1050))
        collection = self.prefs.create_collection('Picks')
        self.prefs.add_assets(collection, [asset(), asset('Smoke')])
        # Adding copies the user's in/out points; the collection then keeps its own.
        self.assertEqual(self.prefs.trim(asset(), collection), (1010, 1050))
        self.assertIsNone(self.prefs.trim(asset('Smoke'), collection))
        self.prefs.set_trim(asset(), (1020, 1030), collection)
        self.assertEqual(self.prefs.trim(asset(), collection), (1020, 1030))
        self.assertEqual(self.prefs.trim(asset()), (1010, 1050))
        exported = self.root / 'picks.json'
        self.prefs.export_collection(collection, exported)
        other = Preferences(self.root / 'other.json')
        imported = other.import_collection(exported)
        self.assertEqual(other.trim(asset(), imported), (1020, 1030))
        self.assertIsNone(other.trim(asset()))  # Importing never changes the artist's own points.
        self.assertIsNone(other.trim(asset('Smoke'), imported))

    def test_import_rejects_invalid_trims(self):
        from tinylib.library import atomic_json
        bad = dict(asset(), **{'in': 1050, 'out': 1010})
        atomic_json(self.root / 'bad.json', {'format': 'tinylib.collection', 'version': 2, 'name': 'Bad',
                                            'assets': [bad]})
        with self.assertRaisesRegex(ValueError, 'in/out'):
            self.prefs.import_collection(self.root / 'bad.json')

    def test_playback_stays_inside_in_and_out(self):
        from tinylib.trim import advance_frame
        self.assertEqual(advance_frame(51, 10, 50, True), (10, False))
        self.assertEqual(advance_frame(9, 10, 50, True), (50, False))
        self.assertEqual(advance_frame(52, 10, 50, True), (11, False))  # Fast playback wraps by the overshoot.
        self.assertEqual(advance_frame(30, 10, 50, True), (30, False))
        self.assertEqual(advance_frame(50, 10, 50, False), (50, True))
        self.assertEqual(advance_frame(10, 10, 50, False), (10, True))
        self.assertEqual(advance_frame(5, 5, 5, True), (5, False))


if __name__ == '__main__':
    unittest.main()
