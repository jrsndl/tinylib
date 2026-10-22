"""The Preferences window of the user menu: personal display settings kept in the user's preferences."""
from .path_format import FORMATS
from .qt import QtWidgets
from .tile_text import DEFAULT_TEMPLATE, TOKENS, validate_template

class PreferencesDialog(QtWidgets.QDialog):
    def __init__(self, values, parent=None):
        """``values``: the current settings, keys of DISPLAY_DEFAULTS plus tile_template."""
        super().__init__(parent)
        self.setWindowTitle('Preferences')
        self.setMinimumWidth(520)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.path_notation = QtWidgets.QComboBox()
        for identifier, label in FORMATS:
            self.path_notation.addItem(label, identifier)
        self.path_notation.setCurrentIndex(max(0, self.path_notation.findData(values['path_notation'])))
        self.path_notation.setToolTip('File-sequence notation for external drag-and-drop, Ctrl+C, collection Copy\n'
                                      'and the Copy paths action.')
        form.addRow('Paths:', self.path_notation)
        layout.addLayout(form)
        self.start_properties = QtWidgets.QCheckBox('Show Properties panel on start')
        self.start_properties.setChecked(values['start_properties'])
        self.start_collections = QtWidgets.QCheckBox('Show Collections panel on start')
        self.start_collections.setChecked(values['start_collections'])
        self.remember_category = QtWidgets.QCheckBox('Remember library and category')
        self.remember_category.setToolTip('Open TinyLib on the library or category you had selected last time.')
        self.remember_category.setChecked(values['remember_category'])
        self.tile_info = QtWidgets.QCheckBox('Show Info')
        self.tile_info.setToolTip('Tile captions in the Tiles view (the Info button).')
        self.tile_info.setChecked(values['tile_info'])
        for box in (self.start_properties, self.start_collections, self.remember_category, self.tile_info):
            layout.addWidget(box)
        layout.addWidget(QtWidgets.QLabel('Tile text:'))
        self.tile_template = QtWidgets.QPlainTextEdit(values['tile_template'])
        self.tile_template.setToolTip('Tokens: ' + ', '.join('{' + token + '}' for token in TOKENS) +
                                      '\nUse \\n or a new line. Up to six lines.')
        self.tile_template.setFixedHeight(90)
        layout.addWidget(self.tile_template)
        hint = QtWidgets.QLabel('Tokens: ' + ', '.join('{' + token + '}' for token in TOKENS))
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        reset = QtWidgets.QPushButton('Default tile text')
        reset.clicked.connect(lambda _=False: self.tile_template.setPlainText(DEFAULT_TEMPLATE))
        layout.addWidget(reset)
        self.message = QtWidgets.QLabel('')
        self.message.setStyleSheet('color: #ff6060;')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {
            'path_notation': self.path_notation.currentData(),
            'start_properties': self.start_properties.isChecked(),
            'start_collections': self.start_collections.isChecked(),
            'remember_category': self.remember_category.isChecked(),
            'tile_info': self.tile_info.isChecked(),
            'tile_template': self.tile_template.toPlainText().strip('\n'),
        }

    def accept(self):
        try:
            validate_template(self.values()['tile_template'])
        except ValueError as error:
            self.message.setText('Tile text: ' + str(error))
            return
        super().accept()
