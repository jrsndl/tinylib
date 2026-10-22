"""The 360 viewer on a real HDRI: proxy, main (HDR) decoding, navigation, grading, screenshots.

    python tests/qt_pano.py           # checks, then closes the window
    python tests/qt_pano.py --show    # opens the window to try it by hand

The viewer needs OpenGL, which Qt's offscreen platform does not have on Windows, so on
Windows the window briefly appears even under tests/run_scripts.py. Screenshots go to
artifacts/pano/.
"""
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
interactive = '--show' in sys.argv
if os.name == 'nt':
    os.environ.pop('QT_QPA_PLATFORM', None)  # offscreen has no OpenGL on Windows
elif not interactive:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tinylib.qt import QtWidgets
from tinylib.player import Player
from tinylib.settings import load_settings

root = Path(__file__).resolve().parents[1]
settings = load_settings(root / 'config/studio.json')
hdri = root / 'testdata/hdrilib/plhn_indoor_abandonedBakery'
name = hdri.name
asset = {'id': 'HDRI/' + name, 'name': name, 'category': 'HDRI', 'kind': 'still',
         'main': str(hdri / 'main' / (name + '.exr')), 'proxy': str(hdri / 'proxy' / (name + '.jpg')),
         'thumb': str(hdri / 'thumb' / (name + '.jpg')), 'highres': str(hdri / 'highres' / (name + '.exr')),
         'metadata': {'is_equirectangular': True}}
output = root / 'artifacts/pano'
output.mkdir(parents=True, exist_ok=True)

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
player = Player(settings)
errors, ratings = [], []
player.error.connect(errors.append)
player.rating_requested.connect(lambda selected, rating: ratings.append(rating))
player.play(asset)
assert len(player.windows) == 1, 'A lat-long still must open the 360 viewer'
window = next(iter(player.windows))
assert window.__class__.__name__ == 'PanoViewer'
if interactive:
    sys.exit(app.exec() if hasattr(app, 'exec') else app.exec_())


def wait(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    return condition()


def shot(label):
    image = window.view.grabFramebuffer()
    image.save(str(output / (label + '.png')))
    return image


view = window.view
wait(lambda: view.isVisible())
assert not view.failure, view.failure
assert view.sizes['proxy'] == (2048, 1024), view.sizes
first = shot('proxy-ahead')
assert not first.isNull()
view.yaw, view.pitch = 90.0, 20.0
view.update()
turned = shot('proxy-right-up')
assert turned != first, 'Turning the view must change the picture'
assert window.mode_button.text() == 'Proxy' and not window.mode_button.isChecked()
assert window.exposure.isHidden() and window.gamma_label.isHidden(), 'No grading controls on the proxy'
assert window.highres_box.isEnabled() and not window.highres_box.isChecked(), 'Highres is off by default'
window.mode_button.click()  # one toggle: proxy -> main
assert view.mode == 'main' and window.mode_button.text() == 'Main (HDR)'
assert view.message == 'Loading the main…'
assert wait(lambda: view.has('main') or errors), 'The main did not load'
assert not errors, errors
assert view.sizes['main'] == (4096, 2048), view.sizes
assert window.main_info['peak'] > 1.0, 'The main must stay scene-linear'
assert not window.exposure.isHidden() and not window.gamma.isHidden()
main = shot('main-right-up')
window.exposure.setValue(-30)
darker = shot('main-minus-3-stops')
assert darker != main, 'Exposure must change the main'
window.exposure_label.clicked.emit()  # clicking the caption resets the slider to the middle
assert window.exposure.value() == 0 and view.exposure == 0
window.gamma.setValue(60)
assert abs(view.gamma - 2.297) < 0.01, view.gamma
window.gamma_label.clicked.emit()
assert window.gamma.value() == 0 and view.gamma == 1
view.flat = True
flat = shot('main-flat')
view.flat_center, view.flat_zoom = (0.25, 0.4), 4.0
view.update()
assert shot('main-flat-zoomed') != flat, 'Zoom and pan must change the flat view'
view.reset_view()
assert (view.flat_center, view.flat_zoom) == ((0.5, 0.5), 1.0)
view.flat = False
window.highres_box.setChecked(True)  # the HDR image becomes the highres
assert window.mode_button.text() == 'Highres (HDR)' and view.message == 'Loading the highres…'
assert wait(lambda: view.has('main') or errors, 180), 'The highres did not load'
assert not errors, errors
assert window.main_info['label'] == 'Highres'
assert window.main_info['source_width'] > 4096, window.main_info
assert view.sizes['main'][0] <= 8192, view.sizes
shot('highres')
window.highres_box.setChecked(False)
assert wait(lambda: view.has('main') or errors), 'The main did not load again'
assert window.main_info['label'] == 'Main'
window.mode_button.click()
assert view.mode == 'proxy'
assert window.exposure.isHidden(), 'Grading applies to the HDR image only'
# PgUp/PgDn keep flat, HDR, highres and grading for the next asset.
window.view.flat = True
window.mode_button.click()  # back to the HDR main
window.exposure.setValue(-20)
state = window.carry_state()
assert state['flat'] and state['hdr'] and state['exposure'] == -20, state
from tinylib.pano_player import PanoViewer
following = PanoViewer(settings, asset)
following.apply_state(state)
assert following.view.flat and following.view.mode == 'main' and following.exposure.value() == -20
following.close()
window.mode_button.click()
window.rating_requested.emit(4)
assert ratings == [4], ratings
window.close()
assert wait(lambda: not player.windows), 'Closing the viewer must release it'
print('360 viewer passed: proxy, main %dx%d (peak %.0f), navigation, exposure, flat view. Screenshots in %s'
      % (view.sizes['main'] + (window.main_info['peak'], output)))
