"""CSV rows and the AYON traypublisher command for the Publish to AYON action. No Qt here.

The publish uses AYON's CSV ingest, as IngestDesktop does (github.com/jrsndl/IngestDesktop):

    ayon_console addon traypublisher ingestcsv --filepath <csv> --project <project>
        --folder-path <folder> --task <task> --preset <preset> [--ignore-validators]

Each TinyLib asset becomes one AYON version with one row (representation) per enabled
component: main, proxy and highres by default (see COMPONENTS). The asset's thumbnail is the
version thumbnail. Footage is published as product type "render", stills as "image".
"""
import csv
import datetime
import os
import re
import subprocess
from pathlib import Path

# The columns of the AYON CSV ingest "Default" preset that TinyLib fills.
COLUMNS = ['File Path', 'Representation', 'Folder Path', 'Task Name', 'Product Type', 'Variant', 'Version',
           'Frame Start', 'Frame End', 'Handle Start', 'Handle End', 'FPS', 'Version Thumbnail',
           'Slate Exists', 'Version Comment', 'Representation Colorspace', 'Representation Tags']

PRODUCT_TYPES = {'footage': 'render', 'still': 'image'}

# Components published as representations. "representation" is the representation name
# ({extension} = the file extension); "colorspace" may be {colorspace}, the asset's own
# colour space (renamed by colorspace_map).
COMPONENTS = [
    {'enabled': True, 'component': 'main', 'representation': '{extension}', 'tags': 'passing',
     'colorspace': 'ACEScg'},
    {'enabled': True, 'component': 'proxy', 'representation': '{extension}',
     'tags': 'passing;ftrackreview;webreview', 'colorspace': 'Output - Rec.709'},
    {'enabled': False, 'component': 'highres', 'representation': '{extension}', 'tags': 'passing',
     'colorspace': 'ACEScg'},
]
COMPONENT_NAMES = ('main', 'proxy', 'highres')

DEFAULTS = {
    'preset': 'Default',
    'ignore_validators': True,
    # Context of the CSV ingest itself; empty = the folder and task picked for the assets.
    'csv_folder_path': '',
    'csv_task': '',
    'components': COMPONENTS,
    # Removed from the tags when there are no ftrack credentials.
    'ftrack_review_tag': 'ftrackreview',
    'colorspace_map': {},
    'default_fps': 25.0,
    'still_frame': 1,
    'comment': 'TinyLib: {library} / {name}',
    'strip_environment': ['PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'QT_PLUGIN_PATH',
                          'QT_QPA_PLATFORM_PLUGIN_PATH', 'AYON_HOST_NAME', 'AYON_APP_NAME',
                          'AYON_FOLDER_PATH', 'AYON_TASK_NAME', 'AYON_WORKDIR', 'AYON_PROJECT_NAME'],
}


def options(config):
    """The action configuration with defaults for anything left out."""
    result = dict(DEFAULTS)
    result.update({key: value for key, value in (config or {}).items() if not key.startswith('_')})
    result['components'] = components(result.get('components'))
    return result


def components(items):
    """The component rows, checked: main, proxy and highres once each, in that order."""
    by_name = {item['component']: dict(item) for item in COMPONENTS}
    for item in items or []:
        name = item.get('component') if isinstance(item, dict) else None
        if name not in COMPONENT_NAMES:
            raise ValueError('Unknown component %r in the Publish to AYON configuration.' % name)
        by_name[name] = dict(by_name[name], **item)
    result = []
    for name in COMPONENT_NAMES:
        item = by_name[name]
        item['enabled'] = bool(item.get('enabled'))
        for key in ('representation', 'tags', 'colorspace'):
            item[key] = str(item.get(key) or '').strip()
        result.append(item)
    return result


def publishable(asset):
    """None when the asset can be published, else the reason it cannot."""
    if asset.get('kind') not in PRODUCT_TYPES:
        return 'only footage and stills can be published'
    for key in ('main', 'thumb'):
        if not asset.get(key):
            return 'it has no %s' % key
    return None


def product_type(asset):
    return PRODUCT_TYPES[asset['kind']]


