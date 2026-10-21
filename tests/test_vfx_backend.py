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
        self.sources = []
        for frame in range(1001, 1004):
            path = self.root / ('plate.%04d.exr' % frame)
            path.write_bytes(b'x' * frame)
            self.sources.append(path)
        self.outputs = {key: self.root / name for key, name in
                        (('proxy', 'p.mp4'), ('thumb', 't.jpg'), ('filmstrip', 'f.jpg'))}

    def tearDown(self):
        self.temp.cleanup()

    def job(self, preview_source='main', **profile):
        profile = dict(proxy_backend='vfx_transcode', ocio_config=str(self.root / 'config.ocio'), **profile)
        return dict(kind='footage', fps=23.976, colorspace='ACES - ACEScg', preview_source=preview_source,
                    tools={'vfx_transcode': 'vfx.exe', 'ffmpeg': 'ffmpeg'}, profile=profile,
                    extension_groups={}, media={k: {'mode': 'generate'} for k in self.outputs})

    def generate(self, job):
        commands = []

        def fake_run(command, timeout=None):
            commands.append([str(v) for v in command])
            if command[0] == 'vfx.exe' and '-ot' in commands[-1]:
                Path(commands[-1][commands[-1].index('-ot') + 1] + '.jpg').write_bytes(b'')
            if command[0] == 'ffmpeg':
                Path(commands[-1][-1]).write_bytes(b'')
            return ''
        with patch('tinylib.processing.run', side_effect=fake_run):
            Processor(job, self.root / 'work').generate(self.sources, self.outputs)
        return commands

    def test_one_run_makes_proxy_thumb_and_filmstrip(self):
        commands = self.generate(self.job())
        vfx = [c for c in commands if c[0] == 'vfx.exe']
        self.assertEqual(len(vfx), 1)
        args = vfx[0]
        self.assertEqual(args[args.index('-r') + 1], '24000/1001')
        encode = args[args.index('-ff_encode') + 1].split()
        self.assertIn('{videofilter}', encode)
        self.assertEqual(args[args.index('-o') + 1], str(self.outputs['proxy']))
        self.assertEqual(args[args.index('-of') + 1], str(self.outputs['filmstrip']))
        self.assertEqual(args[args.index('-filmstrip_source') + 1], 'intermediates')
        self.assertEqual(args[args.index('-thumbnail_frame') + 1], 'largest')
        for flag in ('-burn_preset', '-slate', '-metadata_preset'):
            self.assertEqual(args[args.index(flag) + 1], 'none')
        # The thumbnail is padded to the library canvas by FFmpeg afterwards.
        self.assertTrue(any(c[0] == 'ffmpeg' and str(self.outputs['thumb']) in c for c in commands))

    def test_previews_from_proxy_stay_builtin(self):
        commands = self.generate(self.job(preview_source='proxy'))
        args = [c for c in commands if c[0] == 'vfx.exe'][0]
        self.assertNotIn('-ot', args)
        self.assertNotIn('-of', args)
        self.assertGreater(len([c for c in commands if c[0] == 'ffmpeg']), 1)

    def test_display_view(self):
        args = self.generate(self.job(display='sRGB - Display', view='ACES 1.0 - SDR Video'))[0]
        self.assertEqual(args[args.index('-view') + 1], 'ACES 1.0 - SDR Video')
        self.assertNotIn('-co', args)

    def test_ini_ocio_overrides_profile(self):
        job = self.job()
        job['profile']['ocio_config'] = '${OCIO}'
        job['tools']['ocio'] = str(self.root / 'config.ocio')
        args = self.generate(job)[0]
        self.assertEqual(args[args.index('-ocio') + 1], str(self.root / 'config.ocio'))

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
