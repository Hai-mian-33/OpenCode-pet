"""A tiny OpenCode API client: one-shot GETs plus a reconnecting SSE reader.

The pet is a read-only observer.  It never writes to OpenCode, never mutates a
session and never asks for a permission decision - it only asks what is going
on and renders the answer.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterator, Optional

from . import paths
from .endpoint import Endpoint, discover, probe, read_password

# Reconnect backoff, seconds.  The stream is volatile by contract, so we never
# give up - we just back off and let the reconciler fill in whatever we missed.
BACKOFF_MIN = 0.5
BACKOFF_MAX = 8.0
HEARTBEAT_IDLE = 30.0


class OpenCodeClient:
    def __init__(self, endpoint: Optional[Endpoint] = None, timeout: float = 6.0):
        self.endpoint = endpoint
        self.timeout = timeout
        self.connected = False
        self.last_error = ""

    # -- endpoint management ---------------------------------------------
    @property
    def version(self) -> str:
        return self.endpoint.version if self.endpoint else ""

    def connect(self, cached: str = "") -> bool:
        """Attach to an endpoint, discovering one only if we have none.

        This used to always re-run discovery and overwrite ``self.endpoint``,
        which was invisible while there was a single OpenCode on the machine -
        discovery always found the same one - and fatal the moment there were
        two.  Every client would silently snap back to the *first* service
        discovered, so a client built for a remote workspace would read the
        local service's sessions instead of its own, and the remote
        conversation would never appear.  A client is a handle to *one*
        endpoint; it must not wander off to another.
        """
        if self.endpoint is None:
            endpoint, error = discover(cached=cached, password=read_password() or None)
            self.endpoint = endpoint
            self.last_error = error
        else:
            self.last_error = ""
            # A handle handed to us may be stale (the service restarted on a new
            # port).  Prove it still answers before claiming to be connected.
            if probe(self.endpoint.url, self.endpoint.password,
                     timeout=self.timeout) is None:
                self.connected = False
                self.last_error = "err.no_endpoint"
                return False
        self.connected = bool(self.endpoint)
        return self.connected

    # -- request helpers --------------------------------------------------
    def _request(self, path: str, params: Optional[Dict[str, Any]] = None,
                 method: str = "GET") -> Any:
        if not self.endpoint:
            raise RuntimeError("not connected")
        url = self.endpoint.url + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
        request = urllib.request.Request(url, method=method)
        request.add_header("Authorization", self.endpoint.auth_header())
        request.add_header("Accept", "application/json")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8", "replace")
        return json.loads(body) if body else None

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        payload = self._request(path, params)
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    # -- the calls the pet actually makes ---------------------------------
    def info(self) -> Dict[str, Any]:
        return self.get("/api/info") or {}

    def list_sessions(self, limit: int = 40) -> list:
        try:
            sessions = self.get("/api/session", {"limit": limit}) or []
        except (urllib.error.URLError, OSError, ValueError):
            return []
        return sessions if isinstance(sessions, list) else []

    def active_sessions(self) -> Dict[str, Dict[str, Any]]:
        """``{sessionID: {"type": "running"}}`` for sessions mid-turn."""
        try:
            data = self.get("/api/session/active") or {}
        except (urllib.error.URLError, OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def pending_permissions(self, session_id: str) -> list:
        try:
            data = self.get("/api/session/%s/permission" % urllib.parse.quote(session_id))
        except (urllib.error.URLError, OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    # -- event stream -----------------------------------------------------
    def events(self, stop: threading.Event) -> Iterator[Dict[str, Any]]:
        """Yield decoded SSE payloads, reconnecting forever.

        This never raises for network reasons: a dropped stream is normal and
        is handled by calling :meth:`reconnect` and resuming.  The caller keeps
        the last good snapshot, so a gap degrades to "slightly stale" instead
        of "broken".
        """
        if not self.endpoint:
            self.connect()
        backoff = BACKOFF_MIN
        last_data = time.time()
        while not stop.is_set():
            if not self.endpoint:
                if not self.connect():
                    _sleep(stop, backoff)
                    backoff = min(backoff * 2, BACKOFF_MAX)
                    continue
                backoff = BACKOFF_MIN
            try:
                for payload in self._stream_once(stop):
                    self.connected = True
                    last_data = time.time()
                    yield payload
                # A clean end-of-stream still means the socket is gone.
                self.connected = False
            except Exception as exc:                       # noqa: BLE001
                self.connected = False
                self.last_error = type(exc).__name__
                self.endpoint = None
            if stop.is_set():
                return
            if time.time() - last_data > HEARTBEAT_IDLE:
                # The server sends ": heartbeat" comments; no data for this
                # long means the socket is half-open.  Force a reconnect.
                self.endpoint = None
            _sleep(stop, backoff)
            backoff = min(backoff * 1.7, BACKOFF_MAX)

    def _stream_once(self, stop: threading.Event) -> Iterator[Dict[str, Any]]:
        assert self.endpoint is not None
        request = urllib.request.Request(self.endpoint.url + "/api/event", method="GET")
        request.add_header("Authorization", self.endpoint.auth_header())
        request.add_header("Accept", "text/event-stream")
        request.add_header("Cache-Control", "no-cache")
        response = urllib.request.urlopen(request, timeout=None)
        try:
            buffer = b""
            while not stop.is_set():
                chunk = response.read(1)
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > 8 * 1024 * 1024:
                    buffer = b""
                if not buffer.endswith(b"\n\n"):
                    continue
                for payload in _parse_sse_block(buffer.decode("utf-8", "replace")):
                    yield payload
                buffer = b""
        finally:
            try:
                response.close()
            except Exception:                              # noqa: BLE001
                pass

    def reconnect(self) -> None:
        self.endpoint = None


def _parse_sse_block(block: str):
    for line in block.splitlines():
        if not line.startswith("data:"):
            continue                                   # comments and fields
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("type"):
            yield payload


def _sleep(stop: threading.Event, seconds: float) -> None:
    stop.wait(seconds)


# ---------------------------------------------------------------------------
# focus / raise the desktop app
# ---------------------------------------------------------------------------
PET_WINDOW_TITLES = ("opencode pet", "opencode pet cards")


def window_title_score(title: str) -> int:
    """How likely a window titled ``title`` is the OpenCode desktop app.

    Most specific match wins.  The tiers exist because the previous version
    asked only ``"opencode" in title``, which also matches the pet's own
    windows ("OpenCode Pet", "OpenCode Pet cards") - a click on the pet then
    raised the pet, and looked like a dead button.

    Those two titles are now rejected outright rather than relying on the caller
    filtering them out by PID.  Scoring a pet window at 70 made correctness
    depend on a lookup happening to run inside the pet's own process, and any
    other caller - a helper script, a test, a future tool - would happily raise
    another desktop pet.  Defence in depth: the title itself says "not the app".

    Returns 0 for anything that is not plausibly the app.
    """
    title = (title or "").strip().lower()
    if not title:
        return 0
    if title in PET_WINDOW_TITLES or title.startswith(PET_WINDOW_TITLES):
        return 0
    if title in ("opencode", "open code"):
        return 100
    if title.startswith(("opencode -", "opencode —", "opencode –",
                         "opencode :")):
        return 90
    if title.startswith("opencode"):
        return 70
    if title.startswith("open code"):
        return 60
    if "opencode" in title:
        return 30
    return 0


def opencode_window_visible() -> Optional[bool]:
    """Is there an OpenCode window on screen right now?

    The process check is the wrong question for anything the pet *shows*.
    ``Popen`` returns the moment a process is created and Electron maps its
    window some 500 ms later, so "the process exists" says *open* while there is
    still nothing on screen; and on the way out the window disappears ~1.5 s
    *before* the process does.

    Returns ``None`` when the question could not be answered at all, so the
    caller can tell "no window" from "could not look".
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        own_pid = os.getpid()
    except (ImportError, AttributeError, OSError):             # noqa: BLE001
        return None

    found = [False]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        if found[0] or not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if window_title_score(buf.value) > 0:
            found[0] = True
            return False
        return True

    try:
        user32.EnumWindows(_enum, 0)
    except Exception:                                          # noqa: BLE001
        return None
    return found[0]


