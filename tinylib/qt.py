"""Use Nuke's Qt binding when hosted; allow standalone PySide2 or PySide6."""
import sys
nuke = sys.modules.get('nuke')
NUKE_MAJOR = int(getattr(nuke, 'NUKE_VERSION_MAJOR', 0) or 0) if nuke is not None else 0
# Nuke's GUI session; `nuke -t` (terminal mode) imports nuke but has no Qt windows.
HOSTED_IN_NUKE = nuke is not None and bool(getattr(nuke, 'GUI', True))

if NUKE_MAJOR >= 16:
    from PySide6 import QtCore, QtGui, QtWidgets
else:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401 - re-exported


def exec_dialog(dialog):
    """QDialog.exec() on PySide6, exec_() on PySide2."""
    run = getattr(dialog, 'exec', None) or dialog.exec_
    return run()


def event_pos(event):
    """Mouse/drop position as QPoint; PySide6 deprecates pos() in favour of position()."""
    position = getattr(event, 'position', None)
    if position is not None:
        return position().toPoint()
    return event.pos()
