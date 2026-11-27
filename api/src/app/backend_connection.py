"""Connection to the photos backend (``LUMINARY_PHOTOS_URI``): opened once,
kept alive, and observable.

Read-only like the rest of the photos access: this module only resolves,
connects, authenticates and ``stat``s the root -- it never writes to, creates
in or deletes from the backend.

Why it exists: fsspec's SMB filesystem connects inside its constructor
(DNS, TCP, negotiate, auth, with retries), so the first photo opened after a
start -- or after the server dropped an idle session -- used to stall for
however long that took, with nothing to show for it. Now:

* ``start()`` connects on a background thread at startup (never in the
  lifespan itself, so a slow or unreachable server can't hold up the
  liveness probe), and for SMB a keepalive thread ``stat``s the root every
  ``LUMINARY_PHOTOS_KEEPALIVE_SECONDS`` so an idle session isn't dropped by
  the server -- and a dropped one is noticed and rebuilt.
* ``get_fs_and_root()`` hands out the one cached filesystem; concurrent
  callers wait on a single connect attempt rather than each making their own.
* ``snapshot()`` is what ``GET /api/backend/status`` returns: the state plus
  the connection steps and how long each took, so the web UI can tell the
  user what is happening while it waits.
"""

import contextlib
import logging
import socket
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import fsspec

from .config import (
    get_photos_keepalive_seconds,
    get_photos_password,
    get_photos_uri,
    get_photos_username,
    redact_uri,
)

logger = logging.getLogger(__name__)

# Protocols whose fsspec filesystem accepts plain `username`/`password`
# kwargs (see storage.py's former note: S3 uses boto3's own credential chain,
# HTTP has no generic equivalent).
_USERNAME_PASSWORD_PROTOCOLS = {"smb"}
SMB_DEFAULT_PORT = 445
_PROBE_TIMEOUT_SECONDS = 10

STEP_LABELS = {
    "resolve": "Looking up the photo server",
    "connect": "Opening a connection",
    "authenticate": "Signing in",
    "verify": "Opening the photo share",
}

_state_lock = threading.Lock()  # guards everything below
_connect_lock = threading.Lock()  # one connect attempt at a time
_cached: Optional[tuple] = None  # (key, fs, root)
_status = "idle"  # idle | connecting | ready | error
_reconnecting = False
_steps: list[dict] = []
_error: Optional[str] = None
_attempt_started: Optional[float] = None

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


def reset() -> None:
    """Forget the connection and its state (tests; also what a reconnect
    starts from)."""
    global _cached, _status, _steps, _error, _attempt_started, _reconnecting
    with _state_lock:
        _cached = None
        _status = "idle"
        _steps = []
        _error = None
        _attempt_started = None
        _reconnecting = False


def _credentials(protocol: str) -> dict:
    credentials = {}
    if protocol in _USERNAME_PASSWORD_PROTOCOLS:
        username = get_photos_username()
        password = get_photos_password()
        if username is not None:
            credentials["username"] = username
        if password is not None:
            credentials["password"] = password
    return credentials


def _key() -> tuple:
    return (get_photos_uri(), get_photos_username(), get_photos_password())


def _probe_host(host: str, port: int) -> None:
    """Names the failing step precisely: DNS, then a bare TCP connect. Both
    cheap, and fsspec would otherwise report a failure of either as one
    opaque error after its own retries."""
    with _step("resolve"):
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    with _step("connect"):
        socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_SECONDS).close()


@contextlib.contextmanager
def _step(name: str):
    entry = {"name": name, "label": STEP_LABELS[name], "status": "running", "seconds": None}
    started = time.monotonic()
    with _state_lock:
        _steps.append(entry)
    try:
        yield
    except BaseException:
        entry["status"] = "failed"
        raise
    else:
        entry["status"] = "done"
    finally:
        entry["seconds"] = round(time.monotonic() - started, 2)


def _open(key: tuple) -> tuple:
    uri = key[0]
    protocol, _ = fsspec.core.split_protocol(uri)
    is_smb = protocol == "smb"
    if is_smb:
        parsed = urlparse(uri)
        if parsed.hostname:
            _probe_host(parsed.hostname, parsed.port or SMB_DEFAULT_PORT)
    with _step("authenticate"):
        fs, root = fsspec.core.url_to_fs(uri, **_credentials(protocol))
    root = root.rstrip("/") or "/"
    if is_smb:
        with _step("verify"):
            try:
                fs.info(root)
            except FileNotFoundError:
                pass  # a wrong path is not a connection problem
    return fs, root


