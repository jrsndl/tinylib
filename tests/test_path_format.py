import unittest
from tinylib.path_format import format_asset, format_assets


class PathFormatTests(unittest.TestCase):
    def setUp(self):
        self.sequence = {'main': r'C:\some\folder\files.####.exr', 'first': 1001, 'last': 1002, 'kind': 'footage'}

    def test_all_sequence_notations_use_forward_slashes(self):
        expected = {
            'nuke': ['C:/some/folder/files.####.exr 1001-1002'],
            'ayon': ['C:/some/folder/files.1001.exr', 'C:/some/folder/files.1002.exr'],
            'hashtag': ['C:/some/folder/files.####.exr'],
            'houdini': ['C:/some/folder/files.$F.exr'],
            'flame': ['C:/some/folder/files.[1001-1002].exr'],
            'printf': ['C:/some/folder/files.%04d.exr'],
            'folder': ['C:/some/folder'],
        }
        for notation, value in expected.items():
            self.assertEqual(format_asset(self.sequence, notation), value)

    def test_existing_printf_and_embedded_range_are_normalized(self):
        asset = {'main': '/show/test.%06d.exr 5-6', 'kind': 'footage'}
        self.assertEqual(format_asset(asset, 'hashtag'), ['/show/test.######.exr'])
        self.assertEqual(format_asset(asset, 'flame'), ['/show/test.[5-6].exr'])

    def test_stills_unknown_format_and_asset_joining(self):
        still = {'main': r'D:\images\one.exr', 'kind': 'still'}
        self.assertEqual(format_asset(still, 'nuke'), ['D:/images/one.exr'])
        self.assertEqual(format_asset(self.sequence, 'unknown'), format_asset(self.sequence, 'nuke'))
        self.assertEqual(format_assets([self.sequence, still], 'hashtag'),
                         'C:/some/folder/files.####.exr\nD:/images/one.exr')
        self.assertEqual(format_assets([self.sequence], 'ayon'),
                         'C:/some/folder/files.1001.exr, C:/some/folder/files.1002.exr')


if __name__ == '__main__':
    unittest.main()
