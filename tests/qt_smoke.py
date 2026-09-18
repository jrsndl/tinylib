"""Run with a PySide runtime and QT_QPA_PLATFORM=offscreen."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtCore, QtGui, QtWidgets
from tinylib.ui import Browser
from helpers import test_access
from tinylib.ingest_ui import IngestDialog

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QtGui.QFont('Segoe UI', 10))
root = Path(__file__).resolve().parents[1]
preferences_temp = tempfile.TemporaryDirectory()
window = Browser(root / 'config/demo.json', Path(preferences_temp.name) / 'preferences.json', access=test_access(root / 'config/demo.json'))
window.show()
limit = QtCore.QElapsedTimer()
limit.start()
while window.loader.isRunning() and limit.elapsed() < 45000:
    app.processEvents()
    QtCore.QThread.msleep(20)
app.processEvents()
assert not window.loader.isRunning(), 'Library load timed out'
assert window.assets, window.errors
window.categories.setCurrentItem(window.categories.topLevelItem(2))
app.processEvents()
assert window.model.rowCount() == 3, window.model.rowCount()
window.grid.setCurrentIndex(window.model.index(0, 0))
for _ in range(30):
    app.processEvents()
    QtCore.QThread.msleep(30)
output = root / 'artifacts'
output.mkdir(exist_ok=True)
window.grab().save(str(output / ('browser-' + QtCore.__name__.split('.')[0] + '.png')))
window.search.setText('abandoned bakery')
window.filter()
assert window.model.rowCount() == 1, window.model.rowCount()
window.search.setText('missingkeyword')
window.filter()
assert window.model.rowCount() == 0
dialog = IngestDialog(window.settings, window.assets, window)
dialog.show()
app.processEvents()
dialog.grab().save(str(output / ('ingest-' + QtCore.__name__.split('.')[0] + '.png')))
dialog.close()
window.cache.pool.waitForDone()
window.close()
print('Qt smoke passed:', QtCore.__name__, 'assets:', len(window.assets))
