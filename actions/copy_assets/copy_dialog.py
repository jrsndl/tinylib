"""The Copy Assets window: destination, component, sequence options, Nuke Read nodes, progress."""
import importlib.util
import sys
from pathlib import Path

from tinylib.qt import QtCore, QtWidgets


def sibling(name):
    """A module of this action folder, loaded once under a TinyLib-specific name."""
    key = '_tinylib_copy_assets_' + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


plan_module = sibling('copy_plan')
reads = sibling('nuke_reads')


def in_nuke():
    nuke = sys.modules.get('nuke')
    return nuke is not None and bool(getattr(nuke, 'GUI', False))


class CopyWorker(QtCore.QThread):
    progress = QtCore.Signal(int, int, str)
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, items, overwrite, parent=None):
        super().__init__(parent)
        self.items, self.overwrite = items, overwrite

    def run(self):
        try:
            result = plan_module.run(self.items, self.overwrite, self.progress.emit, self.isInterruptionRequested)
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.done.emit(result)


class CopyDialog(QtWidgets.QDialog):
    def __init__(self, assets, config, trim_for=None, collection='', preferences=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Copy assets')
        self.setMinimumWidth(640)
        self.assets, self.config, self.trim_for = assets, config, trim_for
        self.collection, self.preferences = collection or '', preferences
        self.nuke = in_nuke()
        self.worker, self.items, self.result_message = None, [], None
        remembered = (getattr(preferences, 'data', {}) or {}).get('display', {}).get('copy_assets', {})
        value = lambda key, default: remembered.get(key, config.get(key, default))

        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel('<b>%d asset(s)</b>%s' % (len(assets), ' from collection %s' % collection if collection else ''))
        layout.addWidget(heading)
        form = QtWidgets.QFormLayout()
        base_row = QtWidgets.QHBoxLayout()
        self.base = QtWidgets.QLineEdit()
        script = reads.script_folder() if self.nuke else ''
        self.base.setText(script or remembered.get('base', ''))
        self.base.setPlaceholderText('The folder the path template starts from')
        browse = QtWidgets.QPushButton('Browse…')
        browse.clicked.connect(self.browse)
        base_row.addWidget(self.base, 1)
        base_row.addWidget(browse)
        form.addRow('Base folder', base_row)
        if self.nuke and not script:
            hint = QtWidgets.QLabel('The Nuke script is not saved yet: choose the base folder.')
            hint.setObjectName('muted')
            form.addRow('', hint)
        # Nuke and standalone keep their own template: in Nuke it starts beside the script.
        self.template_key = 'template_nuke' if self.nuke else 'template'
        self.template = QtWidgets.QLineEdit(value(self.template_key, './resources' if self.nuke else '.'))
        self.template.setToolTip('Relative to the base folder; .. goes one folder up. {date} is today (YYYYMMDD), '
                                 '{collection} the collection name. Environment variables are expanded.\n'
                                 'All assets are copied into this one folder.')
        form.addRow('Path template', self.template)
        self.destination = QtWidgets.QLabel()
        self.destination.setWordWrap(True)
        self.destination.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        form.addRow('Copies to', self.destination)
        components = QtWidgets.QHBoxLayout()
        self.component_buttons = {}
        for component in plan_module.COMPONENTS:
            missing = sum(not asset.get(component) for asset in assets)
            button = QtWidgets.QRadioButton(component + (' (%d without)' % missing if missing else ''))
            button.setEnabled(missing < len(assets))
            self.component_buttons[component] = button
            components.addWidget(button)
        components.addStretch()
        chosen = value('component', 'main')
        (self.component_buttons.get(chosen) or self.component_buttons['main']).setChecked(True)
        form.addRow('Component', components)
        self.subfolders = QtWidgets.QCheckBox('A subfolder per file sequence, named after it (clean name)')
        self.subfolders.setChecked(bool(value('subfolders', True)))
        form.addRow('Sequences', self.subfolders)
        self.in_out = QtWidgets.QCheckBox('Only the frames from in to out (where in/out points are set)')
        self.in_out.setToolTip('Uses %s in/out points. Movies and stills are always copied whole.'
                               % ('the collection\'s' if collection else 'your'))
        self.in_out.setChecked(bool(value('in_out', False)))
        form.addRow('', self.in_out)
        self.overwrite = QtWidgets.QComboBox()
        self.overwrite.addItem('Keep them (skip)', False)
        self.overwrite.addItem('Overwrite them', True)
        self.overwrite.setCurrentIndex(1 if value('overwrite', False) else 0)
        form.addRow('Existing files', self.overwrite)
        self.make_reads = QtWidgets.QCheckBox('Create Read nodes for the copied footage')
        self.make_reads.setChecked(bool(value('nuke_reads', True)))
        self.frame_mode = QtWidgets.QComboBox()
        self.frame_mode.addItem('Leave as it is', 'none')
        start = reads.script_frames()[0] if self.nuke else 1001
        self.frame_mode.addItem('Start at the script start frame (%d)' % start, 'start')
        self.frame_mode.addItem('Expression (empty)', 'expression')
        self.frame_mode.setCurrentIndex(max(0, self.frame_mode.findData(value('frame_mode', 'none'))))
        self.read_colorspace = QtWidgets.QCheckBox('Set colorspace')
        self.read_colorspace.setToolTip('Set each Read node to the asset\'s color space (main and highres). '
                                        'Off: Nuke chooses it from the file, as for a Read you make yourself.')
        self.read_colorspace.setChecked(bool(value('nuke_colorspace', False)))
        if self.nuke:
            form.addRow('Nuke', self.make_reads)
            form.addRow('Read frame mode', self.frame_mode)
            form.addRow('', self.read_colorspace)
            self.make_reads.toggled.connect(self.update_read_options)
            self.update_read_options()
        else:
            self.make_reads.hide()
            self.frame_mode.hide()
            self.read_colorspace.hide()
        layout.addLayout(form)
        self.progress = QtWidgets.QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.copy_button = buttons.addButton('Copy', QtWidgets.QDialogButtonBox.AcceptRole)
        self.copy_button.clicked.connect(self.start)
        self.close_button = buttons.button(QtWidgets.QDialogButtonBox.Close)
        self.close_button.clicked.connect(self.reject)
        self.cancel_button = buttons.addButton('Cancel copy', QtWidgets.QDialogButtonBox.RejectRole)
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        layout.addWidget(buttons)
        for signal in (self.base.textChanged, self.template.textChanged):
            signal.connect(self.update_destination)
        self.update_destination()

    def update_read_options(self, *_):
        for widget in (self.frame_mode, self.read_colorspace):
            widget.setEnabled(self.make_reads.isChecked() and self.make_reads.isEnabled())

    def component(self):
        return next(name for name, button in self.component_buttons.items() if button.isChecked())

    def browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Base folder', self.base.text())
        if folder:
            self.base.setText(folder)

    def root(self):
        return plan_module.destination_root(self.base.text(), self.template.text(), self.collection)

    def update_destination(self, *_):
        try:
            self.destination.setText(str(self.root()))
            self.destination.setStyleSheet('')
        except Exception as error:
            self.destination.setText(str(error))
            self.destination.setStyleSheet('color: #ff6060;')

    def show_message(self, text, error=False):
        self.message.setStyleSheet('color: #ff6060;' if error else '')
        self.message.setText(text)

    def remember(self):
        if self.preferences is None:
            return
        try:
            choices = {self.template_key: self.template.text(), 'component': self.component(),
                       'subfolders': self.subfolders.isChecked(), 'in_out': self.in_out.isChecked(),
                       'overwrite': self.overwrite.currentData()}
            if self.nuke:  # the base folder in Nuke is the script's; remember only a chosen one
                choices.update(nuke_reads=self.make_reads.isChecked(), frame_mode=self.frame_mode.currentData(),
                               nuke_colorspace=self.read_colorspace.isChecked())
            else:
                choices['base'] = self.base.text().strip()
            remembered = (self.preferences.data.get('display', {}).get('copy_assets') or {})
            # Kept beside what the other host remembered (its own template).
            self.preferences.set_display(copy_assets=dict(remembered, **choices))
        except Exception:
            pass  # Remembering the choices is a convenience only.

    def start(self):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            root = self.root()
            self.items, self.skipped = plan_module.plan(
                self.assets, self.component(), root, self.subfolders.isChecked(), self.in_out.isChecked(), self.trim_for)
        except Exception as error:
            self.show_message(str(error), error=True)
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not self.items:
            self.show_message('Nothing to copy: ' + '; '.join('%s: %s' % item for item in self.skipped), error=True)
            return
        self.remember()
        total = plan_module.total_files(self.items)
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.progress.show()
        self.set_running(True)
        self.show_message('Copying %d file(s) of %d asset(s) to %s…' % (total, len(self.items), root))
        self.worker = CopyWorker(self.items, bool(self.overwrite.currentData()), self)
        self.worker.progress.connect(self.copied)
        self.worker.done.connect(self.finished_copy)
        self.worker.failed.connect(self.failed_copy)
        self.worker.start()

    def set_running(self, running):
        for widget in (self.base, self.template, self.subfolders, self.in_out, self.overwrite, self.make_reads,
                       self.frame_mode, self.read_colorspace, self.copy_button, self.close_button,
                       *self.component_buttons.values()):
            widget.setEnabled(not running)
        if self.nuke:
            self.update_read_options()
        self.cancel_button.setVisible(running)

    def copied(self, done, total, path):
        self.progress.setValue(done)
        self.progress.setFormat('%%v / %%m · %s' % Path(path).name)

    def cancel(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.show_message('Cancelling after the current file…')

    def failed_copy(self, message):
        self.set_running(False)
        self.show_message('The copy stopped: %s' % message, error=True)

    def finished_copy(self, result):
        self.set_running(False)
        parts = ['%d file(s) copied' % result['copied']]
        if result['existing']:
            parts.append('%d existing file(s) kept' % result['existing'])
        if self.skipped:
            parts.append('skipped: ' + '; '.join('%s (%s)' % item for item in self.skipped))
        if result['cancelled']:
            self.show_message('Cancelled. ' + ', '.join(parts) + '.', error=True)
            return
        warnings = []
        if self.nuke and self.make_reads.isChecked():
            try:
                nodes, warnings = reads.create_reads(self.items, self.component(), self.frame_mode.currentData(),
                                                     self.read_colorspace.isChecked())
                parts.append('%d Read node(s) created' % len(nodes))
            except Exception as error:
                warnings.append('Read nodes: %s' % error)
        self.result_message = 'Copied %d asset(s) to %s: %s.' % (len(self.items), self.root(), ', '.join(parts))
        self.show_message(self.result_message + ('\n' + '\n'.join(warnings) if warnings else ''), error=bool(warnings))
        self.copy_button.setText('Copy again')

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            return  # Cancel the copy first.
        super().reject()


def run_dialog(assets, config, trim_for=None, collection='', preferences=None, parent=None):
    from tinylib.qt import exec_dialog
    dialog = CopyDialog(assets, config, trim_for, collection, preferences, parent)
    exec_dialog(dialog)
    return dialog.result_message
