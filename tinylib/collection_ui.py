"""Collection scratchpad with shared view modes, playback and JSON exchange."""
import json
from pathlib import Path
from .qt import QtCore, QtWidgets
from .preferences import asset_key, ASSET_MIME
from .views import AssetModel, Grid, DetailsView
from .action_ui import ActionPicker
from .path_format import format_assets


class CollectionGrid(Grid):
    assets_dropped = QtCore.Signal(list)

    def __init__(self, cache, parent=None):
        super().__init__(cache, parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDrop)

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


class CollectionTable(DetailsView):
    assets_dropped = QtCore.Signal(list)
    dragEnterEvent = CollectionGrid.dragEnterEvent
    dragMoveEvent = CollectionGrid.dragMoveEvent
    dropEvent = CollectionGrid.dropEvent

    def __init__(self, cards, parent=None):
        super().__init__(cards, parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDrop)


class CollectionsPanel(QtWidgets.QWidget):
    changed = QtCore.Signal()
    selected = QtCore.Signal()
    preview_requested = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(self, preferences, cache, access, parent=None):
        super().__init__(parent)
        self.preferences, self.access = preferences, access
        self.lookup = {}
        self.setMinimumWidth(280)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.addWidget(QtWidgets.QLabel('COLLECTIONS'))
        self.combo = QtWidgets.QComboBox()
        self.combo.currentIndexChanged.connect(self.populate)
        layout.addWidget(self.combo)
        controls = QtWidgets.QHBoxLayout()
        for label, callback in [('New', self.create), ('Rename', self.rename), ('Delete', self.delete),
                                ('Copy', self.copy_paths), ('Export', self.export_file), ('Import', self.import_file)]:
            button = QtWidgets.QPushButton(label)
            button.setStyleSheet('padding: 5px 4px;')
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        controls = QtWidgets.QHBoxLayout()
        self.view_mode = QtWidgets.QComboBox()
        self.view_mode.addItems(['Tiles', 'Details', 'List'])
        self.view_mode.currentTextChanged.connect(self.change_view)
        controls.addWidget(self.view_mode)
        self.play_button = QtWidgets.QPushButton('Play all')
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self.set_play_all)
        controls.addWidget(self.play_button)
        layout.addLayout(controls)
        self.grid = CollectionGrid(cache)
        self.items = self.grid
        self.grid.cards.width = 180
        self.model = AssetModel(self)
        self.grid.setModel(self.model)
        self.table = CollectionTable(self.grid.cards)
        self.table.setModel(self.model)
        self.table.setSelectionModel(self.grid.selectionModel())
        for column, width in enumerate([160, 85, 100, 85, 80, 80, 70, 70, 65, 200]):
            self.table.setColumnWidth(column, width)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.table)
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self.selected.emit())
        for view in (self.grid, self.table):
            view.assets_dropped.connect(self.add_keys)
            view.doubleClicked.connect(lambda *_: self.preview_requested.emit())
        cache.changed.connect(self.table.viewport().update)
        hint = QtWidgets.QLabel('Drop assets here to pick them. Picked assets hide from the main view.')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        layout.addWidget(hint)
        layout.addWidget(self.stack, 1)
        self.count = QtWidgets.QLabel()
        layout.addWidget(self.count)
        remove = QtWidgets.QPushButton('Remove selected from collection')
        remove.clicked.connect(self.remove)
        layout.addWidget(remove)
        self.actions = ActionPicker()
        self.actions.setToolTip('Run an action on every asset in this collection.')
        layout.addWidget(self.actions)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(85)
        self.timer.timeout.connect(self.animate)
        self.timer.start()
        self.refresh()

    def change_view(self, mode):
        self.stack.setCurrentWidget(self.table if mode == 'Details' else self.grid)
        if mode != 'Details':
            self.grid.configure_mode(mode)

    def set_play_all(self, enabled):
        self.grid.cards.play_all = enabled
        self.grid.cards.hover_row = -1
        self.play_button.setText('Stop all' if enabled else 'Play all')
        self.stack.currentWidget().viewport().update()

    def animate(self):
        cards = self.grid.cards
        if self.isVisible() and self.view_mode.currentText() != 'List' and (cards.play_all or cards.hover_row >= 0):
            cards.frame = (cards.frame + 1) % 24
            self.stack.currentWidget().viewport().update()

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

    def entries(self):
        identifier = self.combo.currentData()
        return self.preferences.collection(identifier)['assets'] if identifier else {}

    def populate(self, *_):
        self.model.collection_origin = {'collection': self.combo.currentData(), 'preferences': str(self.preferences.path.resolve())}
        selected = {asset_key(a) for a in self.selected_assets()}
        assets = []
        for key, ref in self.entries().items():
            if not self.access.can(ref['library_root'], 'view'):
                continue
            asset = self.lookup.get(key)
            if asset is None:
                asset = dict(ref, name=ref['name'] + ' [unavailable]', thumb='', filmstrip='',
                             _unavailable=True, _collection_key=key)
            assets.append(asset)
        self.model.replace(assets)
        selection = self.grid.selectionModel()
        for row, asset in enumerate(assets):
            if asset_key(asset) in selected:
                selection.select(self.model.index(row, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        self.count.setText('%d picked assets' % len(assets))
        self.selected.emit()

    def selected_assets(self):
        rows = sorted({index.row() for index in self.grid.selectionModel().selectedIndexes()})
        return [self.model.assets[row] for row in rows if not self.model.assets[row].get('_unavailable')]

    def action_assets(self):
        entries = self.entries()
        assets = [self.lookup[key] for key in entries if key in self.lookup]
        return assets if len(assets) == len(entries) else []

    def create(self):
        try:
            self.refresh(self.preferences.create_collection())
            self.changed.emit()
        except Exception as error:
            self.error.emit(str(error))

    def rename(self):
        identifier = self.combo.currentData()
        if identifier:
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
        self.access.refresh()
        assets = [self.lookup[key] for key in keys if key in self.lookup and self.access.can(self.lookup[key]['library_root'], 'view')]
        if assets:
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
                rows = {index.row() for index in self.grid.selectionModel().selectedIndexes()}
                keys = [self.model.assets[row].get('_collection_key') or asset_key(self.model.assets[row]) for row in rows]
                self.preferences.remove_assets(identifier, keys)
                self.populate()
                self.changed.emit()
            except Exception as error:
                self.error.emit(str(error))

    def remove_keys(self, identifier, keys):
        self.preferences.remove_assets(identifier, keys)
        self.populate()
        self.changed.emit()

    def copy_paths(self):
        assets = list(self.model.assets)
        selected = self.selected_assets()
        if selected and len(selected) < len(assets):
            assets = selected
        if not assets:
            self.error.emit('The current collection has no accessible assets to copy.')
            return
        notation = self.preferences.data.get('display', {}).get('path_notation', 'nuke')
        QtWidgets.QApplication.clipboard().setText(format_assets(assets, notation))

    def downloads(self):
        return QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DownloadLocation) or str(Path.home() / 'Downloads')

    def export_file(self):
        identifier = self.combo.currentData()
        if identifier:
            name = ''.join(c if c.isalnum() or c in '-_ ' else '_' for c in self.combo.currentText())
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export collection', str(Path(self.downloads()) / (name + '.json')), 'JSON (*.json)')
            if path:
                try:
                    self.access.refresh()
                    if any(not self.access.can(ref['library_root'], 'view') for ref in self.entries().values()):
                        raise PermissionError('This collection contains a library you cannot access.')
                    self.preferences.export_collection(identifier, path)
                except Exception as error:
                    self.error.emit(str(error))

    def import_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Import collection', self.downloads(), 'JSON (*.json)')
        if path:
            try:
                self.refresh(self.preferences.import_collection(path))
                self.changed.emit()
            except Exception as error:
                self.error.emit(str(error))
