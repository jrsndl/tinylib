"""Nuke-toned browser with virtualized cards, keyword filtering and hover strips."""
from collections import OrderedDict
import json
import math
import os
import threading
import time
from .qt import QtCore, QtGui, QtWidgets
from .library import Library
from .preferences import asset_key
from .filters import duration
from .preferences import ASSET_MIME, COLLECTION_MIME
from .tile_text import DEFAULT_TEMPLATE, render_template, validate_template
from .path_format import format_assets
from .thumbnail_cache import cache_root, cached_image_path
from pathlib import Path

STYLE = '''
QWidget { background: #282828; color: #d4d4d4; font-family: "Segoe UI"; font-size: 12px; }
QLabel#heading { font-size: 21px; font-weight: 600; color: #eeeeee; }
QLabel#muted { color: #969696; }
QLineEdit, QComboBox, QDoubleSpinBox, QPlainTextEdit { background: #202020; border: 1px solid #444; border-radius: 4px; padding: 7px; selection-background-color: #80623c; }
QLineEdit:focus, QComboBox:focus { border-color: #bf8d4d; }
QPushButton { background: #383838; border: 1px solid #4b4b4b; border-radius: 4px; padding: 7px 12px; }
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
        cached = None
        try:
            cached = cached_image_path(self.disk_root, self.path)
            if cached.is_file():
                image = QtGui.QImage(str(cached))
                if not image.isNull():
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

    def __init__(self, parent=None, disk_root=None):
        super().__init__(parent)
        self.disk_root = Path(disk_root) if disk_root else cache_root()
        self.images = OrderedDict()
        self.bytes = 0
        self.disk_hits = 0
        self.source_loads = 0
        self.failures = 0
        self.requested = 0
        self.completed = 0
        self.started_at = None
        self.pending = set()
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
        if path not in self.pending:
            if self.started_at is None:
                self.started_at = time.perf_counter()
            self.pending.add(path)
            self.requested += 1
            self.pool.start(ImageRead(path, self.signals, self.disk_root))
            self.activity.emit(self.snapshot())
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
        self.images[path] = QtGui.QPixmap.fromImage(image)
        self.bytes += image.width() * image.height() * 4
        while len(self.images) > 160 or self.bytes > 96 * 1024 * 1024:
            _, removed = self.images.popitem(last=False)
            self.bytes -= removed.width() * removed.height() * 4
        self.activity.emit(self.snapshot())
        self.changed.emit()

    def snapshot(self):
        seconds = time.perf_counter() - self.started_at if self.started_at is not None else 0.0
        return {'requested': self.requested, 'completed': self.completed,
                'pending': len(self.pending), 'disk_hits': self.disk_hits,
                'source_loads': self.source_loads, 'failures': self.failures,
                'seconds': seconds, 'root': str(self.disk_root)}


class AssetModel(QtCore.QAbstractTableModel):
    headers = ['Name', 'Preview', 'Library', 'Category', 'Type', 'Length (s)', 'Width', 'Height', 'Stars', 'Keywords']

    def __init__(self, parent):
        super().__init__(parent)
        self.assets = []
        self.collection_origin = None
        self.path_notation = 'nuke'
        self.revision = 0

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
            return [asset['name'], '', asset['library'], asset['category'], asset.get('kind', ''),
                    '%.2f' % seconds if seconds is not None else '—', metadata.get('width', '—'),
                    metadata.get('height', '—'), str(asset.get('_rating', 0)) + ' / 5',
                    ', '.join(asset.get('tags', []))][index.column()]
        if role == QtCore.Qt.UserRole:
            return asset
        if role == QtCore.Qt.ToolTipRole:
            return asset['name'] + '\n' + ', '.join(asset.get('tags', []))

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
        data.setText(format_assets([self.assets[row] for row in rows], self.path_notation))
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
        return self.cache.get(asset.get('thumb', ''))

    def sizeHint(self, option, index):
        lines = len(validate_template(self.template).split('\n')) if self.template else 0
        caption = lines * 21 + 36 if self.info else 0
        return QtCore.QSize(self.width, int(self.width * .5625) + caption)

    def paint(self, painter, option, index):
        asset = index.data(QtCore.Qt.UserRole)
        rect = option.rect.adjusted(5, 5, -5, -5)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QColor('#bf945f' if selected else '#3b3b3b'))
        painter.setBrush(QtGui.QColor('#39342d' if selected else '#2c2c2c'))
        painter.drawRoundedRect(rect, 5, 5)
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
            painter.drawText(image_rect, QtCore.Qt.AlignCenter, 'Preview unavailable')
        if self.info:
            metrics = painter.fontMetrics()
            lines = render_template(self.template, asset).split('\n') if self.template else []
            for line, text in enumerate(lines):
                painter.setPen(QtGui.QColor('#ececec' if line == 0 else '#a6a6a6'))
                painter.drawText(rect.x()+10, image_rect.bottom()+21*(line+1), metrics.elidedText(text, QtCore.Qt.ElideRight, rect.width()-20))
            rating = max(0, min(5, int(asset.get('_rating', 0))))
            painter.setPen(QtGui.QPen(QtGui.QColor('#969696'), 1.2))
            for star in range(5):
                painter.setBrush(QtGui.QColor('#a6a6a6') if star < rating else QtCore.Qt.NoBrush)
                center = QtCore.QPointF(rect.x()+18+star*22, rect.bottom()-14)
                points = []
                for point in range(10):
                    angle = point * math.pi / 5 - math.pi / 2
                    radius = 8 if point % 2 == 0 else 3.6
                    points.append(center + QtCore.QPointF(radius*math.cos(angle), radius*math.sin(angle)))
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
        self.cards.hover_row = self.indexAt(event.pos()).row()
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
        self.setSpacing(3)
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
            self.setItemDelegate(QtWidgets.QStyledItemDelegate(self))
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

    def mouseMoveEvent(self, event):
        if self.list_mode:
            super().mouseMoveEvent(event)
            return
        index = self.indexAt(event.pos())
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
