import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import make_test_access
from tinylib import processing
from tinylib.ingest import make_manifest, transcode_defaults, transcode_options
from tinylib.processing import Processor, has_alpha, run_passthrough

HAVE_FFMPEG = shutil.which('ffmpeg') is not None


class OptionTests(unittest.TestCase):
    def test_defaults_and_studio_override(self):
        self.assertEqual(transcode_defaults({}), {'extension': 'exr', 'compression': 'dwaa', 'start_frame': 1001,
                                                  'output_colorspace': 'ACEScg', 'copy_source': True})
        self.assertEqual(transcode_defaults({'transcode_main': {'output_colorspace': 'ACES - ACEScg', 'x': 1}})
                         ['output_colorspace'], 'ACES - ACEScg')

    def test_options_are_checked(self):
        self.assertIsNone(transcode_options(None))
        self.assertEqual(transcode_options({'extension': '.EXR', 'compression': 'DWAA:45', 'start_frame': '1001',
                                            'output_colorspace': ' ACEScg '}),
                         {'extension': 'exr', 'compression': 'dwaa:45', 'start_frame': 1001, 'output_colorspace': 'ACEScg',
                          'copy_source': False})
        self.assertIs(transcode_options({'extension': 'exr', 'start_frame': 1, 'output_colorspace': 'a',
                                         'copy_source': True})['copy_source'], True)
        good = {'extension': 'exr', 'compression': 'dwaa', 'start_frame': 1, 'output_colorspace': 'ACEScg'}
        for change in ({'extension': 'mov'}, {'compression': 'dwaa; rm'}, {'start_frame': 'x'}, {'start_frame': -1},
                       {'output_colorspace': ''}):
            with self.assertRaises(ValueError, msg=change):
                transcode_options(dict(good, **change))
        self.assertEqual(transcode_options(dict(good, compression=''))['compression'], '')


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'library').mkdir()
        config = self.root / 'studio.json'
        config.write_text(json.dumps({'libraries': [{'name': 'L', 'root': str(self.root / 'library')}],
                                      'profiles': {'P': {'ocio_config': 'ocio://studio-config-latest'}}}))
        from tinylib.settings import load_settings
        self.settings = load_settings(config)
        self.settings['_access'] = make_test_access(config)
        self.library = self.settings['libraries'][0]
        self.mov = self.root / 'stock.mov'
        self.mov.write_bytes(b'mov')
        self.options = {'extension': 'exr', 'compression': 'dwaa', 'start_frame': 1001, 'output_colorspace': 'ACEScg'}
        self.media = {key: {'mode': 'generate'} for key in ('proxy', 'thumb', 'filmstrip')}

    def test_footage_job_carries_the_options(self):
        job = make_manifest(self.settings, self.library, 'Stock', 'fx', str(self.mov), [], 'Output - Rec.709', 'P',
                            self.media, fps=25, transcode_main=self.options)
        self.assertEqual(job['transcode_main'], dict(self.options, copy_source=False))
        self.assertEqual(job['colorspace'], 'Output - Rec.709')  # the main's own; the worker converts from it
        job = make_manifest(self.settings, self.library, 'Stock2', 'fx', str(self.mov), [], 'Output - Rec.709', 'P',
                            self.media, fps=25)
        self.assertIsNone(job['transcode_main'])

    def test_only_footage_and_only_with_a_profile(self):
        still = self.root / 'still.exr'
        still.write_bytes(b'exr')
        with self.assertRaisesRegex(ValueError, 'only available for Footage'):
            make_manifest(self.settings, self.library, 'S', 'fx', str(still), [], 'ACEScg', 'P',
                          {'thumb': {'mode': 'generate'}, 'proxy': {'mode': 'generate'}}, kind='still',
                          transcode_main=self.options)
        with self.assertRaisesRegex(ValueError, 'processing profile'):
            make_manifest(self.settings, self.library, 'Stock', 'fx', str(self.mov), [], 'Output - Rec.709', 'none',
                          {key: {'mode': 'omit'} for key in ('proxy', 'filmstrip')} | {'thumb': {'mode': 'supply',
                           'path': str(self.root / 't.jpg')}}, fps=25, transcode_main=self.options)


