"""Loaded when this directory is registered with nuke.pluginAddPath."""
import traceback

import nuke


def _open_tinylib():
    try:
        import tinylib
        tinylib.show()
    except Exception as error:
        traceback.print_exc()
        nuke.message('TinyLib could not open:\n%s' % error)


nuke.menu('Nuke').addCommand('TinyLib/Studio library', _open_tinylib)
