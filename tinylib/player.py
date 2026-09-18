"""Preview inspection and interactive player launch, with an optional ffplay backend."""
import json
import math
from fractions import Fraction
from pathlib import Path
from .qt import QtCore


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
    for value in (stream.get('avg_frame_rate'), stream.get('r_frame_rate'), asset.get('metadata', {}).get('FPS')):
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
    return ['-hide_banner', '-loglevel', 'error', '-window_title', 'TinyLib — ' + asset['name'],
            '-x', '1280', '-y', '720', '-an', '-vf', frame_filter(asset, stream), '-i', media]


class Player(QtCore.QObject):
    error = QtCore.Signal(str)
    started = QtCore.Signal()
    finished = QtCore.Signal()
    rating_requested = QtCore.Signal(object, int)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.jobs = set()
        self.windows = set()

    def play(self, asset):
        media = asset.get('proxy') or asset.get('thumb')
        if not media or not Path(media).is_file():
            raise ValueError('Preview is unavailable: ' + str(media))
        probe = QtCore.QProcess(self)
        self.jobs.add(probe)
        timer = QtCore.QTimer(probe)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: probe.kill())
        timer.start(15000)

        def failed(_):
            if probe.error() == QtCore.QProcess.FailedToStart:
                timer.stop()
                self.error.emit('Cannot start ffprobe: ' + probe.errorString())
                self.jobs.discard(probe)
                probe.deleteLater()

        def ready(code, _status):
            timer.stop()
            self.jobs.discard(probe)
            if probe.property('cancelled'):
                probe.deleteLater()
                return
            try:
                if code:
                    raise RuntimeError(bytes(probe.readAllStandardError()).decode('utf-8', 'replace') or 'Preview inspection timed out or failed.')
                data = json.loads(bytes(probe.readAllStandardOutput()))
                streams = data['streams']
                if not streams:
                    raise ValueError('Preview has no video/image stream.')
                if not streams[0].get('duration') or streams[0]['duration'] == 'N/A':
                    streams[0]['duration'] = data.get('format', {}).get('duration')
                self.launch(asset, streams[0])
            except Exception as error:
                self.error.emit(str(error))
            probe.deleteLater()

        probe.errorOccurred.connect(failed)
        probe.finished.connect(ready)
        probe.start(sibling_tool(self.settings, 'ffprobe'),
                    ['-v', 'error', '-select_streams', 'v:0', '-show_entries',
                     'stream=avg_frame_rate,r_frame_rate,start_time,width,height,nb_frames,duration:format=duration', '-of', 'json', media])

    def launch(self, asset, stream):
        if self.settings.get('player_backend', 'interactive') != 'ffplay':
            from .review_player import ReviewPlayer
            media = asset.get('proxy') or asset.get('thumb')
            if Path(media).suffix.lower() in ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp'):
                asset = dict(asset, kind='still')
            window = ReviewPlayer(self.settings, asset, stream)
            self.windows.add(window)
            window.error.connect(self.error)
            window.rating_requested.connect(lambda rating, selected=asset: self.rating_requested.emit(selected, rating))
            def closed():
                self.windows.discard(window)
                self.finished.emit()
            window.closed.connect(closed)
            window.show()
            window.activateWindow()
            window.setFocus()
            self.started.emit()
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
