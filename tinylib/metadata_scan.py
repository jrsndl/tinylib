"""Media metadata probing and discrepancy comparison for library maintenance."""
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .asset_types import extension_groups
from .media_probe import PROBE_TIMEOUT, number, pixel_aspect, run_tool
from .ingest import clean_asset_name
from .library import atomic_json, frame_path

DATABASE_METADATA_FIELDS = ('frame_rate', 'width', 'height')


def _run(args):
    return run_tool(args, timeout=PROBE_TIMEOUT).stdout


# Kept as module names because other modules and tests use them.
_number = number
_pixel_aspect = pixel_aspect


def _first(mapping, names):
    lowered = {str(key).casefold(): value for key, value in mapping.items()}
    for name in names:
        if name.casefold() in lowered and lowered[name.casefold()] not in (None, ''):
            return lowered[name.casefold()]
    return None


def _probe_image(path, oiiotool):
    output = _run([oiiotool, '--info:format=xml:verbose=1', path])
    spec = ET.fromstring(output.strip())
    attributes = {item.get('name', ''): item.text or '' for item in spec.findall('.//attrib')}
    result = {'width': int(spec.findtext('width')), 'height': int(spec.findtext('height')),
              'pixel_aspect': _pixel_aspect(_first(attributes, ('PixelAspectRatio', 'pixelAspectRatio')) or 1)}
    timecode = _first(attributes, ('smpte:TimeCode', 'timecode', 'TimeCode'))
    reelid = _first(attributes, ('reelid', 'ReelName', 'reel_name', 'oiio:subimagename'))
    frame_rate = _number(_first(attributes, ('FramesPerSecond', 'frame_rate', 'framerate', 'fps')))
    if timecode is not None:
        result['timecode'] = str(timecode)
    if reelid is not None:
        result['reelid'] = str(reelid)
    if frame_rate:
        result['frame_rate'] = frame_rate
    return result, {'format': 'oiiotool_xml', 'xml': output, 'attributes': attributes}


def probe_image(path, oiiotool):
    return _probe_image(path, oiiotool)[0]


def _probe_container(path, ffprobe):
    output = _run([ffprobe, '-v', 'error', '-select_streams', 'v:0',
                   '-show_streams', '-show_format', '-of', 'json', path])
    payload = json.loads(output)
    streams = payload.get('streams', [])
    if not streams:
        raise ValueError('No readable video stream: ' + str(path))
    stream = streams[0]
    format_info = payload.get('format', {})
    tags = dict(format_info.get('tags') or {})
    tags.update(stream.get('tags') or {})
    fps = _number(stream.get('avg_frame_rate')) or _number(stream.get('r_frame_rate'))
    duration = _number(stream.get('duration')) or _number(format_info.get('duration'))
    frames = int(stream['nb_frames']) if str(stream.get('nb_frames', '')).isdigit() else None
    if frames is None and duration is not None and fps:
        frames = int(round(duration * fps))
    result = {'width': int(stream['width']), 'height': int(stream['height']),
              'pixel_aspect': _pixel_aspect(stream.get('sample_aspect_ratio', '1:1'))}
    if fps:
        result['frame_rate'] = fps
    if frames is not None:
        result['duration_frames'] = frames
    if duration is not None:
        result['duration_seconds'] = duration
    timecode = _first(tags, ('timecode', 'TIMECODE'))
    reelid = _first(tags, ('reel_name', 'reelid', 'REEL'))
    if timecode:
        result['timecode'] = str(timecode)
    if reelid:
        result['reelid'] = str(reelid)
    return result, {'format': 'ffprobe_json', 'payload': payload}


def probe_container(path, ffprobe):
    return _probe_container(path, ffprobe)[0]


