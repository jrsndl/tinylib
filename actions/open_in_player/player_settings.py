"""Open in Player: which [tools] programs are players, the saved choice, and the command. No Qt."""
import shlex
import string

from tinylib.external_tools import COMPONENTS, available, launch, tokens

PREFERENCE = 'open_in_player'  # key under the user's display preferences


def players(settings, config):
    """The [tools] names offered as players: everything in tinylib.ini but TinyLib's own tools."""
    skip = {name.casefold() for name in config.get('not_players', [])}
    return sorted((name for name in (settings.get('tools') or {}) if name.casefold() not in skip), key=str.casefold)


FIELDS = ('arguments', 'in_out_arguments', 'before')


def preset(tool, config):
    """A player's usual arguments, ``{'arguments', 'in_out_arguments', 'before'}``: RV and
    xSTUDIO get their range notations; DJV and mrv2 the first frame, and in/out points as options when they open
    one asset; other players the first frame. A preset written as text is the per-asset arguments."""
    presets = {name.casefold(): value for name, value in config.get('presets', {}).items()}
    found = presets.get((tool or '').casefold(), config.get('default_arguments', '{first_file}'))
    found = {'arguments': found} if isinstance(found, str) else dict(found)
    found.setdefault('arguments', config.get('default_arguments', '{first_file}'))
    found.setdefault('in_out_arguments', '')
    found.setdefault('before', '')
    return {field: found[field] for field in FIELDS}


def saved(preferences, config):
    """The user's saved choice, completed with the defaults; {} when none was saved."""
    data = (getattr(preferences, 'data', {}) or {}).get('display', {}).get(PREFERENCE) or {}
    if not data.get('tool'):
        return {}
    choice = dict(config.get('defaults', {}), **data)
    for field, value in preset(choice['tool'], config).items():
        if field not in data:
            choice[field] = value
    return choice


def problem(choice, settings, config):
    """Why ``choice`` cannot open anything right now, or ''."""
    tool = choice.get('tool')
    if not tool:
        return 'No player is chosen yet.'
    if tool not in players(settings, config):
        return '%s is no longer a tool in tinylib.ini.' % tool
    if not available(settings, tool):
        return '%s was not found (see [tools] in tinylib.ini).' % tool
    if choice.get('component', 'main') not in COMPONENTS:
        return 'Unknown component %r.' % choice.get('component')
    try:
        for field in FIELDS:
            split(choice.get(field, ''))
    except ValueError as error:
        return 'The arguments cannot be read: %s' % error
    return ''


def split(text):
    """Arguments typed on one line, with "double quotes" around one containing spaces.
    Backslashes are kept as typed (Windows paths)."""
    parts = shlex.split(str(text or ''), posix=False)
    return [part[1:-1] if len(part) > 1 and part[0] == part[-1] and part[0] in '"\'' else part for part in parts]


def asset_arguments(asset, template, component, trim):
    """``template`` filled in for one asset. An argument whose tokens are all empty (the frame
    range of a movie or still) is left out. None when the asset has no such component."""
    values = tokens(asset, component, trim)
    if values is None:
        return None
    arguments, previous = [], None
    for part in split(template):
        try:
            text = part.format(**values)
        except KeyError as error:
            raise ValueError('Unknown token {%s}; known: %s' % (error.args[0], ', '.join(
                '{%s}' % name for name in sorted(values)))) from None
        except (IndexError, ValueError):
            raise ValueError('Cannot read the argument %r; tokens look like {first_file}.' % part) from None
        names = [name for _, name, _, _ in string.Formatter().parse(part) if name]
        if names and all(values.get(name, '') == '' for name in names):
            # e.g. "{first}" for a movie or a still, and the option it belongs to ("-in {first}")
            if arguments and arguments[-1].startswith('-') and not _has_tokens(previous):
                arguments.pop()
            previous = part
            continue
        arguments.append(text)
        previous = part
    return arguments


def _has_tokens(part):
    return any(name for _, name, _, _ in string.Formatter().parse(part or '') if name)


def example_asset():
    """A made-up sequence for the command preview when the settings are opened without assets."""
    return {'name': 'example', 'kind': 'footage', 'library_root': '', 'main': 'L:/library/fx/example/example.####.exr',
            'first': 1001, 'last': 1100, 'highres': 'L:/library/fx/example/highres/example.####.exr 1001-1100',
            'proxy': 'L:/library/fx/example/proxy/example.mp4'}


def trimmed(asset, component, trim):
    """Do in/out points narrow this asset's frame range?"""
    if not trim:
        return False
    whole, narrowed = tokens(asset, component), tokens(asset, component, trim)
    return bool(whole and whole['first'] and (whole['first'], whole['last']) != (narrowed['first'], narrowed['last']))


def commands(assets, choice, trim_for=None):
    """(argument lists, skipped count): one list for all assets, or one per asset.

    ``in_out_arguments`` are added after an asset's arguments when a player opens that asset
    alone and its in/out points narrow its range: options such as DJV's ``-inPoint`` can be
    given only once and apply to the file opened last."""
    component = choice.get('component', 'main')
    use_trim = trim_for if choice.get('in_out', True) else None
    per_asset = []
    for asset in assets:
        trim = use_trim(asset) if use_trim else None
        found = asset_arguments(asset, choice.get('arguments', '{first_file}'), component, trim)
        if found is not None:
            per_asset.append((asset, trim, found))
    before = split(choice.get('before', ''))
    skipped = len(assets) - len(per_asset)
    if not per_asset:
        return [], skipped
    groups = [per_asset] if choice.get('one_process', True) else [[item] for item in per_asset]
    lists = []
    for group in groups:
        arguments = before + [argument for _, _, found in group for argument in found]
        if len(group) == 1 and choice.get('in_out_arguments') and trimmed(group[0][0], component, group[0][1]):
            arguments += asset_arguments(group[0][0], choice['in_out_arguments'], component, group[0][1])
        lists.append(arguments)
    return lists, skipped


def open_assets(assets, choice, settings, config, trim_for=None):
    """Start the player. Returns the status message."""
    reason = problem(choice, settings, config)
    if reason:
        raise ValueError('Open in Player: ' + reason)
    lists, skipped = commands(assets, choice, trim_for)
    if not lists:
        raise ValueError('None of the assets has a %s to open.' % choice.get('component', 'main'))
    for arguments in lists:
        launch(settings, choice['tool'], arguments)
    opened = len(assets) - skipped
    return 'Opened %d asset%s in %s%s.' % (opened, '' if opened == 1 else 's', choice['tool'],
                                           ' (%d without %s)' % (skipped, choice.get('component', 'main')) if skipped else '')
