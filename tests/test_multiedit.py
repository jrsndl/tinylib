import copy
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtWidgets
from tinylib.properties_ui import PropertiesEditor


class MultiEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.assets = [dict(id='fire/%s' % name, name=name, library='Test', library_root='C:/test',
                            main='fire/%s/main.exr' % name, category='fire', kind='footage',
                            first=1, last=2, colorspace='ACEScg', tags=[name.lower()],
                            ingest_note=note, metadata={'width': 1920})
                       for name, note in [('One', 'First note'), ('Two', 'Second note')]]
        self.editor = PropertiesEditor()
        self.editor.set_assets(self.assets, True, True)
        self.addCleanup(self.editor.close)

    def test_aggregate_does_not_create_changes(self):
        self.assertEqual(self.editor.fields['ingest_note'].toPlainText(), 'First note Second note')
        self.assertEqual(self.editor.fields['colorspace'].text(), 'ACEScg')
        self.assertEqual(self.editor.changes(), {})
        self.assertIn('#ffa02f', self.editor.fields['ingest_note'].styleSheet())
        self.assertTrue(self.editor.fields['version_number'].isReadOnly())

    def test_keyword_display_merges_trimmed_unique_values_without_changing_assets(self):
        assets = copy.deepcopy(self.assets)
        assets[0]['tags'] = [' smoke ', 'fire, sparks', '']
        assets[1]['tags'] = ['fire', ' smoke', 'embers ', 'sparks']
        self.editor.set_assets(assets, False, True)
        self.editor.set_assets(assets, True, True)
        self.assertEqual(self.editor.fields['tags'].toPlainText(), 'smoke, fire, sparks, embers')
        self.assertEqual(self.editor.changes(), {})
        self.assertIn('#ffa02f', self.editor.fields['tags'].styleSheet())
        self.assertEqual(assets[0]['tags'], [' smoke ', 'fire, sparks', ''])

    def test_only_modified_text_is_red_and_returned(self):
        self.editor.fields['ingest_note'].setPlainText('Shared note')
        self.assertIn('#ef5b5b', self.editor.fields['ingest_note'].styleSheet())
        self.assertIn('#ffa02f', self.editor.fields['colorspace'].styleSheet())
        self.assertEqual(self.editor.changes(), {'ingest_note': 'Shared note'})
        self.assertEqual(self.assets[0]['ingest_note'], 'First note')
        self.editor.fields['ingest_note'].setPlainText('First note Second note')
        self.assertEqual(self.editor.changes(), {})
        self.assertIn('#ffa02f', self.editor.fields['ingest_note'].styleSheet())

    def test_draft_survives_repeat_selection_and_keywords_replace_explicitly(self):
        self.editor.fields['tags'].setPlainText('shared, smoke')
        self.editor.set_assets(self.assets, True, True)
        self.assertEqual(self.editor.changes(), {'tags': ['shared', 'smoke']})

    def test_field_permissions_are_intersection_of_selection(self):
        class Access:
            def field_allowed(self, root, key):
                return not (root == 'C:/restricted' and key == 'ingest_note')
        self.editor.access = Access()
        assets = copy.deepcopy(self.assets)
        assets[1]['library_root'] = 'C:/restricted'
        self.editor.set_assets(assets, True, True)
        self.assertTrue(self.editor.fields['ingest_note'].isReadOnly())
        self.assertTrue(self.editor.fields['ingest_note'].isHidden())
        self.assertNotIn('#ffa02f', self.editor.fields['ingest_note'].styleSheet())
        self.assertFalse(self.editor.fields['tags'].isReadOnly())
