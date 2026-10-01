"""Create reviewable job manifests and submit them to Deadline 10.4."""
import copy
import re
import subprocess
import uuid
from pathlib import Path
from .library import atomic_json, detect_sequence, safe_component, sequence_files, split_sequence
from .access import AccessControl
from .asset_types import (SCHEMA_VERSION, ASSET_TYPES, canonical_type, default_metadata,
                          extension_groups, representation, validate_metadata,
                          validate_representation, relative_paths)


def prepare_source(value, kind, settings=None):
    """Normalize a browsed/dropped main source and validate it immediately."""
    path = Path(value)
    kind = canonical_type(kind)
    if path.is_dir() and kind != 'folder':
        if kind != 'footage':
            raise ValueError('%s requires a file; choose Folder to ingest a directory.' % ASSET_TYPES[kind]['label'])
        allowed = representation('footage', 'main', settings)['extensions']
        files = sorted(item for item in path.iterdir()
                       if item.is_file() and item.suffix.lower().lstrip('.') in allowed)
        sequences, containers, singles = [], [], []
        for item in files:
            if item.suffix.lower().lstrip('.') in extension_groups(settings)['extensions_footage_containers']:
                candidates = containers
                candidate = str(item)
            else:
                candidate = detect_sequence(item)
                candidates = sequences if split_sequence(candidate)[1] is not None else singles
            if candidate not in candidates:
                candidates.append(candidate)
        candidates = sequences + containers if sequences or containers else singles
        if not candidates:
            raise ValueError('The folder contains no supported Footage files.')
        if len(candidates) != 1:
            raise ValueError('The folder contains multiple Footage sources; choose one file from the required sequence or container.')
        value = candidates[0]
    elif path.is_file() and kind == 'footage':
        extension = path.suffix.lower().lstrip('.')
        value = str(path) if extension in extension_groups(settings)['extensions_footage_containers'] else detect_sequence(path)
    validate_representation(kind, 'main', value, settings)
    return value


def clean_asset_name(source):
    pattern, first, _ = split_sequence(source)
    path = Path(pattern)
    name = path.name if path.is_dir() else path.stem
    if first is not None:
        name = re.sub(r'[._-]?(?:#+|%0?\d*d|\d+)$', '', name)
    name = re.sub(r'_?v\d{3}', '', name, flags=re.IGNORECASE)
    return name.strip('._- ') or 'asset'


def keywords_from_name(name):
    words = []
    for chunk in re.split(r'[._-]+', name):
        # Handles CamelCase, acronym-to-word boundaries, and numbers without losing text.
        parts = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', chunk)
        parts = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', parts).split()
        for part in parts:
            value = part.casefold()
            if value and value not in words:
                words.append(value)
    return words


