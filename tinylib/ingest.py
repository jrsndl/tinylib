"""Create reviewable job manifests and submit them to Deadline 10.4."""
import copy
import re
import subprocess
import uuid
from pathlib import Path
from .library import atomic_json, safe_component, sequence_files, split_sequence


def make_manifest(settings, library, name, category, source, tags, colorspace,
                  profile, media, fps=24.0, highres='', preview_source='main', kind='auto'):
    if library.get('read_only'):
        raise ValueError('This library is configured read-only.')
    name, category = safe_component(name), safe_component(category)
    root = Path(library['root'])
    if not root.is_dir():
        raise ValueError('Library root is unavailable: ' + str(root))
    if (root / category / name).exists():
        raise ValueError('Asset folder already exists; use a different name.')
    files = sequence_files(source)
    if any(p.suffix.lower() not in {'.exr', '.hdr', '.tif', '.tiff', '.dpx', '.png', '.jpg', '.jpeg'} for p in files):
        raise ValueError('Main must be an image or image sequence.')
    pattern, first, last = split_sequence(source)
    detected_kind = 'footage' if first is not None else 'still'
    kind = detected_kind if kind == 'auto' else kind
    if kind not in ('still', 'hdri', 'footage') or (kind == 'footage') != (detected_kind == 'footage'):
        raise ValueError('Footage requires an explicit sequence range; still/HDRI requires a single file.')
    if not 0 < float(fps) <= 240:
        raise ValueError('FPS must be between 0 and 240.')
    if not colorspace.strip():
        raise ValueError('Choose an input color space.')
    if preview_source not in ('main', 'proxy'):
        raise ValueError('Preview source must be main or proxy.')
    required = ['thumb', 'proxy'] + (['filmstrip'] if kind == 'footage' else [])
    media = {key: copy.deepcopy(media[key]) for key in required}
    for key, value in media.items():
        if value['mode'] not in ('generate', 'supply'):
            raise ValueError('Choose supply or generate for ' + key)
        if value['mode'] == 'supply':
            path = Path(value.get('path', ''))
            allowed = {'.mp4'} if key == 'proxy' and kind == 'footage' else {'.jpg', '.jpeg'}
            if not path.is_file() or path.suffix.lower() not in allowed:
                raise ValueError('Supply ' + key + ' as ' + ', '.join(sorted(allowed)))
            value['path'] = str(path.resolve())
    if highres:
        sequence_files(highres)
        high_pattern, high_first, high_last = split_sequence(highres)
        highres = str(Path(high_pattern).resolve()) + (' %s-%s' % (high_first, high_last) if high_first is not None else '')
    if any(v['mode'] == 'generate' for v in media.values()):
        if profile not in settings.get('profiles', {}):
            raise ValueError('Choose a configured processing profile.')
    return {
        'manifest_version': 1, 'job_id': uuid.uuid4().hex,
        'library': copy.deepcopy(library), 'name': name, 'category': category,
        'source': str(Path(pattern).resolve()) + (' %s-%s' % (first, last) if first is not None else ''),
        'kind': kind, 'tags': sorted(set(t.strip().lower() for t in tags if t.strip())),
        'colorspace': colorspace.strip(), 'fps': float(fps), 'highres': highres,
        'media': media, 'preview_source': preview_source,
        'profile': copy.deepcopy(settings.get('profiles', {}).get(profile, {})),
        'tools': copy.deepcopy(settings.get('tools', {}))
    }


def save_manifest(manifest, folder):
    path = Path(folder) / (manifest['job_id'] + '.json')
    atomic_json(path, manifest)
    return path


def submit_deadline(manifest, settings):
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
