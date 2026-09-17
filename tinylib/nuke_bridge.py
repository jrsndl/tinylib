"""Nuke imports are isolated from the browser and processing code."""
from pathlib import Path
from .library import frame_path


def import_asset(asset, highres=False):
    import nuke
    key = 'highres' if highres else 'main'
    path = asset.get(key, '')
    first = asset.get('highres_first') if highres else asset.get('first')
    last = asset.get('highres_last') if highres else asset.get('last')
    if not path or not Path(frame_path(path, first) if first is not None else path).is_file():
        raise ValueError('Media is unavailable: ' + path)
    node = nuke.nodes.Read()
    try:
        node['file'].setValue(path.replace('\\', '/'))
        if first is not None:
            for knob, value in [('first', first), ('origfirst', first), ('last', last), ('origlast', last)]:
                node[knob].setValue(int(value))
        space = asset.get('colorspace', 'ACEScg')
        aliases = {'ACEScg': ['ACEScg', 'ACES - ACEScg'], 'ACES - ACEScg': ['ACES - ACEScg', 'ACEScg']}
        options = node['colorspace'].values()
        selected = next((s for s in aliases.get(space, [space]) if s in options), None)
        if selected is None:
            raise ValueError('Color space %r is not available in this Nuke project.' % space)
        node['colorspace'].setValue(selected)
        node['label'].setValue(asset['name'].replace('[', '(').replace(']', ')'))
        return node
    except Exception:
        nuke.delete(node)
        raise
