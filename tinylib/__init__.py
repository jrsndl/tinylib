"""Studio asset browser for Nuke."""
__version__ = "3.1.0"

_window = None


def _alive(window):
    try:
        window.isVisible()  # Raises RuntimeError once Qt has deleted the C++ object.
        return True
    except RuntimeError:
        return False


def show(config_path=None):
    """Open the browser, or bring the existing one to the front.

    Keeping one window per session matters: replacing the reference would let
    Python delete a window whose threads and edit locks are still active.
    """
    global _window
    if _window is not None and _alive(_window):
        same = config_path is None or getattr(_window, 'config_path', None) == config_path
        if same or not _window.close():
            _window.show()
            _window.raise_()
            _window.activateWindow()
            return _window
    from .ui import Browser
    _window = Browser(config_path)
    _window.config_path = config_path
    _window.show()
    return _window
