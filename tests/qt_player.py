"""Real decoding and Qt input tests for TinyLib's interactive review transport."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtCore, QtGui, QtWidgets
if QtCore.__name__.startswith('PySide6'):
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest
from tinylib.player import Player, sibling_tool
from tinylib.settings import load_settings

root = Path(__file__).resolve().parents[1]
settings = load_settings(root / 'config/studio.json')
settings['player_backend'] = 'interactive'
output = root / 'artifacts/interactive-player'
output.mkdir(parents=True, exist_ok=True)
media = output / 'frame test with spaces.mp4'
ffmpeg = settings['tools']['ffmpeg']
# A fractional-FPS, non-keyframe-aligned clip: every decoded image is unique.
subprocess.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=320x180:rate=24000/1001', '-frames:v', '120',
                '-c:v', 'libx264', '-g', '48', '-pix_fmt', 'yuv420p', str(media)], check=True)
reference = subprocess.run([ffmpeg, '-v', 'error', '-i', str(media), '-an', '-pix_fmt', 'rgb24',
                            '-f', 'rawvideo', 'pipe:1'], check=True, capture_output=True).stdout
frame_bytes = 320 * 180 * 3
assert len(reference) == 120 * frame_bytes
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QtGui.QFont('Segoe UI', 10))
controller = Player(settings)
errors = []
ratings = []
controller.error.connect(errors.append)
controller.rating_requested.connect(lambda asset, rating: ratings.append((asset['name'], rating)))


def pump(ms=30):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()
        QtCore.QThread.msleep(2)


def wait(predicate, label):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while not predicate() and not errors and timer.elapsed() < 10000:
        pump(10)
    assert not errors, errors
    assert predicate(), label


def key(key):
    QTest.keyClick(window, key)


def exact_frame(frame):
    window.seek(frame)
    wait(lambda: not window.pending, 'frame decode')
    image = window.picture
    bits = image.constBits()
    if hasattr(bits, 'setsize'):
        bits.setsize(frame_bytes)
    actual = bytes(bits)[:frame_bytes]
    expected = reference[frame * frame_bytes:(frame + 1) * frame_bytes]
    assert hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest(), ('wrong frame', frame, window.current)
    assert window.current == frame


controller.play({'name': 'Review controls', 'proxy': str(media), 'kind': 'footage', 'first': 1001})
wait(lambda: bool(controller.windows), 'player launch')
window = next(iter(controller.windows))
wait(lambda: window.current >= 2, 'autoplay')
assert window.loop and window.loop_toggle.isChecked()
key(QtCore.Qt.Key_5)
key(QtCore.Qt.Key_0)
assert ratings == [('Review controls', 5), ('Review controls', 0)]
assert window.rating == 0 and window.rating_feedback == 'Rating: 0 / 5'
key(QtCore.Qt.Key_K)
assert window.direction == 0 and window.speed == 1
window.decoder.limit = 64  # Force eviction during random-access tests.
# Random access and both sides of each decode chunk must match sequential decode.
for frame in (0, 31, 32, 63, 64, 96, 119, 65, 33, 1):
    exact_frame(frame)
assert len(window.decoder.cache) <= 64
# Cancel stale decoding requests while scrubbing rapidly across the clip.
window.decoder.cache.clear()
for frame in (2, 115, 40, 95, 1):
    window.seek(frame)
wait(lambda: not window.pending, 'latest seek wins')
exact_frame(1)
key(QtCore.Qt.Key_Right)
wait(lambda: not window.pending, 'step right')
assert window.current == 2 and window.direction == 0
key(QtCore.Qt.Key_Left)
wait(lambda: not window.pending, 'step left')
assert window.current == 1
for speed in (1, 2, 4, 8, 16, 16):
    key(QtCore.Qt.Key_L)
    assert window.direction == 1 and window.speed == speed
key(QtCore.Qt.Key_K)
assert window.direction == 0 and window.speed == 1
exact_frame(60)
for speed in (1, 2, 4):
    key(QtCore.Qt.Key_J)
    assert window.direction == -1 and window.speed == speed
wait(lambda: window.current < 60, 'reverse playback')
key(QtCore.Qt.Key_Space)
assert window.direction == 0 and window.speed == 1
key(QtCore.Qt.Key_Space)
assert window.direction == 1 and window.speed == 1
key(QtCore.Qt.Key_K)
key(QtCore.Qt.Key_O)
assert not window.overlay
key(QtCore.Qt.Key_O)
assert window.overlay
# Default loop wraps forward and backward, without resetting shuttle speed.
exact_frame(119)
key(QtCore.Qt.Key_L)
wait(lambda: window.current < 20, 'forward loop')
assert window.direction == 1
key(QtCore.Qt.Key_K)
exact_frame(0)
key(QtCore.Qt.Key_J)
wait(lambda: window.current > 100, 'reverse loop')
assert window.direction == -1
key(QtCore.Qt.Key_K)
window.loop_toggle.setChecked(False)
exact_frame(118)
key(QtCore.Qt.Key_L)
key(QtCore.Qt.Key_L)
wait(lambda: window.direction == 0 and not window.pending, 'stop at end')
assert window.current == 119 and window.speed == 1
exact_frame(1)
key(QtCore.Qt.Key_J)
wait(lambda: window.direction == 0 and not window.pending, 'stop at start')
assert window.current == 0 and window.speed == 1
# Hover and real press/move/release events on the timeline.
QTest.mouseMove(window, QtCore.QPoint(window.width() // 2, 100))
pump()
assert window.bar.isHidden()
QTest.mouseMove(window, QtCore.QPoint(window.width() // 2, window.height() - 85))
pump()
assert window.bar.isVisible()
slider = window.timeline
QTest.mousePress(slider, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(slider.width() // 4, slider.height() // 2))
QTest.mouseMove(slider, QtCore.QPoint(slider.width() * 3 // 4, slider.height() // 2))
QTest.mouseRelease(slider, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(slider.width() * 3 // 4, slider.height() // 2))
wait(lambda: not window.pending, 'scrub decode')
assert 85 <= window.current <= 95 and window.direction == 0
assert slider.value() == window.current
exact_frame(window.current)
window.grab().save(str(output / ('player-' + QtCore.__name__.split('.')[0] + '.png')))
window.loop_toggle.setChecked(True)
controller.stop()
pump()
assert not controller.windows and not controller.jobs
# Still previews remain stable and allow overlay/timeline UI without playback.
still = output / 'still.jpg'
subprocess.run([ffmpeg, '-y', '-v', 'error', '-i', str(media), '-frames:v', '1', str(still)], check=True)
controller.play({'name': 'Still', 'proxy': str(still), 'kind': 'hdri'})
wait(lambda: bool(controller.windows), 'still launch')
window = next(iter(controller.windows))
wait(lambda: not window.pending, 'still decode')
assert window.count == 1 and window.direction == 0
key(QtCore.Qt.Key_L)
assert window.direction == 0
controller.stop()
pump()
assert not errors
print('Interactive player passed:', QtCore.__name__, 'exact frames, autoplay, JKL, speed reset, steps, overlay, scrub, loop on/off, stills, cleanup')
