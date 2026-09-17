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

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
root = Path(__file__).resolve().parents[1]
temp = tempfile.TemporaryDirectory()
start = time.perf_counter()
window = Browser(root / 'config/performance.json', Path(temp.name) / 'preferences.json')
window.show()
while window.loader.isRunning():
    app.processEvents()
    QtCore.QThread.msleep(5)
    if time.perf_counter() - start > 60:
        raise AssertionError('Library load timeout')
app.processEvents()
result = {'assets': len(window.assets), 'ui_load_seconds': round(time.perf_counter() - start, 3)}
assert window.categories.topLevelItem(1).childCount() > 0
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
