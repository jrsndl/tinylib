"""TinyLib logging: a rotating tinylib-<user>.log in the local cache folder.

Modules log through ``get_logger(__name__)``. Nothing is written until an entry
point calls ``configure()``: the browser does on startup and the farm worker
also echoes to the console, so Deadline's task log shows the same lines.
"""
import logging
import logging.handlers
import sys
from pathlib import Path

FORMAT = '%(asctime)s %(levelname)-7s %(name)s: %(message)s'
_log_path = None
# Library convention: silent until an entry point configures logging.
logging.getLogger('tinylib').addHandler(logging.NullHandler())


class _SharedFileHandler(logging.handlers.RotatingFileHandler):
    """Rollover fails on Windows while another TinyLib process has the file open; that must
    never print a traceback into Nuke. The record is dropped and logging carries on."""
    def handleError(self, record):
        pass


def _log_name():
    import getpass
    import re
    try:
        user = re.sub(r'[^A-Za-z0-9._-]+', '_', getpass.getuser()) or 'user'
    except Exception:
        user = 'user'
    return 'tinylib-%s.log' % user  # The cache folder can be shared by every user of the machine.


def get_logger(name='tinylib'):
    return logging.getLogger(name if name.startswith('tinylib') else 'tinylib.' + name)


def configure(log_dir=None, console=False, level=logging.INFO):
    """Attach handlers once per process; returns the log file path (None if unavailable)."""
    global _log_path
    logger = logging.getLogger('tinylib')
    logger.setLevel(level)
    logger.propagate = False  # Keep Nuke's own logging and Script Editor output clean.
    if _log_path is None:
        try:
            if log_dir is None:
                from .thumbnail_cache import cache_root
                log_dir = cache_root()
            path = Path(log_dir) / _log_name()
            handler = _SharedFileHandler(str(path), maxBytes=1024 * 1024, backupCount=3,
                                         encoding='utf-8', delay=True)
            handler.setFormatter(logging.Formatter(FORMAT))
            logger.addHandler(handler)
            _log_path = path
        except OSError:
            _log_path = None
    if console and not any(getattr(h, '_tinylib_console', False) for h in logger.handlers):
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter(FORMAT))
        stream._tinylib_console = True
        logger.addHandler(stream)
    return _log_path


def log_path():
    return _log_path
