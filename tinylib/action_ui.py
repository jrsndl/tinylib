from .qt import QtCore, QtGui, QtWidgets


_ICONS = {}


def _icon(path):
    if not path:
        return QtGui.QIcon()
    if str(path) not in _ICONS:
        _ICONS[str(path)] = QtGui.QIcon(str(path))
    return _ICONS[str(path)]


class ActionPicker(QtWidgets.QWidget):
    requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QtWidgets.QComboBox()
        self.combo.setMinimumWidth(100)
        self.combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.button = QtWidgets.QPushButton('Run')
        self.button.clicked.connect(lambda: self.requested.emit(self.combo.currentData()) if self.combo.currentData() else None)
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.button)
        self.populate([])

    def populate(self, actions):
        # Called on every selection change; rebuilding reads icon PNGs from the network.
        signature = tuple(action.id for action in actions)
        if signature == getattr(self, '_signature', None):
            return
        self._signature = signature
        current = self.combo.currentData()
        self.combo.clear()
        self.combo.addItem('Pick action…', '')
        for action in sorted(actions, key=lambda a: (a.manifest['category'], a.manifest['name'])):
            label = action.manifest['category'] + ' / ' + action.manifest['name']
            self.combo.addItem(_icon(action.icon), label, action.id)
        index = self.combo.findData(current)
        if index > 0:
            self.combo.setCurrentIndex(index)
        self.button.setEnabled(bool(actions))
