"""Nuke-toned browser with virtualized cards, keyword filtering and hover strips."""
from collections import OrderedDict
import json
import math
import os
import threading
import time
from .qt import QtCore, QtGui, QtWidgets, event_pos
from .library import Library
from .preferences import asset_key
from .filters import duration
from .preferences import ASSET_MIME, COLLECTION_MIME
from .tile_text import DEFAULT_TEMPLATE, render_template, validate_template
from .asset_fields import display_name
from .asset_types import is_latlong
from .path_format import format_assets
from .thumbnail_cache import cache_root, cached_image_path
from pathlib import Path

UI_FONT_FAMILY = 'Segoe UI'
UI_FONT_SIZE = 10
HEADING_FONT_SIZE = 16


def interface_font():
    return QtGui.QFont(UI_FONT_FAMILY, UI_FONT_SIZE)


ICONS = Path(__file__).resolve().with_name('icons')


def stylesheet(hosted_in_nuke):
    """The application stylesheet with icon paths filled in (Qt needs absolute paths)."""
    text = STYLE if hosted_in_nuke else Path(__file__).with_name('standalone.qss').read_text(encoding='utf-8')
    return text.replace('@ICONS@', ICONS.as_posix())


def heading_font():
    return QtGui.QFont(UI_FONT_FAMILY, HEADING_FONT_SIZE, QtGui.QFont.DemiBold)


STYLE = '''
QWidget { background: #282828; color: #d4d4d4; }
QLabel#heading { color: #eeeeee; }
QLabel#muted { color: #969696; }
QLineEdit, QComboBox, QDoubleSpinBox, QPlainTextEdit { background: #202020; border: 1px solid #444; border-radius: 4px; padding: 4px 7px; selection-background-color: #80623c; }
QComboBox { background: #383838; border-color: #4b4b4b; padding: 7px 32px 7px 10px; min-height: 18px; }
QComboBox:hover { background: #474747; border-color: #777; }
QComboBox:disabled { color: #737373; background: #2b2b2b; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: 0; border-left: 1px solid #4b4b4b; }
QComboBox::down-arrow { image: url("@ICONS@/arrow_down.png"); width: 10px; height: 6px; }
QComboBox::down-arrow:disabled { image: url("@ICONS@/arrow_down_disabled.png"); }
QComboBox QAbstractItemView { background: #202020; border: 1px solid #4b4b4b; selection-background-color: #80623c; }
QToolButton { background: #383838; border: 1px solid #4b4b4b; border-radius: 4px; padding: 7px 30px 7px 12px; min-height: 18px; }
QToolButton:hover { background: #474747; border-color: #777; }
QToolButton::menu-indicator { image: url("@ICONS@/arrow_down.png"); width: 10px; height: 6px; subcontrol-origin: padding; subcontrol-position: center right; right: 10px; }
QLineEdit { min-height: 20px; }
QLineEdit:focus, QComboBox:focus { border-color: #bf8d4d; }
QPushButton { background: #383838; border: 1px solid #4b4b4b; border-radius: 4px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover { background: #474747; border-color: #777; }
QPushButton:disabled { color: #737373; background: #2b2b2b; }
QPushButton#primary { background: #a7783e; color: #fff; border: 1px solid #c49a67; }
QPushButton#primary:disabled { background: #40382e; color: #777; border-color: #494137; }
QListView, QTreeWidget, QTableView { background: #222; alternate-background-color: #292929; border: 0; outline: none; }
QHeaderView::section { background: #363636; color: #ccc; padding: 6px; border: 0; border-right: 1px solid #484848; }
QPushButton:checked { background: #65513b; border-color: #bf945f; }
QTreeWidget::item { padding: 7px; }
QTreeWidget::item:selected { background: #484035; color: #e5bd88; }
QSplitter::handle { background: #383838; }
QScrollBar:vertical { background: #242424; width: 10px; }
QScrollBar::handle:vertical { background: #555; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #383838; color: #eee; border: 1px solid #777; }
QWidget#browser, QSplitter#panels, QSplitter#panels::handle { background: #191919; }
QWidget#panel { background: #282828; border: 1px solid #3f3f3f; border-radius: 4px; }
QWidget#browser > QLabel#muted { background: transparent; padding-left: 4px; }
QTreeWidget#categories { border: 0; }
QLineEdit#filterValue { padding: 7px 8px; min-height: 18px; }
QLineEdit#search { padding: 7px 10px; min-height: 18px; }
QTreeWidget#categories QHeaderView::section { border: 0; border-bottom: 1px solid #3f3f3f; }
'''