def scan_source_metadata(value, settings):
    """Probe an ingest footage source without writing metadata sidecars."""
    from .ingest import prepare_source
    from .library import split_sequence, sequence_files
    source = prepare_source(value, 'footage', settings)
    pattern, first, last = split_sequence(source)
    files = sequence_files(source)
    sample = files[0]
    tools = settings.get('tools', {})
    containers = extension_groups(settings)['extensions_footage_containers']
    container = sample.suffix.casefold().lstrip('.') in containers
    metadata = (probe_container(sample, tools.get('ffprobe', 'ffprobe')) if container else
                probe_image(sample, tools.get('oiiotool', 'oiiotool')))
    if first is not None:
        metadata.update(frame_start=first, frame_end=last, duration_frames=last - first + 1)
        if metadata.get('frame_rate'):
            metadata['duration_seconds'] = metadata['duration_frames'] / metadata['frame_rate']
    elif metadata.get('duration_frames') is not None:
        metadata.update(frame_start=0, frame_end=metadata['duration_frames'] - 1)
    metadata['is_raw'] = sample.suffix.casefold().lstrip('.') in extension_groups(settings)['extensions_stills_raw']
    metadata['file_size_main'] = sum(path.stat().st_size for path in files)
    metadata['file_size_total'] = metadata['file_size_main']
    return metadata


def scan_still_metadata(path, settings):
    """Probe one still image for ingest. A still oiiotool cannot read keeps the defaults;
    the farm worker measures it again when it publishes."""
    sample = Path(path)
    tools = settings.get('tools', {})
    try:
        metadata = probe_image(sample, tools.get('oiiotool', 'oiiotool'))
    except Exception:
        metadata = {}
    metadata.pop('frame_rate', None)
    metadata['is_raw'] = sample.suffix.casefold().lstrip('.') in extension_groups(settings)['extensions_stills_raw']
    metadata['file_size_main'] = sample.stat().st_size
    metadata['file_size_total'] = metadata['file_size_main']
    return metadata


def _tree_size(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())


def scan_asset_metadata(asset, settings, include_full=False):
    kind = asset.get('kind')
    if kind not in ('still', 'footage'):
        raise ValueError('Metadata rescan supports Still and Footage assets.')
    main = str(asset.get('main', ''))
    if not main:
        raise ValueError('Asset has no main representation.')
    first, last = asset.get('first'), asset.get('last')
    sequence = bool(re.search(r'#+|%0?\d*d', main))
    sample = Path(frame_path(main, first)) if sequence and type(first) is int else Path(main)
    if not sample.is_file():
        raise ValueError('Main media does not exist: ' + str(sample))
    groups = extension_groups(settings)
    containers = {'.' + value for value in groups['extensions_footage_containers']}
    container = sample.suffix.casefold() in containers
    tools = settings.get('tools', {})
    scanned, raw = (_probe_container(sample, tools.get('ffprobe', 'ffprobe')) if container else
                    _probe_image(sample, tools.get('oiiotool', 'oiiotool')))
    if kind == 'footage':
        if sequence and type(first) is int and type(last) is int:
            frames = last - first + 1
            scanned.update(duration_frames=frames, frame_start=first, frame_end=last)
            if scanned.get('frame_rate'):
                scanned['duration_seconds'] = frames / scanned['frame_rate']
        elif container and scanned.get('duration_frames') is not None:
            start = first if type(first) is int else 0
            scanned.update(frame_start=start, frame_end=start + scanned['duration_frames'] - 1)
    scanned['is_raw'] = sample.suffix.casefold().lstrip('.') in groups['extensions_stills_raw']
    if sequence and type(first) is int and type(last) is int:
        files = [Path(frame_path(main, frame)) for frame in range(first, last + 1)]
        scanned['file_size_main'] = sum(path.stat().st_size for path in files if path.is_file())
    else:
        scanned['file_size_main'] = sample.stat().st_size
    asset_folder = Path(asset['library_root']) / asset['category'] / asset['name']
    scanned['file_size_total'] = _tree_size(asset_folder) if asset_folder.exists() else scanned['file_size_main']
    full = {'metadata_scan_version': 1, 'scanned_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'asset': '%s/%s' % (asset.get('category', ''), asset.get('name', '')),
            'main': main, 'sample': str(sample), 'tool': raw, 'normalized': scanned}
    return (scanned, full) if include_full else scanned


