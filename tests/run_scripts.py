"""Run the script-style Qt, player and processing checks and summarise the results.

    python tests/run_scripts.py            # every qt_*.py and *_smoke.py
    python tests/run_scripts.py qt_smoke   # only the named scripts

The unit tests are separate:  python -m unittest discover -s tests

Each script runs in its own process with QT_QPA_PLATFORM=offscreen. A script
whose sample data, configuration or tools are missing on this machine is
reported as "skip" with the reason; screenshots and outputs go to artifacts/.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _file(relative, *alternatives):
    options = (relative,) + alternatives
    return lambda: None if any((ROOT / item).exists() for item in options) else 'needs ' + ' or '.join(options)


def _tools(*names):
    """Tools as configured in config/studio.json (bare names must be on PATH)."""
    def check():
        try:
            sys.path.insert(0, str(ROOT))
            from tinylib.settings import load_settings
            tools = load_settings(ROOT / 'config' / 'studio.json').get('tools', {})
        except Exception as error:
            return 'cannot read config/studio.json: %s' % error
        missing = [name for name in names
                   if not (Path(tools.get(name, name)).is_file() or shutil.which(tools.get(name, name)))]
        return 'needs %s (set tools in config/studio.json or add to PATH)' % ', '.join(missing) if missing else None
    return check


def _qt():
    import importlib.util
    found = any(importlib.util.find_spec(name) for name in ('PySide6', 'PySide2'))
    return None if found else 'needs PySide6 or PySide2'


# Local prerequisites. testdata/, executables/ and the demo/performance configs are not in the repo.
REQUIREMENTS = {
    'qt_features': [_file('config/demo.json', 'config/demo.local.json'), _file('testdata')],
    'qt_smoke': [_file('config/demo.json', 'config/demo.local.json'), _file('testdata')],
    'qt_performance': [_file('config/performance.json', 'config/performance.local.json')],
    'qt_player': [_tools('ffmpeg', 'ffprobe')],
    'qt_pano': [_file('testdata/hdrilib'), _tools('ffmpeg', 'ffprobe')],
    'player_smoke': [_tools('ffmpeg', 'ffprobe', 'ffplay')],
    'processing_smoke': [_file('testdata/demolib'), _file('testdata/hdrilib'), _tools('ffmpeg', 'ffprobe', 'oiiotool')],
    'vfx_smoke': [_file('testdata/demolib'), _tools('ffmpeg', 'ffprobe', 'oiiotool', 'vfx_transcode')],
}


NO_QT = {'processing_smoke', 'vfx_smoke'}


def missing_requirement(name):
    needs_qt = name not in NO_QT
    for check in ([_qt] if needs_qt else []) + REQUIREMENTS.get(name, []):
        reason = check()
        if reason:
            return reason
    return None


def scripts(names):
    # qt_smoke.py matches both patterns; keep each script once.
    found = list(dict.fromkeys(sorted(HERE.glob('qt_*.py')) + sorted(HERE.glob('*_smoke.py'))))
    if names:
        wanted = {name[:-3] if name.endswith('.py') else name for name in names}
        found = [path for path in found if path.stem in wanted]
    return found


def main(argv):
    environment = dict(os.environ, QT_QPA_PLATFORM=os.environ.get('QT_QPA_PLATFORM', 'offscreen'))
    environment['PYTHONPATH'] = os.pathsep.join(filter(None, [str(HERE.parent), environment.get('PYTHONPATH')]))
    results = []
    skipped = 0
    for path in scripts(argv):
        reason = missing_requirement(path.stem)
        if reason:
            skipped += 1
            print('%-22s skip  %s' % (path.stem, reason), flush=True)
            continue
        started = time.monotonic()
        # Never with -O: the scripts check results with assert.
        process = subprocess.run([sys.executable, str(path)], cwd=str(HERE.parent), env=environment,
                                 capture_output=True, text=True, errors='replace')
        seconds = time.monotonic() - started
        ok = process.returncode == 0
        results.append((path.stem, ok, seconds))
        print('%-22s %s  %5.1f s' % (path.stem, 'ok  ' if ok else 'FAIL', seconds), flush=True)
        if not ok:
            print('    ' + (process.stderr or process.stdout).strip().replace('\n', '\n    ')[-3000:])
    failed = [name for name, ok, _ in results if not ok]
    print('\n%d passed, %d failed, %d skipped' % (len(results) - len(failed), len(failed), skipped))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
