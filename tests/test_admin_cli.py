import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from tinylib import admin
from tinylib.access import AccessControl
from tinylib.install import read_install
from tinylib.processing import apply_worker_settings


class AdminCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ini = self.root / 'tinylib.ini'
        self.ini.write_text('[tinylib]\nsecurity = pipe/security.json\n', encoding='utf-8')
        self.security = self.root / 'pipe' / 'security.json'

    def run_cli(self, *argv):
        output = io.StringIO()
        with redirect_stdout(output):
            code = admin.main(list(argv), install=read_install(self.ini))
        return code, output.getvalue()

    def test_first_admin_is_explicit_and_creates_file(self):
        self.assertFalse(self.security.exists())
        self.run_cli('add-user', 'STUDIO\\Jane')
        data = json.loads(self.security.read_text())
        self.assertEqual(data['access']['users'], {'studio\\jane': ['admins']})
        self.assertEqual(data['libraries'], [])
        access = AccessControl({'_security_path': str(self.security)}, identity='studio\\jane')
        self.assertTrue(access.is_admin)
        self.assertIsNone(access.error)

    def test_groups_validation_and_last_admin(self):
        self.run_cli('add-user', 'studio\\jane')
        self.run_cli('add-user', 'studio\\artist', 'users', 'restricted')
        with self.assertRaisesRegex(SystemExit, 'Unknown group'):
            self.run_cli('add-user', 'studio\\x', 'wizards')
        with self.assertRaisesRegex(SystemExit, 'At least one administrator'):
            self.run_cli('remove-user', 'studio\\jane')
        self.run_cli('remove-user', 'studio\\artist')
        code, output = self.run_cli('show')
        self.assertEqual(code, 0)
        self.assertIn('studio\\jane', output)
        self.assertNotIn('artist', output)

    def test_windows_denial_and_missing_install(self):
        with patch('tinylib.admin.atomic_json', side_effect=PermissionError(13, 'Access is denied')):
            with self.assertRaisesRegex(SystemExit, 'Windows denied writing'):
                self.run_cli('add-user', 'studio\\jane')
        self.ini.unlink()
        code, output = self.run_cli('show')
        self.assertEqual(code, 1)
        self.assertIn('is missing', output)


class WorkerSettingsTests(unittest.TestCase):
    def test_worker_uses_its_own_tools_and_profile(self):
        job = {'tools': {'ffmpeg': '//evil/ffmpeg.exe'}, 'profile': {'ocio_config': 'evil'},
               'profile_name': 'Rec709', 'media': {'thumb': {'mode': 'generate'}}}
        settings = {'tools': {'ffmpeg': 'C:/farm/ffmpeg.exe'}, 'profiles': {'Rec709': {'ocio_config': 'farm.ocio'}}}
        apply_worker_settings(job, settings)
        self.assertEqual(job['tools'], {'ffmpeg': 'C:/farm/ffmpeg.exe'})
        self.assertEqual(job['profile'], {'ocio_config': 'farm.ocio'})

    def test_unknown_profile_rejected_only_when_generating(self):
        job = {'tools': {}, 'profile': {}, 'profile_name': 'Missing', 'media': {'thumb': {'mode': 'generate'}}}
        with self.assertRaisesRegex(ValueError, 'not configured on this worker'):
            apply_worker_settings(job, {'tools': {}, 'profiles': {}})
        job['media'] = {'thumb': {'mode': 'supply'}}
        apply_worker_settings(job, {'tools': {}, 'profiles': {}})


if __name__ == '__main__':
    unittest.main()
