"""Lat-long (equirectangular) viewing without Qt: view rays, display transform and HDR loading.

The 360 viewer (``pano_player``) draws with an OpenGL shader. The functions here are the
same mathematics in Python, used for the pixel readout and by the tests, and the loader
that turns a scene-linear EXR or HDR main into an RGBE texture.

Conventions: the image centre (u = 0.5, v = 0.5) is straight ahead at yaw 0 and pitch 0.
Turning right (positive yaw) moves towards larger u; looking up (positive pitch) towards
smaller v, the top row of the image. Directions are right-handed with -Z ahead and +Y up.
"""
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .media_probe import PROBE_TIMEOUT, run_tool

DEFAULT_FOV = 90.0          # horizontal field of view, degrees
MIN_FOV, MAX_FOV = 10.0, 150.0
MAX_PITCH = 90.0
DEFAULT_MAX_WIDTH = 8192    # the main is downsized to this texture width; settings "pano_max_width"
DECODE_TIMEOUT = 300
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def clamp(value, low, high):
    return max(low, min(high, value))


def view_basis(yaw, pitch):
    """(forward, right, up) unit vectors for yaw and pitch in degrees."""
    yaw, pitch = math.radians(yaw), math.radians(pitch)
    forward = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch))
    right = (math.cos(yaw), 0.0, math.sin(yaw))
    up = (right[1] * forward[2] - right[2] * forward[1],
          right[2] * forward[0] - right[0] * forward[2],
          right[0] * forward[1] - right[1] * forward[0])
    return forward, right, up


def view_ray(x, y, yaw, pitch, fov, aspect):
    """The direction through normalized view coordinates x, y in [-1, 1] (+y up).

    ``fov`` is the horizontal field of view in degrees, ``aspect`` the view's width/height.
    The shader computes exactly this.
    """
    forward, right, up = view_basis(yaw, pitch)
    tan_half = math.tan(math.radians(fov) / 2)
    d = [forward[i] + right[i] * x * tan_half + up[i] * y * tan_half / aspect for i in range(3)]
    length = math.sqrt(sum(value * value for value in d))
    return tuple(value / length for value in d)


def direction_to_uv(direction):
    """Texture coordinates (u, v) of a direction; v = 0 is the top row of the image."""
    x, y, z = direction
    u = 0.5 + math.atan2(x, -z) / (2 * math.pi)
    v = 0.5 - math.asin(clamp(y, -1.0, 1.0)) / math.pi
    return u % 1.0, v


def uv_to_direction(u, v):
    longitude = (u - 0.5) * 2 * math.pi
    latitude = (0.5 - v) * math.pi
    return (math.sin(longitude) * math.cos(latitude), math.sin(latitude), -math.cos(longitude) * math.cos(latitude))


MAX_FLAT_ZOOM = 64.0


def flat_scale(aspect):
    """How much of the view (in normalized coordinates) the fitted 2:1 image covers."""
    return (2.0 / aspect, 1.0) if aspect > 2.0 else (1.0, aspect / 2.0)


def flat_uv(x, y, aspect, center=(0.5, 0.5), zoom=1.0):
    """Flat view: the 2:1 image fitted in the view at zoom 1, ``center`` (u, v) in the middle.

    None outside the image. The shader computes exactly this.
    """
    scale = flat_scale(aspect)
    u = center[0] + x / scale[0] * 0.5 / zoom
    v = center[1] - y / scale[1] * 0.5 / zoom
    return (u, v) if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 else None


def flat_zoom_at(x, y, aspect, center, zoom, new_zoom):
    """The centre that keeps the image point under view coordinates x, y fixed while zooming."""
    scale = flat_scale(aspect)
    offset = (x / scale[0] * 0.5, -y / scale[1] * 0.5)
    point = (center[0] + offset[0] / zoom, center[1] + offset[1] / zoom)
    return (clamp(point[0] - offset[0] / new_zoom, 0.0, 1.0), clamp(point[1] - offset[1] / new_zoom, 0.0, 1.0))


