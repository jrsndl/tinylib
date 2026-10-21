"""Ingest form: independent supplied/generated media and a reviewable manifest."""
import json
import copy
import threading
from pathlib import Path
from .qt import QtCore, QtWidgets
from .views import heading_font
from .ingest import (clean_asset_name, keywords_from_name, make_manifest,
                     prepare_source, save_manifest, submit_deadline, make_folder_manifests)
from .asset_types import ASSET_TYPES, default_metadata
from .metadata_scan import scan_source_metadata
from .access import root_key


class IngestComboBox(QtWidgets.QComboBox):
    def wheelEvent(self, event):
        # Scrolling the form must not silently change asset type or media modes.
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


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
    progress = QtCore.Signal(str)
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    item_submitted = QtCore.Signal(object)

    def __init__(self, arguments, settings, parent, batch=False):
        super().__init__(parent)
        # UI edits and other submissions must not change an in-flight job.
        self.settings = copy.deepcopy({key: value for key, value in settings.items() if key != '_access'})
        if settings.get('_access') is not None:
            self.settings['_access'] = copy.copy(settings['_access'])
        self.arguments = (self.settings,) + copy.deepcopy(arguments[1:])
        self.batch = batch

    def run(self):
        confirmed = []
        try:
            self.progress.emit('Validating ingest inputs and sequence frames...')
            if not self.batch:
                manifest = make_manifest(*self.arguments)
                self.done.emit(submit_deadline(manifest, self.settings, progress=self.progress.emit))
                return
            args = self.arguments
            manifests = make_folder_manifests(self.settings, args[1], args[3], args[4], args[6], args[7],
                                               args[11], self.progress.emit)
            for index, manifest in enumerate(manifests, 1):
                def report(message, index=index, name=manifest['name']):
                    self.progress.emit('Job %d/%d - %s: %s' % (index, len(manifests), name, message))
                result = submit_deadline(manifest, self.settings, progress=report)
                item = {'name': manifest['name'], 'job_id': result[0], 'manifest': str(result[1])}
                confirmed.append(item)
                self.item_submitted.emit(item)
            self.done.emit({'jobs': confirmed})
        except Exception as error:
            if self.batch:
                detail = '\n'.join('%s: %s' % (item['name'], item['job_id']) for item in confirmed)
                self.failed.emit('Folder batch stopped; %d job(s) confirmed. %s\n%s' %
                                 (len(confirmed), error, detail))
            else:
                self.failed.emit(str(error))


class SubmissionWindow(QtWidgets.QDialog):
    """Own a submission independently of the input form."""
    def __init__(self, source_dialog, arguments, batch=False):
        super().__init__(source_dialog.parentWidget())
        self.source_dialog = source_dialog
        self.state = 'pending'
        self.batch = batch
        self.confirmed_jobs = []
        self.destination = (str(Path(arguments[1]['root']).resolve()).casefold(),
                            arguments[3].strip().casefold(),
                            '@folder:' + root_key(arguments[4]) if batch else arguments[2].strip().casefold())
        self.setWindowTitle('Deadline folder batch: ' + arguments[2] if batch else 'Deadline submission: ' + arguments[2])
        self.setModal(False)
        self.resize(540, 180)
        layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel('Preparing submission...')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QtWidgets.QLabel('Continue preparing the next asset in the open ingest form.'))
        self.edit_button = QtWidgets.QPushButton('Return to ingest form')
        self.edit_button.clicked.connect(self.edit_inputs)
        layout.addWidget(self.edit_button)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(batch)
        layout.addWidget(self.log)
        self.worker = Submission(arguments, source_dialog.settings, self, batch=batch)
        self.worker.item_submitted.connect(self.record_submission)
        self.worker.progress.connect(self.status.setText)
        self.worker.done.connect(self.completed)
        self.worker.failed.connect(self.failed)
        owner = self.parentWidget()
        if owner is not None:
            if not hasattr(owner, '_ingest_submissions'):
                owner._ingest_submissions = []
            owner._ingest_submissions.append(self)

    def record_submission(self, item):
        self.confirmed_jobs.append(item)
        self.log.appendPlainText('%s: JobID=%s\n%s' % (item['name'], item['job_id'], item['manifest']))

    def completed(self, result):
        self.state = 'submitted'
        if self.batch:
            self.status.setText('Submitted %d Deadline jobs.' % len(result['jobs']))
        else:
            self.status.setText('Submitted job ' + result[0] + '\n' + str(result[1]))

    def failed(self, message):
        self.state = 'partial' if self.confirmed_jobs else 'failed'
        self.status.setText('Submission failed: ' + message)
        self.edit_button.show()
        self.show()

    def edit_inputs(self):
        self.source_dialog.show()
        self.source_dialog.raise_()
        self.source_dialog.activateWindow()
        self.hide()

    def reject(self):
        if self.worker.isRunning():
            self.hide()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)


