"""The Publish to AYON window: accounts, AYON folder and task picker, variants, publish log.

The sign-in dialogs follow Submission Helper (github.com/jrsndl/SubmissionHelper); the
folder/task tree follows the AYON panel of IngestDesktop (github.com/jrsndl/IngestDesktop).
"""
import datetime
import importlib.util
import sys
from pathlib import Path

from tinylib.qt import QtCore, QtGui, QtWidgets, exec_dialog


def sibling(name):
    """A module of this action folder, loaded once under a TinyLib-specific name."""
    key = '_tinylib_ayon_publish_' + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


cred = sibling('credentials')
pub = sibling('publish_csv')
query = sibling('ayon_query')

FOLDER_ROLE = QtCore.Qt.UserRole
TASK_ROLE = QtCore.Qt.UserRole + 1


class _Invoker(QtCore.QObject):
    """Runs a callable in the GUI thread (the ftrack browser sign-in calls back from its thread)."""
    call = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.call.connect(lambda function: function(), QtCore.Qt.QueuedConnection)


def busy(on):
    if on:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    else:
        QtWidgets.QApplication.restoreOverrideCursor()
    QtWidgets.QApplication.processEvents()


class _SignInDialog(QtWidgets.QDialog):
    """Common parts: server URL, message line, buttons."""

    def __init__(self, title, intro, url, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.main_layout = QtWidgets.QVBoxLayout(self)
        intro_label = QtWidgets.QLabel(intro)
        intro_label.setWordWrap(True)
        self.main_layout.addWidget(intro_label)
        self.form = QtWidgets.QFormLayout()
        self.main_layout.addLayout(self.form)
        self.url_edit = QtWidgets.QLineEdit(url or '')
        self.url_edit.setPlaceholderText('https://...')
        self.url_edit.setReadOnly(bool(url))  # A configured URL is not changed here.
        self.form.addRow('Server:', self.url_edit)
        self.message = QtWidgets.QLabel('')
        self.message.setWordWrap(True)
        self.main_layout.addWidget(self.message)
        self.buttons = QtWidgets.QDialogButtonBox()
        self.buttons.addButton(QtWidgets.QDialogButtonBox.Cancel).clicked.connect(self.reject)
        self.main_layout.addWidget(self.buttons)

    def url(self):
        return self.url_edit.text().strip().rstrip('/')

    def show_message(self, text, error=False):
        self.message.setStyleSheet('color: #ff6060;' if error else '')
        self.message.setText(text)


class AyonSignInDialog(_SignInDialog):
    """AYON user name and password; only the session token is stored."""

    def __init__(self, url, parent=None):
        super().__init__('Sign in to AYON',
                         'Your AYON session is stored in the Windows Credential Manager, only for your Windows '
                         'user. Your password is not stored: when the session expires, TinyLib asks for it again.',
                         url, parent)
        user, _ = cred.get_ayon(url) if url else (None, None)
        self.user_edit = QtWidgets.QLineEdit(user or cred.windows_user())
        self.password_edit = QtWidgets.QLineEdit()
        self.password_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.form.addRow('User name:', self.user_edit)
        self.form.addRow('Password:', self.password_edit)
        self.password_edit.setFocus()
        if user:
            self.show_message('Your AYON session has expired or was signed out: enter your password to sign in again.')
        button = self.buttons.addButton('Sign in', QtWidgets.QDialogButtonBox.AcceptRole)
        button.clicked.connect(self._login)
        button.setDefault(True)

    def _login(self):
        url, user = self.url(), self.user_edit.text().strip()
        busy(True)
        try:
            token, message = cred.ayon_login(url, user, self.password_edit.text())
        finally:
            busy(False)
        if token is None:
            self.show_message('Sign-in failed: ' + message, error=True)
            return
        if not cred.save_ayon(url, user, token):
            self.show_message('Signed in, but the session could not be stored in the Windows Credential Manager.',
                              error=True)
            return
        if not self.url_edit.isReadOnly():
            cred.save_server_url(cred.AYON, url)
        self.accept()


class FtrackSignInDialog(_SignInDialog):
    """The personal ftrack API key: from the ftrack web app in the browser, or typed in."""

    def __init__(self, url, parent=None):
        super().__init__('Sign in to ftrack',
                         'With ftrack, the AYON publish also creates the ftrack reviewable. TinyLib stores your '
                         'personal ftrack API key in the Windows Credential Manager, only for your Windows user.',
                         url, parent)
        self._thread = None
        self._invoker = _Invoker(self)
        browser = QtWidgets.QPushButton('Sign in with web browser')
        browser.setToolTip('Opens ftrack in your web browser. When you are signed in there (or sign in),\n'
                           'ftrack sends your personal API key back to TinyLib.')
        browser.clicked.connect(self._browser_login)
        self.main_layout.insertWidget(2, browser)
        user, _ = cred.get_ftrack(url) if url else (None, None)
        self.user_edit = QtWidgets.QLineEdit(user or '')
        self.key_edit = QtWidgets.QLineEdit()
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key_edit.setToolTip('ftrack web app: My Account > Security > Personal API key')
        manual = QtWidgets.QGroupBox('Or enter it yourself')
        form = QtWidgets.QFormLayout(manual)
        form.addRow('User name:', self.user_edit)
        form.addRow('API key:', self.key_edit)
        self.main_layout.insertWidget(3, manual)
        button = self.buttons.addButton('Sign in', QtWidgets.QDialogButtonBox.AcceptRole)
        button.clicked.connect(lambda: self._finish(self.user_edit.text().strip(), self.key_edit.text().strip()))
        button.setDefault(True)

    def _browser_login(self):
        url = self.url()
        if not url:
            self.show_message('Enter the ftrack server first.', error=True)
            return
        self._stop_thread()
        self.show_message('Waiting for the web browser... finish the sign-in there.')

        def received(api_user, api_key):
            self._invoker.call.emit(lambda: self._finish(api_user, api_key))

        self._thread = cred.FtrackBrowserLogin(url, received)
        self._thread.start()

    def _finish(self, user, key):
        url = self.url()
        busy(True)
        try:
            ok, message = cred.check_ftrack(url, user, key)
        finally:
            busy(False)
        if not ok:
            self.show_message('Sign-in failed: ' + message, error=True)
            return
        if not cred.save_ftrack(url, user, key):
            self.show_message('The key could not be stored in the Windows Credential Manager.', error=True)
            return
        if not self.url_edit.isReadOnly():
            cred.save_server_url(cred.FTRACK, url)
        self.accept()

    def _stop_thread(self):
        if self._thread is not None:
            self._thread.stop()
            self._thread = None

    def done(self, result):
        self._stop_thread()
        super().done(result)


class PublishDialog(QtWidgets.QDialog):
    def __init__(self, assets, config, settings=None, preferences=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Publish to AYON')
        self.resize(960, 880)
        self.assets = assets
        self.opts = pub.options(config)
        self.settings = settings or {}
        self.preferences = preferences
        self.credentials = cred.resolve(self.opts)
        self.connection = None
        self.process = None
        self.published = None
        self.queue, self.results, self.current = [], [], None
        self._loaded_project = None
        remembered = (getattr(preferences, 'data', {}) or {}).get('display', {}).get('ayon_publish', {})
        env = QtCore.QProcessEnvironment.systemEnvironment()
        # The host's AYON context (Nuke started from AYON) comes first, then the last publish.
        self.wanted = {
            'project': env.value('AYON_PROJECT_NAME', '') or remembered.get('project', ''),
            'folder': env.value('AYON_FOLDER_PATH', '') or remembered.get('folder', ''),
            'task': env.value('AYON_TASK_NAME', '') or remembered.get('task', ''),
        }

        layout = QtWidgets.QVBoxLayout(self)
        accounts = QtWidgets.QGroupBox('Accounts')
        grid = QtWidgets.QGridLayout(accounts)
        self.account_rows = {}
        for row, (kind, title) in enumerate(((cred.AYON, 'AYON'), (cred.FTRACK, 'ftrack'))):
            status = QtWidgets.QLabel('')
            sign_in = QtWidgets.QPushButton('Sign in…')
            sign_out = QtWidgets.QPushButton('Sign out')
            sign_in.clicked.connect(lambda _=False, k=kind: self.sign_in(k))
            sign_out.clicked.connect(lambda _=False, k=kind: self.sign_out(k))
            grid.addWidget(QtWidgets.QLabel(title + ':'), row, 0)
            grid.addWidget(status, row, 1)
            grid.addWidget(sign_in, row, 2)
            grid.addWidget(sign_out, row, 3)
            self.account_rows[kind] = (status, sign_in, sign_out)
        grid.setColumnStretch(1, 1)
        layout.addWidget(accounts)

        target = QtWidgets.QGroupBox('Publish into')
        target_layout = QtWidgets.QVBoxLayout(target)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('Project:'))
        self.project = QtWidgets.QComboBox()
        self.project.setMinimumWidth(220)
        self.project.currentTextChanged.connect(self.load_project)
        row.addWidget(self.project, 1)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Search folders and tasks…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_search)
        row.addWidget(self.search, 1)
        reload_button = QtWidgets.QPushButton('Reload')
        reload_button.clicked.connect(lambda _=False: self.load_project(self.project.currentText(), force=True))
        row.addWidget(reload_button)
        target_layout.addLayout(row)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(['Folder / task', 'Type'])
        self.tree.setColumnWidth(0, 420)
        self.tree.currentItemChanged.connect(self.update_target)
        target_layout.addWidget(self.tree, 1)
        self.target_label = QtWidgets.QLabel('Pick a task.')
        target_layout.addWidget(self.target_label)
        layout.addWidget(target, 3)

        parts = QtWidgets.QGroupBox('Components')
        parts_layout = QtWidgets.QVBoxLayout(parts)
        self.parts = QtWidgets.QTableWidget(len(self.opts['components']), 5)
        self.parts.setHorizontalHeaderLabels(['On', 'Component', 'Representation', 'Tags', 'Colorspace'])
        self.parts.verticalHeader().hide()
        self.parts.horizontalHeader().setStretchLastSection(True)
        self.parts.setToolTip('One representation per enabled component, for every asset that has it.\n'
                              'Representation: its name in AYON; {extension} is the file extension.\n'
                              'Tags: separated by ;. ftrackreview is left out without ftrack credentials.\n'
                              'Colorspace: {colorspace} is the asset\'s own colour space.')
        for index, part in enumerate(self.opts['components']):
            enabled = QtWidgets.QTableWidgetItem('')
            enabled.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            enabled.setCheckState(QtCore.Qt.Checked if part['enabled'] else QtCore.Qt.Unchecked)
            name = QtWidgets.QTableWidgetItem(part['component'])
            name.setFlags(name.flags() & ~QtCore.Qt.ItemIsEditable)
            self.parts.setItem(index, 0, enabled)
            self.parts.setItem(index, 1, name)
            for column, key in enumerate(('representation', 'tags', 'colorspace'), 2):
                self.parts.setItem(index, column, QtWidgets.QTableWidgetItem(part[key]))
        self.parts.resizeColumnsToContents()
        self.parts.setColumnWidth(3, max(self.parts.columnWidth(3), 240))
        self.parts.setFixedHeight(self.parts.horizontalHeader().height() + 3 * self.parts.rowHeight(0) + 6)
        parts_layout.addWidget(self.parts)
        layout.addWidget(parts)

        items = QtWidgets.QGroupBox('Assets')
        items_layout = QtWidgets.QVBoxLayout(items)
        self.table = QtWidgets.QTableWidget(len(assets), 5)
        self.table.setHorizontalHeaderLabels(['Asset', 'Product type', 'Variant', 'Version', 'Comment'])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, asset in enumerate(assets):
            name = QtWidgets.QTableWidgetItem(asset.get('label') or asset.get('name', ''))
            name.setFlags(name.flags() & ~QtCore.Qt.ItemIsEditable)
            name.setToolTip(str(asset.get('main', '')))
            kind = QtWidgets.QTableWidgetItem(pub.product_type(asset))
            kind.setFlags(kind.flags() & ~QtCore.Qt.ItemIsEditable)
            variant = QtWidgets.QTableWidgetItem(pub.default_variant(asset))
            variant.setToolTip('The product name is the product type and this variant, e.g. renderSmoke_puff_01.')
            version = QtWidgets.QTableWidgetItem(str(pub.default_version(asset)))
            version.setToolTip('The version number; empty publishes the next version.')
            comment = QtWidgets.QTableWidgetItem(pub.default_comment(asset, self.opts))
            for column, item in enumerate((name, kind, variant, version, comment)):
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        items_layout.addWidget(self.table)
        layout.addWidget(items, 2)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        self.log.hide()
        layout.addWidget(self.log, 3)
        options = QtWidgets.QHBoxLayout()
        self.deadline_box = QtWidgets.QCheckBox('Deadline Publish')
        self.deadline_box.setToolTip('Send the publish to the Deadline farm as command-line jobs instead of\n'
                                     'running it on this computer. The CSV files go to a folder the farm can read.')
        self.deadline_box.setChecked(bool(remembered.get('deadline', False)))
        self.separate_box = QtWidgets.QCheckBox('Each Asset Separate')
        self.separate_box.setToolTip('One CSV file and one publish (or Deadline job) per asset, instead of one for\n'
                                     'all: a failing asset does not stop the others.')
        self.separate_box.setChecked(bool(remembered.get('separate', False)))
        options.addWidget(self.deadline_box)
        options.addWidget(self.separate_box)
        options.addStretch()
        layout.addLayout(options)
        buttons = QtWidgets.QHBoxLayout()
        self.message = QtWidgets.QLabel('')
        self.message.setWordWrap(True)
        buttons.addWidget(self.message, 1)
        self.publish_button = QtWidgets.QPushButton('Publish')
        self.publish_button.setDefault(True)
        self.publish_button.clicked.connect(self.publish)
        buttons.addWidget(self.publish_button)
        self.stop_button = QtWidgets.QPushButton('Stop')
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.hide()
        buttons.addWidget(self.stop_button)
        self.close_button = QtWidgets.QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.refresh_accounts()
        QtCore.QTimer.singleShot(0, self.connect_ayon)

    # ------------------------------------------------------------ accounts

    def refresh_accounts(self):
        c = self.credentials
        for kind, (status, sign_in, sign_out) in self.account_rows.items():
            source = c['sources'].get(kind)
            url = c['ayon_url'] if kind == cred.AYON else c['ftrack_url']
            user = c['ayon_user'] if kind == cred.AYON else c['ftrack_user']
            host = cred.hostname(url) or 'no server set'
            if source:
                status.setText('%s @ %s (from the %s)' % (user or 'signed in', host, source))
            elif kind == cred.FTRACK:
                status.setText('not signed in (%s): the publish makes no ftrack reviewable' % host)
            else:
                status.setText('not signed in (%s)' % host)
            # Credentials from the host's environment are the host's; nothing to sign in or out of here.
            sign_in.setVisible(source != cred.SOURCE_ENV)
            sign_out.setVisible(source == cred.SOURCE_KEYRING)

    def sign_in(self, kind):
        url = self.credentials['ayon_url'] if kind == cred.AYON else self.credentials['ftrack_url']
        if not cred.keyring_available():
            self.show_message('The Windows Credential Manager is not available (keyring module missing), '
                              'so TinyLib cannot keep a sign-in.', error=True)
            return
        dialog = AyonSignInDialog(url, self) if kind == cred.AYON else FtrackSignInDialog(url, self)
        if exec_dialog(dialog):
            self.credentials = cred.resolve(self.opts)
            self.refresh_accounts()
            if kind == cred.AYON:
                self.connection = None
                self.connect_ayon()

    def sign_out(self, kind):
        if kind == cred.AYON:
            cred.ayon_logout(self.credentials['ayon_url'])
            self.connection = None
            self._loaded_project = None
            self.project.clear()
            self.tree.clear()
            self.update_target()
        else:
            cred.clear_ftrack(self.credentials['ftrack_url'])
        self.credentials = cred.resolve(self.opts)
        self.refresh_accounts()

    def connect_ayon(self):
        try:
            import ayon_api  # noqa: F401
        except ImportError:
            self.show_message('The ayon_api Python module is not available here. Start the host from AYON, '
                              'or install TinyLib with the "ayon" extra.', error=True)
            return
        busy(True)
        try:
            ok = cred.ensure_ayon(self.credentials)
        finally:
            busy(False)
        self.refresh_accounts()
        if not ok:
            if not self.credentials['ayon_url']:
                self.show_message('Sign in to AYON: enter the AYON server address in the sign-in window, '
                                  'or set ayon_server_url in the action config.json.', error=True)
            else:
                self.show_message('Sign in to AYON to pick the folder and task.', error=True)
            return
        busy(True)
        try:
            self.connection = query.ayon_connection(self.credentials['ayon_url'], self.credentials['ayon_key'])
            projects = sorted(self.connection.get_project_names())
        except Exception as error:
            self.connection = None
            self.show_message('Cannot connect to AYON at %s: %s' % (self.credentials['ayon_url'], error), error=True)
            return
        finally:
            busy(False)
        self.show_message('')
        self.project.blockSignals(True)
        self.project.clear()
        self.project.addItems(projects)
        self.project.blockSignals(False)
        index = self.project.findText(self.wanted['project'])
        self.project.setCurrentIndex(index if index >= 0 else 0)
        self.load_project(self.project.currentText())

    # ------------------------------------------------------------ folders and tasks

    def load_project(self, project, force=False):
        if not project or self.connection is None or (project == self._loaded_project and not force):
            return
        busy(True)
        try:
            roots = query.folder_tree(self.connection, project)
        except Exception as error:
            self.show_message('Cannot read the folders of %s: %s' % (project, error), error=True)
            return
        finally:
            busy(False)
        self._loaded_project = project
        self.tree.clear()
        for folder in sorted(roots, key=lambda f: (f.get('label') or f['name']).casefold()):
            self._add_folder(folder, self.tree.invisibleRootItem())
        self.tree.expandToDepth(0)
        if project == self.wanted['project']:
            self.select(self.wanted['folder'], self.wanted['task'])
        self.apply_search(self.search.text())
        self.update_target()

    def _add_folder(self, folder, parent):
        item = QtWidgets.QTreeWidgetItem(parent, [folder.get('label') or folder['name'],
                                                  folder.get('folderType') or folder.get('type') or ''])
        item.setData(0, FOLDER_ROLE, folder['path'])
        for task in sorted(folder['tasks'], key=lambda t: (t.get('label') or t['name']).casefold()):
            child = QtWidgets.QTreeWidgetItem(item, ['• ' + (task.get('label') or task['name']),
                                                     task.get('taskType') or task.get('type') or ''])
            child.setData(0, FOLDER_ROLE, folder['path'])
            child.setData(0, TASK_ROLE, task['name'])
            child.setForeground(0, QtGui.QBrush(QtGui.QColor('#a6e22e')))
        for sub in sorted(folder['children'], key=lambda f: (f.get('label') or f['name']).casefold()):
            self._add_folder(sub, item)

    def _items(self, parent=None):
        parent = self.tree.invisibleRootItem() if parent is None else parent
        for index in range(parent.childCount()):
            child = parent.child(index)
            yield child
            for item in self._items(child):
                yield item

    def select(self, folder_path, task):
        if not folder_path:
            return
        fallback = None
        for item in self._items():
            if item.data(0, FOLDER_ROLE) != folder_path:
                continue
            if item.data(0, TASK_ROLE) is None:
                fallback = fallback or item
            elif not task or item.data(0, TASK_ROLE) == task:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return
        if fallback is not None:
            fallback.setExpanded(True)
            self.tree.setCurrentItem(fallback)
            self.tree.scrollToItem(fallback)

    def apply_search(self, text):
        """Show matching folders and tasks, their parents, and everything inside a matching folder."""
        words = text.casefold().split()

        def visit(item, parent_matches):
            own = all(word in (item.text(0) + ' ' + (item.data(0, FOLDER_ROLE) or '')).casefold() for word in words)
            matches = parent_matches or own
            shown = matches
            for index in range(item.childCount()):
                shown = visit(item.child(index), matches) or shown
            item.setHidden(not shown)
            if words and shown and not matches:
                item.setExpanded(True)
            return shown

        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            visit(root.child(index), not words)

    def target(self):
        item = self.tree.currentItem()
        if item is None or item.data(0, TASK_ROLE) is None:
            return None
        return self.project.currentText(), item.data(0, FOLDER_ROLE), item.data(0, TASK_ROLE)

    def update_target(self, *_):
        target = self.target()
        self.target_label.setText('Publishing into %s  %s  task %s' % target if target else 'Pick a task.')

    # ------------------------------------------------------------ publish

    def show_message(self, text, error=False):
        self.message.setStyleSheet('color: #ff6060;' if error else '')
        self.message.setText(text)

    def components(self):
        parts = []
        for index in range(self.parts.rowCount()):
            parts.append({'enabled': self.parts.item(index, 0).checkState() == QtCore.Qt.Checked,
                          'component': self.parts.item(index, 1).text(),
                          'representation': self.parts.item(index, 2).text().strip(),
                          'tags': self.parts.item(index, 3).text().strip(),
                          'colorspace': self.parts.item(index, 4).text().strip()})
        return pub.components(parts)

    def asset_rows(self, folder_path, task):
        """[(asset, its CSV rows)] from the Components and Assets tables."""
        parts = self.components()
        if not any(part['enabled'] for part in parts):
            raise ValueError('Enable at least one component.')
        items = []
        for index, asset in enumerate(self.assets):
            items.append((asset, self.table.item(index, 2).text().strip(), self.table.item(index, 3).text().strip(),
                          self.table.item(index, 4).text()))
        pub.check_variants([(asset, variant) for asset, variant, _, _ in items])
        ftrack = cred.has_ftrack(self.credentials)
        return [(asset, pub.asset_rows(asset, folder_path, task, variant, self.opts, version, comment, ftrack, parts))
                for asset, variant, version, comment in items]

    def publish(self):
        target = self.target()
        if self.connection is None or not self.credentials.get('ayon_key'):
            self.show_message('Sign in to AYON first.', error=True)
            return
        if target is None:
            self.show_message('Pick the task to publish into.', error=True)
            return
        project, folder_path, task = target
        deadline, separate = self.deadline_box.isChecked(), self.separate_box.isChecked()
        try:
            batches = pub.batches(self.asset_rows(folder_path, task), separate)
            executable = pub.find_executable(self.opts, self.settings)
            folder = pub.deadline_folder(self.opts, self.settings) if deadline else pub.log_folder(self.opts)
            now = datetime.datetime.now()
            jobs = []
            for index, (label, rows) in enumerate(batches, 1):
                suffix = '%02d_%s' % (index, label) if separate else ''
                csv_file = pub.write_csv(rows, pub.csv_path(self.opts, now, folder=folder, suffix=suffix))
                count = len({row['Variant'] + row['Product Type'] for row in rows})
                jobs.append((label, count, csv_file, pub.command(executable, csv_file, project, folder_path, task, self.opts)))
        except Exception as error:
            self.show_message(str(error), error=True)
            return
        if self.preferences is not None:
            try:
                self.preferences.set_display(ayon_publish={'project': project, 'folder': folder_path, 'task': task,
                                                           'deadline': deadline, 'separate': separate})
            except Exception:
                pass  # Remembering the target is a convenience only.
        self._target = target
        self.log.clear()
        self.log.show()
        if deadline:
            self.submit_deadline(jobs)
            return
        self.queue, self.results = list(jobs), []
        self.set_running(True)
        self.run_next()

    # ------------------------------------------------------------ Deadline

    def submit_deadline(self, jobs):
        from tinylib.ingest import DeadlineSubmissionError, submit_deadline_jobs
        project, folder_path, task = self._target
        environment = pub.deadline_environment(self.credentials, self.opts)
        prepared = []
        for label, _, csv_file, args in jobs:
            name = 'AYON publish: %s %s / %s - %s' % (project, folder_path, task, label)
            job_info, plugin_info = pub.deadline_job(name, args, environment, self.opts, self.settings)
            prepared.append((csv_file.stem, job_info, plugin_info))
            self.log.appendPlainText('CSV: %s\n%s %s' % (csv_file, plugin_info['Executable'], plugin_info['Arguments']))

        def progress(message):
            self.show_message(message)
            QtWidgets.QApplication.processEvents()

        self.set_running(True)
        self.stop_button.hide()  # A submission cannot be stopped half way.
        busy(True)
        try:
            identifiers = submit_deadline_jobs(prepared, self.settings, jobs[0][2].parent, progress)
        except Exception as error:
            known = getattr(error, 'job_ids', None) if isinstance(error, DeadlineSubmissionError) else None
            self.log.appendPlainText(str(error))
            self.show_message('Deadline submission failed%s: %s' % (
                ' (job IDs reported: %s)' % ', '.join(known) if known else '', str(error).splitlines()[0]), error=True)
            return
        finally:
            busy(False)
            self.set_running(False)
            self.save_log(jobs)
        for (label, _, _, _), identifier in zip(jobs, identifiers):
            self.log.appendPlainText('Deadline job %s: %s' % (identifier, label))
        self.save_log(jobs)
        self.published = 'Submitted %d AYON publish job(s) to Deadline for %d asset(s): %s %s / %s.' % (
            len(identifiers), sum(job[1] for job in jobs), project, folder_path, task)
        self.show_message(self.published)
        self.publish_button.setText('Publish again')

    # ------------------------------------------------------------ local publish

    def run_next(self):
        if not self.queue:
            self.finish_local()
            return
        label, count, csv_file, args = self.current = self.queue.pop(0)
        environment = QtCore.QProcessEnvironment()
        for key, value in pub.environment(self.credentials, self.opts).items():
            environment.insert(key, value)
        self.log.appendPlainText('\n=== %s\nCSV: %s\n%s\n' % (label, csv_file, pub.redact(args)))
        self.process = QtCore.QProcess(self)
        self.process.setProcessEnvironment(environment)
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.setWorkingDirectory(str(csv_file.parent))
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        done = len(self.results)
        self.show_message('Publishing %s (%d of %d)…' % (label, done + 1, done + 1 + len(self.queue)))
        self.process.start(args[0], args[1:])

    def set_running(self, running):
        for widget in (self.publish_button, self.close_button, self.tree, self.project, self.parts, self.table,
                       self.deadline_box, self.separate_box):
            widget.setEnabled(not running)
        self.stop_button.setVisible(running)

    def read_output(self):
        data = bytes(self.process.readAllStandardOutput()).decode('utf-8', 'replace')
        for line in data.splitlines():
            if line.strip():
                self.log.appendPlainText(line.rstrip())

    def process_error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self.log.appendPlainText('The AYON launcher did not start: ' + self.process.errorString())
            self.process_finished(-1, None)

    def process_finished(self, code, status=None):
        if self.process is None:
            return
        self.read_output()
        process, self.process = self.process, None
        crashed = status is not None and status == QtCore.QProcess.CrashExit
        ok = code == 0 and not crashed
        if not ok:
            self.log.appendPlainText('Failed (exit code %s).' % code)
        self.results.append((self.current, ok))
        process.deleteLater()
        self.run_next()

    def save_log(self, jobs):
        for job in jobs:
            try:
                job[2].with_suffix('.log').write_text(self.log.toPlainText(), encoding='utf-8')
            except OSError:
                pass

    def finish_local(self):
        self.set_running(False)
        self.save_log([job for job, _ in self.results])
        project, folder_path, task = self._target
        done = [job for job, ok in self.results if ok]
        failed = [job for job, ok in self.results if not ok]
        if done:
            self.published = 'Published %d asset(s) to AYON: %s %s / %s.' % (
                sum(job[1] for job in done), project, folder_path, task)
            self.publish_button.setText('Publish again')
        if failed:
            self.show_message('%s failed: %s. See the log above; it is also saved beside each CSV as .log.' % (
                'The publish' if len(self.results) == 1 else '%d of %d publishes' % (len(failed), len(self.results)),
                ', '.join(job[0] for job in failed)), error=True)
        else:
            self.show_message(self.published)

    def stop(self):
        if self.process is not None:
            self.log.appendPlainText('Stopped by the user.')
            self.queue = []
            self.process.kill()

    def reject(self):
        if self.process is not None:
            return  # Stop the publish first.
        super().reject()


def run_dialog(assets, config, settings=None, preferences=None, parent=None):
    dialog = PublishDialog(assets, config, settings, preferences, parent)
    exec_dialog(dialog)
    return dialog.published
