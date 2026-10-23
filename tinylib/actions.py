"""Trusted studio action discovery and permission-checked dispatch."""
import copy
import importlib.util
import re
import sys
import uuid
from pathlib import Path
from .access import CAPABILITIES
from .library import read_json, split_sequence
from .log import get_logger

log = get_logger(__name__)


def host_name():
    if 'nuke' in sys.modules:
        return 'nuke'
    if 'hou' in sys.modules:
        return 'houdini'
    if 'maya.cmds' in sys.modules:
        return 'maya'
    return 'standalone'


MENU_PLACES = ('main', 'collection', 'player')


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
        self.allow_only = self.manifest.get('allow_only')
        if self.allow_only is not None and (not isinstance(self.allow_only, list) or
                not all(isinstance(group, str) and group.strip() for group in self.allow_only)):
            raise ValueError('allow_only must be a list of group names.')
        callable_name = self.manifest.get('callable', 'run')
        if not isinstance(callable_name, str) or not callable_name.isidentifier():
            raise ValueError('callable must be a Python function name.')
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
        if not isinstance(self.manifest.get('settings', False), bool):
            raise ValueError('settings must be true or false.')
        tools = self.manifest.get('tools', [])
        if not isinstance(tools, list) or not all(isinstance(tool, str) and tool.strip() for tool in tools):
            raise ValueError('tools must be a list of [tools] names from tinylib.ini.')
        menu = self.manifest.get('context_menu', False)
        if isinstance(menu, dict):
            if 'label' in menu and (not isinstance(menu['label'], str) or not menu['label'].strip()):
                raise ValueError('context_menu.label must be text.')
            places = menu.get('where', list(MENU_PLACES))
            if not isinstance(places, list) or not places or any(place not in MENU_PLACES for place in places):
                raise ValueError('context_menu.where must list some of: ' + ', '.join(MENU_PLACES))
        elif not isinstance(menu, bool):
            raise ValueError('context_menu must be true, false or an object.')
        rules = self.manifest.get('asset_filter', {})
        hosts = self.manifest.get('hosts', [])
        if not isinstance(hosts, list) or not all(isinstance(host, str) for host in hosts):
            raise ValueError('hosts must be a list of names.')
        if not isinstance(rules, dict):
            raise ValueError('asset_filter must be an object.')
        for field in ('min_selection', 'max_selection'):
            if field in rules and (type(rules[field]) is not int or rules[field] < 1):
                raise ValueError(field + ' must be a positive integer.')
        permissions = self.manifest.get('permissions', [])
        if not isinstance(permissions, list) or any(item not in CAPABILITIES for item in permissions):
            raise ValueError('permissions must be a list of library capabilities: ' + ', '.join(CAPABILITIES))
        if 'whole_view' in rules and type(rules['whole_view']) is not bool:
            raise ValueError('Asset filter whole_view must be true or false.')
        for field in ('kinds', 'extensions', 'requires'):
            if field in rules and (not isinstance(rules[field], list) or not all(isinstance(v, str) for v in rules[field])):
                raise ValueError('Asset filter ' + field + ' must be a list of strings.')

    @property
    def tools(self):
        """External programs the action runs, by their name under [tools] in tinylib.ini."""
        return list(self.manifest.get('tools', []))

    @property
    def has_settings(self):
        """The action has its own settings window, offered at any time (no selection needed)."""
        return bool(self.manifest.get('settings', False))

    @property
    def settings_label(self):
        return self.manifest['name'] + ' settings…'

    def host_allowed(self, host=None):
        hosts = self.manifest.get('hosts', [])
        return not hosts or (host or host_name()) in hosts

    def in_menu(self, place):
        """Is the action in the right-click menu of ``place`` (main, collection or player)?"""
        menu = self.manifest.get('context_menu', False)
        if isinstance(menu, dict):
            return place in menu.get('where', MENU_PLACES)
        return bool(menu)

    @property
    def menu_label(self):
        menu = self.manifest.get('context_menu')
        return menu.get('label', self.manifest['name']) if isinstance(menu, dict) else self.manifest['name']

    @property
    def permissions(self):
        """Library capabilities the action needs besides ``actions``, e.g. ``["copy"]``."""
        return list(self.manifest.get('permissions', []))

    @property
    def whole_view(self):
        """False when the action needs a selection in the main view (it never runs on everything
        the main view shows). Collections still run it on the whole collection."""
        return self.manifest.get('asset_filter', {}).get('whole_view', True)

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
        # name -> bool: is this [tools] program found? The browser sets it; None skips the check.
        self.tool_available = None
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
        # Permissions are per library: check each library once, so a whole view stays fast.
        roots = {asset['library_root'] for asset in assets}
        if self.tool_available is not None and not all(self.tool_available(tool) for tool in action.tools):
            return False
        return (action.allow_only is None or bool(access.groups.intersection(action.allow_only))) and bool(assets) and action.accepts(assets, host) and all(
            access.can(root, 'view') and access.can(root, 'action', action.id)
            and all(access.can(root, capability) for capability in action.permissions) for root in roots)

    def configurable(self, action, access, host=None):
        """May the user open the action's settings? Its host, group and tool rules apply, but
        no assets are involved, so no library permission is needed."""
        if not action.has_settings or not access.groups or not action.host_allowed(host):
            return False
        if self.tool_available is not None and not all(self.tool_available(tool) for tool in action.tools):
            return False
        return action.allow_only is None or bool(access.groups.intersection(action.allow_only))

    def run(self, identifier, assets, access, context=None, settings=False):
        """Run an action on ``assets``; with ``settings`` open its settings instead: no assets,
        and the context's ``invoked`` is ``'settings'``."""
        access.refresh()
        if identifier not in self.actions:
            raise ValueError('Unknown action: ' + identifier)
        # Revalidate the manifest at execution time, without running Python during discovery.
        action = Action(self.actions[identifier].folder)
        if settings:
            if action.id != identifier or not self.configurable(action, access):
                raise PermissionError('You cannot open the settings of this action.')
            assets, context = [], dict(context or {}, invoked='settings')
        elif action.id != identifier or not self.allowed(action, assets, access):
            raise PermissionError('Action is not permitted for this selection.')
        config = read_json(action.config_path)
        log.info('%s runs action %s %s', getattr(access, 'actor', access.identity), action.id,
                 '(settings)' if settings else 'on %d asset(s)' % len(assets))
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
