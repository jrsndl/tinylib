"""Frame-addressed Qt review player using bounded, asynchronous FFmpeg decoding.

The library's proxies are constant-frame-rate, display-referred media. Decoding
small forward chunks lets the same cache serve forward/reverse playback and seeks.
"""
import math
from collections import OrderedDict
from fractions import Fraction
from .qt import QtCore, QtGui, QtWidgets, event_pos
from .trim import advance_frame, clean_trim


def media_info(asset, stream):
    still = asset.get('kind') != 'footage'
    fps = None
    for value in (stream.get('avg_frame_rate'), stream.get('r_frame_rate'), asset.get('metadata', {}).get('frame_rate'),
                  asset.get('metadata', {}).get('FPS')):
        try:
            candidate = Fraction(str(value))
            if candidate > 0:
                fps = candidate
                break
        except (ValueError, ZeroDivisionError):
            pass
    if still:
        return Fraction(1), 1
    if fps is None:
        raise ValueError('Preview frame rate is unavailable.')
    try:
        count = int(stream.get('nb_frames', 0))
    except (ValueError, TypeError):
        count = 0
    if count <= 0:
        try:
            duration = float(stream.get('duration', 0))
            count = round(duration * float(fps)) if math.isfinite(duration) else 0
        except (ValueError, TypeError):
            count = 0
    if count <= 0:
        raise ValueError('Preview duration/frame count is unavailable.')
    return fps, count


