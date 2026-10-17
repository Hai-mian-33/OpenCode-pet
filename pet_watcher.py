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


def _running_command_lines(needle: str):
    try:
        import psutil  # type: ignore
        found = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
            except Exception:                              # noqa: BLE001
                continue
            joined = " ".join(cmdline)
            if needle in joined and "pet_watcher" not in joined:
                found.append((int(proc.info["pid"]), joined))
        return found
    except Exception:                                      # noqa: BLE001
        return []


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
    entry = os.path.basename(pet_entry())
    return bool(_running_command_lines(entry))


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
    """Stop the pet by its lock holder, falling back to a command-line match.

    The lock is authoritative because the command line varies with how the pet
    was started; the name match is kept for a pet whose lock was removed while
    it was still running.
    """
    # ``SingleInstance`` here only computes the path; the sentinel never claims
    # the pet's lock, because that is the pet's to hold.
    holder = _pet_lock_holder()
    stopped = []
    if holder and holder != os.getpid() and pid_alive(holder):
        stopped.append(holder)
        _terminate(holder)

    entry = os.path.basename(pet_entry())
    for pid, _cmdline in _running_command_lines(entry):
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
    try:
        return _loop(args, lang)
    finally:
        release_single_instance()


def _loop(args, lang: str) -> int:
    if args.always_show and not pet_running():
        launch_pet(lang)

    app_up = False
    launched_at = 0.0
    while True:
        # ``app_alive``, not ``desktop_running``: closing the OpenCode window
        # leaves ``opencode-cli serve --service`` running, and the sessions it
        # holds are still there.  Watching the window alone made the pet exit
        # while OpenCode itself was alive, taking away the only way back into a
        # closed app.  The pet now leaves with the background service, not with
        # the window.
        alive = endpoint_mod.app_alive()
        window = endpoint_mod.desktop_running()
        if alive and not app_up:
            app_up = True
            log("opencode detected%s" % ("" if window else " (service only)"))
            if not pet_running():
                launch_pet(lang)
                launched_at = time.time()
        elif not alive and app_up:
            app_up = False
            log("opencode and its background service are both gone")
            kill_pet()

        if alive and not pet_running():
            # Retry quickly if the last launch produced nothing, so a crash
            # cannot leave the desktop empty for long.
            waited = time.time() - launched_at
            if waited > LAUNCH_LOCK or (launched_at and waited > RETRY_SECONDS):
                launch_pet(lang)
                launched_at = time.time()

        if args.once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
