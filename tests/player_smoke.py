"""Real FFmpeg frame-overlay rendering and ffprobe/ffplay QProcess playback."""
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_RENDER_DRIVER'] = 'software'
from tinylib.qt import QtCore
from tinylib.player import Player, frame_filter, play_arguments, sibling_tool
from tinylib.settings import load_settings

root = Path(__file__).resolve().parents[1]
settings = load_settings(root / 'config/studio.json')
settings['player_backend'] = 'ffplay'
output = root / 'artifacts/player-smoke'
output.mkdir(parents=True, exist_ok=True)
media = output / 'preview with spaces.mp4'
subprocess.run([settings['tools']['ffmpeg'], '-y', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=640x360:rate=24', '-t', '1', '-c:v', 'libx264', str(media)], check=True)
asset = {'name': 'Playback test', 'proxy': str(media), 'kind': 'footage', 'first': 1001}
stream = {'avg_frame_rate': '24/1', 'start_time': '0'}
assert '1001' in frame_filter(dict(asset, kind='still'), {'avg_frame_rate': '0/0'})
assert '24000/1001' in frame_filter(asset, {'avg_frame_rate': '0/0', 'r_frame_rate': '24000/1001'})
try:
    frame_filter(asset, {'avg_frame_rate': '0/0'})
    raise AssertionError('Missing footage FPS must fail')
except ValueError:
    pass
subprocess.run([settings['tools']['ffmpeg'], '-y', '-v', 'error', '-i', str(media), '-vf',
                frame_filter(asset, stream), '-frames:v', '2', str(output / 'frame-%02d.png')], check=True)
app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
player = Player(settings)
errors, started, ended = [], [], []
player.error.connect(errors.append)
player.started.connect(lambda: started.append(True))
player.finished.connect(lambda: ended.append(True))
# Keep production arguments and add only an automatic test exit at EOF.
original_start = QtCore.QProcess.start
def start(process, program, arguments):
    if program == sibling_tool(settings, 'ffplay'):
        arguments = ['-autoexit'] + arguments
    return original_start(process, program, arguments)
QtCore.QProcess.start = start
player.play(asset)
timer = QtCore.QElapsedTimer()
timer.start()
while timer.elapsed() < 15000 and not ended and not errors:
    app.processEvents()
    QtCore.QThread.msleep(10)
assert started and ended and not errors, (started, ended, errors)
assert not player.jobs
QtCore.QProcess.start = original_start
print('Player passed: actual ffprobe/ffplay autoplay and source-frame overlay, including paths with spaces.')
