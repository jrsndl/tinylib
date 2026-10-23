"""Set as thumbnail: permissions, picture geometry and the written file."""
import os
import tempfile
import unittest
from pathlib import Path

from helpers import requires_qt

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import patch

from tinylib.custom_thumbnail import can_set_thumbnail, fit_box, picture_box, replace_file


class FakeAccess:
    def __init__(self, admin=False, grants=(), read_only=False):
        self.is_admin, self.grants, self._read_only = admin, set(grants), read_only

    def can(self, root, capability, action=None):
        return self.is_admin or capability in self.grants

    def read_only(self, root):
        return self._read_only


FOOTAGE = {'kind': 'footage', 'library_root': 'L:/lib', 'thumb': 'L:/lib/fx/a/thumb/a.jpg',
           'metadata': {'width': 3840, 'height': 2160, 'pixel_aspect': 1.0}}


class PermissionTests(unittest.TestCase):
    def test_admins_and_edit_or_ingest_permission(self):
        self.assertTrue(can_set_thumbnail(FakeAccess(admin=True), FOOTAGE)[0])
        self.assertTrue(can_set_thumbnail(FakeAccess(grants={'view', 'edit'}), FOOTAGE)[0])
        self.assertTrue(can_set_thumbnail(FakeAccess(grants={'view', 'ingest'}), FOOTAGE)[0])
        allowed, reason = can_set_thumbnail(FakeAccess(grants={'view'}), FOOTAGE)
        self.assertFalse(allowed)
        self.assertIn('edit or ingest', reason)
        self.assertFalse(can_set_thumbnail(FakeAccess(grants={'edit'}), FOOTAGE)[0])  # cannot even view

    def test_never_in_read_only_libraries_and_only_footage(self):
        self.assertFalse(can_set_thumbnail(FakeAccess(admin=True, read_only=True), FOOTAGE)[0])
        settings = {'libraries': [{'root': 'L:/lib', 'read_only': True}]}
        self.assertFalse(can_set_thumbnail(FakeAccess(admin=True), FOOTAGE, settings)[0])
        self.assertTrue(can_set_thumbnail(FakeAccess(admin=True), FOOTAGE, {'libraries': [{'root': 'X:/other'}]})[0])
        self.assertFalse(can_set_thumbnail(FakeAccess(admin=True), dict(FOOTAGE, kind='still'))[0])
        self.assertFalse(can_set_thumbnail(FakeAccess(admin=True), dict(FOOTAGE, thumb=''))[0])


class GeometryTests(unittest.TestCase):
    def test_fit_box_centres_with_padding(self):
        self.assertEqual(fit_box(1920, 1080, 960, 506), (30, 0, 900, 506))   # 16:9 into the thumbnail canvas
        self.assertEqual(fit_box(2048, 858, 960, 506), (0, 52, 960, 402))    # scope: bars top and bottom
        self.assertEqual(fit_box(960, 506, 960, 506), (0, 0, 960, 506))

    def test_picture_box_removes_the_proxy_padding(self):
        scope = dict(FOOTAGE, metadata={'width': 2048, 'height': 858})
        self.assertEqual(picture_box(1920, 1080, scope), (0, 138, 1920, 804))
        square = dict(FOOTAGE, metadata={'width': 1080, 'height': 1080})
        self.assertEqual(picture_box(1920, 1080, square), (420, 0, 1080, 1080))
        anamorphic = dict(FOOTAGE, metadata={'width': 2048, 'height': 1716, 'pixel_aspect': 2.0})  # 2.39:1 picture
        x, y, w, h = picture_box(1920, 1080, anamorphic)
        self.assertEqual((x, w), (0, 1920))
        self.assertAlmostEqual(w / h, 4096 / 1716, places=2)

    def test_whole_frame_when_it_already_fits_or_metadata_is_missing(self):
        self.assertEqual(picture_box(1920, 1080, FOOTAGE), (0, 0, 1920, 1080))
        self.assertEqual(picture_box(1920, 1080, dict(FOOTAGE, metadata={})), (0, 0, 1920, 1080))
        self.assertEqual(picture_box(1920, 1080, dict(FOOTAGE, metadata={'width': 0, 'height': 0})), (0, 0, 1920, 1080))


class ReplaceTests(unittest.TestCase):
    def test_a_file_in_use_is_retried_then_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'new.jpg', Path(folder) / 'thumb.jpg'
            source.write_bytes(b'new')
            target.write_bytes(b'old')
            real, calls = os.replace, []

            def busy_twice(a, b):
                calls.append(a)
                if len(calls) <= 2:
                    raise PermissionError(32, 'The process cannot access the file because it is being used')
                return real(a, b)
            with patch('tinylib.custom_thumbnail.os.replace', side_effect=busy_twice):
                replace_file(source, target, delay=0)
            self.assertEqual((len(calls), target.read_bytes()), (3, b'new'))
            source.write_bytes(b'newer')
            with patch('tinylib.custom_thumbnail.os.replace', side_effect=PermissionError(32, 'in use')):
                with self.assertRaises(PermissionError):
                    replace_file(source, target, attempts=3, delay=0)
            self.assertEqual(target.read_bytes(), b'new')


@requires_qt
class SaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tinylib.qt import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def frame(self, color='#ff0000'):
        """A 1920x1080 proxy frame: a 2.39:1 red picture with black bars."""
        from tinylib.qt import QtGui
        image = QtGui.QImage(1920, 1080, QtGui.QImage.Format_RGB888)
        image.fill(QtGui.QColor('#000000'))
        painter = QtGui.QPainter(image)
        painter.fillRect(0, 138, 1920, 804, QtGui.QColor(color))
        painter.end()
        return image

    def test_same_size_and_type_as_the_old_thumbnail(self):
        from tinylib.qt import QtGui
        from tinylib.custom_thumbnail import existing_thumbnail, save_thumbnail
        for suffix, size in (('.jpg', (960, 506)), ('.png', (640, 360))):
            path = Path(self.temp.name) / ('thumb' + suffix)
            old = QtGui.QImage(*size, QtGui.QImage.Format_RGB888)
            old.fill(QtGui.QColor('#00ff00'))
            self.assertTrue(old.save(str(path)))
            asset = dict(FOOTAGE, thumb=str(path), metadata={'width': 2048, 'height': 858})
            self.assertEqual(save_thumbnail(self.frame(), asset), path)
            self.assertEqual(existing_thumbnail(path)[1:], size)
            self.assertEqual(existing_thumbnail(path)[0], 'jpeg' if suffix == '.jpg' else 'png')
            image = QtGui.QImage(str(path))
            centre = image.pixelColor(size[0] // 2, size[1] // 2)
            self.assertGreater(centre.red(), 200)
            self.assertLess(centre.green(), 60)  # the old green is gone
            self.assertLess(image.pixelColor(size[0] // 2, 2).red(), 40)  # black padding, as generated ones
            self.assertEqual([p.name for p in path.parent.iterdir() if 'tinylib-tmp' in p.name], [])

    def test_a_missing_thumbnail_is_not_created(self):
        from tinylib.custom_thumbnail import save_thumbnail
        with self.assertRaisesRegex(ValueError, 'missing'):
            save_thumbnail(self.frame(), dict(FOOTAGE, thumb=str(Path(self.temp.name) / 'none.jpg')))


if __name__ == '__main__':
    unittest.main()
