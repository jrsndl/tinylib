"""Farm worker. All outputs are staged and verified before library publication."""
import json
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from .library import Library, atomic_json, read_json, safe_component, sequence_files, split_sequence
from .asset_types import extension_groups, validate_metadata


def run(command):
    command = [str(v) for v in command]
    print(subprocess.list2cmdline(command), flush=True)
    result = subprocess.run(command, capture_output=True, text=True, errors='replace')
    if result.returncode:
        raise RuntimeError('Conversion failed:\n' + result.stderr[-8000:] + result.stdout[-2000:])
    # Old OIIO versions can report failed color conversion as a warning with exit 0.
    if 'warning' in result.stderr.lower() and any(word in result.stderr.lower() for word in ('color', 'ocio')):
        raise RuntimeError('Color conversion warning: ' + result.stderr)
    return result.stdout


def sample_indices(count, samples=24):
    if count < 1:
        raise ValueError('Cannot sample empty media.')
    return [round(i * (count - 1) / (samples - 1)) for i in range(samples)]


def probe(path, tools, count=False):
    args = [tools.get('ffprobe', 'ffprobe'), '-v', 'error', '-select_streams', 'v:0']
    if count:
        args += ['-count_frames']
    args += ['-show_streams', '-of', 'json', str(path)]
    streams = json.loads(run(args)).get('streams', [])
    if not streams:
        raise ValueError('No readable image/video stream: ' + str(path))
    return streams[0]


def fit_filter(width, height):
    return ('scale=%d:%d:force_original_aspect_ratio=decrease,'
            'pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1' % (width, height, width, height))


class Processor:
    def __init__(self, manifest, work):
        self.job = manifest
        self.work = Path(work)
        self.tools = manifest['tools']
        self.profile = manifest['profile']
        self.ffmpeg = self.tools.get('ffmpeg', 'ffmpeg')
        self.cache = {}

    def image(self, source, target, size, convert=True):
        if convert:
            ocio = self.profile.get('ocio_config', '')
            if not ocio or not Path(ocio).is_file():
                raise ValueError('Processing profile requires an accessible OCIO configuration: ' + ocio)
            values = dict(input_colorspace=self.job['colorspace'],
                          output_colorspace=self.profile.get('output_colorspace', 'Output - Rec.709'))
            color_args = self.profile.get('color_args', ['--colorconvert', '{input_colorspace}', '{output_colorspace}'])
            if not color_args:
                raise ValueError('Profile has no color transform.')
            args = [self.tools.get('oiiotool', 'oiiotool'), '--colorconfig', ocio, str(source)]
            args += [arg.format(**values) for arg in color_args]
            args += ['--ch', 'R,G,B', '--fit:pad=1', '%dx%d' % size,
                     '-d', 'uint8', '-o', str(target)]
            run(args)
        else:
            run([self.ffmpeg, '-y', '-v', 'error', '-i', source,
                 '-vf', fit_filter(*size), '-frames:v', '1', '-q:v', '2', target])

    def proxy_frame(self, proxy, index, size, target):
        if self.job['kind'] != 'footage':
            return self.image(proxy, target, size, convert=False)
        run([self.ffmpeg, '-y', '-v', 'error', '-i', proxy,
             '-vf', 'select=eq(n\\,%d),' % index + fit_filter(*size),
             '-frames:v', '1', '-q:v', '2', target])
        if not Path(target).is_file():
            raise ValueError('Proxy does not contain requested frame %d' % index)

    def generate(self, sources, outputs):
        job = self.job
        media = job['media']
        footage = job['kind'] == 'footage'
        groups = extension_groups({'extension_groups': job.get('extension_groups', {})})
        containers = {'.' + item for item in groups.get('extensions_footage_containers', [])}
        container = footage and len(sources) == 1 and sources[0].suffix.lower() in containers
        proxy = outputs.get('proxy')
        if proxy and media['proxy']['mode'] == 'generate':
            if container:
                run([self.ffmpeg, '-y', '-v', 'error', '-i', sources[0], '-vf', fit_filter(1920, 1080),
                     '-c:v', 'libx264', '-crf', '23', '-preset', 'faster', '-pix_fmt', 'yuv420p',
                     '-movflags', '+faststart', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                     '-colorspace', 'bt709', proxy])
            elif footage and self.profile.get('proxy_backend') == 'vfx_transcode':
                ocio = self.profile.get('ocio_config', '')
                if not Path(ocio).is_file():
                    raise ValueError('OCIO config is unavailable: ' + ocio)
                rate = Fraction(str(job['fps'])).limit_denominator(1001)
                rate = str(rate.numerator) if rate.denominator == 1 else '%s/%s' % (rate.numerator, rate.denominator)
                run([self.tools['vfx_transcode'], '-i', sources[0], '-r', rate,
                     '-ci', job['colorspace'], '-co', self.profile['output_colorspace'],
                     '-ocio', ocio, '-oiio', self.tools['oiiotool'], '-ffmpeg', self.ffmpeg,
                     '-width', '1920', '-mezzanine', 'jpg', '-temp', '_vfx_temp',
                     '-ff_filter', fit_filter(1920, 1080),
                     '-ff_encode', '-c:v libx264 -crf 23 -pix_fmt yuv420p -movflags +faststart -color_primaries bt709 -color_trc bt709 -colorspace bt709',
                     '-o', proxy])
            elif footage:
                frames = self.work / 'proxy_frames'
                frames.mkdir()
                for index, source in enumerate(sources):
                    self.image(source, frames / ('%08d.jpg' % index), (1920, 1080))
                run([self.ffmpeg, '-y', '-v', 'error', '-framerate', job['fps'],
                     '-start_number', '0', '-i', str(frames / '%08d.jpg'),
                     '-frames:v', len(sources), '-c:v', 'libx264', '-crf', '23',
                     '-preset', 'faster', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                     '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', proxy])
            else:
                self.image(sources[0], proxy, (1920, 1080))
        if outputs.get('thumb') and media['thumb']['mode'] == 'generate':
            if self.profile.get('thumbnail_frame', 'largest') == 'largest':
                chosen = max(range(len(sources)), key=lambda i: sources[i].stat().st_size)
            else:
                chosen = (len(sources) - 1) // 2
            if job['preview_source'] == 'proxy' and proxy:
                self.proxy_frame(proxy, chosen, (960, 506), outputs['thumb'])
            elif container:
                self.image(sources[0], outputs['thumb'], (960, 506), convert=False)
            else:
                self.image(sources[chosen], outputs['thumb'], (960, 506))
        if footage and outputs.get('filmstrip') and media['filmstrip']['mode'] == 'generate':
            if container:
                info = probe(sources[0], self.tools)
                duration = max(float(info.get('duration') or 1), .001)
                run([self.ffmpeg, '-y', '-v', 'error', '-i', sources[0], '-vf',
                     'fps=24/%s,%s,tile=24x1' % (duration, fit_filter(480, 270)),
                     '-frames:v', '1', '-q:v', '2', outputs['filmstrip']])
                return
            frames = self.work / 'strip_frames'
            frames.mkdir()
            for index, frame in enumerate(sample_indices(len(sources))):
                target = frames / ('%02d.jpg' % index)
                if job['preview_source'] == 'proxy':
                    self.proxy_frame(proxy, frame, (480, 270), target)
                else:
                    self.image(sources[frame], target, (480, 270))
            # A numbered intermediate sequence avoids the old Windows command length issue.
            run([self.ffmpeg, '-y', '-v', 'error', '-start_number', '0',
                 '-i', str(frames / '%02d.jpg'), '-vf', 'tile=24x1', '-frames:v', '1',
                 '-q:v', '2', outputs['filmstrip']])


