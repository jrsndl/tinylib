import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tinylib.metadata_scan import (format_scan_summary, probe_container,
                                   probe_image, scan_asset_metadata, scan_assets)


class MetadataScanTests(unittest.TestCase):
    def test_oiiotool_xml_and_sequence_metadata(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        main = root / 'cat' / 'Plate' / 'main'
        main.mkdir(parents=True)
        (main / 'plate.1001.exr').write_bytes(b'123')
        (main / 'plate.1002.exr').write_bytes(b'4567')
        xml = '''<ImageSpec version="26"><width>2048</width><height>1024</height>
        <attrib name="PixelAspectRatio" type="float">1</attrib>
        <attrib name="oiio:ColorSpace" type="string">ACEScg</attrib>
        <attrib name="FramesPerSecond" type="rational">24/1</attrib></ImageSpec>'''
        asset = {'id': 'cat/Plate', 'name': 'Plate', 'category': 'cat', 'kind': 'footage',
                 'main': str(main / 'plate.####.exr'), 'first': 1001, 'last': 1002,
                 'library_root': str(root), 'metadata': {'width': 1920}}
        settings = {'tools': {'oiiotool': 'oiiotool'}, 'extension_groups': {}}
        with patch('tinylib.metadata_scan._run', return_value=xml):
            scanned = scan_asset_metadata(asset, settings)
        self.assertEqual(scanned['width'], 2048)
        self.assertEqual(scanned['duration_frames'], 2)
        self.assertEqual(scanned['duration_seconds'], 2 / 24)
        self.assertEqual(scanned['file_size_main'], 7)
        self.assertEqual(scanned['FPS'], 24)
        full = {'tool': {'format': 'oiiotool_xml', 'xml': xml}, 'normalized': scanned}
        with patch('tinylib.metadata_scan.scan_asset_metadata', return_value=(scanned, full)):
            result = scan_assets([asset], settings)
        self.assertIn('width', result['results'][0]['differences'])
        self.assertEqual(set(result['results'][0]['scanned']), {'FPS', 'width', 'height'})
        sidecar = main / 'plate_meta.json'
        self.assertTrue(sidecar.is_file())
        self.assertIn('oiiotool_xml', sidecar.read_text(encoding='utf-8'))
        self.assertEqual(result['sidecars'], [str(sidecar)])
        self.assertIn('METADATA DISCREPANCIES (1 assets)', format_scan_summary(result))

    def test_ffprobe_container_metadata(self):
        payload = '''{"streams":[{"width":1920,"height":1080,"sample_aspect_ratio":"1:1",
        "avg_frame_rate":"24000/1001","nb_frames":"120","duration":"5.005",
        "color_space":"bt709","tags":{"timecode":"01:00:00:00"}}],
        "format":{"tags":{"reel_name":"A001"}}}'''
        with patch('tinylib.metadata_scan._run', return_value=payload):
            scanned = probe_container('clip.mov', 'ffprobe')
        self.assertEqual(scanned['duration_frames'], 120)
        self.assertAlmostEqual(scanned['FPS'], 24000 / 1001)
        self.assertEqual(scanned['timecode'], '01:00:00:00')
        self.assertEqual(scanned['reelid'], 'A001')

    def test_oiiotool_probe_parses_required_fields(self):
        xml = '<ImageSpec><width>10</width><height>5</height><attrib name="oiio:ColorSpace" type="string">sRGB</attrib></ImageSpec>'
        with patch('tinylib.metadata_scan._run', return_value=xml):
            self.assertEqual(probe_image('still.jpg', 'oiiotool')['colorspace'], 'sRGB')


if __name__ == '__main__':
    unittest.main()
