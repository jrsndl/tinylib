def run(assets, context, config):
    from tinylib.qt import QtWidgets
    from tinylib.path_format import format_assets
    preferences = getattr(context.get('parent'), 'preferences', None)
    notation = preferences.data.get('display', {}).get('path_notation', 'nuke') if preferences else 'nuke'
    # In/out points narrow the frame range of Nuke, AYON and Flame paths.
    paths = format_assets(assets, notation, context.get('trim'))
    QtWidgets.QApplication.clipboard().setText(paths)
    return 'Copied %d paths' % len(assets)
