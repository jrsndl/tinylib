"""External programs for actions: viewers such as RV, DJV or mrViewer, or any other executable.

Tools are named in ``[tools]`` of ``tinylib.ini`` (``rv = C:/Program Files/.../rv.exe``), which
only administrators can write, so actions never choose which program runs. A tool that is
not set there is looked up on PATH under its own name.

An action declares the tools it needs in its manifest (``"tools": ["rv"]``); it is offered only
when they are found. From the action::

    from tinylib.external_tools import launch, asset_arguments
    launch(context['settings'], 'rv', asset_arguments(assets, ['{rv}'], trim_for=context.get('trim')))

Programs are started detached: TinyLib does not wait for them, and closing TinyLib (or Nuke)
does not close them.
"""
import math
import os
import re
import shutil
import subprocess
import time
from fractions import Fraction
from pathlib import Path

from .asset_fields import display_name
from .library import frame_path, split_sequence
from .log import get_logger
from .trim import clean_trim

log = get_logger(__name__)

COMPONENTS = ('main', 'highres', 'proxy')
SEQUENCE_TOKEN = re.compile(r'#+|%0?(\d*)d')
# Variables a host (Nuke, a frozen TinyLib.exe) sets for its own Python and Qt. Passed on, they
# make another program load the wrong Python or Qt plugins, so launched programs do not get them.
HOST_VARIABLES = ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
                  'QML2_IMPORT_PATH', 'QML_IMPORT_PATH', 'QT_API', 'QT_QPA_PLATFORM')
CACHE_SECONDS = 60
_found = {}


class ToolMissing(FileNotFoundError):
    pass


def configured(settings, name):
    """The ``[tools]`` value for ``name``, or ''."""
    return str((settings.get('tools') or {}).get(name) or '').strip()


def resolve(settings, name):
    """The full path of tool ``name``. Raises ToolMissing with what to set in tinylib.ini."""
    value = configured(settings, name)
    candidate = value or name
    if '/' in candidate or '\\' in candidate:
        if Path(candidate).is_file():
            return str(Path(candidate))
        found = None
    else:
        found = shutil.which(candidate)
    if found:
        return found
    if value:
        raise ToolMissing('%s is set to %s under [tools] in tinylib.ini, but that program was not found.'
                          % (name, value))
    raise ToolMissing('%s is not set up: an administrator adds it under [tools] in tinylib.ini, e.g. '
                      '"%s = C:/Program Files/.../%s.exe".' % (name, name, name))


def available(settings, name):
    """Whether ``resolve`` finds the tool. Remembered for a minute: action lists are rebuilt on
    every selection change, and the tool may live on a slow share."""
    key = (name, configured(settings, name))
    hit = _found.get(key)
    if hit is not None and time.monotonic() - hit[1] < CACHE_SECONDS:
        return hit[0]
    try:
        resolve(settings, name)
        result = True
    except ToolMissing:
        result = False
    _found[key] = (result, time.monotonic())
    return result


def forget():
    """Look tools up again on the next ``available`` (after tinylib.ini changed)."""
    _found.clear()


def environment(keep_host_variables=False):
    """The environment for a launched program: this process's, without the host's Python and Qt."""
    env = dict(os.environ)
    if not keep_host_variables:
        for name in HOST_VARIABLES:
            env.pop(name, None)
    return env


def launch(settings, name, arguments=(), cwd=None, keep_host_variables=False):
    """Start tool ``name`` with ``arguments`` (a list; no shell is involved) and return at once.
    Returns the Popen object; the program keeps running when TinyLib closes."""
    executable = resolve(settings, name)
    command = [executable] + [str(argument) for argument in arguments]
    options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
               'close_fds': True, 'cwd': cwd, 'env': environment(keep_host_variables)}
    if os.name == 'nt':
        options['creationflags'] = (getattr(subprocess, 'DETACHED_PROCESS', 0x8)
                                    | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200))
    else:
        options['start_new_session'] = True
    log.info('Launching %s', subprocess.list2cmdline(command) if os.name == 'nt' else ' '.join(command))
    try:
        return subprocess.Popen(command, **options)
    except OSError as error:
        raise OSError('Could not start %s (%s): %s' % (name, executable, error)) from error


