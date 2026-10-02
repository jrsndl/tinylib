"""Qt progress and completion behavior for the admin library crosscheck."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtCore, QtWidgets
from tinylib.library import atomic_json
from tinylib.library_tools_ui import CrosscheckDialog, MetadataRescanDialog

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
temporary = tempfile.TemporaryDirectory()
folder = Path(temporary.name)
library = folder / 'library'
(library / 'category' / 'Asset' / 'main').mkdir(parents=True)
(library / 'category' / 'Asset' / 'main' / 'image.jpg').write_bytes(b'x')
atomic_json(library / 'tinylib_data.json', {'schema_version': 3, 'assets': [{
    'id': 'category/Asset', 'name': 'Asset', 'category': 'category', 'kind': 'still',
    'main': 'category/Asset/main/image.jpg', 'thumb': 'category/Asset/thumb/missing.jpg',
    'tags': [], 'colorspace': 'ACEScg', 'metadata': {},
}]})

with patch('tinylib.library_tools.download_folder', return_value=folder / 'Downloads'):
    dialog = CrosscheckDialog({'name': 'Qt Test', 'root': str(library)})
    dialog.show()
    timer = QtCore.QElapsedTimer()
    timer.start()
    while not dialog.close_button.isEnabled() and timer.elapsed() < 5000:
        app.processEvents()
        QtCore.QThread.msleep(2)
    app.processEvents()
    dialog.worker.wait(1000)

assert dialog.crosscheck_result and Path(dialog.crosscheck_result['log_path']).is_file()
assert dialog.progress_bar.value() == dialog.progress_bar.maximum()
assert 'missing paths' in dialog.summary.text()
displayed_log = dialog.detail.toPlainText()
assert 'TinyLib library crosscheck' in displayed_log
assert 'JSON RECORDS WITH MISSING FILES (1)' in displayed_log
assert 'ASSET FOLDERS WITHOUT JSON RECORDS (0)' in displayed_log
assert 'thumb' in displayed_log and 'missing.jpg' in displayed_log
assert 'Log saved to:' in displayed_log
assert dialog.close_button.isEnabled() and not dialog.cancel_button.isEnabled()
dialog.close()

asset = {'id': 'category/Asset', 'name': 'Asset', 'category': 'category', 'kind': 'still',
         'main': str(library / 'category' / 'Asset' / 'main' / 'image.jpg'),
         'library_root': str(library), 'metadata': {'width': 10}}
scan_result = {'scanned': 1, 'errors': [], 'results': [{
    'asset': asset, 'scanned': {'width': 20, 'height': 10},
    'differences': {'width': {'stored': 10, 'scanned': 20},
                    'height': {'stored': None, 'scanned': 10}},
}]}
with patch('tinylib.library_tools_ui.scan_assets', return_value=scan_result):
    metadata_dialog = MetadataRescanDialog([asset], {'tools': {}})
    metadata_dialog.show()
    timer.restart()
    while not metadata_dialog.close_button.isEnabled() and timer.elapsed() < 5000:
        app.processEvents()
        QtCore.QThread.msleep(2)
    app.processEvents()
    metadata_dialog.worker.wait(1000)

assert metadata_dialog.scan_result == scan_result
assert metadata_dialog.write_button.isEnabled()
assert "width: 10  ->  20" in metadata_dialog.detail.toPlainText()
metadata_dialog.write_button.click()
assert metadata_dialog.result() == QtWidgets.QDialog.Accepted
print('Library tools UI passed: progress, detail, report path and completion summary')
