"""Admin UI for long-running library maintenance checks."""
import time
from pathlib import Path

from .qt import QtCore, QtGui, QtWidgets
from .library_tools import CrosscheckCancelled, crosscheck_library
from .metadata_scan import format_scan_summary, scan_assets


class CrosscheckWorker(QtCore.QThread):
    progress = QtCore.Signal(int, int, str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library

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
                cancelled=self.isInterruptionRequested)
            self.completed.emit(result)
        except CrosscheckCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


class CrosscheckDialog(QtWidgets.QDialog):
    def __init__(self, library, parent=None):
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
        self.worker = CrosscheckWorker(library, self)
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
        self.setWindowTitle('Rescan Metadata')
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

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.cancel_scan()
            event.ignore()
            return
        super().closeEvent(event)
