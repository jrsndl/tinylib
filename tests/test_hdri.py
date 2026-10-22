"""Lat-long (HDRI) stills: schema flag, ingest validation, preview sizes, viewer mathematics."""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import requires_qt

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib import pano
from tinylib.access import AccessControl
from tinylib.asset_types import is_latlong, is_two_to_one, representation
from tinylib.ingest import make_manifest
from tinylib.processing import LATLONG_PROXY_SIZE, LATLONG_THUMB_SIZE, preview_sizes, validate_media

try:
    import numpy
except ImportError:
    numpy = None
requires_numpy = unittest.skipUnless(numpy is not None, 'numpy is not installed')

TESTDATA = Path(__file__).resolve().parents[1] / 'testdata' / 'hdrilib' / 'plhn_indoor_abandonedBakery'


def latlong(metadata=None):
    return dict({'is_equirectangular': True}, **(metadata or {}))


class FlagTests(unittest.TestCase):
    def test_flag_is_still_metadata(self):
        self.assertTrue(is_latlong({'kind': 'still', 'metadata': {'is_equirectangular': True}}))
        self.assertFalse(is_latlong({'kind': 'still', 'metadata': {'is_equirectangular': False}}))
        self.assertFalse(is_latlong({'kind': 'still', 'metadata': {}}))
        self.assertFalse(is_latlong({'kind': 'footage', 'metadata': {'is_equirectangular': True}}))

    def test_two_to_one_allows_a_pixel_of_rounding(self):
        self.assertTrue(is_two_to_one(4096, 2048))
        self.assertTrue(is_two_to_one(4097, 2048))
        self.assertFalse(is_two_to_one(1920, 1080))
        self.assertFalse(is_two_to_one(100, 0))

    def test_hdr_is_a_still_extension(self):
        self.assertIn('hdr', representation('still', 'main')['extensions'])
        self.assertIn('hdr', representation('still', 'highres')['extensions'])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'library').mkdir()
        self.settings = {'libraries': [{'name': 'Test', 'root': str(self.root / 'library')}],
                         'profiles': {'Preview': {}}, 'tools': {}}
        self.settings['_access'] = AccessControl(self.settings, identity='test', persist=False)
        for name in ('sky.exr', 'sky.hdr', 'sky.jpg', 'sky_16k.exr', 'proxy.jpg', 'thumb.jpg'):
            (self.root / name).write_bytes(b'x')

    def manifest(self, main, metadata, highres='', media=None):
        media = media or {'proxy': {'mode': 'generate', 'path': ''}, 'thumb': {'mode': 'generate', 'path': ''}}
        return make_manifest(self.settings, self.settings['libraries'][0], 'sky', 'hdri', str(self.root / main),
                             [], 'ACES - ACEScg', 'Preview', media, highres=highres, kind='still', metadata=metadata)

    def test_latlong_main_must_be_exr_or_hdr(self):
        self.assertTrue(is_latlong(self.manifest('sky.exr', latlong())))
        self.assertTrue(is_latlong(self.manifest('sky.hdr', latlong())))
        with self.assertRaisesRegex(ValueError, 'EXR or HDR'):
            self.manifest('sky.jpg', latlong())
        # Without the flag a JPEG still is fine.
        self.assertFalse(is_latlong(self.manifest('sky.jpg', {})))

    def test_highres_and_supplied_previews_are_kept(self):
        media = {'proxy': {'mode': 'supply', 'path': str(self.root / 'proxy.jpg')},
                 'thumb': {'mode': 'supply', 'path': str(self.root / 'thumb.jpg')}}
        job = self.manifest('sky.exr', latlong(), highres=str(self.root / 'sky_16k.exr'), media=media)
        self.assertTrue(job['highres'].endswith('sky_16k.exr'))
        self.assertEqual(job['media']['proxy']['mode'], 'supply')
        self.assertEqual(job['preview_source'], 'proxy')