class PassthroughTests(unittest.TestCase):
    def test_fps_mode_first_and_vsync_only_for_old_ffmpeg(self):
        seen = []

        def old_ffmpeg(command, timeout=None):
            seen.append(command)
            if '-fps_mode' in command:
                raise RuntimeError("Conversion failed:\nUnrecognized option 'fps_mode'.")
            return 'ok'
        with patch('tinylib.processing.run', side_effect=old_ffmpeg):
            self.assertEqual(run_passthrough(['ffmpeg', '{fps_mode}', 'passthrough', 'out']), 'ok')
        self.assertEqual(seen, [['ffmpeg', '-fps_mode', 'passthrough', 'out'], ['ffmpeg', '-vsync', 'passthrough', 'out']])
        with patch('tinylib.processing.run', side_effect=RuntimeError('Conversion failed: disk full')) as run:
            with self.assertRaisesRegex(RuntimeError, 'disk full'):
                run_passthrough(['ffmpeg', '{fps_mode}', 'passthrough'])
        self.assertEqual(run.call_count, 1)


class AlphaTests(unittest.TestCase):
    def test_pixel_formats_with_alpha(self):
        for pix_fmt in ('rgba', 'rgba64le', 'bgra', 'argb', 'yuva444p10le', 'yuva420p', 'gbrap', 'gbrapf32le',
                        'ya8', 'ya16be', 'RGBA'):
            self.assertTrue(has_alpha({'pix_fmt': pix_fmt}), pix_fmt)
        for pix_fmt in ('rgb24', 'rgb48le', 'yuv420p', 'yuv422p10le', 'gbrpf32le', 'gray', 'gray16le', '', None):
            self.assertFalse(has_alpha({'pix_fmt': pix_fmt}), pix_fmt)
        self.assertFalse(has_alpha({}))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'work').mkdir()
        self.main = self.root / 'main'
        self.main.mkdir()

    def processor(self, **options):
        transcode = dict({'extension': 'exr', 'compression': 'dwaa', 'start_frame': 1001,
                          'output_colorspace': 'ACEScg'}, **options)
        job = dict(kind='footage', fps=25.0, colorspace='Output - Rec.709', tools={'ffmpeg': 'ffmpeg', 'oiiotool': 'oiiotool'},
                   profile={'ocio_config': 'ocio://studio-config-latest'}, extension_groups={},
                   transcode_main=transcode)
        return Processor(job, self.root / 'work')

    def run_with_fake_oiiotool(self, processor, sources, frames=None, pix_fmt='rgb48le'):
        """FFmpeg runs for real when available; oiiotool writes empty frames for --frames.
        Stand-in media files (not decodable) are probed as ``pix_fmt``."""
        self.commands = []
        self.decoded_pix_fmt = None
        real = processing.run

        def fake(command, timeout=None):
            command = [str(v) for v in command]
            if command[0] == 'ffprobe' and (not HAVE_FFMPEG or Path(command[-1]).read_bytes()[:3] in (b'dpx', b'exr')):
                return json.dumps({'streams': [{'pix_fmt': pix_fmt, 'width': 4, 'height': 4}]})
            if command[0] == 'ffprobe':
                return real(command)
            self.commands.append(command)
            if command[0] == 'oiiotool':
                source = command[command.index('--frames') + 2]
                if HAVE_FFMPEG and source.endswith('.png'):
                    probe = real(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json',
                                  source % int(command[command.index('--frames') + 1].split('-')[0])])
                    self.decoded_pix_fmt = json.loads(probe)['streams'][0]['pix_fmt']
                first, last = map(int, command[command.index('--frames') + 1].split('-'))
                for frame in range(first, last + 1):
                    Path(command[-1] % frame).write_bytes(b'exr')
                return ''
            if HAVE_FFMPEG:
                return real(command)
            for frame in range(1001, 1001 + frames):
                Path(command[-1] % frame).write_bytes(b'png')
            return ''
        with patch('tinylib.processing.run', side_effect=fake):
            return processor.transcode_main(sources, self.main, 'Stock')

    def test_container_becomes_a_numbered_exr_sequence(self):
        source = self.root / 'stock.mov'
        if HAVE_FFMPEG:
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=25',
                            '-frames:v', '6', '-pix_fmt', 'yuv420p', str(source)], check=True)
        else:
            source.write_bytes(b'mov')
        frames, pattern, first, last = self.run_with_fake_oiiotool(self.processor(), [source], frames=6)
        self.assertEqual((pattern, first, last), ('Stock.####.exr', 1001, 1006))
        self.assertEqual([frame.name for frame in frames], ['Stock.%04d.exr' % f for f in range(1001, 1007)])
        self.assertEqual(sorted(p.name for p in self.main.iterdir()), [frame.name for frame in frames])
        decode, convert = self.commands
        self.assertEqual(decode[0], 'ffmpeg')
        self.assertIn('rgb48be', decode)
        self.assertEqual(decode[decode.index('-fps_mode') + 1], 'passthrough')
        self.assertEqual(decode[decode.index('-start_number') + 1], '1001')
        self.assertEqual(convert[convert.index('--frames') + 1], '1001-1006')
        self.assertEqual(convert[convert.index('--colorconvert') + 1:convert.index('--colorconvert') + 3],
                         ['Output - Rec.709', 'ACEScg'])
        self.assertEqual(convert[convert.index('--compression') + 1], 'dwaa')
        self.assertEqual(convert[convert.index('-d') + 1], 'half')
        self.assertEqual(convert[convert.index('--colorconfig') + 1], 'ocio://studio-config-latest')

    @unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
    def test_alpha_is_detected_decoded_and_written(self):
        source = self.root / 'smoke_alpha.mov'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=25', '-frames:v', '4',
                        '-vf', 'format=rgba,colorchannelmixer=aa=0.5', '-c:v', 'png', '-pix_fmt', 'rgba', str(source)],
                       check=True)
        frames, pattern, first, last = self.run_with_fake_oiiotool(self.processor(), [source])
        self.assertEqual((first, last), (1001, 1004))
        decode, convert = self.commands
        self.assertEqual(decode[decode.index('-pix_fmt') + 1], 'rgba64be')
        self.assertEqual(self.decoded_pix_fmt, 'rgba64be')  # the PNGs oiiotool reads carry alpha
        self.assertEqual(convert[convert.index('--ch') + 1], 'R,G,B,A')
        self.assertIn('--colorconvert:unpremult=1', convert)
        self.assertNotIn('--colorconvert', convert)

    def test_image_sequence_with_alpha_keeps_it(self):
        source = self.root / 'scan.0001.exr'
        source.write_bytes(b'exr')
        self.run_with_fake_oiiotool(self.processor(), [source], pix_fmt='gbrapf32le')
        (convert,) = self.commands
        self.assertEqual(convert[convert.index('--ch') + 1], 'R,G,B,A')
        self.run_with_fake_oiiotool(self.processor(), [source], pix_fmt='gbrpf32le')
        self.assertEqual(self.commands[0][self.commands[0].index('--ch') + 1], 'R,G,B')

    def test_image_sequence_is_renumbered_from_the_start_frame(self):
        sources = []
        for frame in range(1, 4):
            path = self.root / ('scan.%04d.dpx' % frame)
            path.write_bytes(b'dpx')
            sources.append(path)
        frames, pattern, first, last = self.run_with_fake_oiiotool(
            self.processor(extension='tif', compression='zip', start_frame=86400), sources)
        self.assertEqual((pattern, first, last), ('Stock.#####.tif', 86400, 86402))
        self.assertEqual([frame.name for frame in frames], ['Stock.86400.tif', 'Stock.86401.tif', 'Stock.86402.tif'])
        (convert,) = self.commands
        self.assertEqual(convert[convert.index('--frames') + 1], '1-3')
        self.assertTrue(convert[convert.index('--frames') + 2].endswith('scan.%04d.dpx'))
        self.assertEqual(convert[convert.index('-d') + 1], 'uint16')

    def test_no_compression_flag_when_empty_and_missing_frames_fail(self):
        source = self.root / 'scan.0001.exr'
        source.write_bytes(b'exr')
        self.run_with_fake_oiiotool(self.processor(compression=''), [source])
        self.assertNotIn('--compression', self.commands[0])

        def lazy(command, timeout=None):
            if str(command[0]) == 'ffprobe':
                return json.dumps({'streams': [{'pix_fmt': 'rgb48le'}]})
            return ''  # oiiotool "succeeds" but writes nothing
        with patch('tinylib.processing.run', side_effect=lazy):
            with self.assertRaisesRegex(ValueError, 'wrote 0 of 1'):
                self.processor().transcode_main([source], self.main, 'Again')