class MetadataScan(QtCore.QThread):
    done = QtCore.Signal(object)

    def __init__(self, source, settings, parent):
        super().__init__(parent)
        self.source, self.settings = source, settings

    def run(self):
        try:
            self.done.emit((scan_source_metadata(self.source, self.settings), ''))
        except Exception as error:
            self.done.emit((None, str(error)))


class CategoryScan(QtCore.QObject):
    done = QtCore.Signal(object)

    def start(self, root, request):
        def scan():
            try:
                categories = {item.name for item in Path(root).iterdir()
                              if item.is_dir() and not item.name.startswith('.')}
                result = (request, categories, '')
            except Exception as error:
                result = (request, set(), str(error))
            try:
                self.done.emit(result)
            except RuntimeError:
                pass  # The form may have been destroyed while the share was offline.
        threading.Thread(target=scan, daemon=True).start()


class IngestDialog(QtWidgets.QDialog):
    def __init__(self, settings, assets, parent=None, library_root='', category=''):
        super().__init__(parent)
        self.settings, self.assets = settings, assets
        self._scan_versions = {'main': 0, 'proxy': 0}
        self._scans = []
        self._submissions = []
        self._category_request = 0
        self._categories_by_root = {}
        roots = {}
        for asset in assets:
            asset_root = asset.get('library_root', '')
            if asset_root not in roots:
                roots[asset_root] = root_key(asset_root)
            self._categories_by_root.setdefault(roots[asset_root], set()).add(
                asset['category'].replace('\\', '/').split('/')[0])
        self.category_scan = CategoryScan(self)
        self.category_scan.done.connect(self.categories_loaded)
        self._proxy_fps = None
        self._main_scan_pending = False
        self._source_error = ''
        self._batch_folder = None
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
        self.form = form
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.library = IngestComboBox()
        for library in settings['libraries']:
            access = settings.get('_access')
            if not library.get('read_only') and not (access and access.read_only(library['root'])) and (not access or (access.can(library['root'], 'view') and access.can(library['root'], 'ingest'))):
                self.library.addItem(library['name'], library)
        form.addRow('Destination library', self.library)

        self.kind = IngestComboBox()
        for identifier, definition in ASSET_TYPES.items():
            self.kind.addItem(definition['label'], identifier)
        self.kind.setCurrentIndex(self.kind.findData('footage'))
        form.addRow('Asset type', self.kind)

        self.category = IngestComboBox()
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
        self._preferred_category = ''
        if library_root:
            selected_root = root_key(library_root)
            for index in range(self.library.count()):
                library = self.library.itemData(index)
                if root_key(library['root']) == selected_root:
                    self.library.setCurrentIndex(index)
                    self._preferred_category = category.replace('\\', '/').split('/')[0]
                    break
        self.library.currentIndexChanged.connect(self.refresh_categories)
        self.refresh_categories()

        self.source = PathField()
        self.source.edit.setAcceptDrops(False)
        self.source.selected.connect(self.source_selected)
        form.addRow('Main', self.source)
        self.batch_hint = QtWidgets.QLabel('Folder batch: each detected sequence/container becomes a separate Deadline job. '
                                          'Names, keywords and FPS are automatic; proxy, thumbnail and filmstrip are generated.')
        self.batch_hint.setWordWrap(True)
        form.addRow('', self.batch_hint)
        self.batch_hint.hide()
        hint = QtWidgets.QLabel('Choose a still, sequence frame, footage container, asset file, or folder. '
                                'Footage folders are searched recursively; image sequences are detected automatically.')
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
        self.color = IngestComboBox()
        self.color.setEditable(True)
        self.color.addItems(settings.get('colorspaces', ['ACES - ACEScg']))
        form.addRow('Main color space', self.color)
        self.tags = QtWidgets.QLineEdit()
        self.tags.setPlaceholderText('Comma-separated keywords; new keywords are welcome')
        form.addRow('Keywords', self.tags)
        self.known_tags = IngestComboBox()
        self.known_tags.addItem('Add an existing keyword…')
        self.known_tags.addItems(sorted({t for a in assets for t in a.get('tags', [])}))
        self.known_tags.activated.connect(self.add_tag)
        form.addRow('', self.known_tags)
        self.profile = IngestComboBox()
        self.profile.addItems(list(settings.get('profiles', {})))
        form.addRow('Processing profile', self.profile)
        self.preview_source = IngestComboBox()
        self.preview_source.addItems(['main', 'proxy'])
        form.addRow('Generate thumb / strip from', self.preview_source)
        self.media = {}
        self.media_rows = {}
        for key, label in [('proxy', 'Proxy'), ('thumb', 'Thumbnail · 960 × 506'),
                           ('filmstrip', 'Filmstrip · 24 × 480 × 270'), ('scene', 'Scene')]:
            field = QtWidgets.QWidget()
            row = QtWidgets.QVBoxLayout(field)
            row.setContentsMargins(0, 0, 0, 0)
            mode = IngestComboBox()
            mode.addItems(['omit', 'generate', 'supply'])
            path = PathField()
            path.setEnabled(False)
            mode.currentTextChanged.connect(lambda text, widget=path: widget.setEnabled(text == 'supply'))
            row.addWidget(mode)
            row.addWidget(path)
            form.addRow(label, field)
            self.media[key] = mode, path
            self.media_rows[key] = field, form.labelForField(field)
        self.metadata = QtWidgets.QPlainTextEdit()
        self.metadata.setMaximumHeight(180)
        form.addRow('Type metadata (JSON)', self.metadata)
        self.proxy_warning = QtWidgets.QLabel()
        self.proxy_warning.setWordWrap(True)
        self.proxy_warning.setStyleSheet('color: #ffa02f;')
        self.media['proxy'][0].parentWidget().layout().addWidget(self.proxy_warning)
        self.preview_source.currentTextChanged.connect(self.scan_proxy)
        self.media['proxy'][0].currentTextChanged.connect(self.scan_proxy)
        self.media['proxy'][1].selected.connect(self.scan_proxy)
        self.media['proxy'][1].edit.textChanged.connect(self.invalidate_proxy)
        self.media['proxy'][1].edit.editingFinished.connect(self.scan_proxy)
        self.fps.valueChanged.connect(self.update_proxy_warning)
        self.source.edit.textChanged.connect(self.invalidate_main)
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
        self.invalidate_main()
        self.invalidate_proxy()
        definition = ASSET_TYPES[kind]
        roles = definition['representations']
        visual = kind in ('still', 'footage')
        for role, (mode, path) in self.media.items():
            supported = role in roles
            field, label = self.media_rows[role]
            field.setVisible(supported)
            label.setVisible(supported)
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
        self._category_request += 1
        library = self.library.currentData()
        self.category.clear()
        if not library:
            return
        categories = self._categories_by_root.get(root_key(library['root']), set())
        self.category.addItems(sorted(categories, key=str.casefold))
        preferred = self._preferred_category
        self._preferred_category = ''
        if preferred and self.category.findText(preferred) >= 0:
            self.category.setCurrentText(preferred)
        self._category_initial = self.category.currentText()
        self._category_preferred = preferred
        self.category.setToolTip('Discovering additional library folders...')
        self.category_scan.start(library['root'], self._category_request)

    def categories_loaded(self, result):
        request, categories, error = result
        if request != self._category_request:
            return
        current = self.category.currentText()
        preferred = (self._category_preferred if current == self._category_initial else '')
        categories.update(self.category.itemText(i) for i in range(self.category.count()))
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItems(sorted(categories, key=str.casefold))
        for candidate in (preferred, current):
            if candidate and self.category.findText(candidate) >= 0:
                self.category.setCurrentText(candidate)
                break
        self.category.blockSignals(False)
        self.category.setToolTip('Could not discover additional folders: ' + error if error else '')

    def toggle_new_category(self, checked):
        self.category.setEnabled(not checked)
        self.new_category_name.setEnabled(checked)

    def selected_category(self):
        return self.new_category_name.text().strip() if self.new_category.isChecked() else self.category.currentText().strip()

    def set_batch_mode(self, enabled):
        self.batch_hint.setVisible(enabled)
        for widget in (self.name, self.fps, self.highres, self.tags, self.known_tags, self.metadata):
            widget.setVisible(not enabled)
            label = self.form.labelForField(widget)
            if label is not None:
                label.setVisible(not enabled)
        for role, (field, label) in self.media_rows.items():
            visible = not enabled and role in ASSET_TYPES[self.kind.currentData()]['representations']
            field.setVisible(visible)
            label.setVisible(visible)

    def source_selected(self, value):
        self.invalidate_main()
        self._source_error = ''
        self._batch_folder = None
        self.set_batch_mode(False)
        if not value:
            return
        try:
            if self.kind.currentData() == 'footage' and Path(value).is_dir():
                self.source.edit.setText(value)
                self._batch_folder = value
                self.set_batch_mode(True)
                self.invalidate_proxy()
                self.status.setText('Folder batch selected. Submit creates one job per sequence/container, '
                                    'using automatic metadata and generated previews.')
                return
            source = prepare_source(value, self.kind.currentData(), self.settings)
            self.source.edit.setText(source)
            clean = clean_asset_name(source)
            self.name.setText(clean)
            self.tags.setText(', '.join(keywords_from_name(clean)))
            self.status.setText('Main source is valid for %s.' % ASSET_TYPES[self.kind.currentData()]['label'])
            if self.kind.currentData() == 'footage':
                self.start_scan('main', source)
            self.scan_proxy()
        except Exception as error:
            self._source_error = str(error)
            self.error(self._source_error)

    def invalidate_main(self, *_):
        if self._batch_folder and self.source.text() != self._batch_folder:
            self._batch_folder = None
            self.set_batch_mode(False)
        self._scan_versions['main'] += 1
        self._main_scan_pending = False

    def invalidate_proxy(self, *_):
        self._scan_versions['proxy'] += 1
        self._proxy_fps = None
        self.proxy_warning.clear()

    def start_scan(self, role, source):
        self._scan_versions[role] += 1
        version = self._scan_versions[role]
        if role == 'main':
            self._main_scan_pending = True
            self.status.setText('Scanning main footage metadata...')
        worker = MetadataScan(source, self.settings, self)
        self._scans.append(worker)
        worker.done.connect(lambda result: self.scan_finished(role, version, result))
        worker.finished.connect(lambda: self._scans.remove(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def scan_finished(self, role, version, result):
        if version != self._scan_versions[role]:
            return
        if role == 'main':
            self._main_scan_pending = False
        metadata, error = result
        if role == 'proxy':
            if error:
                self.proxy_warning.setText('Could not scan proxy metadata: ' + error)
            else:
                self._proxy_fps = metadata.get('frame_rate')
                self.update_proxy_warning()
        elif error:
            self.status.setText('Could not scan main metadata: ' + error)
        else:
            try:
                current = json.loads(self.metadata.toPlainText() or '{}')
                if not isinstance(current, dict):
                    raise ValueError('Type metadata must be a JSON object.')
                current.update(metadata)
                self.metadata.setPlainText(json.dumps(current, indent=2))
                if metadata.get('frame_rate'):
                    self.fps.setValue(metadata['frame_rate'])
                self.status.setText('Main footage metadata loaded.' if metadata.get('frame_rate') else
                                    'Main metadata loaded; no embedded FPS found. Set Sequence FPS manually.')
            except ValueError as error:
                self.status.setText('Could not fill main metadata: ' + str(error))

    def scan_proxy(self, *_):
        self.invalidate_proxy()
        mode, path = self.media['proxy']
        if (not self._batch_folder and self.kind.currentData() == 'footage' and self.preview_source.currentText() == 'proxy'
                and mode.currentText() == 'supply' and path.text()):
            self.start_scan('proxy', path.text())

    def update_proxy_warning(self, *_):
        if self._proxy_fps is None:
            self.proxy_warning.clear()
        elif abs(self._proxy_fps - self.fps.value()) > .01:
            self.proxy_warning.setText('Warning: proxy FPS (%g) differs from main FPS (%g). Ingest can continue.'
                                       % (self._proxy_fps, self.fps.value()))
        else:
            self.proxy_warning.clear()

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

    def manifest_arguments(self):
        if self._source_error:
            raise ValueError(self._source_error)
        if not self._batch_folder and not self.name.text().strip():
            raise ValueError('Enter an asset name before submitting.')
        if self._main_scan_pending:
            raise ValueError('Main footage metadata is still being scanned. Please wait.')
        if self.library.currentData() is None:
            raise ValueError('No writable library is configured.')
        try:
            metadata = {} if self._batch_folder else json.loads(self.metadata.toPlainText() or '{}')
        except ValueError as error:
            raise ValueError('Type metadata is invalid JSON: ' + str(error))
        return (self.settings, self.library.currentData(), Path(self._batch_folder).name if self._batch_folder else self.name.text(),
            self.selected_category(), self.source.text(), self.tags.text().split(','),
            self.color.currentText(), self.profile.currentText(),
            {k: {'mode': mode.currentText(), 'path': path.text()} for k, (mode, path) in self.media.items()},
            self.fps.value(), self.highres.text(), self.preview_source.currentText(), self.kind.currentData(), metadata)

    def manifest(self):
        args = self.manifest_arguments()
        if self._batch_folder:
            return make_folder_manifests(self.settings, args[1], args[3], args[4], args[6], args[7], args[11])
        return make_manifest(*args)

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
                paths = [save_manifest(item, folder) for item in job] if isinstance(job, list) else [save_manifest(job, folder)]
                self.status.setText('Saved %d job manifest(s) to %s' % (len(paths), folder))
        except Exception as error:
            self.error(str(error))

    def submit(self):
        try:
            arguments = self.manifest_arguments()
        except Exception as error:
            return self.error(str(error))
        batch = bool(self._batch_folder)
        destination = (str(Path(arguments[1]['root']).resolve()).casefold(),
                       arguments[3].strip().casefold(),
                       '@folder:' + root_key(arguments[4]) if batch else arguments[2].strip().casefold())
        if any(window.destination == destination and window.state != 'failed'
               for window in self._submissions):
            return self.error('This asset already has a pending or confirmed submission. '
                              'Choose the next asset or a different destination name.')
        self.submission_window = SubmissionWindow(self, arguments, batch=batch)
        self._submissions.append(self.submission_window)
        self.submit_thread = self.submission_window.worker
        self.submission_window.show()
        self.submit_thread.start()
        self.status.setText('Submission started for %s. You can prepare the next asset here.' % arguments[2])
        self.raise_()
        self.activateWindow()

    def reject(self):
        if self._scans:
            self.status.setText('Waiting for metadata scans to finish...')
            return
        super().reject()

    def closeEvent(self, event):
        if self._scans:
            self.status.setText('Waiting for metadata scans to finish...')
            event.ignore()
            return
        super().closeEvent(event)
