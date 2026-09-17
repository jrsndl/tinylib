"""Studio configuration shared by browser and farm workers."""
import os
from pathlib import Path
from .library import read_json


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
    for library in data.get('libraries', []):
        root = Path(library['root'])
        if not root.is_absolute():
            library['root'] = str((path.parent / root).resolve())
    for key, value in data.get('tools', {}).items():
        if value.startswith(('./', '../')):
            data['tools'][key] = str((path.parent / value).resolve())
    data['_config_path'] = str(path.resolve())
    return data
