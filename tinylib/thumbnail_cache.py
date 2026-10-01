"""Persistent local cache paths for decoded TinyLib thumbnails and filmstrips."""
import hashlib
import os
import tempfile
from pathlib import Path

CACHE_VERSION = 1
INDEX_CACHE_VERSION = 1


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


def cache_root(environ=None, fallback=None, shared_candidates=None):
    """Prefer NUKE_TEMP_DIR, then machine-shared temp, then platform temp."""
    environ = os.environ if environ is None else environ
    nuke_temp = environ.get('NUKE_TEMP_DIR', '').strip()
    if nuke_temp:
        folder = _writable_subfolder(nuke_temp)
        if folder:
            return folder

    candidates = list(shared_candidates or [])
    if os.name == 'nt' and shared_candidates is None:
        system_drive = environ.get('SystemDrive', 'C:').rstrip('\\/')
        candidates.append(Path(system_drive + '\\Temp'))
        # Other fixed-drive temp folders are common studio scratch locations.
        candidates.extend(Path('%s:\\Temp' % letter) for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ')
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