def window_ready(process_up: Optional[bool] = None) -> bool:
    """Should the pet show live work?

    The **window** decides, full stop.  There is deliberately no process
    fallback: one was tried, to survive a future window-title change, and it
    undid the whole fix.  During shutdown the window disappears ~1.5 s before
    ``OpenCode.exe`` does, so a fallback keyed on "the process has been up a
    while" reported *open* for that whole 1.5 s and kept the stale "waiting for
    authorization" card on screen - the exact bug, re-introduced through the
    back door.

    If the window cannot be examined at all, ``process_up`` is consulted - that
    is "we could not look", which is a different situation from "we looked and
    there is nothing there".
    """
    visible = opencode_window_visible()
    if visible is None:
        return bool(process_up)
    return visible


def focus_desktop(launch_if_missing: bool = True) -> bool:
    """Bring the OpenCode desktop app to the front.

    Raise the window that is already open, and otherwise launch the executable -
    Electron holds a single-instance lock, so a second launch focuses its
    primary window by itself.

    ``launch_if_missing=False`` is the raise-only attempt.  Callers that want to
    report a failure, and to do something else about it, need to be able to ask
    "is there a window?" separately from "start the app": with the default, both
    questions are answered by the same call and a failure is indistinguishable
    from a refusal to launch.
    """
    if _raise_existing_window():
        return True
    if not launch_if_missing:
        return False
    return _launch_exe()


