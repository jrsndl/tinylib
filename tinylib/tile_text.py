"""Safe, plain-text tile templates; no expressions or attribute evaluation."""
from functools import lru_cache
import string
from .filters import duration
from .asset_fields import display_name

DEFAULT_TEMPLATE = r'{name}\n{type} · {width} × {height}'
# {name} shows the label when the asset has one; {ingest_name} is always the name used at ingest.
TOKENS = ('name', 'ingest_name', 'library', 'category', 'type', 'colorspace', 'tags', 'width', 'height', 'fps', 'length', 'first', 'last')


def validate_template(template):
    if not isinstance(template, str) or len(template) > 1024:
        raise ValueError('Tile text must be at most 1024 characters.')
    return _validate(template)


@lru_cache(maxsize=32)  # Called for every tile on every animation frame.
def _validate(template):
    expanded = template.replace('\\n', '\n')
    if len(expanded.split('\n')) > 6:
        raise ValueError('Tile text supports up to six lines.')
    for _, field, spec, conversion in string.Formatter().parse(expanded):
        if field is not None and (field not in TOKENS or spec or conversion):
            raise ValueError('Use a supported token in braces, for example {name}.')
    return expanded


def render_template(template, asset):
    metadata = asset.get('metadata', {})
    seconds = duration(asset)
    values = {key: asset.get(key) for key in TOKENS}
    values.update(name=display_name(asset), ingest_name=asset.get('name'), type=asset.get('kind', 'still').upper(), tags=', '.join(asset.get('tags', [])),
                  width=metadata.get('width'), height=metadata.get('height'), fps=metadata.get('frame_rate', metadata.get('FPS')),
                  length='%.2f' % seconds if seconds is not None else None)
    return validate_template(template).format_map({key: '—' if value is None else str(value).replace('\n', ' ').replace('\r', ' ') for key, value in values.items()})
