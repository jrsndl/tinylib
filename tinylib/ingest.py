"""Create reviewable job manifests and submit them to Deadline 10.4."""
import copy
import datetime
import re
import os
import queue
import threading
import time
import tempfile
import subprocess
import uuid
from pathlib import Path
from .library import atomic_json, detect_sequence, safe_component, sequence_files, split_sequence
from .access import AccessControl
from .asset_types import (SCHEMA_VERSION, ASSET_TYPES, canonical_type, default_metadata,
                          extension_groups, representation, validate_metadata,
                          validate_representation, relative_paths)


class MultipleFootageSources(ValueError):
    def __init__(self, sources):
        self.sources = tuple(sources)
        super().__init__('The folder contains multiple Footage sources; choose one file from the required sequence or container.')


def discover_footage_sources(folder, settings=None, include_singles=False):
    """Find each sequence/container once, recursively, without scanning per frame."""
    path = Path(folder)
    if not path.is_dir():
        raise ValueError('Source folder is unavailable: ' + str(folder))
    allowed = representation('footage', 'main', settings)['extensions']
    files = sorted(item for item in path.rglob('*')
                   if item.is_file() and item.suffix.lower().lstrip('.') in allowed)
    sequences, containers, singles = [], [], []
    container_extensions = extension_groups(settings)['extensions_footage_containers']
    image_groups = {}
    for item in files:
        if item.suffix.lower().lstrip('.') in container_extensions:
            containers.append(str(item))
            continue
        match = re.match(r'^(.*?)(\d+)(\.[^.]+)$', item.name)
        if not match:
            singles.append(str(item))
            continue
        key = (item.parent, match[1], len(match[2]), match[3])
        image_groups.setdefault(key, []).append((int(match[2]), item))
    for (parent, prefix, padding, suffix), members in image_groups.items():
        if len(members) < 2:
            singles.append(str(members[0][1]))
            continue
        frames = sorted(frame for frame, _ in members)
        if frames != list(range(frames[0], frames[-1] + 1)):
            raise ValueError('Sequence has missing frames in %s; supply an explicit contiguous range.' % parent)
        pattern = parent / (prefix + '#' * padding + suffix)
        sequences.append('%s %s-%s' % (pattern, frames[0], frames[-1]))
    candidates = sequences + containers
    if not candidates and include_singles:
        candidates = singles
    return candidates


def prepare_source(value, kind, settings=None):
    """Normalize a browsed/dropped main source and validate it immediately."""
    path = Path(value)
    kind = canonical_type(kind)
    if path.is_dir() and kind != 'folder':
        if kind != 'footage':
            raise ValueError('%s requires a file; choose Folder to ingest a directory.' % ASSET_TYPES[kind]['label'])
        candidates = discover_footage_sources(path, settings, include_singles=True)
        if not candidates:
            raise ValueError('The folder contains no supported Footage files.')
        if len(candidates) != 1:
            raise MultipleFootageSources(candidates)
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
        'ingest_user': access.identity, 'ingest_date': datetime.datetime.now().strftime('%Y%m%d'),
        'extension_groups': extension_groups(settings),
        'media': media, 'preview_source': preview_source,
        'profile': copy.deepcopy(settings.get('profiles', {}).get(profile, {})),
        'tools': copy.deepcopy(settings.get('tools', {}))
    }


def make_folder_manifests(settings, library, category, folder, colorspace, profile,
                          preview_source='main', progress=None):
    """Prepare all folder jobs before submitting any of them."""
    from .metadata_scan import scan_source_metadata
    report = progress or (lambda message: None)
    report('Discovering footage sequences and containers...')
    sources = discover_footage_sources(folder, settings)
    if not sources:
        raise ValueError('The folder contains no supported footage sequences or containers.')
    names = {}
    for source in sources:
        name = clean_asset_name(source)
        if name.casefold() in names:
            raise ValueError('Duplicate automatic asset name %s for %s and %s. '
                             'Rename one source before submitting the folder.' % (name, names[name.casefold()], source))
        names[name.casefold()] = source
    jobs = []
    for index, source in enumerate(sources, 1):
        name = clean_asset_name(source)
        report('Scanning metadata %d/%d: %s' % (index, len(sources), name))
        try:
            metadata = scan_source_metadata(source, settings)
            fps = metadata.get('frame_rate')
            if fps is None or not 0 < float(fps) <= 240:
                raise ValueError('No valid FPS in source metadata. Folder ingest requires embedded FPS.')
            jobs.append(make_manifest(settings, library, name, category, source, keywords_from_name(name),
                                      colorspace, profile,
                                      {key: {'mode': 'generate', 'path': ''}
                                       for key in ('proxy', 'thumb', 'filmstrip')},
                                      fps=fps, highres='', preview_source=preview_source,
                                      kind='footage', metadata=metadata))
        except Exception as error:
            raise ValueError('Cannot prepare %s (%s): %s. No folder jobs have been submitted.'
                             % (name, source, error)) from error
    return jobs


