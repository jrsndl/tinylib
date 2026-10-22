"""AYON and ftrack credentials for the Publish to AYON action. No Qt here.

Adapted from Submission Helper's credentials.py (github.com/jrsndl/SubmissionHelper).

Where credentials come from, first found wins:

1. the environment: AYON_SERVER_URL / AYON_API_KEY and FTRACK_SERVER /
   FTRACK_API_USER / FTRACK_API_KEY. Nuke started by the AYON launcher has them.
2. the system keyring (Windows Credential Manager): what the user entered in the
   sign-in dialogs of this action, stored under TinyLib's own names
   ("TinyLib/ayon/<host>", "TinyLib/ftrack/<host>").
3. nothing: the publish dialog asks the user to sign in (AYON is required, ftrack is not).

Server URLs come from the environment, else the action's config.json, else the URL the
user typed in a sign-in dialog.

AYON: only the user name and the session token are stored, never the password. A stored
token is checked before use; an expired one is removed and the user signs in again.
ftrack: the personal API key, received from the ftrack web app in the browser or typed in.
"""
import functools
import html
import logging
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import parse

log = logging.getLogger('tinylib.ayon_publish')

KEYRING_PREFIX = 'TinyLib'
AYON = 'ayon'
FTRACK = 'ftrack'
SOURCE_ENV = 'environment'
SOURCE_KEYRING = 'keyring'

# The credential variables as they were when TinyLib started. ayon_api writes
# AYON_API_KEY into the environment when it connects; a token the user signed in with
# must not look as if the AYON launcher had supplied it.
CREDENTIAL_ENV_KEYS = ('AYON_SERVER_URL', 'AYON_API_KEY', 'AYON_USERNAME',
                       'FTRACK_SERVER', 'FTRACK_API_USER', 'FTRACK_API_KEY')
_STARTUP_ENV = {key: os.environ.get(key, '') for key in CREDENTIAL_ENV_KEYS}


def startup_env():
    return dict(_STARTUP_ENV)


def ayon_from_environment(env=None):
    """True when the host was started with AYON (server and key in the environment),
    e.g. Nuke launched by the AYON launcher."""
    env = startup_env() if env is None else env
    return bool(env.get('AYON_SERVER_URL') and env.get('AYON_API_KEY'))


# ---------------------------------------------------------------- keyring

_keyring_module = None
_keyring_failed = False


def _keyring():
    """The keyring module (Windows Credential Manager on Windows), or None."""
    global _keyring_module, _keyring_failed
    if _keyring_module is not None or _keyring_failed:
        return _keyring_module
    try:
        import keyring
        if sys.platform == 'win32':
            # A frozen TinyLib.exe does not always find the backend by itself.
            from keyring.backends import Windows
            keyring.set_keyring(Windows.WinVaultKeyring())
        _keyring_module = keyring
    except Exception as error:
        _keyring_failed = True
        log.warning('The system keyring is not available, credentials cannot be stored: %s', error)
    return _keyring_module


def set_keyring_backend(module):
    """Tests: use a fake keyring (get_password / set_password / delete_password)."""
    global _keyring_module, _keyring_failed
    _keyring_module = module
    _keyring_failed = module is None


def keyring_available():
    return _keyring() is not None


def hostname(url):
    """'https://studio.ftrackapp.com/' -> 'studio.ftrackapp.com'"""
    if not url:
        return ''
    if '//' not in url:
        url = '//' + url
    return (parse.urlparse(url).hostname or '').lower()


def _service(kind, url):
    return '%s/%s/%s' % (KEYRING_PREFIX, kind, hostname(url))


def _get(kind, url, item):
    backend = _keyring()
    if backend is None or not hostname(url):
        return None
    try:
        return backend.get_password(_service(kind, url), item)
    except Exception as error:
        log.warning('Reading %s credentials from the keyring failed: %s', kind, error)
        return None


def _set(kind, url, item, value):
    backend = _keyring()
    if backend is None or not hostname(url):
        return False
    try:
        backend.set_password(_service(kind, url), item, value)
        return True
    except Exception as error:
        log.error('Storing %s credentials in the keyring failed: %s', kind, error)
        return False


def _delete(kind, url, item):
    backend = _keyring()
    if backend is None or not hostname(url):
        return
    try:
        if backend.get_password(_service(kind, url), item) is not None:
            backend.delete_password(_service(kind, url), item)
    except Exception as error:
        log.warning('Removing %s credentials from the keyring failed: %s', kind, error)