def validate_media(outputs, manifest):
    if manifest['kind'] not in ('still', 'footage'):
        return
    sizes = {'thumb': (960, 506), 'proxy': (1920, 1080), 'filmstrip': (11520, 270)}
    for key, path in outputs.items():
        if key not in sizes:
            continue
        info = probe(path, manifest['tools'], count=key == 'proxy' and manifest['kind'] == 'footage')
        if (int(info['width']), int(info['height'])) != sizes[key]:
            raise ValueError('%s must be %sx%s; got %sx%s' % (key, *sizes[key], info['width'], info['height']))
        if key == 'proxy' and manifest['kind'] == 'footage':
            _, first, last = split_sequence(manifest['source'])
            actual = int(info.get('nb_read_frames', 0))
            if first is not None and actual != last - first + 1:
                raise ValueError('Proxy frame count differs from main: %s vs %s' % (actual, last-first+1))
            if abs(float(Fraction(info['avg_frame_rate'])) - manifest['fps']) > .01:
                raise ValueError('Proxy frame rate differs from ingest FPS.')


def process(manifest_path, access=None):
    job = read_json(manifest_path)
    if job.get('manifest_version') != 1:
        raise ValueError('Unsupported manifest version.')
    root = Path(job['library']['root'])
    if access is None:
        from .access import AccessControl
        from .settings import load_settings
        access = AccessControl(load_settings())
    access.refresh()
    access.require(root, 'ingest')
    if job['library'].get('read_only'):
        raise ValueError('Library is read-only.')
    name = safe_component(job['name'])
    category = safe_component(job['category'])
    identifier = safe_component(job['job_id'])
    library = Library(root, job['library'].get('name'), job['library'].get('legacy_roots', []))
    library.load()
    # Deadline retry after a completed publish is a no-op.
    completed = next((a for a in library.assets if a.get('ingest_job_id') == identifier), None)
    status_path = Path(manifest_path).with_suffix('.status.json')
    if completed:
        atomic_json(status_path, {'state': 'complete', 'asset': completed['id']})
        return completed
    staging_parent = root / '.tinylib-staging'
    staging_parent.mkdir(exist_ok=True)
    stage = staging_parent / identifier
    if stage.exists():
        raise ValueError('Staging exists from an earlier attempt; inspect it before retrying: ' + str(stage))
    stage.mkdir()
    work = stage / '_work'
    work.mkdir()
    atomic_json(status_path, {'state': 'processing', 'staging': str(stage)})
    try:
        source_pattern = split_sequence(job['source'])[0]
        source_path = Path(source_pattern)
        sources = [] if job['kind'] == 'folder' else sequence_files(job['source'])
        main_dir = stage / 'main'
        main_dir.mkdir()
        if job['kind'] == 'folder':
            shutil.copytree(source_path, main_dir, dirs_exist_ok=True)
        else:
            for source in sources:
                shutil.copy2(source, main_dir / source.name)
        highres_pattern = ''
        if job.get('highres'):
            (stage / 'highres').mkdir()
            high_source = Path(split_sequence(job['highres'])[0])
            if job['kind'] == 'folder':
                shutil.copytree(high_source, stage / 'highres', dirs_exist_ok=True)
                highres_pattern = ''
            else:
                files = sequence_files(job['highres'])
                for source in files:
                    shutil.copy2(source, stage / 'highres' / source.name)
                highres_pattern = high_source.name
        outputs = {}
        for key, media in job['media'].items():
            if media.get('mode') == 'omit':
                continue
            (stage / key).mkdir()
            supplied = Path(media.get('path', ''))
            extension = supplied.suffix if media['mode'] == 'supply' else ('.mp4' if key == 'proxy' and job['kind'] == 'footage' else '.jpg')
            outputs[key] = stage / key / (name + extension)
            if media['mode'] == 'supply':
                if supplied.is_dir():
                    outputs[key] = stage / key
                    shutil.copytree(supplied, outputs[key], dirs_exist_ok=True)
                else:
                    shutil.copy2(supplied, outputs[key])
        if job['kind'] in ('still', 'footage'):
            Processor(job, work).generate([main_dir / p.name for p in sources], outputs)
        validate_media(outputs, job)
        pattern, first, last = split_sequence(job['source'])
        destination = root / category / name
        metadata = dict(job.get('metadata', {}))
        if job['kind'] in ('still', 'footage'):
            main_info = probe(main_dir / sources[0].name, job['tools'])
            metadata.update(width=int(main_info['width']), height=int(main_info['height']),
                            pixel_aspect=float(main_info.get('sample_aspect_ratio', '1:1').split(':')[0]) /
                                         max(1.0, float(main_info.get('sample_aspect_ratio', '1:1').split(':')[-1])),
                            colorspace=job['colorspace'])
            groups = extension_groups({'extension_groups': job.get('extension_groups', {})})
            metadata['is_raw'] = sources[0].suffix.lower().lstrip('.') in groups.get('extensions_stills_raw', [])
            if job['kind'] == 'footage':
                frame_rate = job['fps']
                try:
                    frame_rate = float(Fraction(main_info.get('avg_frame_rate', job['fps'])))
                except (ValueError, ZeroDivisionError):
                    pass
                frames = (last - first + 1) if first is not None else int(main_info.get('nb_frames') or 0)
                seconds = float(main_info.get('duration') or (frames / frame_rate if frames else 0))
                metadata.update(frame_rate=frame_rate, duration_frames=frames,
                                duration_seconds=seconds, frame_start=first or 0,
                                frame_end=last if last is not None else max(0, frames - 1))
        def tree_size(path):
            return sum(item.stat().st_size for item in Path(path).rglob('*') if item.is_file()) if Path(path).is_dir() else Path(path).stat().st_size
        metadata['file_size_main'] = tree_size(main_dir)
        metadata['file_size_total'] = sum(tree_size(item) for item in stage.iterdir() if item.name != '_work')
        if job['kind'] == 'folder':
            metadata['files'] = sorted(item.relative_to(stage).as_posix() for item in main_dir.rglob('*') if item.is_file())
        validate_metadata(job['kind'], metadata, complete=True)
        main_name = '' if job['kind'] == 'folder' else Path(pattern).name
        record = dict(id=category + '/' + name, name=name, category=category, kind=job['kind'],
                      main=str(destination / 'main' / main_name), first=first, last=last,
                      tags=job['tags'], colorspace=job['colorspace'], ingest_job_id=identifier,
                      metadata=metadata)
        for key, path in outputs.items():
            record[key] = str(destination / key) if path == stage / key else str(destination / key / path.name)
        if highres_pattern:
            record['highres'] = str(destination / 'highres' / highres_pattern)
            _, record['highres_first'], record['highres_last'] = split_sequence(job['highres'])
        elif job.get('highres') and job['kind'] == 'folder':
            record['highres'] = str(destination / 'highres')
        # Only remove our own intermediates inside this exact staging directory.
        if work.resolve().parent != stage.resolve():
            raise ValueError('Unsafe intermediate path.')
        shutil.rmtree(work)
        library.publish(record, stage)
        atomic_json(status_path, {'state': 'complete', 'asset': record['id']})
        return record
    except Exception as error:
        atomic_json(status_path, {'state': 'failed', 'error': str(error), 'staging': str(stage)})
        raise
