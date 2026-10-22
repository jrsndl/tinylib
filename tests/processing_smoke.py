"""Real OIIO/FFmpeg conversions, confined to artifacts/processing-smoke."""
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinylib.settings import load_settings
from tinylib.ingest import make_manifest, save_manifest
from tinylib.processing import process, probe
from tinylib.library import Library
from helpers import test_access

root = Path(__file__).resolve().parents[1]
settings = load_settings(root / 'config/studio.json')
settings['_access'] = test_access(root / 'config/studio.json')
test_root = root / 'artifacts/processing-smoke' / uuid.uuid4().hex[:8]
test_root.mkdir(parents=True, exist_ok=True)
library = {'root': str(test_root), 'name': 'Processing smoke'}
source_root = root / 'testdata/demolib/fire/Big_Fire_01/main'
sequence = str(source_root / 'Big_Fire_01.####.exr') + ' 1001-1003'
media = {k: {'mode': 'generate'} for k in ['proxy', 'thumb', 'filmstrip']}
job = make_manifest(settings, library, 'Three frames', 'test', sequence, ['fire', 'smoke'],
                    'ACES - ACEScg', 'ACES to Rec.709', media, fps=23.976)
path = save_manifest(job, test_root / 'jobs')
record = process(path, access=settings['_access'])
sequence_record = record
assert probe(record['filmstrip'], settings['tools'])['width'] == 11520
assert process(path, access=settings['_access'])['id'] == record['id'], 'Completed jobs should be idempotent'
still = root / 'testdata/hdrilib/plhn_indoor_abandonedBakery/main/plhn_indoor_abandonedBakery.exr'
job = make_manifest(settings, library, 'Bakery', 'HDRI', str(still), ['interior'],
                    'ACES - ACEScg', 'ACES to Rec.709', media, kind='still')
path = save_manifest(job, test_root / 'jobs')
record = process(path, access=settings['_access'])
assert not record.get('filmstrip')
assert probe(record['thumb'], settings['tools'])['height'] == 506
assert len(Library(test_root).load()) == 2
# The same HDRI as a lat-long still: generated proxy and thumbnail stay 2:1.
job = make_manifest(settings, library, 'Bakery latlong', 'HDRI', str(still), ['interior'],
                    'ACES - ACEScg', 'ACES to Rec.709', {k: {'mode': 'generate'} for k in ['proxy', 'thumb']},
                    kind='still', metadata={'is_equirectangular': True})
record = process(save_manifest(job, test_root / 'jobs'), access=settings['_access'])
assert record['metadata']['is_equirectangular'] is True
proxy_info, thumb_info = probe(record['proxy'], settings['tools']), probe(record['thumb'], settings['tools'])
assert (proxy_info['width'], proxy_info['height']) == (4096, 2048), proxy_info
assert (thumb_info['width'], thumb_info['height']) == (960, 480), thumb_info
# A ready-made HDRI: main, proxy, thumbnail and highres all supplied and copied as they are.
hdri = root / 'testdata/hdrilib/plhn_indoor_abandonedConstruction'
name = hdri.name
supplied_hdri = {'proxy': {'mode': 'supply', 'path': str(hdri / 'proxy' / (name + '.jpg'))},
                 'thumb': {'mode': 'supply', 'path': str(hdri / 'thumb' / (name + '.jpg'))}}
job = make_manifest(settings, library, name, 'HDRI', str(hdri / 'main' / (name + '.exr')), ['interior'],
                    'ACES - ACEScg', '', supplied_hdri, kind='still', metadata={'is_equirectangular': True},
                    highres=str(hdri / 'highres' / (name + '.exr')))
record = process(save_manifest(job, test_root / 'jobs'), access=settings['_access'])
assert Path(record['proxy']).read_bytes() == (hdri / 'proxy' / (name + '.jpg')).read_bytes()
assert Path(record['highres']).stat().st_size == (hdri / 'highres' / (name + '.exr')).stat().st_size
assert len(Library(test_root).load()) == 4
supplied = {k: {'mode': 'supply', 'path': sequence_record[k]} for k in ['proxy', 'thumb', 'filmstrip']}
job = make_manifest(settings, library, 'Supplied previews', 'test', sequence, ['supplied'],
                    'ACES - ACEScg', '', supplied, fps=23.976,
                    highres=str(source_root / 'Big_Fire_01.1001.exr'))
record = process(save_manifest(job, test_root / 'jobs'), access=settings['_access'])
assert Path(record['highres']).read_bytes() == (source_root / 'Big_Fire_01.1001.exr').read_bytes()
assert len(Library(test_root).load()) == 5
print('Real processing smoke passed: sequence, proxy-derived strip/thumb, HDRI, lat-long HDRIs (generated and supplied), supplied media, highres copy, dimensions, idempotence.')
