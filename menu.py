"""Loaded when this directory is registered with nuke.pluginAddPath."""
import nuke
import tinylib
nuke.menu('Nuke').addCommand('TinyLib/Studio library', tinylib.show)
