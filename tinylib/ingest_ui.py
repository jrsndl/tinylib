"""Ingest form: independent supplied/generated media and a reviewable manifest."""
import json
from pathlib import Path
from .qt import QtCore, QtWidgets
from .views import heading_font
from .ingest import (clean_asset_name, keywords_from_name, make_manifest,
                     prepare_source, save_manifest, submit_deadline)
from .asset_types import ASSET_TYPES, default_metadata


class PathField(QtWidgets.QWidget):
    selected = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit()
        button = QtWidgets.QPushButton('File…')
        folder_button = QtWidgets.QPushButton('Folder…')
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)
        layout.addWidget(folder_button)
        button.clicked.connect(self.browse)
        folder_button.clicked.connect(self.browse_folder)

    def browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Choose media')
        if path:
            self.edit.setText(path)
            self.selected.emit(path)

    def browse_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, 'Choose folder')
        if path:
            self.edit.setText(path)
            self.selected.emit(path)

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
        self.setAcceptDrops(True)
        self.setWindowTitle('Ingest asset')
        self.resize(760, 800)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel('Add an asset to the library')
        title.setObjectName('heading')
        title.setFont(heading_font())
        layout.addWidget(title)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.library = QtWidgets.QComboBox()
        for library in settings['libraries']:
            access = settings.get('_access')
            if not library.get('read_only') and (not access or (access.can(library['root'], 'view') and access.can(library['root'], 'ingest'))):
                self.library.addItem(library['name'], library)
        form.addRow('Destination library', self.library)

        self.kind = QtWidgets.QComboBox()
        for identifier, definition in ASSET_TYPES.items():
            self.kind.addItem(definition['label'], identifier)
        self.kind.setCurrentIndex(self.kind.findData('footage'))
        form.addRow('Asset type', self.kind)

        self.category = QtWidgets.QComboBox()
        self.new_category = QtWidgets.QCheckBox('Create new')
        self.new_category_name = QtWidgets.QLineEdit('foo')
        self.new_category_name.setEnabled(False)
        category_widget = QtWidgets.QWidget()
        category_row = QtWidgets.QHBoxLayout(category_widget)
        category_row.setContentsMargins(0, 0, 0, 0)
        category_row.addWidget(self.category, 1)
        category_row.addWidget(self.new_category)
        category_row.addWidget(self.new_category_name, 1)
        form.addRow('Main category', category_widget)
        self.new_category.toggled.connect(self.toggle_new_category)
        self.library.currentIndexChanged.connect(self.refresh_categories)
        self.refresh_categories()

        self.source = PathField()
        self.source.edit.setAcceptDrops(False)
        self.source.selected.connect(self.source_selected)
        form.addRow('Main', self.source)
        hint = QtWidgets.QLabel('Choose a still, sequence frame, footage container, asset file, or folder. '
                                'Footage image sequences are detected automatically.')
        hint.setWordWrap(True)
        form.addRow('', hint)

        self.name = QtWidgets.QLineEdit()
        form.addRow('Asset name', self.name)
        self.source.edit.editingFinished.connect(lambda: self.source_selected(self.source.text()))
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
        for key, label in [('proxy', 'Proxy'), ('thumb', 'Thumbnail · 960 × 506'),
                           ('filmstrip', 'Filmstrip · 24 × 480 × 270'), ('scene', 'Scene')]:
            field = QtWidgets.QWidget()
            row = QtWidgets.QVBoxLayout(field)
            row.setContentsMargins(0, 0, 0, 0)
            mode = QtWidgets.QComboBox()
            mode.addItems(['omit', 'generate', 'supply'])
            path = PathField()
            path.setEnabled(False)
            mode.currentTextChanged.connect(lambda text, widget=path: widget.setEnabled(text == 'supply'))
            row.addWidget(mode)
            row.addWidget(path)
            form.addRow(label, field)
            self.media[key] = mode, path
        self.metadata = QtWidgets.QPlainTextEdit()
        self.metadata.setMaximumHeight(180)
        form.addRow('Type metadata (JSON)', self.metadata)
        self.kind.currentIndexChanged.connect(lambda: self.update_type(self.kind.currentData()))
        self.update_type(self.kind.currentData())
        note = QtWidgets.QLabel('Generated previews are available for Still and Footage. Other asset types require a supplied JPG thumbnail. '
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
        # Route OS path drops from every part of the panel to the dialog.
        for child in self.findChildren(QtWidgets.QWidget):
            child.setAcceptDrops(False)
        self.setAcceptDrops(True)
        if not self.library.count():
            self.status.setText('No writable library is configured. Add one to the studio configuration.')
            self.submit_button.setEnabled(False)

    def update_type(self, kind):
        definition = ASSET_TYPES[kind]
        roles = definition['representations']
        visual = kind in ('still', 'footage')
        for role, (mode, path) in self.media.items():
            supported = role in roles
            mode.parentWidget().setVisible(supported)
            if supported:
                required = roles[role][0]
                preferred = 'generate' if visual and (required or role in ('proxy', 'filmstrip')) else ('supply' if required else 'omit')
                mode.setCurrentText(preferred)
                path.setEnabled(preferred == 'supply')
        self.fps.setEnabled(kind == 'footage')
        self.color.setEnabled(visual)
        self.metadata.setPlainText(json.dumps(default_metadata(kind), indent=2))
        if self.source.text():
            self.source_selected(self.source.text())

    def refresh_categories(self, *_):
        library = self.library.currentData()
        root = str(Path(library['root']).resolve()).casefold() if library else ''
        categories = {asset['category'].replace('\\', '/').split('/')[0] for asset in self.assets
                      if str(Path(asset.get('library_root', '')).resolve()).casefold() == root}
        if library:
            library_root = Path(library['root'])
            if library_root.is_dir():
                categories.update(item.name for item in library_root.iterdir()
                                  if item.is_dir() and not item.name.startswith('.'))
        current = self.category.currentText()
        self.category.clear()
        self.category.addItems(sorted(categories, key=str.casefold))
        if current and self.category.findText(current) >= 0:
            self.category.setCurrentText(current)

    def toggle_new_category(self, checked):
        self.category.setEnabled(not checked)
        self.new_category_name.setEnabled(checked)

    def selected_category(self):
        return self.new_category_name.text().strip() if self.new_category.isChecked() else self.category.currentText().strip()

    def source_selected(self, value):
        if not value:
            return
        try:
            source = prepare_source(value, self.kind.currentData(), self.settings)
            self.source.edit.setText(source)
            clean = clean_asset_name(source)
            self.name.setText(clean)
            self.tags.setText(', '.join(keywords_from_name(clean)))
            self.status.setText('Main source is valid for %s.' % ASSET_TYPES[self.kind.currentData()]['label'])
        except Exception as error:
            self.error(str(error))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.source_selected(paths[0])
            event.acceptProposedAction()

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
        try:
            metadata = json.loads(self.metadata.toPlainText() or '{}')
        except ValueError as error:
            raise ValueError('Type metadata is invalid JSON: ' + str(error))
        return make_manifest(self.settings, self.library.currentData(), self.name.text(),
            self.selected_category(), self.source.text(), self.tags.text().split(','),
            self.color.currentText(), self.profile.currentText(),
            {k: {'mode': mode.currentText(), 'path': path.text()} for k, (mode, path) in self.media.items()},
            self.fps.value(), self.highres.text(), self.preview_source.currentText(), self.kind.currentData(), metadata)

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
