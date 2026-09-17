"""Nuke-toned browser with virtualized cards, keyword filtering and hover strips."""
from collections import OrderedDict
import json
from .qt import QtCore, QtGui, QtWidgets
from .library import Library
from .preferences import asset_key
from .filters import duration
from .collection_ui import ASSET_MIME
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

    def __init__(self, configs, parent):
        super().__init__(parent)
        self.configs = configs

    def run(self):
        assets, errors = [], []
        for config in self.configs:
            if self.isInterruptionRequested():
                return
            try:
                assets += Library(config['root'], config['name'], config.get('legacy_roots', [])).load()
            except Exception as error:
                errors.append(config['name'] + ': ' + str(error))
        self.loaded.emit(assets, errors)


class ImageResult(QtCore.QObject):
    ready = QtCore.Signal(str, object)


class ImageRead(QtCore.QRunnable):
    def __init__(self, path, signals):
        super().__init__()
        self.path, self.signals = path, signals

    def run(self):
        image = QtGui.QImage(self.path)
        if image.width() == 11520 and image.height() == 270:
            image = image.scaled(5760, 135, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        elif '/filmstrip/' not in self.path.replace('\\', '/').lower() and image.width() > 640:
            image = image.scaled(640, 360, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.signals.ready.emit(self.path, image)


class ImageCache(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.images = OrderedDict()
        self.bytes = 0
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
            self.pending.add(path)
            self.pool.start(ImageRead(path, self.signals))
        return QtGui.QPixmap()

    def received(self, path, image):
        self.pending.discard(path)
        self.images[path] = QtGui.QPixmap.fromImage(image)
        self.bytes += image.width() * image.height() * 4
        while len(self.images) > 160 or self.bytes > 96 * 1024 * 1024:
            _, removed = self.images.popitem(last=False)
            self.bytes -= removed.width() * removed.height() * 4
        self.changed.emit()


class AssetModel(QtCore.QAbstractTableModel):
    headers = ['Name', 'Preview', 'Library', 'Category', 'Type', 'Length (s)', 'Width', 'Height', 'Stars', 'Keywords']

    def __init__(self, parent):
        super().__init__(parent)
        self.assets = []

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
        self.endResetModel()

    def flags(self, index):
        flags = super().flags(index)
        return flags | QtCore.Qt.ItemIsDragEnabled if index.isValid() else flags

    def mimeTypes(self):
        return [ASSET_MIME]

    def mimeData(self, indexes):
        data = QtCore.QMimeData()
        rows = sorted({index.row() for index in indexes if index.isValid()})
        data.setData(ASSET_MIME, json.dumps([asset_key(self.assets[row]) for row in rows]).encode('utf-8'))
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

    def pixmap(self, asset, row):
        if (self.play_all or row == self.hover_row) and asset.get('kind') == 'footage':
            strip = self.cache.get(asset.get('filmstrip', ''))
            if not strip.isNull() and strip.width() in (5760, 11520) and strip.height() in (135, 270):
                width = strip.width() // 24
                return strip.copy(self.frame * width, 0, width, strip.height())
        return self.cache.get(asset.get('thumb', ''))

    def sizeHint(self, option, index):
        return QtCore.QSize(self.width, int(self.width * .5625) + 74)

    def paint(self, painter, option, index):
        asset = index.data(QtCore.Qt.UserRole)
        rect = option.rect.adjusted(5, 5, -5, -5)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QColor('#bf945f' if selected else '#3b3b3b'))
        painter.setBrush(QtGui.QColor('#39342d' if selected else '#2c2c2c'))
        painter.drawRoundedRect(rect, 5, 5)
        image_rect = QtCore.QRect(rect.x()+1, rect.y()+1, rect.width()-2, int(self.width*.5625)-8)
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
        painter.setPen(QtGui.QColor('#ececec'))
        metrics = painter.fontMetrics()
        name = metrics.elidedText(asset['name'], QtCore.Qt.ElideRight, rect.width()-20)
        painter.drawText(rect.x()+10, image_rect.bottom()+23, name)
        painter.setPen(QtGui.QColor('#a6a6a6'))
        metadata = asset.get('metadata', {})
        dimensions = '%s × %s' % (metadata['width'], metadata['height']) if metadata.get('width') else asset['category']
        subtitle = asset.get('kind', 'still').upper() + '  ·  ' + dimensions
        if asset.get('_rating'):
            subtitle += '  ·  %s/5' % asset['_rating']
        painter.drawText(rect.x()+10, image_rect.bottom()+44, metrics.elidedText(subtitle, QtCore.Qt.ElideRight, rect.width()-20))
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
        self.setItemDelegate(self.cards)
        cache.changed.connect(self.viewport().update)

    def mouseMoveEvent(self, event):
        index = self.indexAt(event.pos())
        row = index.row() if index.isValid() else -1
        if self.cards.hover_row != row:
            self.cards.hover_row, self.cards.frame = row, 0
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.cards.hover_row = -1
        self.viewport().update()
        super().leaveEvent(event)

class PreviewDialog(QtWidgets.QDialog):
    def __init__(self, asset, parent):
        super().__init__(parent)
        self.setWindowTitle(asset['name'])
        self.resize(1000, 650)
        layout = QtWidgets.QVBoxLayout(self)
        path = asset.get('proxy') or asset.get('thumb')
        if asset.get('kind') != 'footage':
            label = QtWidgets.QLabel()
            label.setAlignment(QtCore.Qt.AlignCenter)
            pixmap = QtGui.QPixmap(path)
            if pixmap.isNull():
                raise ValueError('Preview unavailable: ' + path)
            label.setPixmap(pixmap.scaled(960, 540, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            layout.addWidget(label)
            return
        if not path or not Path(path).is_file():
            raise ValueError('Playback proxy unavailable: ' + str(path))
        binding = QtCore.__name__.split('.')[0]
        if binding == 'PySide6':
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtMultimediaWidgets import QVideoWidget
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.audio.setMuted(True)
            self.player.setAudioOutput(self.audio)
            self.player.setSource(QtCore.QUrl.fromLocalFile(path))
        else:
            from PySide2.QtMultimedia import QMediaPlayer, QMediaContent
            from PySide2.QtMultimediaWidgets import QVideoWidget
            self.player = QMediaPlayer(self)
            self.player.setMuted(True)
            self.player.setMedia(QMediaContent(QtCore.QUrl.fromLocalFile(path)))
        video = QVideoWidget()
        self.player.setVideoOutput(video)
        layout.addWidget(video, 1)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.player.durationChanged.connect(lambda duration: slider.setRange(0, duration))
        self.player.positionChanged.connect(slider.setValue)
        slider.sliderMoved.connect(self.player.setPosition)
        layout.addWidget(slider)
        controls = QtWidgets.QHBoxLayout()
        for label, fn in [('Play', self.player.play), ('Pause', self.player.pause), ('Restart', lambda: self.player.setPosition(0))]:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(fn)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.error_label = QtWidgets.QLabel()
        layout.addWidget(self.error_label)
        signal = self.player.errorOccurred if hasattr(self.player, 'errorOccurred') else self.player.error
        signal.connect(lambda *_: self.error_label.setText(self.player.errorString()))
        self.player.play()

    def done(self, result):
        if hasattr(self, 'player'):
            self.player.stop()
        super().done(result)
