"""Pure filter rules shared by all browser views."""
import math
from fractions import Fraction
from .library import matches


def number(value):
    try:
        result = float(Fraction(str(value)))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def duration(asset):
    if asset.get('kind') != 'footage':
        return 0.0
    metadata = asset.get('metadata', {})
    if number(metadata.get('duration_seconds')) not in (None, 0):
        return number(metadata.get('duration_seconds'))
    fps = number(metadata.get('frame_rate', metadata.get('FPS', asset.get('fps'))))
    frames = number(metadata.get('duration_frames', metadata.get('Frame(s)')))
    if frames is None and asset.get('first') is not None and asset.get('last') is not None:
        frames = asset['last'] - asset['first'] + 1
    return frames / fps if frames is not None and fps and fps > 0 else None


def compare(value, operator, threshold):
    if not operator:
        return True
    if value is None:
        return False
    return {'>': value > threshold, '<': value < threshold, '=': value == threshold}[operator]


def accepts(asset, query='', invert=False, tags=(), length=('', 0), width=('', 0), stars=('', 0), rating=0):
    if query.strip() and matches(asset, query) == invert:
        return False
    asset_tags = {str(t).casefold() for t in asset.get('tags', [])}
    if not all(tag.strip().casefold() in asset_tags for tag in tags if tag.strip()):
        return False
    return (compare(duration(asset), *length)
            and compare(number(asset.get('metadata', {}).get('width')), *width)
            and compare(rating, *stars))
