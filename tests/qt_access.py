"""Access editor persistence and denied-library UI visibility."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtCore, QtGui, QtWidgets
from tinylib.access import AccessControl
from tinylib.access_ui import AccessDialog
from tinylib.actions import ActionRegistry
from tinylib.library import atomic_json
from tinylib.ui import Browser

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QtGui.QFont('Segoe UI', 10))
root = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory()
folder = Path(temporary.name)
config = folder / 'studio.json'
atomic_json(config, {'libraries': [{'name': 'Private', 'root': str(folder / 'must-not-be-read')}],
                     'action_roots': [str(root / 'actions')]})
admin = AccessControl({'_config_path': str(config)}, identity='test\\admin')
dialog = AccessDialog(admin, ActionRegistry([root / 'actions']))
dialog.add_user('test\\guest', ['restricted'])
dialog.show()
app.processEvents()
dialog.grab().save(str(root / 'artifacts/access-editor.png'))
dialog.save()
assert dialog.result() == QtWidgets.QDialog.Accepted
user = AccessControl({'_config_path': str(config)}, identity='test\\guest')
assert user.groups == {'restricted'}
window = Browser(config, folder / 'preferences.json', access=user)
window.show()
timer = QtCore.QElapsedTimer()
timer.start()
while window.loader.isRunning() and timer.elapsed() < 5000:
    app.processEvents()
    QtCore.QThread.msleep(5)
app.processEvents()
assert not window.assets and not window.errors, window.errors
assert window.categories.topLevelItemCount() == 1
assert not window.access_button.isVisible()
assert not window.ingest_button.isEnabled()
assert not window.action_picker.button.isEnabled()
window.close()
print('Access UI passed: administrator assignment persists; restricted user cannot load private libraries.')
