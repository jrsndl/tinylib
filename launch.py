"""Standalone browser: python launch.py --demo or --config path/to/studio.json."""
import argparse
import sys
from pathlib import Path
from tinylib.qt import QtWidgets
from tinylib import show

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    config = str(Path(__file__).parent / 'config' / 'demo.json') if args.demo else args.config
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = show(config)
    sys.exit(app.exec_() if hasattr(app, 'exec_') else app.exec())
