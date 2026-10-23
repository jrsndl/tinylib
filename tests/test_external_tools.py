"""External tools for actions, the context-menu manifest keys, and the Open in Player action."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tinylib import external_tools
from tinylib.access import AccessControl
from tinylib.actions import Action, ActionRegistry
from tinylib.library import atomic_json

ACTION = Path(__file__).resolve().parents[1] / 'actions' / 'open_in_player'


def load(name):
    key = '_tinylib_open_in_player_' + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, ACTION / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


player_settings = load('player_settings')
action_module = load('action')
CONFIG = json.loads((ACTION / 'config.json').read_text())

SEQUENCE = {'name': 'fire', 'kind': 'footage', 'library_root': 'L:/lib', 'main': 'L:/lib/fx/fire/main/fire.####.exr',
            'first': 1001, 'last': 1100, 'proxy': 'L:/lib/fx/fire/proxy/fire.mp4'}
MOVIE = {'name': 'smoke', 'kind': 'footage', 'library_root': 'L:/lib', 'main': 'L:/lib/fx/smoke/main/smoke.mov'}


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rv = self.root / 'rv.exe'
        self.rv.write_bytes(b'')
        external_tools.forget()
        self.addCleanup(external_tools.forget)


class ToolTests(Base):
    def test_resolve_configured_path_path_lookup_and_missing(self):
        settings = {'tools': {'rv': str(self.rv), 'djv': str(self.root / 'none.exe')}}
        self.assertEqual(external_tools.resolve(settings, 'rv'), str(self.rv))
        with self.assertRaisesRegex(external_tools.ToolMissing, 'set to .*none.exe.*not found'):
            external_tools.resolve(settings, 'djv')
        with self.assertRaisesRegex(external_tools.ToolMissing, r'adds it under \[tools\]'):
            external_tools.resolve(settings, 'no_such_player_here')
        with patch('tinylib.external_tools.shutil.which', return_value='/usr/bin/mrv2'):
            self.assertEqual(external_tools.resolve({'tools': {}}, 'mrv2'), '/usr/bin/mrv2')
        self.assertTrue(external_tools.available(settings, 'rv'))
        self.assertFalse(external_tools.available(settings, 'djv'))

    def test_availability_is_remembered_briefly(self):
        settings = {'tools': {'rv': str(self.rv)}}
        self.assertTrue(external_tools.available(settings, 'rv'))
        self.rv.unlink()
        self.assertTrue(external_tools.available(settings, 'rv'))
        external_tools.forget()
        self.assertFalse(external_tools.available(settings, 'rv'))

    def test_launch_is_detached_without_host_python_and_qt(self):
        settings = {'tools': {'rv': str(self.rv)}}
        with patch.dict(os.environ, {'PYTHONPATH': 'nuke', 'QT_PLUGIN_PATH': 'nuke', 'OCIO': 'aces.ocio'}), \
                patch('tinylib.external_tools.subprocess.Popen') as popen:
            external_tools.launch(settings, 'rv', ['a b.exr', 7])
            command, options = popen.call_args[0][0], popen.call_args[1]
            self.assertEqual(command, [str(self.rv), 'a b.exr', '7'])
            self.assertNotIn('PYTHONPATH', options['env'])
            self.assertNotIn('QT_PLUGIN_PATH', options['env'])
            self.assertEqual(options['env']['OCIO'], 'aces.ocio')
            self.assertTrue(options.get('start_new_session') or options.get('creationflags'))
            external_tools.launch(settings, 'rv', [], keep_host_variables=True)
            self.assertEqual(popen.call_args[1]['env']['PYTHONPATH'], 'nuke')

    def test_tokens_for_sequences_movies_and_in_out(self):
        values = external_tools.tokens(SEQUENCE, 'main', (1010, 1020))
        self.assertEqual(values['rv'], 'L:/lib/fx/fire/main/fire.1010-1020#.exr')
        self.assertEqual(values['first_file'], 'L:/lib/fx/fire/main/fire.1010.exr')
        self.assertEqual(values['printf'], 'L:/lib/fx/fire/main/fire.%04d.exr')
        self.assertEqual((values['first'], values['last']), ('1010', '1020'))
        self.assertEqual(external_tools.tokens(SEQUENCE, 'main', (5000, 6000))['first'], '1001')  # outside: whole
        three = dict(SEQUENCE, main='C:\\x\\a.%03d.dpx 1-9')
        self.assertEqual(external_tools.tokens(three)['rv'], 'C:/x/a.1-9@@@.dpx')
        movie = external_tools.tokens(MOVIE)
        self.assertEqual((movie['first_file'], movie['first'], movie['rv']), (MOVIE['main'], '', MOVIE['main']))
        self.assertEqual(external_tools.tokens(SEQUENCE, 'proxy')['path'], SEQUENCE['proxy'])
        self.assertIsNone(external_tools.tokens(MOVIE, 'highres'))

    def test_expand_with_optional_groups(self):
        template = ['[', '{path}', ['-in', '{first}', '-out', '{last}'], ']']
        self.assertEqual(external_tools.asset_arguments([SEQUENCE, MOVIE], template),
                         ['[', 'L:/lib/fx/fire/main/fire.####.exr', '-in', '1001', '-out', '1100', ']',
                          '[', MOVIE['main'], ']'])
        with self.assertRaisesRegex(ValueError, 'Unknown token {frames}'):
            external_tools.asset_arguments([SEQUENCE], ['{frames}'])


class ManifestTests(Base):
    def make(self, **manifest):
        folder = self.root / 'actions' / 'test'
        folder.mkdir(parents=True, exist_ok=True)
        atomic_json(folder / 'manifest.json', dict({'id': 'test.view', 'name': 'View', 'version': '1',
                                                    'category': 'Test'}, **manifest))
        atomic_json(folder / 'config.json', {})
        (folder / 'action.py').write_text('def run(assets, context, config):\n    return context.get("invoked")\n')
        return folder

    def test_context_menu_places_and_label(self):
        self.assertFalse(Action(self.make()).in_menu('main'))
        action = Action(self.make(context_menu=True))
        self.assertTrue(all(action.in_menu(place) for place in ('main', 'collection', 'player')))
        action = Action(self.make(context_menu={'label': 'Quick look', 'where': ['player']}))
        self.assertEqual((action.in_menu('main'), action.in_menu('player'), action.menu_label), (False, True, 'Quick look'))
        for bad in ({'where': ['desktop']}, {'where': []}, 'yes', {'label': ''}):
            with self.assertRaisesRegex(ValueError, 'context_menu'):
                Action(self.make(context_menu=bad))
        with self.assertRaisesRegex(ValueError, 'tools'):
            Action(self.make(tools='rv'))

    def test_actions_needing_a_missing_tool_are_not_offered(self):
        self.make(tools=['rv'])
        access = AccessControl({}, identity='studio\\admin', persist=False)
        registry = ActionRegistry([self.root / 'actions'])
        action = registry.actions['test.view']
        asset = dict(SEQUENCE)
        self.assertTrue(registry.allowed(action, [asset], access))  # no check set: offered
        registry.tool_available = lambda name: False
        self.assertFalse(registry.allowed(action, [asset], access))
        with self.assertRaises(PermissionError):
            registry.run('test.view', [asset], access)
        registry.tool_available = lambda name: name == 'rv'
        self.assertEqual(registry.run('test.view', [asset], access, {'invoked': 'context_menu'}), 'context_menu')

    def test_settings_open_without_assets_or_library_rights(self):
        self.make(settings=True, allow_only=['managers'], hosts=['standalone'])
        registry = ActionRegistry([self.root / 'actions'])
        action = registry.actions['test.view']
        self.assertEqual(action.settings_label, 'View settings…')
        admin = AccessControl({}, identity='studio\\admin', persist=False)
        nobody = AccessControl({'access': {'users': {'studio\\admin': ['admins']}}}, identity='studio\\x', persist=False)
        self.assertTrue(registry.configurable(action, admin, 'standalone'))
        self.assertFalse(registry.configurable(action, admin, 'nuke'))
        self.assertFalse(registry.configurable(action, nobody, 'standalone'))  # no groups at all
        with patch('tinylib.actions.host_name', return_value='standalone'):
            self.assertEqual(registry.run('test.view', [dict(SEQUENCE)], admin, {'invoked': 'dropdown'}, settings=True),
                             'settings')
        registry.tool_available = lambda name: False
        self.make(settings=True, tools=['rv'])
        registry.reload()
        self.assertFalse(registry.configurable(registry.actions['test.view'], admin))
        self.make(settings=False)
        registry.reload()
        self.assertFalse(registry.configurable(registry.actions['test.view'], admin))
        with self.assertRaises(PermissionError):
            registry.run('test.view', [], admin, settings=True)
        with self.assertRaisesRegex(ValueError, 'settings'):
            Action(self.make(settings='yes'))

    def test_bundled_open_in_player_manifest(self):
        action = Action(ACTION)
        self.assertTrue(action.has_settings)
        self.assertTrue(action.in_menu('player'))
        self.assertFalse(action.whole_view)


class FakePreferences:
    def __init__(self, choice=None):
        self.data = {'display': {'open_in_player': choice} if choice else {}}

    def set_display(self, **values):
        self.data['display'].update(values)


class OpenInPlayerTests(Base):
    def settings(self):
        return {'tools': {'ffmpeg': 'ffmpeg', 'oiiotool': 'oiiotool', 'ocio': 'x.ocio', 'rv': str(self.rv),
                          'djv': str(self.root / 'none.exe')}}

    def test_players_are_the_ini_tools_but_tinylibs_own(self):
        self.assertEqual(player_settings.players(self.settings(), CONFIG), ['djv', 'rv'])
        self.assertEqual(player_settings.preset('RV', CONFIG), {'arguments': '{rv}', 'in_out_arguments': '', 'before': ''})
        self.assertEqual(player_settings.preset('djv', CONFIG)['in_out_arguments'],
                         '-timeUnits Frames -inPoint {first} -outPoint {last}')
        self.assertEqual(player_settings.preset('other', CONFIG)['arguments'], '{first_file}')
        self.assertEqual(player_settings.preset('x', {'presets': {'x': '{path}'}})['arguments'], '{path}')

    def test_saved_choice_and_problems(self):
        self.assertEqual(player_settings.saved(FakePreferences(), CONFIG), {})
        choice = player_settings.saved(FakePreferences({'tool': 'rv'}), CONFIG)
        self.assertEqual((choice['arguments'], choice['component'], choice['one_process']), ('{rv}', 'main', True))
        self.assertEqual(player_settings.problem(choice, self.settings(), CONFIG), '')
        self.assertIn('not found', player_settings.problem(dict(choice, tool='djv'), self.settings(), CONFIG))
        self.assertIn('no longer', player_settings.problem(dict(choice, tool='mrv2'), self.settings(), CONFIG))
        self.assertIn('cannot be read', player_settings.problem(dict(choice, arguments='"open'), self.settings(), CONFIG))

    def test_commands_group_assets_and_drop_empty_ranges(self):
        choice = {'tool': 'rv', 'arguments': '{first_file} -in={first}', 'before': '-fullscreen "C:\\a b\\x.rvprefs"',
                  'component': 'main', 'in_out': True, 'one_process': True}
        trims = lambda asset: (1050, 1060) if asset['name'] == 'fire' else None
        lists, skipped = player_settings.commands([SEQUENCE, MOVIE], choice, trims)
        self.assertEqual(skipped, 0)
        self.assertEqual(lists, [['-fullscreen', 'C:\\a b\\x.rvprefs', 'L:/lib/fx/fire/main/fire.1050.exr', '-in=1050',
                                  MOVIE['main']]])
        lists, _ = player_settings.commands([SEQUENCE, MOVIE], dict(choice, one_process=False, in_out=False), trims)
        self.assertEqual(len(lists), 2)
        self.assertEqual(lists[0][2:], ['L:/lib/fx/fire/main/fire.1001.exr', '-in=1001'])
        lists, skipped = player_settings.commands([SEQUENCE, MOVIE], dict(choice, component='proxy'))
        self.assertEqual((len(lists), skipped), (1, 1))

    def test_djv_defaults(self):
        choice = player_settings.saved(FakePreferences({'tool': 'djv'}), CONFIG)
        self.assertEqual((choice['arguments'], choice['one_process'], choice['in_out']), ('{first_file}', True, True))
        trims = lambda asset: (1010, 1020)
        # One asset with in/out points: the first frame of the range, then DJV's in/out options.
        lists, _ = player_settings.commands([SEQUENCE], choice, trims)
        self.assertEqual(lists, [['L:/lib/fx/fire/main/fire.1010.exr', '-timeUnits', 'Frames',
                                  '-inPoint', '1010', '-outPoint', '1020']])
        # No in/out points, a movie, or several assets in one DJV: the options are left out
        # (DJV takes each option once, for the file opened last).
        self.assertEqual(player_settings.commands([SEQUENCE], choice)[0], [['L:/lib/fx/fire/main/fire.1001.exr']])
        self.assertEqual(player_settings.commands([MOVIE], choice, trims)[0], [[MOVIE['main']]])
        lists, _ = player_settings.commands([SEQUENCE, dict(SEQUENCE, name='b')], choice, trims)
        self.assertEqual(len(lists), 1)
        self.assertNotIn('-inPoint', lists[0])
        lists, _ = player_settings.commands([SEQUENCE, MOVIE], dict(choice, one_process=False), trims)
        self.assertIn('-inPoint', lists[0])
        self.assertEqual(lists[1], [MOVIE['main']])
        self.assertEqual(player_settings.commands([SEQUENCE], dict(choice, in_out=False), trims)[0],
                         [['L:/lib/fx/fire/main/fire.1001.exr']])

    def test_mrv2_defaults(self):
        choice = player_settings.saved(FakePreferences({'tool': 'mrv2'}), CONFIG)
        asset = dict(SEQUENCE, metadata={'frame_rate': 25.0})
        lists, _ = player_settings.commands([asset], choice, lambda a: (1010, 1020))
        # mrv2 reads -inOutRange as start/duration/rate.
        self.assertEqual(lists, [['L:/lib/fx/fire/main/fire.1010.exr', '-inOutRange', '1010/11/25']])
        self.assertEqual(player_settings.commands([asset], choice)[0], [['L:/lib/fx/fire/main/fire.1001.exr']])
        self.assertEqual(external_tools.frame_rate({'metadata': {'FPS': '24000/1001'}}), '23.976024')
        self.assertEqual(external_tools.frame_rate({'metadata': {'frame_rate': 'bad'}}), '24')
        self.assertEqual(external_tools.tokens(SEQUENCE)['count'], '100')

    def test_xstudio_defaults(self):
        choice = player_settings.saved(FakePreferences({'tool': 'xstudio'}), CONFIG)
        trims = lambda asset: (1010, 1020) if asset['name'] == 'fire' else None
        lists, _ = player_settings.commands([SEQUENCE, dict(SEQUENCE, name='b'), MOVIE], choice, trims)
        # The frames to load are part of each path, so in/out points work for every asset.
        self.assertEqual(lists, [['L:/lib/fx/fire/main/fire.####.exr=1010-1020', 'L:/lib/fx/fire/main/fire.####.exr=1001-1100',
                                  MOVIE['main']]])
        three = dict(SEQUENCE, main='C:\\x\\a.%03d.dpx 1-9')
        self.assertEqual(external_tools.tokens(three)['xstudio'], 'C:/x/a.###.dpx=1-9')

    def test_an_option_goes_with_its_empty_value(self):
        self.assertEqual(player_settings.asset_arguments(MOVIE, '{first_file} -in {first} -out {last} -fs', 'main', None),
                         [MOVIE['main'], '-fs'])
        self.assertEqual(player_settings.asset_arguments(SEQUENCE, '-in {first} {first_file}', 'main', None),
                         ['-in', '1001', 'L:/lib/fx/fire/main/fire.1001.exr'])

    def test_context_menu_opens_with_saved_choice_dropdown_shows_settings(self):
        preferences = FakePreferences({'tool': 'rv'})
        parent = type('Browser', (), {'preferences': preferences})()
        context = {'parent': parent, 'settings': self.settings(), 'invoked': 'context_menu', 'trim': None}
        with patch('tinylib.external_tools.subprocess.Popen') as popen:
            message = action_module.run([SEQUENCE], context, CONFIG)
        self.assertEqual(popen.call_args[0][0], [str(self.rv), 'L:/lib/fx/fire/main/fire.1001-1100#.exr'])
        self.assertIn('Opened 1 asset in rv', message)
        shown = []
        fake = type(sys)('fake_dialog')
        fake.run_dialog = lambda *args: shown.append(args) or 'saved'
        with patch.dict(sys.modules, {'_tinylib_open_in_player_settings_dialog': fake}):
            self.assertEqual(action_module.run([SEQUENCE], dict(context, invoked='dropdown'), CONFIG), 'saved')
            # A saved choice that no longer works shows the settings even from the menu.
            preferences.data['display']['open_in_player'] = {'tool': 'djv'}
            self.assertEqual(action_module.run([SEQUENCE], context, CONFIG), 'saved')
            # "Open in Player settings…": the settings, without assets, whatever is saved.
            preferences.data['display']['open_in_player'] = {'tool': 'rv'}
            self.assertEqual(action_module.run([SEQUENCE], dict(context, invoked='settings'), CONFIG), 'saved')
        self.assertEqual(len(shown), 3)
        self.assertEqual(shown[2][0], [])


if __name__ == '__main__':
    unittest.main()
