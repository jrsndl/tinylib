"""Locked-by-default asset metadata form, with an explicit Save/Cancel workflow."""
import copy
import json
import re
from .qt import QtCore, QtGui, QtWidgets
from .preferences import asset_key


def lock_icon(unlocked):
    pixmap = QtGui.QPixmap(24, 24)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtGui.QPen(QtGui.QColor('#dddddd'), 2))
    painter.drawRoundedRect(QtCore.QRectF(5, 11, 14, 10), 2, 2)
    painter.drawArc(QtCore.QRectF(8 if not unlocked else 13, 3, 9, 14), 0, 180 * 16)
    painter.drawLine(12, 15, 12, 18)
    painter.end()
    return QtGui.QIcon(pixmap)


def visible_metadata(key, value):
    return (key.casefold() not in ('path', 'file', 'filename', 'source', 'main', 'proxy', 'thumb', 'filmstrip', 'highres')
            and not (isinstance(value, str) and re.match(r'^(?:[a-zA-Z]:[/\\]|[/\\]{1,2})', value)))


class PropertiesEditor(QtWidgets.QWidget):
    save_requested = QtCore.Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.asset = None
        self.fields = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.lock = QtWidgets.QPushButton('Edit properties')
        self.lock.setCheckable(True)
        self.lock.setIcon(lock_icon(False))
        self.lock.toggled.connect(self.toggle)
        layout.addWidget(self.lock)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        self.summary = QtWidgets.QFormLayout()
        self.summary.setVerticalSpacing(2)
        self.summary.setHorizontalSpacing(8)
        self.summary.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.form = QtWidgets.QFormLayout()
        self.form.setVerticalSpacing(5)
        self.form.setHorizontalSpacing(8)
        for label, key in [('Name', 'name'), ('Library', 'library'), ('Category', 'category'), ('Type', 'kind'),
                           ('Range', 'range'), ('Color space', 'colorspace'),
                           ('Keywords', 'tags')]:
            field = QtWidgets.QLineEdit()
            field.setMaximumHeight(27)
            self.fields[key] = field
            target = self.summary if key in ('name', 'library', 'category', 'kind', 'range') else self.form
            target.addRow(label, field)
        content_layout.addLayout(self.summary)
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.HLine)
        content_layout.addWidget(separator)
        content_layout.addLayout(self.form)
        self.metadata = QtWidgets.QTableWidget(0, 2)
        self.metadata.setHorizontalHeaderLabels(['Metadata', 'Value'])
        self.metadata.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.metadata.verticalHeader().hide()
        self.metadata.setMinimumHeight(150)
        self.form.addRow(self.metadata)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.buttons = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.buttons)
        row.setContentsMargins(0, 0, 0, 0)
        self.save = QtWidgets.QPushButton('Save')
        self.save.clicked.connect(self.submit)
        self.cancel = QtWidgets.QPushButton('Cancel')
        self.cancel.clicked.connect(self.cancel_edit)
        row.addWidget(self.save)
        row.addWidget(self.cancel)
        layout.addWidget(self.buttons)
        self.toggle(False)

    def set_asset(self, asset, editable=False):
        if self.asset and asset and asset_key(self.asset) == asset_key(asset) and self.lock.isChecked():
            return  # Keep the draft through unrelated selection/paint notifications.
        self.asset = copy.deepcopy(asset) if asset else None
        self.lock.blockSignals(True)
        self.lock.setChecked(False)
        self.lock.blockSignals(False)
        self.lock.setEnabled(bool(asset) and editable)
        self.message.setText('' if editable else 'Read-only library or insufficient write permission.')
        self.fill()
        self.toggle(False)

    def fill(self):
        asset = self.asset or {}
        for key, field in self.fields.items():
            value = ('%s-%s' % (asset.get('first'), asset.get('last'))
                     if key == 'range' and asset.get('kind') == 'footage' else asset.get(key, ''))
            field.setText(', '.join(value) if key == 'tags' else '' if value is None else str(value))
            if key == 'range':
                field.setVisible(asset.get('kind') == 'footage')
                self.summary.labelForField(field).setVisible(asset.get('kind') == 'footage')
        self.metadata.setRowCount(0)
        for key, value in asset.get('metadata', {}).items():
            if not visible_metadata(key, value):
                continue
            row = self.metadata.rowCount()
            self.metadata.insertRow(row)
            label = QtWidgets.QTableWidgetItem(key)
            label.setFlags(label.flags() & ~QtCore.Qt.ItemIsEditable)
            self.metadata.setItem(row, 0, label)
            self.metadata.setItem(row, 1, QtWidgets.QTableWidgetItem(value if isinstance(value, str) else json.dumps(value)))

    def toggle(self, editing):
        self.lock.setIcon(lock_icon(editing))
        self.lock.setText('Editing — unlocked' if editing else 'Edit properties')
        for key, field in self.fields.items():
            field.setReadOnly(not editing or key in ('name', 'library', 'category', 'kind', 'range'))
            field.setStyleSheet('color: #888; background: #252525;' if key in ('name', 'library', 'category', 'kind', 'range') else '')
        self.metadata.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed if editing else QtWidgets.QAbstractItemView.NoEditTriggers)
        self.buttons.setVisible(editing)
        if not editing and self.asset:
            self.fill()

    def cancel_edit(self):
        self.lock.setChecked(False)

    def submit(self):
        if not self.asset or not self.lock.isChecked():
            return
        try:
            updated = {'colorspace': self.fields['colorspace'].text().strip()}
            updated['tags'] = list(dict.fromkeys(tag.strip() for tag in self.fields['tags'].text().split(',') if tag.strip()))
            metadata = copy.deepcopy(self.asset.get('metadata', {}))
            for row in range(self.metadata.rowCount()):
                key = self.metadata.item(row, 0).text()
                text = self.metadata.item(row, 1).text()
                metadata[key] = text if isinstance(metadata[key], str) else json.loads(text)
            updated['metadata'] = metadata
            changes = {key: value for key, value in updated.items() if value != self.asset.get(key)}
            self.save_requested.emit(self.asset, changes)
        except (ValueError, TypeError) as error:
            self.message.setText(str(error))
