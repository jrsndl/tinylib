"""Asset metadata form controlled by the browser-wide Edit/Write session."""
import copy
import json
import re
from .qt import QtCore, QtWidgets
from .preferences import asset_key


def visible_metadata(key, value):
    return (key.casefold() not in ('path', 'file', 'filename', 'source', 'main', 'proxy', 'thumb', 'filmstrip', 'highres')
            and not (isinstance(value, str) and re.match(r'^(?:[a-zA-Z]:[/\\]|[/\\]{1,2})', value)))


class PropertiesEditor(QtWidgets.QWidget):
    cancel_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.asset = None
        self.editing = False
        self.fields = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
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
            # Nuke's host style can under-report QLineEdit's size hint. The
            # stylesheet supplies the content-height floor and compact padding;
            # this widget floor prevents a form row from compressing it again.
            field.setMinimumHeight(34)
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
        self.cancel = QtWidgets.QPushButton('Cancel edit')
        self.cancel.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.cancel)
        layout.addWidget(self.buttons)
        self.set_editing(False)

    def set_asset(self, asset, editing=False, can_edit=False):
        if editing and self.editing and self.asset and asset and asset_key(self.asset) == asset_key(asset):
            return  # Keep the draft through unrelated selection/paint notifications.
        self.asset = copy.deepcopy(asset) if asset else None
        self.message.setText('' if editing else
                             'Use Edit to modify this library.' if can_edit else
                             'Read-only library or insufficient write permission.')
        self.fill()
        self.set_editing(editing)

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

    def set_editing(self, editing):
        self.editing = bool(editing and self.asset)
        for key, field in self.fields.items():
            field.setReadOnly(not self.editing or key in ('name', 'library', 'category', 'kind', 'range'))
            field.setStyleSheet('color: #888; background: #252525;' if key in ('name', 'library', 'category', 'kind', 'range') else '')
        self.metadata.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed if self.editing else QtWidgets.QAbstractItemView.NoEditTriggers)
        self.buttons.setVisible(self.editing)

    def changes(self):
        if not self.asset or not self.editing:
            return {}
        updated = {'colorspace': self.fields['colorspace'].text().strip()}
        updated['tags'] = list(dict.fromkeys(tag.strip() for tag in self.fields['tags'].text().split(',') if tag.strip()))
        metadata = copy.deepcopy(self.asset.get('metadata', {}))
        for row in range(self.metadata.rowCount()):
            key = self.metadata.item(row, 0).text()
            text = self.metadata.item(row, 1).text()
            metadata[key] = text if isinstance(metadata[key], str) else json.loads(text)
        updated['metadata'] = metadata
        return {key: value for key, value in updated.items() if value != self.asset.get(key)}
