"""Exercise the vfx-transcode backend (the default for EXR sequences) on three copied frames."""
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinylib.settings import load_settings
from tinylib.ingest import make_manifest, save_manifest
from tinylib.processing import process
from helpers import test_access

root = Path(__file__).resolve().parents[1]
settings = load_settings(root / 'config/studio.json')
settings['_access'] = test_access(root / 'config/studio.json')
# Force the backend, so a missing vfx-transcode fails the smoke instead of falling back.
settings['profiles']['ACES to Rec.709']['proxy_backend'] = 'vfx_transcode'
destination = root / 'artifacts/vfx-smoke' / uuid.uuid4().hex[:8]
destination.mkdir(parents=True)
source = str(root / 'testdata/demolib/fire/Big_Fire_01/main/Big_Fire_01.####.exr') + ' 1001-1003'
job = make_manifest(settings, {'root': str(destination), 'name': 'VFX smoke'},
    'VFX test', 'fire', source, ['fire'], 'ACES - ACEScg', 'ACES to Rec.709',
    {k: {'mode': 'generate'} for k in ['proxy', 'thumb', 'filmstrip']}, fps=24, preview_source='main')
record = process(save_manifest(job, destination / 'jobs'), access=settings['_access'])
for key in ('proxy', 'thumb', 'filmstrip'):
    if not Path(record[key]).is_file():
        raise SystemExit('VFX Transcode smoke failed: %s was not published (%s)' % (key, record[key]))
if record['metadata']['duration_frames'] != 3:
    raise SystemExit('VFX Transcode smoke failed: expected 3 frames, got %r' % record['metadata']['duration_frames'])
print('VFX Transcode smoke passed.')
