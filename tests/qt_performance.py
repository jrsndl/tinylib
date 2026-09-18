"""Read-only performance measurements on the configured library with scratch user preferences."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtCore, QtGui, QtWidgets
from tinylib.ui import Browser
from helpers import test_access

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
root = Path(__file__).resolve().parents[1]
temp = tempfile.TemporaryDirectory()
start = time.perf_counter()
window = Browser(root / 'config/performance.json', Path(temp.name) / 'preferences.json', access=test_access(root / 'config/performance.json'))
window.show()
while window.loader.isRunning():
    app.processEvents()
    QtCore.QThread.msleep(5)
    if time.perf_counter() - start > 60:
        raise AssertionError('Library load timeout')
app.processEvents()
result = {'assets': len(window.assets), 'ui_load_seconds': round(time.perf_counter() - start, 3)}
assert window.categories.topLevelItem(1).childCount() > 0

class PaintCounter(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.count = 0
    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Paint:
            self.count += 1
        return False

window.view_mode.setCurrentText('List')
app.processEvents()
longest = max(window.grid.fontMetrics().horizontalAdvance(asset['name']) for asset in window.model.assets)
assert window.grid.gridSize().width() >= longest and window.grid.textElideMode() == QtCore.Qt.ElideNone
counter = PaintCounter()
window.grid.viewport().installEventFilter(counter)
# After one settled paint, an idle list must not be redrawn by the animation or image-cache timers.
settle = time.perf_counter()
while time.perf_counter() - settle < .5:
    app.processEvents()
    QtCore.QThread.msleep(5)
counter.count = 0
settle = time.perf_counter()
while time.perf_counter() - settle < 1:
    app.processEvents()
    QtCore.QThread.msleep(5)
result['list_idle_paints_per_second'] = counter.count
assert counter.count <= 2, 'List view is repainting while idle: %s paints/second' % counter.count
window.grid.viewport().removeEventFilter(counter)
window.view_mode.setCurrentText('Tiles')
window.search.setText('fire')
window.length_filter.value.setValue(2)
window.length_filter.operator.setCurrentIndex(1)
window.width_filter.value.setValue(1920)
window.width_filter.operator.setCurrentIndex(1)
start = time.perf_counter()
window.filter()
result['combined_filter_ms'] = round((time.perf_counter() - start)*1000, 2)
result['matching_assets'] = window.model.rowCount()
window.clear_filters()
window.scale.setValue(180)
window.play_button.setChecked(True)
start = time.perf_counter()
delays = []
previous = start
while time.perf_counter() - start < 3:
    app.processEvents()
    current = time.perf_counter()
    delays.append(current - previous)
    previous = current
    QtCore.QThread.msleep(5)
result['play_all_max_event_loop_gap_ms'] = round(max(delays) * 1000, 2)
result['cached_image_mb'] = round(window.cache.bytes / 1024 / 1024, 1)
start = time.perf_counter()
delays = []
previous = start
while time.perf_counter() - start < 2:
    app.processEvents()
    current = time.perf_counter()
    delays.append(current - previous)
    previous = current
    QtCore.QThread.msleep(5)
result['play_all_warm_max_event_loop_gap_ms'] = round(max(delays) * 1000, 2)
window.play_button.setChecked(False)
window.cache.pool.waitForDone()
app.processEvents()
window.close()
(root / 'artifacts/features-performance-e.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
