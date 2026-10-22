"""Studio browser: library tree, filters, multi-selection and collections."""
from pathlib import Path
import math
import re
import json
import threading
import time
from .qt import QtCore, QtGui, QtWidgets, HOSTED_IN_NUKE, exec_dialog
from .settings import load_settings
from .preferences import Preferences, adjacent_asset, asset_key, COLLECTION_MIME
from .filters import accepts
from .collection_ui import CollectionsPanel
from .views import (stylesheet, Loader, ImageCache, AssetModel, Grid, DetailsView,
                    heading_font, interface_font)
from .access import AccessControl, root_key
from .access_ui import AccessDialog
from .actions import ActionRegistry
from .action_ui import ActionPicker
from .player import Player
from .tile_text import DEFAULT_TEMPLATE, TOKENS, validate_template
from .properties_ui import PropertiesEditor
from .asset_edit import DeleteStopped, writable_library, save_asset, save_assets, delete_assets
from .delete_ui import DeleteDialog
from .edit_lock import (LibraryEditLocked, acquire_edit_lock, locked_message,
                        release_edit_lock)
from .path_format import FORMATS, format_assets
from .asset_types import ASSET_TYPES
from .asset_fields import display_name
from .library import Library
from .thumbnail_cache import prune_cache
from .log import configure as configure_logging, log_path
from .library_tools import require_admin
from .library_tools_ui import CrosscheckDialog, MetadataRescanDialog, LibrarySortDialog, CreateLibraryDialog, LabelsDialog, KeywordsDialog


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


class NumberEdit(QtWidgets.QLineEdit):
    """A plain text field for a non-negative number, with the ``value``/``setValue``/
    ``valueChanged`` interface of a spin box.

    A spin box rejected some keystrokes (a digit beyond its decimals, typing over ``0.000``)
    and, with the stylesheets' padding, could squeeze its inner text field until it took no
    input at all. Empty means 0. A comma is read as a decimal point.
    """
    valueChanged = QtCore.Signal(float)

    def __init__(self, maximum, decimals=0, parent=None):
        super().__init__(parent)
        self.maximum, self.decimals = maximum, decimals
        pattern = r'^\d*$' if not decimals else r'^\d*([.,]\d{0,%d})?$' % decimals
        expression = getattr(QtCore, 'QRegularExpression', None)
        if expression is not None and hasattr(QtGui, 'QRegularExpressionValidator'):
            self.setValidator(QtGui.QRegularExpressionValidator(expression(pattern), self))
        else:  # Qt 5 without QRegularExpressionValidator
            self.setValidator(QtGui.QRegExpValidator(QtCore.QRegExp(pattern), self))
        self.setPlaceholderText('0')
        self._last = (False, 0.0)  # (has a number, value): typing "0" counts, e.g. Stars = 0
        self.textChanged.connect(self._text_changed)

    def value(self):
        text = self.text().strip().replace(',', '.')
        try:
            number = float(text) if text not in ('', '.') else 0.0
        except ValueError:
            number = 0.0
        return min(float(self.maximum), max(0.0, number))

    def setValue(self, value):
        value = min(float(self.maximum), max(0.0, float(value)))
        text = ('%d' % round(value)) if not self.decimals else ('%.*f' % (self.decimals, value)).rstrip('0').rstrip('.')
        self.setText('' if value == 0 else text)
        self._text_changed()

    def _text_changed(self, *_):
        state = (bool(self.text().strip()), self.value())
        if state != self._last:
            self._last = state
            self.valueChanged.emit(state[1])


class NumericFilter(QtWidgets.QWidget):
    """Label, operator (Any, >, <, =) and a number. The number is always editable:
    typing one while the operator is Any switches to ``typed_operator``."""
    changed = QtCore.Signal()

    def __init__(self, label, maximum, decimals=0, parent=None, tooltip='', width=80, typed_operator='>'):
        super().__init__(parent)
        self.setToolTip(tooltip)  # Carries the unit; the label stays short.
        self.typed_operator = typed_operator
        self._resetting = False
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel(label))
        self.operator = QtWidgets.QComboBox()
        for text, value in [('Any', ''), ('>', '>'), ('<', '<'), ('=', '=')]:
            self.operator.addItem(text, value)
        self.value = NumberEdit(maximum, decimals)
        self.value.setFixedWidth(width)
        self.value.setObjectName('filterValue')
        self.value.setToolTip(tooltip + ('. ' if tooltip else '') +
                              'Type a number; with Any, the operator becomes %s.' % typed_operator)
        layout.addWidget(self.operator)
        layout.addWidget(self.value)
        self.operator.currentIndexChanged.connect(self.update_operator)
        self.value.valueChanged.connect(self.value_changed)
        self.update_operator()

    def update_operator(self, *_):
        # The number stays editable; with Any it is only dimmed, as it does not filter.
        self.value.setStyleSheet('' if self.operator.currentData() else 'color: #8a8a8a;')
        if not self._resetting:
            self.changed.emit()

    def value_changed(self, *_):
        if self._resetting:
            return
        empty = not self.value.text().strip()
        if not self.operator.currentData():
            if not empty:
                # Typing a number means filtering by it; this also emits changed.
                self.operator.setCurrentIndex(self.operator.findData(self.typed_operator))
        elif empty:
            self.operator.setCurrentIndex(0)  # clearing the number turns the rule off again
        else:
            self.changed.emit()

    def reset(self):
        self._resetting = True
        try:
            self.operator.setCurrentIndex(0)
            self.value.setValue(0)
        finally:
            self._resetting = False
        self.update_operator()

    def rule(self):
        return self.operator.currentData(), self.value.value()


class StarFilter(QtWidgets.QWidget):
    """The rating filter: the same five stars as **Rate selection**, choosing a minimum.

    Click a star to show assets with at least that many stars; click the highest lit star
    again, or **Any**, to stop filtering by rating. ``rule()`` is ``('>=', n)`` or ``('', 0)``.
    """
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.minimum = 0
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        label = QtWidgets.QLabel('Stars')
        label.setToolTip('Show assets with at least this many stars')
        layout.addWidget(label)
        layout.addSpacing(4)
        self.buttons = []
        for value in range(1, 6):
            button = StarButton(value)
            button.setAccessibleName('At least %d stars' % value)
            button.setToolTip('Show assets with at least %d star%s; click again for any rating'
                              % (value, '' if value == 1 else 's'))
            button.clicked.connect(lambda checked=False, stars=value: self.toggle(stars))
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addSpacing(4)
        self.any_button = QtWidgets.QPushButton('Any')
        self.any_button.setToolTip('Any rating, rated or not')
        self.any_button.clicked.connect(lambda: self.set_minimum(0))
        layout.addWidget(self.any_button)
        self.set_minimum(0, notify=False)

    def toggle(self, stars):
        self.set_minimum(0 if stars == self.minimum else stars)

    def set_minimum(self, stars, notify=True):
        self.minimum = max(0, min(5, int(stars)))
        for value, button in enumerate(self.buttons, 1):
            button.set_filled(value <= self.minimum)
        self.any_button.setEnabled(bool(self.minimum))
        if notify:
            self.changed.emit()

    def reset(self):
        self.set_minimum(0, notify=False)

    def rule(self):
        return ('>=', self.minimum) if self.minimum else ('', 0)


