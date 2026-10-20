"""Check prefix visibility, multiline editors, and inheritance in real Qt widgets."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtWidgets
from tinylib.properties_ui import PropertiesEditor
from tinylib.access_ui import AccessDialog
from tinylib.actions import ActionRegistry
from test_common_fields import CommonFieldTests

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
fixture = CommonFieldTests()
fixture.setUp()
try:
    editor = PropertiesEditor()
    editor.show()
    for identity, visible in [('user', set()), ('manager', {'ingest', 'acquire'}),
                              ('admin', {'version', 'ingest', 'acquire'})]:
        editor.access = fixture.access(identity)
        editor.set_asset(fixture.asset, False, identity != 'user')
        app.processEvents()
        for key, field in editor.fields.items():
            for prefix in ('version', 'ingest', 'acquire'):
                if key.startswith(prefix):
                    assert field.isHidden() == (prefix not in visible), (identity, key)
        assert editor.metadata.rowCount() == (1 if identity == 'user' else 2)
    editor.access = fixture.access('manager')
    editor.set_asset(fixture.asset, True, True)
    editor.fields['ingest_note'].setPlainText('Line one\nLine two')
    editor.fields['acquire_usage'].setPlainText('Use A\nUse B')
    changes = editor.changes()
    assert changes['ingest_note'] == 'Line one\nLine two'
    assert changes['acquire_usage'] == 'Use A\nUse B'
    assert not any(key.startswith('version') for key in changes)
    editor.close()

    admin = fixture.access('admin')
    registry = ActionRegistry([])
    dialog = AccessDialog(admin, registry)
    assert dialog.permissions.rowCount() == 14
    default_row = next(row for row, (index, key, _) in enumerate(dialog.rows) if index == -1 and key == 'edit')
    library_row = next(row for row, (index, key, _) in enumerate(dialog.rows) if index == 0 and key == 'edit')
    users_column = dialog.groups.index('users') + 3
    dialog.permissions.cellWidget(default_row, users_column).setChecked(True)
    assert dialog.permissions.cellWidget(library_row, users_column).isChecked()
    dialog.save()
    assert dialog.result() == QtWidgets.QDialog.Accepted, dialog.message.text()
    assert admin.defaults()['edit'] == ['admins', 'managers', 'users']
    assert 'edit' not in admin.data['libraries'][0]['permissions']
    print('Common field Qt checks passed: visibility, multiline editing and studio inheritance.')
finally:
    fixture.doCleanups()
