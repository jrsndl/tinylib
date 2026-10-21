import importlib.util
import unittest

from tinylib.access import AccessControl
from tinylib.settings import load_settings

# Qt tests are skipped, not errored, where neither PySide binding is installed.
HAVE_QT = any(importlib.util.find_spec(name) is not None for name in ('PySide6', 'PySide2'))
requires_qt = unittest.skipUnless(HAVE_QT, 'PySide6 or PySide2 is not installed')


def make_test_access(config):
    settings = load_settings(config)
    settings.pop('access', None)
    return AccessControl(settings, identity='test\\admin', persist=False)


# Older Qt scripts import this name; it is not a unittest test.
test_access = make_test_access
test_access.__test__ = False
