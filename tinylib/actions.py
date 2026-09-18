"""Trusted studio action discovery and permission-checked dispatch."""
import copy
import importlib.util
import re
import sys
import uuid
from pathlib import Path
from .library import read_json, split_sequence


def host_name():
    if 'nuke' in sys.modules:
        return 'nuke'
    if 'hou' in sys.modules:
        return 'houdini'
    if 'maya.cmds' in sys.modules:
        return 'maya'
    return 'standalone'


def local_file(folder, value):
    path = (folder / value).resolve()
    if folder.resolve() not in path.parents:
        raise ValueError('Action paths must stay inside the action folder.')
    return path


class Action:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.manifest = read_json(self.folder / 'manifest.json')
        for key in ('id', 'name', 'version', 'category'):
            if not isinstance(self.manifest.get(key), str) or not self.manifest[key].strip():
                raise ValueError('Action manifest requires ' + key)
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+', self.manifest['id']):
            raise ValueError('Invalid action id.')
        self.id = self.manifest['id']
        self.code = local_file(self.folder, self.manifest.get('entrypoint', 'action.py'))
        if self.code.suffix != '.py' or not self.code.is_file():
            raise ValueError('Action needs a Python entrypoint.')
        self.config_path = local_file(self.folder, self.manifest.get('config', 'config.json'))
        if not self.config_path.is_file():
            raise ValueError('Action needs a config JSON file.')
        icon = self.manifest.get('icon')
        self.icon = local_file(self.folder, icon) if icon else None
        if self.icon and (self.icon.suffix.lower() != '.png' or not self.icon.is_file()):
            raise ValueError('Action icon must be an existing PNG.')
        rules = self.manifest.get('asset_filter', {})
        hosts = self.manifest.get('hosts', [])
        if not isinstance(hosts, list) or not all(isinstance(host, str) for host in hosts):
            raise ValueError('hosts must be a list of names.')
        if not isinstance(rules, dict):
            raise ValueError('asset_filter must be an object.')
        for field in ('min_selection', 'max_selection'):
            if field in rules and (type(rules[field]) is not int or rules[field] < 1):
                raise ValueError(field + ' must be a positive integer.')
        for field in ('kinds', 'extensions', 'requires'):
            if field in rules and (not isinstance(rules[field], list) or not all(isinstance(v, str) for v in rules[field])):
                raise ValueError('Asset filter ' + field + ' must be a list of strings.')

    def accepts(self, assets, host=None):
        host = host or host_name()
        hosts = self.manifest.get('hosts', [])
        if hosts and host not in hosts:
            return False
        rules = self.manifest.get('asset_filter', {})
        if len(assets) < int(rules.get('min_selection', 1)):
            return False
        if rules.get('max_selection') and len(assets) > int(rules['max_selection']):
            return False
        for asset in assets:
            if asset.get('_unavailable'):
                return False
            if rules.get('kinds') and asset.get('kind') not in rules['kinds']:
                return False
            if rules.get('extensions') and Path(split_sequence(asset.get('main', ''))[0]).suffix.lower() not in [ext.lower() for ext in rules['extensions']]:
                return False
            if any(not asset.get(key) for key in rules.get('requires', [])):
                return False
        return True


class ActionRegistry:
    def __init__(self, roots):
        self.roots = roots
        self.actions = {}
        self.errors = []
        self.reload()

    def reload(self):
        self.actions, self.errors = {}, []
        duplicates = set()
        for root in self.roots:
            path = Path(root)
            if not path.is_dir():
                self.errors.append('Action root is unavailable: ' + str(path))
                continue
            for manifest in sorted(path.glob('*/manifest.json')):
                try:
                    action = Action(manifest.parent)
                    if action.id in self.actions or action.id in duplicates:
                        self.actions.pop(action.id, None)
                        duplicates.add(action.id)
                        raise ValueError('Duplicate action id: ' + action.id)
                    self.actions[action.id] = action
                except Exception as error:
                    self.errors.append(str(manifest) + ': ' + str(error))

    def allowed(self, action, assets, access, host=None):
        return bool(assets) and action.accepts(assets, host) and all(
            access.can(asset['library_root'], 'view') and access.can(asset['library_root'], 'action', action.id)
            for asset in assets)

    def run(self, identifier, assets, access, context=None):
        access.refresh()
        if identifier not in self.actions:
            raise ValueError('Unknown action: ' + identifier)
        # Revalidate the manifest at execution time, without running Python during discovery.
        action = Action(self.actions[identifier].folder)
        if action.id != identifier or not self.allowed(action, assets, access):
            raise PermissionError('Action is not permitted for this selection.')
        config = read_json(action.config_path)
        module_name = '_tinylib_action_' + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(module_name, action.code)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            callback = getattr(module, action.manifest.get('callable', 'run'))
            return callback(copy.deepcopy(assets), dict(context or {}, identity=access.identity), config)
        finally:
            sys.modules.pop(module_name, None)
