"""External clipboard/drag representations for still and sequence paths."""
import re
from pathlib import PurePosixPath
from .library import frame_path, split_sequence
from .trim import clean_trim

FORMATS = [
    ('nuke', 'Nuke — #### plus range'),
    ('ayon', 'AYON — every frame path'),
    ('hashtag', 'Hashtag — ####'),
    ('houdini', 'Houdini — $F4'),
    ('flame', 'Flame — [first-last]'),
    ('printf', 'Printf — %04d'),
    ('folder', 'Fallback — parent folder'),
]
FORMAT_IDS = {identifier for identifier, _ in FORMATS}


def forward(value):
    return str(value).replace('\\', '/')


def sequence_parts(asset):
    pattern, embedded_first, embedded_last = split_sequence(forward(asset.get('main', '')))
    first = asset.get('first') if asset.get('first') is not None else embedded_first
    last = asset.get('last') if asset.get('last') is not None else embedded_last
    token = re.search(r'#+|%0?(\d*)d|\$F\d*|\[-?\d+--?\d+\]', pattern)
    return pattern, first, last, token


def hashes(pattern, token):
    if not token:
        return pattern, 0
    text = token.group(0)
    width = (len(text) if text.startswith('#') else
             int(token.group(1) or 1) if text.startswith('%') else
             int(text[2:] or 1) if text.startswith('$F') else
             max(len(part.lstrip('-')) for part in text[1:-1].split('-')))
    return pattern[:token.start()] + '#' * width + pattern[token.end():], width


def trimmed_range(first, last, trim):
    """The in/out points inside ``first..last``; the full range when there are none or they miss it."""
    trim = clean_trim(trim)
    if not trim:
        return first, last
    start, end = max(int(first), trim[0]), min(int(last), trim[1])
    return (start, end) if start <= end else (first, last)


def format_asset(asset, notation='nuke', trim=None):
    """Paths for one asset. ``trim`` (in, out) narrows the frame range of the notations that
    carry one: Nuke, AYON and Flame."""
    if notation not in FORMAT_IDS:
        notation = 'nuke'
    pattern, first, last, token = sequence_parts(asset)
    if notation == 'folder':
        return [str(PurePosixPath(pattern).parent)]
    if not token or first is None or last is None:
        return [pattern]
    hash_pattern, width = hashes(pattern, token)
    first, last = trimmed_range(first, last, trim)
    if notation == 'ayon':
        return [forward(frame_path(hash_pattern, frame)) for frame in range(int(first), int(last) + 1)]
    if notation == 'hashtag':
        return [hash_pattern]
    if notation == 'houdini':
        # Houdini pads with $F<width>: $F4 reads files.0001.exr, plain $F reads files.1.exr.
        return [hash_pattern.replace('#' * width, '$F%d' % width if width > 1 else '$F', 1)]
    if notation == 'flame':
        return [hash_pattern.replace('#' * width, '[%s-%s]' % (first, last), 1)]
    if notation == 'printf':
        return [hash_pattern.replace('#' * width, '%%0%dd' % width, 1)]
    return ['%s %s-%s' % (hash_pattern, first, last)]


def format_assets(assets, notation='nuke', trim_for=None):
    """``trim_for(asset)`` returns the in/out points to use for an asset, or None."""
    paths = [path for asset in assets
             for path in format_asset(asset, notation, trim_for(asset) if trim_for else None)]
    return (', ' if notation == 'ayon' else '\n').join(paths)
