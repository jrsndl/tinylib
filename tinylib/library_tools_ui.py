"""Admin UI for long-running library maintenance checks."""
import time
import copy
from pathlib import Path

from .qt import QtCore, QtGui, QtWidgets
from .library_tools import CrosscheckCancelled, crosscheck_library, sort_library, create_new_library
from .edit_lock import LibraryEditLocked
from .metadata_scan import format_scan_summary, scan_assets


class CrosscheckWorker(QtCore.QThread):
    progress = QtCore.Signal(int, int, str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(self, library, parent=None, access=None):
        super().__init__(parent)
        self.library = library
        self.access = access

    def run(self):
        last_emit = [0.0, -1]

        def report(done, total, detail):
            now = time.monotonic()
            if done != last_emit[1] or now - last_emit[0] >= .08:
                self.progress.emit(done, total, detail)
                last_emit[:] = [now, done]

        try:
            result = crosscheck_library(
                self.library['root'], self.library['name'], progress=report,
                cancelled=self.isInterruptionRequested, access=self.access)
            self.completed.emit(result)
        except CrosscheckCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


class CrosscheckDialog(QtWidgets.QDialog):
    def __init__(self, library, parent=None, access=None):
        super().__init__(parent)
        self.library = library
        self.crosscheck_result = None
        self.setWindowTitle('Library crosscheck · ' + library['name'])
        self.resize(720, 430)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel('Preparing crosscheck…')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1)
        layout.addWidget(self.progress_bar)
        self.detail = QtWidgets.QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText('Scanning details will appear here.')
        layout.addWidget(self.detail, 1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        self.cancel_button = QtWidgets.QPushButton('Cancel')
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.close_button = QtWidgets.QPushButton('Close')
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.accept)
        row.addWidget(self.cancel_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.worker = CrosscheckWorker(library, self, access)
        self.worker.progress.connect(self.show_progress)
        self.worker.completed.connect(self.complete)
        self.worker.failed.connect(self.fail)
        self.worker.cancelled.connect(self.was_cancelled)
        QtCore.QTimer.singleShot(0, self.worker.start)

    def show_progress(self, done, total, detail):
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self.summary.setText('Checked %d of %d records and folders' % (done, total))
        if not self.detail.document().isEmpty():
            self.detail.appendPlainText(detail)
        else:
            self.detail.setPlainText(detail)
        while self.detail.document().blockCount() > 250:
            cursor = self.detail.textCursor()
            cursor.movePosition(QtGui.QTextCursor.Start)
            cursor.select(QtGui.QTextCursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
        self.detail.verticalScrollBar().setValue(self.detail.verticalScrollBar().maximum())

    def complete(self, result):
        self.crosscheck_result = result
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.summary.setText(
            'Complete · %d missing paths in %d JSON records · %d unregistered asset folders' %
            (result['missing_paths'], result['missing_records'], result['orphan_folders']))
        try:
            report = Path(result['log_path']).read_text(encoding='utf-8-sig')
        except OSError as error:
            report = 'The crosscheck completed, but the saved report could not be displayed:\n' + str(error)
        self.detail.setPlainText(report.rstrip() + '\n\nLog saved to:\n' + result['log_path'])
        self.detail.moveCursor(QtGui.QTextCursor.Start)
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)

    def fail(self, message):
        self.summary.setText('Crosscheck failed')
        self.detail.appendPlainText('\n' + message)
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)

    def was_cancelled(self):
        self.summary.setText('Crosscheck cancelled')
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)

    def cancel_scan(self):
        self.worker.requestInterruption()
        self.cancel_button.setEnabled(False)
        self.summary.setText('Cancelling after the current filesystem check…')

    def reject(self):
        # Esc goes through reject(), not closeEvent(): cancel first, close once the thread ends.
        if self.worker.isRunning():
            self.cancel_scan()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.cancel_scan()
            event.ignore()
            return
        super().closeEvent(event)


