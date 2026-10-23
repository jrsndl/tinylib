"""Preview inspection and interactive player launch, with an optional ffplay backend."""
import json
import math
from fractions import Fraction
from pathlib import Path
from .qt import QtCore
from .asset_fields import display_name
from .asset_types import is_latlong


def sibling_tool(settings, name):
    tools = settings.get('tools', {})
    if tools.get(name):
        return tools[name]
    ffmpeg = Path(tools.get('ffmpeg', 'ffmpeg'))
    sibling = ffmpeg.with_name(name + ffmpeg.suffix)
    return str(sibling) if sibling.is_file() else name


def frame_filter(asset, stream):
    # Use timestamps, not drawtext's decoded-frame counter: seeking must not reset it.
    fps = None
    for value in (stream.get('avg_frame_rate'), stream.get('r_frame_rate'), asset.get('metadata', {}).get('frame_rate'),
                  asset.get('metadata', {}).get('FPS')):
        try:
            candidate = Fraction(str(value))
            if candidate > 0:
                fps = candidate
                break
        except (ValueError, ZeroDivisionError):
            continue
    start = float(stream.get('start_time', 0) or 0)
    if not math.isfinite(start):
        raise ValueError('Invalid preview start timestamp.')
    first = asset.get('first')
    first = int(first) if first is not None else 1
    if asset.get('kind') != 'footage':
        expression = str(first)
    else:
        if fps is None:
            raise ValueError('Preview frame rate is unavailable.')
        expression = 'round((t-(%s))*%s/%s)+%s' % (start, fps.numerator, fps.denominator, first)
    return ("drawtext=text='Frame %{eif\\:" + expression + "\\:d}':"
            'fontcolor=white:fontsize=24:box=1:boxcolor=black@0.7:boxborderw=8:x=16:y=h-th-16')


def play_arguments(asset, stream):
    media = asset.get('proxy') or asset.get('thumb')
    return ['-hide_banner', '-loglevel', 'error', '-window_title', 'TinyLib — ' + display_name(asset),
            '-x', '1280', '-y', '720', '-an', '-vf', frame_filter(asset, stream), '-i', media]


