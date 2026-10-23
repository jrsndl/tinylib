from .qt import QtCore, QtGui, QtWidgets


_ICONS = {}
SETTINGS_PREFIX = 'settings:'  # item data of a "… settings…" entry: the prefix and the action id


def action_icon(path):
    if not path:
        return QtGui.QIcon()
    if str(path) not in _ICONS:
        _ICONS[str(path)] = QtGui.QIcon(str(path))
    return _ICONS[str(path)]


class ActionPicker(QtWidgets.QWidget):
    requested = QtCore.Signal(str)
    chosen = QtCore.Signal(str)  # The user picked an action in the list (to remember it).

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QtWidgets.QComboBox()
        self.combo.setMinimumWidth(100)
        self.combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        # The last action the user picked: shown again whenever it is available.
        self.preferred = ''
        self.combo.activated.connect(self._picked)
        self.button = QtWidgets.QPushButton('Run')
        self.button.clicked.connect(lambda: self.requested.emit(self.combo.currentData()) if self.combo.currentData() else None)
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.button)
        self.populate([])

    def _picked(self, index):
        identifier = self.combo.itemData(index)
        if identifier and identifier != self.preferred:
            self.preferred = identifier
            self.chosen.emit(identifier)

    def populate(self, actions, prompt='Pick action…', scope='', configurable=()):
        """List ``actions``. ``prompt`` is the first entry and ``scope`` the tooltip: what the
        action will run on (the selection, or every asset shown). ``configurable`` actions get a
        "… settings…" entry, listed whatever is selected."""
        self.setToolTip(scope)
        self.combo.setToolTip(scope)
        # Called on every selection change; rebuilding reads icon PNGs from the network.
        signature = (tuple(action.id for action in actions), tuple(action.id for action in configurable), prompt)
        if signature == getattr(self, '_signature', None):
            return
        self._signature = signature
        current = self.combo.currentData()
        self.combo.clear()
        self.combo.addItem(prompt, '')
        for action in sorted(actions, key=lambda a: (a.manifest['category'], a.manifest['name'])):
            label = action.manifest['category'] + ' / ' + action.manifest['name']
            self.combo.addItem(action_icon(action.icon), label, action.id)
        if configurable:
            self.combo.insertSeparator(self.combo.count())
        for action in sorted(configurable, key=lambda a: (a.manifest['category'], a.manifest['name'])):
            self.combo.addItem(action_icon(action.icon), action.manifest['category'] + ' / ' + action.settings_label,
                               SETTINGS_PREFIX + action.id)
        index = self.combo.findData(current) if current else -1
        if index <= 0 and self.preferred:
            index = self.combo.findData(self.preferred)
        if index > 0:
            self.combo.setCurrentIndex(index)
        self.button.setEnabled(bool(actions) or bool(configurable))
