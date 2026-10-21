"""Farm worker. All outputs are staged and verified before library publication."""
import json
import re
import shutil
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path
from .library import Library, atomic_json, read_json, safe_component, sequence_files, split_sequence
from .asset_types import extension_groups, validate_metadata
from .media_probe import CONVERSION_TIMEOUT, PROBE_TIMEOUT, number, pixel_aspect
from .log import get_logger

log = get_logger(__name__)


def run(command, timeout=CONVERSION_TIMEOUT):
    command = [str(v) for v in command]
    print(subprocess.list2cmdline(command), flush=True)
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('Conversion did not finish within %g s: %s' % (timeout, command[0])) from error
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
    streams = json.loads(run(args, timeout=PROBE_TIMEOUT)).get('streams', [])
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

    def ocio_config(self):
        """The worker's OCIO config: ``ocio`` in tinylib.ini [tools], else the profile's ``ocio_config``."""
        ocio = str(self.tools.get('ocio') or self.profile.get('ocio_config') or '')
        if ocio.startswith('ocio://') or (ocio and Path(ocio).is_file()):
            return ocio
        if not ocio or re.search(r'\$\{?\w+\}?|%\w+%', ocio):
            raise ValueError('No OCIO config on this worker (%s). Set "ocio" under [tools] in tinylib.ini, '
                             'or set the environment variable it names.' % (ocio or 'not set'))
        raise ValueError('OCIO config is unavailable: ' + ocio)

    def image(self, source, target, size, convert=True):
        if convert:
            ocio = self.ocio_config()
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

    def vfx_transcode(self, sources, outputs):
        """Generate an image sequence's proxy, thumbnail and filmstrip in one vfx-transcode run.

        One run converts the frames once (OCIO, resize) and reuses them for all three.
        Previews taken from the proxy (preview_source 'proxy') are left to the built-in
        code, which reads them from the proxy file. Returns the output keys it made.
        """
        job, media = self.job, self.job['media']
        from_main = job['preview_source'] != 'proxy'
        wanted = [key for key in ('proxy', 'thumb', 'filmstrip')
                  if outputs.get(key) and media[key]['mode'] == 'generate' and (key == 'proxy' or from_main)]
        if not wanted:
            return set()
        ocio = self.ocio_config()
        rate = Fraction(str(job['fps'])).limit_denominator(1001)
        ntsc = Fraction(round(rate * Fraction(1001, 1000)) * 1000, 1001)
        if rate.denominator != 1 and abs(rate - ntsc) < Fraction(1, 100):
            rate = ntsc  # 23.976 is 24000/1001; vfx-transcode uses a typed fraction exactly.
        rate = str(rate.numerator) if rate.denominator == 1 else '%s/%s' % (rate.numerator, rate.denominator)
        args = [self.tools.get('vfx_transcode', 'vfx-transcode'), '-i', sources[0],
                # The ingest FPS is authoritative; validate_media compares the proxy against it.
                '-r', rate, '-fps_source', 'arg_only', '-ci', job['colorspace'], '-ocio', ocio,
                '-oiio', self.tools.get('oiiotool', 'oiiotool'), '-ffmpeg', self.ffmpeg,
                '-ffprobe', self.tools.get('ffprobe', 'ffprobe'),
                # Library previews are clean pictures: no studio defaults from the presets file.
                '-burn_preset', 'none', '-slate', 'none', '-metadata_preset', 'none',
                '-oiio_transform', 'none', '-smart_tokens', 'none',
                '-par_out', 'square', '-alpha', 'drop', '-temp_location', 'user',
                '-mezzanine', 'jpg', '-width', '1920']
        display, view = self.profile.get('display'), self.profile.get('view')
        if display is not None or view is not None:
            args += ['-display', display or '', '-view', view or '']
            if self.profile.get('looks'):
                args += ['-looks', self.profile['looks']]
        else:
            args += ['-co', self.profile.get('output_colorspace', 'Output - Rec.709')]
        if 'proxy' in wanted:
            args += ['-ff_filter', fit_filter(1920, 1080),
                     # -ff_encode replaces the preset and adds no colour tags, so they are given here.
                     '-ff_encode', '-pix_fmt yuv420p {videofilter} -c:v libx264 -crf 23 -preset faster '
                                   '-movflags +faststart -color_primaries bt709 -color_trc bt709 -colorspace bt709',
                     '-o', outputs['proxy']]
        thumb = self.work / 'vfx_thumb'
        if 'thumb' in wanted:
            frame = 'largest' if self.profile.get('thumbnail_frame', 'largest') == 'largest' else 'middle'
            args += ['-ot', thumb, '-thumbnail_frame', frame]
        if 'filmstrip' in wanted:
            args += ['-of', outputs['filmstrip'], '-filmstrip_tiles', '24', '-filmstrip_tile', '480x270',
                     '-filmstrip_grid', '24x1', '-filmstrip_quality', '90', '-filmstrip_slate', '0',
                     '-filmstrip_source', 'intermediates' if 'proxy' in wanted else 'source']
        # Exit code 2 (frames replaced by neighbours) fails the ingest like any other error.
        run(args)
        if 'thumb' in wanted:
            made = sorted(self.work.glob('vfx_thumb.*'))
            if not made:
                raise ValueError('vfx-transcode did not write a thumbnail.')
            # vfx-transcode resizes thumbnails by width; the library needs the padded 960x506 canvas.
            self.image(made[0], outputs['thumb'], (960, 506), convert=False)
        return set(wanted)

    def generate(self, sources, outputs):
        job = self.job
        media = job['media']
        footage = job['kind'] == 'footage'
        groups = extension_groups({'extension_groups': job.get('extension_groups', {})})
        containers = {'.' + item for item in groups.get('extensions_footage_containers', [])}
        container = footage and len(sources) == 1 and sources[0].suffix.lower() in containers
        proxy = outputs.get('proxy')
        made = set()
        if footage and not container and self.profile.get('proxy_backend') == 'vfx_transcode':
            made = self.vfx_transcode(sources, outputs)
        if proxy and media['proxy']['mode'] == 'generate' and 'proxy' not in made:
            if container:
                run([self.ffmpeg, '-y', '-v', 'error', '-i', sources[0], '-vf', fit_filter(1920, 1080),
                     '-c:v', 'libx264', '-crf', '23', '-preset', 'faster', '-pix_fmt', 'yuv420p',
                     '-movflags', '+faststart', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                     '-colorspace', 'bt709', proxy])
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
        if outputs.get('thumb') and media['thumb']['mode'] == 'generate' and 'thumb' not in made:
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
        if (footage and outputs.get('filmstrip') and media['filmstrip']['mode'] == 'generate'
                and 'filmstrip' not in made):
            if container:
                strip_source = proxy if job['preview_source'] == 'proxy' and proxy else sources[0]
                info = probe(strip_source, self.tools)
                duration = max(float(info.get('duration') or 1), .001)
                run([self.ffmpeg, '-y', '-v', 'error', '-i', strip_source, '-vf',
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
            rate = number(info.get('avg_frame_rate'))
            if rate and abs(rate - manifest['fps']) > .01:
                print('Warning: proxy frame rate differs from ingest FPS; ingest will continue.')


def apply_worker_settings(job, settings):
    """Executables and processing profiles come from the farm's own configuration.

    A manifest is written by a workstation into a shared spool, so it may only
    describe *what* to ingest, never which programs the farm runs.
    """
    job['tools'] = dict(settings.get('tools', {}))
    name = job.get('profile_name')
    profiles = settings.get('profiles', {})
    if name in profiles:
        job['profile'] = dict(profiles[name])
    elif any(media.get('mode') == 'generate' for media in job.get('media', {}).values()):
        raise ValueError('Processing profile %r is not configured on this worker.' % name)
    return job


def footage_timing(job, main_info, first, last):
    """(frame_rate, frames, seconds) for footage.

    An image sequence is probed one frame at a time, so ffprobe's rate and
    duration describe a single image (image2 defaults to 25 fps, 0.04 s). The
    sequence uses the ingest FPS and its frame range. Only a container's own
    stream values are trusted.
    """
    fps = float(job['fps'])
    if first is not None:
        frames = last - first + 1
        return fps, frames, frames / fps
    frame_rate = number(main_info.get('avg_frame_rate')) or number(main_info.get('r_frame_rate')) or fps
    frames = int(number(main_info.get('nb_frames')) or 0)
    seconds = number(main_info.get('duration'))
    if not frames and seconds:
        frames = int(round(seconds * frame_rate))
    if seconds is None:
        seconds = frames / frame_rate if frames else 0.0
    return frame_rate, frames, seconds


def clear_failed_attempt(stage):
    """Remove the staging folder an earlier, failed attempt of this job left behind.

    Deadline requeues a failed or stalled task on any worker, so a retry must be
    able to start over. The folder is first renamed aside: while another worker
    still has files open in it, Windows refuses the rename and this attempt stops
    instead of deleting files under it. Only this job's own folder inside
    ``.tinylib-staging`` is touched.
    """
    if stage.parent.name != '.tinylib-staging' or stage.is_symlink() or not stage.is_dir():
        raise ValueError('Unexpected staging path; inspect it before retrying: ' + str(stage))
    aside = stage.with_name('%s.retry-%s' % (stage.name, uuid.uuid4().hex[:8]))
    try:
        stage.rename(aside)
    except OSError as error:
        raise RuntimeError('Staging of an earlier attempt is still in use, probably by another worker: '
                           '%s (%s)' % (stage, error)) from error
    log.warning('Removing staging left by an earlier attempt: %s', stage)
    print('Removing staging left by an earlier attempt: %s' % stage, flush=True)
    shutil.rmtree(aside, ignore_errors=True)
    if aside.exists():
        # Not fatal: the retry uses a fresh folder; an administrator can delete this later.
        log.warning('Could not fully remove %s; delete it by hand.', aside)


def process(manifest_path, access=None, settings=None):
    job = read_json(manifest_path)
    if job.get('manifest_version') != 1:
        raise ValueError('Unsupported manifest version.')
    root = Path(job['library']['root'])
    if access is None:
        from .access import AccessControl
        from .settings import load_settings
        settings = settings or load_settings()
        access = AccessControl(settings)
    if settings is not None:
        apply_worker_settings(job, settings)
    access.refresh()
    access.require(root, 'ingest')
    if job['library'].get('read_only') or access.read_only(root):
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
        clear_failed_attempt(stage)
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
                            pixel_aspect=pixel_aspect(main_info.get('sample_aspect_ratio', '1:1')))
            groups = extension_groups({'extension_groups': job.get('extension_groups', {})})
            metadata['is_raw'] = sources[0].suffix.lower().lstrip('.') in groups.get('extensions_stills_raw', [])
            if job['kind'] == 'footage':
                frame_rate, frames, seconds = footage_timing(job, main_info, first, last)
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
        from .asset_fields import apply_defaults
        apply_defaults(record)
        record['ingest_user'] = job.get('ingest_user', 'unknown')
        record['ingest_date'] = job.get('ingest_date', '')
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
        log.info('Published %s into %s', record['id'], root)
        return record
    except Exception as error:
        log.error('Ingest %s failed; staging kept at %s: %s', identifier, stage, error)
        atomic_json(status_path, {'state': 'failed', 'error': str(error), 'staging': str(stage)})
        raise
