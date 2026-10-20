"""Real Qt selection changes for same-type multiedit and persistent locks."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtCore, QtWidgets
from tinylib.ui import Browser
from tinylib.library import atomic_json, read_json
from helpers import test_access

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
temp = tempfile.TemporaryDirectory()
folder = Path(temp.name)
roots = [folder / 'first', folder / 'second']
for root in roots:
    root.mkdir()
def record(name, kind='footage'):
    return dict(id='fire/' + name, name=name, category='fire', kind=kind,
                main='fire/%s/main/image.exr' % name, tags=[name.lower()],
                colorspace='ACEScg', first=1, last=2,
                ingest_note=name + ' note', metadata={'width': 10, 'height': 10, 'FPS': 24})
atomic_json(roots[0] / 'tinylib_data.json', {'schema_version': 3,
            'assets': [record('One'), record('Two'), record('Still', 'still')]})
atomic_json(roots[1] / 'tinylib_data.json', {'schema_version': 3, 'assets': [record('Other')]})
config = folder / 'studio.json'
atomic_json(config, {'libraries': [{'name': 'First', 'root': str(roots[0])},
                                 {'name': 'Second', 'root': str(roots[1])}]})
window = Browser(config, folder / 'prefs.json', access=test_access(config))
window.show()
def pump():
    for _ in range(30):
        app.processEvents()
        QtCore.QThread.msleep(2)
for _ in range(50):
    pump()
    if not window.loader.isRunning():
        break
assert window.model.rowCount() == 4

def select(*names):
    selected = QtCore.QItemSelection()
    for row, asset in enumerate(window.model.assets):
        if asset['name'] in names:
            index = window.model.index(row, 0)
            selected.select(index, index)
    window.grid.selectionModel().select(selected, QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
    pump()

def stored(root, name):
    return next(a for a in read_json(root / 'tinylib_data.json')['assets'] if a['name'] == name)

select('One', 'Two')
assert window.properties.isVisible() and window.edit_button.isEnabled()
window.begin_edit()
editor = window.properties
assert editor.editing and len(editor.assets) == 2
assert editor.fields['ingest_note'].toPlainText() == 'One note Two note'
assert editor.changes() == {}
lock_before = next(roots[0].glob('lock.*.txt')).read_bytes()
editor.fields['ingest_note'].setPlainText('Shared note')
assert '#ef5b5b' in editor.fields['ingest_note'].styleSheet()
with patch('tinylib.library.atomic_json', wraps=atomic_json) as write:
    select()
    assert write.call_count == 1, write.call_count
assert stored(roots[0], 'One')['ingest_note'] == 'Shared note'
assert stored(roots[0], 'Two')['ingest_note'] == 'Shared note'
assert stored(roots[0], 'One')['tags'] == ['one']
assert stored(roots[0], 'Two')['tags'] == ['two']
assert next(roots[0].glob('lock.*.txt')).read_bytes() == lock_before
select('One', 'Two')
editor.fields['colorspace'].clear()
select('Still')
assert {a['name'] for a in window.main_selected()} == {'One', 'Two'}
assert editor.fields['colorspace'].text() == '' and editor.editing
window.cancel_edit()
assert stored(roots[0], 'One')['colorspace'] == 'ACEScg'
assert not list(roots[0].glob('lock.*.txt'))
# Selecting different types cannot activate multiedit.
select('One', 'Still')
assert not window.edit_button.isEnabled() and not window.properties.isVisible()
# Same-type multiedit across libraries acquires each lock and retains the right token.
select('One', 'Other')
window.begin_edit()
assert len(window.edit_session['locks']) == 2
editor.fields['acquire_note'].setPlainText('Across libraries')
select('Other')
assert stored(roots[0], 'One')['acquire_note'] == 'Across libraries'
assert stored(roots[1], 'Other')['acquire_note'] == 'Across libraries'
editor.fields['ingest_note'].setPlainText('Second library only')
select()
assert stored(roots[1], 'Other')['ingest_note'] == 'Second library only'
window.write_edit()
assert not window.edit_mode
assert all(not list(root.glob('lock.*.txt')) for root in roots)
window.close()
pump()
temp.cleanup()
print('Multiedit UI passed: joined values, dirty borders, deselection commit, batch write, invalid draft restoration, mixed types, and library locks')
