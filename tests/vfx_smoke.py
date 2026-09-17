"""Exercise the installed optional vfx-transcode backend on three copied frames."""
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinylib.settings import load_settings
from tinylib.ingest import make_manifest, save_manifest
from tinylib.processing import process

root = Path(__file__).resolve().parents[1]
settings = load_settings(root / 'config/studio.json')
destination = root / 'artifacts/vfx-smoke' / uuid.uuid4().hex[:8]
destination.mkdir(parents=True)
source = str(root / 'testdata/demolib/fire/Big_Fire_01/main/Big_Fire_01.####.exr') + ' 1001-1003'
job = make_manifest(settings, {'root': str(destination), 'name': 'VFX smoke'},
    'VFX test', 'fire', source, ['fire'], 'ACES - ACEScg', 'VFX Transcode — Rec.709',
    {k: {'mode': 'generate'} for k in ['proxy', 'thumb', 'filmstrip']}, fps=24, preview_source='main')
process(save_manifest(job, destination / 'jobs'))
print('VFX Transcode smoke passed.')