def metadata_sidecar_path(asset):
    main = str(asset['main'])
    sequence = bool(re.search(r'#+|%0?\d*d', main))
    source = main
    if sequence and type(asset.get('first')) is int and type(asset.get('last')) is int:
        source += ' %d-%d' % (asset['first'], asset['last'])
    # Beside the representation folders, never inside main/: main must hold only the source media.
    folder = Path(main).parent
    if folder.name.casefold() == 'main':
        folder = folder.parent
    return folder / (clean_asset_name(source) + '_meta.json')


def values_equal(stored, scanned):
    if type(stored) in (int, float) and type(scanned) in (int, float):
        return math.isclose(float(stored), float(scanned), rel_tol=1e-6, abs_tol=1e-6)
    return stored == scanned


def scan_assets(assets, settings, progress=None, cancelled=None):
    results, errors, completed = [], [], []
    total = len(assets)
    total_work = total * 2
    for index, asset in enumerate(assets, 1):
        if cancelled and cancelled():
            raise RuntimeError('Metadata rescan cancelled.')
        label = '%s / %s' % (asset.get('category', ''), asset.get('name', ''))
        if progress:
            progress(index - 1, total_work, 'Scanning ' + label)
        try:
            scanned, full = scan_asset_metadata(asset, settings, include_full=True)
            stored = asset.get('metadata', {})
            database_values = {key: scanned[key] for key in DATABASE_METADATA_FIELDS if key in scanned}
            differences = {key: {'stored': stored.get(key), 'scanned': value}
                           for key, value in database_values.items()
                           if key not in stored or not values_equal(stored.get(key), value)}
            if differences:
                results.append({'asset': asset, 'scanned': database_values, 'differences': differences})
            completed.append((asset, full))
        except Exception as error:
            errors.append({'asset': asset, 'error': str(error)})
        if progress:
            progress(index, total_work, 'Checked ' + label)
    sidecars = []
    if cancelled and cancelled():
        raise RuntimeError('Metadata rescan cancelled.')
    for index, (asset, full) in enumerate(completed, 1):
        if cancelled and cancelled():
            raise RuntimeError('Metadata rescan cancelled.')
        path = metadata_sidecar_path(asset)
        if progress:
            progress(total + index - 1, total_work, 'Writing metadata sidecar ' + str(path))
        try:
            atomic_json(path, full)
            sidecars.append(str(path))
        except Exception as error:
            errors.append({'asset': asset, 'error': 'Could not save metadata sidecar %s: %s' % (path, error)})
        if progress:
            progress(total + index, total_work, 'Wrote metadata sidecar ' + str(path))
    return {'results': results, 'errors': errors, 'scanned': total, 'sidecars': sidecars}


def format_scan_summary(scan):
    lines = ['METADATA DISCREPANCIES (%d assets)' % len(scan['results']), '=' * 72]
    if not scan['results']:
        lines.append('None')
    for result in scan['results']:
        asset = result['asset']
        lines.append('\n[%s/%s]' % (asset.get('category', ''), asset.get('name', '')))
        for field, values in sorted(result['differences'].items()):
            lines.append('  %s: %r  ->  %r' % (field, values['stored'], values['scanned']))
    lines.extend(['', 'METADATA SIDECARS WRITTEN (%d)' % len(scan.get('sidecars', [])), '=' * 72])
    lines.extend(scan.get('sidecars', []) or ['None'])
    lines.extend(['', 'SCAN ERRORS (%d assets)' % len(scan['errors']), '=' * 72])
    if not scan['errors']:
        lines.append('None')
    for error in scan['errors']:
        lines.append('[%s/%s] %s' % (error['asset'].get('category', ''),
                                     error['asset'].get('name', ''), error['error']))
    return '\n'.join(lines)
