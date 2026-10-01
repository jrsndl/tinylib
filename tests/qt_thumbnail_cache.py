"""Verify that a second ImageCache instance reads the local disk preview."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tinylib.qt import QtCore, QtGui, QtWidgets
from tinylib.thumbnail_cache import cached_image_path
from tinylib.views import ImageCache

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    source = root / 'library-thumb.jpg'
    image = QtGui.QImage(960, 506, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor('#ca782d'))
    assert image.save(str(source), 'JPG')
    disk_root = root / 'local-cache'

    first = ImageCache(disk_root=disk_root)
    assert first.get(str(source)).isNull()
    first.pool.waitForDone()
    app.processEvents()
    assert cached_image_path(disk_root, source).is_file()
    assert first.disk_hits == 0

    second = ImageCache(disk_root=disk_root)
    assert second.get(str(source)).isNull()
    second.pool.waitForDone()
    app.processEvents()
    pixmap = second.get(str(source))
    assert not pixmap.isNull() and pixmap.width() == 640
    assert second.disk_hits == 1
    assert second.snapshot()['completed'] == 1 and second.snapshot()['pending'] == 0

print('Persistent thumbnail cache passed')