def save_manifest(manifest, folder):
    path = Path(folder) / (manifest['job_id'] + '.json')
    atomic_json(path, manifest)
    return path


def _deadline_confirmation(command, report, timeout=60):
    """Read confirmation live; Deadline may take time to exit after accepting a job."""
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors='replace',
                               creationflags=0x08000000 if os.name == 'nt' else 0)
    events = queue.Queue()

    def read_output():
        try:
            for line in process.stdout:
                events.put(line)
        finally:
            process.stdout.close()
            events.put(None)
            process.wait()

    # Keep draining/reaping the process if success arrives before process exit.
    threading.Thread(target=read_output, daemon=True).start()
    output, job_id, success, latest = [], None, False, 'Connecting to Deadline'
    next_update = 0
    while True:
        elapsed = time.monotonic() - started
        try:
            line = events.get(timeout=.25)
        except queue.Empty:
            line = ''
        if line is None:
            code = process.wait(timeout=max(1, timeout - elapsed))
            if not code and job_id:
                return job_id, ''.join(output), elapsed
            raise RuntimeError('Deadline did not confirm submission. Inspect Monitor before retrying.\n' + ''.join(output))
        if line:
            output.append(line)
            latest = line.strip() or latest
            match = re.search(r'JobID=([^\s]+)', line)
            if match:
                job_id = match[1]
            success = success or bool(re.search(r'Result=Success\b', line, re.IGNORECASE))
            if job_id and success:
                return job_id, ''.join(output), time.monotonic() - started
        if elapsed >= next_update:
            report('Deadline submission (%.0f s): %s' % (elapsed, latest))
            next_update = elapsed + 1
        if elapsed >= timeout:
            if process.poll() is None:
                process.kill()
            raise RuntimeError('Deadline did not confirm submission within %g seconds. '
                               'The job may already exist; inspect Monitor before retrying.\n%s'
                               % (timeout, ''.join(output)))


def submit_deadline(manifest, settings, progress=None):
    report = progress or (lambda message: None)
    report('Checking ingest permissions...')
    access = settings.get('_access') or AccessControl(settings)
    access.refresh()
    access.require(manifest['library']['root'], 'ingest')
    report('Checking Deadline worker and spool paths...')
    config = settings.get('deadline', {})
    script = config.get('worker_script', '')
    spool = config.get('spool_root', '')
    if not script or not Path(script).is_file() or not spool or not Path(spool).is_dir():
        raise ValueError('Set Deadline worker_script and spool_root to existing farm-accessible paths in studio.json.')
    # Reject line injection into Deadline's key=value files.
    for value in (script, spool, manifest['name'], config.get('pool', ''), config.get('group', ''), config.get('python_version', '3.10')):
        if '\n' in str(value) or '\r' in str(value) or '"' in str(value):
            raise ValueError('Invalid character in Deadline configuration.')
    report('Writing job files to the Deadline spool...')
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
    report('Waiting for Deadline to confirm submission...')
    # Deadline only needs these tiny descriptor files locally. The manifest stays
    # on the shared spool so farm workers can read it.
    with tempfile.TemporaryDirectory(prefix='tinylib-submit-') as local:
        local_job = Path(local) / job.name
        local_plugin = Path(local) / plugin.name
        local_job.write_bytes(job.read_bytes())
        local_plugin.write_bytes(plugin.read_bytes())
        identifier, output, seconds = _deadline_confirmation(
            [config.get('command') or 'deadlinecommand', str(local_job), str(local_plugin)],
            report, timeout=60)
    report('Saving Deadline confirmation...')
    atomic_json(path.with_suffix('.submission.json'), {'deadline_job_id': identifier, 'manifest': str(path),
                                                      'confirmation_seconds': seconds, 'output': output})
    return identifier, path
