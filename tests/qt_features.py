"""Exercise UI selections, all view modes, filters, actual drop events and persistence."""
import json
import os
import sys
import tempfile
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtCore, QtGui, QtWidgets
if QtCore.__name__.startswith('PySide6'):
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest
from tinylib.ui import Browser
from helpers import test_access
from tinylib.preferences import Preferences, asset_key
from tinylib.collection_ui import ASSET_MIME

root = Path(__file__).resolve().parents[1]
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QtGui.QFont('Segoe UI', 10))
temp = tempfile.TemporaryDirectory()
preferences = Path(temp.name) / 'preferences.json'
window = Browser(root / 'config/demo.json', preferences, access=test_access(root / 'config/demo.json'))
window.show()


def pump(milliseconds=100):
    elapsed = QtCore.QElapsedTimer()
    elapsed.start()
    while elapsed.elapsed() < milliseconds:
        app.processEvents()
        QtCore.QThread.msleep(5)
    app.processEvents()


def select_rows(*rows):
    selection = window.grid.selectionModel()
    selection.clearSelection()
    for row in rows:
        selection.select(window.model.index(row, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
    pump()


def wait_load():
    for _ in range(450):
        pump(100)
        if not window.loader.isRunning():
            return
    raise AssertionError('Load timed out')


wait_load()
assert window.categories.topLevelItemCount() == 3
assert all(window.categories.topLevelItem(i).isExpanded() for i in [1, 2])
assert window.collections.isHidden()
window.categories.setCurrentItem(window.categories.topLevelItem(2))
pump()
assert window.model.rowCount() == 3
# Real Ctrl-click selection in tiles (not just selection model calls).
for row, modifiers in [(0, QtCore.Qt.NoModifier), (1, QtCore.Qt.ControlModifier)]:
    position = window.grid.visualRect(window.model.index(row, 0)).center()
    QTest.mouseClick(window.grid.viewport(), QtCore.Qt.LeftButton, modifiers, position)
pump()
assert len(window.selected_assets()) == 2, len(window.selected_assets())
selected_keys = {asset_key(a) for a in window.selected_assets()}
QTest.mouseClick(window.star_buttons[3], QtCore.Qt.LeftButton)
assert all(a['_rating'] == 4 for a in window.selected_assets())
assert len(window.selected_assets()) == 2
for mode in ['Details', 'List', 'Tiles']:
    window.view_mode.setCurrentText(mode)
    pump()
    assert {asset_key(a) for a in window.selected_assets()} == selected_keys
    if mode == 'List':
        assert window.grid.visualRect(window.model.index(1, 0)).x() > window.grid.visualRect(window.model.index(0, 0)).x()
window.stars_filter.value.setValue(3)
window.stars_filter.operator.setCurrentIndex(1)
assert window.model.rowCount() == 2
window.stars_filter.operator.setCurrentIndex(2)
assert window.model.rowCount() == 1
window.clear_filters()
window.search.setText('bakery')
window.filter()
assert window.model.rowCount() == 1
window.invert.setChecked(True)
assert window.model.rowCount() == 2
window.clear_filters()
window.tags.setText('indoor, abandonedbakery')
window.filter()
assert window.model.rowCount() == 1
window.clear_filters()
select_rows(0, 1)
window.collection_toggle.setChecked(True)
pump()
mime = window.model.mimeData(window.grid.selectionModel().selectedIndexes())
assert len(json.loads(bytes(mime.data(ASSET_MIME)))) == 2
# Deliver Qt drag-enter/drop to the actual collection viewport.
point = QtCore.QPoint(40, 60)
enter = QtGui.QDragEnterEvent(point, QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
QtWidgets.QApplication.sendEvent(window.collections.items.viewport(), enter)
assert enter.isAccepted()
drop = QtGui.QDropEvent(QtCore.QPointF(point), QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
QtWidgets.QApplication.sendEvent(window.collections.items.viewport(), drop)
pump()
assert drop.isAccepted()
assert window.collections.model.rowCount() == 2
assert window.model.rowCount() == 1
assert window.collections.combo.currentText() == 'collection01'
assert window.collections.view_mode.currentText() == 'Tiles'
for mode in ['Details', 'List', 'Tiles']:
    window.collections.view_mode.setCurrentText(mode)
    pump()
    assert window.collections.model.rowCount() == 2
window.collections.play_button.setChecked(True)
pump(200)
assert window.collections.grid.cards.play_all and not window.grid.cards.play_all
window.collections.play_button.setChecked(False)
window.run_action('clipboard.paths', collection=True)
assert len(app.clipboard().text().splitlines()) == 2
exported = str(Path(temp.name) / 'collection.json')
with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(exported, 'JSON')) as dialog:
    window.collections.export_file()
    assert Path(dialog.call_args.args[2]).parent == Path(window.collections.downloads())
with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(exported, 'JSON')) as dialog:
    window.collections.import_file()
    assert dialog.call_args.args[2] == window.collections.downloads()
assert window.collections.combo.currentText() == 'collection01 (2)'
window.collections.delete()
collection_id = window.collections.combo.currentData()
window.preferences.rename_collection(collection_id, 'Lighting picks')
window.collections.refresh()
assert window.collections.combo.currentText() == 'Lighting picks'
window.collections.grid.selectionModel().select(window.collections.model.index(0, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
window.collections.remove()
assert window.model.rowCount() == 2
# A picked item stays hidden even when collections are closed.
window.collection_toggle.setChecked(False)
assert window.model.rowCount() == 2
window.collection_toggle.setChecked(True)
window.collections.grid.selectionModel().select(window.collections.model.index(0, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
QTest.mouseClick(window.star_buttons[4], QtCore.Qt.LeftButton)
assert window.collections.selected_assets()[0]['_rating'] == 5
window.collections.create()
assert window.collections.combo.currentText() == 'collection02'
assert window.collections.model.rowCount() == 0
assert not window.selected_assets(), 'Changing collection must clear stale selection'
window.collections.delete()
assert window.collections.combo.currentText() == 'Lighting picks'
# Pick real footage with filmstrips to exercise shared play-all animation.
window.categories.setCurrentItem(window.categories.topLevelItem(1))
window.search.setText('Big_Fire')
window.filter()
pump(500)
assert window.model.rowCount() > 0
window.play_button.setChecked(True)
pump(400)
assert window.grid.cards.play_all
frame = window.grid.cards.frame
window.animate()
assert window.grid.cards.frame == (frame + 1) % 24
window.play_button.setChecked(False)
assert not window.grid.cards.play_all
before = window.grid.cards.frame
window.animate()
assert window.grid.cards.frame == before
window.scale.setValue(200)
assert window.grid.cards.width == 200
window.view_mode.setCurrentText('Details')
select_rows(0, 1)
assert len(window.selected_assets()) == 2
window.collections.grid.selectionModel().select(window.collections.model.index(0, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
window.collections.items.setFocus()
pump()
assert window.selection_source == 'collection'
window.table.setFocus()
pump()
assert len(window.selected_assets()) == 2 and window.selection_source == 'main'
window.length_filter.value.setValue(0)
window.length_filter.operator.setCurrentIndex(1)
assert window.model.rowCount() > 0
window.width_filter.value.setValue(999999)
window.width_filter.operator.setCurrentIndex(1)
assert window.model.rowCount() == 0
window.clear_filters()
window.search.setText('Big_Fire')
window.filter()
select_rows(0, 1)
window.rate_selected(3)
window.play_button.setChecked(True)
pump(600)
output = root / 'artifacts'
window.grab().save(str(output / ('features-details-' + QtCore.__name__.split('.')[0] + '.png')))
window.view_mode.setCurrentText('Tiles')
pump(600)
window.grab().save(str(output / ('features-tiles-' + QtCore.__name__.split('.')[0] + '.png')))
saved = Preferences(preferences)
assert len(saved.collections) == 1 and saved.collections[0]['name'] == 'Lighting picks'
assert len(saved.picked_keys()) == 1
assert 5 in saved.data['ratings'].values()
window.cache.pool.waitForDone()
window.close()
window = Browser(root / 'config/demo.json', preferences, access=test_access(root / 'config/demo.json'))
window.show()
wait_load()
assert window.collections.isHidden()
window.categories.setCurrentItem(window.categories.topLevelItem(2))
assert window.model.rowCount() == 2, 'Saved picks must stay hidden after reopening'
assert any(a['_rating'] == 4 for a in window.model.assets)
window.cache.pool.waitForDone()
window.close()
print('Qt features passed:', QtCore.__name__, 'multiselect, ratings, view modes, filters, drag/drop, collections, playback, persistence')