def srgb_encode(value):
    value = clamp(value, 0.0, 1.0)
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def display_value(linear, exposure=0.0, gamma=1.0):
    """Scene-linear to display, as Nuke's viewer: gain (2^exposure), gamma, then the sRGB curve."""
    value = max(0.0, linear * 2.0 ** exposure)
    return srgb_encode(value ** (1.0 / gamma))


def reduction(width, max_width=DEFAULT_MAX_WIDTH):
    """The whole-number factor a main is downsized by so it is at most ``max_width`` wide."""
    return max(1, -(-int(width) // max(1, int(max_width))))


def fitted_size(width, height, max_width=DEFAULT_MAX_WIDTH):
    """(width, height) the main is shown at: unchanged, or reduced by a whole number."""
    factor = reduction(width, max_width)
    return max(1, int(width) // factor), max(1, int(height) // factor)


def box_reduce(planes, factor):
    """Average ``factor`` x ``factor`` blocks of R, G, B float planes, a band at a time.

    ``planes`` are three 2D arrays (views are fine); returns three float32 arrays.
    """
    import numpy
    height, width = planes[0].shape
    out_h, out_w = height // factor, width // factor
    result = []
    band = max(1, 64 // factor) * factor
    for plane in planes:
        out = numpy.empty((out_h, out_w), numpy.float32)
        for top in range(0, out_h * factor, band):
            bottom = min(out_h * factor, top + band)
            block = numpy.nan_to_num(numpy.asarray(plane[top:bottom, :out_w * factor], numpy.float32),
                                     nan=0.0, posinf=65504.0)
            block = block.reshape((bottom - top) // factor, factor, out_w, factor)
            out[top // factor:bottom // factor] = block.mean(axis=(1, 3), dtype=numpy.float64)
        result.append(out)
    return tuple(result)


# --- RGBE: shared-exponent 8-bit HDR, decoded in the shader --------------------------------

def encode_rgbe(red, green, blue):
    """Float arrays (any shape) to an RGBE uint8 array of shape + (4,) (Ward's Radiance format)."""
    import numpy
    rgb = numpy.stack([red, green, blue], axis=-1).astype(numpy.float32)
    rgb = numpy.nan_to_num(rgb, nan=0.0, posinf=1e30, neginf=0.0)
    numpy.maximum(rgb, 0.0, out=rgb)
    peak = numpy.minimum(rgb.max(axis=-1), 1e37)
    mantissa, exponent = numpy.frexp(peak)
    visible = peak > 1e-32
    scale = numpy.where(visible, mantissa * 256.0 / numpy.where(visible, peak, 1.0), 0.0)
    out = numpy.empty(peak.shape + (4,), numpy.uint8)
    out[..., :3] = numpy.clip(rgb * scale[..., None], 0, 255).astype(numpy.uint8)
    out[..., 3] = numpy.where(visible, numpy.clip(exponent + 128, 0, 255), 0).astype(numpy.uint8)
    return out


def decode_rgbe(pixel):
    """One RGBE pixel (4 integers) to linear (r, g, b), as the shader decodes it."""
    r, g, b, e = (int(value) for value in pixel)
    if not e:
        return 0.0, 0.0, 0.0
    scale = 2.0 ** (e - 128) / 256.0
    return (r + 0.5) * scale, (g + 0.5) * scale, (b + 0.5) * scale


def planes_to_rgbe(planes, rows=256):
    """R, G, B float planes (three 2D arrays; views are fine) to an RGBE array (height, width, 4).

    Converted in bands of rows, so the peak memory is the decoded pixels plus the result.
    Also returns the peak and average luminance for the viewer's information line.
    """
    import numpy
    height, width = planes[0].shape
    out = numpy.empty((height, width, 4), numpy.uint8)
    peak, total = 0.0, 0.0
    for top in range(0, height, rows):
        bottom = min(height, top + rows)
        red, green, blue = (numpy.asarray(planes[index][top:bottom], numpy.float32) for index in range(3))
        out[top:bottom] = encode_rgbe(red, green, blue)
        luminance = numpy.nan_to_num(0.2126 * red + 0.7152 * green + 0.0722 * blue, nan=0.0, posinf=0.0)
        peak = max(peak, float(luminance.max(initial=0.0)))
        total += float(numpy.maximum(luminance, 0.0).sum(dtype=numpy.float64))
    return out, {'peak': peak, 'average': total / max(1, width * height)}


def planar_to_rgbe(data, width, height, rows=256):
    """FFmpeg ``gbrpf32le`` bytes (G, B, R planes) to an RGBE array; see ``planes_to_rgbe``."""
    import numpy
    expected = width * height * 3 * 4
    if len(data) < expected:
        raise ValueError('The main decoded to %d bytes; expected %d for %dx%d.' % (len(data), expected, width, height))
    planes = numpy.frombuffer(data, numpy.float32, width * height * 3).reshape(3, height, width)
    return planes_to_rgbe((planes[2], planes[0], planes[1]), rows)


# --- reading float pixels without any conversion that could clamp them ----------------------

def read_pfm(data):
    """A Portable FloatMap (what oiiotool writes for ``.pfm``) as R, G, B planes (2D views).

    PFM is a short text header and raw floats, bottom row first.
    """
    import numpy
    tokens, position = [], 0
    while len(tokens) < 4:
        while data[position:position + 1].isspace():
            position += 1
        end = position
        while not data[end:end + 1].isspace():
            end += 1
        tokens.append(data[position:end].decode('ascii'))
        position = end
    position += 1  # the single whitespace character before the pixels
    kind, width, height, scale = tokens[0], int(tokens[1]), int(tokens[2]), float(tokens[3])
    if kind not in ('PF', 'Pf'):
        raise ValueError('Not a PFM file (%s).' % kind)
    channels = 3 if kind == 'PF' else 1
    pixels = numpy.frombuffer(data, '<f4' if scale < 0 else '>f4', width * height * channels, position)
    pixels = pixels.reshape(height, width, channels)[::-1]
    if channels == 1:
        return (pixels[..., 0],) * 3
    return pixels[..., 0], pixels[..., 1], pixels[..., 2]


# FFmpeg pixel formats read as they are: (numpy type, planar, channel order).
NATIVE_FORMATS = {
    'gbrpf32le': ('<f4', True, 'gbr'), 'gbrapf32le': ('<f4', True, 'gbra'),
    'gbrpf16le': ('<f2', True, 'gbr'), 'gbrapf16le': ('<f2', True, 'gbra'),
    'grayf32le': ('<f4', True, 'y'), 'grayf16le': ('<f2', True, 'y'),
    'rgbf32le': ('<f4', False, 'rgb'), 'rgbaf32le': ('<f4', False, 'rgba'),
    'rgbf16le': ('<f2', False, 'rgb'), 'rgbaf16le': ('<f2', False, 'rgba'),
}


def read_native(data, pixel_format, width, height):
    """FFmpeg rawvideo in a format from NATIVE_FORMATS as R, G, B planes: 2D views, not copies."""
    import numpy
    kind, planar, order = NATIVE_FORMATS[pixel_format]
    count = width * height * len(order)
    if len(data) != count * numpy.dtype(kind).itemsize:
        raise RuntimeError('FFmpeg decoded the main to %d bytes; %dx%d %s is %d bytes.'
                           % (len(data), width, height, pixel_format, count * numpy.dtype(kind).itemsize))
    values = numpy.frombuffer(data, kind, count)
    channels = (values.reshape(len(order), height, width) if planar
                else values.reshape(height, width, len(order)).transpose(2, 0, 1))
    if order == 'y':
        return (channels[0],) * 3
    return tuple(channels[order.index(name)] for name in 'rgb')


def main_info(path, settings):
    """(width, height, pixel format) of the main, from ffprobe."""
    tools = settings.get('tools', {})
    result = run_tool([tools.get('ffprobe', 'ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                       '-show_entries', 'stream=width,height,pix_fmt', '-of', 'csv=p=0', str(path)],
                      timeout=PROBE_TIMEOUT)
    width, height, pixel_format = (result.stdout.strip().split(',') + ['', '', ''])[:3]
    return int(width), int(height), pixel_format.strip()


def decode_arguments(ffmpeg, path, pixel_format='gbrpf32le'):
    """FFmpeg rawvideo in ``pixel_format``, unscaled. Use the main's own format: FFmpeg's
    converter and scaler clamp float pixels to 1.0 (half-float EXRs in FFmpeg 7.1+)."""
    return [ffmpeg, '-v', 'error', '-i', str(path), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', pixel_format, '-']


def _decode(args):
    try:
        result = subprocess.run([str(value) for value in args], capture_output=True, timeout=DECODE_TIMEOUT,
                                creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('Decoding the main took longer than %d s.' % DECODE_TIMEOUT) from error
    except FileNotFoundError as error:
        raise RuntimeError('Tool not found: %s. Check "tools" in the studio configuration.' % args[0]) from error
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace')[-4000:] or '%s failed.' % args[0])
    return result.stdout


def load_main(path, settings, width=0, height=0):
    """Decode a scene-linear main or highres (EXR or HDR) for the viewer, without clamping it.

    FFmpeg (``ffmpeg`` in tinylib.ini [tools]) decodes the main in its own pixel format,
    never converted: FFmpeg's converter and scaler clamp float pixels to 1.0. numpy
    downsizes it to ``pano_max_width``. A main FFmpeg cannot read, or reads only in a
    format it would have to convert, is read by oiiotool instead, through a temporary PFM
    (raw floats). Returns ``(rgbe, info)``: an RGBE uint8 array (height, width, 4) and a
    dict with the source and texture sizes and the peak and average luminance.
    """
    try:
        import numpy  # noqa: F401 - checked here so the message is clear
    except ImportError as error:
        raise RuntimeError('Showing the main needs numpy, which this Python does not have. '
                           'The proxy can still be viewed.') from error
    tools = settings.get('tools', {})
    max_width = int(settings.get('pano_max_width') or DEFAULT_MAX_WIDTH)
    try:
        probed_width, probed_height, pixel_format = main_info(path, settings)
        if pixel_format not in NATIVE_FORMATS:
            raise RuntimeError('FFmpeg reads this main as %s, which it would clamp.' % (pixel_format or 'an unknown format'))
        data = _decode(decode_arguments(tools.get('ffmpeg', 'ffmpeg'), path, pixel_format))
        planes = read_native(data, pixel_format, probed_width, probed_height)
        del data
        reader = 'FFmpeg %s' % pixel_format
    except RuntimeError as ffmpeg_error:
        oiiotool = tools.get('oiiotool', 'oiiotool')
        if not (Path(oiiotool).is_file() or shutil.which(oiiotool)):
            raise RuntimeError('%s Set "oiiotool" under [tools] in tinylib.ini to read it.' % ffmpeg_error) from ffmpeg_error
        folder = tempfile.mkdtemp(prefix='tinylib-pano-')
        try:
            target = Path(folder) / 'main.pfm'
            run_tool([oiiotool, str(path), '--ch', 'R,G,B', '-d', 'float', '-o', str(target)], timeout=DECODE_TIMEOUT)
            planes = read_pfm(target.read_bytes())
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        reader = 'oiiotool'
    width, height = int(width or planes[0].shape[1]), int(height or planes[0].shape[0])
    if planes[0].shape[1] > max_width:
        planes = box_reduce(planes, reduction(planes[0].shape[1], max_width))
    rgbe, stats = planes_to_rgbe(planes)
    del planes
    return rgbe, dict(stats, source_width=width, source_height=height, reader=reader,
                      width=rgbe.shape[1], height=rgbe.shape[0])
