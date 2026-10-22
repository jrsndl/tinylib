"""The Length, Width and Stars filters: typing a number turns the rule on, clearing it off."""
import os
import unittest

from helpers import HAVE_QT, requires_qt

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if HAVE_QT:
    from tinylib.qt import QtCore, QtWidgets
    from tinylib.ui import NumericFilter, StarFilter
    if QtCore.__name__.startswith('PySide6'):
        from PySide6.QtTest import QTest
    else:
        from PySide2.QtTest import QTest


@requires_qt
class NumericFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make(self, *args, **kwargs):
        widget = NumericFilter(*args, **kwargs)
        self.addCleanup(widget.close)
        changes = []
        widget.changed.connect(lambda: changes.append(widget.rule()))
        return widget, changes

    def test_typing_turns_the_rule_on_and_clearing_turns_it_off(self):
        widget, changes = self.make('Width', 1000000)
        self.assertEqual(widget.rule(), ('', 0.0))
        QTest.keyClicks(widget.value, '1920')
        self.assertEqual(widget.rule(), ('>', 1920.0))
        self.assertEqual(changes[-1], ('>', 1920.0))
        widget.value.selectAll()
        QTest.keyClick(widget.value, QtCore.Qt.Key_Delete)
        self.assertEqual(widget.rule(), ('', 0.0))

    def test_stars_use_equals_and_zero_filters_unrated(self):
        widget, _ = self.make('Stars', 5, typed_operator='=')
        QTest.keyClicks(widget.value, '0')
        self.assertEqual(widget.rule(), ('=', 0.0))
        QTest.keyClicks(widget.value, '9')  # "09" is accepted as text but capped at 5
        self.assertEqual(widget.rule()[1], 5.0)

    def test_decimals_accept_a_point_or_a_comma_and_reject_letters(self):
        widget, _ = self.make('Length', 1000000, 3)
        QTest.keyClicks(widget.value, '2,5x')
        self.assertEqual(widget.value.text(), '2,5')
        self.assertEqual(widget.rule(), ('>', 2.5))
        widget.reset()
        self.assertEqual((widget.value.text(), widget.rule()), ('', ('', 0.0)))
        widget.value.setValue(1.25)
        self.assertEqual(widget.value.text(), '1.25')


@requires_qt
class StarFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_clicking_stars_sets_a_minimum_and_clicking_again_clears_it(self):
        widget = StarFilter()
        self.addCleanup(widget.close)
        changes = []
        widget.changed.connect(lambda: changes.append(widget.rule()))
        self.assertEqual(widget.rule(), ('', 0))
        self.assertFalse(widget.any_button.isEnabled())
        QTest.mouseClick(widget.buttons[2], QtCore.Qt.LeftButton)
        self.assertEqual(widget.rule(), ('>=', 3))
        self.assertEqual([button.filled for button in widget.buttons], [True, True, True, False, False])
        QTest.mouseClick(widget.buttons[4], QtCore.Qt.LeftButton)
        self.assertEqual(widget.rule(), ('>=', 5))
        QTest.mouseClick(widget.buttons[4], QtCore.Qt.LeftButton)  # the highest lit star again: any
        self.assertEqual(widget.rule(), ('', 0))
        QTest.mouseClick(widget.buttons[0], QtCore.Qt.LeftButton)
        QTest.mouseClick(widget.any_button, QtCore.Qt.LeftButton)
        self.assertEqual(widget.rule(), ('', 0))
        self.assertEqual(changes, [('>=', 3), ('>=', 5), ('', 0), ('>=', 1), ('', 0)])
        widget.set_minimum(2)
        widget.reset()
        self.assertEqual((widget.rule(), sum(button.filled for button in widget.buttons)), (('', 0), 0))


if __name__ == '__main__':
    unittest.main()
