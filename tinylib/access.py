"""OS account identity and group grants stored in the studio security file.

The security file location comes only from tinylib.ini (see install.py). Its
filesystem permissions are the real boundary: artists read it, administrators
write it. TinyLib never writes it to create a first administrator.
"""
import copy
import os
from pathlib import Path
from .library import atomic_json, library_lock, read_json
from .log import get_logger

log = get_logger(__name__)

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
    return pwd.getpwuid(os.getuid()).pw_name.casefold()


def root_key(root):
    return os.path.normcase(os.path.abspath(str(root))).replace('\\', '/').rstrip('/')


def default_permissions():
    return {'view': ['users', 'managers', 'admins'], 'ingest': ['admins'],
            'edit': ['managers', 'admins'], 'actions': ['users', 'managers', 'admins'],
            'copy': ['users', 'managers', 'admins'],
            'version_fields': ['admins'], 'ingest_fields': ['managers', 'admins'],
            'acquire_fields': ['managers', 'admins']}


CAPABILITIES = tuple(default_permissions())


def _resolved_root(base, value):
    text = os.path.expandvars(str(value))
    path = Path(text)
    return path if path.is_absolute() or text.startswith(('//', '\\\\')) else Path(base) / path


class AccessControl:
    """Group grants read from the studio security file named by tinylib.ini.

    ``persist=True`` (every production entry point) rereads that file on each
    refresh. A missing install, an unreadable or invalid file, or an empty user
    table fails closed: ``error`` explains why and nothing is granted. Nothing
    is written to create the first administrator; use
    ``python -m tinylib.admin add-user``.

    ``persist=False`` is an in-memory test mode built from ``settings``. When
    that copy has no users, ``identity`` is seeded as an administrator in
    memory only.
    """

    def __init__(self, settings, identity=None, persist=True):
        self.identity = identity or windows_identity()
        # Who is really signed in. ``identity`` differs only while an admin is logged in as another user.
        self.real_identity = self.identity
        self.persist = persist
        self.error = None
        self.groups = set()
        if persist and settings.get('_security_error'):
            location = None
        else:
            location = settings.get('_security_path') or settings.get('_config_path')
        self.path = Path(location) if location else None
        self._install_error = settings.get('_security_error') or 'No studio security file is configured.'
        self._memory = copy.deepcopy(settings)
        if not persist:
            access = self._memory.setdefault('access', {})
            if not access.get('users'):
                access['groups'] = list(access.get('groups') or DEFAULT_GROUPS)
                access['users'] = {self.identity: list(DEFAULT_GROUPS)}
                access.setdefault('defaults', {})
        self.refresh()

    def _deny(self, message):
        if message != getattr(self, '_logged_error', None):
            log.warning('Access denied for %s: %s', self.identity, message)
            self._logged_error = message
        self.error = message
        self.groups = set()
        self.data = {'access': {'groups': list(DEFAULT_GROUPS), 'users': {}, 'defaults': {}}, 'libraries': []}

    def refresh(self):
        self.groups = set()
        self.error = None
        if not self.persist:
            data = copy.deepcopy(self._memory)
        elif self.path is None:
            return self._deny(self._install_error)
        else:
            try:
                data = read_json(self.path)
            except FileNotFoundError:
                return self._deny('Studio security file not found: %s' % self.path)
            except (OSError, ValueError) as error:
                return self._deny('Cannot read studio security file %s: %s' % (self.path, error))
        problem = validate_security(data)
        if problem:
            return self._deny('Invalid studio security file %s: %s' % (self.path, problem))
        users = {key.casefold(): value for key, value in data.get('access', {}).get('users', {}).items()}
        if not users:
            return self._deny('No users are configured in %s. An administrator must add the first account '
                              'with: python -m tinylib.admin add-user' % self.path)
        self.data = data
        self.groups = set(users.get(self.identity.casefold(), []))
        if not self.groups:
            self.error = 'Your account %s has no TinyLib access. Ask an administrator to add it.' % self.identity

    @property
    def acting_as(self):
        """True while an admin is logged in as another user (see ``login_as``)."""
        return self.identity.casefold() != self.real_identity.casefold()

    @property
    def actor(self):
        """The identity for records and logs: "studio\\admin as studio\\jan" while logged in as someone else."""
        return '%s as %s' % (self.real_identity, self.identity) if self.acting_as else self.identity

    def users(self):
        """The accounts listed in the security file."""
        return sorted(self.data.get('access', {}).get('users', {}))

    def login_as(self, identity):
        """Let an admin use TinyLib with another user's groups and permissions, until ``logout``.

        The real account must be an admin in the security file right now, and ``identity`` a listed user.
        """
        target = identity.strip().casefold()
        current = self.identity
        self.identity = self.real_identity
        self.refresh()
        if not self.is_admin:
            raise PermissionError('Only administrators can log in as another user.')
        if target not in {user.casefold() for user in self.users()}:
            self.identity = current
            self.refresh()
            raise ValueError('%s is not a TinyLib user.' % identity)
        self.identity = target
        log.warning('%s logged in as %s', self.real_identity, target)
        self.refresh()

    def logout(self):
        """Back to the real account after ``login_as``."""
        if self.acting_as:
            log.warning('%s logged out from %s', self.real_identity, self.identity)
        self.identity = self.real_identity
        self.refresh()

    @property
    def security_dir(self):
        return self.path.parent if self.path else Path('.')

    @property
    def is_admin(self):
        return 'admins' in self.groups

    def library(self, root):
        """The security file's entry for ``root``, or None when it is not registered."""
        for library in self.data.get('libraries', []):
            if root_key(_resolved_root(self.security_dir, library['root'])) == root_key(root):
                return library
        return None

    def policy(self, root):
        library = self.library(root)
        return library.get('permissions', {}) if library else {}

    def read_only(self, root):
        """read_only in the security file wins over any browser configuration."""
        library = self.library(root)
        return bool(library and library.get('read_only'))

    def defaults(self):
        return dict(default_permissions(), **self.data.get('access', {}).get('defaults', {}))

    def field_allowed(self, root, key):
        from .asset_fields import field_capability
        capability = field_capability(key)
        return capability is None or self.can(root, capability)

    def can(self, root, capability, action=None):
        capability = 'actions' if capability == 'action' else capability
        if capability not in CAPABILITIES:
            raise ValueError('Unknown library capability.')
        if self.is_admin:
            return True
        # Libraries missing from the security file never receive grants.
        if self.library(root) is None:
            return False
        policy = self.policy(root)
        grants = policy.get(capability, self.defaults().get(capability, []))
        # Old per-action mappings no longer grant individual actions.
        if capability == 'actions' and isinstance(grants, dict):
            grants = self.defaults()['actions']
        if not isinstance(grants, list):
            return False
        return bool(self.groups.intersection(grants))

    def can_copy(self, assets):
        """May these assets' paths and pictures leave TinyLib (drag, Ctrl+C, Copy)? Needs the
        ``copy`` capability on every library involved."""
        roots = {asset.get('library_root', '') for asset in assets}
        return all(self.can(root, 'view') and self.can(root, 'copy') for root in roots)

    def require(self, root, capability, action=None):
        if not self.can(root, 'view') or not self.can(root, capability, action):
            raise PermissionError(self.error or 'Your groups do not permit %s%s for this library.' %
                                  (capability, ' ' + action if action else ''))

    def save(self, users, groups, libraries, defaults=None):
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
            policies = [defaults if defaults is not None else self.defaults()]
            for library in libraries:
                policy = library.get('permissions', {})
                policies.append(policy)
            for policy in policies:
                grants = list(policy.values())
                if any(not isinstance(grant, list) or not set(grant).issubset(groups_set) for grant in grants):
                    raise ValueError('Library permissions reference unknown groups.')
            self.data['access'] = {'groups': sorted(groups_set),
                                   'users': {key.casefold(): value for key, value in users.items()},
                                   'defaults': copy.deepcopy(defaults if defaults is not None else self.defaults())}
            self.data['libraries'] = copy.deepcopy(libraries)
            if self.persist:
                atomic_json(self.path, self.data)
            else:
                self._memory = copy.deepcopy(self.data)
            self.refresh()
        if not self.persist:
            return update()
        if self.path is None:
            raise PermissionError(self._install_error)
        try:
            with library_lock(self.path.parent):
                update()
        except PermissionError as error:
            if error.errno is None:  # Raised by TinyLib's own checks, not by Windows.
                raise
            raise PermissionError(security_write_denied(self.path)) from error


def copy_denied_libraries(assets, access):
    """The names of the libraries whose assets may not be copied, for messages."""
    names = sorted({asset.get('library') or asset.get('library_root', '') for asset in assets
                    if not access.can_copy([asset])})
    return ', '.join(names) if names else 'these libraries'


def security_write_denied(path):
    return ('Windows denied writing %s. Only accounts with write permission on the security file '
            'can change access rights.' % path)


def validate_security(data):
    """Return a problem description, or None when the security document is usable."""
    if not isinstance(data, dict):
        return 'the top level must be a JSON object.'
    access = data.get('access', {})
    if not isinstance(access, dict) or not isinstance(access.get('users', {}), dict):
        return '"access.users" must be an object.'
    if any(not isinstance(value, list) or not all(isinstance(group, str) for group in value)
           for value in access.get('users', {}).values()):
        return 'user group assignments must be lists of group names.'
    libraries = data.get('libraries', [])
    if not isinstance(libraries, list) or any(not isinstance(item, dict) or not item.get('root')
                                              for item in libraries):
        return '"libraries" must be a list of objects that each have a root.'
    return None
