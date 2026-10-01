"""Studio configuration shared by browser and farm workers."""
import os
from pathlib import Path
from .library import read_json
from .asset_types import SCHEMA_VERSION, extension_groups


def load_settings(path=None):
    path = Path(path or os.environ.get('TINYLIB_CONFIG') or
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
    data['asset_type_schema_version'] = SCHEMA_VERSION
    data['extension_groups'] = extension_groups(data)
    for library in data.get('libraries', []):
        root = Path(library['root'])
        if not root.is_absolute():
            library['root'] = str((path.parent / root).resolve())
    for key, value in data.get('tools', {}).items():
        if value.startswith(('./', '../')):
            data['tools'][key] = str((path.parent / value).resolve())
    action_roots = data.get('action_roots', [str(Path(__file__).resolve().parent.parent / 'actions')])
    data['action_roots'] = [str((path.parent / root).resolve()) if not Path(root).is_absolute() else root
                            for root in action_roots]
    security = Path(data['security_config']) if data.get('security_config') else path.resolve()
    if not security.is_absolute():
        security = path.parent / security
    security_local = security.with_name(security.stem + '.local' + security.suffix)
    if security_local.is_file():
        security = security_local
    data['_security_path'] = str(security.resolve())
    data['_config_path'] = str(path.resolve())
    return data