def media(asset, component='main'):
    """(pattern, first, last) of a component; first and last are None for a single file.
    None when the asset has no such component."""
    if component not in COMPONENTS:
        raise ValueError('Unknown component %r.' % component)
    value = asset.get(component)
    if not value:
        return None
    pattern, first, last = split_sequence(str(value))
    if first is None and component == 'main':
        first, last = asset.get('first'), asset.get('last')
    elif first is None and component == 'highres':
        first, last = asset.get('highres_first'), asset.get('highres_last')
    if not SEQUENCE_TOKEN.search(Path(pattern).name) or first is None or last is None:
        return pattern, None, None
    return pattern, int(first), int(last)


def tokens(asset, component='main', trim=None):
    """The values an argument template can use for one asset (forward slashes throughout):

    ``{path}``       the file, or the sequence with ``####``
    ``{first_file}`` the file, or the first frame's file (in point when set)
    ``{first}`` ``{last}``  the frame range, narrowed to the in/out points; empty for a single file
    ``{printf}``     the sequence with ``%04d``
    ``{rv}``         RV's notation with the range, ``name.1001-1100#.exr``
    ``{xstudio}``    xSTUDIO's notation with the frames to load, ``name.####.exr=1001-1100``
    ``{count}``      the number of frames from first to last; empty for a single file
    ``{fps}``        the asset's frame rate (metadata ``frame_rate`` or ``FPS``), else 24
    ``{folder}``     the containing folder
    ``{name}``       the asset's display name
    """
    found = media(asset, component)
    if found is None:
        return None
    pattern, first, last = found
    pattern = pattern.replace('\\', '/')
    values = {'name': display_name(asset), 'folder': str(Path(pattern).parent).replace('\\', '/'),
              'fps': frame_rate(asset), 'count': '',
              'first': '', 'last': '', 'path': pattern, 'first_file': pattern, 'printf': pattern, 'rv': pattern,
              'xstudio': pattern}
    if first is None:
        return values
    trim = clean_trim(trim)
    if trim and max(first, trim[0]) <= min(last, trim[1]):
        first, last = max(first, trim[0]), min(last, trim[1])
    match = SEQUENCE_TOKEN.search(pattern)
    text = match.group(0)
    width = len(text) if text.startswith('#') else int(match.group(1) or 1)
    start, end = pattern[:match.start()], pattern[match.end():]
    rv = '#' if width == 4 else '@' * width
    values.update({'first': str(first), 'last': str(last), 'count': str(last - first + 1), 'path': start + '#' * width + end,
                   'first_file': frame_path(pattern, first).replace('\\', '/'),
                   'printf': '%s%%0%dd%s' % (start, width, end),
                   'rv': '%s%d-%d%s%s' % (start, first, last, rv, end),
                   'xstudio': '%s%s%s=%d-%d' % (start, '#' * width, end, first, last)})
    return values


def frame_rate(asset, default='24'):
    """The asset's frame rate as a plain decimal ("25", "23.976024"), for players' time options."""
    metadata = asset.get('metadata') or {}
    for value in (metadata.get('frame_rate'), metadata.get('FPS'), asset.get('fps')):
        try:
            rate = float(Fraction(str(value).strip()))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if rate > 0 and math.isfinite(rate):
            return ('%.6f' % rate).rstrip('0').rstrip('.')
    return default


def _format(text, values):
    try:
        return str(text).format(**values)
    except KeyError as error:
        raise ValueError('Unknown token {%s} in the arguments; known: %s'
                         % (error.args[0], ', '.join('{%s}' % name for name in sorted(values)))) from None


def expand(template, values):
    """Arguments from a template list. A nested list is an optional group, left out when any of
    its tokens is empty (e.g. ``["-in", "{first}"]`` for a movie)."""
    arguments = []
    for item in template:
        if isinstance(item, (list, tuple)):
            group = [_format(part, values) for part in item]
            names = [name for part in item for name in re.findall(r'{(\w+)}', str(part))]
            if all(values.get(name) not in ('', None) for name in names):
                arguments.extend(group)
        else:
            arguments.append(_format(item, values))
    return arguments


def asset_arguments(assets, template, component='main', trim_for=None):
    """``template`` expanded for each asset in turn; assets without ``component`` are skipped."""
    arguments = []
    for asset in assets:
        values = tokens(asset, component, trim_for(asset) if trim_for else None)
        if values is not None:
            arguments.extend(expand(template, values))
    return arguments

