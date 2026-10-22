"""Keyword cleaner: the rules, the plan, and writing one library (or its selected assets)."""
import os
import tempfile
import unittest
from pathlib import Path

from helpers import requires_qt

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.access import AccessControl
from tinylib.edit_lock import LibraryEditLocked, acquire_edit_lock, read_edit_locks, release_edit_lock
from tinylib.library import atomic_json, read_json
from tinylib.library_tools import clean_keywords, clean_library_keywords, keyword_list, plan_keywords


class KeywordRuleTests(unittest.TestCase):
    def test_number_only_keywords_go(self):
        self.assertEqual(clean_keywords(['fire', '01', '2525', '1.5', '2k', 'v002']), ['fire', '2k', 'v002'])
        self.assertEqual(clean_keywords(['01', 'fire'], remove_numbers=False), ['01', 'fire'])

    def test_replace_matches_whole_keywords_ignoring_case(self):
        rules = [('Fire', 'flame'), ('smoke', ''), ('', 'ignored')]
        self.assertEqual(clean_keywords(['fire', 'firestorm', 'smoke', 'dark'], replacements=rules),
                         ['flame', 'firestorm', 'dark'])

    def test_a_comma_separated_replacement_gives_several_keywords(self):
        rules = [('abandonedbakery', 'Abandoned, bakery, ')]
        self.assertEqual(clean_keywords(['abandonedbakery', 'indoor'], replacements=rules),
                         ['abandoned', 'bakery', 'indoor'])

    def test_replacements_run_in_order_and_duplicates_are_kept_once(self):
        rules = [('a', 'b'), ('b', 'c')]
        self.assertEqual(clean_keywords(['a', 'b', 'C', 'd'], replacements=rules), ['c', 'd'])

    def test_added_keywords_go_last_and_only_once(self):
        self.assertEqual(clean_keywords(['Indoor', '01'], add=['hdri', 'indoor', 'HDRI ']), ['Indoor', 'hdri'])
        self.assertEqual(keyword_list(' HDRI, polyhaven,, indoor '), ['hdri', 'polyhaven', 'indoor'])

    def test_plan_lists_only_changed_assets(self):
        records = [{'name': 'x', 'tags': ['fire', '01']}, {'name': 'y', 'tags': ['smoke']}, {'name': 'z'}]
        self.assertEqual([(record['name'], tags) for record, tags in plan_keywords(records)], [('x', ['fire'])])


class CleanLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = {'name': 'Test', 'root': str(self.root)}
        self.settings = {'libraries': [self.library], '_config_path': str(self.root / 'studio.json')}
        self.access = AccessControl(self.settings, identity='test/admin', persist=False)
        self.database = self.root / 'tinylib_data.json'
        atomic_json(self.database, {'schema_version': 3, 'custom': 1, 'assets': [
            {'id': 'fx/a', 'category': 'fx', 'name': 'a', 'tags': ['fire', '01'], 'metadata': {'w': 1}},
            {'id': 'fx/b', 'category': 'fx', 'name': 'b', 'tags': ['smoke', '2525']},
        ]})

    def test_whole_library(self):
        result = clean_library_keywords(self.settings, self.access, self.library, replacements=[('smoke', 'haze')])
        self.assertEqual(result['assets'], 2)
        self.assertEqual(result['changes'], [('fx/a', ['fire', '01'], ['fire']), ('fx/b', ['smoke', '2525'], ['haze'])])
        saved = read_json(self.database)
        self.assertEqual([asset['tags'] for asset in saved['assets']], [['fire'], ['haze']])
        self.assertEqual((saved['custom'], saved['assets'][0]['metadata']), (1, {'w': 1}))
        self.assertFalse(read_edit_locks(self.root))

    def test_add_keywords_to_the_selected_assets(self):
        result = clean_library_keywords(self.settings, self.access, self.library, asset_ids=['fx/a'],
                                        remove_numbers=False, add=['hdri', 'fire'])
        self.assertEqual(result['changes'], [('fx/a', ['fire', '01'], ['fire', '01', 'hdri'])])
        self.assertEqual(read_json(self.database)['assets'][1]['tags'], ['smoke', '2525'])

    def test_only_the_selected_assets(self):
        result = clean_library_keywords(self.settings, self.access, self.library, asset_ids=['fx/b'])
        self.assertEqual((result['assets'], len(result['changes'])), (1, 1))
        self.assertEqual([asset['tags'] for asset in read_json(self.database)['assets']], [['fire', '01'], ['smoke']])

    def test_an_edit_lock_blocks_it(self):
        handle = acquire_edit_lock(self.root, 'other/editor')
        try:
            before = self.database.read_bytes()
            with self.assertRaises(LibraryEditLocked):
                clean_library_keywords(self.settings, self.access, self.library)
            self.assertEqual(self.database.read_bytes(), before)
        finally:
            release_edit_lock(handle)


@requires_qt
class KeywordsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tinylib.qt import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_a_selection_narrows_the_scope_and_the_ui_says_so(self):
        from tinylib.library_tools_ui import KeywordsDialog
        libraries = [{'name': 'A', 'root': 'c:/libA'}, {'name': 'B', 'root': 'c:/libB'}]
        assets = [{'library_root': 'c:/libA', 'id': 'x/1', 'name': 'one', 'tags': ['fire', '01']},
                  {'library_root': 'c:/libA', 'id': 'x/2', 'name': 'two', 'tags': ['2525']},
                  {'library_root': 'c:/libB', 'id': 'y/1', 'name': 'three', 'tags': ['7']}]
        dialog = KeywordsDialog(libraries, assets, [assets[1]], {}, None, '')
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.library.currentText(), 'A')  # the library of the selection
        self.assertIn('1 selected asset', dialog.scope.text())
        self.assertEqual(dialog.preview.topLevelItemCount(), 1)
        self.assertEqual(dialog.preview.topLevelItem(0).text(2), '(none)')
        dialog.add.setText('hdri, Fire')
        dialog.update_preview()
        self.assertEqual(dialog.preview.topLevelItem(0).text(2), 'hdri, fire')
        dialog.add.clear()
        dialog.library.setCurrentIndex(1)  # nothing of B is selected: the whole library
        self.assertIn('whole library', dialog.scope.text())
        self.assertEqual(dialog.preview.topLevelItemCount(), 1)


if __name__ == '__main__':
    unittest.main()
