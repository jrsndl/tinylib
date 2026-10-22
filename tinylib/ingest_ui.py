"""Ingest form: independent supplied/generated media and a reviewable manifest."""
import json
import copy
import threading
from pathlib import Path
from .qt import QtCore, QtWidgets, exec_dialog
from .views import heading_font
from .ingest import (FOLDER_KINDS, PAIRED_ROLES, category_for, paired_roles, clean_asset_name, keywords_from_name, make_manifest,
                     prepare_source, save_manifest, submit_deadline, submit_deadline_batch,
                     make_folder_manifests)
from .asset_types import ASSET_TYPES, LATLONG_MAIN_EXTENSIONS, default_metadata, is_two_to_one
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

    def __init__(self, parent=None, files=True):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit()
        layout.addWidget(self.edit, 1)
        if files:  # a folder-only field (folder batch components) has no File… button
            button = QtWidgets.QPushButton('File…')
            layout.addWidget(button)
            button.clicked.connect(self.browse)
        folder_button = QtWidgets.QPushButton('Folder…')
        layout.addWidget(folder_button)
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


def category_key(category):
    """A comparable text for a category name or a ``{'parent': N}`` rule."""
    return json.dumps(category, sort_keys=True) if isinstance(category, dict) else str(category).strip().casefold()


def folder_manifests(args, progress=None):
    """The folder batch for form arguments; ``args[13]`` carries ``{'pairing': ...}`` in batch mode."""
    options = args[13] if isinstance(args[13], dict) else {}
    return make_folder_manifests(args[0], args[1], args[3], args[4], args[6], args[7],
                                 progress=progress, pairing=options.get('pairing'), kind=args[12],
                                 latlong=options.get('is_equirectangular') is True)


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
            self.progress.emit('Validating ingest inputs and sequence frames…')
            if not self.batch:
                manifest = make_manifest(*self.arguments)
                self.done.emit(submit_deadline(manifest, self.settings, progress=self.progress.emit))
                return
            manifests = folder_manifests(self.arguments, self.progress.emit)
            def submitted(manifest, identifier, path):
                item = {'name': manifest['name'], 'job_id': identifier, 'manifest': str(path)}
                confirmed.append(item)
                self.item_submitted.emit(item)
            submit_deadline_batch(manifests, self.settings, progress=self.progress.emit, submitted=submitted)
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
                            category_key(arguments[3]),
                            '@folder:' + root_key(arguments[4]) if batch else arguments[2].strip().casefold())
        self.setWindowTitle('Deadline folder batch: ' + arguments[2] if batch else 'Deadline submission: ' + arguments[2])
        self.setModal(False)
        self.resize(540, 180)
        layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel('Preparing submission…')
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


def worker_arguments(args):
    """Give a worker thread its own AccessControl: make_manifest refreshes it, and the GUI
    thread must not see the shared one change half-way through a permission check."""
    settings = dict(args[0])
    if settings.get('_access') is not None:
        settings['_access'] = copy.deepcopy(settings['_access'])
    return (settings,) + tuple(args[1:])


def build_manifest(args, batch):
    """Pure function of the form values: safe to run in a worker thread."""
    if batch:
        return folder_manifests(args)
    return make_manifest(*args)


