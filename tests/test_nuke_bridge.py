"""Nuke imports: a color space the project lacks never stops the import."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tinylib import nuke_bridge


class Knob:
    def __init__(self, value=None, values=()):
        self.v, self.options = value, list(values)

    def setValue(self, value):
        self.v = value

    def value(self):
        return self.v

    def values(self):
        return self.options


def fake_nuke(menu):
    created, deleted = [], []

    def read():
        node = {name: Knob() for name in ('file', 'first', 'origfirst', 'last', 'origlast', 'label')}
        node['colorspace'] = Knob('default', menu)
        created.append(node)
        return node
    module = types.ModuleType('nuke')
    module.nodes = types.SimpleNamespace(Read=read)
    module.delete = deleted.append
    return module, created, deleted


class ColorspaceTests(unittest.TestCase):
    def test_matching(self):
        menu = ['default', 'Roles/scene_linear', 'ACES/ACES - ACEScg', 'Utility/sRGB - Texture']
        self.assertEqual(nuke_bridge.match_colorspace('ACEScg', menu), 'ACES/ACES - ACEScg')
        self.assertEqual(nuke_bridge.match_colorspace('srgb - texture', menu), 'Utility/sRGB - Texture')
        self.assertEqual(nuke_bridge.match_colorspace('ACEScg', ['ACEScg', 'ACES - ACEScg']), 'ACEScg')
        self.assertIsNone(nuke_bridge.match_colorspace('Linear Rec.709', menu))


class ImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        for frame in (1001, 1002):
            (folder / ('fire.%04d.exr' % frame)).write_bytes(b'')
        self.asset = {'name': 'fire', 'main': str(folder / 'fire.####.exr'), 'first': 1001, 'last': 1002,
                      'colorspace': 'ACEScg'}

    def test_missing_colorspace_keeps_the_node_and_warns(self):
        nuke, created, deleted = fake_nuke(['default', 'sRGB', 'linear'])
        with patch.dict(sys.modules, {'nuke': nuke}):
            message = nuke_bridge.import_assets([self.asset, dict(self.asset, name='smoke')])
        self.assertEqual((len(created), deleted), (2, []))
        self.assertEqual(created[0]['colorspace'].value(), 'default')
        self.assertEqual((created[0]['first'].value(), created[0]['last'].value()), (1001, 1002))
        self.assertIn('Created 2 Read nodes.', message)
        self.assertIn("fire: color space 'ACEScg' is not in this Nuke project", message)
        self.assertIn('smoke:', message)

    def test_known_colorspace_is_set_without_a_warning(self):
        nuke, created, _ = fake_nuke(['default', 'ACES - ACEScg'])
        with patch.dict(sys.modules, {'nuke': nuke}):
            self.assertEqual(nuke_bridge.import_assets([self.asset]), 'Created 1 Read node.')
        self.assertEqual(created[0]['colorspace'].value(), 'ACES - ACEScg')

    def test_missing_media_still_stops(self):
        nuke, created, _ = fake_nuke(['default'])
        with patch.dict(sys.modules, {'nuke': nuke}):
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                nuke_bridge.import_assets([dict(self.asset, main='/none/x.####.exr')])
        self.assertEqual(created, [])


if __name__ == '__main__':
    unittest.main()
