"""Asset metadata form controlled by the browser-wide Edit/Write session."""
import copy
import json
import re
from .qt import QtCore, QtWidgets
from .preferences import asset_key
from .asset_fields import DEFAULT_FIELDS, MULTILINE_FIELDS, apply_defaults


def visible_metadata(key, value):
    return (key.casefold() not in ('path', 'file', 'filename', 'source', 'main', 'proxy', 'thumb', 'filmstrip', 'highres')
            and not (isinstance(value, str) and re.match(r'^(?:[a-zA-Z]:[/\\]|[/\\]{1,2})', value)))


class PropertiesEditor(QtWidgets.QWidget):
    cancel_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.asset = None
        self.assets = []
        self._dirty_text = set()
        self._initial_text = {}
        self._filling = False
        self.editing = False
        self.fields = {}
        self.access = None
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
            if key == 'tags':
                field = QtWidgets.QPlainTextEdit()
                field.setTabChangesFocus(True)
                field.setFixedHeight(max(58, 2 * field.fontMetrics().lineSpacing() + 20))
            else:
                field = QtWidgets.QLineEdit()
            # Nuke's host style can under-report QLineEdit's size hint. The
            # stylesheet supplies the content-height floor and compact padding;
            # this widget floor prevents a form row from compressing it again.
            if key != 'tags':
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
        for key, default in DEFAULT_FIELDS.items():
            if key in MULTILINE_FIELDS:
                field = QtWidgets.QPlainTextEdit()
                field.setMinimumHeight(70)
                field.setMaximumHeight(110)
            elif type(default) is bool:
                field = QtWidgets.QCheckBox()
            elif type(default) is int:
                field = QtWidgets.QSpinBox()
                field.setRange(1, 2147483647)
            else:
                field = QtWidgets.QLineEdit()
                field.setMinimumHeight(34)
            self.fields[key] = field
            self.form.addRow(key.replace('_', ' ').capitalize(), field)
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
        for key, field in self.fields.items():
            if isinstance(field, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit)):
                field.textChanged.connect(lambda *_, key=key: self.text_changed(key))
        self.set_editing(False)

    def set_asset(self, asset, editing=False, can_edit=False):
        if editing and self.editing and len(self.assets) == 1 and self.asset and asset and asset_key(self.asset) == asset_key(asset):
            return  # Keep the draft through unrelated selection/paint notifications.
        self.assets = [apply_defaults(copy.deepcopy(asset))] if asset else []
        self.asset = self.assets[0] if self.assets else None
        self._dirty_text.clear()
        self.message.setText('' if editing else
                             'Use Edit to modify this library.' if can_edit else
                             'Read-only library or insufficient write permission.')
        self.fill()
        self.set_editing(editing)

    def fill(self):
        self._filling = True
        asset = self.asset or {}
        for key, field in self.fields.items():
            value = ('%s-%s' % (asset.get('first'), asset.get('last'))
                     if key == 'range' and asset.get('kind') == 'footage' else asset.get(key, ''))
            if key == 'tags':
                value = ', '.join(value)
            if isinstance(field, QtWidgets.QCheckBox):
                field.setChecked(bool(value))
            elif isinstance(field, QtWidgets.QSpinBox):
                field.setValue(value if type(value) is int else 1)
            elif isinstance(field, QtWidgets.QPlainTextEdit):
                field.setPlainText(str(value))
                field.setPlaceholderText('')
            else:
                field.setText('' if value is None else str(value))
                field.setPlaceholderText('')
            if key in DEFAULT_FIELDS:
                allowed = self.field_allowed(key)
                field.setVisible(allowed)
                self.form.labelForField(field).setVisible(allowed)
            if key == 'range':
                field.setVisible(asset.get('kind') == 'footage')
                self.summary.labelForField(field).setVisible(asset.get('kind') == 'footage')
        self.metadata.setRowCount(0)
        for key, value in asset.get('metadata', {}).items():
            if not visible_metadata(key, value) or not self.field_allowed(key):
                continue
            row = self.metadata.rowCount()
            self.metadata.insertRow(row)
            label = QtWidgets.QTableWidgetItem(key)
            label.setFlags(label.flags() & ~QtCore.Qt.ItemIsEditable)
            self.metadata.setItem(row, 0, label)
            self.metadata.setItem(row, 1, QtWidgets.QTableWidgetItem(value if isinstance(value, str) else json.dumps(value)))

        self._filling = False

    def set_assets(self, assets, editing=False, can_edit=False):
        if len(assets) <= 1:
            return self.set_asset(assets[0] if assets else None, editing, can_edit)
        if (editing and self.editing and
                {asset_key(a) for a in assets} == {asset_key(a) for a in self.assets}):
            return
        self.assets = [apply_defaults(copy.deepcopy(asset)) for asset in assets]
        self.asset = self.assets[0]
        self._dirty_text.clear()
        self.fill()
        self._filling = True
        for key, field in self.fields.items():
            if not isinstance(field, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit)):
                continue
            values = []
            for asset in self.assets:
                value = ('%s-%s' % (asset.get('first'), asset.get('last')) if key == 'range'
                         else asset.get(key, ''))
                value = ', '.join(value) if key == 'tags' else str(value)
                if value not in values:
                    values.append(value)
            placeholder = ''
            if key == 'tags':
                keywords = dict.fromkeys(keyword.strip() for value in values
                                         for keyword in value.split(',') if keyword.strip())
                text = ', '.join(keywords)
            elif len(values) > 1 and key not in ('name', 'library', 'category', 'kind', 'range'):
                # Editable field with differing values: start empty so typing replaces them all,
                # instead of writing the joined text ("ACEScg sRGB") into every asset.
                text = ''
                placeholder = 'Multiple values: ' + ' | '.join(value or '(empty)' for value in values)
            else:
                text = ' '.join(values)
            field.setPlainText(text) if isinstance(field, QtWidgets.QPlainTextEdit) else field.setText(text)
            field.setPlaceholderText(placeholder)
        self.metadata.setRowCount(0)
        metadata_keys = dict.fromkeys(key for asset in self.assets for key in asset.get('metadata', {}))
        for key in metadata_keys:
            values = [asset.get('metadata', {}).get(key, '') for asset in self.assets]
            if not all(visible_metadata(key, value) for value in values) or not self.field_allowed(key):
                continue
            texts = list(dict.fromkeys(value if isinstance(value, str) else json.dumps(value) for value in values))
            row = self.metadata.rowCount()
            self.metadata.insertRow(row)
            self.metadata.setItem(row, 0, QtWidgets.QTableWidgetItem(key))
            self.metadata.setItem(row, 1, QtWidgets.QTableWidgetItem(' '.join(texts)))
        self._initial_text = {key: self.field_text(field) for key, field in self.fields.items()
                              if isinstance(field, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit))}
        self._filling = False
        self.message.setText('Multiedit: changing selection saves only red fields to every selected asset.'
                             if editing else 'Use Edit to modify these assets.' if can_edit else
                             'Read-only library or insufficient write permission.')
        self.set_editing(editing)

    @staticmethod
    def field_text(field):
        return field.toPlainText() if isinstance(field, QtWidgets.QPlainTextEdit) else field.text()

    def text_changed(self, key):
        if self._filling or not self.editing or len(self.assets) < 2:
            return
        field = self.fields[key]
        if field.isReadOnly():
            return
        if self.field_text(field) == self._initial_text.get(key, ''):
            self._dirty_text.discard(key)
        else:
            self._dirty_text.add(key)
        self.style_field(key, field)

    def style_field(self, key, field):
        immutable = key in ('name', 'library', 'category', 'kind', 'range')
        style = 'color: #888; background: #252525;' if immutable else ''
        if isinstance(field, QtWidgets.QPlainTextEdit):
            style = 'background-color: #5c5c5c;'
            field.viewport().setStyleSheet('background-color: #5c5c5c;')
        if (len(self.assets) > 1 and self.editing and not immutable
                and isinstance(field, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit))
                and self.field_allowed(key)):
            color = '#ef5b5b' if key in self._dirty_text else '#ffa02f'
            style += ' border: 2px solid %s;' % color
        field.setStyleSheet(style)

    def set_editing(self, editing):
        self.editing = bool(editing and self.asset)
        for key, field in self.fields.items():
            enabled = self.editing and key not in ('name', 'library', 'category', 'kind', 'range') and self.field_allowed(key)
            if len(self.assets) > 1 and not isinstance(field, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit)):
                enabled = False
            if isinstance(field, QtWidgets.QCheckBox):
                field.setEnabled(enabled)
            else:
                field.setReadOnly(not enabled)
            self.style_field(key, field)
        self.metadata.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed if self.editing and len(self.assets) == 1 else QtWidgets.QAbstractItemView.NoEditTriggers)
        self.buttons.setVisible(self.editing)

    def field_allowed(self, key):
        return bool(self.assets) and (self.access is None or
                all(self.access.field_allowed(asset['library_root'], key) for asset in self.assets))

    def changes(self):
        if not self.asset or not self.editing:
            return {}
        if len(self.assets) > 1:
            updated = {}
            for key in self._dirty_text:
                if not self.field_allowed(key):
                    continue
                text = self.field_text(self.fields[key])
                updated[key] = (list(dict.fromkeys(tag.strip() for tag in re.split(r'[,\r\n]+', text) if tag.strip()))
                                if key == 'tags' else text if key in MULTILINE_FIELDS else text.strip())
            return updated
        updated = {'colorspace': self.fields['colorspace'].text().strip()}
        updated['tags'] = list(dict.fromkeys(tag.strip() for tag in re.split(r'[,\r\n]+', self.fields['tags'].toPlainText()) if tag.strip()))
        metadata = copy.deepcopy(self.asset.get('metadata', {}))
        for row in range(self.metadata.rowCount()):
            key = self.metadata.item(row, 0).text()
            text = self.metadata.item(row, 1).text()
            metadata[key] = text if isinstance(metadata[key], str) else json.loads(text)
        updated['metadata'] = metadata
        for key in DEFAULT_FIELDS:
            if not self.field_allowed(key):
                continue
            field = self.fields[key]
            if isinstance(field, QtWidgets.QCheckBox):
                updated[key] = field.isChecked()
            elif isinstance(field, QtWidgets.QSpinBox):
                updated[key] = field.value()
            elif isinstance(field, QtWidgets.QPlainTextEdit):
                updated[key] = field.toPlainText()
            else:
                updated[key] = field.text().strip()
        return {key: value for key, value in updated.items() if value != self.asset.get(key)}
