"""Open in Player settings: which player, its arguments, component and how assets are grouped."""
import importlib.util
import sys
from pathlib import Path

from tinylib.qt import QtCore, QtWidgets


def sibling(name):
    key = '_tinylib_open_in_player_' + name
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


player_settings = sibling('player_settings')
TOKENS = ('{first_file} first frame file (or the file) · {path} sequence with #### · {printf} with %04d · '
          '{rv} RV range notation · {xstudio} xSTUDIO range notation · {first} {last} frame range · {count} frames · {fps} · {folder} · {name}\n'
          'An argument whose tokens are all empty (the range of a movie or still) is left out, '
          'with the option before it ("-in {first}"). '
          'Use "double quotes" around an argument with spaces.')


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, assets, choice, settings, config, preferences, trim_for=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Open in Player')
        self.setMinimumWidth(620)
        self.assets, self.settings, self.config = assets, settings, config
        self.preferences, self.trim_for = preferences, trim_for
        self.opened = None
        names = player_settings.players(settings, config)
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel('Right-click assets and choose <b>Open in Player</b> to open them with these '
                                 'settings. Players are the tools set under [tools] in tinylib.ini.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QtWidgets.QFormLayout()
        self.tool = QtWidgets.QComboBox()
        for name in names:
            found = player_settings.available(settings, name)
            self.tool.addItem(name if found else '%s (not found)' % name, name)
        form.addRow('Player', self.tool)
        self.arguments = QtWidgets.QLineEdit()
        self.arguments.setToolTip(TOKENS)
        form.addRow('Arguments per asset', self.arguments)
        self.in_out_arguments = QtWidgets.QLineEdit()
        self.in_out_arguments.setPlaceholderText('optional, e.g. -inPoint {first} -outPoint {last}')
        self.in_out_arguments.setToolTip('Added after the asset when the player opens one asset whose in/out points '
                                         'narrow its frames (options a player takes only once, e.g. DJV).')
        form.addRow('In/out arguments (one asset)', self.in_out_arguments)
        self.before = QtWidgets.QLineEdit()
        self.before.setPlaceholderText('optional, e.g. -fullscreen')
        form.addRow('Arguments before the assets', self.before)
        self.fields = {'arguments': self.arguments, 'in_out_arguments': self.in_out_arguments, 'before': self.before}
        help_text = QtWidgets.QLabel(TOKENS)
        help_text.setWordWrap(True)
        help_text.setObjectName('muted')
        form.addRow('', help_text)
        self.component = QtWidgets.QComboBox()
        for component in player_settings.COMPONENTS:
            self.component.addItem(component, component)
        self.component.setCurrentIndex(max(0, self.component.findData(choice.get('component', 'main'))))
        form.addRow('Component', self.component)
        self.in_out = QtWidgets.QCheckBox('Only the frames from in to out, where in/out points are set')
        self.in_out.setChecked(bool(choice.get('in_out', True)))
        form.addRow('', self.in_out)
        self.grouping = QtWidgets.QComboBox()
        self.grouping.addItem('All assets in one player', True)
        self.grouping.addItem('One player per asset', False)
        self.grouping.setCurrentIndex(0 if choice.get('one_process', True) else 1)
        form.addRow('Several assets', self.grouping)
        self.preview = QtWidgets.QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        form.addRow('Command', self.preview)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.save_button = buttons.addButton('Save', QtWidgets.QDialogButtonBox.AcceptRole)
        self.open_button = buttons.addButton('Save and open %d asset%s' % (len(assets), '' if len(assets) == 1 else 's'),
                                             QtWidgets.QDialogButtonBox.AcceptRole)
        self.open_button.setVisible(bool(assets))  # settings opened without assets: Save only
        self.save_button.clicked.connect(lambda: self.finish(False))
        self.open_button.clicked.connect(lambda: self.finish(True))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if not names:
            for widget in (self.tool, self.arguments, self.in_out_arguments, self.before, self.component, self.in_out, self.grouping,
                           self.save_button, self.open_button):
                widget.setEnabled(False)
            self.preview.setText('No players are set up. An administrator adds them under [tools] in tinylib.ini, '
                                 'e.g. rv = C:/Program Files/ShotGrid/RV-2024/bin/rv.exe')
            return
        index = self.tool.findData(choice.get('tool'))
        self.tool.setCurrentIndex(max(0, index))
        self.last_tool = self.tool.currentData()
        defaults = player_settings.preset(self.last_tool, config)
        for field, widget in self.fields.items():
            widget.setText(choice[field] if index >= 0 and field in choice else defaults[field])
        self.tool.currentIndexChanged.connect(self.tool_changed)
        for signal in (self.arguments.textChanged, self.in_out_arguments.textChanged, self.before.textChanged,
                       self.component.currentIndexChanged,
                       self.in_out.toggled, self.grouping.currentIndexChanged):
            signal.connect(self.update_preview)
        self.update_preview()

    def tool_changed(self, *_):
        # A preset left as it was follows the player; arguments you typed stay.
        old = player_settings.preset(self.last_tool, self.config)
        new = player_settings.preset(self.tool.currentData(), self.config)
        for field, widget in self.fields.items():
            if widget.text().strip() == old[field]:
                widget.setText(new[field])
        self.last_tool = self.tool.currentData()
        self.update_preview()

    def choice(self):
        return {'tool': self.tool.currentData(), 'arguments': self.arguments.text().strip(),
                'in_out_arguments': self.in_out_arguments.text().strip(),
                'before': self.before.text().strip(), 'component': self.component.currentData(),
                'in_out': self.in_out.isChecked(), 'one_process': bool(self.grouping.currentData())}

    def update_preview(self, *_):
        choice = self.choice()
        try:
            reason = player_settings.problem(choice, self.settings, self.config)
            lists, skipped = player_settings.commands(self.assets, choice, self.trim_for)
            if not self.assets:
                example = player_settings.example_asset()
                lists, skipped = player_settings.commands([example], choice, lambda asset: (1010, 1020))
            text = '\n'.join(' '.join(_quoted(part) for part in [choice['tool']] + arguments) for arguments in lists[:3])
            if not self.assets:
                text = 'For example, a sequence with in/out points 1010–1020:\n' + text
            if len(lists) > 3:
                text += '\n… %d more' % (len(lists) - 3)
            if skipped:
                text += '\n%d asset(s) without %s are skipped.' % (skipped, choice['component'])
            if not lists and self.assets:
                text = 'None of the assets has a %s.' % choice['component']
            self.preview.setStyleSheet('color: #ff6060;' if reason or not lists else '')
            lists = lists if self.assets else []
            self.preview.setText(reason + ('\n' if reason else '') + text)
            self.open_button.setEnabled(not reason and bool(lists))
        except ValueError as error:
            self.preview.setStyleSheet('color: #ff6060;')
            self.preview.setText(str(error))
            self.open_button.setEnabled(False)

    def finish(self, open_now):
        choice = self.choice()
        try:
            for field in player_settings.FIELDS:
                player_settings.split(choice[field])
            self.preferences.set_display(**{player_settings.PREFERENCE: choice})
            if open_now:
                self.opened = player_settings.open_assets(self.assets, choice, self.settings, self.config, self.trim_for)
            else:
                self.opened = 'Open in Player now uses %s.' % choice['tool']
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, 'Open in Player', str(error))
            return
        self.accept()


def _quoted(text):
    return '"%s"' % text if ' ' in text else text


def run_dialog(assets, choice, settings, config, preferences, trim_for=None, parent=None):
    """Show the settings; returns the status message, or None when cancelled."""
    from tinylib.qt import exec_dialog
    dialog = SettingsDialog(assets, choice, settings, config, preferences, trim_for, parent)
    exec_dialog(dialog)
    return dialog.opened
