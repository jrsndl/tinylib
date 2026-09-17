"""Named collection scratchpad with multi-asset drag/drop."""
import json
from .qt import QtCore, QtWidgets
from .preferences import asset_key

ASSET_MIME = 'application/x-tinylib-asset-keys'


class CollectionList(QtWidgets.QListWidget):
    assets_dropped = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(ASSET_MIME):
            event.setDropAction(QtCore.Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        try:
            keys = json.loads(bytes(event.mimeData().data(ASSET_MIME)).decode('utf-8'))
            if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                raise ValueError('Invalid asset list')
        except (ValueError, UnicodeDecodeError, TypeError):
            event.ignore()
            return
        self.assets_dropped.emit(keys)
        event.setDropAction(QtCore.Qt.CopyAction)
        event.accept()


class CollectionsPanel(QtWidgets.QWidget):
    changed = QtCore.Signal()
    selected = QtCore.Signal()
    preview_requested = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        self.preferences = preferences
        self.lookup = {}
        self.setMinimumWidth(230)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.addWidget(QtWidgets.QLabel('COLLECTIONS'))
        self.combo = QtWidgets.QComboBox()
        self.combo.currentIndexChanged.connect(self.populate)
        layout.addWidget(self.combo)
        controls = QtWidgets.QHBoxLayout()
        for label, callback in [('New', self.create), ('Rename', self.rename), ('Delete', self.delete)]:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        hint = QtWidgets.QLabel('Drop selected assets here to pick them.\nPicked assets are hidden from the main view.')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        layout.addWidget(hint)
        self.items = CollectionList()
        self.items.assets_dropped.connect(self.add_keys)
        self.items.itemSelectionChanged.connect(self.selected)
        self.items.itemDoubleClicked.connect(lambda *_: self.preview_requested.emit())
        layout.addWidget(self.items, 1)
        self.count = QtWidgets.QLabel()
        layout.addWidget(self.count)
        remove = QtWidgets.QPushButton('Remove selected from collection')
        remove.clicked.connect(self.remove)
        layout.addWidget(remove)
        self.refresh()

    def refresh(self, identifier=None):
        identifier = identifier or self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        for collection in self.preferences.collections:
            self.combo.addItem(collection['name'], collection['id'])
        index = self.combo.findData(identifier)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        self.populate()

    def populate(self, *_):
        selected = {asset_key(a) for a in self.selected_assets()}
        self.items.blockSignals(True)
        self.items.clear()
        identifier = self.combo.currentData()
        entries = self.preferences.collection(identifier)['assets'] if identifier else {}
        for key, ref in entries.items():
            asset = self.lookup.get(key)
            label = ref['name'] + ('' if asset else '  [unavailable]')
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, key)
            item.setToolTip(ref['library'] + '\n' + ref['main'])
            self.items.addItem(item)
            item.setSelected(key in selected)
        self.items.blockSignals(False)
        self.count.setText('%d picked assets' % len(entries))
        self.selected.emit()

    def selected_assets(self):
        return [self.lookup[item.data(QtCore.Qt.UserRole)] for item in self.items.selectedItems()
                if item.data(QtCore.Qt.UserRole) in self.lookup]

    def create(self):
        try:
            identifier = self.preferences.create_collection()
            self.refresh(identifier)
            self.changed.emit()
        except Exception as error:
            self.error.emit(str(error))

    def rename(self):
        identifier = self.combo.currentData()
        if not identifier:
            return
        name, accepted = QtWidgets.QInputDialog.getText(self, 'Rename collection', 'Name', text=self.combo.currentText())
        if accepted:
            try:
                self.preferences.rename_collection(identifier, name)
                self.refresh(identifier)
                self.changed.emit()
            except Exception as error:
                self.error.emit(str(error))

    def delete(self):
        identifier = self.combo.currentData()
        if identifier:
            try:
                self.preferences.delete_collection(identifier)
                self.refresh()
                self.changed.emit()
            except Exception as error:
                self.error.emit(str(error))

    def add_keys(self, keys):
        assets = [self.lookup[key] for key in keys if key in self.lookup]
        if not assets:
            return
        try:
            identifier = self.combo.currentData() or self.preferences.create_collection()
            self.preferences.add_assets(identifier, assets)
            self.refresh(identifier)
            self.changed.emit()
        except Exception as error:
            self.error.emit(str(error))

    def remove(self):
        identifier = self.combo.currentData()
        if identifier:
            try:
                self.preferences.remove_assets(identifier, [i.data(QtCore.Qt.UserRole) for i in self.items.selectedItems()])
                self.populate()
                self.changed.emit()
            except Exception as error:
                self.error.emit(str(error))
