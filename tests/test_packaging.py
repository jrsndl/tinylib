import re
import unittest
from pathlib import Path

import tinylib

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_version_matches_pyproject(self):
        text = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
        version = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)
        self.assertEqual(version, tinylib.__version__)

    def test_changelog_mentions_current_version(self):
        self.assertIn('## ' + tinylib.__version__, (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