def default_variant(asset):
    """The asset name (not its label), other characters than letters, digits and _ as _:
    "smoke puff-01" -> "smoke_puff_01"."""
    return re.sub(r'[^A-Za-z0-9_]+', '_', str(asset.get('name') or '')).strip('_') or 'Main'


def default_version(asset):
    """The asset's version_number, or 1."""
    try:
        return max(1, int(asset.get('version_number') or 1))
    except (TypeError, ValueError):
        return 1


def valid_variant(text):
    return bool(re.fullmatch(r'[A-Za-z0-9_]+', text or ''))


def sequence(value, first=None, last=None):
    """(path with forward slashes and #### padding, first, last) of a main or proxy."""
    path, start, end = str(value), first, last
    match = re.match(r'^(.*?)\s+(-?\d+)-(-?\d+)$', path)
    if match:
        path, start, end = match[1], int(match[2]), int(match[3])
    path = re.sub(r'%0?(\d*)d', lambda m: '#' * int(m[1] or 1), path)
    return path.replace('\\', '/'), start, end


def extension(path):
    return Path(path).suffix.lower().lstrip('.')


def frame_range(asset, opts):
    """(first, last) frames of the version."""
    _, first, last = sequence(asset['main'], asset.get('first'), asset.get('last'))
    if first is not None and last is not None:
        return first, last
    metadata = asset.get('metadata') or {}
    if asset['kind'] == 'footage' and metadata.get('frame_end') is not None:
        return int(metadata.get('frame_start') or 0), int(metadata['frame_end'])
    if asset['kind'] == 'footage' and metadata.get('duration_frames'):
        return 1, int(metadata['duration_frames'])
    frame = int(opts['still_frame'])
    return frame, frame


def fps(asset, opts):
    rate = (asset.get('metadata') or {}).get('frame_rate')
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        rate = 0.0
    return rate if rate > 0 else float(opts['default_fps'])


def _number(value):
    return ('%.3f' % value).rstrip('0').rstrip('.') if isinstance(value, float) else str(value)


def _tags(*parts):
    tags = []
    for part in parts:
        for tag in str(part or '').split(';'):
            if tag.strip() and tag.strip() not in tags:
                tags.append(tag.strip())
    return ';'.join(tags)


def default_comment(asset, opts):
    """The version comment the window starts with: config "comment" filled in."""
    return str(opts.get('comment') or '').format(
        library=asset.get('library', ''), name=asset.get('name', ''), label=asset.get('label') or asset.get('name', ''),
        category=asset.get('category', ''), id=asset.get('id', '')).strip()


def component_file(asset, name):
    """(path, first, last) of a component of the asset, or None when it has none."""
    if not asset.get(name):
        return None
    if name == 'main':
        return sequence(asset['main'], asset.get('first'), asset.get('last'))
    if name == 'highres':
        return sequence(asset['highres'], asset.get('highres_first'), asset.get('highres_last'))
    return sequence(asset[name])


