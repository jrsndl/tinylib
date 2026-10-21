"""Persistent local cache paths for decoded TinyLib thumbnails and filmstrips."""
import hashlib
import os
import tempfile
from pathlib import Path

CACHE_VERSION = 1
INDEX_CACHE_VERSION = 3  # Bump when Library.normalize output changes.


def _writable_subfolder(base):
    try:
        base = Path(base)
        if not base.is_dir():
            return None
        folder = base / 'tinylib'
        folder.mkdir(exist_ok=True)
        probe = folder / ('.write-test-%s' % os.getpid())
        with open(probe, 'wb') as stream:
            stream.write(b'')
        probe.unlink()
        return folder
    except (OSError, ValueError):
        return None


def _fixed_drive(path):
    """True for a local fixed disk on Windows; network, removable and optical drives are skipped."""
    if os.name != 'nt':
        return True
    import ctypes
    drive = os.path.splitdrive(str(path))[0]
    if not drive or drive.startswith(('\\\\', '//')):
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(drive + '\\') == 3  # DRIVE_FIXED


_CACHE_ROOT = None


def cache_root(environ=None, fallback=None, shared_candidates=None):
    """Prefer NUKE_TEMP_DIR, then machine-shared temp on a fixed local disk, then platform temp.

    The default lookup is done once per process; passing arguments (tests) bypasses it.
    """
    global _CACHE_ROOT
    default = environ is None and fallback is None and shared_candidates is None
    if default and _CACHE_ROOT is not None:
        return _CACHE_ROOT
    environ = os.environ if environ is None else environ
    folder = _choose_cache_root(environ, fallback, shared_candidates)
    if default:
        _CACHE_ROOT = folder
    return folder


def _choose_cache_root(environ, fallback, shared_candidates):
    nuke_temp = environ.get('NUKE_TEMP_DIR', '').strip()
    if nuke_temp:
        folder = _writable_subfolder(nuke_temp)
        if folder:
            return folder

    candidates = list(shared_candidates or [])
    if os.name == 'nt' and shared_candidates is None:
        system_drive = environ.get('SystemDrive', 'C:').rstrip('\\/')
        candidates.append(Path(system_drive + '\\Temp'))
        # Other fixed-drive temp folders are common studio scratch locations. Checking the drive
        # type first avoids waking disconnected network, card-reader and optical drives.
        candidates.extend(Path('%s:\\Temp' % letter) for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ'
                          if _fixed_drive('%s:' % letter))
        candidates.append(Path(environ.get('SystemRoot', 'C:\\Windows')) / 'Temp')
    for candidate in candidates:
        folder = _writable_subfolder(candidate)
        if folder:
            return folder

    base = Path(fallback or tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    folder = base / 'tinylib'
    folder.mkdir(exist_ok=True)
    return folder


def prune_cache(root, max_age_days=60, now=None):
    """Delete cached files unused for ``max_age_days`` and folders from older cache versions.

    Returns the number of files removed. Safe to run in a background thread.
    """
    import shutil
    import time
    root = Path(root)
    now = time.time() if now is None else now
    limit = now - max_age_days * 86400
    current = {'thumbnails-v%s' % CACHE_VERSION, 'indexes-v%s' % INDEX_CACHE_VERSION}
    removed = 0
    try:
        children = list(root.iterdir())
    except OSError:
        return 0
    for child in children:
        if not child.is_dir() or not child.name.startswith(('thumbnails-v', 'indexes-v')):
            continue
        if child.name not in current:
            shutil.rmtree(child, ignore_errors=True)
            continue
        for item in child.rglob('*'):
            try:
                # Access time is unreliable on Windows; modification time is when it was cached.
                if item.is_file() and item.stat().st_mtime < limit:
                    item.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def cached_image_path(root, source):
    """Return a content-identity path without reading the source image bytes."""
    source = Path(source)
    stat = source.stat()
    identity = '%s\0%s\0%s\0%s' % (
        CACHE_VERSION, os.path.normcase(os.path.abspath(str(source))), stat.st_size, stat.st_mtime_ns)
    digest = hashlib.sha256(identity.encode('utf-8', errors='surrogatepass')).hexdigest()
    return Path(root) / ('thumbnails-v%s' % CACHE_VERSION) / digest[:2] / (digest + '.jpg')


def cached_library_index_path(root, database, library_root, name, legacy_roots=()):
    database = Path(database)
    stat = database.stat()
    identity = '%s\0%s\0%s\0%s\0%s\0%s' % (
        INDEX_CACHE_VERSION, os.path.normcase(os.path.abspath(str(database))),
        stat.st_size, stat.st_mtime_ns, str(name),
        '|'.join([os.path.normcase(os.path.abspath(str(library_root))), *sorted(legacy_roots)]))
    digest = hashlib.sha256(identity.encode('utf-8', errors='surrogatepass')).hexdigest()
    return Path(root) / ('indexes-v%s' % INDEX_CACHE_VERSION) / (digest + '.json')
