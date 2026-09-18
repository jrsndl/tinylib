"""Studio browser: library tree, filters, multi-selection and collections."""
from pathlib import Path
import math
import json
from .qt import QtCore, QtGui, QtWidgets, HOSTED_IN_NUKE
from .settings import load_settings
from .preferences import Preferences, asset_key, COLLECTION_MIME
from .filters import accepts
from .collection_ui import CollectionsPanel
from .views import STYLE, Loader, ImageCache, AssetModel, Grid, DetailsView
from .access import AccessControl
from .access_ui import AccessDialog
from .actions import ActionRegistry
from .action_ui import ActionPicker
from .player import Player
from .tile_text import DEFAULT_TEMPLATE, TOKENS, validate_template
from .properties_ui import PropertiesEditor
from .asset_edit import writable_library, save_asset
from .path_format import FORMATS, format_assets


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
    def __init__(self, config_path=None, preferences_path=None, access=None):
        super().__init__()
        self.settings = load_settings(config_path)
        self.access = access or AccessControl(self.settings)
        self.settings['_access'] = self.access
        self.registry = ActionRegistry(self.settings['action_roots'])
        self.player = Player(self.settings, self)
        self.player.error.connect(self.show_error)
        self.player.rating_requested.connect(self.rate_asset)
        self.preferences = Preferences(preferences_path)
        self.assets, self.errors = [], []
        self.selection_source = 'main'
        self._filtering = False
        self.setWindowTitle('TinyLib · Studio library')
        self.resize(1580, 900)
        stylesheet = STYLE if HOSTED_IN_NUKE else (Path(__file__).with_name('standalone.qss').read_text(encoding='utf-8'))
        self.setStyleSheet(stylesheet)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        top = QtWidgets.QHBoxLayout()
        heading = QtWidgets.QLabel('TinyLib  /  Studio library')
        heading.setObjectName('heading')
        top.addWidget(heading)
        top.addStretch()
        self.identity_label = QtWidgets.QLabel(self.access.identity)
        top.addWidget(self.identity_label)
        self.access_button = QtWidgets.QPushButton('Access rights…')
        self.access_button.clicked.connect(self.manage_access)
        self.access_button.setVisible(self.access.is_admin)
        top.addWidget(self.access_button)
        config_button = QtWidgets.QPushButton('Configuration…')
        config_button.clicked.connect(self.configuration)
        config_button.setEnabled(self.access.is_admin)
        top.addWidget(config_button)
        self.refresh = QtWidgets.QPushButton('Refresh')
        self.refresh.clicked.connect(self.reload)
        top.addWidget(self.refresh)
        self.ingest_button = QtWidgets.QPushButton('+ Ingest asset')
        self.ingest_button.setObjectName('primary')
        self.ingest_button.clicked.connect(self.ingest)
        top.addWidget(self.ingest_button)
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
        display = self.preferences.data.get('display', {})
        template = display.get('tile_template', self.settings.get('tile_template', DEFAULT_TEMPLATE))
        try:
            validate_template(template)
        except ValueError:
            template = DEFAULT_TEMPLATE
        self.grid.cards.template = template
        self.grid.cards.info = self.info_toggle.isChecked()
        self.model = AssetModel(self)
        self.model.path_notation = self.preferences.data.get('display', {}).get('path_notation', 'nuke')
        self.grid.setModel(self.model)
        self.table = DetailsView(self.grid.cards)
        self.table.setModel(self.model)
        self.table.setSelectionModel(self.grid.selectionModel())
        for view in (self.grid, self.table):
            view.setAcceptDrops(True)
            view.setDragDropMode(QtWidgets.QAbstractItemView.DragDrop)
        for column, width in enumerate([230, 90, 140, 110, 85, 95, 75, 75, 95, 240]):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.table)
        main_layout.addWidget(self.stack, 1)
        self.splitter.addWidget(main)
        self.collections = CollectionsPanel(self.preferences, self.cache, self.access)
        self.collections.model.path_notation = self.model.path_notation
        self.collections.hide()
        self.collections.changed.connect(self.collections_changed)
        self.collections.selected.connect(self.collection_selection)
        self.collections.preview_requested.connect(self.preview)
        self.collections.error.connect(self.show_error)
        self.collections.actions.requested.connect(lambda identifier: self.run_action(identifier, collection=True))
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
                       self.collections.grid, self.collections.grid.viewport(), self.collections.table, self.collections.table.viewport()):
            widget.installEventFilter(self)
        self.detail_image.installEventFilter(self)
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
        row.addWidget(QtWidgets.QLabel('Paths'))
        self.path_notation = QtWidgets.QComboBox()
        for identifier, label in FORMATS:
            self.path_notation.addItem(label, identifier)
        selected_format = self.preferences.data.get('display', {}).get('path_notation', 'nuke')
        self.path_notation.setCurrentIndex(max(0, self.path_notation.findData(selected_format)))
        self.path_notation.currentIndexChanged.connect(self.change_path_notation)
        self.path_notation.setToolTip('File-sequence notation used for external drags and clipboard copy.')
        row.addWidget(self.path_notation)
        row.addStretch()
        self.collection_toggle = QtWidgets.QPushButton('Collections')
        self.collection_toggle.setCheckable(True)
        self.collection_toggle.toggled.connect(self.toggle_collections)
        row.addWidget(self.collection_toggle)
        self.caption_button = QtWidgets.QPushButton('Tile text…')
        self.caption_button.clicked.connect(self.configure_tile_text)
        row.addWidget(self.caption_button)
        self.info_toggle = QtWidgets.QPushButton('Info')
        self.info_toggle.setCheckable(True)
        self.info_toggle.setChecked(self.preferences.data.get('display', {}).get('tile_info', True))
        self.info_toggle.toggled.connect(self.toggle_tile_info)
        row.addWidget(self.info_toggle)
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
        self.properties = PropertiesEditor()
        self.properties.save_requested.connect(self.save_properties)
        self.properties.hide()
        details.addWidget(self.properties, 1)
        self.preview_button = QtWidgets.QPushButton('Open preview')
        self.preview_button.clicked.connect(self.preview)
        self.preview_button.setEnabled(False)
        details.addWidget(self.preview_button)
        self.action_picker = ActionPicker()
        self.action_picker.requested.connect(self.run_action)
        details.addWidget(self.action_picker)
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
        try:
            self.access.refresh()
            configs = [library for library in self.settings['libraries'] if self.access.can(library['root'], 'view')]
        except Exception as error:
            configs = []
            self.status.setText('Cannot read studio permissions: ' + str(error))
        self.player.stop()
        self.registry.reload()
        self.ingest_button.setEnabled(any(not library.get('read_only') and self.access.can(library['root'], 'ingest') for library in configs))
        self.access_button.setVisible(self.access.is_admin)
        self.loader = Loader(configs, self)
        self.loader.loaded.connect(self.loaded)
        self.loader.finished.connect(lambda: self.refresh.setEnabled(True))
        self.loader.start()

    def loaded(self, assets, errors):
        try:
            self.access.refresh()
            assets = [asset for asset in assets if self.access.can(asset['library_root'], 'view')]
        except Exception as error:
            assets = []
            errors = errors + ['Cannot read studio permissions: ' + str(error)]
        self.assets, self.errors = assets, errors + self.registry.errors
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
            if not self.access.can(config['root'], 'view'):
                continue
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
        main_views = (self.grid, self.grid.viewport(), self.table, self.table.viewport())
        collection_views = (self.collections.grid, self.collections.grid.viewport(), self.collections.table, self.collections.table.viewport())
        if watched is self.detail_image and event.type() == QtCore.QEvent.MouseButtonDblClick:
            self.preview()
            return True
        if watched in main_views and event.type() in (QtCore.QEvent.DragEnter, QtCore.QEvent.DragMove, QtCore.QEvent.Drop):
            try:
                payload = json.loads(bytes(event.mimeData().data(COLLECTION_MIME)))
                if payload['preferences'] != str(self.preferences.path.resolve()) or not isinstance(payload['keys'], list) or not all(isinstance(key, str) for key in payload['keys']):
                    raise ValueError('Invalid collection drag')
                self.preferences.collection(payload['collection'])
                if event.type() == QtCore.QEvent.Drop:
                    self.collections.remove_keys(payload['collection'], payload['keys'])
                    self.selection_source = 'main'
                    self.selection()
                event.setDropAction(QtCore.Qt.CopyAction)
                event.accept()
            except (ValueError, KeyError, TypeError, StopIteration):
                event.ignore()
            except Exception as error:
                event.ignore()
                self.show_error(str(error))
            return True
        if watched in main_views + collection_views and event.type() == QtCore.QEvent.KeyPress and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.selection_source = 'main' if watched in main_views else 'collection'
            if not event.isAutoRepeat():
                self.preview()
            return True
        if watched in main_views + collection_views and event.type() == QtCore.QEvent.KeyPress and event.matches(QtGui.QKeySequence.Copy):
            self.selection_source = 'main' if watched in main_views else 'collection'
            self.copy_selected_paths()
            return True
        rating_keys = {QtCore.Qt.Key_0: 0, QtCore.Qt.Key_1: 1, QtCore.Qt.Key_2: 2,
                       QtCore.Qt.Key_3: 3, QtCore.Qt.Key_4: 4, QtCore.Qt.Key_5: 5}
        if watched in main_views and event.type() == QtCore.QEvent.KeyPress and event.key() in rating_keys:
            self.selection_source = 'main'
            if not event.isAutoRepeat():
                self.rate_selected(rating_keys[event.key()])
            return True
        if event.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.MouseButtonPress):
            if watched in (self.grid, self.grid.viewport(), self.table, self.table.viewport()):
                self.selection_source = 'main'
            elif watched in (self.collections.grid, self.collections.grid.viewport(), self.collections.table, self.collections.table.viewport()):
                self.selection_source = 'collection'
            QtCore.QTimer.singleShot(0, self.selection)
        return super().eventFilter(watched, event)

    def collection_selection(self):
        self.selection_source = 'collection' if self.collections.isVisible() else 'main'
        self.selection()

    def selection(self, *_):
        assets = self.selected_assets()
        asset = assets[0] if assets else None
        for button in (self.preview_button, self.clear_stars, *self.star_buttons):
            button.setEnabled(bool(asset))
        self.update_actions()
        ratings = {a.get('_rating', 0) for a in assets}
        rating = next(iter(ratings)) if len(ratings) == 1 else 0
        self.properties.setVisible(len(assets) == 1)
        self.detail_text.setVisible(len(assets) != 1)
        editable = False
        if len(assets) == 1:
            try:
                writable_library(self.settings, self.access, asset)
                editable = True
            except PermissionError:
                pass
        self.properties.set_asset(asset if len(assets) == 1 else None, editable)
        for value, button in enumerate(self.star_buttons, 1):
            button.set_filled(value <= rating)
        if not asset:
            self.detail_title.setText('Asset details')
            self.detail_text.clear()
            self.detail_image.clear()
            self.detail_image.setText('Select assets')
            return
        self.preview_button.setEnabled(len(assets) == 1)
        self.detail_title.setText(asset['name'].replace('_', ' ') if len(assets) == 1 else '%d assets selected' % len(assets))
        self.detail_title.setToolTip(asset['name'])
        if len(assets) > 1:
            self.detail_text.setPlainText('Stars: ' + ('Mixed' if len(ratings) > 1 else str(rating)) +
                                         '\n\n' + '\n'.join(a['name'] for a in assets))
        self.update_detail_image()

    def save_properties(self, original, changes):
        try:
            updated = save_asset(self.settings, self.access, original, changes)
        except Exception as error:
            self.properties.message.setText(str(error))
            return
        for asset in self.assets:
            if asset_key(asset) == asset_key(original):
                asset.clear()
                asset.update(updated)
        self.properties.cancel_edit()
        self.loaded(self.assets, [])
        self.status.setText('Asset properties saved.')

    def toggle_tile_info(self, enabled):
        self.grid.cards.info = enabled
        self.grid.doItemsLayout()
        self.grid.viewport().update()
        try:
            self.preferences.set_display(tile_info=enabled)
        except Exception as error:
            self.show_error(str(error))

    def configure_tile_text(self):
        text, accepted = QtWidgets.QInputDialog.getMultiLineText(self, 'Tile text',
            'Tokens: ' + ', '.join('{' + token + '}' for token in TOKENS) + '\nUse \\n or a new line. Up to six lines.', self.grid.cards.template)
        if accepted:
            try:
                validate_template(text)
                self.preferences.set_display(tile_template=text)
                self.grid.cards.template = text
                self.grid.doItemsLayout()
                self.grid.viewport().update()
            except Exception as error:
                self.show_error(str(error))

    def change_path_notation(self, *_):
        notation = self.path_notation.currentData()
        if hasattr(self, 'model'):
            self.model.path_notation = notation
        if hasattr(self, 'collections'):
            self.collections.model.path_notation = notation
        try:
            self.preferences.set_display(path_notation=notation)
        except Exception as error:
            self.show_error(str(error))

    def copy_selected_paths(self):
        assets = self.selected_assets()
        if not assets:
            return
        QtWidgets.QApplication.clipboard().setText(format_assets(assets, self.path_notation.currentData()))
        self.status.setText('Copied %d asset path%s.' % (len(assets), '' if len(assets) == 1 else 's'))

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
        if mode != 'Details':
            self.grid.configure_mode(mode)
        self.scale.setEnabled(mode == 'Tiles')
        self.info_toggle.setEnabled(mode == 'Tiles')
        self.caption_button.setEnabled(mode == 'Tiles')
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
                self.access.refresh()
                self.access.require(assets[0]['library_root'], 'view')
                self.player.play(assets[0])
            except Exception as error:
                self.show_error(str(error))

    def update_actions(self):
        assets = self.main_selected()
        self.action_picker.populate([action for action in self.registry.actions.values()
                                     if self.registry.allowed(action, assets, self.access)])
        collection_assets = self.collections.action_assets()
        self.collections.actions.populate([action for action in self.registry.actions.values()
                                          if self.registry.allowed(action, collection_assets, self.access)])

    def run_action(self, identifier, collection=False):
        assets = self.collections.action_assets() if collection else self.main_selected()
        try:
            result = self.registry.run(identifier, assets, self.access,
                                       {'parent': self, 'settings': self.settings,
                                        'collection': self.collections.combo.currentText() if collection else None})
            self.status.setText(str(result) if isinstance(result, str) else 'Action completed.')
        except Exception as error:
            self.show_error(str(error))

    def rate_asset(self, asset, stars):
        try:
            self.preferences.rate([asset], stars)
            key = asset_key(asset)
            for current in self.assets:
                if asset_key(current) == key:
                    current['_rating'] = stars
            self.collections.populate()
            self.filter()
            self.status.setText('Set %s to %d star%s.' % (asset.get('name', 'asset'), stars, '' if stars == 1 else 's'))
        except Exception as error:
            self.show_error(str(error))

    def manage_access(self):
        try:
            dialog = AccessDialog(self.access, self.registry, self)
            accepted = dialog.exec_() if hasattr(dialog, 'exec_') else dialog.exec()
            if accepted:
                self.player.stop()
                self.reload()
        except Exception as error:
            self.show_error(str(error))

    def ingest(self):
        try:
            self.access.refresh()
            from .ingest_ui import IngestDialog
            dialog = IngestDialog(self.settings, self.assets, self)
            dialog.exec_() if hasattr(dialog, 'exec_') else dialog.exec()
        except Exception as error:
            self.show_error(str(error))

    def configuration(self):
        self.access.refresh()
        if self.access.is_admin:
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
        self.player.stop()
        super().closeEvent(event)
