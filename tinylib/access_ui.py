"""Administrator controls for Windows-account group membership and library grants."""
import copy
from .qt import QtCore, QtWidgets
from .access import CAPABILITIES


class AccessDialog(QtWidgets.QDialog):
    def __init__(self, access, actions, parent=None):
        super().__init__(parent)
        access.refresh()
        if not access.is_admin:
            raise PermissionError('Only admins can manage access.')
        self.access = access
        self.actions = actions
        self.groups = list(access.data['access']['groups'])
        self.libraries = copy.deepcopy(access.data.get('libraries', []))
        self.setWindowTitle('TinyLib — Studio access')
        self.resize(950, 700)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel('Signed in as ' + access.identity + '\nAdmins have every library permission; action allow_only restrictions still apply.'))
        tabs = QtWidgets.QTabWidget()
        users_panel = QtWidgets.QWidget()
        users_layout = QtWidgets.QVBoxLayout(users_panel)
        self.users = QtWidgets.QTableWidget(0, len(self.groups) + 1)
        self.users.setHorizontalHeaderLabels(['Windows account (DOMAIN\\login)'] + self.groups)
        self.users.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        users_layout.addWidget(self.users)
        for user, groups in access.data['access']['users'].items():
            self.add_user(user, groups)
        controls = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton('Add user')
        add.clicked.connect(lambda: self.add_user('', ['restricted']))
        controls.addWidget(add)
        remove = QtWidgets.QPushButton('Remove selected user')
        remove.clicked.connect(lambda: self.users.removeRow(self.users.currentRow()))
        controls.addWidget(remove)
        users_layout.addLayout(controls)
        tabs.addTab(users_panel, 'Users and groups')
        self.permissions = QtWidgets.QTableWidget()
        self.permissions.setColumnCount(3 + len(self.groups))
        self.permissions.setHorizontalHeaderLabels(['Library', 'Permission', 'Override defaults'] + self.groups)
        self.permissions.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.rows = []
        for library_index, library in [(-1, {'name': 'Studio defaults', 'permissions': access.defaults()})] + list(enumerate(self.libraries)):
            policy = library.get('permissions', {})
            rules = [(capability, '') for capability in CAPABILITIES]
            for capability, identifier in rules:
                row = self.permissions.rowCount()
                self.permissions.insertRow(row)
                self.rows.append((library_index, capability, identifier))
                for column, text in enumerate([library['name'], capability.replace('_', ' ')]):
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    self.permissions.setItem(row, column, item)
                override = QtWidgets.QCheckBox()
                override.setChecked(library_index == -1 or (capability in policy and isinstance(policy[capability], list)))
                override.setEnabled(library_index != -1)
                self.permissions.setCellWidget(row, 2, override)
                grants = policy.get(capability, access.defaults()[capability])
                if not isinstance(grants, list):
                    grants = access.defaults()[capability]
                for column, group in enumerate(self.groups, 3):
                    checkbox = QtWidgets.QCheckBox()
                    checkbox.setChecked(group == 'admins' or group in grants)
                    checkbox.setEnabled(group != 'admins')
                    self.permissions.setCellWidget(row, column, checkbox)
                    if library_index == -1:
                        checkbox.toggled.connect(lambda *_: self.sync_inherited())
                override.toggled.connect(lambda checked, r=row: self.update_grants(r, checked))
                self.enable_grants(row, override.isChecked())
        tabs.addTab(self.permissions, 'Library permissions')
        layout.addWidget(tabs)
        self.message = QtWidgets.QLabel('Unknown accounts have no access until assigned to a group.')
        layout.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def enable_grants(self, row, enabled):
        for column, group in enumerate(self.groups, 3):
            self.permissions.cellWidget(row, column).setEnabled(enabled and group != 'admins')

    def update_grants(self, row, enabled):
        self.enable_grants(row, enabled)
        self.sync_inherited()

    def sync_inherited(self):
        defaults = {capability: row for row, (index, capability, _) in enumerate(self.rows) if index == -1}
        for row, (index, capability, _) in enumerate(self.rows):
            if index == -1 or self.permissions.cellWidget(row, 2).isChecked():
                continue
            for column in range(3, 3 + len(self.groups)):
                checkbox = self.permissions.cellWidget(row, column)
                checkbox.blockSignals(True)
                checkbox.setChecked(self.permissions.cellWidget(defaults[capability], column).isChecked())
                checkbox.blockSignals(False)

    def add_user(self, name, memberships):
        row = self.users.rowCount()
        self.users.insertRow(row)
        self.users.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
        for column, group in enumerate(self.groups, 1):
            checkbox = QtWidgets.QCheckBox()
            checkbox.setChecked(group in memberships)
            self.users.setCellWidget(row, column, checkbox)

    def save(self):
        try:
            users = {}
            for row in range(self.users.rowCount()):
                item = self.users.item(row, 0)
                name = item.text().strip().casefold() if item else ''
                if not name:
                    raise ValueError('Row %d has no account name. Enter DOMAIN\\login or remove the row.' % (row + 1))
                if name in users:
                    raise ValueError('Duplicate Windows account: ' + name)
                users[name] = [group for column, group in enumerate(self.groups, 1)
                               if self.users.cellWidget(row, column).isChecked()]
            defaults = {}
            for library in self.libraries:
                if isinstance(library.get('permissions', {}).get('actions'), dict):
                    library['permissions'].pop('actions')
            for row, (library_index, capability, identifier) in enumerate(self.rows):
                grants = [group for column, group in enumerate(self.groups, 3)
                          if self.permissions.cellWidget(row, column).isChecked()]
                policy = defaults if library_index == -1 else self.libraries[library_index].setdefault('permissions', {})
                if self.permissions.cellWidget(row, 2).isChecked():
                    policy[capability] = grants
                else:
                    policy.pop(capability, None)
            me = self.access.identity.casefold()
            if 'admins' not in users.get(me, []):
                answer = QtWidgets.QMessageBox.question(
                    self, 'TinyLib', 'You are removing your own admin rights (%s). You will not be able to '
                    'change access rights afterwards. Continue?' % me)
                if answer != QtWidgets.QMessageBox.Yes:
                    return
            self.access.save(users, self.groups, self.libraries, defaults)
            self.accept()
        except Exception as error:
            self.message.setText(str(error))