class Loader(QtCore.QThread):
    loaded = QtCore.Signal(object, object)
    progress = QtCore.Signal(object)

    def __init__(self, configs, parent, use_index_cache=True):
        super().__init__(parent)
        self.configs = configs
        self.index_cache_root = parent.cache.disk_root
        self.use_index_cache = use_index_cache

    def run(self):
        assets, errors = [], []
        total = len(self.configs)
        started = time.perf_counter()
        for index, config in enumerate(self.configs, 1):
            if self.isInterruptionRequested():
                return
            self.progress.emit({'phase': 'reading', 'name': config['name'], 'index': index,
                                'total': total, 'assets': len(assets),
                                'seconds': time.perf_counter() - started})
            before = len(assets)
            library = None
            try:
                def normalized(current, record_total):
                    self.progress.emit({'phase': 'normalizing', 'name': config['name'],
                                        'index': index, 'total': total, 'current': current,
                                        'record_total': record_total, 'assets': len(assets) + current,
                                        'seconds': time.perf_counter() - started})
                library = Library(config['root'], config['name'], config.get('legacy_roots', []),
                                  index_cache_root=self.index_cache_root)
                assets += library.load(normalized, use_cache=self.use_index_cache)
            except Exception as error:
                errors.append(config['name'] + ': ' + str(error))
            self.progress.emit({'phase': 'read', 'name': config['name'], 'index': index,
                                'total': total, 'library_assets': len(assets) - before,
                                'assets': len(assets), 'seconds': time.perf_counter() - started,
                                'index_cache_hit': bool(library and library.index_cache_hit)})
        self.loaded.emit(assets, errors)


class ImageResult(QtCore.QObject):
    ready = QtCore.Signal(str, object, bool)


