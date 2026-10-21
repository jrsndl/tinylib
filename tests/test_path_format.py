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
            'houdini': ['C:/some/folder/files.$F4.exr'],
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
        self.assertEqual(format_asset(asset, 'houdini'), ['/show/test.$F6.exr'])
        unpadded = {'main': '/show/test.#.exr', 'first': 1, 'last': 9, 'kind': 'footage'}
        self.assertEqual(format_asset(unpadded, 'houdini'), ['/show/test.$F.exr'])

    def test_in_and_out_points_narrow_ranged_notations(self):
        clip = {'main': '/show/clip.####.exr', 'first': 1001, 'last': 1100, 'kind': 'footage'}
        self.assertEqual(format_asset(clip, 'nuke', (1010, 1012)), ['/show/clip.####.exr 1010-1012'])
        self.assertEqual(format_asset(clip, 'flame', (1010, 1012)), ['/show/clip.[1010-1012].exr'])
        self.assertEqual(format_asset(clip, 'ayon', (1010, 1011)),
                         ['/show/clip.1010.exr', '/show/clip.1011.exr'])
        self.assertEqual(format_asset(clip, 'hashtag', (1010, 1012)), ['/show/clip.####.exr'])
        # Points beyond the clip are clipped to it; points that miss it entirely are ignored.
        self.assertEqual(format_asset(clip, 'nuke', (990, 1005)), ['/show/clip.####.exr 1001-1005'])
        self.assertEqual(format_asset(clip, 'nuke', (2000, 2010)), ['/show/clip.####.exr 1001-1100'])
        other = dict(clip, main='/show/other.####.exr')
        trims = {clip['main']: (1050, 1060)}
        self.assertEqual(format_assets([clip, other], 'nuke', lambda a: trims.get(a['main'])),
                         '/show/clip.####.exr 1050-1060\n/show/other.####.exr 1001-1100')

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
