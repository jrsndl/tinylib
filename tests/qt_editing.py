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
from tinylib.path_format import format_assets
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
records[2]['name'] = 'A complete asset name that must never be shortened in list mode'
atomic_json(folder / 'data.json', {'schema_version': 3, 'assets': records})
config = folder / 'studio.json'
atomic_json(config, {'libraries': [{'name': 'Editable test library', 'root': str(folder)}]})
preferences = folder / 'preferences.json'
window = Browser(config, preferences, access=test_access(config))
window.show()
assert '#ffa02f' in window.styleSheet() and 'url(:images/' not in window.styleSheet()


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
# Main-view 0-5 shortcuts apply to the whole selection.
QTest.keyClick(window.grid, QtCore.Qt.Key_5)
assert window.preferences.rating(window.main_selected()[0]) == 5
select(window.model, window.grid, 0, 1)
QTest.keyClick(window.grid, QtCore.Qt.Key_2)
assert all(window.preferences.rating(asset) == 2 for asset in window.main_selected())
QTest.keyClick(window.grid, QtCore.Qt.Key_0)
assert all(window.preferences.rating(asset) == 0 for asset in window.main_selected())
select(window.model, window.grid, 0)
editor = window.properties
assert not editor.lock.isChecked() and editor.fields['name'].isReadOnly()
assert editor.metadata.rowCount() == 3, 'Source paths must not be exposed in metadata fields'
QTest.mouseClick(editor.lock, QtCore.Qt.LeftButton)
assert editor.fields['name'].isReadOnly()
assert editor.fields['library'].isReadOnly() and editor.fields['kind'].isReadOnly()
editor.fields['colorspace'].setText('ACES2065-1')
editor.fields['tags'].setText('hot, flame')
assert editor.fields['range'].text() == '1001-1100' and editor.fields['range'].isReadOnly()
assert not hasattr(editor, 'star_buttons') and not hasattr(editor, 'stars_widget')
QTest.mouseClick(editor.save, QtCore.Qt.LeftButton)
pump()
saved = read_json(folder / 'data.json')['assets'][0]
assert saved['name'] == 'Fire 0' and saved['colorspace'] == 'ACES2065-1' and saved['tags'] == ['hot', 'flame']
assert saved['main'] == records[0]['main'] and saved['kind'] == 'footage'
assert '_rating' not in saved and 'library_root' not in saved
assert window.assets[0]['_rating'] == 0 and not editor.lock.isChecked()
select(window.model, window.grid, 0)
QTest.mouseClick(editor.lock, QtCore.Qt.LeftButton)
editor.fields['colorspace'].setText('Discard me')
QTest.mouseClick(editor.cancel, QtCore.Qt.LeftButton)
assert editor.fields['colorspace'].text() == 'ACES2065-1'
# A permission change after unlocking is checked again at save time.
QTest.mouseClick(editor.lock, QtCore.Qt.LeftButton)
editor.fields['colorspace'].setText('Denied')
window.settings['libraries'][0]['read_only'] = True
QTest.mouseClick(editor.save, QtCore.Qt.LeftButton)
assert 'read-only' in editor.message.text()
assert read_json(folder / 'data.json')['assets'][0]['colorspace'] == 'ACES2065-1'
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
        if mode == 'List':
            longest = max(window.fontMetrics().horizontalAdvance(asset['name']) for asset in window.model.assets)
            assert window.grid.textElideMode() == QtCore.Qt.ElideNone
            assert window.grid.gridSize().width() >= longest
        select(window.model, view, 0, 1)
        QTest.keyClick(view, QtCore.Qt.Key_Enter)
        assert play.call_count == 1
    select(window.model, window.grid, 0)
    play.reset_mock()
    double_click = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonDblClick, QtCore.QPointF(20, 20),
                                     QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    app.sendEvent(window.detail_image, double_click)
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
# Copy uses only a proper subset selection; no selection or all selected means all.
window.preferences.add_assets(first, assets)
window.collections.refresh(first)
window.path_notation.setCurrentIndex(window.path_notation.findData('flame'))
select(window.collections.model, window.collections.grid, 0)
window.collections.copy_paths()
assert app.clipboard().text() == format_assets([window.collections.model.assets[0]], 'flame')
window.collections.grid.selectionModel().clearSelection()
window.collections.copy_paths()
assert app.clipboard().text() == format_assets(window.collections.model.assets, 'flame')
select(window.collections.model, window.collections.grid, *range(window.collections.model.rowCount()))
window.collections.copy_paths()
assert app.clipboard().text() == format_assets(window.collections.model.assets, 'flame')
window.preferences.delete_collection(first)
window.preferences.delete_collection(second)
window.collections.refresh()
window.filter()
window.collection_toggle.setChecked(False)
window.view_mode.setCurrentText('Tiles')
select(window.model, window.grid, 0)
# External drag text and Ctrl+C share the selected preference and use forward slashes.
for notation in ('nuke', 'ayon', 'hashtag', 'houdini', 'flame', 'printf', 'folder'):
    window.path_notation.setCurrentIndex(window.path_notation.findData(notation))
    mime = window.model.mimeData(window.grid.selectionModel().selectedIndexes())
    assert mime.text() == format_assets(window.main_selected(), notation)
    assert '\\' not in mime.text()
    QTest.keyClick(window.grid, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)
    assert app.clipboard().text() == mime.text()
assert Preferences(preferences).data['display']['path_notation'] == 'folder'
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
