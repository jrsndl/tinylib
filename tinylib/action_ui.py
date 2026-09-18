from .qt import QtCore, QtGui, QtWidgets


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
        current = self.combo.currentData()
        self.combo.clear()
        self.combo.addItem('Pick action…', '')
        for action in sorted(actions, key=lambda a: (a.manifest['category'], a.manifest['name'])):
            label = action.manifest['category'] + ' / ' + action.manifest['name']
            self.combo.addItem(QtGui.QIcon(str(action.icon)) if action.icon else QtGui.QIcon(), label, action.id)
        index = self.combo.findData(current)
        if index > 0:
            self.combo.setCurrentIndex(index)
        self.button.setEnabled(bool(actions))
