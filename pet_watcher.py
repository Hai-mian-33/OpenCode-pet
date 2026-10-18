#!/usr/bin/env python
"""A headless sign-in sentinel - the only thing that reacts instantly.

It imports **no** Qt and opens no window.  At sign-in it starts, polls for the
OpenCode desktop process, and:

* OpenCode opens  -> starts the pet
* OpenCode closes -> stops the pet

Splitting it this way is what makes the pet genuinely live and die with the
app.  Letting the pet itself hide and wait would leave a resident window
hanging around all week even when you never opened OpenCode.

    python pet_watcher.py             # run in the foreground
    python pet_watcher.py --status    # report state
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from petcore import endpoint as endpoint_mod
from petcore import i18n, paths
from petcore.liveness import AbsentConfirm, SleepWatch
from petcore.lock import SingleInstance, pid_alive
from petcore.lock import lock_path as single_instance_path

# The guard has to outlive ``acquire_single_instance`` so the lock is only
# released by the same process that claimed it.
_SENTINEL_GUARD: list = []

POLL_SECONDS = 1.5
LAUNCH_LOCK = 12.0          # seconds to wait after a launch before retrying
# A launch that produced no pet must be retried sooner than this, or a crashed
# pet leaves the desktop empty for twelve seconds.
RETRY_SECONDS = 2.5

def log(message: str) -> None:
    try:
        paths.ensure_dirs()
        with open(paths.watcher_log_file(), "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%H:%M:%S"), message))
    except OSError:
        pass


def arm_exit_tracing() -> None:
    """Leave a record of how this process ended.

    The sentinel is the single point of failure for the whole pet: if it dies,
    nothing restarts the pet until the next sign-in.  And on 2026-10-18 it
    *did* die, silently - it runs under ``pythonw.exe``, which discards stderr,
    so an uncaught exception had nowhere to go and the watcher log simply
    stopped.  That is an unactionable failure, so it cannot be how it happens
    again.

    These hooks cover the three ways this process can end that are otherwise
    invisible: an uncaught exception on the main thread, one on a worker
    thread, and a clean exit nobody logged.
    """
    def _report(what: str, text: str = "") -> None:
        log("%s%s" % (what, ("\n" + text) if text else ""))

    def excepthook(kind, value, tb) -> None:
        import traceback
        _report("UNCAUGHT EXCEPTION in the sentinel",
                "".join(traceback.format_exception(kind, value, tb)))

    def thread_excepthook(args) -> None:
        import traceback
        _report("UNCAUGHT EXCEPTION on thread %r"
                % (getattr(args.thread, "name"),),
                "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                   args.exc_traceback)))

    sys.excepthook = excepthook
    try:
        import threading
        threading.excepthook = thread_excepthook
    except Exception:                                      # noqa: BLE001
        pass
    import atexit
    atexit.register(lambda: _report("sentinel is exiting (atexit)"))


# ---------------------------------------------------------------------------
# single instance
# ---------------------------------------------------------------------------
LOCK_NAME = "sentinel.lock"

# The pet claims this; the sentinel reads it to know the pet is already up.
PET_LOCK_NAME = "pet.lock"


def lock_path() -> str:
    return single_instance_path(LOCK_NAME)


def acquire_single_instance() -> Optional[int]:
    """Claim the sentinel lock, or return the PID that already holds it.

    Startup can run the launcher more than once - a user double-clicking it,
    a restored session, a re-install - and two sentinels would fight over the
    same pet.
    """
    guard = SingleInstance(LOCK_NAME)
    guard.acquire()
    _SENTINEL_GUARD.append(guard)
    if guard.acquired:
        return None
    return guard.holder if guard.holder and guard.holder > 0 else -1


def pet_entry() -> str:
    return os.path.join(paths.APP_ROOT, "opencode_pet.py")


def _running_command_lines(needle: str, root: str = None):
    """Every live process whose command line contains ``needle``.

    ``root`` narrows the match to a process launched *from that application
    root* - see ``_owns``.  Without it a bare script-name match finds every copy
    of this project on the machine.
    """
    try:
        import psutil  # type: ignore
        found = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
            except Exception:                              # noqa: BLE001
                continue
            joined = " ".join(cmdline)
            if needle not in joined or "pet_watcher" in joined:
                continue
            if root is not None and not _started_from(cmdline, root):
                continue
            found.append((int(proc.info["pid"]), joined))
        return found
    except Exception:                                      # noqa: BLE001
        return []


def _started_from(cmdline, root: str) -> bool:
    """Whether ``cmdline`` launches a script that lives inside ``root``.

    The script path is the reliable signal here.  The *working directory* is not
    available through ``psutil.process_iter`` without another syscall per
    process, and a copy started by double-clicking has a bare
    ``opencode_pet.py`` in its arguments with no directory at all - so the path
    of the script being run is what has to be checked, on both the absolute and
    the drive-relative spellings Windows allows.
    """
    try:
        root_abs = os.path.normcase(os.path.abspath(root))
        for part in cmdline:
            if not part or not part.lower().endswith(".py"):
                continue
            try:
                absolute = os.path.normcase(os.path.abspath(part))
            except (OSError, ValueError):
                continue
            if absolute.startswith(root_abs + os.sep):
                return True
    except (OSError, ValueError):
        pass
    return False


def _owns(root: str, needle: str = None):
    """(pid, cmdline) for pets belonging to *this* installation only.

    This is the guard that keeps one copy of the project from meddling with
    another.  The single-instance lock already prevents two pets from running at
    once, but the sentinel still has to answer "is *my* pet running?" and the
    old code answered it with a machine-wide script-name match.  That is how a
    sentinel started from a development folder ended up killing the pet that was
    serving the installed copy: it found a process called ``opencode_pet.py``,
    could not tell whose it was, and terminated it.
    """
    needle = needle or os.path.basename(pet_entry())
    return _running_command_lines(needle, root=root)


def pet_running() -> bool:
    """Whether a pet already exists, by the pet's own lock rather than by name.

    Matching command lines is not enough: the pet can be started from a
    terminal, from a restored session or from this sentinel, and the argument
    list differs in every case (``opencode_pet.py`` relative or absolute,
    extra flags).  The lock is the pet's own declaration that it is up.
    """
    holder = _pet_lock_holder()
    if holder and holder != os.getpid() and pid_alive(holder):
        return True
    return bool(_owns(paths.APP_ROOT))


def _pet_lock_holder() -> int:
    """The PID recorded in the pet's lock, or 0 when there is none.

    Read directly rather than through ``SingleInstance``: the sentinel needs
    the pet's path, but claiming it would make the two processes collide.
    """
    from petcore.lock import lock_path as pet_lock_path

    path = pet_lock_path(PET_LOCK_NAME)
    return _lock_holder(path) if os.path.exists(path) else 0


def _lock_holder(path: str) -> int:
    try:
        with open(path, "r", encoding="ascii") as handle:
            return int((handle.read() or "0").strip() or 0)
    except (OSError, ValueError):
        return 0


def kill_pet() -> None:
    """Stop *this installation's* pet.

    The lock is authoritative because the command line varies with how the pet
    was started; the name match is kept for a pet whose lock was removed while
    it was still running.

    Both are scoped to ``APP_ROOT``.  An unscoped match would make this a
    machine-wide kill switch: any copy of the project could stop any other
    copy's pet, and a stale lock file left behind in one checkout would be
    enough to do it.
    """
    # ``SingleInstance`` here only computes the path; the sentinel never claims
    # the pet's lock, because that is the pet's to hold.
    holder = _pet_lock_holder()
    stopped = []
    if holder and holder != os.getpid() and pid_alive(holder):
        stopped.append(holder)
        _terminate(holder)

    for pid, _cmdline in _owns(paths.APP_ROOT):
        if pid not in stopped:
            _terminate(pid)
            stopped.append(pid)

    log("pet stopped by sentinel (%d process(es))" % len(stopped))


def _terminate(pid: int) -> None:
    try:
        import psutil  # type: ignore
        psutil.Process(pid).terminate()
        return
    except Exception:                                      # noqa: BLE001
        pass
    try:
        os.kill(pid, 15)
    except (OSError, AttributeError):
        pass


def launch_pet(language: str = "") -> bool:
    entry = pet_entry()
    if not os.path.isfile(entry):
        log("pet entry missing: %s" % entry)
        return False
    args = [paths.pythonw(), entry]
    if language:
        args += ["--lang", language]
    # Says "this start was not requested by the user".  It is how the pet tells
    # a background restart apart from somebody running it by hand, and therefore
    # whether a previous "stay closed" request still applies.  Without it the
    # user's own Exit is indistinguishable from a crash, and the pet comes back.
    args.append("--from-sentinel")
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        subprocess.Popen(args, cwd=paths.APP_ROOT, creationflags=flags,
                         close_fds=True)
    except (OSError, ValueError) as exc:
        log("launch failed: %r" % (exc,))
        return False
    log("pet launched")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pet_watcher.py",
        description="Start and stop OpenCode Pet with the OpenCode desktop app.")
    parser.add_argument("--lang", choices=i18n.available(), default=None)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--once", action="store_true",
                        help="check once, act, and exit")
    parser.add_argument("--always-show", action="store_true",
                        help="start the pet immediately and keep it up")
    args = parser.parse_args(argv)

    if args.status:
        from petcore.lock import lock_path as pet_lock_path

        running = endpoint_mod.desktop_running()
        holder = _lock_holder(lock_path()) if os.path.exists(lock_path()) else 0
        pet_path = pet_lock_path(PET_LOCK_NAME)
        pet_holder = (_lock_holder(pet_path) if os.path.exists(pet_path) else 0)
        pet_alive = bool(pet_holder and pet_holder != os.getpid()
                         and pid_alive(pet_holder))
        extra = max(0, len(_running_command_lines(
            os.path.basename(pet_entry()))) - (1 if pet_alive else 0))

        print("OpenCode Pet sentinel")
        print("  opencode desktop : %s" % ("running" if running else "stopped"))
        print("  pet              : %s%s"
              % ("running" if pet_alive else "stopped",
                 "" if not extra else "   *** %d DUPLICATE(S) ***" % extra))
        print("  sentinel pid     : %s"
              % (holder if holder and pid_alive(holder) else "none"))
        print("  app root         : %s" % paths.APP_ROOT)
        print("  log              : %s" % paths.watcher_log_file())
        return 1 if extra else 0

    paths.ensure_dirs()
    lang = args.lang or ""

    holder = acquire_single_instance()
    if holder is not None:
        if holder == os.getpid():                          # pragma: no cover
            return 0
        if holder > 0:
            log("another sentinel already owns the lock (pid %d); exiting" % holder)
        else:
            log("could not claim the sentinel lock; exiting")
        return 0

    log("sentinel started (pid %d)" % os.getpid())
    arm_exit_tracing()
    try:
        return _loop(args, lang)
    finally:
        release_single_instance()


def _loop(args, lang: str, clock=None) -> int:
    """Poll OpenCode and keep the pet in step with it.

    ``clock`` is the monotonic time source used for the sustained-absence
    grace period.  It is a parameter rather than a direct ``time.monotonic()``
    call so the tests can drive a whole shutdown in microseconds instead of
    sleeping through the real grace period.
    """
    clock = clock or time.monotonic
    if args.always_show and not pet_running():
        launch_pet(lang)

    app_up = False
    launched_at = 0.0
    gone = AbsentConfirm()
    sleep_watch = SleepWatch()
    stayed_off = False

    while True:
        # Nothing below may escape.  The sentinel is started **once**, at
        # sign-in, and nothing restarts it: an exception reaching this frame
        # would end the only process that can bring the pet back, leaving the
        # desktop empty until the next logon with nothing in any log to say why.
        # So each pass is isolated and a failing pass is logged and retried.
        try:
            # The user asked for the pet to stay closed.  Honour it, or Exit in
            # the tray menu means nothing: the sentinel would relaunch the pet
            # within a couple of seconds and the pet could never be shut off for
            # longer than a blink.  Checked here rather than only in the pet, so
            # this process also stops waiting for a pet that will never come.
            if paths.user_quit():
                if not stayed_off:
                    log("the user closed the pet; the sentinel will not start it")
                    stayed_off = True
            else:
                stayed_off = False

            # A suspend is not OpenCode closing.  This laptop sleeps five times
            # an hour, and just after a resume the process table cannot be
            # trusted: ``psutil.process_iter`` can come back empty while
            # OpenCode is very much alive.  Trusting that sample is what killed
            # the pet on 2026-10-18.  See ``petcore/liveness.py``.
            if sleep_watch.update():
                log("the machine was asleep; not trusting process scans for %.0fs"
                    % sleep_watch.settle)
            trust = not sleep_watch.settling

            # ``app_alive``, not ``desktop_running``: closing the OpenCode window
            # leaves ``opencode-cli serve --service`` running, and the sessions it
            # holds are still there.  Watching the window alone made the pet exit
            # while OpenCode itself was alive, taking away the only way back
            # into a closed app.
            alive = endpoint_mod.app_alive()
            window = endpoint_mod.desktop_running()

            # Absence has to be *sustained* before anything irreversible
            # happens.  This used to act on a single failed scan, and the log
            # recorded what that cost, in one second:
            #
            #     20:13:43 opencode detected (service only)
            #     20:13:43 opencode and its background service are both gone
            #
            # One empty process enumeration - a service restart, a plugin reload,
            # a ``psutil`` throw with an empty ``tasklist`` fallback - was enough
            # to stop the pet for the rest of the session.  See
            # ``petcore/liveness.py``.
            confirmed_gone = gone.update(alive, clock(), trust=trust)

            if confirmed_gone:
                if app_up:
                    app_up = False
                    log("opencode and its background service are gone "
                        "(confirmed after %.1fs)" % gone.grace)
                    kill_pet()
            elif alive and not stayed_off:
                if not app_up:
                    app_up = True
                    log("opencode detected%s"
                        % ("" if window else " (service only)"))
                # A blip that did not last is simply not acted on: no log line,
                # no teardown, and ``app_up`` is left exactly as it was.
                if not pet_running():
                    # Retry quickly if the last launch produced nothing, so a
                    # crash cannot leave the desktop empty for long.
                    waited = time.time() - launched_at
                    if waited > LAUNCH_LOCK or (launched_at
                                                and waited > RETRY_SECONDS):
                        launch_pet(lang)
                        launched_at = time.time()
            elif stayed_off and app_up:
                # OpenCode came back but the user closed the pet.  Say once, then
                # keep quiet - this is the state that must last until a human
                # asks for the pet again, not a transient condition to announce
                # on every poll.
                app_up = False

            if args.once:
                return 0
        except Exception as exc:                          # noqa: BLE001
            log("poll failed (%r); continuing" % (exc,))
            if args.once:
                return 1

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