def _connect(key: tuple) -> tuple:
    global _cached, _status, _steps, _error, _attempt_started, _reconnecting
    with _connect_lock:
        with _state_lock:
            if _cached is not None and _cached[0] == key:
                return _cached[1], _cached[2]
            _status = "connecting"
            _steps = []
            _error = None
            _attempt_started = time.monotonic()
        logger.info("Connecting to photos backend %s", redact_uri(key[0]))
        try:
            fs, root = _open(key)
        except Exception as exc:
            logger.exception("Could not connect to photos backend %s", redact_uri(key[0]))
            with _state_lock:
                _status = "error"
                _error = str(exc) or type(exc).__name__
                _reconnecting = False
            raise
        with _state_lock:
            _cached = (key, fs, root)
            _status = "ready"
            _reconnecting = False
            took = time.monotonic() - _attempt_started
        logger.info("Connected to photos backend %s in %.1fs", redact_uri(key[0]), took)
        return fs, root


def get_fs_and_root() -> tuple[fsspec.AbstractFileSystem, str]:
    """The photos backend's filesystem and root path, connecting first if
    nothing is connected yet (or the configuration changed)."""
    key = _key()
    with _state_lock:
        if _cached is not None and _cached[0] == key:
            return _cached[1], _cached[2]
    return _connect(key)


def is_connection_error(exc: BaseException) -> bool:
    """Whether `exc` looks like the session/connection dying (as opposed to
    a missing file or a bad path), matched by name so smbprotocol needn't be
    importable for non-SMB backends."""
    if isinstance(exc, (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError)):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError, EOFError)):
        return True
    return any(
        marker in cls.__name__ for cls in type(exc).__mro__ for marker in ("Connection", "Transport", "Timeout")
    )


def reconnect(failed_fs: Optional[fsspec.AbstractFileSystem] = None) -> tuple[fsspec.AbstractFileSystem, str]:
    """Throw away the cached connection and open a fresh one. If another
    thread already replaced `failed_fs`, that new connection is returned
    instead of tearing it down again."""
    global _cached, _reconnecting
    with _state_lock:
        if _cached is not None and failed_fs is not None and _cached[1] is not failed_fs:
            return _cached[1], _cached[2]
        stale = _cached[1] if _cached is not None else failed_fs
        _cached = None
        _reconnecting = True
    _discard(stale)
    return _connect(_key())


def _discard(fs) -> None:
    """Best-effort: drop fsspec's instance cache (otherwise the same dead
    object is handed straight back) and smbclient's session pool."""
    if fs is not None:
        try:
            type(fs).clear_instance_cache()
        except Exception:
            logger.debug("Could not clear fsspec instance cache", exc_info=True)
    try:
        import smbclient

        smbclient.reset_connection_cache()
    except Exception:
        logger.debug("Could not reset smbclient connection cache", exc_info=True)


def snapshot() -> dict:
    with _state_lock:
        steps = [dict(step) for step in _steps]
        elapsed = None
        if _status == "connecting" and _attempt_started is not None:
            elapsed = round(time.monotonic() - _attempt_started, 1)
        running = next((s for s in steps if s["status"] == "running"), None)
        if _status == "connecting":
            prefix = "Reconnecting: " if _reconnecting else ""
            message = prefix + (running["label"] if running else "Connecting to the photo server") + "…"
        elif _status == "error":
            message = "Could not reach the photo server"
        elif _status == "ready":
            message = "Connected"
        else:
            message = "Not connected yet"
        return {
            "status": _status,
            "message": message,
            "steps": steps,
            "error": _error,
            "elapsed_seconds": elapsed,
        }


def _is_smb() -> bool:
    return fsspec.core.split_protocol(get_photos_uri())[0] == "smb"


def _keepalive_once(check_session: bool = True) -> None:
    """Connect if not connected; otherwise (SMB only -- the session is what
    servers time out) ``stat`` the root and rebuild the connection if that
    fails."""
    with _state_lock:
        cached = _cached
    if cached is None or cached[0] != _key():
        get_fs_and_root()
        return
    if not check_session or not _is_smb():
        return
    _, fs, root = cached
    try:
        fs.info(root)
    except FileNotFoundError:
        return
    except Exception:
        logger.warning("Photos backend keepalive failed; reconnecting", exc_info=True)
        reconnect(fs)


def _run() -> None:
    interval = get_photos_keepalive_seconds()
    while not _stop_event.is_set():
        try:
            _keepalive_once(check_session=interval > 0)
        except Exception:
            pass  # already logged and recorded by _connect; retried next tick
        # Without a keepalive interval, still retry a failed connect.
        if _stop_event.wait(interval if interval > 0 else 60):
            return


def start() -> None:
    """Connect in the background and keep the connection alive. Idempotent;
    never raises and never blocks the caller."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_run, name="photos-backend-connection", daemon=True)
    _thread.start()


def stop() -> None:
    global _thread
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=2)
        _thread = None
