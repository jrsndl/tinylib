def run(assets, context, config):
    from tinylib.nuke_bridge import import_asset
    return [import_asset(asset, highres=config.get('highres', True)) for asset in assets]