class ImageRead(QtCore.QRunnable):
    def __init__(self, path, signals, disk_root):
        super().__init__()
        self.path, self.signals, self.disk_root = path, signals, disk_root

    def run(self):
        try:
            self._read()
        except Exception:  # Any failure must still answer, or the preview stays pending forever.
            self.signals.ready.emit(self.path, QtGui.QImage(), False)

    def _read(self):
        cached = None
        try:
            cached = cached_image_path(self.disk_root, self.path)
            if cached.is_file():
                image = QtGui.QImage(str(cached))
                if not image.isNull():
                    try:
                        os.utime(cached)  # Keeps previews in daily use out of the 60-day prune.
                    except OSError:
                        pass
                    self.signals.ready.emit(self.path, image, True)
                    return
                cached.unlink()
        except OSError:
            cached = None
        image = QtGui.QImage(self.path)
        if image.width() == 11520 and image.height() == 270:
            image = image.scaled(5760, 135, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        elif '/filmstrip/' not in self.path.replace('\\', '/').lower() and image.width() > 640:
            image = image.scaled(640, 360, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        if cached and not image.isNull():
            temporary = cached.with_name(cached.name + '.%s.%s.tmp' % (os.getpid(), threading.get_ident()))
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                if image.save(str(temporary), 'JPG', 88):
                    os.replace(temporary, cached)
            except OSError:
                pass
            finally:
                try:
                    temporary.unlink()
                except OSError:
                    pass
        self.signals.ready.emit(self.path, image, False)


class ImageCache(QtCore.QObject):
    changed = QtCore.Signal()
    activity = QtCore.Signal(object)
    MAX_IMAGES = 400
    MAX_BYTES = 256 * 1024 * 1024
    # A path evicted this recently is served from its small poster instead of being re-read.
    # Otherwise more visible previews than the cache holds would evict, repaint and re-read forever.
    EVICTION_COOLDOWN = 5.0

    def __init__(self, parent=None, disk_root=None):
        super().__init__(parent)
        self.disk_root = Path(disk_root) if disk_root else cache_root()
        self.images = OrderedDict()
        self.fallbacks = OrderedDict()
        self.fallback_bytes = 0
        self.bytes = 0
        self.disk_hits = 0
        self.source_loads = 0
        self.failures = 0
        self.requested = 0
        self.completed = 0
        self.started_at = None
        self.pending = set()
        self.evicted = OrderedDict()
        # Repaint once cooled-down paths may be read again, so poster-only tiles sharpen.
        self.cooldown = QtCore.QTimer(self)
        self.cooldown.setSingleShot(True)
        self.cooldown.setInterval(int(self.EVICTION_COOLDOWN * 1000) + 50)
        self.cooldown.timeout.connect(self.changed.emit)
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.signals = ImageResult(self)
        self.signals.ready.connect(self.received)

    def get(self, path):
        if not path:
            return QtGui.QPixmap()
        if path in self.images:
            self.images.move_to_end(path)
            return self.images[path]
        evicted_at = self.evicted.get(path)
        if evicted_at is not None:
            if time.monotonic() - evicted_at < self.EVICTION_COOLDOWN:
                if not self.cooldown.isActive():
                    self.cooldown.start()
                return self.fallback(path)
            del self.evicted[path]
        if path not in self.pending:
            if self.started_at is None:
                self.started_at = time.perf_counter()
            self.pending.add(path)
            self.requested += 1
            self.pool.start(ImageRead(path, self.signals, self.disk_root))
            self.activity.emit(self.snapshot())
        return self.fallback(path)

    def fallback(self, path):
        if path in self.fallbacks:
            self.fallbacks.move_to_end(path)
            return self.fallbacks[path]
        return QtGui.QPixmap()

    def received(self, path, image, from_disk):
        self.pending.discard(path)
        self.completed += 1
        if from_disk:
            self.disk_hits += 1
        elif not image.isNull():
            self.source_loads += 1
        else:
            self.failures += 1
        # Keep inexpensive posters outside the large-image LRU. A cache miss
        # during animation or resizing should not erase an already-seen preview.
        if not image.isNull():
            poster = image
            if image.width() in (5760, 11520) and image.height() in (135, 270):
                poster = image.copy(0, 0, image.width() // 24, image.height())
            poster = QtGui.QPixmap.fromImage(poster.scaled(240, 135, QtCore.Qt.KeepAspectRatio,
                                                         QtCore.Qt.SmoothTransformation))
            previous = self.fallbacks.pop(path, None)
            if previous is not None:
                self.fallback_bytes -= previous.width() * previous.height() * 4
            self.fallbacks[path] = poster
            self.fallback_bytes += poster.width() * poster.height() * 4
            while len(self.fallbacks) > 512 or self.fallback_bytes > 24 * 1024 * 1024:
                _, removed = self.fallbacks.popitem(last=False)
                self.fallback_bytes -= removed.width() * removed.height() * 4
        previous = self.images.pop(path, None)
        if previous is not None:
            self.bytes -= previous.width() * previous.height() * 4
        self.images[path] = QtGui.QPixmap.fromImage(image)
        self.bytes += image.width() * image.height() * 4
        while len(self.images) > self.MAX_IMAGES or self.bytes > self.MAX_BYTES:
            evicted_path, removed = self.images.popitem(last=False)
            self.bytes -= removed.width() * removed.height() * 4
            self.evicted[evicted_path] = time.monotonic()
        while len(self.evicted) > 4096:
            self.evicted.popitem(last=False)
        self.activity.emit(self.snapshot())
        self.changed.emit()

    def snapshot(self):
        seconds = time.perf_counter() - self.started_at if self.started_at is not None else 0.0
        return {'requested': self.requested, 'completed': self.completed,
                'pending': len(self.pending), 'disk_hits': self.disk_hits,
                'source_loads': self.source_loads, 'failures': self.failures,
                'seconds': seconds, 'root': str(self.disk_root)}


MARKED_COLOR = '#ef5b5b'


def is_marked(index):
    model = index.model()
    keys = getattr(model, 'marked_keys', None)
    return bool(keys) and asset_key(index.data(QtCore.Qt.UserRole)) in keys


def paint_marked(painter, rect, label=True):
    """Red veil and outline over the preview of an asset marked for deletion."""
    painter.fillRect(rect, QtGui.QColor(166, 40, 40, 110))
    painter.setPen(QtGui.QPen(QtGui.QColor(MARKED_COLOR), 2))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawRect(rect.adjusted(1, 1, -1, -1))
    if label:
        painter.setPen(QtGui.QColor('white'))
        painter.drawText(rect, QtCore.Qt.AlignCenter, 'Marked for deletion')


class AssetModel(QtCore.QAbstractTableModel):
    headers = ['Name', 'Preview', 'Library', 'Category', 'Type', 'Length (s)', 'Width', 'Height', 'Stars', 'Keywords']

    def __init__(self, parent):
        super().__init__(parent)
        self.assets = []
        self.collection_origin = None
        self.path_notation = 'nuke'
        self.trim_for = None  # asset -> (in, out) for dragged paths, set by the owner.
        self.revision = 0
        self.marked_keys = set()  # asset_key()s marked for deletion in edit mode

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.assets)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        asset = self.assets[index.row()]
        if role == QtCore.Qt.DisplayRole:
            seconds = duration(asset)
            metadata = asset.get('metadata', {})
            return [display_name(asset), '', asset['library'], asset['category'], asset.get('kind', ''),
                    '%.2f' % seconds if seconds is not None else '—', metadata.get('width', '—'),
                    metadata.get('height', '—'), str(asset.get('_rating', 0)) + ' / 5',
                    ', '.join(asset.get('tags', []))][index.column()]
        if role == QtCore.Qt.UserRole:
            return asset
        if role == QtCore.Qt.ToolTipRole:
            marked = '\nMarked for deletion' if asset_key(asset) in self.marked_keys else ''
            label = display_name(asset)
            title = label if label == asset['name'] else '%s\nIngest name: %s' % (label, asset['name'])
            return title + '\n' + ', '.join(asset.get('tags', [])) + marked
        if self.marked_keys and asset_key(asset) in self.marked_keys:
            if role == QtCore.Qt.ForegroundRole:
                return QtGui.QBrush(QtGui.QColor(MARKED_COLOR))
            if role == QtCore.Qt.FontRole:
                font = QtGui.QFont()
                font.setStrikeOut(True)
                return font

    def replace(self, assets):
        self.beginResetModel()
        self.assets = assets
        self.revision += 1
        self.endResetModel()

    def flags(self, index):
        flags = super().flags(index)
        return flags | QtCore.Qt.ItemIsDragEnabled if index.isValid() else flags

    def mimeTypes(self):
        return [ASSET_MIME, COLLECTION_MIME]

    def mimeData(self, indexes):
        data = QtCore.QMimeData()
        rows = sorted({index.row() for index in indexes if index.isValid()})
        data.setData(ASSET_MIME, json.dumps([asset_key(self.assets[row]) for row in rows]).encode('utf-8'))
        if self.collection_origin:
            origin = dict(self.collection_origin, keys=[self.assets[row].get('_collection_key') or asset_key(self.assets[row]) for row in rows])
            data.setData(COLLECTION_MIME, json.dumps(origin).encode('utf-8'))
        data.setText(format_assets([self.assets[row] for row in rows], self.path_notation, self.trim_for))
        return data

    def supportedDragActions(self):
        return QtCore.Qt.CopyAction


class Cards(QtWidgets.QStyledItemDelegate):
    def __init__(self, view, cache):
        super().__init__(view)
        self.view, self.cache = view, cache
        self.width = 248
        self.hover_row = -1
        self.frame = 0
        self.play_all = False
        self.info = True
        self.template = DEFAULT_TEMPLATE

    def pixmap(self, asset, row):
        if (self.play_all or row == self.hover_row) and asset.get('kind') == 'footage':
            strip = self.cache.get(asset.get('filmstrip', ''))
            if not strip.isNull() and strip.width() in (5760, 11520) and strip.height() in (135, 270):
                width = strip.width() // 24
                return strip.copy(self.frame * width, 0, width, strip.height())
        thumbnail = self.cache.get(asset.get('thumb', ''))
        if not thumbnail.isNull():
            return thumbnail
        # A filmstrip poster also covers assets whose thumbnail is still queued.
        return self.cache.fallback(asset.get('filmstrip', ''))

    def sizeHint(self, option, index):
        lines = len(validate_template(self.template).split('\n')) if self.template else 0
        caption = lines * 21 + 6 if self.info else 0  # the rating is drawn on the image
        return QtCore.QSize(self.width, int(self.width * .5625) + caption)

    def paint(self, painter, option, index):
        asset = index.data(QtCore.Qt.UserRole)
        # 3 px inset plus 1 px item spacing on each side: 8 px between neighbouring tiles.
        rect = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        painter.save()
        # Square-cornered card, drawn without antialiasing so its 1 px outline stays crisp.
        painter.setPen(QtGui.QColor('#bf945f' if selected else '#3b3b3b'))
        painter.setBrush(QtGui.QColor('#39342d' if selected else '#2c2c2c'))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        image_rect = QtCore.QRect(rect.x()+1, rect.y()+1, rect.width()-2, int(self.width*.5625)-12)
        painter.fillRect(image_rect, QtGui.QColor('#181818'))
        pixmap = self.pixmap(asset, index.row())
        if not pixmap.isNull():
            fitted = pixmap.size().scaled(image_rect.size(), QtCore.Qt.KeepAspectRatio)
            target = QtCore.QRect(QtCore.QPoint(), fitted)
            target.moveCenter(image_rect.center())
            painter.drawPixmap(target, pixmap)
        else:
            painter.setPen(QtGui.QColor('#707070'))
            loading = any(asset.get(role) in self.cache.pending for role in ('thumb', 'filmstrip'))
            painter.drawText(image_rect, QtCore.Qt.AlignCenter, 'Loading preview…' if loading else 'Preview unavailable')
        if is_latlong(asset):
            badge = QtCore.QRect(image_rect.x() + 6, image_rect.y() + 6, 34, 18)
            painter.fillRect(badge, QtGui.QColor(0, 0, 0, 170))
            painter.setPen(QtGui.QColor('#e0b47a'))
            painter.drawText(badge, QtCore.Qt.AlignCenter, '360')
        rating = max(0, min(5, int(asset.get('_rating', 0) or 0)))
        if rating:
            paint_rating(painter, image_rect, rating)
        if is_marked(index):
            paint_marked(painter, image_rect)
        if self.info:
            metrics = painter.fontMetrics()
            lines = render_template(self.template, asset).split('\n') if self.template else []
            for line, text in enumerate(lines):
                painter.setPen(QtGui.QColor('#ececec' if line == 0 else '#a6a6a6'))
                painter.drawText(rect.x()+10, image_rect.bottom()+21*(line+1), metrics.elidedText(text, QtCore.Qt.ElideRight, rect.width()-20))
        painter.restore()


def paint_rating(painter, image_rect, rating):
    """The personal star rating on a tile's image, top right, like the 360 badge top left.
    Only rated assets show it."""
    step, outer, inner = 13, 5.6, 2.5
    badge = QtCore.QRect(image_rect.right() - 6 - (5 * step + 6), image_rect.y() + 6, 5 * step + 6, 18)
    painter.save()
    painter.fillRect(badge, QtGui.QColor(0, 0, 0, 170))
    painter.setRenderHint(QtGui.QPainter.Antialiasing)  # Smooth stars only.
    painter.setPen(QtGui.QPen(QtGui.QColor('#e0b47a'), 1.0))
    for star in range(5):
        painter.setBrush(QtGui.QColor('#e0b47a') if star < rating else QtCore.Qt.NoBrush)
        center = QtCore.QPointF(badge.x() + 3 + step / 2 + star * step, badge.center().y() + 1)
        points = []
        for point in range(10):
            angle = point * math.pi / 5 - math.pi / 2
            radius = outer if point % 2 == 0 else inner
            points.append(center + QtCore.QPointF(radius * math.cos(angle), radius * math.sin(angle)))
        painter.drawPolygon(QtGui.QPolygonF(points))
    painter.restore()


class TinyPreview(QtWidgets.QStyledItemDelegate):
    def __init__(self, cards, parent):
        super().__init__(parent)
        self.cards = cards

    def paint(self, painter, option, index):
        painter.save()
        painter.fillRect(option.rect, QtGui.QColor('#484035' if option.state & QtWidgets.QStyle.State_Selected else '#222222'))
        pixmap = self.cards.pixmap(index.data(QtCore.Qt.UserRole), index.row())
        if not pixmap.isNull():
            target = QtCore.QRect(QtCore.QPoint(), pixmap.size().scaled(option.rect.size() - QtCore.QSize(8, 8), QtCore.Qt.KeepAspectRatio))
            target.moveCenter(option.rect.center())
            painter.drawPixmap(target, pixmap)
        if is_marked(index):
            paint_marked(painter, option.rect.adjusted(2, 2, -2, -2), label=False)
        painter.restore()


class DetailsView(QtWidgets.QTableView):
    def __init__(self, cards, parent=None):
        super().__init__(parent)
        self.cards = cards
        self.setMouseTracking(True)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self.setDefaultDropAction(QtCore.Qt.CopyAction)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(54)
        self.setItemDelegateForColumn(1, TinyPreview(cards, self))

    def mouseMoveEvent(self, event):
        self.cards.hover_row = self.indexAt(event_pos(event)).row()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.cards.hover_row = -1
        self.viewport().update()
        super().leaveEvent(event)


class Grid(QtWidgets.QListView):
    def __init__(self, cache, parent=None):
        super().__init__(parent)
        self.setViewMode(QtWidgets.QListView.IconMode)
        self.setResizeMode(QtWidgets.QListView.Adjust)
        self.setMovement(QtWidgets.QListView.Static)
        self.setLayoutMode(QtWidgets.QListView.Batched)
        self.setBatchSize(80)
        self.setUniformItemSizes(True)
        self.setSpacing(1)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)  # The panel already frames the view.
        self.setMouseTracking(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self.setDefaultDropAction(QtCore.Qt.CopyAction)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.cards = Cards(self, cache)
        self.list_mode = False
        self.list_revision = -1
        self.setItemDelegate(self.cards)
        cache.changed.connect(self.cache_changed)

    def cache_changed(self):
        if not self.list_mode:
            self.viewport().update()

    def setModel(self, model):
        previous = self.model()
        if previous is not None:
            try:
                previous.modelReset.disconnect(self.update_list_geometry)
            except (RuntimeError, TypeError):
                pass
        super().setModel(model)
        if model is not None:
            model.modelReset.connect(self.update_list_geometry)

    def update_list_geometry(self):
        if not self.list_mode or self.model() is None or self.list_revision == self.model().revision:
            return
        self.list_revision = self.model().revision
        metrics = self.fontMetrics()
        width = max((metrics.horizontalAdvance(str(asset.get('name', ''))) for asset in self.model().assets), default=80) + 28
        self.setGridSize(QtCore.QSize(max(120, width), max(28, metrics.height() + 10)))
        self.scheduleDelayedItemsLayout()

    def configure_mode(self, mode):
        if mode == 'List':
            self.list_mode = True
            self.setViewMode(QtWidgets.QListView.ListMode)
            self.setFlow(QtWidgets.QListView.LeftToRight)
            self.setWrapping(True)
            self.setLayoutMode(QtWidgets.QListView.SinglePass)
            self.setResizeMode(QtWidgets.QListView.Fixed)
            self.setTextElideMode(QtCore.Qt.ElideNone)
            if getattr(self, '_list_delegate', None) is None:
                self._list_delegate = QtWidgets.QStyledItemDelegate(self)
            self.setItemDelegate(self._list_delegate)
            self.list_revision = -1
            self.update_list_geometry()
        else:
            self.list_mode = False
            self.setViewMode(QtWidgets.QListView.IconMode)
            self.setFlow(QtWidgets.QListView.LeftToRight)
            self.setWrapping(True)
            self.setLayoutMode(QtWidgets.QListView.Batched)
            self.setResizeMode(QtWidgets.QListView.Adjust)
            self.setTextElideMode(QtCore.Qt.ElideRight)
            self.setItemDelegate(self.cards)
            self.setGridSize(QtCore.QSize())
        self.setMovement(QtWidgets.QListView.Static)
        # Qt's view-mode defaults can reset drop acceptance when switching modes.
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDrop)
        self.setAcceptDrops(True)
        self.doItemsLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.list_mode:
            self.scheduleDelayedItemsLayout()

    def updateGeometries(self):
        super().updateGeometries()
        # Icon mode scrolls per pixel with a tiny step, so a wheel notch moved only a few
        # pixels. One notch (normally three steps) now moves one row of tiles.
        if self.list_mode or self.model() is None or not self.model().rowCount():
            return
        row = self.sizeHintForIndex(self.model().index(0, 0)).height() + 2 * self.spacing()
        if row > 0:
            lines = max(1, QtWidgets.QApplication.wheelScrollLines())
            self.verticalScrollBar().setSingleStep(max(20, row // lines))

    def mouseMoveEvent(self, event):
        if self.list_mode:
            super().mouseMoveEvent(event)
            return
        index = self.indexAt(event_pos(event))
        row = index.row() if index.isValid() else -1
        if self.cards.hover_row != row:
            self.cards.hover_row, self.cards.frame = row, 0
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.list_mode:
            super().leaveEvent(event)
            return
        self.cards.hover_row = -1
        self.viewport().update()
        super().leaveEvent(event)
