import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.access import AccessControl, DEFAULT_GROUPS
from tinylib.install import read_install
from tinylib.library import atomic_json
from tinylib.settings import load_settings


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ini = self.root / 'tinylib.ini'
        self.security = self.root / 'security.json'
        self.config = self.root / 'studio.json'
        self.library = self.root / 'library'
        atomic_json(self.security, {'libraries': [{'name': 'Lib', 'root': 'library'}],
                                    'access': {'groups': list(DEFAULT_GROUPS),
                                               'users': {'studio\\boss': ['admins']}}})
        atomic_json(self.config, {'libraries': [{'name': 'Lib', 'root': 'library'}]})

    def write_ini(self, text):
        self.ini.write_text(text, encoding='utf-8')

    def test_missing_ini_reports_error(self):
        install = read_install(self.ini)
        self.assertIsNone(install['security'])
        self.assertIn('is missing', install['error'])

    def test_missing_section_and_key(self):
        self.write_ini('[other]\nsecurity = x.json\n')
        self.assertIn('[tinylib]', read_install(self.ini)['error'])
        self.write_ini('[tinylib]\nconfig = studio.json\n')
        self.assertIn('"security"', read_install(self.ini)['error'])

    def test_relative_unc_and_unexpanded_paths(self):
        self.write_ini('[tinylib]\nsecurity = security.json\nconfig = studio.json\n')
        install = read_install(self.ini)
        self.assertIsNone(install['error'])
        self.assertEqual(Path(install['security']), self.root / 'security.json')
        self.assertEqual(Path(install['config']), self.root / 'studio.json')
        self.write_ini('[tinylib]\nsecurity = //studio/pipe/security.json\n')
        self.assertEqual(Path(read_install(self.ini)['security']), Path('//studio/pipe/security.json'))
        self.write_ini('[tinylib]\nsecurity = %USERPROFILE%/evil.json\n')
        self.assertIn('%USERPROFILE%', read_install(self.ini)['security'])

    def test_config_overrides_cannot_redirect_security(self):
        self.write_ini('[tinylib]\nsecurity = security.json\nconfig = studio.json\n')
        evil = self.root / 'evil.json'
        atomic_json(evil, {'access': {'users': {'studio\\artist': ['admins']}}})
        atomic_json(self.root / 'studio.local.json', {'libraries': [], 'security_config': str(evil)})
        atomic_json(self.root / 'other.json', {'libraries': [], 'security_config': str(evil)})
        with patch.dict(os.environ, {'TINYLIB_CONFIG': str(self.root / 'other.json')}):
            for settings in (load_settings(install=self.ini), load_settings(self.config, install=self.ini)):
                self.assertEqual(Path(settings['_security_path']), self.security)
                self.assertNotIn('security_config', settings)
                self.assertFalse(AccessControl(settings, identity='studio\\artist').is_admin)
            # TINYLIB_CONFIG still chooses the browser configuration, just not the security file.
            self.assertEqual(Path(load_settings(install=self.ini)['_config_path']).name, 'other.json')
        self.assertEqual(Path(load_settings(install=self.ini)['_config_path']).name, 'studio.local.json')

    def test_ini_config_is_default_browser_config(self):
        self.write_ini('[tinylib]\nsecurity = security.json\nconfig = studio.json\n')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TINYLIB_CONFIG', None)
            settings = load_settings(install=self.ini)
        self.assertEqual(Path(settings['_config_path']), self.config.resolve())
        access = AccessControl(settings, identity='STUDIO\\Boss')
        self.assertTrue(access.is_admin)
        self.assertIsNotNone(access.library(self.library))

    def test_app_placeholder_for_actions_and_tools(self):
        from tinylib.install import APP_ROOT
        atomic_json(self.config, {'libraries': [], 'action_roots': ['{app}/actions', 'more', '//share/actions'],
                                  'tools': {'ffmpeg': '{app}\\executables\\ffmpeg.exe', 'oiiotool': 'oiiotool',
                                            'local': '..\\bin\\tool.exe'}})
        settings = load_settings(self.config, install=self.ini)
        self.assertEqual(Path(settings['action_roots'][0]), (APP_ROOT / 'actions').resolve())
        self.assertEqual(Path(settings['action_roots'][1]), (self.root / 'more').resolve())
        self.assertEqual(settings['action_roots'][2], '//share/actions')
        self.assertEqual(Path(settings['tools']['ffmpeg']), (APP_ROOT / 'executables' / 'ffmpeg.exe').resolve())
        self.assertEqual(settings['tools']['oiiotool'], 'oiiotool')
        self.assertEqual(Path(settings['tools']['local']).name, 'tool.exe')
        atomic_json(self.config, {'libraries': []})
        self.assertEqual(Path(load_settings(self.config, install=self.ini)['action_roots'][0]),
                         (APP_ROOT / 'actions').resolve())

    def test_ini_tools_override_browser_config(self):
        self.write_ini('[tinylib]\nsecurity = security.json\n\n[tools]\n'
                       'ffmpeg = C:/tools/ffmpeg/ffmpeg.exe\n'
                       'ffprobe = //studio/tools/ffprobe.exe\n'
                       'oiiotool = oiiotool\n'
                       'vfx_transcode = bin\\vfx-transcode.exe\n'
                       'ocio = ocio://studio-config-latest\n'
                       'ffplay =\n')
        atomic_json(self.config, {'libraries': [], 'tools': {'ffmpeg': 'old-ffmpeg', 'ffplay': 'config-ffplay'}})
        tools = load_settings(self.config, install=self.ini)['tools']
        self.assertEqual(tools['ffmpeg'], 'C:/tools/ffmpeg/ffmpeg.exe')
        self.assertEqual(tools['ffprobe'], '//studio/tools/ffprobe.exe')
        self.assertEqual(tools['oiiotool'], 'oiiotool')  # Bare name: found on PATH.
        self.assertEqual(Path(tools['vfx_transcode']), (self.root / 'bin' / 'vfx-transcode.exe').resolve())
        self.assertEqual(tools['ffplay'], 'config-ffplay')  # Empty in the ini: the config value stays.
        self.assertEqual(tools['ocio'], 'ocio://studio-config-latest')  # OCIO URIs are kept as written.

    def test_ini_tools_reach_the_farm_worker(self):
        from tinylib.processing import apply_worker_settings
        self.write_ini('[tinylib]\nsecurity = security.json\n\n[tools]\nffmpeg = C:/farm/ffmpeg.exe\n')
        settings = load_settings(self.config, install=self.ini)
        job = apply_worker_settings({'tools': {'ffmpeg': '//evil/ffmpeg.exe'}, 'media': {}}, settings)
        self.assertEqual(job['tools']['ffmpeg'], 'C:/farm/ffmpeg.exe')

    def test_without_ini_production_access_is_denied(self):
        settings = load_settings(self.config, install=self.ini)
        access = AccessControl(settings, identity='studio\\boss')
        self.assertFalse(access.is_admin)
        self.assertIn('is missing', access.error)
        self.assertFalse(access.can(self.library, 'view'))


class FrozenAppRootTests(unittest.TestCase):
    def test_frozen_exe_looks_beside_itself(self):
        import sys
        from tinylib.install import app_root
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'TinyLib.exe'
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)):
                self.assertEqual(app_root(), Path(folder).resolve())
        self.assertEqual(app_root(), Path(__file__).resolve().parents[1])


if __name__ == '__main__':
    unittest.main()