class PreviewSizeTests(unittest.TestCase):
    def job(self, kind='still', metadata=None, profile=None):
        return {'kind': kind, 'metadata': metadata or {}, 'tools': {}, 'profile': profile or {},
                'source': 'x.exr', 'fps': 24.0}

    def test_generated_sizes(self):
        self.assertEqual(preview_sizes(self.job(metadata=latlong())), (LATLONG_PROXY_SIZE, LATLONG_THUMB_SIZE))
        self.assertEqual(preview_sizes(self.job()), ((1920, 1080), (960, 506)))
        self.assertEqual(preview_sizes(self.job(metadata=latlong(), profile={'latlong_proxy_size': [8192, 4096]}))[0],
                         (8192, 4096))
        with self.assertRaisesRegex(ValueError, '2:1'):
            preview_sizes(self.job(metadata=latlong(), profile={'latlong_proxy_size': [1920, 1080]}))

    def validate(self, job, sizes):
        def probe(path, tools, count=False):
            width, height = sizes[Path(path).stem]
            return {'width': width, 'height': height}
        with patch('tinylib.processing.probe', side_effect=probe):
            validate_media({key: Path(key + '.jpg') for key in sizes}, job)

    def test_still_proxy_size_is_not_enforced(self):
        self.validate(self.job(), {'proxy': (3000, 2000), 'thumb': (960, 506)})
        with self.assertRaisesRegex(ValueError, 'thumb must be 960x506'):
            self.validate(self.job(), {'proxy': (3000, 2000), 'thumb': (960, 480)})

    def test_latlong_previews_must_be_two_to_one_of_any_size(self):
        job = self.job(metadata=latlong())
        self.validate(job, {'proxy': (2048, 1024), 'thumb': (960, 480)})
        self.validate(job, {'proxy': (8192, 4096), 'thumb': (512, 256)})
        with self.assertRaisesRegex(ValueError, 'proxy of a lat-long still must be 2:1'):
            self.validate(job, {'proxy': (1920, 1080), 'thumb': (960, 480)})
        with self.assertRaisesRegex(ValueError, 'thumb of a lat-long still must be 2:1'):
            self.validate(job, {'proxy': (2048, 1024), 'thumb': (960, 506)})

    def test_footage_sizes_are_unchanged(self):
        job = self.job(kind='footage')
        with patch('tinylib.processing.probe', return_value={'width': 1280, 'height': 720}):
            with self.assertRaisesRegex(ValueError, 'proxy must be 1920x1080'):
                validate_media({'proxy': Path('p.mp4')}, job)


