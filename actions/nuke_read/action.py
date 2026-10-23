def run(assets, context, config):
    from tinylib.nuke_bridge import import_assets
    # A color space missing from the Nuke project is reported, not an error.
    return import_assets(assets, highres=config.get('highres', False))
