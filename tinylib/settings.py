"""Studio configuration shared by browser and farm workers."""
import os
from pathlib import Path
from .library import read_json
from .asset_types import SCHEMA_VERSION, extension_groups
from .install import APP_ROOT, read_install
from .log import get_logger

log = get_logger(__name__)

# "{app}/actions" or "{app}/executables/ffmpeg.exe": relative to the TinyLib install folder.
APP_TOKEN = '{app}'


def app_path(value):
    rest = value[len(APP_TOKEN):].replace('\\', '/').lstrip('/')
    return str((APP_ROOT / rest).resolve()) if rest else str(APP_ROOT)


class SettingsError(ValueError):
    pass


def validate_settings(data, path):
    """Reject configuration mistakes with a message naming the file and key."""
    def fail(message):
        raise SettingsError('%s: %s' % (path, message))
    if not isinstance(data, dict):
        fail('the top level must be a JSON object.')
    libraries = data.get('libraries', [])
    if not isinstance(libraries, list):
        fail('"libraries" must be a list.')
    for index, library in enumerate(libraries):
        if not isinstance(library, dict):
            fail('libraries[%d] must be an object.' % index)
        for key in ('name', 'root'):
            if not isinstance(library.get(key), str) or not library[key].strip():
                fail('libraries[%d] needs a non-empty "%s".' % (index, key))
    for key in ('tools', 'profiles', 'deadline'):
        if not isinstance(data.get(key, {}), dict):
            fail('"%s" must be an object.' % key)
    web = data.get('deadline', {}).get('web_service', {})
    if not isinstance(web, dict):
        fail('"deadline.web_service" must be an object.')
    for key in ('require_authentication', 'verify_ssl'):
        if key in web and not isinstance(web[key], bool):
            fail('deadline.web_service.%s must be true or false.' % key)
    for name, value in list(data.get('tools', {}).items()):
        if value is None:
            del data['tools'][name]  # null means "use the default on PATH".
        elif not isinstance(value, str):
            fail('tools.%s must be a path string.' % name)
    roots = data.get('action_roots', [])
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        fail('"action_roots" must be a list of folder paths.')


def load_settings(path=None, install=None):
    """Load the browser configuration; the security file always comes from tinylib.ini.

    ``install`` is a tinylib.ini path or a ``read_install()`` result (tests use it).
    """
    install = install if isinstance(install, dict) else read_install(install)
    path = Path(path or os.environ.get('TINYLIB_CONFIG') or install.get('config') or
                Path(__file__).resolve().parent.parent / 'config' / 'studio.json')
    local = path.with_name(path.stem + '.local' + path.suffix)
    if local.is_file():
        path = local
    data = read_json(path)

    def expand(value):
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return os.path.expandvars(value) if isinstance(value, str) else value

    data = expand(data)
    validate_settings(data, path)
    data['asset_type_schema_version'] = SCHEMA_VERSION
    data['extension_groups'] = extension_groups(data)
    for library in data.get('libraries', []):
        root = Path(library['root'])
        if not root.is_absolute():
            library['root'] = str((path.parent / root).resolve())
    for key, value in data.get('tools', {}).items():
        if isinstance(value, str) and value.startswith(APP_TOKEN):
            data['tools'][key] = app_path(value)
        elif isinstance(value, str) and value.startswith(('./', '../', '.\\', '..\\')):
            data['tools'][key] = str((path.parent / value.replace('\\', '/')).resolve())
    # tinylib.ini [tools] wins: the admin-owned install decides which programs run.
    data['tools'] = dict(data.get('tools', {}), **install.get('tools', {}))
    action_roots = data.get('action_roots', [APP_TOKEN + '/actions'])
    data['action_roots'] = [app_path(root) if root.startswith(APP_TOKEN) else
                            root if Path(root).is_absolute() else str((path.parent / root).resolve())
                            for root in action_roots]
    # Neither the browser configuration nor its .local override may choose the security file.
    data.pop('security_config', None)
    data['_install_path'] = install['path']
    data['_security_path'] = install['security']
    data['_security_error'] = install['error']
    data['_config_path'] = str(path.resolve())
    log.info('Configuration %s; security file %s', data['_config_path'], install['security'] or install['error'])
    return data