class ProjectionTests(unittest.TestCase):
    def assertUv(self, direction, u, v):
        actual = pano.direction_to_uv(direction)
        self.assertAlmostEqual(actual[0] % 1.0, u % 1.0, places=6)
        self.assertAlmostEqual(actual[1], v, places=6)

    def test_image_centre_is_straight_ahead(self):
        self.assertUv(pano.view_ray(0, 0, 0, 0, 90, 16 / 9), 0.5, 0.5)

    def test_turning_right_and_looking_up(self):
        self.assertUv(pano.view_ray(0, 0, 90, 0, 90, 1), 0.75, 0.5)
        self.assertUv(pano.view_ray(0, 0, -90, 0, 90, 1), 0.25, 0.5)
        self.assertUv(pano.view_ray(0, 0, 180, 0, 90, 1), 0.0, 0.5)
        self.assertAlmostEqual(pano.direction_to_uv(pano.view_ray(0, 0, 0, 90, 90, 1))[1], 0.0, places=6)
        self.assertAlmostEqual(pano.direction_to_uv(pano.view_ray(0, 0, 0, -90, 90, 1))[1], 1.0, places=6)

    def test_view_edges_follow_the_field_of_view(self):
        # The right edge of a 90° view is 45° to the right: u moves by 1/8.
        self.assertUv(pano.view_ray(1, 0, 0, 0, 90, 2), 0.625, 0.5)
        # The left edge of the view is to the left in the image: the picture is not mirrored.
        self.assertLess(pano.direction_to_uv(pano.view_ray(-1, 0, 0, 0, 90, 2))[0], 0.5)
        self.assertLess(pano.direction_to_uv(pano.view_ray(0, 1, 0, 0, 90, 2))[1], 0.5)

    def test_uv_round_trip(self):
        for u, v in ((0.1, 0.2), (0.5, 0.5), (0.9, 0.7), (0.3, 0.99)):
            back = pano.direction_to_uv(pano.uv_to_direction(u, v))
            self.assertAlmostEqual(back[0], u, places=9)
            self.assertAlmostEqual(back[1], v, places=9)

    def test_flat_view_fits_the_image(self):
        self.assertEqual(pano.flat_uv(0, 0, 16 / 9), (0.5, 0.5))
        self.assertEqual(pano.flat_uv(1, 0, 2.0), (1.0, 0.5))
        self.assertIsNone(pano.flat_uv(1, 0, 4.0))  # a wide view has empty sides
        self.assertIsNone(pano.flat_uv(0, 1, 1.0))  # a tall view, empty above and below

    def test_flat_zoom_and_pan(self):
        # Zoomed 4x on the image's right quarter: the view centre is that point.
        self.assertEqual(pano.flat_uv(0, 0, 2.0, center=(0.75, 0.5), zoom=4.0), (0.75, 0.5))
        # The right edge of the view is an eighth of the image further right.
        self.assertAlmostEqual(pano.flat_uv(1, 0, 2.0, center=(0.75, 0.5), zoom=4.0)[0], 0.875)
        # Zooming about a point keeps that point under the mouse.
        before = pano.flat_uv(0.5, 0.25, 16 / 9, (0.5, 0.5), 1.0)
        center = pano.flat_zoom_at(0.5, 0.25, 16 / 9, (0.5, 0.5), 1.0, 3.0)
        after = pano.flat_uv(0.5, 0.25, 16 / 9, center, 3.0)
        self.assertAlmostEqual(before[0], after[0])
        self.assertAlmostEqual(before[1], after[1])

    def test_display_transform(self):
        self.assertAlmostEqual(pano.display_value(1.0), 1.0)
        self.assertAlmostEqual(pano.display_value(0.18), pano.srgb_encode(0.18))
        self.assertAlmostEqual(pano.display_value(0.09, exposure=1), pano.display_value(0.18))
        self.assertAlmostEqual(pano.display_value(0.25, gamma=2.0), pano.srgb_encode(0.5))
        self.assertEqual(pano.display_value(-1.0), 0.0)

    def test_fitted_size(self):
        self.assertEqual(pano.fitted_size(4096, 2048, 8192), (4096, 2048))
        self.assertEqual(pano.fitted_size(16384, 8192, 8192), (8192, 4096))
        self.assertEqual(pano.fitted_size(10000, 5000, 8192), (5000, 2500))  # whole-number reduction
        self.assertNotIn('-vf', pano.decode_arguments('ffmpeg', 'a.exr'))  # FFmpeg's scaler clamps HDR