def asset_rows(asset, folder_path, task, variant, opts, version='', comment='', ftrack=False, parts=None):
    """The CSV rows (dicts) of one asset: one per enabled component it has.

    ``parts`` are the component rows (default: the configuration's). Without ftrack
    credentials the ftrack review tag is left out. A proxy whose representation name is the
    main's (a JPEG still with a JPEG proxy) is not a row of its own: its tags go to the main.
    """
    problem = publishable(asset)
    if problem:
        raise ValueError('%s cannot be published: %s.' % (asset.get('name', 'Asset'), problem))
    if not valid_variant(variant):
        raise ValueError('Variant %r of %s may contain only letters, digits and _.' % (variant, asset.get('name')))
    if str(version).strip() and not str(version).strip().isdigit():
        raise ValueError('Version of %s must be a number, or empty for the next version.' % asset.get('name'))
    first, last = frame_range(asset, opts)
    common = {
        'Folder Path': folder_path, 'Task Name': task, 'Product Type': product_type(asset), 'Variant': variant,
        'Version': str(version or '').strip(), 'Frame Start': str(first), 'Frame End': str(last),
        'Handle Start': '0', 'Handle End': '0', 'FPS': _number(fps(asset, opts)),
        'Version Thumbnail': str(asset['thumb']).replace('\\', '/'), 'Slate Exists': 'False',
        'Version Comment': comment.strip(),
    }
    space = asset.get('colorspace', '') or ''
    skip = '' if ftrack else opts.get('ftrack_review_tag', '')
    rows, names = [], {}
    for part in parts if parts is not None else opts['components']:
        found = component_file(asset, part['component']) if part['enabled'] else None
        if found is None:
            continue
        path = found[0]
        name = part['representation'].format(extension=extension(path), ext=extension(path))
        tags = _tags(*[tag for tag in part['tags'].split(';') if tag.strip() != skip])
        colorspace = part['colorspace'].format(colorspace=opts['colorspace_map'].get(space, space))
        if not name:
            raise ValueError('The %s component needs a representation name.' % part['component'])
        if name in names:
            if part['component'] == 'proxy' and names[name]['_component'] == 'main':
                names[name]['Representation Tags'] = _tags(names[name]['Representation Tags'], tags)
                continue
            raise ValueError('%s: the %s and %s components would both be representation "%s". Give them different '
                             'representation names under Components.' % (asset.get('name'), names[name]['_component'],
                                                                         part['component'], name))
        row = dict(common, **{'File Path': path, 'Representation': name, 'Representation Colorspace': colorspace,
                              'Representation Tags': tags, '_component': part['component']})
        names[name] = row
        rows.append(row)
    if not rows:
        raise ValueError('%s has none of the enabled components.' % asset.get('name'))
    return rows


def check_variants(items):
    """Two assets into the same product would be one version: refuse that."""
    seen = {}
    for asset, variant in items:
        key = (product_type(asset), variant.casefold())
        if key in seen:
            raise ValueError('%s and %s would both be published as %s%s. Give them different variants.'
                             % (seen[key], asset.get('name'), product_type(asset), variant))
        seen[key] = asset.get('name')


def write_csv(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, '') for column in COLUMNS})
    return path


def log_folder(opts, env=None):
    env = os.environ if env is None else env
    if opts.get('log_folder'):
        return Path(os.path.expandvars(opts['log_folder']))
    base = env.get('LOCALAPPDATA') or env.get('APPDATA') or str(Path.home() / '.config')
    return Path(base) / 'TinyLib' / 'ayon_publish'


def csv_path(opts, now=None, env=None, folder=None, suffix=''):
    stamp = (now or datetime.datetime.now()).strftime('%Y%m%d_%H%M%S')
    name = 'tinylib_ayon_%s%s.csv' % (stamp, ('_' + re.sub(r'[^A-Za-z0-9_.-]+', '_', suffix)) if suffix else '')
    return Path(folder or log_folder(opts, env)) / name


def batches(items, separate):
    """``items``: [(asset, rows)]. One (label, rows) per asset with ``separate``, else one for all."""
    if separate:
        return [(asset.get('name') or 'asset', rows) for asset, rows in items]
    rows = [row for _, asset_rows in items for row in asset_rows]
    return [('%d assets' % len(items) if len(items) > 1 else (items[0][0].get('name') or 'asset'), rows)]


# ---------------------------------------------------------------- Deadline

def deadline_folder(opts, settings):
    """Farm-readable folder for the CSV and job files: the action's deadline_csv_folder, else
    ayon_publish in the Deadline spool_root of the browser configuration."""
    folder = opts.get('deadline_csv_folder') or ''
    if not folder:
        spool = ((settings or {}).get('deadline') or {}).get('spool_root') or ''
        folder = str(Path(spool) / 'ayon_publish') if spool else ''
    if not folder:
        raise ValueError('Deadline publish needs a folder the farm can read: set deadline_csv_folder in the '
                         'Publish to AYON config.json, or deadline.spool_root in the browser configuration.')
    return Path(os.path.expandvars(folder))


