"""360 viewer for lat-long stills (HDRIs): look around inside the sphere.

One OpenGL fragment shader turns each view pixel into a direction and samples the
lat-long image there, so there is no sphere mesh, no pole pinching and no seam. The
proxy is a tonemapped JPEG, shown as it is. The main (EXR or HDR) is decoded in the
background into an RGBE texture and shown scene-linear through gain, gamma and the
sRGB curve, like Nuke's viewer.
"""
import math
import threading
from pathlib import Path

from .qt import QtCore, QtGui, QtWidgets, event_pos
from .asset_fields import display_name
from . import pano

if QtCore.__name__.startswith('PySide6'):
    from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture, QOpenGLVertexArrayObject
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
else:
    from PySide2.QtGui import QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture, QOpenGLVertexArrayObject
    from PySide2.QtWidgets import QOpenGLWidget

GL_TRIANGLES = 0x0004
GL_COLOR_BUFFER_BIT = 0x4000

VERTEX_SHADER = """
#version 130
out vec2 ndc;
void main() {
    // One triangle covering the view, from the vertex index alone: no vertex buffer.
    vec2 p = vec2(float((gl_VertexID & 1) << 2) - 1.0, float((gl_VertexID & 2) << 1) - 1.0);
    ndc = p;
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 130
in vec2 ndc;
uniform sampler2D tex;
uniform vec2 texSize;
uniform int hdr;          // 1: RGBE main, scene-linear; 0: display-referred proxy
uniform int flatView;     // 1: the flat lat-long image, fitted at zoom 1
uniform vec2 flatCenter;  // flat view: image (u, v) in the middle of the view
uniform float flatZoom;   // flat view: 1 = the whole image fitted
uniform vec3 forward;
uniform vec3 right;
uniform vec3 up;
uniform float tanHalf;    // tan(horizontal field of view / 2)
uniform float aspect;     // view width / height
uniform float gain;       // 2^exposure
uniform float invGamma;   // 1 / gamma
const float PI = 3.14159265358979;

vec3 rgbe(vec2 texel) {
    vec4 c = texture(tex, (texel + 0.5) / texSize) * 255.0;
    if (c.a < 0.5) return vec3(0.0);
    return (c.rgb + 0.5) / 256.0 * exp2(c.a - 128.0);
}

vec3 sampleHdr(vec2 uv) {
    // RGBE cannot be filtered by the GPU; decode four texels and blend them here.
    vec2 t = uv * texSize - 0.5;
    vec2 b = floor(t);
    vec2 f = t - b;
    float y0 = clamp(b.y, 0.0, texSize.y - 1.0);
    float y1 = clamp(b.y + 1.0, 0.0, texSize.y - 1.0);
    vec3 top = mix(rgbe(vec2(b.x, y0)), rgbe(vec2(b.x + 1.0, y0)), f.x);
    vec3 bottom = mix(rgbe(vec2(b.x, y1)), rgbe(vec2(b.x + 1.0, y1)), f.x);
    return mix(top, bottom, f.y);
}

vec3 sampleLdr(vec2 uv) {
    // u jumps from 1 to 0 behind the viewer; take the derivative of the shifted coordinate
    // there, so the mipmap level stays right and no seam line appears.
    vec2 dx = dFdx(uv);
    vec2 dy = dFdy(uv);
    float shifted = fract(uv.x + 0.5);
    float sx = dFdx(shifted);
    float sy = dFdy(shifted);
    if (abs(sx) < abs(dx.x)) dx.x = sx;
    if (abs(sy) < abs(dy.x)) dy.x = sy;
    return textureGrad(tex, uv, dx, dy).rgb;
}

float srgb(float x) {
    return x <= 0.0031308 ? 12.92 * x : 1.055 * pow(x, 1.0 / 2.4) - 0.055;
}

void main() {
    vec2 uv;
    if (flatView == 1) {
        vec2 scale = aspect > 2.0 ? vec2(2.0 / aspect, 1.0) : vec2(1.0, aspect / 2.0);
        uv = flatCenter + vec2(ndc.x / scale.x, -ndc.y / scale.y) * 0.5 / flatZoom;
        if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
            gl_FragColor = vec4(0.063, 0.063, 0.063, 1.0);
            return;
        }
    } else {
        vec3 d = normalize(forward + right * (ndc.x * tanHalf) + up * (ndc.y * tanHalf / aspect));
        uv = vec2(0.5 + atan(d.x, -d.z) / (2.0 * PI), 0.5 - asin(clamp(d.y, -1.0, 1.0)) / PI);
    }
    vec3 color;
    if (hdr == 1) {
        color = pow(max(sampleHdr(uv) * gain, 0.0), vec3(invGamma));
        color = clamp(color, 0.0, 1.0);
        color = vec3(srgb(color.r), srgb(color.g), srgb(color.b));
    } else {
        color = sampleLdr(uv);
    }
    gl_FragColor = vec4(color, 1.0);
}
"""