class FrameDecoder(QtCore.QObject):
    available = QtCore.Signal(int)
    error = QtCore.Signal(str)
    # The stream holds fewer frames than its metadata promised (count from duration × fps).
    length_changed = QtCore.Signal(int)
    # FFmpeg 5.1 renamed -vsync to -fps_mode; older studio builds only know -vsync.
    legacy_vsync = False
    CHUNK = 32
    BUDGET = 128 * 1024 * 1024

    def __init__(self, settings, media, stream, fps, count, parent=None, full_resolution=False):
        super().__init__(parent)
        self.executable = settings.get('tools', {}).get('ffmpeg', 'ffmpeg')
        self.media, self.fps, self.count = media, fps, count
        width, height = int(stream['width']), int(stream['height'])
        if width <= 0 or height <= 0:
            raise ValueError('Invalid preview dimensions.')
        if full_resolution:
            self.CHUNK = 1
        scale = 1 if full_resolution else min(1, 960 / width, 540 / height)
        self.width, self.height = max(1, round(width * scale)), max(1, round(height * scale))
        self.frame_bytes = self.width * self.height * 3
        self.limit = max(self.CHUNK * 2, self.BUDGET // self.frame_bytes)
        self.cache = OrderedDict()
        self.process = None
        self.start_frame = 0
        self.end_frame = 0
        self.closed = False
        self.requested = 0
        self.failed_prefetch = None

    def image(self, frame):
        image = self.cache.get(frame)
        if image is not None:
            self.cache.move_to_end(frame)
        return image

    def request(self, frame, prefetch=False):
        if self.closed or not 0 <= frame < self.count or frame in self.cache:
            return
        if not prefetch:
            self.requested = frame
            self.failed_prefetch = None
        elif frame // self.CHUNK * self.CHUNK == getattr(self, 'failed_prefetch', None):
            return  # tick() asks every frame near a boundary; don't restart a chunk that just failed.
        if self.process is not None:
            if prefetch or self.start_frame <= frame < self.end_frame:
                return
            self.cancel()
        start = frame // self.CHUNK * self.CHUNK
        end = min(start + self.CHUNK, self.count)
        self.start_frame, self.end_frame = start, end
        process = QtCore.QProcess(self)
        self.process = process
        retry = (frame, prefetch)
        uses_fps_mode = not FrameDecoder.legacy_vsync
        buffer, errors = bytearray(), bytearray()
        next_frame = [start]
        timeout = QtCore.QTimer(process)
        timeout.setSingleShot(True)
        timeout.setInterval(15000)

        def read():
            if process is not self.process:
                return
            buffer.extend(bytes(process.readAllStandardOutput()))
            while len(buffer) >= self.frame_bytes and next_frame[0] < end:
                raw = bytes(buffer[:self.frame_bytes])
                del buffer[:self.frame_bytes]
                image = QtGui.QImage(raw, self.width, self.height, self.width * 3, QtGui.QImage.Format_RGB888).copy()
                index = next_frame[0]
                self.cache[index] = image
                next_frame[0] += 1
                while len(self.cache) > self.limit:
                    self.cache.popitem(last=False)
                self.available.emit(index)
            timeout.start()

        def read_errors():
            errors.extend(bytes(process.readAllStandardError()))
            del errors[:-16000]

        def done(code, status):
            timeout.stop()
            if process is self.process:
                read()
                timeout.stop()
                read_errors()
                self.process = None
                message = errors.decode('utf-8', 'replace')
                normal = status == QtCore.QProcess.NormalExit
                if code and uses_fps_mode and 'fps_mode' in message:
                    # This process used -fps_mode (other decoders may have switched already).
                    FrameDecoder.legacy_vsync = True
                    process.deleteLater()
                    return self.request(*retry)
                elif normal and not code and 0 < next_frame[0] < end:
                    # Decoding stopped early: the clip is shorter than estimated. Trim, don't fail.
                    self.count = next_frame[0]
                    self.length_changed.emit(self.count)
                elif code or next_frame[0] < end:
                    if retry[1]:
                        self.failed_prefetch = start
                        return process.deleteLater()  # A failed prefetch is retried when the frame is needed.
                    self.error.emit(message or 'Could not decode the requested preview frames.')
            process.deleteLater()

        def failed(_):
            if process.error() == QtCore.QProcess.FailedToStart:
                timeout.stop()
                if process is self.process:
                    self.process = None
                    self.error.emit('Cannot start FFmpeg: ' + process.errorString())
                process.deleteLater()

        def timed_out():
            if self.process is process:
                self.cancel()
                self.error.emit('Preview decoding timed out.')

        timeout.timeout.connect(timed_out)
        process.readyReadStandardOutput.connect(read)
        process.readyReadStandardError.connect(read_errors)
        process.finished.connect(done)
        process.errorOccurred.connect(failed)
        # Input seeking is accurate while transcoding. CFR frame centers avoid
        # decimal rounding landing just after the intended frame timestamp.
        seek = max(0, (start - 0.25) / float(self.fps))
        args = ['-hide_banner', '-loglevel', 'error', '-nostdin', '-threads', '2']
        if start:
            args += ['-ss', '%.9f' % seek]
        args += ['-i', self.media, '-map', '0:v:0', '-an', '-sn',
                '-vf', 'scale=%d:%d' % (self.width, self.height),
                '-fps_mode' if uses_fps_mode else '-vsync', 'passthrough',
                '-frames:v', str(end - start), '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
        process.start(self.executable, args)
        timeout.start()

    def cancel(self):
        process, self.process = self.process, None
        if process is not None:
            process.kill()
            # Reap the process before its owning player can be destroyed.
            process.waitForFinished(500)

    def close(self):
        self.closed = True
        self.cancel()
        self.cache.clear()


class Timeline(QtWidgets.QSlider):
    """Absolute click/drag seeking instead of QSlider's page-step behavior."""
    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setMouseTracking(True)
        self.dragging = False
        self.trim = None  # (in, out) slider values, shown as a band with end marks.
        self.setAccessibleName('Preview timeline')

    def value_x(self, value):
        option = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(QtWidgets.QStyle.CC_Slider, option, QtWidgets.QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(QtWidgets.QStyle.CC_Slider, option, QtWidgets.QStyle.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        return groove.x() + handle.width() // 2 + QtWidgets.QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), value, span)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.trim:
            return
        painter = QtGui.QPainter(self)
        left, right = self.value_x(self.trim[0]), self.value_x(self.trim[1])
        middle = self.height() // 2
        painter.fillRect(QtCore.QRect(left, middle - 5, max(1, right - left), 10), QtGui.QColor(110, 170, 230, 90))
        painter.setPen(QtGui.QPen(QtGui.QColor('#7fb8ee'), 2))
        for x in (left, right):
            painter.drawLine(x, middle - 8, x, middle + 8)
        painter.end()

    def position_value(self, event):
        option = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(QtWidgets.QStyle.CC_Slider, option, QtWidgets.QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(QtWidgets.QStyle.CC_Slider, option, QtWidgets.QStyle.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        x = event_pos(event).x() - groove.x() - handle.width() // 2
        return QtWidgets.QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), max(0, min(span, x)), span)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.dragging = True
            self.sliderPressed.emit()
            self.setValue(self.position_value(event))
            event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.setValue(self.position_value(event))
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.dragging and event.button() == QtCore.Qt.LeftButton:
            self.setValue(self.position_value(event))
            self.dragging = False
            self.sliderReleased.emit()
        event.accept()


class ReviewPlayer(QtWidgets.QWidget):
    error = QtCore.Signal(str)
    closed = QtCore.Signal()
    frame_shown = QtCore.Signal(int)
    rating_requested = QtCore.Signal(int)
    trim_requested = QtCore.Signal(object)  # (in, out) in asset frame numbers, or None for the whole range.

    def __init__(self, settings, asset, stream, parent=None):
        super().__init__(parent, QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle('TinyLib — ' + asset['name'])
        self.setMinimumSize(480, 270)
        self.resize(1100, 660)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setToolTip('J/L: reverse/forward, repeat for speed · K: stop · Space: play/stop\n'
                        'Left/Right: one frame · I/O: set in/out point · Alt+X: clear in/out\n'
                        'F: frame overlay · Ctrl+C: copy stopped frame at full resolution\n'
                        'Hover bottom: timeline and Loop')
        self.fps, self.count = media_info(asset, stream)
        self.first = int(asset['first']) if asset.get('first') is not None else 1
        self.footage = asset.get('kind') == 'footage'
        # In/out points as frame indexes; the whole range when none are set.
        self.in_point, self.out_point = 0, self.count - 1
        trim = clean_trim(asset.get('_trim')) if self.footage else None
        if trim:
            self.in_point = max(0, min(self.count - 1, trim[0] - self.first))
            self.out_point = max(self.in_point, min(self.count - 1, trim[1] - self.first))
        self.current = 0
        self.target = 0
        self.direction, self.speed = (1 if self.count > 1 else 0), 1
        self.overlay = True
        self.rating = max(0, min(5, int(asset.get('_rating', 0))))
        self.rating_feedback = ''
        self.loop = True
        self.picture = QtGui.QImage()
        self.pending = True
        self.fraction = 0.0
        self.failure = ''
        self.scrubbing = False
        self.copy_decoder = None
        self.copy_target = None
        self.copy_settings, self.copy_stream = settings, dict(stream)
        self.decoder = FrameDecoder(settings, asset.get('proxy') or asset.get('thumb'), stream, self.fps, self.count, self)
        self.decoder.available.connect(self.frame_ready)
        self.decoder.error.connect(self.decode_error)
        self.decoder.length_changed.connect(self.length_changed)
        self.bar = QtWidgets.QWidget(self)
        self.bar.setObjectName('timelineBar')
        self.bar.setStyleSheet('QWidget#timelineBar { background: #292929; } QLabel { color: #ddd; background: transparent; } '
                              'QSlider::groove:horizontal { height: 4px; background: #555; margin: 0 7px; } '
                              'QSlider::sub-page:horizontal { background: #b98c54; } '
                              'QSlider::handle:horizontal { background: #e0b47a; width: 12px; margin: -5px 0; border-radius: 4px; }')
        layout = QtWidgets.QVBoxLayout(self.bar)
        layout.setContentsMargins(16, 6, 16, 10)
        self.time_label = QtWidgets.QLabel()
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(self.time_label, 1)
        self.loop_toggle = QtWidgets.QCheckBox('Loop')
        self.loop_toggle.setFocusPolicy(QtCore.Qt.NoFocus)
        self.loop_toggle.setChecked(True)
        self.loop_toggle.setStyleSheet('QCheckBox { color: #ddd; background: transparent; spacing: 7px; } '
                                      'QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #888; background: #222; } '
                                      'QCheckBox::indicator:checked { background: #d8ab70; border: 1px solid #e5c59c; }')
        self.loop_toggle.toggled.connect(lambda enabled: setattr(self, 'loop', enabled))
        heading.addWidget(self.loop_toggle)
        layout.addLayout(heading)
        self.timeline = Timeline(self.bar)
        self.timeline.setRange(0, self.count - 1)
        self.timeline.setEnabled(self.count > 1)
        self.timeline.sliderPressed.connect(self.begin_scrub)
        self.timeline.sliderReleased.connect(self.end_scrub)
        self.timeline.valueChanged.connect(self.scrub)
        layout.addWidget(self.timeline)
        self.bar.hide()
        if self.trimmed():
            self.current = self.target = self.in_point
        self.debounce = QtCore.QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(35)
        self.debounce.timeout.connect(lambda: self.seek(self.target))
        self.rating_timer = QtCore.QTimer(self)
        self.rating_timer.setSingleShot(True)
        self.rating_timer.setInterval(1200)
        self.rating_timer.timeout.connect(self.clear_rating_feedback)
        self.clock = QtCore.QElapsedTimer()
        self.clock.start()
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.update_indicator()
        QtCore.QTimer.singleShot(0, lambda: self.decoder.request(self.target))

    def update_indicator(self):
        def stamp(frame):
            milliseconds = round(frame / float(self.fps) * 1000)
            seconds, ms = divmod(milliseconds, 1000)
            minutes, seconds = divmod(seconds, 60)
            return '%02d:%02d.%03d' % (minutes, seconds, ms)
        state = ('Reverse' if self.direction < 0 else 'Forward') + ' %d×' % self.speed if self.direction else 'Stopped · 1×'
        trim = '     In %d · Out %d' % (self.first + self.in_point, self.first + self.out_point) if self.trimmed() else ''
        self.time_label.setText('%s / %s     %s%s' % (stamp(self.current), stamp(self.count - 1), state, trim))
        self.timeline.trim = (self.in_point, self.out_point) if self.trimmed() else None
        if not self.scrubbing:
            self.timeline.blockSignals(True)
            self.timeline.setValue(self.current)
            self.timeline.blockSignals(False)
        self.update()

    def stop_playback(self):
        self.direction, self.speed, self.fraction = 0, 1, 0.0
        self.clock.restart()
        self.update_indicator()

    def trimmed(self):
        return (self.in_point, self.out_point) != (0, self.count - 1)

    def set_trim(self, in_point, out_point, message):
        self.in_point, self.out_point = in_point, out_point
        trim = (self.first + in_point, self.first + out_point) if self.trimmed() else None
        self.trim_requested.emit(trim)
        self.rating_feedback = message
        self.rating_timer.start()
        self.update_indicator()
        self.timeline.update()

    def mark_in(self):
        # An in point after the out point moves the out point back to the end.
        out_point = self.out_point if self.current <= self.out_point else self.count - 1
        self.set_trim(self.current, out_point, 'In: %d' % (self.first + self.current))

    def mark_out(self):
        in_point = self.in_point if self.current >= self.in_point else 0
        self.set_trim(in_point, self.current, 'Out: %d' % (self.first + self.current))

    def play_direction(self, direction):
        if self.count <= 1 or self.failure:
            return
        if not self.in_point <= self.target <= self.out_point:
            self.seek(self.in_point if direction > 0 else self.out_point)
        self.speed = min(self.speed * 2, 16) if self.direction == direction else 1
        self.direction = direction
        self.fraction = 0.0
        self.clock.restart()
        self.update_indicator()

    def step(self, amount):
        self.stop_playback()
        self.seek(self.target + amount)

    def length_changed(self, count):
        self.count = max(1, count)
        self.timeline.setRange(0, self.count - 1)
        self.out_point = min(self.out_point, self.count - 1)
        self.in_point = min(self.in_point, self.out_point)
        if self.target >= self.count:
            self.seek(self.count - 1)
        self.update_indicator()

    def seek(self, frame):
        self.failure = ''  # Any explicit seek retries after an earlier decode error.
        self.target = max(0, min(self.count - 1, int(frame)))
        image = self.decoder.image(self.target)
        self.pending = image is None
        if image is None:
            self.decoder.request(self.target)
            self.update()
        else:
            self.display(self.target, image)

    def display(self, frame, image):
        self.current, self.picture = frame, image
        self.pending = False
        self.update_indicator()
        self.frame_shown.emit(frame)

    def frame_ready(self, frame):
        if frame == self.target:
            self.display(frame, self.decoder.image(frame))
            # Buffer waits do not cause a large catch-up jump.
            self.clock.restart()

    def tick(self):
        elapsed = min(self.clock.restart() / 1000, 0.25)
        if not self.direction or self.pending or self.scrubbing or self.failure:
            return
        self.fraction += elapsed * float(self.fps) * self.speed
        advance = int(self.fraction)
        if advance:
            self.fraction -= advance
            # Loops (or stops) inside the in/out points; they are the whole range when unset.
            frame, stop = advance_frame(self.current + advance * self.direction,
                                        self.in_point, self.out_point, self.loop)
            self.seek(frame)
            if stop:
                self.stop_playback()
        # Warm the next chunk in the current direction while cached frames play.
        boundary = ((self.current // self.decoder.CHUNK) + (1 if self.direction > 0 else 0)) * self.decoder.CHUNK
        if abs(boundary - self.current) <= 12:
            self.decoder.request(boundary if self.direction > 0 else boundary - 1, prefetch=True)

    def begin_scrub(self):
        self.scrubbing = True
        self.stop_playback()

    def scrub(self, value):
        if self.scrubbing:
            self.target = value
            if self.decoder.image(value) is not None:
                self.seek(value)
            else:
                self.debounce.start()

    def end_scrub(self):
        self.debounce.stop()
        self.scrubbing = False
        self.seek(self.timeline.value())
        self.setFocus()

    def decode_error(self, message):
        self.failure = message
        self.pending = False
        self.stop_playback()
        self.error.emit(message)

    def copy_current_frame(self):
        if self.direction or self.pending or self.scrubbing or self.failure or self.picture.isNull():
            return
        self.copy_target = self.current
        if (self.picture.width(), self.picture.height()) == (int(self.copy_stream['width']), int(self.copy_stream['height'])):
            self.copy_image(self.picture)
            return
        if self.copy_decoder is None:
            self.copy_decoder = FrameDecoder(self.copy_settings, self.decoder.media, self.copy_stream,
                                             self.fps, self.count, self, full_resolution=True)
            self.copy_decoder.available.connect(self.copy_frame_ready)
            self.copy_decoder.error.connect(self.copy_error)
        image = self.copy_decoder.image(self.copy_target)
        if image is not None:
            self.copy_image(image)
        else:
            self.copy_decoder.request(self.copy_target)

    def copy_frame_ready(self, frame):
        if frame == self.copy_target:
            self.copy_image(self.copy_decoder.image(frame))

    def copy_image(self, image):
        QtWidgets.QApplication.clipboard().setImage(image.copy())
        self.rating_feedback = 'Frame copied'
        self.rating_timer.start()
        self.update()

    def copy_error(self, message):
        self.error.emit('Could not copy proxy frame: ' + message)

    def keyPressEvent(self, event):
        if event.matches(QtGui.QKeySequence.Copy):
            if not event.isAutoRepeat():
                self.copy_current_frame()
            event.accept()
            return
        key = event.key()
        ratings = {QtCore.Qt.Key_0: 0, QtCore.Qt.Key_1: 1, QtCore.Qt.Key_2: 2,
                   QtCore.Qt.Key_3: 3, QtCore.Qt.Key_4: 4, QtCore.Qt.Key_5: 5}
        if key in ({QtCore.Qt.Key_J, QtCore.Qt.Key_K, QtCore.Qt.Key_L, QtCore.Qt.Key_Space, QtCore.Qt.Key_F,
                    QtCore.Qt.Key_I, QtCore.Qt.Key_O, QtCore.Qt.Key_X} | set(ratings)) and event.isAutoRepeat():
            event.accept()
            return
        alt = bool(event.modifiers() & QtCore.Qt.AltModifier)
        if key == QtCore.Qt.Key_X and alt:
            if self.footage and self.trimmed():
                self.set_trim(0, self.count - 1, 'In/out cleared')
        elif key == QtCore.Qt.Key_I and self.footage and self.count > 1:
            self.mark_in()
        elif key == QtCore.Qt.Key_O and self.footage and self.count > 1:
            self.mark_out()
        elif key in ratings:
            self.rating = ratings[key]
            self.rating_feedback = 'Rating: %d / 5' % self.rating
            self.rating_requested.emit(self.rating)
            self.rating_timer.start()
            self.update()
        elif key == QtCore.Qt.Key_J:
            self.play_direction(-1)
        elif key == QtCore.Qt.Key_L:
            self.play_direction(1)
        elif key == QtCore.Qt.Key_K:
            self.stop_playback()
        elif key == QtCore.Qt.Key_Space:
            self.stop_playback() if self.direction else self.play_direction(1)
        elif key == QtCore.Qt.Key_Left:
            self.step(-1)
        elif key == QtCore.Qt.Key_Right:
            self.step(1)
        elif key == QtCore.Qt.Key_F:
            self.overlay = not self.overlay
            self.update()
        elif key in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def clear_rating_feedback(self):
        self.rating_feedback = ''
        self.update()

    def mouseMoveEvent(self, event):
        self.bar.setVisible(event_pos(event).y() >= self.height() - 90 or self.scrubbing)
        self.update()

    def leaveEvent(self, event):
        if not self.scrubbing and not self.rect().contains(self.mapFromGlobal(QtGui.QCursor.pos())):
            self.bar.hide()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        self.bar.setGeometry(0, self.height() - 76, self.width(), 76)
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor('#101010'))
        if not self.picture.isNull():
            size = self.picture.size().scaled(self.size(), QtCore.Qt.KeepAspectRatio)
            target = QtCore.QRect((self.width() - size.width()) // 2, (self.height() - size.height()) // 2, size.width(), size.height())
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            painter.drawImage(target, self.picture)
        if self.overlay and not self.picture.isNull():
            y = self.height() - (118 if self.bar.isVisible() else 46)
            box = QtCore.QRect(16, y, 210, 32)
            painter.fillRect(box, QtGui.QColor(0, 0, 0, 180))
            painter.setPen(QtGui.QColor('#eeeeee'))
            painter.setFont(QtGui.QFont('Segoe UI', 14))
            painter.drawText(box.adjusted(10, 0, 0, 0), QtCore.Qt.AlignVCenter, 'Frame %d' % (self.first + self.current))
        if self.failure or self.picture.isNull() or self.pending:
            painter.setPen(QtGui.QColor('#eeeeee'))
            message = self.failure or 'Loading preview…'
            painter.drawText(self.rect().adjusted(24, 20, -24, -20), QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap, message)
        if self.rating_feedback:
            box = QtCore.QRect(self.width() - 160, 16, 144, 32)
            painter.fillRect(box, QtGui.QColor(0, 0, 0, 190))
            painter.setPen(QtGui.QColor('#d4aa6c'))
            painter.drawText(box, QtCore.Qt.AlignCenter, self.rating_feedback)
        painter.end()

    def closeEvent(self, event):
        self.timer.stop()
        self.debounce.stop()
        self.rating_timer.stop()
        self.decoder.close()
        if self.copy_decoder is not None:
            self.copy_decoder.close()
        self.closed.emit()
        super().closeEvent(event)