def deadline_environment(credentials, opts):
    """Environment of the farm job: the AYON server, and (deadline_send_credentials) the AYON
    and ftrack keys, plus deadline_environment from the config."""
    env = {'AYON_SERVER_URL': credentials['ayon_url']}
    if opts.get('deadline_send_credentials', True):
        env['AYON_API_KEY'] = credentials['ayon_key']
        if credentials.get('ftrack_url') and credentials.get('ftrack_user') and credentials.get('ftrack_key'):
            env.update(FTRACK_SERVER=credentials['ftrack_url'], FTRACK_API_USER=credentials['ftrack_user'],
                       FTRACK_API_KEY=credentials['ftrack_key'])
    env.update({str(key): str(value) for key, value in (opts.get('deadline_environment') or {}).items()})
    return env


def deadline_job(name, args, environment, opts, settings):
    """(job_info, plugin_info) of a Deadline CommandLine job running the ingest command ``args``."""
    config = dict(((settings or {}).get('deadline') or {}), **(opts.get('deadline') or {}))
    executable = opts.get('deadline_ayon_executable') or args[0]
    job_info = {'Plugin': 'CommandLine', 'Name': name, 'Frames': '0', 'ChunkSize': '1',
                'Pool': str(config.get('pool', 'none')), 'Group': str(config.get('group', 'none')),
                'Priority': str(int(config.get('priority', 50))), 'MachineLimit': '1',
                'Comment': 'TinyLib Publish to AYON'}
    for index, (key, value) in enumerate(sorted(environment.items())):
        job_info['EnvironmentKeyValue%d' % index] = '%s=%s' % (key, value)
    plugin_info = {'Executable': executable, 'Arguments': subprocess.list2cmdline([str(arg) for arg in args[1:]]),
                   'StartupDirectory': '', 'ShellExecute': 'False'}
    return job_info, plugin_info


def find_executable(opts, settings=None, env=None, exists=os.path.isfile):
    """The AYON launcher console executable: the action's ``ayon_executable``, else
    ``ayon_console`` in tinylib.ini [tools], else beside AYON_EXECUTABLE (set when the
    AYON launcher started the host). ayon_console is preferred over ayon.exe, which has
    no console output on Windows."""
    env = os.environ if env is None else env
    candidates = [opts.get('ayon_executable'), ((settings or {}).get('tools') or {}).get('ayon_console')]
    launcher = env.get('AYON_EXECUTABLE')
    if launcher:
        folder = os.path.dirname(launcher)
        console = 'ayon_console.exe' if launcher.lower().endswith('.exe') else 'ayon_console'
        candidates += [os.path.join(folder, console), launcher]
    for candidate in candidates:
        if candidate and exists(os.path.expandvars(candidate)):
            return os.path.expandvars(candidate)
    raise FileNotFoundError(
        'The AYON launcher was not found. Start the host from AYON, or set ayon_console in the '
        '[tools] section of tinylib.ini (e.g. C:/Program Files/Ynput/AYON 1.6.3/ayon_console.exe).')


def command(executable, csv_file, project, folder_path, task, opts):
    folder = opts.get('csv_folder_path') or folder_path
    task_name = opts.get('csv_task') or (task if not opts.get('csv_folder_path') else '')
    args = [executable, 'addon', 'traypublisher', 'ingestcsv', '--filepath', str(csv_file),
            '--project', project, '--folder-path', folder]
    if task_name:
        args += ['--task', task_name]
    args += ['--preset', opts['preset']]
    if opts.get('ignore_validators'):
        args.append('--ignore-validators')
    return args


def environment(credentials, opts, base=None):
    """The publish process environment: the host's, without the host's own Python and AYON
    context, with the AYON (and when signed in, ftrack) credentials."""
    env = dict(os.environ if base is None else base)
    for name in opts.get('strip_environment') or []:
        env.pop(name, None)
    env['AYON_SERVER_URL'] = credentials['ayon_url']
    env['AYON_API_KEY'] = credentials['ayon_key']
    if credentials.get('ftrack_url') and credentials.get('ftrack_user') and credentials.get('ftrack_key'):
        env['FTRACK_SERVER'] = credentials['ftrack_url']
        env['FTRACK_API_USER'] = credentials['ftrack_user']
        env['FTRACK_API_KEY'] = credentials['ftrack_key']
    return env


def redact(args):
    """The command as text for the log."""
    return ' '.join('"%s"' % arg if ' ' in arg else arg for arg in args)