class MainLoader(QtCore.QObject):
    """Decodes the main on a Python thread; the result returns to the GUI thread as a signal."""
    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def start(self, path, settings, width, height):
        def work():
            try:
                rgbe, info = pano.load_main(path, settings, width, height)
                rows, columns = rgbe.shape[:2]
                data = rgbe.tobytes()
                del rgbe
                image = QtGui.QImage(data, columns, rows, columns * 4, QtGui.QImage.Format_RGBA8888).copy()
                del data
                result = (image, info)
            except Exception as error:  # reported in the viewer, never raised on the thread
                result = str(error) or error.__class__.__name__
            try:
                if isinstance(result, str):
                    self.failed.emit(result)
                else:
                    self.loaded.emit(result)
            except RuntimeError:
                pass  # the viewer closed while decoding
        threading.Thread(target=work, name='tinylib-pano-main', daemon=True).start()


class ResetLabel(QtWidgets.QLabel):
    """A slider's caption; clicking it puts the slider back in the middle (neutral)."""
    clicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip('Click to reset')

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.isEnabled():
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


def gamma_value(position):
    """Gamma for a slider position -100..100: 0.25..4, with 1 in the middle."""
    return 2.0 ** (position / 50.0)


class PanoView(QOpenGLWidget):
    """The OpenGL view. State lives here; the window adds controls and keys."""
    view_changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMinimumSize(320, 180)
        self.yaw, self.pitch, self.fov = 0.0, 0.0, pano.DEFAULT_FOV
        self.flat = False
        self.flat_center, self.flat_zoom = (0.5, 0.5), 1.0
        self.mode = 'proxy'
        self.images = {}       # mode -> QImage waiting for upload
        self.textures = {}     # mode -> QOpenGLTexture
        self.sizes = {}        # mode -> (width, height)
        self.main_image = None   # RGBE, kept for the pixel readout
        self.proxy_image = None
        self.exposure, self.gamma = 0.0, 1.0
        self.message = ''
        self.failure = ''
        self.cursor = None
        self.drag = None
        self.program = None
        self.vao = None

    # --- content -----------------------------------------------------------------------------

    def set_image(self, mode, image):
        self.images[mode] = image
        self.sizes[mode] = (image.width(), image.height())
        if mode == 'main':
            self.main_image = image
        else:
            self.proxy_image = image
        self.update()

    def drop(self, mode):
        """Forget an image and its texture, e.g. before the main is replaced by the highres."""
        self.images.pop(mode, None)
        self.sizes.pop(mode, None)
        if mode == 'main':
            self.main_image = None
        texture = self.textures.pop(mode, None)
        if texture is not None:
            self.makeCurrent()
            texture.destroy()
            self.doneCurrent()
        self.update()

    def has(self, mode):
        return mode in self.images or mode in self.textures

    def set_mode(self, mode):
        self.mode = mode
        self.update()
        self.view_changed.emit()

    def reset_view(self):
        if self.flat:
            self.flat_center, self.flat_zoom = (0.5, 0.5), 1.0
        else:
            self.yaw, self.pitch, self.fov = 0.0, 0.0, pano.DEFAULT_FOV
        self.update()
        self.view_changed.emit()

    # --- OpenGL ------------------------------------------------------------------------------

    def initializeGL(self):
        functions = self.context().functions()
        functions.glClearColor(0.063, 0.063, 0.063, 1.0)
        program = QOpenGLShaderProgram(self)
        if not (program.addShaderFromSourceCode(QOpenGLShader.Vertex, VERTEX_SHADER)
                and program.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAGMENT_SHADER)
                and program.link()):
            self.failure = ('The 360 viewer needs OpenGL 3.0 (GLSL 1.30), which this graphics driver '
                            'does not provide:\n' + program.log())
            return
        self.program = program
        self.vao = QOpenGLVertexArrayObject(self)
        self.vao.create()
        self.context().aboutToBeDestroyed.connect(self.release_gl)

    def release_gl(self):
        self.makeCurrent()
        for texture in self.textures.values():
            texture.destroy()
        self.textures.clear()
        if self.vao is not None:
            self.vao.destroy()
            self.vao = None
        self.program = None
        self.doneCurrent()

    def upload(self, mode):
        image = self.images.pop(mode, None)
        if image is None:
            return
        old = self.textures.pop(mode, None)
        if old is not None:
            old.destroy()
        if mode == 'main':
            texture = QOpenGLTexture(image, QOpenGLTexture.DontGenerateMipMaps)
            texture.setMinMagFilters(QOpenGLTexture.Nearest, QOpenGLTexture.Nearest)
        else:
            texture = QOpenGLTexture(image.convertToFormat(QtGui.QImage.Format_RGBA8888))
            texture.setMinMagFilters(QOpenGLTexture.LinearMipMapLinear, QOpenGLTexture.Linear)
        if not texture.isCreated():
            self.failure = 'The graphics card could not hold the %s texture (%dx%d).' % ((mode,) + self.sizes[mode])
            return
        texture.setWrapMode(QOpenGLTexture.DirectionS, QOpenGLTexture.Repeat)
        texture.setWrapMode(QOpenGLTexture.DirectionT, QOpenGLTexture.ClampToEdge)
        self.textures[mode] = texture

    def paintGL(self):
        functions = self.context().functions()
        functions.glClear(GL_COLOR_BUFFER_BIT)
        if self.program is not None and not self.failure:
            self.upload(self.mode)
            texture = self.textures.get(self.mode)
            if texture is not None:
                self.draw(functions, texture)
        painter = QtGui.QPainter(self)
        self.paint_overlay(painter)
        painter.end()

    def draw(self, functions, texture):
        forward, right, up = pano.view_basis(self.yaw, self.pitch)
        width, height = self.sizes[self.mode]
        program = self.program
        program.bind()
        program.setUniformValue1i('tex', 0)
        program.setUniformValue('texSize', QtGui.QVector2D(width, height))
        program.setUniformValue1i('hdr', 1 if self.mode == 'main' else 0)
        program.setUniformValue1i('flatView', 1 if self.flat else 0)
        program.setUniformValue('flatCenter', QtGui.QVector2D(*self.flat_center))
        program.setUniformValue1f('flatZoom', self.flat_zoom)
        program.setUniformValue('forward', QtGui.QVector3D(*forward))
        program.setUniformValue('right', QtGui.QVector3D(*right))
        program.setUniformValue('up', QtGui.QVector3D(*up))
        program.setUniformValue1f('tanHalf', math.tan(math.radians(self.fov) / 2))
        program.setUniformValue1f('aspect', self.width() / max(1, self.height()))
        program.setUniformValue1f('gain', 2.0 ** self.exposure)
        program.setUniformValue1f('invGamma', 1.0 / max(0.01, self.gamma))
        texture.bind(0)
        self.vao.bind()
        functions.glDrawArrays(GL_TRIANGLES, 0, 3)
        self.vao.release()
        texture.release()
        program.release()

    # --- overlay -----------------------------------------------------------------------------

    def uv_at(self, point):
        x = (point.x() + 0.5) / max(1, self.width()) * 2 - 1
        y = 1 - (point.y() + 0.5) / max(1, self.height()) * 2
        aspect = self.width() / max(1, self.height())
        if self.flat:
            return pano.flat_uv(x, y, aspect, self.flat_center, self.flat_zoom)
        return pano.direction_to_uv(pano.view_ray(x, y, self.yaw, self.pitch, self.fov, aspect))

    def readout(self):
        if self.cursor is None or self.mode not in self.textures:
            return ''
        uv = self.uv_at(self.cursor)
        if uv is None:
            return ''
        image = self.main_image if self.mode == 'main' else self.proxy_image
        if image is None:
            return ''
        width, height = image.width(), image.height()
        color = image.pixelColor(min(width - 1, int(uv[0] * width)), min(height - 1, int(uv[1] * height)))
        if self.mode == 'main':
            pixel = (color.red(), color.green(), color.blue(), color.alpha())
            return 'Linear  R %.4g  G %.4g  B %.4g' % pano.decode_rgbe(pixel)
        return 'Display  R %d  G %d  B %d' % (color.red(), color.green(), color.blue())

    def paint_overlay(self, painter):
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
        text = self.failure or self.message
        if text:
            painter.setPen(QtGui.QColor('#eeeeee'))
            painter.drawText(self.rect().adjusted(24, 20, -24, -20), QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap, text)
        value = self.readout()
        if value:
            box = QtCore.QRect(12, self.height() - 40, 340, 28)
            painter.fillRect(box, QtGui.QColor(0, 0, 0, 170))
            painter.setPen(QtGui.QColor('#dddddd'))
            painter.drawText(box.adjusted(10, 0, 0, 0), QtCore.Qt.AlignVCenter, value)

    # --- navigation --------------------------------------------------------------------------

    def degrees_per_pixel(self):
        return self.fov / max(1, self.width())

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.drag = event_pos(event)
            self.setCursor(QtCore.Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = event_pos(event)
        self.cursor = point
        if self.drag is not None and self.flat:
            delta = point - self.drag
            self.drag = point
            # Pan: the image follows the mouse.
            scale = pano.flat_scale(self.width() / max(1, self.height()))
            u = self.flat_center[0] - delta.x() / (max(1, self.width()) * scale[0] * self.flat_zoom)
            v = self.flat_center[1] - delta.y() / (max(1, self.height()) * scale[1] * self.flat_zoom)
            self.flat_center = (pano.clamp(u, 0.0, 1.0), pano.clamp(v, 0.0, 1.0))
            self.view_changed.emit()
        elif self.drag is not None:
            delta = point - self.drag
            self.drag = point
            # Drag the picture: moving the mouse right turns the view left.
            self.yaw = (self.yaw - delta.x() * self.degrees_per_pixel() + 180.0) % 360.0 - 180.0
            self.pitch = pano.clamp(self.pitch + delta.y() * self.degrees_per_pixel(), -pano.MAX_PITCH, pano.MAX_PITCH)
            self.view_changed.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.reset_view()

    def leaveEvent(self, event):
        self.cursor = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if self.flat:
            # Zoom about the point under the mouse.
            zoom = pano.clamp(self.flat_zoom * 1.25 ** steps, 1.0, pano.MAX_FLAT_ZOOM)
            point = event_pos(event)
            x = (point.x() + 0.5) / max(1, self.width()) * 2 - 1
            y = 1 - (point.y() + 0.5) / max(1, self.height()) * 2
            self.flat_center = pano.flat_zoom_at(x, y, self.width() / max(1, self.height()),
                                                 self.flat_center, self.flat_zoom, zoom)
            self.flat_zoom = zoom
        else:
            self.fov = pano.clamp(self.fov * 0.9 ** steps, pano.MIN_FOV, pano.MAX_FOV)
        self.update()
        self.view_changed.emit()
        event.accept()


class PanoViewer(QtWidgets.QWidget):
    """Window for one lat-long still. Signals match ReviewPlayer, so Player treats both alike."""
    error = QtCore.Signal(str)
    closed = QtCore.Signal()
    rating_requested = QtCore.Signal(int)
    trim_requested = QtCore.Signal(object)  # never emitted: a still has no in/out points
    navigate_requested = QtCore.Signal(int)  # PgUp -1 / PgDn +1: the previous or next asset

    HELP = '\n'.join([  # one control per line
        'Drag: look around',
        'Wheel: field of view',
        'Double-click / R: reset the view',
        'Tab: proxy / main',
        'P: proxy',
        'M: main',
        'F: flat lat-long view (drag pans, wheel zooms)',
        '+ / −: exposure ±½ stop',
        'G: reset exposure and gamma',
        '0–5: rating',
        'PgUp / PgDn: previous / next asset',
        'Esc / Q: close'])

    def __init__(self, settings, asset, parent=None):
        super().__init__(parent, QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle('TinyLib 360 — ' + display_name(asset))
        self.resize(1200, 700)
        self.settings, self.asset = settings, asset
        self.loader = None
        self.main_info = None
        highres = str(asset.get('highres') or '')
        self.highres_path = highres if highres and Path(highres).is_file() else ''
        self.rating = max(0, min(5, int(asset.get('_rating', 0))))
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view = PanoView(self)
        self.view.setToolTip(self.HELP)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.build_controls())
        self.view.view_changed.connect(self.update_info)
        preview = asset.get('proxy') or asset.get('thumb')
        image = QtGui.QImage(str(preview)) if preview and Path(preview).is_file() else QtGui.QImage()
        self.available = {'proxy': not image.isNull()}
        if image.isNull():
            self.view.message = 'The proxy is unavailable: %s' % preview
        else:
            self.view.set_image('proxy', image)
        main = asset.get('main') or ''
        self.available['main'] = bool(main) and Path(main).is_file()
        if not self.available['main']:
            self.mode_button.setToolTip('The main file is unavailable: %s' % main)
        self.mode_button.setEnabled(all(self.available.values()))
        if not self.highres_path:
            self.highres_box.setEnabled(False)
            self.highres_box.setToolTip('This asset has no highres file.' if not asset.get('highres') else
                                        'The highres file is unavailable: %s' % asset.get('highres'))
        self.update_info()
        if image.isNull() and self.available['main']:
            self.show_mode('main')

    def build_controls(self):
        bar = QtWidgets.QWidget(self)
        bar.setObjectName('panoBar')
        # Explicit colours in every state: the studio stylesheet and the Windows style
        # otherwise paint a light (or hatched, when checked) button behind light text.
        bar.setStyleSheet('QWidget#panoBar { background: #292929; } QLabel { color: #ddd; } '
                          'QLabel:disabled { color: #777; } '
                          'QToolButton { color: #eee; background: #444; border: 1px solid #666; '
                          'border-radius: 4px; padding: 4px 12px; font-weight: bold; } '
                          'QToolButton:hover { background: #505050; } '
                          'QToolButton:checked { color: #111; background: #d8ab70; border-color: #e5c59c; } '
                          'QToolButton:checked:hover { background: #e0b47a; } '
                          'QToolButton:disabled { color: #888; background: #333; border-color: #444; }')
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 12, 6)
        # One toggle: unchecked shows the proxy, checked the main.
        self.mode_button = QtWidgets.QToolButton()
        self.mode_button.setCheckable(True)
        self.mode_button.setFocusPolicy(QtCore.Qt.NoFocus)
        self.mode_button.setMinimumWidth(110)
        self.mode_button.setText('Proxy')
        self.mode_button.setToolTip('Switch between the proxy and the HDR main (Tab)')
        self.mode_button.toggled.connect(lambda checked: self.show_mode('main' if checked else 'proxy'))
        row.addWidget(self.mode_button)
        row.addSpacing(10)
        self.highres_box = QtWidgets.QCheckBox('Use highres if available')
        self.highres_box.setFocusPolicy(QtCore.Qt.NoFocus)
        self.highres_box.setToolTip('Show the highres instead of the main as the HDR image.')
        self.highres_box.setStyleSheet('QCheckBox { color: #ddd; } QCheckBox:disabled { color: #777; }')
        self.highres_box.toggled.connect(self.highres_toggled)
        row.addWidget(self.highres_box)
        row.addSpacing(16)
        # Both sliders are neutral in the middle; clicking a caption puts its slider back there.
        self.exposure_label = ResetLabel()
        self.exposure = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.exposure.setRange(-100, 100)  # tenths of a stop
        self.exposure.setFixedWidth(170)
        self.exposure.valueChanged.connect(self.adjust)
        self.exposure_label.clicked.connect(lambda: self.exposure.setValue(0))
        self.gamma_label = ResetLabel()
        self.gamma = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.gamma.setRange(-100, 100)  # gamma_value(): 0.25 .. 1 .. 4
        self.gamma.setFixedWidth(130)
        self.gamma.valueChanged.connect(self.adjust)
        self.gamma_label.clicked.connect(lambda: self.gamma.setValue(0))
        for widget in (self.exposure_label, self.exposure, self.gamma_label, self.gamma):
            widget.setFocusPolicy(QtCore.Qt.NoFocus)
            row.addWidget(widget)
        row.addStretch(1)
        self.info = QtWidgets.QLabel()
        row.addWidget(self.info)
        self.adjust()
        return bar

    # --- modes and grading -------------------------------------------------------------------

    def show_mode(self, mode):
        if not self.available.get(mode):
            blocked = self.mode_button.blockSignals(True)
            self.mode_button.setChecked(self.view.mode == 'main')
            self.mode_button.blockSignals(blocked)
            return
        blocked = self.mode_button.blockSignals(True)
        self.mode_button.setChecked(mode == 'main')
        self.mode_button.setText(('Highres (HDR)' if self.use_highres() else 'Main (HDR)') if mode == 'main' else 'Proxy')
        self.mode_button.blockSignals(blocked)
        self.view.set_mode(mode)
        if mode == 'main' and not self.view.has('main') and self.loader is None:
            self.load_main()
        self.view.message = ('Loading the %s…' % self.hdr_name()
                             if mode == 'main' and not self.view.has('main') else '')
        self.adjust()

    def use_highres(self):
        return self.highres_box.isChecked() and bool(self.highres_path)

    def hdr_name(self):
        return 'highres' if self.use_highres() else 'main'

    def highres_toggled(self, _checked):
        """Swap the HDR image between main and highres; the proxy is not affected."""
        self.loader = None  # a decode still running for the other file is ignored when it finishes
        self.main_info = None
        self.view.drop('main')
        if self.view.mode == 'main':
            self.show_mode('main')  # loads the newly chosen file
        self.update_info()

    def load_main(self):
        if self.use_highres():
            path, width, height = self.highres_path, 0, 0  # its size is probed
        else:
            metadata = self.asset.get('metadata') or {}
            path, width, height = self.asset['main'], metadata.get('width') or 0, metadata.get('height') or 0
        loader = MainLoader(self)
        self.loader = loader
        loader.loaded.connect(lambda result: self.main_loaded(loader, result))
        loader.failed.connect(lambda message: self.main_failed(loader, message))
        loader.start(path, self.settings, width, height)

    def main_loaded(self, loader, result):
        if loader is not self.loader:
            return  # main/highres was switched while this was decoding
        image, info = result
        self.main_info = dict(info, label='Highres' if self.use_highres() else 'Main')
        self.view.set_image('main', image)
        if self.view.mode == 'main':
            self.view.message = ''
        self.update_info()

    def main_failed(self, loader, message):
        if loader is not self.loader:
            return
        name = self.hdr_name()
        self.loader = None  # switching to the HDR image again retries
        self.view.message = ''
        self.error.emit('Cannot show the %s: %s' % (name, message))
        self.show_mode('proxy')

    def carry_state(self):
        """What the next asset (PgUp/PgDn) keeps from this one."""
        return {'flat': self.view.flat, 'hdr': self.view.mode == 'main', 'highres': self.highres_box.isChecked(),
                'exposure': self.exposure.value(), 'gamma': self.gamma.value()}

    def apply_state(self, state):
        """Open like the previous asset's viewer: flat or sphere, proxy or HDR (main or highres),
        and the same exposure and gamma."""
        if 'exposure' in state:
            self.exposure.setValue(int(state['exposure']))
        if 'gamma' in state:
            self.gamma.setValue(int(state['gamma']))
        if 'highres' in state:
            blocked = self.highres_box.blockSignals(True)
            self.highres_box.setChecked(bool(state['highres']))
            self.highres_box.blockSignals(blocked)
        if 'flat' in state:
            self.view.flat = bool(state['flat'])
        if state.get('hdr') and self.available.get('main'):
            self.show_mode('main')
        self.update_info()

    def adjust(self, *_):
        hdr = self.view.mode == 'main'
        stops = self.exposure.value() / 10.0
        gamma = gamma_value(self.gamma.value())
        self.exposure_label.setText('Exposure %+.1f' % stops)
        self.gamma_label.setText('Gamma %.2f' % gamma)
        # Shown only for the HDR image: the proxy is already tonemapped.
        for widget in (self.exposure, self.exposure_label, self.gamma, self.gamma_label):
            widget.setVisible(hdr)
        # The proxy is already tonemapped; grading applies to the main only.
        self.view.exposure, self.view.gamma = stops, gamma
        self.view.update()

    def reset_grade(self):
        self.exposure.setValue(0)
        self.gamma.setValue(0)

    def update_info(self):
        view = self.view
        parts = []
        size = view.sizes.get(view.mode)
        if size:
            info = self.main_info
            label = (info or {}).get('label', 'Main') if view.mode == 'main' else 'Proxy'
            parts.append('%s %d × %d' % (label, size[0], size[1]))
            if view.mode == 'main' and info and (info['source_width'], info['source_height']) != size:
                parts[-1] += ' (of %d × %d)' % (info['source_width'], info['source_height'])
            if view.mode == 'main' and info:
                parts.append('peak %.4g' % info['peak'])
                parts.append('read by ' + info.get('reader', '?'))
        parts.append(('flat ×%.3g' % view.flat_zoom) if view.flat else 'FOV %d°' % round(view.fov))
        self.info.setText('   ·   '.join(parts))

    # --- keys --------------------------------------------------------------------------------

    def focusNextPrevChild(self, forward):
        return False  # Tab switches proxy/main instead of moving focus.

    def keyPressEvent(self, event):
        key = event.key()
        ratings = {QtCore.Qt.Key_0: 0, QtCore.Qt.Key_1: 1, QtCore.Qt.Key_2: 2,
                   QtCore.Qt.Key_3: 3, QtCore.Qt.Key_4: 4, QtCore.Qt.Key_5: 5}
        if key in ratings:
            if not event.isAutoRepeat():
                self.rating = ratings[key]
                self.rating_requested.emit(self.rating)
                self.info.setText('Rating: %d / 5' % self.rating)
                QtCore.QTimer.singleShot(1200, self.update_info)
        elif key in (QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown):
            if not event.isAutoRepeat():
                self.navigate_requested.emit(-1 if key == QtCore.Qt.Key_PageUp else 1)
        elif key in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            self.show_mode('proxy' if self.view.mode == 'main' else 'main')
        elif key == QtCore.Qt.Key_P:
            self.show_mode('proxy')
        elif key == QtCore.Qt.Key_M:
            self.show_mode('main')
        elif key == QtCore.Qt.Key_F:
            self.view.flat = not self.view.flat
            self.view.update()
            self.update_info()
        elif key == QtCore.Qt.Key_R:
            self.view.reset_view()
        elif key in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self.exposure.setValue(self.exposure.value() + 5)
        elif key in (QtCore.Qt.Key_Minus, QtCore.Qt.Key_Underscore):
            self.exposure.setValue(self.exposure.value() - 5)
        elif key == QtCore.Qt.Key_G:
            self.reset_grade()
        elif key in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def closeEvent(self, event):
        self.view.release_gl()
        self.closed.emit()
        super().closeEvent(event)