class MetadataScanWorker(QtCore.QThread):
    progress = QtCore.Signal(int, int, str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, assets, settings, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.settings = settings

    def run(self):
        try:
            self.completed.emit(scan_assets(self.assets, self.settings, self.progress.emit,
                                            self.isInterruptionRequested))
        except Exception as error:
            self.failed.emit(str(error))


class MetadataRescanDialog(QtWidgets.QDialog):
    def __init__(self, assets, settings, parent=None):
        super().__init__(parent)
        self.scan_result = None
        self.setWindowTitle('Rescan metadata')
        self.resize(760, 500)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel('Preparing metadata scan for %d asset(s)…' % len(assets))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, max(len(assets), 1))
        layout.addWidget(self.progress_bar)
        self.detail = QtWidgets.QPlainTextEdit()
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail, 1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        self.cancel_button = QtWidgets.QPushButton('Cancel')
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.write_button = QtWidgets.QPushButton('Write metadata')
        self.write_button.setEnabled(False)
        self.write_button.clicked.connect(self.accept)
        self.close_button = QtWidgets.QPushButton('Close')
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button)
        row.addWidget(self.write_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.worker = MetadataScanWorker(list(assets), settings, self)
        self.worker.progress.connect(self.show_progress)
        self.worker.completed.connect(self.complete)
        self.worker.failed.connect(self.fail)
        QtCore.QTimer.singleShot(0, self.worker.start)

    def show_progress(self, done, total, detail):
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self.summary.setText('Metadata rescan progress %d of %d' % (done, total))
        self.detail.setPlainText(detail)

    def complete(self, result):
        self.scan_result = result
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.summary.setText('%d discrepancy asset(s), %d metadata sidecar(s), %d scan error(s)' %
                             (len(result['results']), len(result.get('sidecars', [])),
                              len(result['errors'])))
        self.detail.setPlainText(format_scan_summary(result))
        self.detail.moveCursor(QtGui.QTextCursor.Start)
        self.cancel_button.setEnabled(False)
        self.write_button.setEnabled(bool(result['results']))
        self.close_button.setEnabled(True)

    def fail(self, message):
        self.summary.setText('Metadata scan failed')
        self.detail.setPlainText(message)
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)

    def cancel_scan(self):
        self.worker.requestInterruption()
        self.cancel_button.setEnabled(False)
        self.summary.setText('Cancelling after the current metadata probe…')

    def reject(self):
        # Esc goes through reject(), not closeEvent(): cancel first, close once the thread ends.
        if self.worker.isRunning():
            self.cancel_scan()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.cancel_scan()
            event.ignore()
            return
        super().closeEvent(event)


class LibrarySortWorker(QtCore.QThread):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(object)

    def __init__(self, library, settings, access, override=False, parent=None):
        super().__init__(parent)
        self.library, self.settings = library, settings
        self.access, self.override = copy.copy(access), override

    def run(self):
        try:
            self.completed.emit(sort_library(self.settings, self.access, self.library,
                                             self.override, self.progress.emit))
        except Exception as error:
            self.failed.emit(error)


class LibrarySortDialog(QtWidgets.QDialog):
    def __init__(self, library, settings, access, parent=None):
        super().__init__(parent)
        self.library, self.settings, self.access = library, settings, access
        self.sort_result, self.error = None, None
        self.setWindowTitle('Library sort · ' + library['name'])
        self.resize(520, 150)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel('Preparing library sort…')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)
        self.close_button = QtWidgets.QPushButton('Close')
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        self.start_sort()

    def start_sort(self, override=False):
        self.error = None
        self.worker = LibrarySortWorker(self.library, self.settings, self.access, override, self)
        self.worker.progress.connect(self.summary.setText)
        self.worker.completed.connect(self.complete)
        self.worker.failed.connect(self.fail)
        self.worker.finished.connect(self.finished_sort)
        self.worker.start()

    def complete(self, result):
        self.sort_result = result

    def fail(self, error):
        self.error = error

    def finished_sort(self):
        if self.sort_result is not None:
            self.accept()
        elif (isinstance(self.error, LibraryEditLocked) and self.access.is_admin
              and self.parentWidget() is not None
              and self.parentWidget().confirm_edit_override(self.error.locks)):
            self.start_sort(override=True)
        else:
            self.summary.setText('Library sort failed: ' + str(self.error))
            self.progress_bar.setRange(0, 1)
            self.close_button.setEnabled(True)

    def reject(self):
        if not self.worker.isRunning():
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


