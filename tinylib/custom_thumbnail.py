"""User-picked thumbnail: a stopped player frame replaces a Footage asset's thumbnail on disk.

The new file keeps the existing thumbnail's file type and pixel size. The frame comes from
the proxy, which is the main fitted into 1920 × 1080 with black padding; that padding is
cropped using the main's own aspect ratio, and the picture is fitted into the thumbnail with
black padding, as generated thumbnails are.
"""
import os
import time
import uuid
from pathlib import Path

from .storage import clear_hidden

JPEG_QUALITY = 95


def can_set_thumbnail(access, asset, settings=None):
    """(allowed, reason). Footage only; administrators, and users with the library's edit or
    ingest permission; never in a read-only library."""
    root = asset.get('library_root', '')
    if asset.get('kind') != 'footage':
        return False, 'Only Footage assets can have a picked thumbnail.'
    from .access import root_key
    library = next((item for item in (settings or {}).get('libraries', [])
                    if root_key(item.get('root', '')) == root_key(root)), {})
    if library.get('read_only') or access.read_only(root):
        return False, 'The library is read-only.'
    if not access.can(root, 'view'):
        return False, 'You cannot view this library.'
    if not (access.is_admin or access.can(root, 'edit') or access.can(root, 'ingest')):
        return False, 'Setting a thumbnail needs administrator rights or the library\'s edit or ingest permission.'
    if not asset.get('thumb'):
        return False, 'The asset has no thumbnail file to replace.'
    return True, ''


def fit_box(width, height, box_width, box_height):
    """(x, y, w, h) of a width × height picture fitted and centred inside box_width × box_height."""
    scale = min(box_width / width, box_height / height)
    fitted_width = max(1, min(box_width, round(width * scale)))
    fitted_height = max(1, min(box_height, round(height * scale)))
    return (box_width - fitted_width) // 2, (box_height - fitted_height) // 2, fitted_width, fitted_height


def picture_box(frame_width, frame_height, asset):
    """(x, y, w, h) of the main's picture inside a proxy frame, without the proxy's padding.

    The aspect ratio is the main's (width × pixel aspect / height, from the asset metadata);
    without it, or when it already matches the frame within a pixel, the whole frame.
    """
    metadata = asset.get('metadata') or {}
    try:
        aspect = float(metadata['width']) * float(metadata.get('pixel_aspect') or 1) / float(metadata['height'])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 0, 0, frame_width, frame_height
    if aspect <= 0:
        return 0, 0, frame_width, frame_height
    box = fit_box(aspect, 1.0, frame_width, frame_height)
    if box[2] >= frame_width - 1 and box[3] >= frame_height - 1:
        return 0, 0, frame_width, frame_height
    return box


def image_info(data, name=''):
    """(Qt format name, width, height) of encoded image bytes. Read from memory, so no file
    stays open (Windows cannot replace a file that is open, even in this process)."""
    from .qt import QtCore, QtGui
    buffer = QtCore.QBuffer()
    buffer.setData(QtCore.QByteArray(data))
    buffer.open(QtCore.QIODevice.ReadOnly)
    reader = QtGui.QImageReader(buffer)
    image_format = bytes(reader.format()).decode('ascii', 'replace').lower()
    size = reader.size()
    if not image_format or not size.isValid() or size.isEmpty():
        image = reader.read()
        if image.isNull():
            raise ValueError('The image cannot be read: %s (%s)' % (name, reader.errorString()))
        size = image.size()
        image_format = image_format or Path(name).suffix.lower().lstrip('.')
    buffer.close()
    return image_format, size.width(), size.height()


def existing_thumbnail(path):
    """(Qt format name, width, height) of the thumbnail file on disk."""
    with open(path, 'rb') as stream:
        return image_info(stream.read(), str(path))


def encode(image, image_format):
    """The image encoded as ``image_format`` (JPEG at JPEG_QUALITY), as bytes."""
    from .qt import QtCore, QtGui
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.WriteOnly)
    writer = QtGui.QImageWriter(buffer, image_format.encode('ascii'))
    if image_format in ('jpg', 'jpeg'):
        writer.setQuality(JPEG_QUALITY)
    if not writer.write(image):
        raise ValueError('Cannot encode the thumbnail as %s: %s' % (image_format, writer.errorString()))
    buffer.close()
    return bytes(buffer.data())


def replace_file(source, target, attempts=20, delay=.25):
    """os.replace, retried for a few seconds while Windows reports the file in use: an antivirus
    or the file server often holds a file that was just written."""
    for attempt in range(attempts):
        try:
            os.replace(str(source), str(target))
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def make_thumbnail(frame, asset, width, height):
    """The frame without the proxy padding, fitted into a black width × height image."""
    from .qt import QtCore, QtGui
    x, y, w, h = picture_box(frame.width(), frame.height(), asset)
    picture = frame.copy(x, y, w, h)
    fx, fy, fw, fh = fit_box(w, h, width, height)
    canvas = QtGui.QImage(width, height, QtGui.QImage.Format_RGB888)
    canvas.fill(QtGui.QColor(0, 0, 0))
    painter = QtGui.QPainter(canvas)
    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    painter.drawImage(QtCore.QRect(fx, fy, fw, fh), picture)
    painter.end()
    return canvas


def save_thumbnail(frame, asset):
    """Replace the asset's thumbnail with ``frame`` (a QImage): same file type and size as the
    file it replaces, written beside it and swapped in. Returns the thumbnail path."""
    path = Path(asset['thumb'])
    if not path.is_file():
        raise ValueError('The thumbnail file is missing: %s' % path)
    image_format, width, height = existing_thumbnail(path)
    image = make_thumbnail(frame, asset, width, height)
    data = encode(image, image_format)
    written = image_info(data, str(path))
    if written[1:] != (width, height):
        raise ValueError('The new thumbnail is %dx%d, not %dx%d.' % (written[1], written[2], width, height))
    # Not a dot name: a Samba share would hide it, and the thumbnail after the rename.
    temporary = path.with_name('%s.%s.tinylib-tmp%s' % (path.stem, uuid.uuid4().hex[:8], path.suffix))
    try:
        with open(temporary, 'wb') as stream:
            stream.write(data)
        replace_file(temporary, path)
        clear_hidden(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return path
