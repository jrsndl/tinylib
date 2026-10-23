"""Copy Assets in Nuke: Read nodes for the copied footage."""
from tinylib.asset_fields import display_name

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


def create_reads(items, component, frame_mode='none'):
    """One Read node per copied asset. Returns (nodes, warnings)."""
    import nuke
    nodes, warnings = [], []
    start = script_frames()[0]
    aliases = {'ACEScg': ['ACEScg', 'ACES - ACEScg'], 'ACES - ACEScg': ['ACES - ACEScg', 'ACEScg']}
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
        space = colorspace_for(asset, component)
        if space:
            options = node['colorspace'].values()
            chosen = next((name for name in aliases.get(space, [space]) if name in options), None)
            if chosen:
                node['colorspace'].setValue(chosen)
            else:
                warnings.append('%s: color space %r is not in this Nuke project; the Read node uses Nuke\'s default.'
                                % (display_name(asset), space))
        if frame_mode == 'start':
            node['frame_mode'].setValue('start at')
            node['frame'].setValue(str(start))
        elif frame_mode == 'expression':
            node['frame_mode'].setValue('expression')
            node['frame'].setValue('')
        node['label'].setValue(display_name(asset).replace('[', '(').replace(']', ')'))
        nodes.append(node)
    return nodes, warnings
