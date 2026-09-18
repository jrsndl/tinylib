"""OS account identity and group grants stored in the studio configuration."""
import copy
import os
from pathlib import Path
from .library import atomic_json, library_lock, read_json

DEFAULT_GROUPS = ['admins', 'managers', 'users', 'restricted']


def windows_identity():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        size = wintypes.ULONG(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        # NameSamCompatible includes the domain/computer; never trust USERNAME env overrides.
        if ctypes.windll.secur32.GetUserNameExW(2, buffer, ctypes.byref(size)):
            return buffer.value.casefold()
        size = wintypes.DWORD(1024)
        if ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
            return buffer.value.casefold()
        raise RuntimeError('Windows could not identify the current account.')
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def root_key(root):
    return os.path.normcase(os.path.abspath(str(root))).replace('\\', '/').rstrip('/')


def default_permissions():
    return {'view': ['managers', 'users'], 'ingest': ['managers'],
            'actions': {action: ['managers', 'users'] for action in ('nuke.read', 'nuke.highres', 'clipboard.paths')}}


class AccessControl:
    def __init__(self, settings, identity=None, persist=True):
        self.identity = identity or windows_identity()
        self.persist = persist
        self.path = Path(settings.get('_security_path') or settings['_config_path'])
        self._memory = copy.deepcopy(settings)
        self.refresh()
        if not self.data.get('access', {}).get('users'):
            if persist:
                with library_lock(self.path.parent):
                    self.data = read_json(self.path)
                    if not self.data.get('access', {}).get('users'):
                        self._bootstrap()
                        atomic_json(self.path, self.data)
            else:
                self._bootstrap()
                self._memory = copy.deepcopy(self.data)
        self.refresh()

    def _bootstrap(self):
        self.data['access'] = {'groups': list(DEFAULT_GROUPS), 'users': {self.identity: list(DEFAULT_GROUPS)}}
        for library in self.data.get('libraries', []):
            library.setdefault('permissions', default_permissions())

    def refresh(self):
        self.groups = set()
        self.data = read_json(self.path) if self.persist else copy.deepcopy(self._memory)
        access = self.data.get('access', {})
        if not isinstance(access, dict) or not isinstance(access.get('users', {}), dict):
            raise ValueError('Invalid studio access configuration.')
        users = {key.casefold(): value for key, value in access.get('users', {}).items()}
        if any(not isinstance(value, list) or not all(isinstance(group, str) for group in value) for value in users.values()):
            raise ValueError('User group assignments must be lists of group names.')
        self.groups = set(users.get(self.identity.casefold(), []))

    @property
    def is_admin(self):
        return 'admins' in self.groups

    def policy(self, root):
        for library in self.data.get('libraries', []):
            path = Path(os.path.expandvars(library['root']))
            if not path.is_absolute():
                path = self.path.parent / path
            if root_key(path) == root_key(root):
                return library.get('permissions', {})
        return {}

    def can(self, root, capability, action=None):
        if capability not in ('view', 'ingest', 'action'):
            raise ValueError('Unknown library capability.')
        if self.is_admin:
            return True
        policy = self.policy(root)
        if capability == 'action':
            grants = policy.get('actions', {}).get(action, [])
        else:
            grants = policy.get(capability, [])
        if not isinstance(grants, list):
            return False
        return bool(self.groups.intersection(grants))

    def require(self, root, capability, action=None):
        if not self.can(root, 'view') or not self.can(root, capability, action):
            raise PermissionError('Your groups do not permit %s%s for this library.' %
                                  (capability, ' ' + action if action else ''))

    def save(self, users, groups, libraries):
        def update():
            self.refresh()
            if not self.is_admin:
                raise PermissionError('Only admins can change studio permissions.')
            groups_set = set(groups)
            if not set(DEFAULT_GROUPS).issubset(groups_set):
                raise ValueError('The four default groups must be retained.')
            for user, memberships in users.items():
                if not user.strip() or not memberships or not set(memberships).issubset(groups_set):
                    raise ValueError('Each user needs an identity and one or more valid groups.')
            if not any('admins' in memberships for memberships in users.values()):
                raise ValueError('At least one administrator must remain.')
            for library in libraries:
                policy = library.get('permissions', {})
                grants = [policy.get('view', []), policy.get('ingest', [])] + list(policy.get('actions', {}).values())
                if any(not isinstance(grant, list) or not set(grant).issubset(groups_set) for grant in grants):
                    raise ValueError('Library permissions reference unknown groups.')
            self.data['access'] = {'groups': sorted(groups_set),
                                   'users': {key.casefold(): value for key, value in users.items()}}
            self.data['libraries'] = copy.deepcopy(libraries)
            if self.persist:
                atomic_json(self.path, self.data)
            else:
                self._memory = copy.deepcopy(self.data)
            self.refresh()
        if self.persist:
            with library_lock(self.path.parent):
                update()
        else:
            update()
