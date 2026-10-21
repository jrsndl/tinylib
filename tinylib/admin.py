"""Command-line administration of the studio security file named by tinylib.ini.

    python -m tinylib.admin whoami
    python -m tinylib.admin show
    python -m tinylib.admin add-user "STUDIO\\jane" admins
    python -m tinylib.admin remove-user "STUDIO\\joe"

Writes succeed only when Windows lets the current account write the security
file, so this works for whoever the filesystem trusts, including the very first
administrator of an empty file. Nothing else creates administrators.
"""
import argparse
import copy
import sys
from pathlib import Path

from .access import DEFAULT_GROUPS, default_permissions, security_write_denied, validate_security, windows_identity
from .install import read_install
from .library import atomic_json, library_lock, read_json


def _security_path(install):
    if install['error']:
        raise SystemExit(install['error'])
    return Path(install['security'])


def _load(path):
    if not path.exists():
        return {'access': {'groups': list(DEFAULT_GROUPS), 'users': {}, 'defaults': default_permissions()},
                'libraries': []}
    data = read_json(path)
    problem = validate_security(data)
    if problem:
        raise SystemExit('Invalid studio security file %s: %s' % (path, problem))
    access = data.setdefault('access', {})
    access.setdefault('groups', list(DEFAULT_GROUPS))
    access.setdefault('users', {})
    return data


def _change(path, operation):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with library_lock(path.parent):
            data = _load(path)
            before = copy.deepcopy(data)
            operation(data)
            if data != before:
                atomic_json(path, data)
    except PermissionError as error:
        if error.errno is None:
            raise
        raise SystemExit(security_write_denied(path)) from error


def add_user(path, identity, groups):
    identity = identity.strip().casefold()
    if not identity:
        raise SystemExit('Enter an account name such as STUDIO\\jane.')

    def operation(data):
        known = data['access']['groups']
        unknown = [group for group in groups if group not in known]
        if unknown:
            raise SystemExit('Unknown group(s): %s. Known groups: %s' % (', '.join(unknown), ', '.join(known)))
        users = {key.casefold(): value for key, value in data['access']['users'].items()}
        users[identity] = sorted(set(groups))
        data['access']['users'] = users
    _change(path, operation)


def remove_user(path, identity):
    identity = identity.strip().casefold()

    def operation(data):
        users = {key.casefold(): value for key, value in data['access']['users'].items()}
        if identity not in users:
            raise SystemExit('No such user: ' + identity)
        users.pop(identity)
        if not any('admins' in groups for groups in users.values()):
            raise SystemExit('At least one administrator must remain.')
        data['access']['users'] = users
    _change(path, operation)


def main(argv=None, install=None):
    parser = argparse.ArgumentParser(prog='python -m tinylib.admin', description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('whoami', help='print the account name TinyLib sees for you')
    commands.add_parser('show', help='print the install, security file and users')
    add = commands.add_parser('add-user', help='add or update an account (default group: admins)')
    add.add_argument('identity', help='DOMAIN\\login as printed by whoami')
    add.add_argument('groups', nargs='*', default=['admins'])
    remove = commands.add_parser('remove-user', help='remove an account')
    remove.add_argument('identity')
    args = parser.parse_args(argv)
    install = install if isinstance(install, dict) else read_install(install)

    if args.command == 'whoami':
        print(windows_identity())
    elif args.command == 'show':
        print('Install file:  %s' % install['path'])
        if install['error']:
            print('Problem:       %s' % install['error'])
            return 1
        path = _security_path(install)
        print('Security file: %s' % path)
        data = _load(path)
        users = data['access']['users']
        print('Users (%d):' % len(users))
        for user, groups in sorted(users.items()):
            print('  %-32s %s' % (user, ', '.join(groups)))
        print('Libraries (%d):' % len(data.get('libraries', [])))
        for library in data.get('libraries', []):
            print('  %-32s %s%s' % (library.get('name', ''), library['root'],
                                    '  (read-only)' if library.get('read_only') else ''))
    elif args.command == 'add-user':
        add_user(_security_path(install), args.identity, args.groups)
        print('Saved %s: %s' % (args.identity.casefold(), ', '.join(sorted(set(args.groups)))))
    elif args.command == 'remove-user':
        remove_user(_security_path(install), args.identity)
        print('Removed ' + args.identity.casefold())
    return 0


if __name__ == '__main__':
    sys.exit(main())