class Background(QtCore.QThread):
    done = QtCore.Signal(object)

    def __init__(self, work, parent):
        super().__init__(parent)
        self.work = work

    def run(self):
        try:
            self.done.emit((self.work(), ''))
        except Exception as error:
            self.done.emit((None, str(error)))


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
        self._background = []
        self._last_source = None
        self._auto_name = ''
        self._auto_tags = ''
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

        self.category_mode = IngestComboBox()
        for label, mode in (('Pick existing', 'pick'), ('Create new', 'new'), ('From main folder', 'parent')):
            self.category_mode.addItem(label, mode)
        self.category = IngestComboBox()
        self.new_category_name = QtWidgets.QLineEdit()
        self.new_category_name.setPlaceholderText('New category name')
        self.parent_levels = IngestComboBox()
        for levels in (1, 2, 3):
            self.parent_levels.addItem('%d level%s up' % (levels, '' if levels == 1 else 's'), levels)
        self.category_preview = QtWidgets.QLabel()
        category_widget = QtWidgets.QWidget()
        category_row = QtWidgets.QHBoxLayout(category_widget)
        category_row.setContentsMargins(0, 0, 0, 0)
        category_row.addWidget(self.category_mode)
        category_row.addWidget(self.category, 1)
        category_row.addWidget(self.new_category_name, 1)
        category_row.addWidget(self.parent_levels)
        category_row.addWidget(self.category_preview, 1)
        form.addRow('Main category', category_widget)
        self.category_mode.currentIndexChanged.connect(self.update_category_mode)
        self.parent_levels.currentIndexChanged.connect(self.update_category_preview)
        self.update_category_mode()
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
        self.batch_hint = QtWidgets.QLabel(
            'Folder batch: each detected sequence or container (Footage), or each image file (Still), becomes '
            'a separate Deadline job. Names, keywords and, for footage, FPS are automatic. Paired components '
            'are used as they are; what is missing is generated, from the paired proxy when there is one.')
        self.batch_hint.setWordWrap(True)
        form.addRow('', self.batch_hint)
        self.batch_hint.hide()
        self.pairing_mode = IngestComboBox()
        self.pairing_mode.addItem('Separate folder structure', 'separate')
        self.pairing_mode.addItem('Same folder structure', 'same')
        self.pairing_mode.setToolTip('Separate: each component has its own folder, paired by file name. '
                                     'Same: the components are among the main files; a regex picks each.')
        form.addRow('Components are in', self.pairing_mode)
        self.pairing_fields = {}
        self.pairing_use = {}
        for role in PAIRED_ROLES:
            field = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(field)
            row.setContentsMargins(0, 0, 0, 0)
            if role != 'highres':  # previews: use the paired file or generate; a highres is never generated
                use = IngestComboBox()
                use.addItem('Paired, else generate', 'auto')
                use.addItem('Supply (paired)', 'supply')
                use.addItem('Generate', 'generate')
                use.setToolTip('Paired, else generate: the paired file when there is one.\n'
                               'Supply: the paired file; a main without one stops the batch.\n'
                               'Generate: always generated from the main; paired files are ignored '
                               '(and still never ingested as mains).')
                row.addWidget(use)
                self.pairing_use[role] = use
            folder = PathField(files=False)
            regex = QtWidgets.QLineEdit()
            folder.edit.setPlaceholderText('Folder (empty: not paired)')
            row.addWidget(folder, 1)
            row.addWidget(regex, 1)
            form.addRow(role.capitalize(), field)
            self.pairing_fields[role] = field, folder, regex
        self.pairing_mode.currentIndexChanged.connect(self.update_pairing_mode)
        hint = QtWidgets.QLabel('Choose a still, sequence frame, footage container, asset file, or folder. '
                                'Footage and Still folders are searched recursively for a folder batch; '
                                'image sequences are detected automatically for Footage.')
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
        self.highres.setToolTip('Copied as it is; never converted.')
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
        self.latlong = QtWidgets.QCheckBox('Lat-long (equirectangular HDRI)')
        self.latlong.setToolTip('Sets is_equirectangular. The main must be EXR or HDR; the proxy and thumbnail '
                                'are tonemapped 2:1 lat-long JPEGs, of any size. Opens in the 360 viewer.\n'
                                'Ticked automatically for a 2:1 EXR or HDR main.')
        form.addRow('Projection', self.latlong)
        self.metadata = QtWidgets.QPlainTextEdit()
        self.metadata.setMaximumHeight(180)
        form.addRow('Type metadata (JSON)', self.metadata)
        self.latlong.toggled.connect(self.set_latlong)
        self.metadata.textChanged.connect(self.sync_latlong)
        self.proxy_warning = QtWidgets.QLabel()
        self.proxy_warning.setWordWrap(True)
        self.proxy_warning.setStyleSheet('color: #ffa02f;')
        self.media['proxy'][0].parentWidget().layout().addWidget(self.proxy_warning)
        self.media['proxy'][0].currentTextChanged.connect(self.scan_proxy)
        self.media['proxy'][1].selected.connect(self.scan_proxy)
        self.media['proxy'][1].edit.textChanged.connect(self.invalidate_proxy)
        self.media['proxy'][1].edit.editingFinished.connect(self.scan_proxy)
        self.fps.valueChanged.connect(self.update_proxy_warning)
        self.source.edit.textChanged.connect(self.invalidate_main)
        self.kind.currentIndexChanged.connect(lambda: self.update_type(self.kind.currentData()))
        self.update_type(self.kind.currentData())
        self.set_batch_mode(False)  # pairing rows show only for a folder batch
        note = QtWidgets.QLabel('Generated previews are available for Still and Footage. Other asset types require a supplied JPG thumbnail. '
                              'Main and highres files are copied without changing their pixels. For Footage with a supplied '
                              'proxy, a generated thumbnail and filmstrip are taken from the proxy.')
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
        self.show_latlong_row()
        if self.source.text():
            self.source_selected(self.source.text())

    def show_latlong_row(self):
        visible = self.kind.currentData() == 'still'  # also for a folder batch: every still is flagged
        self.latlong.setVisible(visible)
        self.form.labelForField(self.latlong).setVisible(visible)

    def metadata_object(self):
        try:
            value = json.loads(self.metadata.toPlainText() or '{}')
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def set_latlong(self, enabled):
        """The checkbox writes is_equirectangular into the type metadata JSON."""
        current = self.metadata_object()
        if current is None:
            self.status.setText('Type metadata is invalid JSON; fix it to change the projection.')
        elif current.get('is_equirectangular') is not enabled:
            current['is_equirectangular'] = enabled
            self.metadata.setPlainText(json.dumps(current, indent=2))
        self.update_preview_labels()

    def sync_latlong(self):
        current = self.metadata_object()
        if current is not None:
            blocked = self.latlong.blockSignals(True)
            self.latlong.setChecked(current.get('is_equirectangular') is True)
            self.latlong.blockSignals(blocked)
        self.update_preview_labels()

    def update_preview_labels(self):
        latlong = self.kind.currentData() == 'still' and self.latlong.isChecked()
        self.media_rows['proxy'][1].setText('Proxy · 2:1 lat-long, any size' if latlong else
                                            'Proxy · any size' if self.kind.currentData() == 'still' else 'Proxy')
        self.media_rows['thumb'][1].setText('Thumbnail · 2:1 lat-long, 960 × 480' if latlong else 'Thumbnail · 960 × 506')

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
        self.category.setToolTip('Discovering additional library folders…')
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

    def update_category_mode(self, *_):
        mode = self.category_mode.currentData()
        self.category.setVisible(mode == 'pick')
        self.new_category_name.setVisible(mode == 'new')
        self.parent_levels.setVisible(mode == 'parent')
        self.category_preview.setVisible(mode == 'parent')
        self.update_category_preview()

    def update_category_preview(self, *_):
        if self.category_mode.currentData() != 'parent':
            return
        if self._batch_folder:
            self.category_preview.setText('from each main file\'s folder')
            return
        try:
            name = category_for(self.selected_category(), self.source.text()) if self.source.text() else ''
            self.category_preview.setText('→ ' + name if name else '')
        except ValueError as error:
            self.category_preview.setText(str(error))

    def selected_category(self):
        """A category name, or ``{'parent': N}`` for the folder N levels above the main file."""
        mode = self.category_mode.currentData()
        if mode == 'parent':
            return {'parent': self.parent_levels.currentData()}
        return self.new_category_name.text().strip() if mode == 'new' else self.category.currentText().strip()

    PAIRING_EXAMPLES = {
        'footage': {'proxy': r'^.*\.mp4$', 'highres': r'^.*_8K.*\.exr$', 'thumb': r'^.*_thumb\.jpg$',
                    'filmstrip': r'^.*_filmstrip\.jpg$'},
        'still': {'proxy': r'^.*_proxy\.jpg$', 'highres': r'^.*_16K\.exr$', 'thumb': r'^.*_thumb\.jpg$'},
    }

    def update_pairing_mode(self, *_):
        separate = self.pairing_mode.currentData() == 'separate'
        kind = self.kind.currentData()
        roles = paired_roles(kind) if kind in FOLDER_KINDS else ()
        examples = self.PAIRING_EXAMPLES.get(kind, {})
        for role, (field, folder, regex) in self.pairing_fields.items():
            folder.setVisible(separate)
            regex.setVisible(not separate)
            if role in examples:
                regex.setPlaceholderText('Regex, e.g. ' + examples[role] + ' (empty: not paired)')
            visible = bool(self._batch_folder) and role in roles
            field.setVisible(visible)
            self.form.labelForField(field).setVisible(visible)

    def pairing(self):
        mode = self.pairing_mode.currentData()
        values = {role: (folder.text() if mode == 'separate' else regex.text().strip())
                  for role, (field, folder, regex) in self.pairing_fields.items()}
        return dict(values, mode=mode, use={role: combo.currentData() for role, combo in self.pairing_use.items()})

    def set_batch_mode(self, enabled):
        self.batch_hint.setVisible(enabled)
        for widget in [self.pairing_mode] + [field for field, _, _ in self.pairing_fields.values()]:
            widget.setVisible(enabled)
            label = self.form.labelForField(widget)
            if label is not None:
                label.setVisible(enabled)
        if enabled:
            self.update_pairing_mode()
        self.update_category_preview()
        for widget in (self.name, self.fps, self.highres, self.tags, self.known_tags, self.metadata):
            widget.setVisible(not enabled)
            label = self.form.labelForField(widget)
            if label is not None:
                label.setVisible(not enabled)
        for role, (field, label) in self.media_rows.items():
            visible = not enabled and role in ASSET_TYPES[self.kind.currentData()]['representations']
            field.setVisible(visible)
            label.setVisible(visible)
        self.show_latlong_row()

    def source_selected(self, value):
        # editingFinished fires on every focus change; only a real change re-reads the source.
        if value and (value, self.kind.currentData()) == self._last_source:
            return
        self.invalidate_main()  # Also clears _last_source; set again once the source is valid.
        self._source_error = ''
        self._batch_folder = None
        self.set_batch_mode(False)
        if not value:
            return
        try:
            if self.kind.currentData() in FOLDER_KINDS and Path(value).is_dir():
                self.source.edit.setText(value)
                self._batch_folder = value
                self.set_batch_mode(True)
                self.invalidate_proxy()
                self.status.setText('Folder batch selected. Choose where the proxies, highres and other '
                                    'components are%s; Review job shows how they paired.'
                                    % (', and tick Lat-long if they are HDRIs' if self.kind.currentData() == 'still' else ''))
                return
            source = prepare_source(value, self.kind.currentData(), self.settings)
            self.source.edit.setText(source)
            self._last_source = (source, self.kind.currentData())
            clean = clean_asset_name(source)
            # Fill name and keywords from the file name, but never overwrite what the user typed.
            if not self.name.text().strip() or self.name.text() == self._auto_name:
                self.name.setText(clean)
                self._auto_name = clean
            keywords = ', '.join(keywords_from_name(clean))
            if not self.tags.text().strip() or self.tags.text() == self._auto_tags:
                self.tags.setText(keywords)
                self._auto_tags = keywords
            self.status.setText('Main source is valid for %s.' % ASSET_TYPES[self.kind.currentData()]['label'])
            self.update_category_preview()
            if self.kind.currentData() == 'footage':
                self.start_scan('main', source)
            elif (self.kind.currentData() == 'still'
                  and Path(source).suffix.lower().lstrip('.') in LATLONG_MAIN_EXTENSIONS):
                self.start_scan('main', source)  # a 2:1 EXR/HDR is ticked as lat-long
            self.scan_proxy()
        except Exception as error:
            self._last_source = None
            self._source_error = str(error)
            self.error(self._source_error)

    def invalidate_main(self, *_):
        # Any edit to the source text cancels the running scan, so the same text must be re-read.
        self._last_source = None
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
            self.status.setText('Scanning main metadata…')
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
        elif self.kind.currentData() == 'still':
            self.still_scanned(metadata)
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

    def still_scanned(self, metadata):
        """An EXR/HDR still: fill its size, and tick lat-long when it is 2:1."""
        current = self.metadata_object()
        if current is None:
            self.status.setText('Main metadata scanned; type metadata is invalid JSON, so it was not filled.')
            return
        for key in ('width', 'height', 'pixel_aspect'):
            if key in metadata:
                current[key] = metadata[key]
        two_to_one = bool(metadata.get('width')) and is_two_to_one(metadata['width'], metadata.get('height', 0))
        if two_to_one:
            current['is_equirectangular'] = True
        self.metadata.setPlainText(json.dumps(current, indent=2))
        self.status.setText('Main is %sx%s%s.' % (metadata.get('width'), metadata.get('height'),
                            ', 2:1: marked lat-long (HDRI)' if two_to_one else ''))

    def scan_proxy(self, *_):
        self.invalidate_proxy()
        mode, path = self.media['proxy']
        if (not self._batch_folder and self.kind.currentData() == 'footage'
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
            raise ValueError('Main metadata is still being scanned. Please wait.')
        if self.library.currentData() is None:
            raise ValueError('No writable library is configured.')
        try:
            metadata = {} if self._batch_folder else json.loads(self.metadata.toPlainText() or '{}')
        except ValueError as error:
            raise ValueError('Type metadata is invalid JSON: ' + str(error))
        category = self.selected_category()
        if self._batch_folder:
            # Resolved per main file; the pairing travels where single jobs carry metadata.
            metadata = {'pairing': self.pairing()}
            if self.kind.currentData() == 'still':
                metadata['is_equirectangular'] = self.latlong.isChecked()
        else:
            category = category_for(category, self.source.text())
        if not category:
            raise ValueError('Choose or enter a main category.')
        return (self.settings, self.library.currentData(), Path(self._batch_folder).name if self._batch_folder else self.name.text(),
            category, self.source.text(), self.tags.text().split(','),
            self.color.currentText(), self.profile.currentText(),
            {k: {'mode': mode.currentText(), 'path': path.text()} for k, (mode, path) in self.media.items()},
            self.fps.value(), self.highres.text(), 'main', self.kind.currentData(), metadata)

    def manifest(self):
        return build_manifest(self.manifest_arguments(), bool(self._batch_folder))

    def error(self, message):
        self.status.setText(message)

    def in_background(self, work, done, message):
        """Run network-heavy work (folder discovery, probing every source) off the GUI thread."""
        if any(task.isRunning() for task in self._background):
            return self.error('Still preparing the previous request…')
        task = Background(work, self)
        self._background.append(task)

        def delivered(result):
            value, error = result
            if error:
                self.error(error)
            else:
                done(value)

        def finished():
            # Only after run() has returned: deleting a QThread that is still running aborts Nuke.
            self._background.remove(task)
            task.deleteLater()
        task.done.connect(delivered)
        task.finished.connect(finished)
        self.status.setText(message)
        task.start()

    def review(self):
        try:
            # Read the form here, on the GUI thread; the worker only gets plain values.
            args, batch = worker_arguments(self.manifest_arguments()), bool(self._batch_folder)
        except Exception as error:
            return self.error(str(error))
        self.in_background(lambda: build_manifest(args, batch), self.show_review, 'Preparing job manifest(s)…')

    def show_review(self, job):
        try:
            self.status.setText('')
            dialog = QtWidgets.QDialog(self)
            dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            dialog.setWindowTitle('Review ingest job')
            dialog.resize(700, 600)
            layout = QtWidgets.QVBoxLayout(dialog)
            text = QtWidgets.QPlainTextEdit(json.dumps(job, indent=2))
            text.setReadOnly(True)
            layout.addWidget(text)
            exec_dialog(dialog)
        except Exception as error:
            self.error(str(error))

    def export(self):
        try:
            args, batch = worker_arguments(self.manifest_arguments()), bool(self._batch_folder)
        except Exception as error:
            return self.error(str(error))
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Save manifest folder')
        if folder:
            self.in_background(lambda: build_manifest(args, batch), lambda job: self.save_manifests(job, folder),
                               'Preparing job manifest(s)…')

    def save_manifests(self, job, folder):
        try:
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
                       category_key(arguments[3]),
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

    # Closing only hides the form; running scans finish in the background (tools time out after
    # a minute) and their results land in the hidden form, which is reused by "Return to ingest form".