def make_manifest(settings, library, name, category, source, tags, colorspace,
                  profile, media, fps=24.0, highres='', preview_source='main', kind='footage', metadata=None):
    access = settings.get('_access') or AccessControl(settings)
    access.refresh()
    access.require(library['root'], 'ingest')
    if library.get('read_only'):
        raise ValueError('This library is configured read-only.')
    name, category = safe_component(name), safe_component(category)
    root = Path(library['root'])
    if not root.is_dir():
        raise ValueError('Library root is unavailable: ' + str(root))
    if (root / category / name).exists():
        raise ValueError('Asset folder already exists; use a different name.')
    pattern, first, last = split_sequence(source)
    kind = canonical_type(kind)
    if kind not in ASSET_TYPES:
        raise ValueError('Unknown asset type: ' + kind)
    validate_representation(kind, 'main', source, settings)
    if kind == 'folder':
        files = []
    else:
        files = sequence_files(source)
    if kind == 'still' and first is not None:
        raise ValueError('A Still must contain one image; choose Footage for an image sequence.')
    if kind == 'footage':
        if not 0 < float(fps) <= 240:
            raise ValueError('FPS must be between 0 and 240.')
    if kind in ('still', 'footage') and not colorspace.strip():
        raise ValueError('Choose an input color space.')
    if preview_source not in ('main', 'proxy'):
        raise ValueError('Preview source must be main or proxy.')
    specs = ASSET_TYPES[kind]['representations']
    media = {key: copy.deepcopy(value) for key, value in media.items() if key in specs and key not in ('main', 'highres')}
    for key, (required, _) in specs.items():
        if key in ('main', 'highres'):
            continue
        if required and key not in media:
            raise ValueError(key + ' is required for ' + ASSET_TYPES[kind]['label'] + '.')
    for key, value in media.items():
        if value.get('mode') == 'omit' and not representation(kind, key, settings)['required']:
            continue
        if value.get('mode') not in ('generate', 'supply'):
            raise ValueError('Choose supply, generate, or omit for ' + key)
        if value['mode'] == 'supply':
            path = Path(value.get('path', ''))
            validate_representation(kind, key, str(path), settings)
            value['path'] = str(path.resolve())
        elif kind not in ('still', 'footage'):
            raise ValueError('Generate is only available for Still and Footage previews; supply ' + key + '.')
    if highres:
        validate_representation(kind, 'highres', highres, settings)
        if kind != 'folder':
            sequence_files(highres)
        high_pattern, high_first, high_last = split_sequence(highres)
        highres = str(Path(high_pattern).resolve()) + (' %s-%s' % (high_first, high_last) if high_first is not None else '')
    if any(v.get('mode') == 'generate' for v in media.values()):
        if profile not in settings.get('profiles', {}):
            raise ValueError('Choose a configured processing profile.')
    metadata = dict(default_metadata(kind), **copy.deepcopy(metadata or {}))
    metadata['colorspace'] = colorspace.strip() if kind in ('still', 'footage') else metadata.get('colorspace', '')
    if kind == 'footage':
        metadata['frame_rate'] = float(fps)
    validate_metadata(kind, metadata, complete=True)
    if kind == 'model':
        relative_paths(metadata['textures'])
    if kind == 'folder':
        relative_paths(metadata['files'])
    return {
        'manifest_version': 1, 'asset_type_schema_version': SCHEMA_VERSION, 'job_id': uuid.uuid4().hex,
        'library': copy.deepcopy(library), 'name': name, 'category': category,
        'source': str(Path(pattern).resolve()) + (' %s-%s' % (first, last) if first is not None else ''),
        'kind': kind, 'tags': sorted(set(t.strip().lower() for t in tags if t.strip())),
        'colorspace': colorspace.strip(), 'fps': float(fps), 'highres': highres, 'metadata': metadata,
        'extension_groups': extension_groups(settings),
        'media': media, 'preview_source': preview_source,
        'profile': copy.deepcopy(settings.get('profiles', {}).get(profile, {})),
        'tools': copy.deepcopy(settings.get('tools', {}))
    }


def save_manifest(manifest, folder):
    path = Path(folder) / (manifest['job_id'] + '.json')
    atomic_json(path, manifest)
    return path


def submit_deadline(manifest, settings):
    access = settings.get('_access') or AccessControl(settings)
    access.refresh()
    access.require(manifest['library']['root'], 'ingest')
    config = settings.get('deadline', {})
    script = config.get('worker_script', '')
    spool = config.get('spool_root', '')
    if not script or not Path(script).is_file() or not spool or not Path(spool).is_dir():
        raise ValueError('Set Deadline worker_script and spool_root to existing farm-accessible paths in studio.json.')
    # Reject line injection into Deadline's key=value files.
    for value in (script, spool, manifest['name'], config.get('pool', ''), config.get('group', ''), config.get('python_version', '3.10')):
        if '\n' in str(value) or '\r' in str(value) or '"' in str(value):
            raise ValueError('Invalid character in Deadline configuration.')
    path = save_manifest(manifest, spool)
    job = path.with_suffix('.job')
    plugin = path.with_suffix('.plugin')
    job.write_text('\n'.join([
        'Plugin=Python', 'Name=TinyLib ingest: ' + manifest['name'], 'Frames=0', 'ChunkSize=1',
        'Pool=' + config.get('pool', 'none'), 'Group=' + config.get('group', 'none'),
        'Priority=' + str(int(config.get('priority', 50))),
        'Comment=Manifest: ' + str(path), '']), encoding='utf-8')
    plugin.write_text('\n'.join([
        'Version=' + config.get('python_version', '3.10'), 'ScriptFile=' + script,
        'Arguments="' + str(path) + '"', 'SingleFramesOnly=False', '']), encoding='utf-8')
    result = subprocess.run([config.get('command') or 'deadlinecommand', str(job), str(plugin)],
                            capture_output=True, text=True, timeout=60)
    output = result.stdout + result.stderr
    match = re.search(r'JobID=([^\s]+)', output)
    if result.returncode or not match:
        raise RuntimeError('Deadline did not confirm submission. Inspect Monitor before retrying.\n' + output)
    atomic_json(path.with_suffix('.submission.json'), {'deadline_job_id': match[1], 'manifest': str(path)})
    return match[1], path
