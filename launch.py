"""Standalone browser: python launch.py [--demo | --config path/to/studio.json]."""
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='TinyLib studio asset browser.')
    parser.add_argument('--config', help='browser configuration JSON (default: config in tinylib.ini)')
    parser.add_argument('--demo', action='store_true', help='use config/demo.json')
    args = parser.parse_args()
    if args.demo and not (Path(__file__).parent / 'config' / 'demo.json').is_file():
        parser.error('config/demo.json is not present in this installation.')
    # Import Qt only after argparse so --help works without PySide installed.
    from tinylib.qt import QtWidgets, exec_dialog
    from tinylib import show
    config = str(Path(__file__).parent / 'config' / 'demo.json') if args.demo else args.config
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = show(config)  # noqa: F841 - keep a reference for the lifetime of the app
    return exec_dialog(app)


if __name__ == '__main__':
    sys.exit(main())
