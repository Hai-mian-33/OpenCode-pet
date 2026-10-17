#!/usr/bin/env python
"""OpenCode Pet - a desktop pet for the OpenCode desktop app.

    python opencode_pet.py              # run the pet
    python opencode_pet.py --demo       # play one synthetic turn
    python opencode_pet.py --doctor     # print the resolved paths + endpoint
    python opencode_pet.py --selftest   # render every state to docs/images/

How it works
------------
The OpenCode desktop app already runs a local HTTP service with an SSE event
stream.  This process is a read-only observer of that stream:

    opencode-cli.exe serve --service
        |  GET /api/event            (live)
        |  GET /api/session          (reconcile)
        |  GET /api/session/active   (reconcile)
        |  GET /api/session/{id}/permission
        v
    petcore.reducer  -- pure, unit-tested
        v
    petui            -- the character, the window, the cards

The event stream is the fast path and the poll is the slow path; neither is
trusted alone.  Nothing here ever writes to OpenCode.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from petcore import __version__, bridge as bridge_mod, client as client_mod
from petcore import endpoint as endpoint_mod
from petcore import i18n, paths, reducer
from petcore.reducer import DONE, ERROR, IDLE, OFFLINE, WAITING
from petcore.settings import Settings

# ---------------------------------------------------------------------------
# logging - the desktop must stay clean, the file gets everything
# ---------------------------------------------------------------------------
_LOG_LOCK = threading.Lock()
_LOG_PATH: Optional[str] = None


def log(message: str) -> None:
    """Append one line to ``state/pet.log``.

    Milliseconds, not seconds.  Several bugs in this project were pure timing
    - a card that outlived a window closing by a second and a half - and with
    second-resolution stamps the log could not tell a race from a slow poll.
    Two events in the same second are different stories.
    """
    if not _LOG_PATH:
        return
    try:
        with _LOG_LOCK:
            with open(_LOG_PATH, "a", encoding="utf-8") as handle:
                stamp = time.strftime("%H:%M:%S")
                millis = int((time.time() % 1) * 1000)
                handle.write("%s.%03d %s\n" % (stamp, millis, message))
    except OSError:
        pass


def install_crash_handler() -> None:
    """Write a native crash - a Qt segfault, not a Python exception - to the log.

    The sentinel relaunches the pet whenever it disappears, so a hard crash
    looks exactly like a harmless restart.  Without this the log shows the new
    process starting and nothing about why the old one died.
    """
    import faulthandler
    try:
        handle = open(os.path.join(paths.state_dir(), "crash.log"), "a",
                      encoding="utf-8")
    except OSError:
        return
    faulthandler.enable(file=handle, all_threads=True)
    log("crash handler armed -> state\\crash.log")


def now_ms() -> int:
    return int(time.time() * 1000)


# How often the OpenCode window is checked for appearing or disappearing.  The
# window itself takes ~23 ms to go away after the user closes it (measured), so
# this interval is what the pet adds on top - which is why it is short.  The
# check is an EnumWindows pass, 0.6 ms, so the cost is negligible.
LIVENESS_INTERVAL_S = 0.02

# Routine (event-driven) refreshes are collapsed into at most one per this many
# seconds.  Below that a human cannot see the difference anyway, and the Qt event
# loop is left free to act on the things that are urgent - above all, hiding a
# card the moment the OpenCode window goes.
REFRESH_COALESCE_S = 0.12


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencode_pet.py", add_help=True,
        description="A bilingual desktop pet for the OpenCode desktop app.")
    parser.add_argument("--lang", choices=i18n.available(), default=None,
                        help="force the interface language")
    parser.add_argument("--scale", type=float, default=None,
                        help="start at this scale (0.5 - 2.0)")
    parser.add_argument("--demo", action="store_true",
                        help="play one synthetic turn so you can see every state")
    parser.add_argument("--selftest", action="store_true",
                        help="render every state to docs/images/ and exit")
    parser.add_argument("--no-server", action="store_true",
                        help="do not open the local HTTP event bridge")
    parser.add_argument("--port", type=int, default=0,
                        help="preferred event-bridge port (default 8767)")
    parser.add_argument("--no-titles", action="store_true",
                        help="show the project folder instead of session titles")
    parser.add_argument("--always-show", action="store_true",
                        help="stay up even when OpenCode is not running")
    parser.add_argument("--no-follow", action="store_true",
                        help="do not follow the OpenCode lifecycle")
    parser.add_argument("--doctor", action="store_true",
                        help="print resolved paths and the discovered endpoint")
    parser.add_argument("--allow-multiple", action="store_true",
                        help="do not take the single-instance lock (debugging)")
    parser.add_argument("--log-file", default=None)
    return parser


# The pet's own lock name.  ``pet_watcher`` reads this file to know whether
# the pet is already up, so the two files must agree.
PET_LOCK_NAME = "pet.lock"

_PET_GUARD = None


def claim_single_instance(verbose: bool = True) -> bool:
    """Claim the pet lock, or report the PID that already holds it.

    Two pets share the two frameless windows, the tray icon and the event
    bridge port, so a duplicate is not a degraded mode - it is two characters
    fighting over one corner of the desktop, and the loser of the port race
    just sits there looking hung.  The second copy therefore prints one line
    and exits.
    """
    global _PET_GUARD
    from petcore.lock import SingleInstance

    guard = SingleInstance(PET_LOCK_NAME)
    guard.acquire()
    _PET_GUARD = guard
    if guard.acquired:
        return True
    holder = guard.holder or 0
    if verbose:
        if holder > 0:
            print("OpenCode Pet is already running (pid %d); "
                  "not starting a second one." % holder)
        else:
            print("OpenCode Pet could not claim its single-instance lock; "
                  "another copy may be starting.")
    return False


def release_single_instance() -> None:
    global _PET_GUARD
    if _PET_GUARD is not None:
        _PET_GUARD.release()
        _PET_GUARD = None


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    paths.ensure_dirs()
    global _LOG_PATH
    _LOG_PATH = args.log_file or paths.log_file()

    # One pet, one set of windows.  A duplicate is refused before any Qt window
    # exists, so the loser never paints anything.
    if not args.allow_multiple and not claim_single_instance():
        return 0

    try:
        return _dispatch(args)
    finally:
        release_single_instance()


def _dispatch(args) -> int:
    install_crash_handler()

    if args.selftest:
        return run_selftest(args)
    if args.doctor:
        return run_doctor()

    settings = Settings()
    if args.lang:
        settings.set_lang(args.lang)
    if args.scale:
        settings.set("scale", max(0.5, min(2.0, args.scale)))
    if args.no_titles:
        settings.set("show_titles", False)
    if args.always_show or args.no_follow:
        settings.set("follow_app", False)

    return run_app(args, settings)


# ---------------------------------------------------------------------------
# --doctor / --selftest
# ---------------------------------------------------------------------------
def run_doctor() -> int:
    info = paths.describe()
    print("OpenCode Pet %s" % __version__)
    for key, value in info.items():
        print("  %-22s %s" % (key, value or "(not found)"))
    password = endpoint_mod.read_password()
    print("  %-22s %s" % ("service password", "found" if password else "MISSING"))
    found, error = endpoint_mod.discover()
    if found:
        print("  %-22s %s  pid=%s" % ("endpoint", found.url, found.pid))
        print("  %-22s %s" % ("server version", found.version))
    else:
        print("  %-22s (%s)" % ("endpoint", i18n.t(error)))
    log_hints = endpoint_mod.ports_from_log()
    if log_hints:
        print("  %-22s %s" % ("ports in opencode.log", log_hints))
    return 0 if found else 1


def run_selftest(args) -> int:
    """Render the documentation images, then exit.

    This used to carry its own copy of the render loop.  It drifted: it kept
    writing ``states.png`` with a hardcoded list of eight states, so ``closed``
    and ``offline`` never appeared and the flag quietly produced a different
    document from the one ``tools/gen_preview.py`` produced.  There is now one
    renderer, and the flag is a convenience name for it.
    """
    tools_dir = os.path.join(paths.app_root(), "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import gen_preview

    # Drop argv[0] (this script) and argv[1] (--selftest itself); the rest are
    # gen_preview's own options, so `--selftest --lang en` does what it reads like.
    return gen_preview.main(sys.argv[2:])


# ---------------------------------------------------------------------------
# the observer: two threads feeding one pure reducer
# ---------------------------------------------------------------------------
class Observer:
    """Reads OpenCode and owns the session fact set.

    Runs on its own threads and never touches Qt.  ``on_change`` is invoked
    with a snapshot dict whenever the reducer's output actually differs.
    """

    def __init__(self, settings: Settings, on_change, on_notice=None,
                 on_status=None):
        self.settings = settings
        self.on_change = on_change
        self.on_notice = on_notice
        self.on_status = on_status
        self.client = client_mod.OpenCodeClient()
        self.sessions: Dict[str, Dict] = {}
        self.external: Dict[str, Dict] = {}
        self.version = ""
        self.connected = False
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.RLock()
        self._dirty = False
        self._ack_restored = False
        # Whether the OpenCode window exists.  The service outlives it, and while
        # it is gone the pet must not report live work - see reducer.mood.
        self.app_open = True
        self._was_app_open = True
        self._live_checked_at = 0.0
        self._wake_poll = threading.Event()
        # Set by the UI: called (from the liveness thread) when the OpenCode
        # window appears or disappears.  It must be cheap and thread-safe.
        self.on_liveness = None

    def liveness(self, max_age_ms: int = 0) -> bool:
        """Is there an OpenCode window the user can click into?

        The check is the *window*, not the process.  A process check is wrong in
        both directions: ``Popen`` returns before Electron maps its window, so on
        launch it said "open" half a second before there was anything on screen,
        and on shutdown the window goes ~1.5 s before the process does.  A
        process fallback was tried here and it re-introduced the exact bug it was
        meant to guard against - see ``client.window_ready``.

        Cheap enough to run on a fast timer: an ``EnumWindows`` pass is 0.6 ms,
        and the process scan is skipped entirely unless the window could not be
        examined.  ``max_age_ms`` only exists for callers that want to share one
        answer across several reads.
        """
        now = time.time()
        if self._live_checked_at and (now - self._live_checked_at) * 1000.0 < max_age_ms:
            return self.app_open

        # Hot path: the window enumeration only - 0.6 ms.  The process scan
        # costs 2.2 ms and is only needed when the window could not be examined
        # at all, so it must not run on a timer that fires many times a second.
        visible = client_mod.opencode_window_visible()
        if visible is None:
            visible = bool(endpoint_mod.desktop_running())
        self.app_open = bool(visible)
        self._live_checked_at = now
        if self.app_open != self._was_app_open:
            log("OpenCode window %s (seen on %s)"
                % ("opened" if self.app_open else "closed",
                   threading.current_thread().name))
            self._was_app_open = self.app_open
            with self._lock:
                if self.app_open:
                    # Coming back is not evidence that anything is running.
                    # Re-read the API now instead of leaving the pet blank until
                    # the next poll tick, up to 2.5 s later.
                    self._wake_poll.set()
                else:
                    # The records in memory now describe an app that is gone.
                    dropped = reducer.invalidate_live(self.sessions)
                    if dropped:
                        log("dropped %d unverified session claim(s)" % dropped)
                    self._dirty = True
        return self.app_open

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self._threads = [
            threading.Thread(target=self._stream_loop, name="pet-events", daemon=True),
            threading.Thread(target=self._poll_loop, name="pet-poll", daemon=True),
            threading.Thread(target=self._liveness_loop, name="pet-liveness",
                             daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        log("observer started")

    def stop(self) -> None:
        self._stop.set()
        self._wake_poll.set()          # release the poll thread immediately
        self.client.reconnect()

    # -- threads ----------------------------------------------------------
    def _stream_loop(self) -> None:
        for event in self.client.events(self._stop):
            if self._stop.is_set():
                break
            self._apply(event)
        log("event stream ended")

    def _liveness_loop(self) -> None:
        """Watch the OpenCode window closely, and say so only when it changes.

        20 ms.  The window is gone ~23 ms after the user clicks its close button
        (measured), so this adds roughly nothing to what they see - which a Qt
        timer did not manage, because it competes with rendering for the event
        loop and came out late.

        Only a *change* is reported, so the Qt side is woken twice per close/open
        rather than fifty times a second.
        """
        last = self.liveness()
        while not self._stop.is_set():
            if self._stop.wait(LIVENESS_INTERVAL_S):
                break
            current = self.liveness()
            if current == last:
                continue
            last = current
            if self.on_liveness is None:
                continue
            try:
                self.on_liveness()
            except Exception as exc:                          # noqa: BLE001
                log("liveness callback raised %r" % (exc,))

    def _poll_loop(self) -> None:
        # The first pass happens immediately so the pet is correct even when
        # it starts in the middle of a running task.
        while not self._stop.is_set():
            # Clear *before* reconciling.  Clearing afterwards would swallow a
            # wake-up that arrived while the reconcile was running - exactly the
            # window reopening - and the pet would sit blank for a whole tick.
            self._wake_poll.clear()
            try:
                self._reconcile()
            except Exception as exc:                       # noqa: BLE001
                log("reconcile failed: %r" % (exc,))
            # Waiting on a wake event lets the window coming back pull the next
            # read forward.  ``stop()`` sets it too, so shutdown is not held up.
            self._wake_poll.wait(2.5)

    # -- folding ----------------------------------------------------------
    def _apply(self, event: Dict) -> None:
        kind = event.get("type") or ""
        with self._lock:
            if not self.client.connected:
                self.client.connected = True
            if kind == "server.connected":
                self.version = self._version_from_client()
                self._publish_status()
                return
            reducer.apply_event(self.sessions, event, now_ms())
            if kind in ("session.idle", "session.error", "session.tool.failed"):
                self._notice_locked(self.sessions.get(event.get("data", {}).get("sessionID")))
            self._dirty = True

    def _reconcile(self) -> None:
        # Liveness *first*, before the API calls below.  Those take long enough
        # (a permission probe per active session) that a window closed halfway
        # through would be paired with a liveness value read afterwards - i.e.
        # this pass would record "waiting for authorization" under a window that
        # had already gone.
        self.liveness(max_age_ms=0)
        cached = self.settings.get("endpoint") or ""
        if not self.client.connected or self.client.endpoint is None:
            if not self.client.connect(cached=cached):
                with self._lock:
                    if self.connected:
                        self.connected = False
                        self._publish_status()
                    self._mark_all_idle()
                self._stop.wait(3.0)
                return
            self.settings.set("endpoint", self.client.endpoint.url)
            self.version = self.client.version
            log("connected to %s (v%s, pid %s)" % (self.client.endpoint.url,
                                                  self.client.version,
                                                  self.client.endpoint.pid))
        with self._lock:
            was_connected = self.connected
            self.connected = self.client.connected
            if self.connected and not was_connected:
                self._publish_status()

            infos = self.client.list_sessions(limit=40)
            active = self.client.active_sessions()
            permissions = {}
            for session_id in list(active)[:12]:
                # Record every polled session, empty list included: that is how
                # the reducer learns an approval request was answered.
                permissions[session_id] = self.client.pending_permissions(session_id)
            reducer.reconcile(self.sessions, active, infos, permissions, now_ms())
            reducer.prune(self.sessions,
                          keep_ids=[s.get("id") for s in infos if isinstance(s, dict)])
            # Re-apply what has already been read.  Without this the session list
            # comes back from the API with acknowledged=False on every start, and
            # every finished task the user had already dismissed appears again.
            if not self._ack_restored:
                restored = reducer.restore_ack(self.sessions,
                                               self.settings.get("acked") or {})
                self._ack_restored = True
                if restored:
                    log("restored %d already-read task(s)" % restored)
            else:
                # ``reconcile`` re-derives state from the API on every poll and
                # resets the flag as it adopts each finished session.  Keeping
                # the memory reapplied means a task stays dismissed for as long
                # as the remembered stamp matches, instead of blinking back the
                # first time anything about it is re-read.
                reducer.restore_ack(self.sessions,
                                    self.settings.get("acked") or {})
            for record in self.sessions.values():
                self._notice_locked(record)
            self._dirty = True
            if not self.connected:
                self._mark_all_idle()
                self._publish_status()

    def _mark_all_idle(self) -> None:
        """No service, no claims: nothing may claim to be running."""
        for record in self.sessions.values():
            if record["running"] or record["state"] not in (IDLE, OFFLINE):
                record["state"] = OFFLINE
                record["running"] = False
                record["tool"] = ""
                record["permission"] = ""

    def _version_from_client(self) -> str:
        try:
            return str(self.client.info().get("version") or "")
        except Exception:                                  # noqa: BLE001
            return self.client.version

    # -- notifications ----------------------------------------------------
    def _notice_locked(self, record: Optional[Dict]) -> None:
        if record is None or not self.on_notice:
            return
        if self.settings.get("silent"):
            return
        if record.get("acknowledged") and record.get("state") != reducer.WAITING:
            # Already read, including a mark restored from memory after a
            # restart.  Without this the toast re-fires every time the session
            # list is re-read from the API, so the same finished task announced
            # itself again on every wake-up even with its card hidden.
            return
        key = reducer.new_notice(self.sessions, record)
        if not key:
            return
        self.on_notice(key, record)

    def _publish_status(self) -> None:
        if self.on_status:
            self.on_status(self.connected, self.version)

    # -- bridge injection -------------------------------------------------
    def inject(self, payload: Dict) -> str:
        """Add or update a session pushed by the local HTTP bridge."""
        session_id = str(payload.get("session") or "").strip() or \
            "external-%d" % int(time.time())
        state = str(payload.get("state") or IDLE).lower()
        with self._lock:
            record = self.external.get(session_id)
            if record is None:
                record = reducer.new_session(session_id, now_ms())
                record["started"] = int(payload.get("started") or now_ms())
                self.external[session_id] = record
            record["state"] = state
            record["running"] = state in reducer.RUNNING_STATES
            record["title"] = str(payload.get("task") or "")
            record["project"] = str(payload.get("project") or "") or "bridge"
            record["directory"] = record["project"]
            record["tool"] = str(payload.get("tool") or "")
            record["steps"] = int(payload.get("steps") or record.get("steps") or 0)
            record["errors"] = int(payload.get("errors") or 0)
            record["updated"] = now_ms()
            if state in (DONE, ERROR):
                record["finished"] = record["updated"]
                record["acknowledged"] = False
            elif state in reducer.RUNNING_STATES:
                record["finished"] = 0
                record["acknowledged"] = False
            self._notice_locked(record)
            self._dirty = True
        return session_id

    # -- snapshot ---------------------------------------------------------
    def snapshot(self) -> Dict:
        """A consistent view for the UI, rebuilt only when something moved."""
        with self._lock:
            if not self._dirty:
                return {"changed": False, "sessions": {}, "connected":
                        self.connected, "version": self.version,
                        "app_open": self.app_open}
            merged = dict(self.sessions)
            merged.update(self.external)
            self._dirty = False
            return {"changed": True, "sessions": merged, "connected":
                    self.connected, "version": self.version,
                    "app_open": self.app_open}

    def acknowledge(self, session_id: str) -> bool:
        with self._lock:
            target = self.external if session_id in self.external else self.sessions
            ok = reducer.acknowledge(target, session_id)
            if ok:
                self._remember_ack_locked(target.get(session_id) or {})
            self._dirty = True
            return ok

    def _remember_ack_locked(self, record: Dict) -> None:
        """Write the "already read" mark to disk so a restart will honour it.

        Without this the acknowledgement only ever existed in memory, so the pet
        forgot everything it had shown the user the moment it was restarted -
        which, with a pet that wakes up whenever OpenCode starts, meant the same
        dismissed tasks came back on screen over and over.
        """
        session_id = str(record.get("id") or "")
        if not session_id:
            return
        acked = self.settings.get("acked")
        if not isinstance(acked, dict):
            acked = {}
        reducer.remember_ack(acked, session_id,
                             reducer.ack_key(record) or now_ms())
        self.settings.set("acked", acked)

    def touch(self) -> None:
        """Force the next snapshot to be rebuilt (used after user actions)."""
        with self._lock:
            self._dirty = True


# ---------------------------------------------------------------------------
# the Qt application
# ---------------------------------------------------------------------------
def run_app(args, settings: Settings) -> int:
    from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot
    from PyQt5.QtWidgets import QApplication, QMessageBox

    from petui.cards import CardStack
    from petui.petwindow import PetWindow
    from petui.tray import Tray

    class Bridge(QObject):
        """Marshals worker-thread callbacks onto the Qt thread."""
        changed = pyqtSignal()
        notice = pyqtSignal(str, object)
        status = pyqtSignal(bool, str)
        # Emitted by the click-to-launch worker once it has finished waiting for
        # the OpenCode window.  Carries whether it worked, so the toast can be
        # raised from the Qt thread instead of from a background one.
        launched = pyqtSignal(bool)

    class WindowWatch(QObject):
        """Carries the liveness change from the watcher thread to this one.

        Its own class because it needs a signal of its own: the connection is
        explicitly queued, so the work happens on the next event-loop turn
        rather than on whichever thread noticed.
        """
        changed = pyqtSignal()

    app = QApplication(sys.argv[:1])
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(paths.APP_NAME)
    app.setApplicationVersion(__version__)

    bridge = Bridge()
    window = PetWindow(scale=float(settings["scale"]),
                       topmost=bool(settings["topmost"]))
    cards = CardStack()

    # ---- mutable state, declared before any handler closes over it --------
    expanded = bool(settings["expanded"])
    records: List[Dict] = []
    mood = {"state": IDLE, "session": None}
    last_state = ""
    last_tray_state = ""
    last_tray_tip = ""
    observer = None
    event_bridge = None
    tray = None
    quit_requested = False
    app_seen = False
    last_app_open = True
    refresh_at = [0.0]
    demo_stop = threading.Event()

    # ---- handlers --------------------------------------------------------
    _launching = threading.Event()

    def open_desktop() -> None:
        """Bring OpenCode to the front, launching it when its window is gone.

        This is the path that matters most once the desktop window has been
        closed: the service is still alive, so the pet stays on screen, and the
        pet has to be the thing that brings the window back.  Three real bugs
        made that not happen:

        * ``focus_desktop`` (no argument) already *includes* the launch step, so
          the old code's second "fallback" call was byte-for-byte the same call
          as the first.  When the first returned False, the second repeated the
          failure and the user got nothing.  The raise-only attempt and the
          launch are now two genuinely different calls.
        * A launched Electron app takes a second or two to map its window, so the
          launch is not treated as instant success: the pet waits for a window
          and then raises it, and says so honestly when it cannot.
        * That wait must not happen on the Qt thread.  ``open_desktop`` runs in a
          slot, and a blocking wait there freezes the whole pet - no repaint, no
          dragging - for as long as the wait lasts.  The slow half therefore runs
          on a worker; ``raise`` only needs Win32 calls, not the event loop.
        """
        # Fast path: a window is already there.  Milliseconds, safe on this thread.
        if client_mod.focus_desktop(launch_if_missing=False):
            log("raised the OpenCode window")
            return

        # Slow path: the desktop was closed.  Cold-start it on a worker so the
        # pet stays responsive, and ignore repeat clicks while one is in flight.
        if _launching.is_set():
            log("a launch is already in progress; ignoring the extra click")
            return
        log("no OpenCode window found; launching the desktop app")

        def worker() -> None:
            try:
                ok = client_mod.launch_desktop_and_wait()
            except Exception as exc:                          # noqa: BLE001
                log("launching OpenCode raised %r" % (exc,))
                ok = False
            finally:
                _launching.clear()
            log("OpenCode launched and raised" if ok
                else "could not open the OpenCode desktop app")
            # ``launched`` is a Qt signal, so this hops back onto the Qt thread.
            bridge.launched.emit(bool(ok))

        _launching.set()
        threading.Thread(target=worker, name="pet-launch", daemon=True).start()

    def on_launch_finished(ok: bool) -> None:
        if not ok:
            tray.notify(i18n.t("app.name"), i18n.t("err.cannot_open_desktop"),
                        warning=True)

    def persist_pos() -> None:
        point = window.pos()
        settings.update({"x": point.x(), "y": point.y()}, save=False)
        settings.save()

    def set_lang(lang: str) -> None:
        settings.set_lang(lang)
        cards.update()
        window.update()
        refresh()
        tray.notify(i18n.t("app.name"), i18n.language_name())

    def set_scale(value: float) -> None:
        window.set_scale(value)
        settings.set("scale", window.scale, save=False)
        tray.scale = window.scale
        persist_pos()

    def toggle_topmost() -> None:
        value = not window.topmost
        window.set_topmost(value)
        settings.set("topmost", value)

    def toggle_titles() -> None:
        settings.set("show_titles", not bool(settings["show_titles"]))
        observer.touch()
        refresh()

    def toggle_silent() -> None:
        settings.set("silent", not bool(settings["silent"]))

    def toggle_expand() -> None:
        nonlocal expanded
        expanded = not expanded
        settings.set("expanded", expanded)
        refresh()

    def reset_pos() -> None:
        window.place_default()
        window.clamp_to_screen()
        persist_pos()

    def show_about() -> None:
        box = QMessageBox(window)
        box.setWindowTitle(i18n.t("about.title"))
        box.setText(i18n.t("about.body", version=__version__))
        box.setIcon(QMessageBox.Information)
        box.exec_()

    def quit_app() -> None:
        nonlocal quit_requested
        if quit_requested:
            return
        quit_requested = True
        log("quitting")
        if observer is not None:
            observer.stop()
        if event_bridge is not None:
            event_bridge.stop()
        demo_stop.set()
        persist_pos()
        settings.save()
        cards.hide()
        if tray is not None:
            tray.hide()
        window.hide()
        app.quit()

    # ---- demo ------------------------------------------------------------
    def start_demo() -> None:
        if demo_stop.is_set():
            return
        demo_stop.clear()
        threading.Thread(target=_demo_script, name="pet-demo", daemon=True).start()

    def _demo_script() -> None:
        """A synthetic turn, so every state can be seen without a real task."""
        script = [
            (IDLE, 2.0, "", "opencode-pet"),
            ("thinking", 3.5, "", "opencode-pet"),
            ("executing", 4.0, "shell", "opencode-pet"),
            (WAITING, 3.5, "", "opencode-pet"),
            ("executing", 3.0, "edit", "opencode-pet"),
            ("longtask", 3.0, "grep", "opencode-pet"),
            (DONE, 4.0, "", "opencode-pet"),
            (IDLE, 1.5, "", "opencode-pet"),
        ]
        session_id = "demo-session"
        title = i18n.t("card.untitled")
        for state, seconds, tool, project in script:
            if demo_stop.wait(0):
                return
            now = now_ms()
            with observer._lock:
                record = observer.external.get(session_id)
                if record is None:
                    record = reducer.new_session(session_id, now)
                    observer.external[session_id] = record
                record.update({"state": state, "tool": tool, "title": title,
                               "project": project, "directory": project,
                               "running": state in reducer.RUNNING_STATES,
                               "updated": now, "started": now, "steps": 3,
                               "acknowledged": False, "permission": ""})
                if state in (DONE, ERROR):
                    record["finished"] = now
                observer._dirty = True
            time.sleep(seconds)
        demo_stop.set()

    # ---- render ----------------------------------------------------------
    def decorate(record: Dict, show_titles: bool) -> Dict:
        out = dict(record)
        out["label"] = reducer.session_label(record, show_titles) or \
            i18n.t("card.untitled")
        return out

    def position_cards(below: bool = False) -> None:
        """Park the card stack centred directly above the pet.

        Not beside it: the pet is almost always in a corner, and "above" is the
        one direction with free screen in all four of them, so the cards never
        end up jammed against an edge or overlapping the taskbar.

        Horizontally the card is centred on the pet and only clamped when the
        screen edge forbids it.  Aligning their edges instead - which is what
        this used to do - left 136 px of card hanging out on one side, because
        the card is twice as wide as the pet, and that reads as "upper-left".

        Showing is done here rather than in ``place``: the widget has no visible
        geometry until Qt has laid it out, and hiding an unshown window is a
        no-op, so a first pass before the layout would leave the stack stuck
        invisible while the log cheerfully reported cards on screen.
        """
        if not records:
            cards.hide()
            return

        pet = window.geometry()
        screen = window.available_rect()
        cards.place(pet, screen, prefer_below=below)
        if not cards.isVisible():
            cards.show()
            # ``show`` can settle the window on a default position, so the
            # geometry has to be applied once more after it becomes visible.
            cards.place(pet, screen, prefer_below=below)

    def refresh(urgent: bool = False) -> None:
        nonlocal last_state, last_tray_state, last_tray_tip, mood, records
        nonlocal last_app_open
        # A busy task produces a steady stream of SSE events, and each one used
        # to rebuild the cards and re-render the pet.  That work queued ahead of
        # the liveness change, so hiding a stale card after the user closed
        # OpenCode was delayed by traffic that was not urgent.  Routine refreshes
        # are coalesced; the timer and the liveness watcher come in as urgent and
        # never wait.
        if not urgent:
            stamp = time.monotonic()
            if stamp - refresh_at[0] < REFRESH_COALESCE_S:
                return
            refresh_at[0] = stamp

        app_open_now = observer.liveness()

        if not app_open_now:
            # Applied unconditionally, *before* the "nothing changed" early
            # return below.  A closed window is a fact about the world, not a
            # rendering of session data, so it must not depend on a dirty flag
            # another path may already have consumed - which is what let the
            # "waiting for authorization" card survive a close for over a
            # second, intermittently, on a path that measured as 50 ms.
            if records:
                log("hiding live cards (%d were showing)" % len(records))
                records = []
                cards.set_records([], expanded, bool(settings["show_titles"]))
                cards.hide()
            last_app_open = False
            if last_state != reducer.CLOSED:
                last_state = reducer.CLOSED
                window.set_state(reducer.CLOSED)
                tray.scale = window.scale
                tray.set_icon(window.grab_pixmap(64))
                last_tray_state = reducer.CLOSED
                tray.set_tooltip(i18n.t("about.tray_text",
                                        state=i18n.t("state.closed")))
                last_tray_tip = i18n.t("hover.app_closed")
                window.set_hover_tip(last_tray_tip)
            return

        if app_open_now != last_app_open:
            # Coming back has to force a rebuild: the records in memory are the
            # ones the closing window left behind.
            last_app_open = app_open_now
            observer.touch()

        snapshot = observer.snapshot()
        if not snapshot["changed"]:
            return
        sessions = snapshot["sessions"]
        moment = now_ms()
        app_open = app_open_now
        mood = reducer.mood(sessions, moment, dragging=window.dragging,
                            app_open=app_open)
        records = [decorate(r, bool(settings["show_titles"]))
                   for r in reducer.visible(sessions, moment, app_open=app_open)]

        state = mood["state"]
        if not snapshot["connected"] and state == IDLE:
            state = OFFLINE
        if state != last_state:
            log_mood(last_state, state, mood.get("session"), sessions,
                     len(records), bool(settings["show_titles"]))
            last_state = state
            window.set_state(state)
            # The tray icon is a full render plus a shell_NotifyIcon call, and
            # ``refresh`` runs on every event while a task is busy.  Doing that
            # unconditionally starved the 50 ms liveness watcher - the Qt loop
            # could not service it promptly, and the tail on "how long does a
            # stale card survive a close" went to 273 ms.  It only needs to change
            # when the character does.
            tray.scale = window.scale
            tray.set_icon(window.grab_pixmap(64))
        else:
            window.set_state(state)

        label = i18n.t("state." + state)
        if state != last_tray_state:
            last_tray_state = state
            tray.set_tooltip(i18n.t("about.tray_text", state=label))
        tip = (i18n.t("hover.connected", version=snapshot["version"])
               if snapshot["connected"] else i18n.t("hover.offline"))
        if snapshot["connected"] and not app_open:
            # Connected to a service whose window is gone: say so on hover, or
            # the calm face looks like the pet has simply gone quiet.
            tip = i18n.t("hover.app_closed")
        if tip != last_tray_tip:
            last_tray_tip = tip
            window.set_hover_tip(tip)

        cards.set_records(records, expanded, bool(settings["show_titles"]))
        position_cards()

    def log_mood(previous: str, state: str, record: Optional[Dict],
                 sessions: Dict, count: int, show_titles: bool) -> None:
        detail = ""
        if record:
            detail = " [%s %s]" % (
                reducer.session_label(record, show_titles)[:28],
                record.get("tool") or "-")
        running = sum(1 for r in sessions.values() if r["running"])
        log("mood %s -> %s%s  (running=%d cards=%d)"
            % (previous or "-", state, detail, running, count))

    def on_card_clicked(session_id: str) -> None:
        # Raise OpenCode *first*.  Acknowledging removes the card, and if that
        # happened first the window that owned the click would already be on
        # its way out - the raise then has no foreground rights left to work
        # with, which is why clicking a card did nothing.
        log("card clicked: %s" % session_id)
        open_desktop()
        observer.acknowledge(session_id)
        observer.touch()
        refresh()

    def on_notice(key: str, record: Dict) -> None:
        project = record.get("project") or i18n.t("app.name")
        if key == "notif.waiting_title":
            body = i18n.t("notif.waiting_body", project=project,
                          action=record.get("permission") or "")
            tray.notify(i18n.t(key), body, warning=True)
        elif key == "notif.done_title":
            body = i18n.t("notif.done_body", project=project,
                          steps=i18n.t("card.steps", n=int(record.get("steps") or 0)))
            tray.notify(i18n.t(key), body)
        else:
            tray.notify(i18n.t(key),
                        i18n.t("notif.error_body", project=project), warning=True)

    def lifecycle() -> None:
        nonlocal app_seen
        if not settings.get("follow_app"):
            return
        # Watch OpenCode as a whole, not just its window.  Closing the window
        # leaves ``opencode-cli serve --service`` running with every session
        # intact, and the pet is then the only way back into the closed app -
        # quitting here threw that away for no reason.
        alive = endpoint_mod.app_alive()   # one scan; both names, not two calls
        if alive and not app_seen:
            app_seen = True
            log("OpenCode detected; pet is up")
        elif not alive and app_seen:
            log("OpenCode and its background service are both gone: %s"
                % i18n.t("cli.daemon_exit"))
            quit_app()

    def tray_handlers() -> Dict:
        """Read by the tray every time the menu opens, so it is never stale."""
        return {
            "open": open_desktop,
            "quit": quit_app,
            "about": show_about,
            "demo": start_demo,
            "reset_pos": reset_pos,
            "toggle_expand": toggle_expand,
            "toggle_topmost": toggle_topmost,
            "toggle_titles": toggle_titles,
            "toggle_silent": toggle_silent,
            "set_scale": set_scale,
            "set_lang": set_lang,
            "has_cards": lambda: bool(records),
            "expanded": lambda: expanded,
            "topmost": lambda: window.topmost,
            "show_titles": lambda: bool(settings["show_titles"]),
            "silent": lambda: bool(settings["silent"]),
            "lang": lambda: settings.lang,
        }

    # ---- wiring ----------------------------------------------------------
    tray = Tray(tray_handlers(), scale=window.scale, parent=app)

    observer = Observer(settings,
                        on_change=lambda: bridge.changed.emit(),
                        on_notice=lambda key, rec: bridge.notice.emit(key, rec),
                        on_status=lambda on, ver: bridge.status.emit(on, ver))
    def _refresh_and_place() -> None:
        """Refresh, then re-anchor the cards to the pet's current position.

        ``refresh`` only rebuilds when the observer reports a change, but the
        cards must follow the pet even when the session data has not moved -
        after a drag, a scale or an edge snap.  Urgent: this is also what picks
        up any refresh the coalescing dropped.
        """
        refresh(urgent=True)
        if records:
            position_cards()

    bridge.changed.connect(refresh)
    bridge.notice.connect(on_notice)
    bridge.status.connect(lambda *_: observer.touch())
    bridge.launched.connect(on_launch_finished)

    if not args.no_server:
        event_bridge = bridge_mod.EventBridge(
            on_event=observer.inject,
            port=int(args.port or settings.get("bridge_port") or 0))
        if event_bridge.start():
            settings.set("bridge_port", event_bridge.port)
            log("event bridge on 127.0.0.1:%d" % event_bridge.port)

    def on_pet_clicked() -> None:
        log("pet clicked")
        open_desktop()
        observer.touch()
        refresh()

    def on_pet_scaled(value: float) -> None:
        settings.set("scale", value, save=False)
        tray.scale = value
        persist_pos()

    # One physical click must produce one raise.  The pet is shown without
    # activating, so Qt can deliver the release both as the polled-cursor path
    # and as a native event; without this guard a single click raises twice and
    # the second raise has already lost its foreground rights.
    _last_click = [0.0]

    def once_per_click(slot) -> Callable[..., None]:
        """Wrap ``slot`` so repeat signals inside 0.4 s are dropped.

        The wrapper forwards *args, because the pet click takes none and the
        card click takes a session id - an earlier version dropped them and
        every card click raised ``TypeError``, which killed the process from
        inside the event loop.
        """
        def guarded(*args, **kwargs) -> None:
            now = time.time()
            if now - _last_click[0] < 0.4:
                return
            _last_click[0] = now
            slot(*args, **kwargs)
        return guarded

    window.clicked.connect(once_per_click(on_pet_clicked))
    window.double_clicked.connect(toggle_expand)
    window.menu_requested.connect(lambda at: tray.popup(at))
    window.scrolled.connect(on_pet_scaled)
    window.scale_changed.connect(lambda value: setattr(tray, "scale", value))
    window.moved.connect(persist_pos)
    # Any movement has to re-place the cards: they are anchored to the pet, so
    # dragging without this leaves them behind in the old corner.
    window.moved.connect(lambda: position_cards())
    window.scaled_moved.connect(lambda: position_cards())
    cards.card_clicked.connect(once_per_click(on_card_clicked))
    cards.expand_toggled.connect(lambda _value: toggle_expand())

    window.restore_pos(int(settings["x"]), int(settings["y"]))
    window.show()
    # The icon must exist before the tray becomes visible, or Windows warns.
    tray.set_icon(window.grab_pixmap(64))
    tray.set_tooltip(i18n.t("about.tray_text", state=i18n.t("state.idle")))
    tray.show()

    # Geometry is not final until the window is on screen: before that Qt has
    # no real geometry and screen() is meaningless, so a first refresh would
    # place the cards against a default rect and leave them stranded there.
    QTimer.singleShot(0, lambda: (position_cards(), refresh()))

    # Repositioning only helps if something triggers it: the cards are anchored
    # to the pet, and nothing else moves the pet.  The idle tick is the natural
    # moment, so the stack follows every drag, scale and edge snap without a
    # signal for each.
    refresh_timer = QTimer()
    refresh_timer.timeout.connect(_refresh_and_place)
    refresh_timer.start(500)

    # A dedicated watcher for one thing only: did the OpenCode window just
    # appear or disappear?  It exists because "how long does a stale card stay up
    # after the user closes OpenCode" must not depend on whatever else happens to
    # call ``refresh`` - a burst of SSE events was masking it, and then not, which
    # is exactly the kind of timing that reproduces as "sometimes it flashes".
    #
    # It runs on its own thread rather than a Qt timer: a timer competes with
    # rendering for the event loop and was measurably late (a 273 ms tail).  On a
    # thread the reaction is bounded only by the poll interval - 20 ms, about the
    # same as the ~23 ms the OpenCode window itself takes to disappear, so the pet
    # adds almost nothing to what the user sees.
    #
    # A WinEvent hook was tried first and crash-looped the pet: a ctypes callback
    # that re-enters Python from inside the Win32 message pump corrupts the stack.
    # Polling a cheap predicate is not glamorous, but it works.
    window_watch = WindowWatch()

    @pyqtSlot()
    def on_window_event() -> None:
        # Urgent: this is the path that has to hide a stale card the instant the
        # OpenCode window goes away, and it must not queue behind routine work.
        if observer.liveness() != last_app_open:
            refresh(urgent=True)

    window_watch.changed.connect(on_window_event, Qt.QueuedConnection)
    observer.on_liveness = window_watch.changed.emit

    life_timer = QTimer()
    life_timer.timeout.connect(lifecycle)
    life_timer.start(2000)
    lifecycle()

    observer.start()
    if args.demo:
        start_demo()

    app.aboutToQuit.connect(observer.stop)

    try:
        return app.exec_()
    finally:
        observer.stop()
        if event_bridge is not None:
            event_bridge.stop()
        log("pet stopped")


if __name__ == "__main__":
    sys.exit(main())
