import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tinylib.processing import Processor


class VfxTranscodeBackendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'work').mkdir()
        (self.root / 'config.ocio').write_text('')
        self.vfx = self.root / 'vfx.exe'
        self.vfx.write_text('')
        self.sources = self.sequence('exr')
        self.outputs = {key: self.root / name for key, name in
                        (('proxy', 'p.mp4'), ('thumb', 't.jpg'), ('filmstrip', 'f.jpg'))}
        self.size = ('3840', '2160', '1:1')
        self.container_frames = 5

    def tearDown(self):
        self.temp.cleanup()

    def sequence(self, extension, sizes=(1001, 1003, 1002)):
        paths = []
        for frame, size in zip(range(1001, 1004), sizes):
            path = self.root / ('plate.%04d.%s' % (frame, extension))
            path.write_bytes(b'x' * size)
            paths.append(path)
        return paths

    def job(self, preview_source='main', **profile):
        profile = dict(ocio_config=str(self.root / 'config.ocio'), **profile)
        return dict(kind='footage', fps=23.976, colorspace='ACES - ACEScg', preview_source=preview_source,
                    tools={'vfx_transcode': str(self.vfx), 'ffmpeg': 'ffmpeg', 'oiiotool': 'oiiotool'},
                    profile=profile, extension_groups={},
                    media={k: {'mode': 'generate'} for k in self.outputs})

    def generate(self, job, sources=None):
        self.commands = commands = []

        def fake_run(command, timeout=None):
            command = [str(v) for v in command]
            commands.append(command)
            if command[0] == str(self.vfx):
                self.presets = json.loads(Path(command[command.index('-presets_file') + 1]).read_text())
                if '-ot' in command:
                    Path(command[command.index('-ot') + 1] + '.png').write_bytes(b'')
            if command[0] == 'ffmpeg' and command[-1].endswith('frame.%08d.png'):
                for frame in range(1001, 1001 + self.container_frames):
                    Path(command[-1] % frame).write_bytes(b'')
            elif command[0] == 'oiiotool' and '--parallel-frames' in command:
                first, last = map(int, command[command.index('--frames') + 1].split('-'))
                self.exr_folder = Path(command[-1]).parent
                for frame in range(first, last + 1):
                    Path(command[-1] % frame).write_bytes(b'')
            elif command[0] in ('ffmpeg', 'oiiotool'):
                Path(command[-1]).write_bytes(b'')
            return ''

        def fake_probe(path, tools, count=False):
            width, height, sar = self.size
            return {'width': width, 'height': height, 'sample_aspect_ratio': sar}
        with patch('tinylib.processing.run', side_effect=fake_run), \
                patch('tinylib.processing.probe', side_effect=fake_probe):
            self.processor = Processor(job, self.root / 'work')
            self.processor.generate(sources or self.sources, self.outputs)
        return [c for c in commands if c[0] == str(self.vfx)]

    @staticmethod
    def value(args, flag):
        return args[args.index(flag) + 1]

    def test_default_profile_uses_vfx_transcode_once(self):
        vfx = self.generate(self.job())
        self.assertEqual(len(vfx), 1)
        args = vfx[0]
        self.assertEqual(self.value(args, '-r'), '24000/1001')
        self.assertEqual(self.value(args, '-o'), str(self.outputs['proxy']))
        self.assertEqual(self.value(args, '-of'), str(self.outputs['filmstrip']))
        self.assertEqual(self.value(args, '-filmstrip_source'), 'intermediates')
        self.assertEqual(self.value(args, '-thumbnail_frame'), 'largest')
        self.assertEqual(self.value(args, '-width'), '1920')
        # No oiiotool per frame: the only other commands pad the thumbnail.
        others = [c for c in self.commands if c[0] != str(self.vfx)]
        self.assertEqual(len(others), 1)
        self.assertEqual(others[0][-1], str(self.outputs['thumb']))
        self.assertEqual(self.processor.main_info['width'], '3840')

    def test_own_presets_file_sets_encode_and_thumbnail_and_no_studio_defaults(self):
        self.generate(self.job())
        encode = self.presets['encode_presets'][self.presets['default_encode']]['encode'].split()
        self.assertIn('{videofilter}', encode)
        thumb = self.presets['thumbnail_presets'][self.presets['default_thumbnail']]
        # 16:9 is narrower than the 960x506 canvas, so the height decides.
        self.assertEqual((thumb['width'], thumb['height'], thumb['format']), (0, 506, 'png'))
        for key in ('default_burnin', 'default_slate', 'default_metadata', 'default_oiio_transform',
                    'default_smart_tokens', 'name_tokens'):
            self.assertNotIn(key, self.presets)

    def test_tall_frames_are_converted_at_the_fitted_size(self):
        self.size = ('2048', '1536', '1:1')  # 4:3
        args = self.generate(self.job())[0]
        self.assertEqual(self.value(args, '-width'), '1440')
        thumb = self.presets['thumbnail_presets']['tinylib_thumb']
        self.assertEqual((thumb['width'], thumb['height']), (0, 506))

    def test_anamorphic_aspect_includes_pixel_aspect(self):
        self.size = ('2048', '1716', '2:1')  # 2.39 on screen
        args = self.generate(self.job())[0]
        self.assertEqual(self.value(args, '-width'), '1920')
        thumb = self.presets['thumbnail_presets']['tinylib_thumb']
        self.assertEqual((thumb['width'], thumb['height']), (960, 0))

    def test_middle_frame_profile(self):
        args = self.generate(self.job(thumbnail_frame='middle'))[0]
        self.assertEqual(self.value(args, '-thumbnail_frame'), 'middle')

    def test_supplied_proxy_picks_largest_source_frame_by_number(self):
        job = self.job()
        job['media']['proxy'] = {'mode': 'supply'}
        args = self.generate(job)[0]
        self.assertNotIn('-o', args)
        self.assertEqual(self.value(args, '-thumbnail_frame'), '1002')
        self.assertEqual(self.value(args, '-filmstrip_source'), 'source')

    def test_previews_from_proxy_read_the_filmstrip_from_the_movie(self):
        args = self.generate(self.job(preview_source='proxy'))[0]
        self.assertNotIn('-ot', args)
        self.assertEqual(self.value(args, '-filmstrip_source'), 'movie')
        # Only the thumbnail is taken from the proxy afterwards: one FFmpeg run, not 25.
        ffmpeg = [c for c in self.commands if c[0] == 'ffmpeg']
        self.assertEqual(len(ffmpeg), 1)
        self.assertEqual(ffmpeg[0][-1], str(self.outputs['thumb']))

    def test_builtin_opt_out(self):
        self.assertEqual(self.generate(self.job(proxy_backend='builtin')), [])
        self.assertTrue(any(c[0] == 'oiiotool' for c in self.commands))

    def test_display_referred_sequence_is_converted_to_scene_linear_exrs_first(self):
        # Scenario 3: vfx-transcode only colour-converts EXR, so a DPX/PNG/TIFF main becomes
        # ACEScg EXRs (resized in display space, in one oiiotool run) for it.
        job = self.job()
        job['colorspace'] = 'Output - Rec.709'
        args = self.generate(job, self.sequence('png'))[0]
        oiio = [c for c in self.commands if c[0] == 'oiiotool']
        self.assertEqual(len(oiio), 1)
        convert = oiio[0]
        self.assertEqual(self.value(convert, '--frames'), '1001-1003')
        self.assertTrue(convert[convert.index('--frames') + 2].endswith('plate.%04d.png'))
        self.assertEqual(self.value(convert, '--resize'), '1920x1080')
        self.assertLess(convert.index('--resize'), convert.index('--colorconvert'))
        self.assertEqual(convert[convert.index('--colorconvert') + 1:convert.index('--colorconvert') + 3],
                         ['Output - Rec.709', 'ACES - ACEScg'])
        self.assertEqual(self.value(args, '-ci'), 'ACES - ACEScg')
        self.assertEqual(self.value(args, '-par'), '1')
        self.assertTrue(args[args.index('-i') + 1].endswith('frame.00001001.exr'))
        self.assertFalse(self.exr_folder.exists(), 'temporary EXRs are removed')

    def test_display_referred_container_is_decoded_to_16bit_png(self):
        movie = self.root / 'clip.mov'
        movie.write_bytes(b'mov')
        job = self.job(working_colorspace='ACEScg')
        job['colorspace'] = 'Output - sRGB'
        args = self.generate(job, [movie])[0]
        decode = [c for c in self.commands if c[0] == 'ffmpeg' and c[-1].endswith('.png')][0]
        self.assertEqual(self.value(decode, '-pix_fmt'), 'rgb48be')
        self.assertIn('scale=1920:1080', self.value(decode, '-vf'))
        convert = [c for c in self.commands if c[0] == 'oiiotool'][0]
        self.assertEqual(self.value(convert, '--frames'), '1001-1005')
        self.assertNotIn('--resize', convert)  # FFmpeg already decoded at the proxy size
        self.assertEqual(self.value(args, '-ci'), 'ACEScg')
        self.assertEqual(self.value(args, '-thumbnail_frame'), 'largest')

    def test_container_without_movie_uses_middle_thumbnail(self):
        movie = self.root / 'clip.mov'
        movie.write_bytes(b'mov')
        job = self.job()
        job['media']['proxy'] = {'mode': 'omit'}
        args = self.generate(job, [movie])[0]
        self.assertEqual(self.value(args, '-thumbnail_frame'), 'middle')

    def test_supplied_proxy_needs_no_vfx_transcode_and_reads_long_strips_in_one_pass(self):
        # Scenario 2: thumbnail and filmstrip from the supplied proxy, by FFmpeg.
        sources = []
        for frame in range(1001, 1031):
            path = self.root / ('long.%04d.exr' % frame)
            path.write_bytes(b'x' * frame)
            sources.append(path)
        job = self.job(preview_source='proxy')
        job['media']['proxy'] = {'mode': 'supply'}
        self.assertEqual(self.generate(job, sources), [])
        ffmpeg = [c for c in self.commands if c[0] == 'ffmpeg']
        self.assertEqual(len(ffmpeg), 2)  # thumbnail, filmstrip
        strip = self.value(ffmpeg[1], '-vf')
        self.assertEqual(strip.count('eq(n'), 24)
        self.assertIn('tile=24x1', strip)

    def test_missing_tool_falls_back_unless_forced(self):
        self.vfx.unlink()
        self.assertEqual(self.generate(self.job()), [])
        self.assertEqual(len(self.generate(self.job(proxy_backend='vfx_transcode'))), 1)

    def test_custom_color_args(self):
        custom = ['--ociolook', 'Film', '--colorconvert', '{input_colorspace}', '{output_colorspace}']
        self.assertEqual(self.generate(self.job(color_args=custom)), [])
        with self.assertRaisesRegex(ValueError, 'color_args'):
            self.generate(self.job(color_args=custom, proxy_backend='vfx_transcode'))

    def test_unknown_backend(self):
        with self.assertRaisesRegex(ValueError, 'proxy_backend'):
            self.generate(self.job(proxy_backend='vfx-transcode'))

    def test_display_view(self):
        args = self.generate(self.job(display='sRGB - Display', view='ACES 1.0 - SDR Video'))[0]
        self.assertEqual(self.value(args, '-view'), 'ACES 1.0 - SDR Video')
        self.assertNotIn('-co', args)

    def test_ini_ocio_overrides_profile(self):
        job = self.job()
        job['profile']['ocio_config'] = '${OCIO}'
        job['tools']['ocio'] = str(self.root / 'config.ocio')
        args = self.generate(job)[0]
        self.assertEqual(self.value(args, '-ocio'), str(self.root / 'config.ocio'))

    def test_unexpanded_variable_names_the_ini_key(self):
        job = self.job()
        job['profile']['ocio_config'] = '${OCIO}'
        with self.assertRaisesRegex(ValueError, r'\[tools\]'):
            self.generate(job)

    def test_missing_ocio_is_an_error(self):
        job = self.job()
        job['profile']['ocio_config'] = str(self.root / 'missing.ocio')
        with self.assertRaises(ValueError):
            self.generate(job)


if __name__ == '__main__':
    unittest.main()
