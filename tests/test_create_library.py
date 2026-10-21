import copy
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.access import AccessControl
from tinylib.library import atomic_json, read_json
from tinylib.library_tools import create_new_library
from tinylib.settings import load_settings


class CreateLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.config = self.folder / 'studio.json'
        self.existing = self.folder / 'old'
        self.existing.mkdir()
        document = {'libraries': [{'name': 'Existing', 'root': str(self.existing), 'read_only': True,
                                  'permissions': {'edit': ['admins']}, 'custom_option': {'keep': 3}}],
                    'profiles': {'Keep profile': {}},
                    'access': {'groups': ['admins', 'users', 'managers', 'restricted'],
                               'users': {'admin': ['admins'], 'user': ['users']},
                               'defaults': {'edit': ['managers']}}}
        atomic_json(self.config, document)
        self.load(self.config)
        self.root = self.folder / 'new'

    def load(self, security):
        install = self.folder / 'tinylib.ini'
        install.write_text('[tinylib]\nsecurity = %s\n' % security.name, encoding='utf-8')
        self.settings = load_settings(self.config, install=install)
        self.access = AccessControl(self.settings, identity='admin')

    def create(self, **kwargs):
        return create_new_library(self.settings, self.access, 'New', str(self.root), **kwargs)

    def test_defaults_create_empty_json_register_and_inherit_permissions(self):
        new = self.create()
        self.assertEqual(read_json(self.root / 'tinylib_data.json'),
                         {'schema_version': 3, 'asset_type_schema_version': 1, 'assets': []})
        self.assertEqual(new['permissions'], {})
        self.assertFalse(new['read_only'])
        saved = read_json(self.config)
        self.assertEqual(saved['libraries'][-1], new)
        self.assertIn('Keep profile', saved['profiles'])
        self.assertEqual(self.access.policy(self.root), {})
        self.assertFalse(list(self.folder.rglob('.tinylib-write.lock')))

    def test_copy_existing_settings_excludes_identity_and_can_override_read_only(self):
        new = self.create(template_root=str(self.existing), read_only=False)
        self.assertEqual(new['permissions'], {'edit': ['admins']})
        self.assertEqual(new['custom_option'], {'keep': 3})
        self.assertFalse(new['read_only'])
        self.assertEqual(new['name'], 'New')
        self.assertEqual(new['root'], str(self.root))

    def test_studio_library_defaults(self):
        data = read_json(self.config)
        data['library_defaults'] = {'read_only': True, 'permissions': {'ingest': ['admins']}, 'custom': 7}
        atomic_json(self.config, data)
        new = self.create()
        self.assertTrue(new['read_only'])
        self.assertEqual(new['custom'], 7)

    def test_existing_database_is_never_overwritten(self):
        self.root.mkdir()
        database = self.root / 'tinylib_data.json'
        database.write_text('Existing data', encoding='utf-8')
        before = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'already contains'):
            self.create()
        self.assertEqual(database.read_text(), 'Existing data')
        self.assertEqual(self.config.read_bytes(), before)

    def test_duplicate_name_and_root_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'name already exists'):
            create_new_library(self.settings, self.access, 'existing', self.root)
        with self.assertRaisesRegex(ValueError, 'root is already registered'):
            create_new_library(self.settings, self.access, 'Other', self.existing)
        self.assertFalse((self.root / 'tinylib_data.json').exists())

    def test_non_admin_cannot_create(self):
        access = AccessControl(self.settings, identity='user')
        with self.assertRaises(PermissionError):
            create_new_library(self.settings, access, 'Denied', self.root)
        self.assertFalse(self.root.exists())

    def test_config_failure_removes_only_new_database(self):
        before = self.config.read_bytes()
        self.root.mkdir()
        keep = self.root / 'keep.txt'
        keep.write_text('Keep me')
        def write(path, value):
            if Path(path) == self.config:
                raise OSError('Config unavailable')
            return atomic_json(path, value)
        with patch('tinylib.library.atomic_json', side_effect=write):
            with self.assertRaisesRegex(OSError, 'Config unavailable'):
                self.create()
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.root / 'tinylib_data.json').exists())
        self.assertEqual(keep.read_text(), 'Keep me')

    def test_separate_security_config_is_updated_and_preserved(self):
        security = self.folder / 'security.json'
        data = read_json(self.config)
        atomic_json(security, data)
        self.load(security)
        new = self.create()
        self.assertEqual(read_json(security)['libraries'][-1], new)
        self.assertEqual(read_json(self.config)['libraries'][-1], new)
        self.assertTrue(self.access.can(self.root, 'view'))

    def test_security_write_failure_rolls_back_studio_registration(self):
        security = self.folder / 'security.json'
        data = read_json(self.config)
        atomic_json(security, data)
        self.load(security)
        before = copy.deepcopy(data)
        def write(path, value):
            if Path(path) == security:
                raise OSError('Security config unavailable')
            return atomic_json(path, value)
        with patch('tinylib.library.atomic_json', side_effect=write):
            with self.assertRaisesRegex(OSError, 'Security config unavailable'):
                self.create()
        self.assertEqual(read_json(self.config), before)
        self.assertFalse((self.root / 'tinylib_data.json').exists())
        self.assertFalse(list(self.folder.rglob('.tinylib-write.lock')))

    def test_dialog_creates_from_selected_template(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from tinylib.qt import QtWidgets
        from tinylib.library_tools_ui import CreateLibraryDialog
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        dialog = CreateLibraryDialog(self.settings, self.access)
        dialog.name.setText('New')
        dialog.root.setText(str(self.root))
        dialog.template.setCurrentIndex(1)
        self.assertTrue(dialog.read_only.isChecked())
        dialog.read_only.setChecked(False)
        dialog.show()
        dialog.create_library()
        deadline = time.monotonic() + 3
        while dialog.isVisible() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        self.assertTrue(dialog.worker.wait(1000))
        app.processEvents()
        self.assertEqual(dialog.created_library['custom_option'], {'keep': 3})
        self.assertFalse(dialog.created_library['read_only'])
        self.assertFalse(dialog.isVisible())
