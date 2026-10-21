"""In and out points ("trims") for footage: per-user, never stored in a library database.

A trim is ``(in, out)`` in the asset's own frame numbers (the numbers the player
shows), with ``in <= out``. No trim means the whole range.
"""


def clean_trim(value):
    """Return a valid ``(in, out)`` tuple from stored or imported data, else None."""
    if isinstance(value, dict):
        value = (value.get('in'), value.get('out'))
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    start, end = value
    if type(start) is not int or type(end) is not int or start > end:
        return None
    return start, end


def stored_trim(trim):
    """The JSON form of a trim, or None to remove it."""
    trim = clean_trim(trim)
    return {'in': trim[0], 'out': trim[1]} if trim else None


def advance_frame(frame, low, high, loop):
    """Where playback goes after moving to ``frame`` within the playable range ``low..high``.

    Returns ``(frame, stop)``. Looping wraps around inside the range (keeping any
    overshoot at higher speeds); without looping, playback stops at the range ends.
    The player moves playback inside the range before it starts.
    """
    if loop:
        span = high - low + 1
        return (low + (frame - low) % span if span > 0 else low), False
    if frame >= high:
        return high, True
    if frame <= low:
        return low, True
    return frame, False