class CreateLibraryWorker(QtCore.QThread):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, settings, access, name, root, template, read_only, parent):
        super().__init__(parent)
        self.settings, self.access = settings, copy.copy(access)
        self.name, self.root, self.template, self.read_only = name, root, template, read_only

    def run(self):
        try:
            self.completed.emit(create_new_library(self.settings, self.access, self.name, self.root,
                                                   self.template, self.read_only, self.progress.emit))
        except Exception as error:
            self.failed.emit(str(error))


class CreateLibraryDialog(QtWidgets.QDialog):
    def __init__(self, settings, access, parent=None):
        super().__init__(parent)
        self.settings, self.access = settings, access
        self.created_library, self.worker = None, None
        self.setWindowTitle('Create new library')
        self.resize(600, 240)
        layout = QtWidgets.QVBoxLayout(self)
        self.inputs = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.inputs)
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText('New library name')
        form.addRow('Library name', self.name)
        folder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(folder)
        row.setContentsMargins(0, 0, 0, 0)
        self.root = QtWidgets.QLineEdit()
        browse = QtWidgets.QPushButton('Choose folder…')
        browse.clicked.connect(self.browse)
        row.addWidget(self.root, 1)
        row.addWidget(browse)
        form.addRow('Root folder', folder)
        self.template = QtWidgets.QComboBox()
        self.template.addItem('Studio defaults', None)
        for library in settings['libraries']:
            self.template.addItem(library['name'], library['root'])
        self.template.currentIndexChanged.connect(self.template_changed)
        form.addRow('Copy settings from', self.template)
        self.read_only = QtWidgets.QCheckBox('Read-only library')
        form.addRow('', self.read_only)
        self.template_changed()
        layout.addWidget(self.inputs)
        self.message = QtWidgets.QLabel('Creates an empty tinylib_data.json and adds the library to the studio. '
                                       'Tool paths and processing profiles use the shared studio settings.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.create_button = buttons.addButton('Create library', QtWidgets.QDialogButtonBox.AcceptRole)
        self.create_button.clicked.connect(self.create_library)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def template_changed(self, *_):
        root = self.template.currentData()
        source = next((item for item in self.settings['libraries'] if item['root'] == root),
                      self.settings.get('library_defaults', {}))
        self.read_only.setChecked(bool(source.get('read_only', False)))

    def browse(self):
        root = QtWidgets.QFileDialog.getExistingDirectory(self, 'New library root folder', self.root.text())
        if root:
            self.root.setText(root)
            if not self.name.text().strip():
                self.name.setText(Path(root).name)

    def create_library(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.name.text().strip() or not self.root.text().strip():
            self.message.setText('Enter a library name and choose a root folder.')
            return
        self.inputs.setEnabled(False)
        self.create_button.setEnabled(False)
        self.worker = CreateLibraryWorker(self.settings, self.access, self.name.text(), self.root.text(),
                                          self.template.currentData(), self.read_only.isChecked(), self)
        self.worker.progress.connect(self.message.setText)
        self.worker.completed.connect(self.complete)
        self.worker.failed.connect(self.message.setText)
        self.worker.finished.connect(self.finished_creation)
        self.worker.start()

    def complete(self, library):
        self.created_library = library
        if not self.access.persist:
            self.access._memory = copy.deepcopy(self.worker.access._memory)
            self.access.refresh()

    def finished_creation(self):
        if self.created_library is not None:
            self.accept()
        else:
            self.inputs.setEnabled(True)
            self.create_button.setEnabled(True)

    def reject(self):
        if self.worker is None or not self.worker.isRunning():
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)
