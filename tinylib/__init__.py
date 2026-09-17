"""Studio asset browser for Nuke."""
__version__ = "3.0.0"


def show(config_path=None):
    from .ui import Browser
    global _window
    _window = Browser(config_path)
    _window.show()
    return _window
