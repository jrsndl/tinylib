"""Persistent, user-visible library edit locks.

The short-lived ``.tinylib-write.lock`` serializes lock-file changes on SMB.  The
``lock.<user>.txt`` file then remains for the duration of an interactive edit.
"""
import json
import os
import re
import socket
import uuid
from datetime import datetime
from pathlib import Path

from .library import library_lock


class LibraryEditLocked(RuntimeError):
    def __init__(self, locks):
        self.locks = locks
        super().__init__(locked_message(locks))


def _safe_user(identity):
    value = re.sub(r'[^A-Za-z0-9._-]+', '_', str(identity).strip()).strip('._-')
    return value or 'user'


def _lock_files(root):
    root = Path(root)
    try:
        return sorted(path for path in root.glob('lock.*.txt') if path.is_file())
    except OSError:
        return []


def _read_lock(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        try:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec='seconds')
        except OSError:
            timestamp = 'unknown time'
        value = {'identity': path.name[5:-4], 'created_at': timestamp}
    value['path'] = str(path)
    value.setdefault('identity', path.name[5:-4])
    value.setdefault('created_at', 'unknown time')
    return value


def read_edit_locks(root):
    return [_read_lock(path) for path in _lock_files(root)]


def locked_message(locks):
    if not locks:
        return 'The library is locked for editing.'
    first = locks[0]
    message = 'User %s is editing the library (%s), edit is not possible.' % (
        first.get('identity', 'unknown'), first.get('created_at', 'unknown time'))
    if len(locks) > 1:
        message += ' %d edit lock files were found.' % len(locks)
    return message


def acquire_edit_lock(root, identity, override=False):
    """Acquire a persistent edit lock, optionally removing existing locks."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with library_lock(root):
        locks = read_edit_locks(root)
        if locks and not override:
            raise LibraryEditLocked(locks)
        if override:
            for item in locks:
                path = Path(item['path'])
                if path.parent.resolve() != root.resolve() or not re.fullmatch(r'lock\..+\.txt', path.name):
                    raise RuntimeError('Refusing to remove an invalid edit lock path: ' + str(path))
                path.unlink()
        token = uuid.uuid4().hex
        path = root / ('lock.%s.txt' % _safe_user(identity))
        record = {
            'identity': str(identity),
            'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'host': socket.gethostname(),
            'pid': os.getpid(),
            'token': token,
        }
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            payload = (json.dumps(record, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
    return dict(record, path=str(path), root=str(root))


def assert_edit_write_allowed(root, token=None):
    """Call while holding ``library_lock`` before changing tinylib_data.json."""
    locks = read_edit_locks(root)
    if locks and not (token and all(item.get('token') == token for item in locks)):
        raise LibraryEditLocked(locks)


def release_edit_lock(handle):
    """Remove only the lock created by this edit session."""
    if not handle:
        return
    root = Path(handle['root'])
    path = Path(handle['path'])
    with library_lock(root):
        if not path.exists():
            return
        current = _read_lock(path)
        if current.get('token') != handle.get('token'):
            raise RuntimeError('The edit lock changed and cannot be removed safely: ' + str(path))
        path.unlink()
