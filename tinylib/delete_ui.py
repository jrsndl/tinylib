"""Confirm the deletion of assets marked in edit mode."""
from .qt import QtCore, QtWidgets
from .views import AssetModel, Cards, DetailsView, TinyPreview


def _checked(value):
    return int(getattr(value, 'value', value)) == int(getattr(QtCore.Qt.Checked, 'value', 2))


class ConfirmModel(AssetModel):
    """The main view's details columns, after a "Delete" checkbox column."""
    headers = ['Delete'] + AssetModel.headers

    def __init__(self, assets, parent):
        super().__init__(parent)
        self.assets = list(assets)
        self.confirmed = [True] * len(self.assets)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        if index.column() == 0:
            if role == QtCore.Qt.CheckStateRole:
                return QtCore.Qt.Checked if self.confirmed[index.row()] else QtCore.Qt.Unchecked
            if role == QtCore.Qt.UserRole:
                return self.assets[index.row()]
            if role == QtCore.Qt.ToolTipRole:
                return 'Uncheck to keep this asset.'
            return None
        return super().data(self.createIndex(index.row(), index.column() - 1), role)

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if index.isValid() and index.column() == 0 and role == QtCore.Qt.CheckStateRole:
            self.confirmed[index.row()] = _checked(value)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def flags(self, index):
        if not index.isValid():
            return QtCore.Qt.NoItemFlags
        flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        return flags | QtCore.Qt.ItemIsUserCheckable if index.column() == 0 else flags

    def set_all(self, confirmed):
        self.confirmed = [confirmed] * len(self.assets)
        if self.assets:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.assets) - 1, 0))


class DeleteDialog(QtWidgets.QDialog):
    """Lists the marked assets like the details view; each can be unchecked to keep it."""
    def __init__(self, assets, cache, parent=None):
        super().__init__(parent)
        self.setWindowTitle('TinyLib · Delete assets')
        self.resize(1100, 560)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel()
        layout.addWidget(self.summary)
        self.model = ConfirmModel(assets, self)
        self.cards = Cards(self, cache)
        self.table = DetailsView(self.cards)
        self.table.setModel(self.model)
        self.table.setDragEnabled(False)
        self.table.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.table.setItemDelegateForColumn(1, QtWidgets.QStyledItemDelegate(self.table))
        self.table.setItemDelegateForColumn(2, TinyPreview(self.cards, self.table))
        for column, width in enumerate([60, 230, 90, 140, 110, 85, 95, 75, 75, 95, 240]):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        toggles = QtWidgets.QHBoxLayout()
        for label, value in (('Check all', True), ('Uncheck all', False)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _=False, value=value: self.model.set_all(value))
            toggles.addWidget(button)
        toggles.addStretch()
        layout.addLayout(toggles)
        self.database_only = QtWidgets.QRadioButton('Delete from database only')
        self.with_storage = QtWidgets.QRadioButton('Delete from storage and database')
        self.database_only.setChecked(True)
        self.database_only.setToolTip('The asset leaves the library; its files stay where they are.')
        self.with_storage.setToolTip("Also deletes each asset's folder <library>/<category>/<name>. "
                                     'This cannot be undone.')
        group = QtWidgets.QButtonGroup(self)
        for button in (self.database_only, self.with_storage):
            group.addButton(button)
            layout.addWidget(button)
        self.warning = QtWidgets.QLabel("Each asset's folder is deleted permanently. Files that are not in "
                                        'the folder TinyLib ingest made for the asset are kept and listed.')
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet('color: #ef5b5b;')
        layout.addWidget(self.warning)
        buttons = QtWidgets.QDialogButtonBox()
        self.delete_button = buttons.addButton('Delete', QtWidgets.QDialogButtonBox.DestructiveRole)
        cancel = buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        # Enter must never start a deletion: Cancel is the default, nothing else takes Enter.
        for button in self.findChildren(QtWidgets.QPushButton):
            button.setAutoDefault(False)
        cancel.setDefault(True)
        self.delete_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.with_storage.toggled.connect(self.update_summary)
        self.model.dataChanged.connect(self.update_summary)
        self.update_summary()

    def confirmed_assets(self):
        return [asset for asset, keep in zip(self.model.assets, self.model.confirmed) if keep]

    def remove_files(self):
        return self.with_storage.isChecked()

    def update_summary(self, *_):
        count, total = sum(self.model.confirmed), len(self.model.assets)
        where = 'from storage and the database' if self.remove_files() else 'from the database only'
        self.summary.setText('%d of %d marked asset(s) will be deleted %s. Uncheck an asset to keep it.'
                             % (count, total, where))
        self.warning.setVisible(self.remove_files())
        self.delete_button.setText('Delete %d asset%s' % (count, '' if count == 1 else 's') if count else
                                   'Delete none, write other changes')