if __name__ == '__main__':
    unittest.main()


class ProcessCopySourceTests(unittest.TestCase):
    """process() with Transcode main: the original is kept beside the new main as main-original."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'library').mkdir()
        config = self.root / 'studio.json'
        config.write_text(json.dumps({'libraries': [{'name': 'L', 'root': str(self.root / 'library')}],
                                      'profiles': {'P': {'ocio_config': 'ocio://studio-config-latest'}}}))
        from tinylib.settings import load_settings
        self.settings = load_settings(config)
        self.settings['_access'] = make_test_access(config)
        self.mov = self.root / 'incoming' / 'BloodHit.mov'
        self.mov.parent.mkdir()
        self.mov.write_bytes(b'original movie')

    def ingest(self, copy_source):
        from tinylib.ingest import save_manifest
        from tinylib.library import Library
        options = {'extension': 'exr', 'compression': 'dwaa', 'start_frame': 1001, 'output_colorspace': 'ACEScg',
                   'copy_source': copy_source}
        name = 'Blood%d' % copy_source
        job = make_manifest(self.settings, self.settings['libraries'][0], name, 'fx', str(self.mov), [],
                            'Output - Rec.709', 'P', {key: {'mode': 'generate'} for key in ('proxy', 'thumb', 'filmstrip')},
                            fps=25, transcode_main=options)
        path = save_manifest(job, self.root / 'jobs')

        def transcode(processor, sources, target, asset_name):
            frames = []
            for frame in (1001, 1002):
                frames.append(Path(target) / ('%s.%04d.exr' % (asset_name, frame)))
                frames[-1].write_bytes(b'exr')
            return frames, asset_name + '.####.exr', 1001, 1002

        def generate(processor, sources, outputs):
            for output in outputs.values():
                Path(output).write_bytes(b'preview')
        with patch.object(Processor, 'transcode_main', transcode), patch.object(Processor, 'generate', generate), \
                patch('tinylib.processing.validate_media'), \
                patch('tinylib.processing.probe', return_value={'width': 1920, 'height': 1080, 'sample_aspect_ratio': '1:1'}):
            record = processing.process(path, access=self.settings['_access'])
        library = Library(self.root / 'library')
        library.load(use_cache=False)
        return record, next(asset for asset in library.assets if asset['name'] == name)

    def test_original_is_copied_and_recorded(self):
        record, loaded = self.ingest(True)
        folder = self.root / 'library' / 'fx' / 'Blood1' / 'main'
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['Blood1.1001.exr', 'Blood1.1002.exr', 'BloodHit.mov'])
        self.assertEqual((folder / 'BloodHit.mov').read_bytes(), b'original movie')
        self.assertEqual(Path(loaded['main-original']), folder / 'BloodHit.mov')
        self.assertEqual(Path(loaded['main']), folder / 'Blood1.####.exr')
        self.assertEqual((loaded['first'], loaded['last'], loaded['colorspace']), (1001, 1002, 'ACEScg'))
        from tinylib.asset_fields import asset_parts
        self.assertEqual(asset_parts(loaded), 'main:exr main-original:mov proxy:mp4')
        stored = json.loads((self.root / 'library' / 'tinylib_data.json').read_text())['assets'][0]
        self.assertEqual(stored['main-original'], 'fx/Blood1/main/BloodHit.mov')  # relative, like main

    def test_without_copy_only_the_transcoded_main(self):
        record, loaded = self.ingest(False)
        folder = self.root / 'library' / 'fx' / 'Blood0' / 'main'
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['Blood0.1001.exr', 'Blood0.1002.exr'])
        self.assertFalse(loaded.get('main-original'))

    def test_a_clashing_copy_is_refused_before_submission(self):
        frames = self.root / 'incoming' / 'plate'
        frames.mkdir()
        for frame in (1, 2):
            (frames / ('plate.%04d.exr' % frame)).write_bytes(b'exr')
        with self.assertRaisesRegex(ValueError, 'same file names'):
            make_manifest(self.settings, self.settings['libraries'][0], 'plate', 'fx', str(frames / 'plate.####.exr') + ' 1-2',
                          [], 'ACES - ACEScg', 'P', {key: {'mode': 'generate'} for key in ('proxy', 'thumb', 'filmstrip')},
                          fps=25, transcode_main={'extension': 'exr', 'compression': 'zip', 'start_frame': 1001,
                                                  'output_colorspace': 'ACEScg', 'copy_source': True})
