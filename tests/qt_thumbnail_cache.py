"""Verify that a second ImageCache instance reads the local disk preview."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tinylib.qt import QtGui, QtWidgets
from tinylib.thumbnail_cache import cached_image_path
from tinylib.views import Cards, ImageCache

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    source = root / 'library-thumb.jpg'
    image = QtGui.QImage(960, 506, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor('#ca782d'))
    assert image.save(str(source), 'JPG')
    disk_root = root / 'local-cache'

    first = ImageCache(disk_root=disk_root)
    assert first.get(str(source)).isNull()
    first.pool.waitForDone()
    app.processEvents()
    assert cached_image_path(disk_root, source).is_file()
    assert first.disk_hits == 0

    second = ImageCache(disk_root=disk_root)
    assert second.get(str(source)).isNull()
    second.pool.waitForDone()
    app.processEvents()
    pixmap = second.get(str(source))
    assert not pixmap.isNull() and pixmap.width() == 640
    assert second.disk_hits == 1
    assert second.snapshot()['completed'] == 1 and second.snapshot()['pending'] == 0

# Filmstrips exceed the full-image budget with enough visible animated cards.
# Evicted strips must still have a lightweight image while being reloaded.
with tempfile.TemporaryDirectory() as temporary:
    cache = ImageCache(disk_root=temporary)
    cache.MAX_BYTES = 96 * 1024 * 1024  # Small budget so 50 strips must evict.
    strip = QtGui.QImage(5760, 135, QtGui.QImage.Format_RGB32)
    strip.fill(QtGui.QColor('#ca782d'))
    assets = [{'kind': 'footage', 'filmstrip': 'strip-%d.jpg' % i, 'thumb': ''} for i in range(50)]
    for asset in assets:
        cache.received(asset['filmstrip'], strip, False)
    assert len(cache.images) < len(assets), 'Test must force full-image eviction'
    assert cache.bytes <= 96 * 1024 * 1024
    assert cache.fallback_bytes <= 24 * 1024 * 1024
    view = QtWidgets.QListView()
    cards = Cards(view, cache)
    cards.play_all = True
    # Recently evicted paths show their poster without re-reading for a few seconds...
    with patch.object(cache.pool, 'start'):
        assert not cards.pixmap(assets[0], 0).isNull()
    assert cache.requested == 0, 'Evicted images must not be re-read during the cooldown'
    cache.EVICTION_COOLDOWN = 0  # ...and are read again once it has passed.
    with patch.object(cache.pool, 'start'):
        for width in (180, 360, 248):
            cards.width = width
            for row, asset in enumerate(assets):
                assert not cards.pixmap(asset, row).isNull(), (width, row)
    assert cache.requested > 0, 'Evicted images must still reload in the background'
    # Updating the same path must not inflate memory accounting.
    last = assets[-1]['filmstrip']
    before = cache.bytes
    cache.received(last, strip, False)
    assert cache.bytes == before
    view.close()

print('Preview cache passed: local persistence, eviction fallback, Play all, resizing, bounded memory')