def stored_server_url(kind):
    """The server URL the user typed in a sign-in dialog (none was configured)."""
    backend = _keyring()
    if backend is None:
        return ''
    try:
        return backend.get_password('%s/%s' % (KEYRING_PREFIX, kind), 'server_url') or ''
    except Exception:
        return ''


def save_server_url(kind, url):
    backend = _keyring()
    if backend is None or not url:
        return
    try:
        backend.set_password('%s/%s' % (KEYRING_PREFIX, kind), 'server_url', url)
    except Exception as error:
        log.warning('Storing the %s server URL failed: %s', kind, error)


# ---------------------------------------------------------------- ftrack

def get_ftrack(url):
    """(username, api_key) stored for this ftrack server, or (None, None)."""
    return _get(FTRACK, url, 'username'), _get(FTRACK, url, 'api_key')


def save_ftrack(url, username, api_key):
    ok = _set(FTRACK, url, 'username', username)
    ok = _set(FTRACK, url, 'api_key', api_key) and ok
    return ok


def clear_ftrack(url):
    _delete(FTRACK, url, 'username')
    _delete(FTRACK, url, 'api_key')


def check_ftrack(url, username, api_key):
    """(ok, message): the server accepts the user and key. Never raises.

    Without the ftrack_api module the key cannot be checked here; it is accepted and
    the AYON publish reports a wrong one.
    """
    if not url or not username or not api_key:
        return False, 'server, user name and API key are needed'
    try:
        import ftrack_api
    except ImportError:
        return True, ''
    try:
        session = ftrack_api.Session(server_url=url, api_key=api_key, api_user=username,
                                     auto_connect_event_hub=False, timeout=15)
        try:
            user = session.query('User where username is "%s"' % username).first()
        finally:
            session.close()
        if user is None:
            return False, 'user %s not found' % username
        return True, ''
    except Exception as error:
        return False, str(error)


class _FtrackLoginHandler(BaseHTTPRequestHandler):
    """Receives the redirect from the ftrack web app with the credentials."""

    def __init__(self, on_credentials, *args, **kwargs):
        self.on_credentials = on_credentials
        super().__init__(*args, **kwargs)

    def log_message(self, format_string, *args):
        log.debug('ftrack sign-in: ' + (format_string % args))

    def do_GET(self):
        query = parse.parse_qs(parse.urlparse(self.path).query)
        api_user = (query.get('api_user') or [None])[0]
        api_key = (query.get('api_key') or [None])[0]
        if api_user and api_key:
            body = ("<html><body style='font-family:sans-serif'><h2>Signed in to ftrack as %s</h2>"
                    "<p>You can close this tab and return to TinyLib.</p></body></html>" % html.escape(api_user))
        else:
            body = '<html><body><h2>ftrack sign-in failed</h2></body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))
        if api_user and api_key:
            self.on_credentials(api_user, api_key)


class FtrackBrowserLogin(threading.Thread):
    """Sign in to ftrack in the web browser, as the ftrack addon of AYON does: a one-shot
    web server on localhost; the browser opens <server>/user/api_credentials with a
    redirect to it, and the ftrack web app sends back api_user and the personal api_key.

    callback(api_user, api_key) runs in this thread.
    """

    def __init__(self, url, callback, open_browser=webbrowser.open_new_tab):
        super().__init__(daemon=True)
        self.url = url.rstrip('/')
        self.callback = callback
        self.open_browser = open_browser
        self._server = HTTPServer(('localhost', 0), functools.partial(_FtrackLoginHandler, callback))
        self.login_url = '%s/user/api_credentials?redirect_url=http://localhost:%d' % (
            self.url, self._server.server_port)
        self._stopped = False

    def run(self):
        self.open_browser(self.login_url)
        try:
            self._server.handle_request()
        except Exception as error:
            if not self._stopped:
                log.warning('ftrack browser sign-in failed: %s', error)
        finally:
            self._server.server_close()

    def stop(self):
        self._stopped = True
        try:
            self._server.server_close()
        except Exception:
            pass


# ---------------------------------------------------------------- AYON

def get_ayon(url):
    """(username, token) stored for this AYON server."""
    return _get(AYON, url, 'username'), _get(AYON, url, 'token')


