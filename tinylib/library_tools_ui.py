"""Admin UI for long-running library maintenance checks."""
import time
import copy
from pathlib import Path

from .qt import QtCore, QtGui, QtWidgets
from .access import root_key
from .library_tools import (KEYWORD_REPLACEMENTS, keyword_list, LABEL_REPLACEMENTS, CrosscheckCancelled, clean_library_keywords,
                            crosscheck_library, create_new_library, label_library, plan_keywords, plan_labels,
                            sort_library)
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


class LabelWorker(QtCore.QThread):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(object)

    def __init__(self, library, settings, access, only_empty, rules, override=False, parent=None):
        super().__init__(parent)
        self.library, self.settings, self.access = library, settings, copy.copy(access)
        self.only_empty, self.rules, self.override = only_empty, rules, override

    def run(self):
        try:
            self.completed.emit(label_library(self.settings, self.access, self.library, self.only_empty,
                                              self.rules, self.override, self.progress.emit))
        except Exception as error:
            self.failed.emit(error)


class LabelsDialog(QtWidgets.QDialog):
    """Labels from names: make asset labels from asset names in one library, with a preview."""
    PREVIEW_ROWS = 500

    def __init__(self, libraries, assets, settings, access, current_root='', parent=None):
        super().__init__(parent)
        self.libraries, self.assets, self.settings, self.access = libraries, assets, settings, access
        self.result, self.error, self.worker = None, None, None
        self.setWindowTitle('Labels from names')
        self.resize(820, 640)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.library = QtWidgets.QComboBox()
        for library in libraries:
            self.library.addItem(library['name'], library)
        current = next((index for index, library in enumerate(libraries)
                        if current_root and root_key(library['root']) == root_key(current_root)), 0)
        self.library.setCurrentIndex(current)
        form.addRow('Library', self.library)
        self.only_empty = QtWidgets.QCheckBox('Only assets with an empty label')
        self.only_empty.setChecked(True)
        self.only_empty.setToolTip('Off: labels you or others set by hand are replaced too.')
        form.addRow('', self.only_empty)
        self.separators = QtWidgets.QCheckBox('Replace underscores and dashes with spaces')
        self.separators.setChecked(True)
        form.addRow('', self.separators)
        self.camel_case = QtWidgets.QCheckBox('Split words at capitals ("abandonedBakery" → "abandoned bakery")')
        self.camel_case.setChecked(True)
        form.addRow('', self.camel_case)
        self.replacements = []
        for index in range(LABEL_REPLACEMENTS):
            row = QtWidgets.QHBoxLayout()
            search, replace = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
            search.setPlaceholderText('Search (plain text, case-sensitive)')
            replace.setPlaceholderText('Replace with (empty: remove)')
            row.addWidget(search, 1)
            row.addWidget(QtWidgets.QLabel('→'))
            row.addWidget(replace, 1)
            form.addRow('Replace %d' % (index + 1), row)
            self.replacements.append((search, replace))
        layout.addLayout(form)
        order = QtWidgets.QLabel('Applied in order: split at capitals, underscores and dashes, then replacements '
                                 '1 to 3; repeated spaces become one. A label equal to the name is left empty.')
        order.setWordWrap(True)
        layout.addWidget(order)
        self.preview = QtWidgets.QTreeWidget()
        self.preview.setHeaderLabels(['Name', 'Current label', 'New label'])
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.setColumnWidth(0, 260)
        self.preview.setColumnWidth(1, 220)
        layout.addWidget(self.preview, 1)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.apply_button = buttons.addButton('Apply labels', QtWidgets.QDialogButtonBox.AcceptRole)
        self.apply_button.clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.close_button = buttons.button(QtWidgets.QDialogButtonBox.Close)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.update_preview)
        for widget in (self.only_empty, self.separators, self.camel_case):
            widget.toggled.connect(self.timer.start)
        for search, replace in self.replacements:
            search.textChanged.connect(self.timer.start)
            replace.textChanged.connect(self.timer.start)
        self.library.currentIndexChanged.connect(self.timer.start)
        self.update_preview()

    def rules(self):
        return {'separators': self.separators.isChecked(), 'camel_case': self.camel_case.isChecked(),
                'replacements': [(search.text(), replace.text()) for search, replace in self.replacements]}

    def library_assets(self):
        library = self.library.currentData()
        if library is None:
            return []
        return [asset for asset in self.assets if root_key(asset.get('library_root', '')) == root_key(library['root'])]

    def update_preview(self):
        self.preview.clear()
        assets = self.library_assets()
        try:
            plan = plan_labels(assets, self.only_empty.isChecked(), **self.rules())
        except ValueError as error:
            self.summary.setText('Cannot make labels: %s' % error)
            self.apply_button.setEnabled(False)
            return
        items = []
        for record, label in plan[:self.PREVIEW_ROWS]:
            items.append(QtWidgets.QTreeWidgetItem([str(record.get('name', '')), str(record.get('label') or ''),
                                                    label or '(empty: shows the name)']))
        self.preview.addTopLevelItems(items)
        shown = '' if len(plan) <= self.PREVIEW_ROWS else ' The first %d are listed.' % self.PREVIEW_ROWS
        self.summary.setText('%d of %d assets get a new label.%s' % (len(plan), len(assets), shown)
                             if plan else 'No asset in this library gets a new label.')
        self.apply_button.setEnabled(bool(plan) and self.library.currentData() is not None)

    def apply(self, override=False):
        library = self.library.currentData()
        if library is None:
            return
        self.error, self.result = None, None
        for widget in (self.apply_button, self.close_button, self.library):
            widget.setEnabled(False)
        self.progress_bar.show()
        self.worker = LabelWorker(library, self.settings, self.access, self.only_empty.isChecked(), self.rules(),
                                  override, self)
        self.worker.progress.connect(self.summary.setText)
        self.worker.completed.connect(lambda result: setattr(self, 'result', result))
        self.worker.failed.connect(lambda error: setattr(self, 'error', error))
        self.worker.finished.connect(self.finished_labels)
        self.worker.start()

    def finished_labels(self):
        self.progress_bar.hide()
        for widget in (self.close_button, self.library):
            widget.setEnabled(True)
        if self.result is not None:
            self.summary.setText('%d labels written to %s.' % (len(self.result['changes']),
                                                              self.library.currentData()['name']))
            self.apply_button.setEnabled(False)
            return
        if (isinstance(self.error, LibraryEditLocked) and self.access.is_admin
                and self.parentWidget() is not None
                and self.parentWidget().confirm_edit_override(self.error.locks)):
            return self.apply(override=True)
        self.summary.setText('Labels from names failed: ' + str(self.error))
        self.apply_button.setEnabled(True)

    def reject(self):
        if self.worker is None or not self.worker.isRunning():
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


class KeywordWorker(QtCore.QThread):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(object)

    def __init__(self, library, settings, access, asset_ids, remove_numbers, replacements, override=False,
                 parent=None, add=()):
        super().__init__(parent)
        self.library, self.settings, self.access = library, settings, copy.copy(access)
        self.asset_ids, self.remove_numbers, self.replacements = asset_ids, remove_numbers, replacements
        self.override, self.add = override, add

    def run(self):
        try:
            self.completed.emit(clean_library_keywords(self.settings, self.access, self.library, self.asset_ids,
                                                       self.remove_numbers, self.replacements, self.override,
                                                       self.progress.emit, self.add))
        except Exception as error:
            self.failed.emit(error)


class KeywordsDialog(QtWidgets.QDialog):
    """Keyword cleaner: one library at a time; only the selected assets when some of it is selected."""
    PREVIEW_ROWS = 500

    def __init__(self, libraries, assets, selected, settings, access, current_root='', parent=None):
        super().__init__(parent)
        self.libraries, self.assets, self.settings, self.access = libraries, assets, settings, access
        self.selected = selected
        self.result, self.error, self.worker = None, None, None
        self.setWindowTitle('Keyword cleaner')
        self.resize(820, 640)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.library = QtWidgets.QComboBox()
        for library in libraries:
            self.library.addItem(library['name'], library)
        roots = [current_root] + [asset.get('library_root', '') for asset in selected]
        current = next((index for root in roots if root for index, library in enumerate(libraries)
                        if root_key(library['root']) == root_key(root)), 0)
        self.library.setCurrentIndex(current)
        form.addRow('Library', self.library)
        self.scope = QtWidgets.QLabel()
        self.scope.setWordWrap(True)
        form.addRow('Works on', self.scope)
        self.remove_numbers = QtWidgets.QCheckBox('Remove keywords that are only a number (01, 2525, 1.5)')
        self.remove_numbers.setChecked(True)
        form.addRow('', self.remove_numbers)
        self.replacements = []
        for index in range(KEYWORD_REPLACEMENTS):
            row = QtWidgets.QHBoxLayout()
            search, replace = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
            search.setPlaceholderText('Keyword (whole keyword, any case)')
            replace.setPlaceholderText('Replace with (empty: remove; commas: several)')
            row.addWidget(search, 1)
            row.addWidget(QtWidgets.QLabel('→'))
            row.addWidget(replace, 1)
            form.addRow('Replace %d' % (index + 1), row)
            self.replacements.append((search, replace))
        self.add = QtWidgets.QLineEdit()
        self.add.setPlaceholderText('Keywords to add to every asset in scope, separated by commas')
        form.addRow('Add keywords', self.add)
        layout.addLayout(form)
        self.preview = QtWidgets.QTreeWidget()
        self.preview.setHeaderLabels(['Asset', 'Keywords now', 'New keywords'])
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.setColumnWidth(0, 230)
        self.preview.setColumnWidth(1, 260)
        layout.addWidget(self.preview, 1)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.apply_button = buttons.addButton('Apply keywords', QtWidgets.QDialogButtonBox.AcceptRole)
        self.apply_button.clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.close_button = buttons.button(QtWidgets.QDialogButtonBox.Close)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.update_preview)
        self.remove_numbers.toggled.connect(self.timer.start)
        self.add.textChanged.connect(self.timer.start)
        for search, replace in self.replacements:
            search.textChanged.connect(self.timer.start)
            replace.textChanged.connect(self.timer.start)
        self.library.currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def rules(self):
        return {'remove_numbers': self.remove_numbers.isChecked(),
                'replacements': [(search.text(), replace.text()) for search, replace in self.replacements],
                'add': keyword_list(self.add.text())}

    def in_library(self, assets):
        library = self.library.currentData()
        if library is None:
            return []
        return [asset for asset in assets if root_key(asset.get('library_root', '')) == root_key(library['root'])]

    def scope_assets(self):
        """(assets in scope, selected only): the selected assets of this library, or all of it."""
        selected = self.in_library(self.selected)
        return (selected, True) if selected else (self.in_library(self.assets), False)

    def update_preview(self):
        self.preview.clear()
        assets, only_selected = self.scope_assets()
        library = self.library.currentData() or {'name': ''}
        self.scope.setText(('<b>%d selected asset%s</b> in %s. Clear the selection to clean the whole library.'
                            % (len(assets), '' if len(assets) == 1 else 's', library['name'])) if only_selected else
                           ('<b>The whole library</b>: %d assets in %s (nothing of it is selected).'
                            % (len(assets), library['name'])))
        plan = plan_keywords(assets, **self.rules())
        items = [QtWidgets.QTreeWidgetItem([str(record.get('label') or record.get('name', '')),
                                            ', '.join(record.get('tags') or []), ', '.join(tags) or '(none)'])
                 for record, tags in plan[:self.PREVIEW_ROWS]]
        self.preview.addTopLevelItems(items)
        shown = '' if len(plan) <= self.PREVIEW_ROWS else ' The first %d are listed.' % self.PREVIEW_ROWS
        self.summary.setText('%d of %d assets get new keywords.%s' % (len(plan), len(assets), shown)
                             if plan else 'No keyword changes.')
        self.apply_button.setEnabled(bool(plan))

    def apply(self, override=False):
        library = self.library.currentData()
        if library is None:
            return
        assets, only_selected = self.scope_assets()
        ids = [asset.get('id') for asset in assets] if only_selected else None
        self.error, self.result = None, None
        for widget in (self.apply_button, self.close_button, self.library):
            widget.setEnabled(False)
        self.progress_bar.show()
        rules = self.rules()
        self.worker = KeywordWorker(library, self.settings, self.access, ids, rules['remove_numbers'],
                                    rules['replacements'], override, self, rules['add'])
        self.worker.progress.connect(self.summary.setText)
        self.worker.completed.connect(lambda result: setattr(self, 'result', result))
        self.worker.failed.connect(lambda error: setattr(self, 'error', error))
        self.worker.finished.connect(self.finished_keywords)
        self.worker.start()

    def finished_keywords(self):
        self.progress_bar.hide()
        for widget in (self.close_button, self.library):
            widget.setEnabled(True)
        if self.result is not None:
            self.summary.setText('Keywords of %d assets saved in %s.' % (len(self.result['changes']),
                                                                         self.library.currentData()['name']))
            self.apply_button.setEnabled(False)
            return
        if (isinstance(self.error, LibraryEditLocked) and self.access.is_admin
                and self.parentWidget() is not None
                and self.parentWidget().confirm_edit_override(self.error.locks)):
            return self.apply(override=True)
        self.summary.setText('Keyword cleaner failed: ' + str(self.error))
        self.apply_button.setEnabled(True)

    def reject(self):
        if self.worker is None or not self.worker.isRunning():
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


class CategoryChangeDialog(QtWidgets.QDialog):
    """Category change: where the selected assets of one library move to. Only asks; the
    browser moves them (in edit mode, with its library edit lock)."""

    def __init__(self, library, assets, categories, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Category change')
        self.setMinimumWidth(560)
        self.assets = assets
        layout = QtWidgets.QVBoxLayout(self)
        current = sorted({asset.get('category', '') for asset in assets})
        intro = QtWidgets.QLabel('<b>%d asset(s)</b> in <b>%s</b>, now in: %s'
                                 % (len(assets), library['name'], ', '.join(current)))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QtWidgets.QFormLayout()
        self.existing = QtWidgets.QRadioButton('Existing category')
        self.category = QtWidgets.QComboBox()
        self.category.addItems(categories)
        self.new = QtWidgets.QRadioButton('New category')
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText('Folder name of the new category')
        form.addRow(self.existing, self.category)
        form.addRow(self.new, self.name)
        layout.addLayout(form)
        (self.existing if categories else self.new).setChecked(True)
        self.existing.setEnabled(bool(categories))
        self.destination = QtWidgets.QLabel()
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        note = QtWidgets.QLabel(
            'Each asset folder is moved on disk to <i>&lt;library&gt;/&lt;category&gt;/&lt;asset name&gt;</i> and its '
            'record is updated. Nothing is copied or deleted. If a folder cannot be moved (for example a file '
            'in it is open), nothing is changed. Your own ratings, in/out points and collection entries follow '
            'the moved assets.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        layout.addWidget(note)
        warning = QtWidgets.QLabel(
            '⚠ <b>Other users lose their ratings, in/out points and collection entries for these assets.</b> '
            'They are stored with the old path: their collections show the moved assets as unavailable.')
        warning.setWordWrap(True)
        warning.setStyleSheet('color: #ffa02f;')
        layout.addWidget(warning)
        self.message = QtWidgets.QLabel()
        self.message.setStyleSheet('color: #ff6060;')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.move_button = buttons.addButton('Move assets', QtWidgets.QDialogButtonBox.AcceptRole)
        self.move_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for signal in (self.existing.toggled, self.category.currentTextChanged, self.name.textChanged):
            signal.connect(self.update_destination)
        self.name.textEdited.connect(lambda _: self.new.setChecked(True))
        self.update_destination()

    def chosen(self):
        return (self.category.currentText() if self.existing.isChecked() else self.name.text()).strip()

    def problem(self):
        from .library import safe_component
        category = self.chosen()
        if not category:
            return 'Choose or enter a category.'
        try:
            safe_component(category)
        except ValueError:
            return 'A category is one folder name: no \\ / : * ? " < > | and no trailing dot or space.'
        if category.startswith('.'):
            return 'A category name cannot start with a dot.'
        if all(asset.get('category') == category for asset in self.assets):
            return 'The assets are already in %s.' % category
        return ''

    def update_destination(self, *_):
        self.category.setEnabled(self.existing.isChecked())
        problem = self.problem()
        self.message.setText(problem)
        self.move_button.setEnabled(not problem)
        staying = sum(asset.get('category') == self.chosen() for asset in self.assets)
        self.destination.setText('' if problem else 'Moves %d asset(s) to <b>%s</b>%s.' % (
            len(self.assets) - staying, self.chosen(),
            ' (%d already there stay)' % staying if staying else ''))

    def accept(self):
        if not self.problem():
            super().accept()