@requires_numpy
class RgbeTests(unittest.TestCase):
    def test_box_reduce_keeps_hdr_values(self):
        planes = numpy.zeros((3, 4, 8), numpy.float32)
        planes[:, :2, :2] = 400.0
        reduced = numpy.stack(pano.box_reduce(planes, 2))
        self.assertEqual(reduced.shape, (3, 2, 4))
        self.assertAlmostEqual(float(reduced[0, 0, 0]), 400.0)
        self.assertEqual(float(reduced[0, 1, 1]), 0.0)

    def test_round_trip_within_one_percent(self):
        values = numpy.array([[0.0, 1e-4, 0.18, 1.0, 37.5, 5240.0]], numpy.float32)
        encoded = pano.encode_rgbe(values, values * 0.5, values * 0.25)
        for column, value in enumerate(values[0]):
            red, green, blue = pano.decode_rgbe(encoded[0, column])
            if value == 0:
                self.assertEqual((red, green, blue), (0.0, 0.0, 0.0))
                continue
            self.assertAlmostEqual(red / value, 1.0, delta=0.01)
            self.assertAlmostEqual(green / (value * 0.5), 1.0, delta=0.02)

    def test_negative_and_nan_become_black(self):
        bad = numpy.array([[-1.0, float('nan')]], numpy.float32)
        encoded = pano.encode_rgbe(bad, bad, bad)
        self.assertEqual(encoded[..., 3].tolist(), [[0, 0]])

    def test_planar_order_is_gbr(self):
        width, height = 3, 2
        planes = numpy.zeros((3, height, width), numpy.float32)
        planes[2] = 1.0  # FFmpeg gbrp: G, B, R planes; R last
        rgbe, stats = pano.planar_to_rgbe(planes.tobytes(), width, height, rows=1)
        red, green, blue = pano.decode_rgbe(rgbe[1, 2])
        self.assertAlmostEqual(red, 1.0, delta=0.01)
        self.assertLess(max(green, blue), 0.005)  # RGBE's half-step offset
        self.assertAlmostEqual(stats['peak'], 0.2126, places=4)
        with self.assertRaisesRegex(ValueError, 'expected'):
            pano.planar_to_rgbe(b'\0' * 8, width, height)


@requires_numpy
class FloatReaderTests(unittest.TestCase):
    def test_pfm_is_bottom_row_first_and_kept_above_one(self):
        rows = numpy.array([[[0.5, 0.25, 0.125], [2.0, 3.0, 4.0]],       # bottom row in the file
                            [[100.0, 200.0, 300.0], [0.0, 0.0, 1e-3]]],  # top row
                           '<f4')
        data = b'PF\n2 2\n-1.0\n' + rows.tobytes()
        planes = numpy.stack(pano.read_pfm(data))
        self.assertEqual(planes.shape, (3, 2, 2))
        self.assertEqual(planes[:, 0, 0].tolist(), [100.0, 200.0, 300.0])  # top-left
        self.assertEqual(planes[:, 1, 1].tolist(), [2.0, 3.0, 4.0])

    def test_native_half_float_is_not_clamped(self):
        # FFmpeg 7.1+ gives half-float EXRs as gbrapf16le; converting them clamps to 1.0.
        width, height = 2, 1
        g, b, r, a = ([value] * width for value in (2.0, 3.0, 40.0, 1.0))
        data = numpy.array([g, b, r, a], '<f2').tobytes()
        planes = numpy.stack(pano.read_native(data, 'gbrapf16le', width, height))
        self.assertEqual(planes[:, 0, 1].tolist(), [40.0, 2.0, 3.0])
        packed = numpy.array([[[5.0, 6.0, 7.0, 1.0]] * width], '<f4').tobytes()
        self.assertEqual(numpy.stack(pano.read_native(packed, 'rgbaf32le', width, height))[:, 0, 0].tolist(),
                         [5.0, 6.0, 7.0])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is missing')
    def test_pfm_from_another_writer(self):
        import subprocess
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        target = Path(folder) / 'ramp.pfm'
        # Top half white, bottom half black: orientation must survive.
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=white:s=4x4,drawbox=y=2:h=2:c=black:t=fill',
                        '-frames:v', '1', '-pix_fmt', 'gbrpf32le', '-c:v', 'pfm', str(target)], check=True)
        planes = numpy.stack(pano.read_pfm(target.read_bytes()))
        self.assertGreater(planes[0, 0, 0], 0.9)
        self.assertLess(planes[0, 3, 0], 0.1)


@requires_numpy
@unittest.skipUnless(shutil.which('ffmpeg') and (TESTDATA / 'main').is_dir(), 'ffmpeg or the HDRI test data is missing')
class LoadMainTests(unittest.TestCase):
    def test_decodes_the_test_hdri(self):
        main = next((TESTDATA / 'main').glob('*.exr'))
        rgbe, info = pano.load_main(main, {'pano_max_width': 1024})
        self.assertEqual(rgbe.shape, (512, 1024, 4))
        self.assertEqual((info['width'], info['height']), (1024, 512))
        self.assertTrue(is_two_to_one(info['source_width'], info['source_height']))
        self.assertGreater(info['peak'], 1.0)  # scene-linear: brighter than display white


