"""The Parts row of the properties panel: components and their file types."""
import os
import unittest

from helpers import requires_qt

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.asset_fields import asset_parts


class PartsTests(unittest.TestCase):
    def test_footage_main_first_previews_hidden(self):
        asset = {'main': 'L:/lib/fx/a/main/a.####.exr', 'proxy': 'L:/lib/fx/a/proxy/a.mp4',
                 'thumb': 'L:/lib/fx/a/thumb/a.jpg', 'filmstrip': 'L:/lib/fx/a/filmstrip/a.jpg'}
        self.assertEqual(asset_parts(asset), 'main:exr proxy:mp4')

    def test_hdri_others_alphabetical_and_ranges_ignored(self):
        asset = {'proxy': 'P:/b/proxy/b.JPG', 'highres': 'P:/b/highres/b.####.exr 1001-1002',
                 'main': 'P:\\\\b\\\\main\\\\b.exr', 'thumb': 'P:/b/thumb/b.jpg'}
        self.assertEqual(asset_parts(asset), 'main:exr highres:exr proxy:jpg')

    def test_folders_scenes_and_missing_parts(self):
        self.assertEqual(asset_parts({'main': 'L:/lib/c/x/main', 'scene': 'L:/s/x.blend', 'proxy': ''}),
                         'main:folder scene:blend')
        self.assertEqual(asset_parts({}), '')


@requires_qt
class PartsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tinylib.qt import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_the_row_sits_below_type_and_is_read_only(self):
        from tinylib.properties_ui import PropertiesEditor
        panel = PropertiesEditor()
        self.addCleanup(panel.close)
        panel.set_assets([{'name': 'b', 'library': 'L', 'category': 'c', 'kind': 'still',
                           'main': 'P:/b/main/b.exr', 'proxy': 'P:/b/proxy/b.jpg', 'metadata': {}}], True, True)
        field = panel.fields['parts']
        self.assertEqual(field.text(), 'main:exr proxy:jpg')
        self.assertTrue(field.isReadOnly())
        row = panel.summary.getWidgetPosition(field)[0]
        self.assertEqual(row, panel.summary.getWidgetPosition(panel.fields['kind'])[0] + 1)
        self.assertNotIn('parts', panel.changes())


if __name__ == '__main__':
    unittest.main()
