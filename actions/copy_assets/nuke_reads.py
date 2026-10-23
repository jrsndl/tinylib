"""Copy Assets in Nuke: Read nodes for the copied footage."""
from tinylib.asset_fields import display_name
from tinylib.nuke_bridge import set_colorspace

FRAME_MODES = ('none', 'start', 'expression')


def script_frames():
    import nuke
    root = nuke.root()
    return int(root['first_frame'].value()), int(root['last_frame'].value())


def script_folder():
    """The folder of the open Nuke script, or '' when it was never saved."""
    import os
    import nuke
    name = nuke.root().name()
    return os.path.dirname(name) if name and name != 'Root' else ''


def colorspace_for(asset, component):
    """The color space a Read node should use: the asset's for main and highres; proxies are
    display-referred previews, left to Nuke's default for their file type."""
    return asset.get('colorspace', '') if component in ('main', 'highres') else ''


def create_reads(items, component, frame_mode='none', colorspace=False):
    """One Read node per copied asset. Returns (nodes, warnings). With ``colorspace`` the
    asset's color space is set (main and highres); otherwise Nuke chooses it."""
    import nuke
    nodes, warnings = [], []
    start = script_frames()[0]
    for item in items:
        read = item.get('read')
        if not read:
            continue  # a Folder asset: nothing Nuke can read
        asset = item['asset']
        node = nuke.nodes.Read()
        node['file'].setValue(str(read['path']).replace('\\', '/'))
        if read['first'] is not None:
            for knob, value in (('first', read['first']), ('origfirst', read['first']),
                                ('last', read['last']), ('origlast', read['last'])):
                node[knob].setValue(int(value))
        if colorspace:
            warning = set_colorspace(node, colorspace_for(asset, component), display_name(asset))
            if warning:
                warnings.append(warning)
        if frame_mode == 'start':
            node['frame_mode'].setValue('start at')
            node['frame'].setValue(str(start))
        elif frame_mode == 'expression':
            node['frame_mode'].setValue('expression')
            node['frame'].setValue('')
        node['label'].setValue(display_name(asset).replace('[', '(').replace(']', ')'))
        nodes.append(node)
    return nodes, warnings
