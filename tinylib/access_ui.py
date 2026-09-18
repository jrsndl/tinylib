"""Administrator controls for Windows-account group membership and library grants."""
import copy
from .qt import QtCore, QtWidgets


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
        layout.addWidget(QtWidgets.QLabel('Signed in as ' + access.identity + '\nAdmins always have every library permission.'))
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
        self.permissions.setHorizontalHeaderLabels(['Library', 'Permission', 'Action'] + self.groups)
        self.permissions.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.rows = []
        for library_index, library in enumerate(self.libraries):
            policy = library.get('permissions', {})
            rules = [('view', ''), ('ingest', '')]
            rules += [('action', identifier) for identifier in sorted(set(actions.actions) | set(policy.get('actions', {})))]
            for capability, identifier in rules:
                row = self.permissions.rowCount()
                self.permissions.insertRow(row)
                self.rows.append((library_index, capability, identifier))
                for column, text in enumerate([library['name'], capability, identifier]):
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    self.permissions.setItem(row, column, item)
                grants = policy.get('actions', {}).get(identifier, []) if identifier else policy.get(capability, [])
                for column, group in enumerate(self.groups, 3):
                    checkbox = QtWidgets.QCheckBox()
                    checkbox.setChecked(group == 'admins' or group in grants)
                    checkbox.setEnabled(group != 'admins')
                    self.permissions.setCellWidget(row, column, checkbox)
        tabs.addTab(self.permissions, 'Library permissions')
        layout.addWidget(tabs)
        self.message = QtWidgets.QLabel('Unknown accounts have no access until assigned to a group.')
        layout.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
                name = self.users.item(row, 0).text().strip().casefold()
                if name in users:
                    raise ValueError('Duplicate Windows account: ' + name)
                users[name] = [group for column, group in enumerate(self.groups, 1)
                               if self.users.cellWidget(row, column).isChecked()]
            for row, (library_index, capability, identifier) in enumerate(self.rows):
                grants = [group for column, group in enumerate(self.groups, 3)
                          if group != 'admins' and self.permissions.cellWidget(row, column).isChecked()]
                policy = self.libraries[library_index].setdefault('permissions', {})
                if identifier:
                    policy.setdefault('actions', {})[identifier] = grants
                else:
                    policy[capability] = grants
            self.access.save(users, self.groups, self.libraries)
            self.accept()
        except Exception as error:
            self.message.setText(str(error))
