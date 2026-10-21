"""Install-level pointer to the studio security file.

``tinylib.ini`` sits in the application folder, beside ``menu.py`` and
``worker.py``. Only administrators should be able to write that folder. The ini
names the ``security.json`` that holds users, groups, libraries and grants, and
optionally the default browser configuration::

    [tinylib]
    security = //studio/pipeline/tinylib/security.json
    config   = //studio/pipeline/tinylib/studio.json

The security location is deliberately read only from this file. Environment
variables, command-line options, ``*.local.json`` overrides and the
``security_config`` key cannot redirect it, and values are not expanded.
"""
import configparser
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
INSTALL_FILENAME = 'tinylib.ini'


def install_path():
    return APP_ROOT / INSTALL_FILENAME


def read_install(path=None):
    """Return ``{'path', 'security', 'config', 'error'}``; never raises.

    ``error`` is a user-facing message when the install is incomplete. In that
    case ``security`` is None and callers must deny access.
    """
    path = Path(path) if path else install_path()
    result = {'path': str(path), 'security': None, 'config': None, 'error': None}
    if not path.is_file():
        result['error'] = ('TinyLib is not configured: %s is missing. Ask an administrator to create it '
                           '(see docs/security.md).' % path)
        return result
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding='utf-8-sig') as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error) as error:
        result['error'] = 'Cannot read %s: %s' % (path, error)
        return result
    if not parser.has_section('tinylib'):
        result['error'] = '%s needs a [tinylib] section.' % path
        return result
    section = parser['tinylib']
    for key in ('security', 'config'):
        value = section.get(key, '').strip().strip('"')
        if value:
            location = Path(value)
            result[key] = str(location if location.is_absolute() or value.startswith(('//', '\\\\'))
                              else path.parent / location)
    if not result['security']:
        result['error'] = '%s does not set "security" to the studio security JSON.' % path
    return result
