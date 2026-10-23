"""Open in Player: open assets in an external player (RV, DJV, mrViewer…) set in tinylib.ini.

From the action dropdown it shows the settings (player, arguments, component, grouping), saved
in your user preferences. From the right-click menu it opens the assets with them at once; the
settings are shown first only when none are saved or they no longer work. "Open in Player
settings…" (dropdowns and right-click menu) opens the settings at any time, without assets.
"""
import importlib.util
import sys
from pathlib import Path


def _module(name):
    key = '_tinylib_open_in_player_' + name
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


def run(assets, context, config):
    player_settings = _module('player_settings')
    parent = context.get('parent')
    preferences = getattr(parent, 'preferences', None)
    settings = context['settings']
    choice = player_settings.saved(preferences, config)
    if context.get('invoked') == 'settings':
        assets = []
    elif context.get('invoked') == 'context_menu' and choice and not player_settings.problem(choice, settings, config):
        return player_settings.open_assets(assets, choice, settings, config, context.get('trim'))
    if preferences is None:
        raise ValueError('Open in Player needs the TinyLib browser to keep its settings.')
    dialog = _module('settings_dialog')
    result = dialog.run_dialog(assets, choice or dict(config.get('defaults', {})), settings, config, preferences,
                               context.get('trim'), context.get('window') or parent)
    return result or 'Open in Player: nothing changed.'
