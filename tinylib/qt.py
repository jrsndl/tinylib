"""Use Nuke's Qt binding when hosted; allow standalone PySide2 or PySide6."""
import sys
nuke = sys.modules.get('nuke')
HOSTED_IN_NUKE = nuke is not None

if nuke is not None and nuke.NUKE_VERSION_MAJOR >= 16:
    from PySide6 import QtCore, QtGui, QtWidgets
else:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets
