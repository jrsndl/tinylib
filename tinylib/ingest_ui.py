"""Ingest form: independent supplied/generated media and a reviewable manifest."""
import json
from pathlib import Path
from .qt import QtCore, QtWidgets
from .ingest import make_manifest, save_manifest, submit_deadline
from .library import detect_sequence


class PathField(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit()
        button = QtWidgets.QPushButton('Browse…')
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)
        button.clicked.connect(self.browse)

    def browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Choose media')
        if path:
            self.edit.setText(path)

    def text(self):
        return self.edit.text().strip()


class Submission(QtCore.QThread):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, manifest, settings, parent):
        super().__init__(parent)
        self.manifest, self.settings = manifest, settings

    def run(self):
        try:
            self.done.emit(submit_deadline(self.manifest, self.settings))
        except Exception as error:
            self.failed.emit(str(error))


class IngestDialog(QtWidgets.QDialog):
    def __init__(self, settings, assets, parent=None):
        super().__init__(parent)
        self.settings, self.assets = settings, assets
        self.setWindowTitle('Ingest asset')
        self.resize(760, 800)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel('Add an asset to the library')
        title.setObjectName('heading')
        layout.addWidget(title)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.library = QtWidgets.QComboBox()
        for library in settings['libraries']:
            if not library.get('read_only'):
                self.library.addItem(library['name'], library)
        form.addRow('Destination library', self.library)
        self.name = QtWidgets.QLineEdit()
        form.addRow('Asset name', self.name)
        self.category = QtWidgets.QComboBox()
        self.category.setEditable(True)
        self.category.addItems(sorted({a['category'] for a in assets}))
        form.addRow('Main category', self.category)
        self.source = PathField()
        form.addRow('Main image / sequence', self.source)
        detect = QtWidgets.QPushButton('Detect sequence from selected frame')
        detect.clicked.connect(self.detect)
        form.addRow('', detect)
        hint = QtWidgets.QLabel('Sequence syntax: name.####.exr 1001-1100   ·   A single file stays a still.')
        hint.setWordWrap(True)
        form.addRow('', hint)
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(['auto', 'still', 'hdri', 'footage'])
        form.addRow('Asset type', self.kind)
        self.fps = QtWidgets.QDoubleSpinBox()
        self.fps.setRange(.001, 240)
        self.fps.setDecimals(3)
        self.fps.setValue(24)
        form.addRow('Sequence FPS', self.fps)
        self.highres = PathField()
        form.addRow('Highres (optional)', self.highres)
        self.color = QtWidgets.QComboBox()
        self.color.setEditable(True)
        self.color.addItems(settings.get('colorspaces', ['ACES - ACEScg']))
        form.addRow('Main color space', self.color)
        self.tags = QtWidgets.QLineEdit()
        self.tags.setPlaceholderText('Comma-separated keywords; new keywords are welcome')
        form.addRow('Keywords', self.tags)
        self.known_tags = QtWidgets.QComboBox()
        self.known_tags.addItem('Add an existing keyword…')
        self.known_tags.addItems(sorted({t for a in assets for t in a.get('tags', [])}))
        self.known_tags.activated.connect(self.add_tag)
        form.addRow('', self.known_tags)
        self.profile = QtWidgets.QComboBox()
        self.profile.addItems(list(settings.get('profiles', {})))
        form.addRow('Processing profile', self.profile)
        self.preview_source = QtWidgets.QComboBox()
        self.preview_source.addItems(['main', 'proxy'])
        form.addRow('Generate thumb / strip from', self.preview_source)
        self.media = {}
        for key, label in [('proxy', 'Proxy · 1920 × 1080'), ('thumb', 'Thumbnail · 960 × 506'), ('filmstrip', 'Filmstrip · 24 × 480 × 270')]:
            field = QtWidgets.QWidget()
            row = QtWidgets.QVBoxLayout(field)
            row.setContentsMargins(0, 0, 0, 0)
            mode = QtWidgets.QComboBox()
            mode.addItems(['generate', 'supply'])
            path = PathField()
            path.setEnabled(False)
            mode.currentTextChanged.connect(lambda text, widget=path: widget.setEnabled(text == 'supply'))
            row.addWidget(mode)
            row.addWidget(path)
            form.addRow(label, field)
            self.media[key] = mode, path
        note = QtWidgets.QLabel('Filmstrips are omitted for stills/HDRIs. Supplied previews must already be Rec.709. '
                              'Main and highres files are copied without changing their pixels.')
        note.setWordWrap(True)
        form.addRow('', note)
        self.status = QtWidgets.QLabel('Choose inputs, then review the job before submission.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        self.review_button = QtWidgets.QPushButton('Review job…')
        self.export_button = QtWidgets.QPushButton('Save job manifest…')
        self.submit_button = QtWidgets.QPushButton('Submit to Deadline')
        self.submit_button.setObjectName('primary')
        self.review_button.clicked.connect(self.review)
        self.export_button.clicked.connect(self.export)
        self.submit_button.clicked.connect(self.submit)
        for button in (self.review_button, self.export_button, self.submit_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        if not self.library.count():
            self.status.setText('No writable library is configured. Add one to the studio configuration.')
            self.submit_button.setEnabled(False)

    def detect(self):
        try:
            self.source.edit.setText(detect_sequence(self.source.text()))
        except Exception as error:
            self.error(str(error))

    def add_tag(self, index):
        if index:
            tags = [t.strip() for t in self.tags.text().split(',') if t.strip()]
            tag = self.known_tags.itemText(index)
            if tag not in tags:
                self.tags.setText(', '.join(tags + [tag]))
            self.known_tags.setCurrentIndex(0)

    def manifest(self):
        if self.library.currentData() is None:
            raise ValueError('No writable library is configured.')
        return make_manifest(self.settings, self.library.currentData(), self.name.text(),
            self.category.currentText(), self.source.text(), self.tags.text().split(','),
            self.color.currentText(), self.profile.currentText(),
            {k: {'mode': mode.currentText(), 'path': path.text()} for k, (mode, path) in self.media.items()},
            self.fps.value(), self.highres.text(), self.preview_source.currentText(), self.kind.currentText())

    def error(self, message):
        self.status.setText(message)

    def review(self):
        try:
            job = self.manifest()
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle('Review ingest job')
            dialog.resize(700, 600)
            layout = QtWidgets.QVBoxLayout(dialog)
            text = QtWidgets.QPlainTextEdit(json.dumps(job, indent=2))
            text.setReadOnly(True)
            layout.addWidget(text)
            dialog.exec_() if hasattr(dialog, 'exec_') else dialog.exec()
        except Exception as error:
            self.error(str(error))

    def export(self):
        try:
            job = self.manifest()
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Save manifest folder')
            if folder:
                self.status.setText('Saved ' + str(save_manifest(job, folder)))
        except Exception as error:
            self.error(str(error))

    def submit(self):
        try:
            job = self.manifest()
        except Exception as error:
            return self.error(str(error))
        self.submit_button.setEnabled(False)
        self.status.setText('Submitting to Deadline…')
        self.submit_thread = Submission(job, self.settings, self)
        self.submit_thread.done.connect(lambda result: self.status.setText('Submitted job ' + result[0] + '\n' + str(result[1])))
        self.submit_thread.failed.connect(self.error)
        self.submit_thread.finished.connect(lambda: self.submit_button.setEnabled(True))
        self.submit_thread.start()

    def reject(self):
        if hasattr(self, 'submit_thread') and self.submit_thread.isRunning():
            self.status.setText('Waiting for Deadline submission to finish…')
            return
        super().reject()

    def closeEvent(self, event):
        if hasattr(self, 'submit_thread') and self.submit_thread.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)
