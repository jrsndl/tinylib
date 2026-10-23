"""Shared JSON storage: atomic replacement and cross-machine write locks.

Everything TinyLib writes to a share (library databases, the security file,
edit locks, user preferences) goes through these two helpers.
"""
import json
import os
import socket
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from .log import get_logger

log = get_logger(__name__)

LOCK_FILENAME = '.tinylib-write.lock'
# A write under the lock takes seconds. A lock this old belongs to a crashed writer.
STALE_LOCK_SECONDS = 600


def read_json(path):
    with open(path, encoding='utf-8-sig') as stream:
        return json.load(stream)


def _replace(source, target, attempts=20, delay=.1):
    """os.replace, retried while Windows reports the target open in another process."""
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            # Readers on Windows shares hold the file without FILE_SHARE_DELETE for a moment.
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


FILE_ATTRIBUTE_HIDDEN = 0x2


def clear_hidden(path):
    """Remove the Windows hidden attribute from ``path``, if it has it; elsewhere nothing.

    A Samba share with ``hide dot files`` (its default) marks a file whose name starts with a
    dot as hidden, and the mark stays when the file is renamed. Temporary names therefore never
    start with a dot, and files written through one are checked here as well."""
    if os.name != 'nt':
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        attributes = kernel32.GetFileAttributesW(str(path))
        if attributes == 0xFFFFFFFF or not attributes & FILE_ATTRIBUTE_HIDDEN:
            return False
        return bool(kernel32.SetFileAttributesW(str(path), attributes & ~FILE_ATTRIBUTE_HIDDEN))
    except Exception:  # never fail a write over an attribute
        return False


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        _replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def pid_alive(pid):
    """True when a process with ``pid`` exists on this machine (unknown counts as alive)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name == 'nt':
        import ctypes
        # use_last_error: Python's own API calls must not overwrite the error we read.
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Only ERROR_INVALID_PARAMETER means "no such process"; access denied
            # (another user's process on a shared render or terminal server) means alive.
            return ctypes.get_last_error() != 87
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_lock(path):
    """Describe a write lock: host, pid, token, age_seconds. Tolerates old 'PID n' locks."""
    path = Path(path)
    info = {'path': str(path), 'host': None, 'pid': None, 'token': None, 'age_seconds': 0.0,
            'raw': None, 'exists': True}
    try:
        info['age_seconds'] = max(0.0, time.time() - path.stat().st_mtime)
        text = path.read_text(encoding='utf-8', errors='replace').strip()
    except FileNotFoundError:
        info['exists'] = False
        return info
    except OSError:
        return info  # Exists but unreadable right now (sharing violation): raw stays None.
    info['raw'] = text
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            info.update({key: data.get(key) for key in ('host', 'pid', 'token')})
    except ValueError:
        if text.startswith('PID '):
            info['pid'] = text[4:].strip()
    return info


def lock_is_stale(info, stale_after=STALE_LOCK_SECONDS):
    if info['age_seconds'] >= stale_after:
        return True
    same_host = info.get('host') and str(info['host']).casefold() == socket.gethostname().casefold()
    return bool(same_host and info.get('pid') is not None and not pid_alive(info['pid']))


def describe_lock(info):
    owner = '%s (process %s)' % (info.get('host') or 'unknown computer', info.get('pid') or '?')
    return 'held by %s for %d s' % (owner, info['age_seconds'])


def _break_lock(lock, observed):
    """Move the stale lock we inspected aside, and only that one.

    Two processes can judge the same lock stale. The first moves it and creates
    its own; the second would then move the *live* lock. So after moving, the
    content is compared with what was inspected, and a different lock is put back.
    """
    if observed.get('raw') is None:
        return False  # Never break a lock whose content could not be read.
    aside = lock.with_name('%s.stale-%s' % (lock.name, uuid.uuid4().hex[:8]))
    try:
        os.rename(str(lock), str(aside))
    except OSError:
        return False
    try:
        moved = aside.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        moved = None
    if moved != observed['raw']:
        _restore_lock(aside, lock)
        return False
    try:
        aside.unlink()
    except OSError:
        pass
    return True


def _restore_lock(aside, lock):
    """Put back a lock moved by mistake without overwriting a newer one."""
    try:
        if os.name == 'nt':
            os.rename(str(aside), str(lock))  # Fails instead of replacing an existing file.
        else:
            os.link(str(aside), str(lock))    # Fails if a lock exists; rename would replace it.
            aside.unlink()
    except OSError:
        log.warning('Could not restore write lock %s from %s', lock, aside)


@contextmanager
def library_lock(root, timeout=15, stale_after=STALE_LOCK_SECONDS):
    """Exclusive create works across processes and SMB.

    A lock left by a crashed writer is broken automatically: immediately when
    its process is gone from this computer, otherwise once it is older than
    ``stale_after`` seconds. The lock is only removed by the writer that owns it.
    """
    lock = Path(root) / LOCK_FILENAME
    token = uuid.uuid4().hex
    payload = json.dumps({'host': socket.gethostname(), 'pid': os.getpid(), 'token': token,
                          'created': time.time()}).encode('utf-8')
    end = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = read_lock(lock)
            if lock_is_stale(info, stale_after) and _break_lock(lock, info):
                log.warning('Broke stale write lock %s (%s)', lock, describe_lock(info))
                continue
            if time.monotonic() >= end:
                log.warning('Timed out waiting for write lock %s (%s)', lock, describe_lock(info))
                raise RuntimeError('Library is locked by another writer (%s): %s' % (describe_lock(info), lock))
            time.sleep(.1)
            continue
        try:
            os.write(fd, payload)
        except OSError:
            os.close(fd)
            lock.unlink()
            raise
        os.close(fd)
        break
    try:
        yield
    finally:
        _release(lock, token)


def _release(lock, token, attempts=20, delay=.1):
    """Remove our lock. Waiting processes read it every 100 ms, so on Windows shares the
    read or the delete can briefly hit a sharing violation; retry instead of leaving it."""
    for attempt in range(attempts):
        info = read_lock(lock)
        if not info['exists']:
            return
        if info['raw'] is not None:
            if info['token'] != token:
                return  # Broken as stale and re-taken by another writer: not ours any more.
            try:
                lock.unlink()
                return
            except FileNotFoundError:
                return
            except PermissionError:
                pass
        time.sleep(delay)
    log.warning('Could not remove write lock %s; it will be treated as stale later.', lock)