class Player(QtCore.QObject):
    error = QtCore.Signal(str)
    started = QtCore.Signal()
    finished = QtCore.Signal()
    rating_requested = QtCore.Signal(object, int)
    trim_requested = QtCore.Signal(object, object)  # asset, (in, out) or None
    # asset, step (-1 previous, +1 next), the window asking: PgUp/PgDn in a player window.
    navigate_requested = QtCore.Signal(object, int, object)
    # asset, QImage, the player window: Set as thumbnail (only offered where thumbnail_allowed says so).
    thumbnail_requested = QtCore.Signal(object, object, object)
    # asset, global QPoint, the player window: right-click, for the actions' context menu.
    context_menu_requested = QtCore.Signal(object, object, object)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.thumbnail_allowed = lambda asset: False  # The browser sets the permission check.
        self.copy_allowed = lambda asset: True  # Ctrl+C of a frame; the browser sets the permission check.
        self.jobs = set()
        self.windows = set()
        self.replacing = set()  # windows whose next/previous asset is opening; further PgUp/PgDn wait

    def play(self, asset, replace=None):
        """Open a player window for ``asset``.

        ``replace`` is an open player window the new one takes the place of (PgUp/PgDn):
        it closes once the new window is open, which gets its size and position. If the new
        one fails to open, the old one stays.
        """
        if is_latlong(asset):
            return self.play_pano(asset, replace)
        media = asset.get('proxy') or asset.get('thumb')
        if not media or not Path(media).is_file():
            raise ValueError('Preview is unavailable: ' + str(media))
        if replace is not None:
            self.replacing.add(replace)
        probe = QtCore.QProcess(self)
        self.jobs.add(probe)
        timer = QtCore.QTimer(probe)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: probe.kill())
        timer.start(15000)

        def failed(_):
            if probe.error() == QtCore.QProcess.FailedToStart:
                self.replacing.discard(replace)
                timer.stop()
                self.error.emit('Cannot start ffprobe: ' + probe.errorString())
                self.jobs.discard(probe)
                probe.deleteLater()

        def ready(code, status):
            timer.stop()
            self.jobs.discard(probe)
            if probe.property('cancelled'):
                probe.deleteLater()
                return
            try:
                if status != QtCore.QProcess.NormalExit:
                    # The 15 s timer kills ffprobe; a killed process can still report exit code 0.
                    raise RuntimeError('Preview inspection timed out or ffprobe crashed.')
                if code:
                    raise RuntimeError(bytes(probe.readAllStandardError()).decode('utf-8', 'replace') or 'Preview inspection failed.')
                data = json.loads(bytes(probe.readAllStandardOutput()))
                streams = data['streams']
                if not streams:
                    raise ValueError('Preview has no video/image stream.')
                if not streams[0].get('duration') or streams[0]['duration'] == 'N/A':
                    streams[0]['duration'] = data.get('format', {}).get('duration')
                self.launch(asset, streams[0], replace)
            except Exception as error:
                self.replacing.discard(replace)
                self.error.emit(str(error))
            probe.deleteLater()

        probe.errorOccurred.connect(failed)
        probe.finished.connect(ready)
        probe.start(sibling_tool(self.settings, 'ffprobe'),
                    ['-v', 'error', '-select_streams', 'v:0', '-show_entries',
                     'stream=avg_frame_rate,r_frame_rate,start_time,width,height,nb_frames,duration:format=duration', '-of', 'json', media])

    def play_pano(self, asset, replace=None):
        """A lat-long still opens in the 360 viewer, whatever the player backend."""
        from .pano_player import PanoViewer
        window = PanoViewer(self.settings, asset)
        self.show_window(window, asset, replace)

    def show_window(self, window, asset, replace=None):
        self.windows.add(window)
        window.error.connect(self.error)
        window.rating_requested.connect(lambda rating, selected=asset: self.rating_requested.emit(selected, rating))
        window.trim_requested.connect(lambda trim, selected=asset: self.trim_requested.emit(selected, trim))
        window.navigate_requested.connect(lambda step, selected=asset, source=window:
                                          source in self.replacing or self.navigate_requested.emit(selected, step, source))

        def closed():
            self.windows.discard(window)
            self.replacing.discard(window)
            self.finished.emit()
        window.closed.connect(closed)
        window.context_menu_requested.connect(
            lambda position, selected=asset, source=window: self.context_menu_requested.emit(selected, position, source))
        replaced = replace is not None and replace in self.windows
        if replaced:
            # PgUp/PgDn keep how you were looking: playing or stopped, flat or sphere, proxy or HDR.
            # What one window type does not use (a footage player has no flat view) is kept for
            # the next window of the other type.
            state = dict(getattr(replace, 'carried', {}), **replace.carry_state())
            window.carried = state
            window.apply_state(state)
            window.setGeometry(replace.geometry())
            if replace.isMaximized() or replace.isFullScreen():
                window.setWindowState(replace.windowState())
        window.show()
        window.activateWindow()
        window.setFocus()
        self.replacing.discard(replace)
        if replaced:
            replace.close()
        self.started.emit()

    def launch(self, asset, stream, replace=None):
        if self.settings.get('player_backend', 'interactive') != 'ffplay':
            from .review_player import ReviewPlayer
            media = asset.get('proxy') or asset.get('thumb')
            if Path(media).suffix.lower() in ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp'):
                asset = dict(asset, kind='still')
            allowed = asset.get('kind') == 'footage' and bool(self.thumbnail_allowed(asset))
            window = ReviewPlayer(self.settings, asset, stream, can_set_thumbnail=allowed,
                                  can_copy=bool(self.copy_allowed(asset)))
            window.thumbnail_requested.connect(
                lambda image, selected=asset, source=window: self.thumbnail_requested.emit(selected, image, source))
            self.show_window(window, asset, replace)
            return
        process = QtCore.QProcess(self)
        self.jobs.add(process)
        stderr = bytearray()

        def read_errors():
            stderr.extend(bytes(process.readAllStandardError()))
            del stderr[:-16000]

        def finished(code, _):
            read_errors()
            if code and not process.property('cancelled'):
                self.error.emit('ffplay failed: ' + stderr.decode('utf-8', 'replace'))
            self.jobs.discard(process)
            self.finished.emit()
            process.deleteLater()

        process.readyReadStandardError.connect(read_errors)
        def failed(_):
            if process.error() == QtCore.QProcess.FailedToStart:
                self.error.emit('Cannot start ffplay: ' + process.errorString())
                self.jobs.discard(process)
                process.deleteLater()

        process.errorOccurred.connect(failed)
        process.started.connect(self.started)
        process.finished.connect(finished)
        process.start(sibling_tool(self.settings, 'ffplay'), play_arguments(asset, stream))

    def stop(self):
        for window in list(self.windows):
            window.close()
        for process in list(self.jobs):
            process.setProperty('cancelled', True)
            process.kill()
