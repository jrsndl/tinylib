"""Real Qt events for drag-back, tile info and locked metadata editing."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtCore, QtGui, QtWidgets
if QtCore.__name__.startswith('PySide6'):
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest
from tinylib.library import atomic_json, read_json
from tinylib.preferences import asset_key, Preferences
from tinylib.ui import Browser
from helpers import test_access

root = Path(__file__).resolve().parents[1]
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QtGui.QFont('Segoe UI', 10))
temp = tempfile.TemporaryDirectory()
folder = Path(temp.name)
image = QtGui.QImage(640, 360, QtGui.QImage.Format_RGB32)
image.fill(QtGui.QColor('#536a78'))
image.save(str(folder / 'thumb.jpg'))
records = [{'id': 'fire/%d' % i, 'name': 'Fire %d' % i, 'category': 'fire', 'kind': 'footage',
            'main': 'fire/%d/main/test.####.exr' % i, 'thumb': 'thumb.jpg', 'tags': ['fire'],
            'colorspace': 'ACEScg', 'first': 1001, 'last': 1100,
            'metadata': {'width': 640, 'height': 360, 'FPS': 24, 'source': 'C:/hidden/file.exr'}} for i in range(3)]
atomic_json(folder / 'data.json', {'schema_version': 3, 'assets': records})
config = folder / 'studio.json'
atomic_json(config, {'libraries': [{'name': 'Editable test library', 'root': str(folder)}]})
preferences = folder / 'preferences.json'
window = Browser(config, preferences, access=test_access(config))
window.show()


def pump(ms=80):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()
        QtCore.QThread.msleep(2)


def select(model, view, *rows):
    selection = view.selectionModel()
    selection.clearSelection()
    for row in rows:
        selection.select(model.index(row, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
    view.setFocus()
    pump()


def drop(view, mime):
    assert view.viewport().acceptDrops(), ('drops disabled', window.view_mode.currentText())
    point = QtCore.QPoint(20, 20)
    enter = QtGui.QDragEnterEvent(point, QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    app.sendEvent(view.viewport(), enter)
    event = QtGui.QDropEvent(QtCore.QPointF(point), QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    app.sendEvent(view.viewport(), event)
    pump()
    assert enter.isAccepted() and event.isAccepted(), (window.view_mode.currentText(), enter.isAccepted(), event.isAccepted(), mime.formats())


for _ in range(100):
    pump(20)
    if not window.loader.isRunning() and window.model.rowCount():
        break
assert window.model.rowCount() == 3, (window.model.rowCount(), window.errors, window.assets)
select(window.model, window.grid, 0)
editor = window.properties
assert not editor.lock.isChecked() and editor.fields['name'].isReadOnly()
assert editor.metadata.rowCount() == 3, 'Source paths must not be exposed in metadata fields'
QTest.mouseClick(editor.lock, QtCore.Qt.LeftButton)
assert not editor.fields['name'].isReadOnly()
assert editor.fields['library'].isReadOnly() and editor.fields['kind'].isReadOnly()
editor.fields['name'].setText('Renamed flame')
editor.fields['tags'].setText('hot, flame')
editor.stars.setValue(4)
QTest.mouseClick(editor.save, QtCore.Qt.LeftButton)
pump()
saved = read_json(folder / 'data.json')['assets'][0]
assert saved['name'] == 'Renamed flame' and saved['tags'] == ['hot', 'flame']
assert saved['main'] == records[0]['main'] and saved['kind'] == 'footage'
assert '_rating' not in saved and 'library_root' not in saved
assert window.assets[0]['_rating'] == 4 and not editor.lock.isChecked()
select(window.model, window.grid, 0)
QTest.mouseClick(editor.lock, QtCore.Qt.LeftButton)
editor.fields['name'].setText('Discard me')
QTest.mouseClick(editor.cancel, QtCore.Qt.LeftButton)
assert editor.fields['name'].text() == 'Renamed flame'
# A permission change after unlocking is checked again at save time.
QTest.mouseClick(editor.lock, QtCore.Qt.LeftButton)
editor.fields['name'].setText('Denied')
window.settings['libraries'][0]['read_only'] = True
QTest.mouseClick(editor.save, QtCore.Qt.LeftButton)
assert 'read-only' in editor.message.text()
assert read_json(folder / 'data.json')['assets'][0]['name'] == 'Renamed flame'
editor.cancel_edit()
window.settings['libraries'][0]['read_only'] = False
# Enter only opens a single selection, in every main view mode.
with patch.object(window.player, 'play') as play:
    for mode in ('Tiles', 'Details', 'List'):
        window.view_mode.setCurrentText(mode)
        view = window.table if mode == 'Details' else window.grid
        select(window.model, view, 0)
        play.reset_mock()
        QTest.keyClick(view, QtCore.Qt.Key_Return)
        assert play.call_count == 1
        select(window.model, view, 0, 1)
        QTest.keyClick(view, QtCore.Qt.Key_Enter)
        assert play.call_count == 1
# Each mode accepts a collection-origin drag. Main-origin drops are rejected.
window.collection_toggle.setChecked(True)
first = window.preferences.create_collection('First')
second = window.preferences.create_collection('Other picks')
assets = list(window.assets)
window.preferences.add_assets(second, [assets[0]])
for mode in ('Tiles', 'Details', 'List'):
    window.preferences.add_assets(first, assets)
    window.collections.refresh(first)
    window.filter()
    window.view_mode.setCurrentText(mode)
    window.collections.view_mode.setCurrentText(mode)
    view = window.table if mode == 'Details' else window.grid
    collection_view = window.collections.table if mode == 'Details' else window.collections.grid
    select(window.collections.model, collection_view, 0, 1)
    mime = window.collections.model.mimeData(collection_view.selectionModel().selectedIndexes())
    drop(view, mime)
    assert len(window.preferences.collection(first)['assets']) == 1
    assert len(window.preferences.collection(second)['assets']) == 1
    assert window.model.rowCount() == 1, 'Pick in another collection must remain hidden'
window.preferences.delete_collection(first)
window.preferences.delete_collection(second)
window.collections.refresh()
window.filter()
window.collection_toggle.setChecked(False)
window.view_mode.setCurrentText('Tiles')
select(window.model, window.grid, 0)
before = window.grid.cards.sizeHint(None, window.model.index(0, 0)).height()
window.info_toggle.setChecked(False)
pump()
after = window.grid.cards.sizeHint(None, window.model.index(0, 0)).height()
assert after < before and not window.grid.cards.info
output = root / 'artifacts'
window.grab().save(str(output / ('editing-no-info-' + QtCore.__name__.split('.')[0] + '.png')))
window.info_toggle.setChecked(True)
with patch.object(QtWidgets.QInputDialog, 'getMultiLineText', return_value=(r'{name}\n{category}\n{width} × {height}', True)):
    window.configure_tile_text()
assert window.grid.cards.template == r'{name}\n{category}\n{width} × {height}'
assert Preferences(preferences).data['display']['tile_info'] is True
QTest.mouseClick(editor.lock, QtCore.Qt.LeftButton)
pump()
window.grab().save(str(output / ('editing-info-' + QtCore.__name__.split('.')[0] + '.png')))
window.cache.pool.waitForDone()
window.close()
print('Editing UI passed:', QtCore.__name__, 'drag-back in all modes, Enter, info/template preferences, save/cancel, immutable fields and read-only enforcement')
