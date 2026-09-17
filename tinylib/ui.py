"""Studio browser: library tree, filters, multi-selection and collections."""
from pathlib import Path
import math
from .qt import QtCore, QtGui, QtWidgets
from .settings import load_settings
from .preferences import Preferences, asset_key
from .filters import accepts
from .collection_ui import CollectionsPanel
from .views import STYLE, Loader, ImageCache, AssetModel, Grid, DetailsView, PreviewDialog


class StarButton(QtWidgets.QPushButton):
    """Draw the stars directly so they do not depend on installed symbol fonts."""
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.filled = False
        self.setFixedSize(30, 30)
        self.setAccessibleName('Set %d stars' % value)

    def set_filled(self, filled):
        self.filled = filled
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        color = QtGui.QColor('#d4aa6c' if self.isEnabled() else '#626262')
        painter.setPen(QtGui.QPen(color, 1.4))
        painter.setBrush(color if self.filled else QtCore.Qt.NoBrush)
        points = []
        for index in range(10):
            angle = index * math.pi / 5 - math.pi / 2
            radius = 10 if index % 2 == 0 else 4.5
            points.append(QtCore.QPointF(15 + radius * math.cos(angle), 15 + radius * math.sin(angle)))
        painter.drawPolygon(QtGui.QPolygonF(points))
        painter.end()


class NumericFilter(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, label, maximum, decimals=0, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel(label))
        self.operator = QtWidgets.QComboBox()
        for text, value in [('Any', ''), ('>', '>'), ('<', '<'), ('=', '=')]:
            self.operator.addItem(text, value)
        self.value = QtWidgets.QDoubleSpinBox()
        self.value.setDecimals(decimals)
        self.value.setRange(0, maximum)
        self.value.setEnabled(False)
        self.value.setMaximumWidth(110)
        layout.addWidget(self.operator)
        layout.addWidget(self.value)
        self.operator.currentIndexChanged.connect(self.update_operator)
        self.value.valueChanged.connect(lambda *_: self.changed.emit())

    def update_operator(self, *_):
        self.value.setEnabled(bool(self.operator.currentData()))
        self.changed.emit()

    def rule(self):
        return self.operator.currentData(), self.value.value()