@requires_qt
class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tinylib.qt import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_two_to_one_exr_is_ticked_and_checkbox_edits_metadata(self):
        import json
        from tinylib.ingest_ui import IngestDialog
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'library').mkdir()
        main = root / 'sky.exr'
        main.write_bytes(b'x')
        settings = {'libraries': [{'name': 'Test', 'root': str(root / 'library')}], 'profiles': {'Preview': {}},
                    'tools': {}}
        settings['_access'] = AccessControl(settings, identity='test', persist=False)
        dialog = IngestDialog(settings, [])
        self.addCleanup(dialog.close)
        dialog.kind.setCurrentIndex(dialog.kind.findData('still'))
        self.assertFalse(dialog.latlong.isHidden())
        self.assertFalse(dialog.latlong.isChecked())
        scanned = {'width': 4096, 'height': 2048, 'pixel_aspect': 1.0, 'frame_start': 0}
        with patch('tinylib.ingest_ui.scan_source_metadata', return_value=scanned):
            dialog.source_selected(str(main))
            deadline = time.monotonic() + 5
            while dialog._scans and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.002)
            self.app.processEvents()
        self.assertTrue(dialog.latlong.isChecked())
        metadata = json.loads(dialog.metadata.toPlainText())
        self.assertIs(metadata['is_equirectangular'], True)
        self.assertEqual(metadata['width'], 4096)
        self.assertNotIn('frame_start', metadata)
        self.assertIn('960 × 480', dialog.media_rows['thumb'][1].text())
        dialog.latlong.setChecked(False)
        self.assertIs(json.loads(dialog.metadata.toPlainText())['is_equirectangular'], False)
        dialog.kind.setCurrentIndex(dialog.kind.findData('footage'))
        self.assertTrue(dialog.latlong.isHidden())

    def test_still_folder_batch_carries_the_latlong_flag(self):
        from tinylib.ingest_ui import IngestDialog
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'library').mkdir()
        (root / 'hdris').mkdir()
        (root / 'hdris' / 'sky.exr').write_bytes(b'x')
        settings = {'libraries': [{'name': 'Test', 'root': str(root / 'library')}], 'profiles': {'Preview': {}},
                    'tools': {}}
        settings['_access'] = AccessControl(settings, identity='test', persist=False)
        dialog = IngestDialog(settings, [])
        self.addCleanup(dialog.close)
        dialog.kind.setCurrentIndex(dialog.kind.findData('still'))
        dialog.source_selected(str(root / 'hdris'))
        self.assertEqual(dialog._batch_folder, str(root / 'hdris'))
        self.assertFalse(dialog.latlong.isHidden(), 'The lat-long flag applies to the whole folder')
        self.assertTrue(dialog.pairing_fields['filmstrip'][0].isHidden(), 'Stills have no filmstrip')
        self.assertFalse(dialog.pairing_fields['proxy'][0].isHidden())
        from tinylib.qt import QtWidgets
        buttons = [button.text() for button in dialog.pairing_fields['proxy'][1].findChildren(QtWidgets.QPushButton)]
        self.assertEqual(buttons, ['Folder…'], 'Component folders take folders only')
        self.assertEqual(sorted(dialog.pairing_use), ['filmstrip', 'proxy', 'thumb'])
        dialog.pairing_use['thumb'].setCurrentIndex(dialog.pairing_use['thumb'].findData('generate'))
        dialog.category_mode.setCurrentIndex(dialog.category_mode.findData('new'))
        dialog.new_category_name.setText('hdri')
        dialog.latlong.setChecked(True)
        args = dialog.manifest_arguments()
        self.assertEqual(args[12], 'still')
        self.assertIs(args[13]['is_equirectangular'], True)
        self.assertEqual(args[13]['pairing']['use']['thumb'], 'generate')
        self.assertEqual(args[13]['pairing']['use']['proxy'], 'auto')


if __name__ == '__main__':
    unittest.main()
