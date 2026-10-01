"""Offscreen ingest-dialog checks for the versioned asset type schema."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tinylib.qt import QtWidgets
from tinylib.access import AccessControl
from tinylib.ingest_ui import IngestDialog


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    settings = {
        'libraries': [{'name': 'Writable', 'root': str(root)}],
        'profiles': {'preview': {}}, 'colorspaces': ['ACES - ACEScg'],
        'action_roots': [], '_config_path': str(root / 'studio.json')
    }
    settings['_access'] = AccessControl(settings, identity='tester', persist=False)
    dialog = IngestDialog(settings, [])
    dialog.setStyleSheet((Path(__file__).resolve().parents[1] / 'tinylib/standalone.qss').read_text(encoding='utf-8'))
    values = [dialog.kind.itemText(index) for index in range(dialog.kind.count())]
    assert values == ['Still', 'Footage', 'Model', 'Folder', 'Splat', 'PDF', 'Material'], values
    assert dialog.kind.currentData() == 'footage'
    assert dialog.new_category_name.text() == 'foo'
    dialog.new_category.setChecked(True)
    assert not dialog.category.isEnabled() and dialog.new_category_name.isEnabled()
    for frame in (1001, 1002):
        (root / ('MyBigFire_v001.%04d.exr' % frame)).touch()
    dialog.source_selected(str(root / 'MyBigFire_v001.1001.exr'))
    assert dialog.source.text().endswith('MyBigFire_v001.####.exr 1001-1002')
    assert dialog.name.text() == 'MyBigFire'
    assert dialog.tags.text() == 'my, big, fire'
    dialog.kind.setCurrentIndex(dialog.kind.findData('model'))
    assert dialog.media['thumb'][0].currentText() == 'supply'
    assert dialog.media['proxy'][0].currentText() == 'omit'
    assert not dialog.color.isEnabled()
    dialog.kind.setCurrentIndex(dialog.kind.findData('footage'))
    assert dialog.media['thumb'][0].currentText() == 'generate'
    assert dialog.media['filmstrip'][0].currentText() == 'generate'
    assert dialog.color.isEnabled() and dialog.fps.isEnabled()
    dialog.show()
    app.processEvents()
    artifact = Path(__file__).resolve().parents[1] / 'artifacts' / 'ingest-asset-types.png'
    artifact.parent.mkdir(exist_ok=True)
    assert dialog.grab().save(str(artifact))
    dialog.close()

print('Asset type UI passed')