class Browser(QtWidgets.QWidget):
    def __init__(self, config_path=None, preferences_path=None):
        super().__init__()
        self.settings = load_settings(config_path)
        self.preferences = Preferences(preferences_path)
        self.assets, self.errors = [], []
        self.selection_source = 'main'
        self._filtering = False
        self.setWindowTitle('TinyLib · Studio library')
        self.resize(1580, 900)
        self.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        top = QtWidgets.QHBoxLayout()
        heading = QtWidgets.QLabel('TinyLib  /  Studio library')
        heading.setObjectName('heading')
        top.addWidget(heading)
        top.addStretch()
        config_button = QtWidgets.QPushButton('Configuration…')
        config_button.clicked.connect(self.configuration)
        top.addWidget(config_button)
        self.refresh = QtWidgets.QPushButton('Refresh')
        self.refresh.clicked.connect(self.reload)
        top.addWidget(self.refresh)
        ingest = QtWidgets.QPushButton('+ Ingest asset')
        ingest.setObjectName('primary')
        ingest.clicked.connect(self.ingest)
        top.addWidget(ingest)
        layout.addLayout(top)
        self.make_filters(layout)
        self.splitter = QtWidgets.QSplitter()
        self.categories = QtWidgets.QTreeWidget()
        self.categories.setHeaderLabel('LIBRARIES / CATEGORIES')
        self.categories.setMinimumWidth(170)
        self.splitter.addWidget(self.categories)
        main = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.make_controls(main_layout)
        self.cache = ImageCache(self)
        self.grid = Grid(self.cache)
        self.model = AssetModel(self)
        self.grid.setModel(self.model)
        self.table = DetailsView(self.grid.cards)
        self.table.setModel(self.model)
        self.table.setSelectionModel(self.grid.selectionModel())
        for column, width in enumerate([230, 90, 140, 110, 85, 95, 75, 75, 95, 240]):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.table)
        main_layout.addWidget(self.stack, 1)
        self.splitter.addWidget(main)
        self.collections = CollectionsPanel(self.preferences)
        self.collections.hide()
        self.collections.changed.connect(self.collections_changed)
        self.collections.selected.connect(self.collection_selection)
        self.collections.preview_requested.connect(self.preview)
        self.collections.error.connect(self.show_error)
        self.splitter.addWidget(self.collections)
        self.make_properties()
        self.splitter.setSizes([190, 1000, 0, 300])
        layout.addWidget(self.splitter, 1)
        self.status = QtWidgets.QLabel('Loading libraries…')
        self.status.setObjectName('muted')
        layout.addWidget(self.status)
        self.debounce = QtCore.QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(140)
        self.debounce.timeout.connect(self.filter)
        self.search.textChanged.connect(lambda: self.debounce.start())
        self.tags.textChanged.connect(lambda: self.debounce.start())
        self.invert.toggled.connect(self.filter)
        self.kind.currentIndexChanged.connect(self.filter)
        for widget in (self.length_filter, self.width_filter, self.stars_filter):
            widget.changed.connect(self.filter)
        self.categories.itemSelectionChanged.connect(self.filter)
        self.grid.selectionModel().selectionChanged.connect(self.main_selection)
        self.grid.selectionModel().currentChanged.connect(self.main_selection)
        for widget in (self.grid, self.grid.viewport(), self.table, self.table.viewport(),
                       self.collections.items, self.collections.items.viewport()):
            widget.installEventFilter(self)
        self.grid.doubleClicked.connect(self.preview)
        self.table.doubleClicked.connect(self.preview)
        self.cache.changed.connect(self.update_detail_image)
        self.cache.changed.connect(self.table.viewport().update)
        self.animation = QtCore.QTimer(self)
        self.animation.setInterval(85)
        self.animation.timeout.connect(self.animate)
        self.animation.start()
        self.reload()

    def make_filters(self, layout):
        filters = QtWidgets.QHBoxLayout()
        filters.addWidget(QtWidgets.QLabel('Fulltext'))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Search names, categories and keywords…')
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search, 2)
        self.invert = QtWidgets.QCheckBox('Invert')
        self.invert.setToolTip('Exclude fulltext matches; other filters still apply.')
        filters.addWidget(self.invert)
        filters.addWidget(QtWidgets.QLabel('Tags'))
        self.tags = QtWidgets.QLineEdit()
        self.tags.setClearButtonEnabled(True)
        self.tags.setPlaceholderText('Exact tags, comma-separated (all match)')
        filters.addWidget(self.tags, 1)
        self.tag_picker = QtWidgets.QComboBox()
        self.tag_picker.setMaximumWidth(150)
        self.tag_picker.addItem('Pick tag…')
        self.tag_picker.activated.connect(self.pick_tag)
        filters.addWidget(self.tag_picker)
        self.kind = QtWidgets.QComboBox()
        for text, data in [('All media', ''), ('Footage', 'footage'), ('Stills / HDRI', 'stills')]:
            self.kind.addItem(text, data)
        filters.addWidget(self.kind)
        layout.addLayout(filters)
        numeric = QtWidgets.QHBoxLayout()
        self.length_filter = NumericFilter('Length (seconds)', 1000000, 3)
        self.width_filter = NumericFilter('Width (pixels)', 1000000)
        self.stars_filter = NumericFilter('Stars', 5)
        for widget in (self.length_filter, self.width_filter, self.stars_filter):
            numeric.addWidget(widget)
        numeric.addStretch()
        clear = QtWidgets.QPushButton('Clear filters')
        clear.clicked.connect(self.clear_filters)
        numeric.addWidget(clear)
        layout.addLayout(numeric)

    def make_controls(self, layout):
        row = QtWidgets.QHBoxLayout()
        self.view_mode = QtWidgets.QComboBox()
        self.view_mode.addItems(['Tiles', 'Details', 'List'])
        self.view_mode.currentTextChanged.connect(self.change_view)
        row.addWidget(self.view_mode)
        self.play_button = QtWidgets.QPushButton('Play all')
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self.set_play_all)
        row.addWidget(self.play_button)
        row.addStretch()
        self.collection_toggle = QtWidgets.QPushButton('Collections')
        self.collection_toggle.setCheckable(True)
        self.collection_toggle.toggled.connect(self.toggle_collections)
        row.addWidget(self.collection_toggle)
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('Rate selection'))
        self.star_buttons = []
        for value in range(1, 6):
            button = StarButton(value)
            button.setToolTip('Set %d star%s on selected assets' % (value, '' if value == 1 else 's'))
            button.clicked.connect(lambda checked=False, stars=value: self.rate_selected(stars))
            row.addWidget(button)
            self.star_buttons.append(button)
        self.clear_stars = QtWidgets.QPushButton('Clear')
        self.clear_stars.setToolTip('Set selected assets to zero stars')
        self.clear_stars.clicked.connect(lambda: self.rate_selected(0))
        row.addWidget(self.clear_stars)
        row.addStretch()
        row.addWidget(QtWidgets.QLabel('Card size'))
        self.scale = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scale.setRange(180, 360)
        self.scale.setValue(248)
        self.scale.setFixedWidth(100)
        self.scale.valueChanged.connect(self.resize_cards)
        row.addWidget(self.scale)
        layout.addLayout(row)

    def make_properties(self):
        panel = QtWidgets.QWidget()
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(320)
        details = QtWidgets.QVBoxLayout(panel)
        self.detail_image = QtWidgets.QLabel('Select an asset')
        self.detail_image.setAlignment(QtCore.Qt.AlignCenter)
        self.detail_image.setMinimumHeight(150)
        self.detail_image.setMaximumHeight(190)
        details.addWidget(self.detail_image)
        self.detail_title = QtWidgets.QLabel('Asset details')
        self.detail_title.setWordWrap(True)
        self.detail_title.setMinimumWidth(0)
        self.detail_title.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.detail_title.setObjectName('heading')
        details.addWidget(self.detail_title)
        self.detail_text = QtWidgets.QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        details.addWidget(self.detail_text, 1)
        self.preview_button = QtWidgets.QPushButton('Open preview')
        self.preview_button.clicked.connect(self.preview)
        self.import_button = QtWidgets.QPushButton('Import main into Nuke')
        self.import_button.setObjectName('primary')
        self.import_button.clicked.connect(lambda: self.import_selected(False))
        self.highres_button = QtWidgets.QPushButton('Import highres into Nuke')
        self.highres_button.clicked.connect(lambda: self.import_selected(True))
        for button in (self.preview_button, self.import_button, self.highres_button):
            button.setEnabled(False)
            details.addWidget(button)
        self.splitter.addWidget(panel)

    def pick_tag(self, index):
        if index:
            tags = [t.strip() for t in self.tags.text().split(',') if t.strip()]
            tag = self.tag_picker.itemText(index)
            if tag not in tags:
                self.tags.setText(', '.join(tags + [tag]))
            self.tag_picker.setCurrentIndex(0)

    def clear_filters(self):
        self.search.clear()
        self.tags.clear()
        self.invert.setChecked(False)
        self.kind.setCurrentIndex(0)
        for widget in (self.length_filter, self.width_filter, self.stars_filter):
            widget.operator.setCurrentIndex(0)
            widget.value.setValue(0)
        self.filter()

    def reload(self):
        if hasattr(self, 'loader') and self.loader.isRunning():
            return
        self.refresh.setEnabled(False)
        self.status.setText('Loading libraries…')
        self.loader = Loader(self.settings['libraries'], self)
        self.loader.loaded.connect(self.loaded)
        self.loader.finished.connect(lambda: self.refresh.setEnabled(True))
        self.loader.start()

    def loaded(self, assets, errors):
        self.assets, self.errors = assets, errors
        for asset in assets:
            asset['_rating'] = self.preferences.rating(asset)
        current = self.categories.currentItem()
        selected_scope = current.data(0, QtCore.Qt.UserRole) if current else ('', '')
        self.categories.blockSignals(True)
        self.categories.clear()
        all_item = QtWidgets.QTreeWidgetItem(['All libraries'])
        all_item.setData(0, QtCore.Qt.UserRole, ('', ''))
        self.categories.addTopLevelItem(all_item)
        target = all_item
        for config in self.settings['libraries']:
            root = str(Path(config['root']))
            parent = QtWidgets.QTreeWidgetItem([config['name']])
            parent.setData(0, QtCore.Qt.UserRole, (root, ''))
            parent.setToolTip(0, root)
            self.categories.addTopLevelItem(parent)
            if tuple(selected_scope) == (root, ''):
                target = parent
            nodes = {'': parent}
            for category in sorted({a['category'] for a in assets if a['library_root'] == root}):
                prefix = ''
                for part in category.split('/'):
                    ancestor = prefix
                    prefix = prefix + '/' + part if prefix else part
                    if prefix not in nodes:
                        item = QtWidgets.QTreeWidgetItem([part])
                        item.setData(0, QtCore.Qt.UserRole, (root, prefix))
                        nodes[ancestor].addChild(item)
                        nodes[prefix] = item
                        if tuple(selected_scope) == (root, prefix):
                            target = item
        self.categories.expandAll()
        self.categories.setCurrentItem(target)
        self.categories.blockSignals(False)
        self.tag_picker.clear()
        self.tag_picker.addItem('Pick tag…')
        self.tag_picker.addItems(sorted({t for a in assets for t in a.get('tags', [])}))
        self.collections.lookup = {asset_key(a): a for a in assets}
        self.collections.refresh()
        self.filter()

    def filter(self, *_):
        if self._filtering:
            return
        self._filtering = True
        keys = {asset_key(a) for a in self.main_selected()}
        selected = self.categories.currentItem()
        root, category = selected.data(0, QtCore.Qt.UserRole) if selected else ('', '')
        kind = self.kind.currentData()
        picked = self.preferences.picked_keys()
        result = [a for a in self.assets if (not root or a['library_root'] == root)
                  and (not category or a['category'] == category or a['category'].startswith(category + '/'))
                  and (not kind or (a.get('kind') != 'footage' if kind == 'stills' else a.get('kind') == kind))
                  and asset_key(a) not in picked
                  and accepts(a, self.search.text(), self.invert.isChecked(), self.tags.text().split(','),
                              self.length_filter.rule(), self.width_filter.rule(), self.stars_filter.rule(), a['_rating'])]
        self.grid.cards.hover_row = -1
        self.model.replace(result)
        selection = self.grid.selectionModel()
        for row, asset in enumerate(result):
            if asset_key(asset) in keys:
                selection.select(self.model.index(row, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        self._filtering = False
        self.selection()
        error = '  ·  %d library error(s) — see tooltip' % len(self.errors) if self.errors else ''
        self.status.setText('%s assets  /  %s total  ·  %s picked%s' % (len(result), len(self.assets), len(picked), error))
        self.status.setToolTip('\n'.join(self.errors))

    def main_selected(self):
        rows = sorted({index.row() for index in self.grid.selectionModel().selectedIndexes()})
        return [self.model.assets[row] for row in rows]

    def selected_assets(self):
        return self.collections.selected_assets() if self.selection_source == 'collection' else self.main_selected()

    def selected(self):
        assets = self.selected_assets()
        return assets[0] if assets else None

    def main_selection(self, *_):
        if self._filtering:
            return
        self.selection_source = 'main'
        self.selection()

    def eventFilter(self, watched, event):
        if event.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.MouseButtonPress):
            if watched in (self.grid, self.grid.viewport(), self.table, self.table.viewport()):
                self.selection_source = 'main'
            elif watched in (self.collections.items, self.collections.items.viewport()):
                self.selection_source = 'collection'
            QtCore.QTimer.singleShot(0, self.selection)
        return super().eventFilter(watched, event)

    def collection_selection(self):
        self.selection_source = 'collection' if self.collections.isVisible() else 'main'
        self.selection()

    def selection(self, *_):
        assets = self.selected_assets()
        asset = assets[0] if assets else None
        for button in (self.preview_button, self.import_button, self.highres_button, self.clear_stars, *self.star_buttons):
            button.setEnabled(bool(asset))
        ratings = {a.get('_rating', 0) for a in assets}
        rating = next(iter(ratings)) if len(ratings) == 1 else 0
        for value, button in enumerate(self.star_buttons, 1):
            button.set_filled(value <= rating)
        if not asset:
            self.detail_title.setText('Asset details')
            self.detail_text.clear()
            self.detail_image.clear()
            self.detail_image.setText('Select assets')
            return
        self.preview_button.setEnabled(len(assets) == 1)
        self.highres_button.setEnabled(all(a.get('highres') for a in assets))
        self.detail_title.setText(asset['name'].replace('_', ' ') if len(assets) == 1 else '%d assets selected' % len(assets))
        self.detail_title.setToolTip(asset['name'])
        if len(assets) > 1:
            self.detail_text.setPlainText('Stars: ' + ('Mixed' if len(ratings) > 1 else str(rating)) +
                                         '\n\n' + '\n'.join(a['name'] for a in assets))
        else:
            text = [asset['library'] + '  /  ' + asset['category'],
                    'Type: ' + asset.get('kind', 'still'), 'Color: ' + asset.get('colorspace', 'ACEScg'),
                    'Stars: ' + str(rating), '\nKEYWORDS', ', '.join(asset.get('tags', [])), '\nMEDIA']
            text += ['%s: %s' % (key, value) for key, value in asset.get('metadata', {}).items()]
            for key in ('main', 'proxy', 'thumb', 'filmstrip', 'highres'):
                if asset.get(key):
                    text.extend(['\n' + key.upper(), asset[key]])
            self.detail_text.setPlainText('\n'.join(text))
        self.update_detail_image()

    def update_detail_image(self):
        asset = self.selected()
        if asset:
            pixmap = self.cache.get(asset.get('thumb', ''))
            if not pixmap.isNull():
                self.detail_image.setPixmap(pixmap.scaled(280, 160, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            else:
                self.detail_image.clear()
                self.detail_image.setText('Preview unavailable')

    def rate_selected(self, stars):
        assets = self.selected_assets()
        if not assets:
            return
        try:
            self.preferences.rate(assets, stars)
            for asset in self.assets:
                asset['_rating'] = self.preferences.rating(asset)
            self.filter()
        except Exception as error:
            self.show_error(str(error))

    def change_view(self, mode):
        self.grid.cards.hover_row = -1
        self.stack.setCurrentWidget(self.table if mode == 'Details' else self.grid)
        if mode == 'List':
            self.grid.setViewMode(QtWidgets.QListView.ListMode)
            self.grid.setWrapping(False)
            self.grid.setItemDelegate(QtWidgets.QStyledItemDelegate(self.grid))
        elif mode == 'Tiles':
            self.grid.setViewMode(QtWidgets.QListView.IconMode)
            self.grid.setWrapping(True)
            self.grid.setItemDelegate(self.grid.cards)
        self.grid.setMovement(QtWidgets.QListView.Static)
        self.scale.setEnabled(mode == 'Tiles')
        self.grid.doItemsLayout()

    def resize_cards(self, width):
        self.grid.cards.width = width
        self.grid.doItemsLayout()

    def set_play_all(self, enabled):
        self.grid.cards.play_all = enabled
        self.grid.cards.hover_row = -1
        self.play_button.setText('Stop all' if enabled else 'Play all')
        self.grid.viewport().update()
        self.table.viewport().update()

    def animate(self):
        cards = self.grid.cards
        if self.view_mode.currentText() == 'List' or not self.isVisible():
            return
        if cards.play_all or cards.hover_row >= 0:
            cards.frame = (cards.frame + 1) % 24
            self.stack.currentWidget().viewport().update()

    def toggle_collections(self, enabled):
        self.collections.setVisible(enabled)
        if enabled:
            self.splitter.setSizes([190, 720, 290, 280])
        elif self.selection_source == 'collection':
            self.selection_source = 'main'
            self.selection()

    def collections_changed(self):
        self.filter()

    def preview(self, *_):
        assets = self.selected_assets()
        if len(assets) == 1:
            try:
                dialog = PreviewDialog(assets[0], self)
                dialog.exec_() if hasattr(dialog, 'exec_') else dialog.exec()
            except Exception as error:
                self.show_error(str(error))

    def import_selected(self, highres):
        assets = self.selected_assets()
        if assets:
            try:
                from .nuke_bridge import import_asset
                for asset in assets:
                    import_asset(asset, highres)
            except ImportError:
                QtWidgets.QMessageBox.information(self, 'Nuke', 'Open this browser inside Nuke to import assets.')
            except Exception as error:
                self.show_error(str(error))

    def ingest(self):
        from .ingest_ui import IngestDialog
        dialog = IngestDialog(self.settings, self.assets, self)
        dialog.exec_() if hasattr(dialog, 'exec_') else dialog.exec()

    def configuration(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.settings['_config_path']))

    def show_error(self, message):
        QtWidgets.QMessageBox.warning(self, 'TinyLib', message)

    def closeEvent(self, event):
        if self.loader.isRunning():
            self.status.setText('Library load is still running; close again when it finishes.')
            self.loader.requestInterruption()
            event.ignore()
            return
        self.cache.pool.clear()
        if not self.cache.pool.waitForDone(200):
            self.status.setText('Waiting for preview reads; close again in a moment.')
            event.ignore()
            return
        super().closeEvent(event)
