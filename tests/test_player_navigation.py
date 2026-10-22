"""PgUp/PgDn in the player: the previous or next asset of the list it was opened from."""
import unittest

from tinylib.preferences import adjacent_asset


def asset(name):
    return {'library_root': 'L:/lib', 'main': 'L:/lib/%s/main/%s.exr' % (name, name), 'name': name}


class AdjacentAssetTests(unittest.TestCase):
    assets = [asset('a'), asset('b'), asset('c')]

    def name(self, current, step):
        return adjacent_asset(self.assets, current, step)['name']

    def test_steps_and_wraps_at_both_ends(self):
        self.assertEqual(self.name(asset('a'), 1), 'b')
        self.assertEqual(self.name(asset('b'), -1), 'a')
        self.assertEqual(self.name(asset('c'), 1), 'a')   # PgDn on the last: the first
        self.assertEqual(self.name(asset('a'), -1), 'c')  # PgUp on the first: the last

    def test_matches_by_identity_not_by_dict(self):
        # The player's copy carries extra keys (in/out points, play source).
        self.assertEqual(self.name(dict(asset('b'), _trim=(1, 2), _play_source='main'), 1), 'c')

    def test_an_asset_no_longer_listed_starts_at_the_matching_end(self):
        self.assertEqual(self.name(asset('gone'), 1), 'a')
        self.assertEqual(self.name(asset('gone'), -1), 'c')

    def test_single_and_empty_lists(self):
        self.assertEqual(adjacent_asset([asset('a')], asset('a'), 1)['name'], 'a')
        self.assertIsNone(adjacent_asset([], asset('a'), 1))


if __name__ == '__main__':
    unittest.main()
