def run(assets, context, config):
    from tinylib.qt import QtWidgets
    paths = config.get('separator', '\n').join(asset['main'] for asset in assets)
    QtWidgets.QApplication.clipboard().setText(paths)
    return 'Copied %d paths' % len(assets)
