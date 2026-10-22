"""Publish to AYON: selected footage and stills become AYON versions through AYON's CSV ingest.

Footage is published as product type "render", stills as "image"; main and proxy are the
representations, proxy being the reviewable, and the thumbnail is the version thumbnail.
See docs/ayon-publish.md.
"""
import importlib.util
import sys
from pathlib import Path


def _module(name):
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


def run(assets, context, config):
    publish_csv = _module('publish_csv')
    for asset in assets:
        problem = publish_csv.publishable(asset)
        if problem:
            raise ValueError('%s cannot be published to AYON: %s.' % (asset.get('name', 'Asset'), problem))
    parent = context.get('parent')
    dialog = _module('publish_dialog')
    published = dialog.run_dialog(assets, config, context.get('settings'), getattr(parent, 'preferences', None), parent)
    return published or 'Nothing was published to AYON.'
