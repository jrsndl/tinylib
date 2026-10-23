"""Copy Assets: copy the main, highres or proxy files of assets to a folder, optionally loading
them into Nuke as Read nodes. See docs/actions-and-access.md.

Runs on the selected assets. From the collection panel it runs on the collection's selected
assets, or on the whole collection when none is selected; from the main view it needs a
selection (it never copies the whole library).
"""
import importlib.util
import sys
from pathlib import Path


def _module(name):
    key = '_tinylib_copy_assets_' + name
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


def _key(asset):
    return (asset.get('library_root', ''), asset.get('main', ''))


def scope(assets, context):
    """The assets to copy: refuses the whole main view, narrows a collection run to its selection."""
    if context.get('whole_view'):
        raise ValueError('Copy Assets copies selected assets only: select the assets to copy '
                         '(or run it from a collection).')
    selected = context.get('collection_selected') or []
    if context.get('collection') and selected:
        wanted = {_key(asset) for asset in selected}
        assets = [asset for asset in assets if _key(asset) in wanted]
    return assets


def run(assets, context, config):
    assets = scope(assets, context)
    if not assets:
        return 'Nothing to copy.'
    parent = context.get('parent')
    dialog = _module('copy_dialog')
    copied = dialog.run_dialog(assets, config, context.get('trim'), context.get('collection') or '',
                               getattr(parent, 'preferences', None), parent)
    return copied or 'Nothing was copied.'
