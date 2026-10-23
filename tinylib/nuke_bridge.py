"""Nuke imports are isolated from the browser and processing code."""
from pathlib import Path
from .library import frame_path
from .asset_fields import display_name
from .log import get_logger

log = get_logger(__name__)

# Names a color space goes by in different OCIO configs (ACES 1.2 vs the studio and built-in configs).
ALIASES = {'acescg': ['ACEScg', 'ACES - ACEScg'], 'aces - acescg': ['ACES - ACEScg', 'ACEScg']}


def _leaf(name):
    """'Roles/scene_linear' and 'ACES/ACEScg' as Nuke may list them: the last part, any case."""
    return str(name).replace('\\', '/').rsplit('/', 1)[-1].strip().casefold()


def match_colorspace(space, options):
    """The entry of ``options`` (a Read node's colorspace menu) that is ``space``, or None.
    Exact names first, then aliases, then a case-insensitive match ignoring menu folders."""
    options = list(options)
    wanted = ALIASES.get(str(space).strip().casefold(), [space])
    for name in wanted:
        if name in options:
            return name
    leaves = {_leaf(name) for name in wanted}
    return next((option for option in options if _leaf(option) in leaves), None)


def set_colorspace(node, space, name=''):
    """Set a Read node's color space. A space the Nuke project does not have is not an error:
    the node keeps Nuke's default and the returned warning says so ('' when it was set)."""
    if not space:
        return ''
    try:
        selected = match_colorspace(space, node['colorspace'].values())
        if selected is not None:
            node['colorspace'].setValue(selected)
            return ''
    except Exception as error:  # an unusual Nuke color setup must not stop the import
        log.warning('Cannot set color space %r on %s: %s', space, name, error)
    warning = ('%s: color space %r is not in this Nuke project; the Read node uses Nuke\'s default.'
               % (name or 'Read', space))
    log.warning(warning)
    return warning


def import_asset(asset, highres=False, warnings=None):
    """A Read node for the asset's main or highres. A missing color space is added to
    ``warnings`` (a list) instead of stopping the import."""
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
        warning = set_colorspace(node, asset.get('colorspace', 'ACEScg'), display_name(asset))
        if warning and warnings is not None:
            warnings.append(warning)
        node['label'].setValue(display_name(asset).replace('[', '(').replace(']', ')'))
        return node
    except Exception:
        nuke.delete(node)
        raise


def import_assets(assets, highres=False):
    """Read nodes for the assets; returns the status message, with any color space warnings."""
    warnings = []
    nodes = [import_asset(asset, highres, warnings) for asset in assets]
    message = 'Created %d Read node%s.' % (len(nodes), '' if len(nodes) == 1 else 's')
    return message + (' ' + ' '.join(warnings) if warnings else '')
