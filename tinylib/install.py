"""Install-level pointer to the studio security file.

``tinylib.ini`` sits in the application folder, beside ``menu.py`` and
``worker.py``. Only administrators should be able to write that folder. The ini
names the ``security.json`` that holds users, groups, libraries and grants, and
optionally the default browser configuration::

    [tinylib]
    security = //studio/pipeline/tinylib/security.json
    config   = //studio/pipeline/tinylib/studio.json

    [tools]
    ffmpeg   = C:/tools/ffmpeg/ffmpeg.exe
    oiiotool = oiiotool

``[tools]`` names the executables TinyLib runs (ffmpeg, ffprobe, ffplay, oiiotool,
vfx_transcode) and the OCIO config (``ocio``). They override any ``tools`` in a
browser configuration, and the farm worker uses them too, so only administrators
decide which programs run.

The security location is deliberately read only from this file. Environment
variables, command-line options, ``*.local.json`` overrides and the
``security_config`` key cannot redirect it, and values are not expanded.
"""
import configparser
import re
import sys
from pathlib import Path


def app_root():
    """The TinyLib install folder: the repository, or the folder of a frozen TinyLib.exe.

    A frozen exe unpacks its modules into a private folder, so tinylib.ini, config\\
    and actions\\ are looked up beside the exe instead.
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = app_root()
INSTALL_FILENAME = 'tinylib.ini'


def install_path():
    return APP_ROOT / INSTALL_FILENAME


def read_install(path=None):
    """Return ``{'path', 'security', 'config', 'error'}``; never raises.

    ``error`` is a user-facing message when the install is incomplete. In that
    case ``security`` is None and callers must deny access.
    """
    path = Path(path) if path else install_path()
    result = {'path': str(path), 'security': None, 'config': None, 'tools': {}, 'error': None}
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
    if parser.has_section('tools'):
        result['tools'] = {name: tool_path(path.parent, value)
                           for name, value in parser['tools'].items() if value.strip().strip('"')}
    if not result['security']:
        result['error'] = '%s does not set "security" to the studio security JSON.' % path
    return result


def tool_path(base, value):
    """A bare name ("ffmpeg") is found on PATH; a relative path resolves beside tinylib.ini.

    OCIO built-in config URIs (``ocio://studio-config-latest``) are kept as written.
    """
    value = value.strip().strip('"')
    if value.startswith('ocio://'):
        return value
    if '/' not in value and '\\' not in value:
        return value
    if Path(value).is_absolute() or value.startswith(('//', '\\\\')) or re.match(r'^[A-Za-z]:[\\/]', value):
        return value
    return str((Path(base) / value.replace('\\', '/')).resolve())