PANEL_GAP = 6


def panel_widget(widget=None):
    """A framed panel (styled by ``#panel`` in the stylesheets) on the darker window."""
    widget = widget or QtWidgets.QWidget()
    widget.setObjectName('panel')
    widget.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    return widget


class Browser(QtWidgets.QWidget):
    def __init__(self, config_path=None, preferences_path=None, access=None):
        super().__init__()
        configure_logging()
        self.settings = load_settings(config_path)
        self.access = access or AccessControl(self.settings)
        self.settings['_access'] = self.access
        self.registry = ActionRegistry(self.settings['action_roots'])
        self.player = Player(self.settings, self)
        self.player.error.connect(self.show_error)
        self.player.rating_requested.connect(self.rate_asset)
        self.player.trim_requested.connect(self.trim_asset)
        self.player.navigate_requested.connect(self.play_adjacent)
        self.preferences = Preferences(preferences_path)
        self.assets, self.errors = [], []
        self.edit_mode = False
        self.edit_session = None
        self.marked_for_delete = {}  # asset_key -> asset, deleted on Write after confirmation
        self._restoring_edit_selection = False
        self.selection_source = 'main'
        self._filtering = False
        self.resize(1580, 900)
        self.setFont(interface_font())
        self.setObjectName('browser')
        self.setStyleSheet(stylesheet(HOSTED_IN_NUKE))
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 6)
        layout.setSpacing(PANEL_GAP)
        # Two panels side by side: the filters (search on row 1; numeric filters, tag
        # picker, asset type and Clear filters on row 2) and the main buttons.
        # The user and groups are in the window title.
        top = QtWidgets.QHBoxLayout()
        second_row = self.make_filters(top)
        # Buttons panel, right-aligned: admin and Refresh on the top row, Edit and Ingest below.
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch()
        actions_bottom = QtWidgets.QHBoxLayout()
        actions_bottom.addStretch()
        self.update_window_title()
        self.access_button = QtWidgets.QPushButton('Access rights…')
        self.access_button.clicked.connect(self.manage_access)
        self.access_button.setVisible(self.access.is_admin)
        actions.addWidget(self.access_button)
        self.config_button = QtWidgets.QPushButton('Configuration…')
        self.config_button.clicked.connect(self.configuration)
        self.config_button.setVisible(self.access.is_admin)
        actions.addWidget(self.config_button)
        self.refresh = QtWidgets.QPushButton('Refresh')
        self.refresh.clicked.connect(lambda: self.reload(False))
        actions.addWidget(self.refresh)
        self.edit_button = QtWidgets.QPushButton('Edit')
        self.edit_button.setCheckable(True)
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self.toggle_edit)
        # Shown only while editing with a selection: marks the selected assets; Write deletes them.
        self.delete_button = QtWidgets.QPushButton('Delete')
        self.delete_button.setToolTip('Mark the selected assets for deletion. Write asks you to confirm.')
        self.delete_button.clicked.connect(self.toggle_delete_marks)
        self.delete_button.hide()
        actions_bottom.addWidget(self.delete_button)
        # Shown only while editing, directly beside the red Write button it pairs with.
        self.cancel_edit_button = QtWidgets.QPushButton('Cancel edit')
        self.cancel_edit_button.setToolTip('Discard your changes and leave edit mode.')
        self.cancel_edit_button.clicked.connect(self.cancel_edit)
        self.cancel_edit_button.hide()
        actions_bottom.addWidget(self.cancel_edit_button)
        actions_bottom.addWidget(self.edit_button)
        self.ingest_button = QtWidgets.QPushButton('+ Ingest asset')
        self.ingest_button.setObjectName('primary')
        self.ingest_button.clicked.connect(self.ingest)
        actions_bottom.addWidget(self.ingest_button)
        filters_panel = panel_widget()
        filters_layout = QtWidgets.QVBoxLayout(filters_panel)
        filters_layout.setContentsMargins(8, 6, 8, 6)
        filters_layout.addLayout(top)
        filters_layout.addLayout(second_row)
        actions_panel = panel_widget()
        actions_layout = QtWidgets.QVBoxLayout(actions_panel)
        actions_layout.setContentsMargins(8, 6, 8, 6)
        actions_layout.addLayout(actions)
        actions_layout.addLayout(actions_bottom)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(PANEL_GAP)
        header.addWidget(filters_panel, 1)
        header.addWidget(actions_panel)
        layout.addLayout(header)
        self.splitter = QtWidgets.QSplitter()
        self.splitter.setObjectName('panels')
        self.splitter.setHandleWidth(PANEL_GAP)
        library_panel = panel_widget()
        library_layout = QtWidgets.QVBoxLayout(library_panel)
        library_layout.setContentsMargins(6, 6, 6, 6)
        self.categories = QtWidgets.QTreeWidget()
        self.categories.setObjectName('categories')
        self.categories.setFrameShape(QtWidgets.QFrame.NoFrame)  # The panel already frames it.
        self.categories.setHeaderLabel('LIBRARIES / CATEGORIES')
        self.categories.setMinimumWidth(170)
        library_layout.addWidget(self.categories, 1)
        self.library_tools_button = QtWidgets.QToolButton()
        self.library_tools_button.setText('Library tools')
        self.library_tools_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        tools_menu = QtWidgets.QMenu(self.library_tools_button)
        crosscheck_action = tools_menu.addAction('Library crosscheck…')
        crosscheck_action.triggered.connect(self.library_crosscheck)
        rescan_action = tools_menu.addAction('Rescan metadata…')
        rescan_action.triggered.connect(self.rescan_metadata)
        sort_action = tools_menu.addAction('Library sort…')
        sort_action.triggered.connect(self.library_sort)
        labels_action = tools_menu.addAction('Labels from names…')
        labels_action.triggered.connect(self.labels_from_names)
        keywords_action = tools_menu.addAction('Keyword cleaner…')
        keywords_action.triggered.connect(self.keyword_cleaner)
        create_action = tools_menu.addAction('Create new library…')
        create_action.triggered.connect(self.create_new_library)
        self.library_tools_button.setMenu(tools_menu)
        self.library_tools_button.setVisible(self.access.is_admin)
        library_layout.addWidget(self.library_tools_button)
        self.splitter.addWidget(library_panel)
        main = panel_widget()
        main_layout = QtWidgets.QVBoxLayout(main)
        main_layout.setContentsMargins(6, 6, 6, 6)
        self.make_controls(main_layout)
        self.cache = ImageCache(self)
        self.cache.activity.connect(self.preview_progress)
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
        self.model.trim_for = self.preferences.trim  # The user's own in/out points.
        self.grid.setModel(self.model)
        self.table = DetailsView(self.grid.cards)
        self.table.setFrameShape(QtWidgets.QFrame.NoFrame)
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
        panel_widget(self.collections)
        self.collections.model.path_notation = self.model.path_notation
        self.collections.model.trim_for = self.collections.trim_for
        self.collections.hide()
        self.collections.changed.connect(self.collections_changed)
        self.collections.selected.connect(self.collection_selection)
        self.collections.preview_requested.connect(self.preview)
        self.collections.error.connect(self.show_error)
        self.collections.actions.requested.connect(lambda identifier: self.run_action(identifier, collection=True))
        self.splitter.addWidget(self.collections)
        self.make_properties()
        self.splitter.setSizes([190, 1000, 0, 300])
        self.details_panel.setVisible(self.details_toggle.isChecked())
        self.details_toggle.toggled.connect(self.toggle_details)
        layout.addWidget(self.splitter, 1)
        self.status = QtWidgets.QLabel('Loading libraries…')
        self.status.setObjectName('muted')
        self._asset_status = ''
        self.update_status_tooltip()
        layout.addWidget(self.status)
        self.loading_timer = QtCore.QTimer(self)
        self.loading_timer.setInterval(250)
        self.loading_timer.timeout.connect(self.render_library_progress)
        self._library_progress = None
        self._library_load_started = 0.0
        self.debounce = QtCore.QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(140)
        self.debounce.timeout.connect(self.filter)
        self.search.textChanged.connect(lambda: self.debounce.start())
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
        app = QtWidgets.QApplication.instance()
        if app is not None:
            # closeEvent is not guaranteed when the host application quits.
            app.aboutToQuit.connect(self.shutdown)
        if self.preferences.warning:
            QtCore.QTimer.singleShot(0, lambda: self.show_error(self.preferences.warning))
        threading.Thread(target=prune_cache, args=(self.cache.disk_root,), daemon=True).start()
        self.reload()

    def make_filters(self, top):
        """Add the fulltext search to ``top``; return the second row with the other filters."""
        top.addWidget(QtWidgets.QLabel('Search'))
        self.search = QtWidgets.QLineEdit()
        self.search.setObjectName('search')  # Styled as tall as the buttons beside it.
        self.search.setPlaceholderText('Search names, categories, keywords; {field}:text')
        self.search.setToolTip('Combine text and JSON field filters, e.g. fire {acquire price}:usd. '
                               'Use quotes for phrases: {ingest note}:"slow motion". '
                               'Exclude with !burst, !"big burst", or {ingest note}!burst. '
                               'Nested keys: {metadata.width}:1920. All terms must match.')
        self.search.setClearButtonEnabled(True)
        top.addWidget(self.search, 2)
        row = QtWidgets.QHBoxLayout()
        self.length_filter = NumericFilter('Length', 1000000, 3, tooltip='Length in seconds (frames ÷ frame rate)', width=80)
        self.width_filter = NumericFilter('Width', 1000000, tooltip='Width in pixels', width=64)
        self.stars_filter = StarFilter()
        for widget in (self.length_filter, self.width_filter, self.stars_filter):
            row.addWidget(widget)
        row.addSpacing(16)
        self.tag_picker = QtWidgets.QComboBox()
        self.tag_picker.setToolTip('Adds {tags}:<tag> to the search.')
        self.tag_picker.setMaximumWidth(150)
        self.tag_picker.addItem('Pick tag…')
        self.tag_picker.activated.connect(self.pick_tag)
        row.addWidget(self.tag_picker)
        self.kind = QtWidgets.QComboBox()
        self.kind.addItem('All asset types', '')
        for identifier, definition in ASSET_TYPES.items():
            self.kind.addItem(definition['label'], identifier)
        row.addWidget(self.kind)
        clear = QtWidgets.QPushButton('Clear filters')
        clear.clicked.connect(self.clear_filters)
        row.addWidget(clear)
        row.addStretch()
        return row

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
        self.details_toggle = QtWidgets.QPushButton('Properties')
        self.details_toggle.setCheckable(True)
        self.details_toggle.setChecked(self.preferences.data.get('display', {}).get('details_panel', True))
        self.details_toggle.setToolTip('Show or hide the asset properties panel.')
        row.addWidget(self.details_toggle)
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
        panel = self.details_panel = panel_widget()
        panel.setMinimumWidth(240)
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
        self.detail_title.setFont(heading_font())
        details.addWidget(self.detail_title)
        self.detail_text = QtWidgets.QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        details.addWidget(self.detail_text, 1)
        self.properties = PropertiesEditor()
        self.properties.access = self.access
        self.properties.cancel_requested.connect(self.cancel_edit)
        self.properties.hide()
        # Editing one asset, its label is typed where the heading is.
        details.insertWidget(details.indexOf(self.detail_title) + 1, self.properties.label_field)
        self.properties.label_editing.connect(lambda editing: self.detail_title.setVisible(not editing))
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
        """Add the picked tag to the search as a {tags}: filter."""
        if index:
            tag = self.tag_picker.itemText(index)
            term = '{tags}:' + ('"%s"' % tag.replace('"', '\\"') if re.search(r'[\s{}"\']', tag) else tag)
            text = self.search.text().strip()
            if term not in text.split():
                self.search.setText((text + ' ' + term).strip())
            self.tag_picker.setCurrentIndex(0)

    def clear_filters(self):
        self.search.clear()
        self.kind.setCurrentIndex(0)
        for widget in (self.length_filter, self.width_filter, self.stars_filter):
            widget.reset()
        self.filter()

    def reload(self, use_index_cache=True):
        if hasattr(self, 'loader') and self.loader.isRunning():
            return
        self.refresh.setEnabled(False)
        self.status.setText('Preparing library load…')
        self._library_progress = None
        self._library_load_started = time.perf_counter()
        self.loading_timer.start()
        try:
            self.access.refresh()
            configs = [library for library in self.settings['libraries'] if self.access.can(library['root'], 'view')]
        except Exception as error:
            configs = []
            self.status.setText('Cannot read studio permissions: ' + str(error))
        if self.access.error:
            self.status.setText(self.access.error)
        # Open review players stay open; they hold their own copy of the asset.
        self.registry.reload()
        self.ingest_button.setEnabled(any(not (library.get('read_only') or self.access.read_only(library['root'])) and self.access.can(library['root'], 'ingest') for library in configs))
        self.update_window_title()
        self.update_admin_controls()
        if getattr(self, 'loader', None) is not None:
            self.loader.deleteLater()  # Finished (checked above); don't keep one per Refresh.
        self.loader = Loader(configs, self, use_index_cache=use_index_cache)
        self.loader.progress.connect(self.library_progress)
        self.loader.loaded.connect(self.loaded)
        self.loader.finished.connect(self.loading_timer.stop)
        self.loader.finished.connect(lambda: self.refresh.setEnabled(True))
        self.loader.start()

    def library_progress(self, progress):
        self._library_progress = progress
        self.render_library_progress()

    def render_library_progress(self):
        progress = self._library_progress
        if not progress:
            elapsed = time.perf_counter() - self._library_load_started
            self.status.setText('Preparing library load · %.1f s' % elapsed)
            return
        elapsed = time.perf_counter() - self._library_load_started
        if progress['phase'] == 'reading':
            self.status.setText('Libraries %d/%d · reading %s · %d assets found · %.1f s' %
                                (progress['index'], progress['total'], progress['name'],
                                 progress['assets'], elapsed))
        elif progress['phase'] == 'normalizing':
            self.status.setText('Libraries %d/%d · %s · resolving media paths %d/%d · %.1f s' %
                                (progress['index'], progress['total'], progress['name'],
                                 progress['current'], progress['record_total'], elapsed))
        else:
            source = 'local index' if progress.get('index_cache_hit') else 'network scan'
            self.status.setText('Libraries %d/%d · %s: %d assets · %d total · %s · %.1f s' %
                                (progress['index'], progress['total'], progress['name'],
                                 progress['library_assets'], progress['assets'], source, elapsed))

    def preview_progress(self, progress):
        if not self._asset_status:
            return
        unavailable = ' · %d unavailable' % progress['failures'] if progress['failures'] else ''
        if progress['pending']:
            self.status.setText('%s · previews %d/%d · %d local · %d source · %d pending · %.1f s%s' %
                                (self._asset_status, progress['completed'], progress['requested'],
                                 progress['disk_hits'], progress['source_loads'],
                                 progress['pending'], progress['seconds'], unavailable))
        elif progress['requested']:
            self.status.setText('%s · previews ready: %d local, %d source · %.1f s%s' %
                                (self._asset_status, progress['disk_hits'],
                                 progress['source_loads'], progress['seconds'], unavailable))
        else:
            self.status.setText(self._asset_status + ' · waiting for visible previews…')
        self.update_status_tooltip()

    def update_status_tooltip(self):
        lines = list(getattr(self, 'errors', []))
        lines.append('Local thumbnail cache: ' + str(self.cache.disk_root))
        if log_path():
            lines.append('Log file: ' + str(log_path()))
        self.status.setToolTip('\n'.join(lines))

    def loaded(self, assets, errors=None):
        """Show loaded assets. errors=None keeps the last library load errors (after a save)."""
        if errors is None:
            errors = getattr(self, '_load_errors', [])
        self._load_errors = list(errors)
        self.loading_timer.stop()
        self.status.setText('Building browser index for %d assets…' % len(assets))
        try:
            self.access.refresh()
            assets = [asset for asset in assets if self.access.can(asset['library_root'], 'view')]
        except Exception as error:
            assets = []
            errors = errors + ['Cannot read studio permissions: ' + str(error)]
        if self.access.error:
            errors = [self.access.error] + errors
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
        try:
            keys = {asset_key(a) for a in self.main_selected()}
            selected = self.categories.currentItem()
            root, category = selected.data(0, QtCore.Qt.UserRole) if selected else ('', '')
            kind = self.kind.currentData()
            picked = self.preferences.picked_keys()
            result = [a for a in self.assets if (not root or a['library_root'] == root)
                      and (not category or a['category'] == category or a['category'].startswith(category + '/'))
                      and (not kind or a.get('kind') == kind)
                      and asset_key(a) not in picked
                      and accepts(a, self.search.text(), False, (),
                                  self.length_filter.rule(), self.width_filter.rule(), self.stars_filter.rule(), a['_rating'])]
            self.grid.cards.hover_row = -1
            self.model.replace(result)
            selection = self.grid.selectionModel()
            for row, asset in enumerate(result):
                if asset_key(asset) in keys:
                    selection.select(self.model.index(row, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        except Exception as error:  # Never leave filtering disabled for the rest of the session.
            self.status.setText('Filter failed: %s' % error)
            return
        finally:
            self._filtering = False
        self.selection()
        error = '  ·  %d library error(s) — see tooltip' % len(self.errors) if self.errors else ''
        if self.access.error:
            error = '  ·  ' + self.access.error
        self._asset_status = '%s assets / %s total · %s picked%s' % (len(result), len(self.assets), len(picked), error)
        self.preview_progress(self.cache.snapshot())

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
        if self._restoring_edit_selection:
            return
        assets = self.selected_assets()
        if self.edit_mode and self.edit_session:
            previous = self.edit_session['originals']
            if {asset_key(a) for a in assets} != {asset_key(a) for a in previous}:
                if not self.write_edit(selection_change=True, next_assets=assets,
                                       next_source=self.selection_source):
                    self._restore_edit_selection()
                    return
                assets = self.selected_assets()
        same_type = bool(assets) and len({a.get('kind') for a in assets}) == 1
        if self.edit_mode and not self.edit_session and same_type:
            if not self._activate_edit_assets(assets, self.selection_source):
                # Leave write mode, or every later selection signal would retry and show the error again.
                self.edit_mode = False
                self._set_edit_button(False)
                self._freeze_for_edit(False)
                if self.marked_for_delete:
                    self._clear_delete_marks()
                    self.status.setText('Edit mode ended; the assets marked for deletion were unmarked.')
        asset = assets[0] if assets else None
        for button in (self.preview_button, self.clear_stars, *self.star_buttons):
            button.setEnabled(bool(asset))
        self.update_actions()
        ratings = {a.get('_rating', 0) for a in assets}
        rating = next(iter(ratings)) if len(ratings) == 1 else 0
        self.properties.setVisible(same_type)
        self.detail_text.setVisible(not same_type)
        editable = same_type
        if editable:
            try:
                for selected in assets:
                    writable_library(self.settings, self.access, selected)
            except PermissionError:
                editable = False
        editing = bool(self.edit_session and assets and
                       {asset_key(a) for a in assets} ==
                       {asset_key(a) for a in self.edit_session['originals']})
        self.edit_button.setEnabled(self.edit_mode or editing or editable)
        self.update_delete_button(assets)
        self.properties.set_assets(assets if same_type else [], editing, editable)
        for value, button in enumerate(self.star_buttons, 1):
            button.set_filled(value <= rating)
        if not asset:
            self.detail_title.setText('Asset details')
            self.detail_text.clear()
            self.detail_image.clear()
            self.detail_image.setText('Select assets')
            return
        self.preview_button.setEnabled(len(assets) == 1)
        if len(assets) == 1:
            label = display_name(asset)
            # Without a label, the name reads better with spaces; a label is shown as typed.
            self.detail_title.setText(asset['name'].replace('_', ' ') if label == asset['name'] else label)
            self.detail_title.setToolTip(asset['name'] if label == asset['name'] else
                                         '%s\nIngest name: %s' % (label, asset['name']))
        else:
            self.detail_title.setText('%d assets selected' % len(assets))
            self.detail_title.setToolTip('')
        if len(assets) > 1:
            self.detail_text.setPlainText('Stars: ' + ('Mixed' if len(ratings) > 1 else str(rating)) +
                                         '\n\n' + '\n'.join(display_name(a) for a in assets))
        self.update_detail_image()

    def _set_edit_button(self, editing):
        self.edit_button.blockSignals(True)
        self.edit_button.setChecked(editing)
        self.edit_button.blockSignals(False)
        self.edit_button.setText('Write' if editing else 'Edit')
        self.cancel_edit_button.setVisible(editing)
        if not editing:
            self.delete_button.hide()
        self.edit_button.setStyleSheet(
            'QPushButton { background: #a62828; border-color: #ef5b5b; color: white; font-weight: bold; }'
            if editing else '')

    def _freeze_for_edit(self, editing):
        # Keep both asset views active: choosing another item commits the draft.
        for widget in (self.refresh, self.ingest_button, self.categories, self.search,
                       self.tag_picker, self.kind,
                       self.length_filter, self.width_filter, self.stars_filter,
                       self.library_tools_button):
            widget.setEnabled(not editing)
        if not editing:
            self.ingest_button.setEnabled(any(
                not (library.get('read_only') or self.access.read_only(library['root'])) and self.access.can(library['root'], 'ingest')
                for library in self.settings['libraries']))

    def confirm_edit_override(self, locks):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle('TinyLib · Library locked')
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText(locked_message(locks))
        box.setInformativeText('As an administrator, you can delete the existing lock and take over editing.')
        override = box.addButton('Override lock', QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        exec_dialog(box)
        return box.clickedButton() is override

    def toggle_edit(self, checked):
        if checked:
            if not self.details_toggle.isChecked():
                self.details_toggle.setChecked(True)  # Editing happens in the details panel.
            self.begin_edit()
        else:
            self.write_pressed()

    def begin_edit(self):
        assets = self.selected_assets()
        if not assets or len({a.get('kind') for a in assets}) != 1:
            self._set_edit_button(False)
            return
        if not self._activate_edit_assets(assets, self.selection_source):
            self.edit_mode = False
            self._set_edit_button(False)
            return
        self.edit_mode = True
        self._set_edit_button(True)
        self._freeze_for_edit(True)
        self.update_delete_button()

    def _acquire_edit_lock(self, library):
        """The library's edit lock; an administrator may take over someone else's."""
        try:
            return acquire_edit_lock(library['root'], self.access.identity)
        except LibraryEditLocked as error:
            if not self.access.is_admin or not self.confirm_edit_override(error.locks):
                raise
            return acquire_edit_lock(library['root'], self.access.identity, override=True)

    def _activate_edit_assets(self, originals, selection_source):
        handles = {}
        try:
            self.access.refresh()
            libraries = {}
            for original in originals:
                library = writable_library(self.settings, self.access, original)
                libraries[root_key(library['root'])] = library
            for key, library in sorted(libraries.items()):
                handles[key] = self._acquire_edit_lock(library)
        except Exception as error:
            for handle in handles.values():
                release_edit_lock(handle)
            self.show_error(str(error))
            return False
        library = next(iter(libraries.values()))
        self.edit_session = {'original': originals[0], 'originals': list(originals),
                             'library': library, 'lock': next(iter(handles.values())),
                             'locks': handles, 'selection_source': selection_source}
        self.properties.set_assets(originals, True, True)
        self.properties.message.setText('Changing selection saves modified fields; Write ends editing.')
        self.status.setText('Write mode - %d asset(s), %d library lock(s).' % (len(originals), len(handles)))
        return True

    def _release_edit_session(self, update_selection=True, keep_mode=False):
        if not self.edit_session:
            return True
        try:
            for handle in self.edit_session['locks'].values():
                release_edit_lock(handle)
        except Exception as error:
            self.properties.message.setText('Could not remove the library lock: ' + str(error))
            self._set_edit_button(True)
            return False
        self.edit_session = None
        if not keep_mode:
            self._clear_delete_marks()
            self.edit_mode = False
            self._set_edit_button(False)
            self._freeze_for_edit(False)
        if update_selection:
            self.selection()
        return True

    def _restore_edit_selection(self):
        """Keep an invalid/failed draft attached to its original selected asset."""
        if not self.edit_session:
            return
        self._restoring_edit_selection = True
        try:
            source = self.edit_session.get('selection_source', 'main')
            original_keys = {asset_key(a) for a in self.edit_session['originals']}
            if source == 'collection':
                model = self.collections.model
                selection = self.collections.grid.selectionModel()
            else:
                model = self.model
                selection = self.grid.selectionModel()
            selection.clearSelection()
            for row, asset in enumerate(model.assets):
                if asset_key(asset) in original_keys:
                    selection.select(model.index(row, 0),
                                     QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
            self.selection_source = source
        finally:
            self._restoring_edit_selection = False
        self.selection()

    def cancel_edit(self):
        if self._release_edit_session():
            self.status.setText('Edit cancelled; library lock released.')

    # --- Deleting assets: marked in edit mode, confirmed and deleted on Write ---

    def update_delete_button(self, assets=None):
        assets = self.selected_assets() if assets is None else assets
        self.delete_button.setVisible(self.edit_mode and bool(assets) and self.access.is_admin)
        marked = bool(assets) and all(asset_key(asset) in self.marked_for_delete for asset in assets)
        self.delete_button.setText('Undo delete' if marked else 'Delete')
        self.delete_button.setToolTip('Keep the selected assets.' if marked else
                                      'Mark the selected assets for deletion. Write asks you to confirm.')

    def toggle_delete_marks(self):
        assets = self.selected_assets()
        if not assets:
            return
        if not self.access.is_admin:
            self.delete_button.hide()
            return self.show_error('Only administrators can delete assets.')
        try:
            for asset in assets:
                writable_library(self.settings, self.access, asset)
        except PermissionError as error:
            return self.show_error('Cannot delete from this library: ' + str(error))
        keys = [asset_key(asset) for asset in assets]
        if all(key in self.marked_for_delete for key in keys):
            for key in keys:
                self.marked_for_delete.pop(key, None)
        else:
            self.marked_for_delete.update(zip(keys, assets))
        self._marks_changed()
        count = len(self.marked_for_delete)
        self.status.setText('%d asset(s) marked for deletion; Write asks you to confirm.' % count if count else
                            'No assets marked for deletion.')

    def _marks_changed(self):
        keys = set(self.marked_for_delete)
        for model in (self.model, self.collections.model):
            model.marked_keys = keys
        for view in (self.grid, self.table, self.collections.grid, self.collections.table):
            view.viewport().update()
        self.update_delete_button()

    def _clear_delete_marks(self):
        if self.marked_for_delete:
            self.marked_for_delete.clear()
            self._marks_changed()

    def write_pressed(self):
        """Write: delete the confirmed marked assets, then save the edited fields and leave edit mode."""
        deleted = []
        if self.marked_for_delete and not self.access.is_admin:
            # Admin rights were removed (security file re-read) after the assets were marked.
            self._clear_delete_marks()
            self.show_error('Only administrators can delete assets; the marks were removed. '
                            'Your other changes are written.')
        if self.marked_for_delete:
            deleted = self.confirm_and_delete()
            if deleted is None:  # Cancelled or failed: stay in edit mode, marks kept.
                self._set_edit_button(True)
                return
        had_session = self.edit_session is not None
        if self.write_edit():
            if deleted and not had_session:
                self.loaded(self.assets)  # write_edit refreshes the views only after a session
        elif deleted:
            # Still editing (the draft did not validate): drop the deleted rows, keep the draft.
            self._drop_deleted_rows(deleted)
            self._restore_edit_selection()

    def _drop_deleted_rows(self, deleted):
        """Remove deleted assets from both views without committing the edit draft."""
        keys = {asset_key(asset) for asset in deleted}
        self._restoring_edit_selection = True
        try:
            self.model.replace([asset for asset in self.model.assets if asset_key(asset) not in keys])
            for key in keys:
                self.collections.lookup.pop(key, None)
            self.collections.refresh()
        finally:
            self._restoring_edit_selection = False

    def _forget_deleted(self, deleted):
        keys = {asset_key(asset) for asset in deleted}
        self.assets[:] = [asset for asset in self.assets if asset_key(asset) not in keys]
        if self.edit_session:
            # The edited fields of a deleted asset are dropped with it.
            self.edit_session['originals'] = [asset for asset in self.edit_session['originals']
                                              if asset_key(asset) not in keys]
        for key in keys:
            self.marked_for_delete.pop(key, None)
        self._marks_changed()

    def confirm_and_delete(self):
        """Show the confirmation window and delete what stays checked.

        Returns the deleted assets, or None when the user cancelled or deletion failed.
        """
        dialog = DeleteDialog(list(self.marked_for_delete.values()), self.cache, self)
        if not exec_dialog(dialog):
            return None
        chosen, remove_files = dialog.confirmed_assets(), dialog.remove_files()
        if not chosen:
            self._clear_delete_marks()
            return []
        tokens = {key: handle['token'] for key, handle in (self.edit_session or {}).get('locks', {}).items()}
        temporary = []
        try:
            self.access.refresh()
            for asset in chosen:
                library = writable_library(self.settings, self.access, asset)
                key = root_key(library['root'])
                if key not in tokens:
                    # Marked earlier in a library this edit session no longer holds.
                    handle = self._acquire_edit_lock(library)
                    temporary.append(handle)
                    tokens[key] = handle['token']
            problems = delete_assets(self.settings, self.access, chosen, tokens, remove_files=remove_files)
        except DeleteStopped as error:
            # Some libraries were written before one failed: show those as deleted, keep the rest marked.
            self._forget_deleted(error.deleted)
            self._drop_deleted_rows(error.deleted)
            self.show_error('Deleted %d asset(s), then deletion stopped: %s\nThe other assets are still marked.'
                            % (len(error.deleted), error) + ('\n\n' + '\n'.join(error.problems) if error.problems else ''))
            return None
        except Exception as error:
            self.show_error('Nothing was deleted: %s' % error)
            return None
        finally:
            for handle in temporary:
                try:
                    release_edit_lock(handle)
                except Exception as error:
                    self.show_error('Could not remove the library lock: ' + str(error))
        self._forget_deleted(chosen)
        self._clear_delete_marks()  # unchecked assets are kept and unmarked
        if not remove_files:
            self.status.setText('Deleted %d asset(s) from the database; their files were kept.' % len(chosen))
        else:
            self.status.setText('Deleted %d asset(s) from the database and storage%s.'
                                % (len(chosen), '; some files were kept' if problems else ''))
        if problems:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle('TinyLib · Deleted with notes')
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setText('The assets were deleted from the database. Some files were kept:')
            box.setDetailedText('\n'.join(problems))
            box.setInformativeText('\n'.join(problems[:5]) + ('\n…' if len(problems) > 5 else ''))
            exec_dialog(box)
        return chosen

    def write_edit(self, selection_change=False, next_asset=None, next_source=None, next_assets=None):
        if not self.edit_session:
            if not selection_change:
                self._clear_delete_marks()
                self.edit_mode = False
                self._set_edit_button(False)
                self._freeze_for_edit(False)
                self.selection()
            return True
        originals = self.edit_session['originals']
        try:
            changes = self.properties.changes() if originals else {}
            if changes and len(originals) > 1:
                edits = [(original, {key: value for key, value in changes.items()
                                     if original.get(key) != value}) for original in originals]
                edits = [(original, patch) for original, patch in edits if patch]
                updated = save_assets(self.settings, self.access, edits,
                                      {key: handle['token'] for key, handle in self.edit_session['locks'].items()}) if edits else []
            elif changes:
                updated = [save_asset(self.settings, self.access, originals[0], changes,
                                      edit_token=self.edit_session['locks'][root_key(originals[0]['library_root'])]['token'])]
            else:
                updated = []
        except Exception as error:
            self.properties.message.setText(str(error))
            self._set_edit_button(True)
            return False
        updates = {asset_key(asset): asset for asset in updated}
        if updates:
            for asset in self.assets:
                replacement = updates.get(asset_key(asset))
                if replacement is not None:
                    personal = {key: value for key, value in asset.items() if key.startswith('_')}
                    asset.clear()
                    asset.update(replacement)
                    asset.update(personal)
        originals = [updates.get(asset_key(asset), asset) for asset in originals]
        self.edit_session['originals'] = originals
        if originals:
            self.edit_session['original'] = originals[0]
        if selection_change:
            targets = list(next_assets if next_assets is not None else [next_asset] if next_asset else [])
            valid_type = bool(targets) and len({a.get('kind') for a in targets}) == 1
            target_roots = {root_key(a['library_root']) for a in targets}
            if not targets or not valid_type or target_roots.issubset(self.edit_session['locks']):
                self.edit_session['originals'] = targets if valid_type else []
                if valid_type:
                    self.edit_session['original'] = targets[0]
                    self.edit_session['selection_source'] = next_source
                    # Force a refill after a committed draft, even if keys overlap.
                    self.properties.editing = False
                    self.properties.set_assets(targets, True, True)
                self.status.setText('Write mode - library locks retained.')
                return True
        if not self._release_edit_session(update_selection=not selection_change, keep_mode=selection_change):
            return False
        if selection_change:
            self.status.setText('Asset properties saved; write mode remains active.')
        else:
            self.loaded(self.assets)
            self.status.setText('Asset properties saved.')
        return True

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
        QtWidgets.QApplication.clipboard().setText(format_assets(assets, self.path_notation.currentData(),
                                                                 self.trim_source(self.selection_source == 'collection')))
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

    def toggle_details(self, enabled):
        self.details_panel.setVisible(enabled)
        try:
            self.preferences.set_display(details_panel=enabled)
        except Exception as error:
            self.show_error(str(error))

    def collections_changed(self):
        self.filter()

    def preview(self, *_):
        assets = self.selected_assets()
        if len(assets) == 1:
            try:
                self.play_asset(assets[0], self.selection_source)
            except Exception as error:
                self.show_error(str(error))

    def play_asset(self, asset, source, replace=None):
        """Open the player for ``asset``. ``source`` is where it was played from, ``'main'``
        or ``'collection'``: PgUp/PgDn then step through that list."""
        self.access.refresh()
        self.access.require(asset['library_root'], 'view')
        # Played from a collection, the player uses and edits that collection's in/out points;
        # otherwise the user's own.
        collection = self.collections.combo.currentData() if source == 'collection' else None
        trim = self.preferences.trim(asset, collection)
        self.player.play(dict(asset, _trim=trim, _trim_collection=collection, _play_source=source), replace)

    def play_list(self, source):
        """The assets PgUp/PgDn step through: the current collection, or what the main view shows
        (filtered and sorted as it is now)."""
        if source == 'collection':
            return [asset for asset in self.collections.model.assets if not asset.get('_unavailable')]
        return list(self.model.assets)

    def play_adjacent(self, asset, step, window):
        """PgUp/PgDn in a player: the previous or next asset of its list, wrapping at the ends."""
        source = asset.get('_play_source', 'main')
        target = adjacent_asset(self.play_list(source), asset, step)
        if target is None:
            return
        try:
            self.play_asset(target, source, replace=window)
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
                                        'collection': self.collections.combo.currentText() if collection else None,
                                        'trim': self.trim_source(collection)})
            self.status.setText(str(result) if isinstance(result, str) else 'Action completed.')
        except Exception as error:
            self.show_error(str(error))

    def trim_source(self, collection):
        """asset -> in/out points: the current collection's, or the user's own."""
        return self.collections.trim_for if collection else self.preferences.trim

    def rate_asset(self, asset, stars):
        try:
            self.preferences.rate([asset], stars)
            key = asset_key(asset)
            for current in self.assets:
                if asset_key(current) == key:
                    current['_rating'] = stars
            self.collections.populate()
            self.filter()
            self.status.setText('Set %s to %d star%s.' % (display_name(asset), stars, '' if stars == 1 else 's'))
        except Exception as error:
            self.show_error(str(error))

    def trim_asset(self, asset, trim):
        try:
            self.preferences.set_trim(asset, trim, asset.get('_trim_collection'))
            where = ' in the collection' if asset.get('_trim_collection') else ''
            if trim:
                self.status.setText('Set %s in/out to %d–%d%s.' % (display_name(asset), trim[0], trim[1], where))
            else:
                self.status.setText('Cleared %s in/out%s.' % (display_name(asset), where))
        except Exception as error:
            self.show_error(str(error))

    def update_admin_controls(self):
        """Admin-only controls are hidden, not just disabled, for everyone else."""
        for widget in (self.access_button, self.config_button, self.library_tools_button):
            widget.setVisible(self.access.is_admin)

    def manage_access(self):
        if not self.require_admin():
            return
        try:
            dialog = AccessDialog(self.access, self.registry, self)
            accepted = exec_dialog(dialog)
            dialog.deleteLater()
            if accepted:
                self.player.stop()
                self.reload()
        except Exception as error:
            self.show_error(str(error))

    def library_crosscheck(self):
        if not self.require_admin():
            return
        current = self.categories.currentItem()
        root = current.data(0, QtCore.Qt.UserRole)[0] if current else ''
        libraries = [library for library in self.settings['libraries']
                     if self.access.can(library['root'], 'view')]
        library = next((item for item in libraries if root_key(item['root']) == root_key(root)), None)
        if library is None:
            if not libraries:
                self.show_error('No visible library is available for crosscheck.')
                return
            names = [item['name'] for item in libraries]
            name, accepted = QtWidgets.QInputDialog.getItem(
                self, 'Library crosscheck', 'Library', names, 0, False)
            if not accepted:
                return
            library = libraries[names.index(name)]
        dialog = CrosscheckDialog(library, self, self.access)
        exec_dialog(dialog)
        result = dialog.crosscheck_result
        dialog.deleteLater()
        if result:
            self.status.setText('Library crosscheck saved: ' + result['log_path'])

    def create_new_library(self):
        if not self.require_admin():
            return
        dialog = CreateLibraryDialog(self.settings, self.access, self)
        exec_dialog(dialog)
        created = dialog.created_library
        dialog.deleteLater()
        if created is not None:
            self.settings['libraries'].append(created)
            self.reload(use_index_cache=False)

    def library_sort(self):
        if not self.require_admin():
            return
        if self.edit_session:
            return self.show_error('Finish editing before sorting the library.')
        current = self.categories.currentItem()
        root = current.data(0, QtCore.Qt.UserRole)[0] if current else ''
        libraries = [library for library in self.settings['libraries']
                     if not (library.get('read_only') or self.access.read_only(library['root'])) and self.access.can(library['root'], 'view')
                     and self.access.can(library['root'], 'edit')]
        library = next((item for item in libraries if root_key(item['root']) == root_key(root)), None)
        if library is None:
            if root:
                return self.show_error('The selected library is read-only or unavailable for editing.')
            if not libraries:
                return self.show_error('No writable library is available for library sort.')
            names = [item['name'] for item in libraries]
            name, accepted = QtWidgets.QInputDialog.getItem(self, 'Library sort', 'Library', names, 0, False)
            if not accepted:
                return
            library = libraries[names.index(name)]
        dialog = LibrarySortDialog(library, self.settings, self.access, self)
        exec_dialog(dialog)
        sorted_result = dialog.sort_result
        dialog.deleteLater()
        if sorted_result is not None:
            self.reload(use_index_cache=False)

    def labels_from_names(self):
        if not self.require_admin():
            return
        if self.edit_session:
            return self.show_error('Finish editing before making labels.')
        libraries = [library for library in self.settings['libraries']
                     if not (library.get('read_only') or self.access.read_only(library['root']))
                     and self.access.can(library['root'], 'view') and self.access.can(library['root'], 'edit')]
        if not libraries:
            return self.show_error('No writable library is available for labels.')
        current = self.categories.currentItem()
        root = current.data(0, QtCore.Qt.UserRole)[0] if current else ''
        dialog = LabelsDialog(libraries, self.assets, self.settings, self.access, root, self)
        exec_dialog(dialog)
        written = dialog.result
        dialog.deleteLater()
        if written and written['changes']:
            self.reload(use_index_cache=False)
            self.status.setText('Labels from names: %d labels written.' % len(written['changes']))

    def keyword_cleaner(self):
        if not self.require_admin():
            return
        if self.edit_session:
            return self.show_error('Finish editing before cleaning keywords.')
        libraries = [library for library in self.settings['libraries']
                     if not (library.get('read_only') or self.access.read_only(library['root']))
                     and self.access.can(library['root'], 'view') and self.access.can(library['root'], 'edit')]
        if not libraries:
            return self.show_error('No writable library is available for the keyword cleaner.')
        current = self.categories.currentItem()
        root = current.data(0, QtCore.Qt.UserRole)[0] if current else ''
        dialog = KeywordsDialog(libraries, self.assets, self.main_selected(), self.settings, self.access, root, self)
        exec_dialog(dialog)
        written = dialog.result
        dialog.deleteLater()
        if written and written['changes']:
            self.reload(use_index_cache=False)
            self.status.setText('Keyword cleaner: keywords of %d assets changed.' % len(written['changes']))

    def rescan_metadata(self):
        if not self.require_admin():
            return
        assets = list(self.selected_assets())
        if not assets:
            current = self.categories.currentItem()
            root = current.data(0, QtCore.Qt.UserRole)[0] if current else ''
            libraries = [library for library in self.settings['libraries']
                         if self.access.can(library['root'], 'view')]
            library = next((item for item in libraries
                            if root_key(item['root']) == root_key(root)), None)
            if library is None:
                names = [item['name'] for item in libraries]
                if not names:
                    self.show_error('No visible library is available for metadata rescan.')
                    return
                name, accepted = QtWidgets.QInputDialog.getItem(
                    self, 'Rescan metadata', 'Library', names, 0, False)
                if not accepted:
                    return
                library = libraries[names.index(name)]
            assets = [asset for asset in self.assets
                      if root_key(asset['library_root']) == root_key(library['root'])]
        if not assets:
            self.show_error('No assets are available for metadata rescan.')
            return
        dialog = MetadataRescanDialog(assets, self.settings, self)
        accepted = exec_dialog(dialog)
        scan = dialog.scan_result
        dialog.deleteLater()
        if accepted and scan and scan['results']:
            self.write_rescanned_metadata(scan['results'])

    def require_admin(self):
        """Re-read the security file; only administrators may use Library tools."""
        try:
            require_admin(self.access)
            return True
        except PermissionError as error:
            self.show_error(str(error))
            return False
        finally:
            self.update_admin_controls()

    def write_rescanned_metadata(self, results):
        groups = {}
        for result in results:
            groups.setdefault(root_key(result['asset']['library_root']), []).append(result)
        handles = []
        plans = []
        updated_assets = []
        try:
            require_admin(self.access)
            for root, items in groups.items():
                asset = items[0]['asset']
                config = writable_library(self.settings, self.access, asset)
                try:
                    handle = acquire_edit_lock(config['root'], self.access.identity)
                except LibraryEditLocked as error:
                    if not self.access.is_admin or not self.confirm_edit_override(error.locks):
                        raise
                    handle = acquire_edit_lock(config['root'], self.access.identity, override=True)
                handles.append(handle)
                edits = []
                for item in items:
                    metadata = dict(item['asset'].get('metadata', {}))
                    metadata.update(item['scanned'])
                    edits.append((item['asset'], {'metadata': metadata}))
                database = Library(config['root'], config['name'], config.get('legacy_roots', []))
                plans.append((database, edits, handle))
            for database, edits, handle in plans:
                updated_assets.extend(database.update_assets(edits, edit_token=handle['token']))
        except Exception as error:
            self.show_error('Metadata was not fully updated: ' + str(error))
            return
        finally:
            release_errors = []
            for handle in reversed(handles):
                try:
                    release_edit_lock(handle)
                except Exception as error:
                    release_errors.append(str(error))
            if release_errors:
                self.show_error('Metadata write completed, but lock cleanup failed:\n' +
                                '\n'.join(release_errors))
        updates = {asset_key(asset): asset for asset in updated_assets}
        for asset in self.assets:
            updated = updates.get(asset_key(asset))
            if updated:
                personal = {key: value for key, value in asset.items() if key.startswith('_')}
                asset.clear()
                asset.update(updated)
                asset.update(personal)
        self.loaded(self.assets)
        self.status.setText('Updated metadata for %d asset(s).' % len(updated_assets))

    def ingest(self):
        try:
            from .ingest_ui import IngestDialog
            current = self.categories.currentItem()
            root, category = current.data(0, QtCore.Qt.UserRole) if current else ('', '')
            existing = getattr(self, '_ingest_dialog', None)
            if existing is not None:
                # One form per browser, hidden or not, so "Return to ingest form" and the
                # toolbar button always reach the same form and its running submissions.
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            # Non-modal, so its submission windows ("Return to ingest form") stay clickable.
            self._ingest_dialog = IngestDialog(self.settings, self.assets, self,
                                               library_root=root, category=category)
            self._ingest_dialog.show()
        except Exception as error:
            self.show_error(str(error))

    def update_window_title(self):
        """Window title with the user and its groups, e.g. "TinyLib · Studio library · jan (admins, artists)"."""
        groups = ', '.join(sorted(self.access.groups))
        user = '%s (%s)' % (self.access.identity, groups) if groups else self.access.identity
        self.setWindowTitle('TinyLib · Studio library · ' + user)

    def configuration(self):
        if self.require_admin():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.settings['_config_path']))

    def shutdown(self):
        """Best-effort cleanup when the host quits: release edit locks, stop threads, never prompt."""
        if self.edit_session:
            for handle in self.edit_session['locks'].values():
                try:
                    release_edit_lock(handle)
                except Exception:
                    pass
            self.edit_session = None
        loader = getattr(self, 'loader', None)
        if loader is not None and loader.isRunning():
            loader.requestInterruption()
            loader.wait(2000)
        for task in self.ingest_threads():
            task.wait(5000)  # Tools time out after a minute; don't hold Nuke's exit longer than this.
        self.cache.pool.clear()
        self.player.stop()

    def show_error(self, message):
        QtWidgets.QMessageBox.warning(self, 'TinyLib', message)

    def ingest_threads(self):
        """Metadata scans and manifest builds still running in the ingest form."""
        dialog = getattr(self, '_ingest_dialog', None)
        if dialog is None:
            return []
        try:
            return [task for task in list(dialog._scans) + list(dialog._background) if task.isRunning()]
        except RuntimeError:  # The form was already deleted.
            return []

    def closeEvent(self, event):
        if self.ingest_threads():
            self.status.setText('The ingest form is still reading media metadata; close again in a moment.')
            event.ignore()
            return
        if any(window.worker.isRunning() for window in getattr(self, '_ingest_submissions', [])):
            self.status.setText('Deadline submission is still running; you can keep using the library.')
            event.ignore()
            return
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
        if self.edit_session and not self._release_edit_session():
            event.ignore()
            return
        self.player.stop()
        super().closeEvent(event)
