"""Labels from names: the label rules, the plan, and writing one library under its locks."""
import os
import tempfile
import unittest
from pathlib import Path

from helpers import requires_qt

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.access import AccessControl
from tinylib.edit_lock import LibraryEditLocked, acquire_edit_lock, read_edit_locks, release_edit_lock
from tinylib.library import atomic_json, read_json
from tinylib.library_tools import label_from_name, label_library, plan_labels


class LabelRuleTests(unittest.TestCase):
    def test_camel_case_and_separators(self):
        self.assertEqual(label_from_name('plhn_indoor_abandonedBakery'), 'plhn indoor abandoned bakery')
        self.assertEqual(label_from_name('abandonedFactory-canteen_01'), 'abandoned factory canteen 01')
        self.assertEqual(label_from_name('HDRISky'), 'HDRI sky')
        self.assertEqual(label_from_name('skyHDRI'), 'sky HDRI')
        self.assertEqual(label_from_name('Big_Fire_01'), 'Big Fire 01')  # a capital after a separator stays

    def test_each_rule_can_be_switched_off(self):
        self.assertEqual(label_from_name('my_abandonedBakery', camel_case=False), 'my abandonedBakery')
        self.assertEqual(label_from_name('my_abandonedBakery', separators=False), 'my_abandoned bakery')
        self.assertEqual(label_from_name('a__b', separators=False, camel_case=False), 'a__b')

    def test_replacements_run_last_in_order(self):
        rules = {'replacements': [('plhn ', ''), ('indoor', 'interior'), ('', 'ignored')]}
        self.assertEqual(label_from_name('plhn_indoor_abandonedBakery', **rules), 'interior abandoned bakery')
        self.assertEqual(label_from_name('a_b', replacements=[('a b', 'x'), ('x', 'y')]), 'y')
        self.assertEqual(label_from_name('Fire', replacements=[('fire', 'smoke')]), 'Fire')  # case-sensitive


class PlanTests(unittest.TestCase):
    records = [{'name': 'abandonedBakery'}, {'name': 'kitchen', 'label': ''},
               {'name': 'sky_dome', 'label': 'Hand made'}, {'name': 'Already Fine'}]

    def test_only_empty_labels_by_default(self):
        plan = [(record['name'], label) for record, label in plan_labels(self.records)]
        # kitchen and "Already Fine" would equal their names: an empty label already shows the name.
        self.assertEqual(plan, [('abandonedBakery', 'abandoned bakery')])

    def test_all_labels_replaced_when_asked(self):
        plan = [(record['name'], label) for record, label in plan_labels(self.records, only_empty=False)]
        self.assertEqual(plan, [('abandonedBakery', 'abandoned bakery'), ('sky_dome', 'sky dome')])

    def test_a_label_that_comes_out_as_the_name_is_cleared(self):
        plan = plan_labels([{'name': 'kitchen', 'label': 'old'}], only_empty=False)
        self.assertEqual(plan[0][1], '')

    def test_invalid_labels_stop_the_plan(self):
        with self.assertRaisesRegex(ValueError, 'at most'):
            plan_labels([{'name': 'x' * 300 + '_y'}])


class LabelLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = {'name': 'Test', 'root': str(self.root)}
        self.settings = {'libraries': [self.library], '_config_path': str(self.root / 'studio.json')}
        self.access = AccessControl(self.settings, identity='test/admin', persist=False)
        self.database = self.root / 'tinylib_data.json'
        atomic_json(self.database, {'schema_version': 3, 'custom': {'keep': 1}, 'assets': [
            {'id': 'hdri/a', 'category': 'hdri', 'name': 'plhn_abandonedBakery', 'metadata': {'x': 1}},
            {'id': 'hdri/b', 'category': 'hdri', 'name': 'plhn_skyDome', 'label': 'Keep me'},
        ]})

    def test_writes_labels_and_keeps_everything_else(self):
        rules = {'replacements': [('plhn ', '')]}
        result = label_library(self.settings, self.access, self.library, rules=rules)
        self.assertEqual(result, {'assets': 2, 'changes': [('hdri/a', '', 'abandoned bakery')]})
        saved = read_json(self.database)
        self.assertEqual(saved['custom'], {'keep': 1})
        self.assertEqual(saved['assets'][0]['label'], 'abandoned bakery')
        self.assertEqual(saved['assets'][0]['metadata'], {'x': 1})
        self.assertEqual(saved['assets'][1]['label'], 'Keep me')
        self.assertFalse(read_edit_locks(self.root))

    def test_replace_existing_labels(self):
        result = label_library(self.settings, self.access, self.library, only_empty=False)
        self.assertEqual(len(result['changes']), 2)
        self.assertEqual(read_json(self.database)['assets'][1]['label'], 'plhn sky dome')

    def test_an_edit_lock_blocks_it_without_changes(self):
        handle = acquire_edit_lock(self.root, 'other/editor')
        try:
            before = self.database.read_bytes()
            with self.assertRaises(LibraryEditLocked):
                label_library(self.settings, self.access, self.library)
            self.assertEqual(self.database.read_bytes(), before)
        finally:
            release_edit_lock(handle)


@requires_qt
class LabelsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tinylib.qt import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_preview_follows_the_options(self):
        from tinylib.library_tools_ui import LabelsDialog
        libraries = [{'name': 'A', 'root': 'c:/libA'}, {'name': 'B', 'root': 'c:/libB'}]
        assets = [{'library_root': 'c:/libA', 'name': 'abandonedBakery'},
                  {'library_root': 'c:/libA', 'name': 'sky_dome', 'label': 'Mine'},
                  {'library_root': 'c:/libB', 'name': 'other_Asset'}]
        dialog = LabelsDialog(libraries, assets, {}, None, 'c:/libA')
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.library.currentText(), 'A')
        self.assertTrue(dialog.only_empty.isChecked())
        self.assertEqual(dialog.preview.topLevelItemCount(), 1)
        self.assertEqual(dialog.preview.topLevelItem(0).text(2), 'abandoned bakery')
        dialog.only_empty.setChecked(False)
        dialog.update_preview()
        self.assertEqual(dialog.preview.topLevelItemCount(), 2)
        dialog.replacements[0][0].setText('dome')
        dialog.replacements[0][1].setText('sphere')
        dialog.update_preview()
        self.assertEqual(dialog.preview.topLevelItem(1).text(2), 'sky sphere')
        dialog.library.setCurrentIndex(1)
        dialog.update_preview()
        self.assertEqual(dialog.preview.topLevelItemCount(), 1)  # one library at a time


if __name__ == '__main__':
    unittest.main()
