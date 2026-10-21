"""Running external media tools (ffmpeg, ffprobe, oiiotool) and parsing their numbers.

Every call has a timeout, so an unreadable file on an offline share cannot hang
the browser or a farm task.
"""
import os
import subprocess
from fractions import Fraction

PROBE_TIMEOUT = 60          # ffprobe / oiiotool --info on one file
CONVERSION_TIMEOUT = 3600   # one ffmpeg / oiiotool conversion step on the farm
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


class ToolTimeout(RuntimeError):
    pass


def run_tool(args, timeout=PROBE_TIMEOUT):
    """Run a tool without a console window; return stdout or raise RuntimeError."""
    command = [str(value) for value in args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors='replace',
                                timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as error:
        raise ToolTimeout('%s did not finish within %g s.' % (os.path.basename(command[0]), timeout)) from error
    except FileNotFoundError as error:
        raise RuntimeError('Tool not found: %s. Check "tools" in the studio configuration.' % command[0]) from error
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or '%s failed.' % command[0])[-8000:])
    return result


def number(value):
    """Parse ffprobe/OIIO numbers such as '24000/1001', '25', '1.0'; None when unknown."""
    if value in (None, '', 'N/A', '0/0'):
        return None
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        try:
            return float(value)
        except (ValueError, TypeError):
            return None


def pixel_aspect(value):
    """'1:1', '4:3', '0:1' (unknown), 'N/A' or a number; unknown or invalid becomes 1.0."""
    parsed = number(str(value).replace(':', '/'))
    return parsed if parsed and parsed > 0 else 1.0