def save_ayon(url, username, token):
    """Store the user name and the session token, never the password."""
    ok = _set(AYON, url, 'username', username)
    return _set(AYON, url, 'token', token) and ok


def clear_ayon(url):
    for item in ('username', 'token'):
        _delete(AYON, url, item)


def ayon_login(url, username, password):
    """(token or None, message). Never raises."""
    if not url or not username or not password:
        return None, 'server, user name and password are needed'
    try:
        import ayon_api
        token = ayon_api.login_to_server(url.rstrip('/'), username, password)
    except Exception as error:
        return None, str(error)
    if not token:
        return None, 'wrong user name or password'
    return token, ''


def ayon_token_valid(url, token):
    if not url or not token:
        return False
    try:
        import ayon_api
        return bool(ayon_api.is_token_valid(url.rstrip('/'), token))
    except Exception as error:
        log.warning('Checking the AYON login failed: %s', error)
        return False


def valid_ayon_token(url, check=ayon_token_valid):
    """The stored token while the server still accepts it, else None. An expired token is
    removed; the user name is kept to pre-fill the next sign-in."""
    _, token = get_ayon(url)
    if not token:
        return None
    if check(url, token):
        return token
    _delete(AYON, url, 'token')
    return None


def ayon_logout(url):
    """End the stored session on the server and forget it."""
    _, token = get_ayon(url)
    if token:
        try:
            import ayon_api
            ayon_api.logout_from_server(url.rstrip('/'), token)
        except Exception as error:
            log.debug('AYON logout failed: %s', error)
    clear_ayon(url)


# ---------------------------------------------------------------- resolve

def resolve(config=None, env=None):
    """Credentials by the order in the module docstring, without network access.

    Returns a dict with ayon_url, ayon_key, ayon_user, ftrack_url, ftrack_user, ftrack_key
    and sources = {'ayon': ..., 'ftrack': ...} (None when missing). A keyring AYON token
    is checked later, by ``ensure_ayon``.
    """
    config = config or {}
    env = startup_env() if env is None else env
    result = {'sources': {AYON: None, FTRACK: None}}

    url = env.get('AYON_SERVER_URL') or config.get('ayon_server_url') or stored_server_url(AYON)
    key, user = env.get('AYON_API_KEY'), env.get('AYON_USERNAME') or ''
    if url and key:
        result['sources'][AYON] = SOURCE_ENV
    else:
        stored_user, key = get_ayon(url) if url else (None, None)
        if key:
            result['sources'][AYON] = SOURCE_KEYRING
            user = stored_user or user
        else:
            key = ''
            user = stored_user or user
    result.update(ayon_url=(url or '').rstrip('/'), ayon_key=key or '', ayon_user=user or '')

    url = env.get('FTRACK_SERVER') or config.get('ftrack_server') or stored_server_url(FTRACK)
    user, key = env.get('FTRACK_API_USER'), env.get('FTRACK_API_KEY')
    if url and user and key:
        result['sources'][FTRACK] = SOURCE_ENV
    else:
        user, key = get_ftrack(url) if url else (None, None)
        if user and key:
            result['sources'][FTRACK] = SOURCE_KEYRING
        else:
            user, key = '', ''
    result.update(ftrack_url=(url or '').rstrip('/'), ftrack_user=user or '', ftrack_key=key or '')
    return result


def ensure_ayon(credentials, check=ayon_token_valid):
    """A working AYON token in ``credentials``: an environment token is used as it is; a
    keyring token is checked and dropped when expired. True when a token is set."""
    if credentials.get('ayon_key') and credentials['sources'].get(AYON) == SOURCE_ENV:
        return True
    url = credentials.get('ayon_url')
    token = valid_ayon_token(url, check) if url else None
    if token:
        credentials['ayon_key'] = token
        credentials['ayon_user'] = get_ayon(url)[0] or credentials.get('ayon_user', '')
        credentials['sources'][AYON] = SOURCE_KEYRING
        return True
    credentials['ayon_key'] = ''
    credentials['sources'][AYON] = None
    return False


def has_ftrack(credentials):
    return bool(credentials.get('ftrack_url') and credentials.get('ftrack_user') and credentials.get('ftrack_key'))


def windows_user():
    """The login name, to pre-fill the AYON user name."""
    return os.environ.get('USERNAME') or os.environ.get('USER') or ''