def launch_desktop_and_wait(timeout: float = 12.0,
                            interval: float = 0.35) -> bool:
    """Start the desktop app, wait for its window, and raise it.

    Splitting "spawn the process" from "the user can see the app" is the whole
    point.  ``focus_desktop`` returned ``True`` the instant ``Popen`` succeeded,
    which says only that a process was created: a cold Electron start takes a
    second or two to map its window, so the caller was told "done" while nothing
    was on screen yet.

    This waits for a real window to appear and then raises it, so the return
    value means the user is looking at OpenCode.  A single-instance launch (the
    app is already running with its window hidden, which is exactly the
    reported situation) shows the window rather than starting a second copy, so
    polling for the window handles that case too.
    """
    if not _launch_exe():
        return False

    import time

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        # Check before sleeping so an already-visible window is raised at once.
        if _raise_existing_window():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.05, interval))


def _launch_exe() -> bool:
    """Spawn the desktop executable.  Reports only that the spawn was accepted."""
    exe = paths.desktop_exe()
    if not exe:
        return False
    import subprocess
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        subprocess.Popen([exe], creationflags=flags)
        return True
    except (OSError, ValueError):
        return False


def _raise_existing_window() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    user32 = ctypes.windll.user32
    own_pid = os.getpid()
    best = {"hwnd": None, "score": 0}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True                          # never raise the pet itself
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        points = window_title_score(buf.value)
        # Strictly positive, and strictly better than the incumbent.  Starting
        # the running best at -1 let a *zero* - meaning "definitely not
        # OpenCode" - win the comparison, so with the OpenCode window closed
        # the pet raised whatever unrelated window happened to be enumerated
        # first (another desktop pet, on this machine) and reported success.
        # The launch fallback then never ran, and clicking the pet did nothing
        # useful at all - the precise failure that was reported.
        if points > best["score"]:
            best["score"] = points
            best["hwnd"] = hwnd
        return True

    user32.EnumWindows(_enum, 0)
    if not best["hwnd"]:
        return False
    return _raise(best["hwnd"])


def _raise(hwnd) -> bool:
    """Put ``hwnd`` in front and confirm it actually got there.

    Total on purpose.  This is reached from a Qt slot, and **any exception that
    reaches a slot kills the event loop** - the pet then dies and the sentinel
    silently relaunches it, so the log shows a clean start and no error at all.
    That is not hypothetical here: this function used to call
    ``kernel32.AttachThreadInput``, which does not exist.  ``AttachThreadInput``
    is exported by **user32**; kernel32 has no such symbol, so the retry path
    raised ``AttributeError`` every single time Windows refused the first
    foreground change - i.e. exactly whenever the retry was needed.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except (ImportError, AttributeError, OSError):             # noqa: BLE001
        return False

    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)            # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() == hwnd:
            return True

        # Windows refuses foreground changes from a process that does not own the
        # current foreground window.  The trick is to borrow the rights of the
        # window that *is* in front, which is why the foreground window's own
        # thread is attached for the duration of the call.
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        our_thread = kernel32.GetCurrentThreadId()
        # user32, not kernel32 - see the docstring above.
        attach = getattr(user32, "AttachThreadInput", None)
        attached = False
        if attach is not None and fg_thread and fg_thread != our_thread:
            try:
                attached = bool(attach(our_thread, fg_thread, True))
            except (OSError, ValueError):                      # noqa: BLE001
                attached = False
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                try:
                    attach(our_thread, fg_thread, False)
                except (OSError, ValueError):                  # noqa: BLE001
                    pass
        return user32.GetForegroundWindow() == hwnd
    except Exception:                                          # noqa: BLE001
        # Never let a Win32 oddity escape into the Qt event loop.
        return False
