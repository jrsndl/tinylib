from tinylib.access import AccessControl
from tinylib.settings import load_settings


def test_access(config):
    settings = load_settings(config)
    settings.pop('access', None)
    return AccessControl(settings, identity='test\\admin', persist=False)
