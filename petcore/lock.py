"""A single-instance lock shared by the pet and the sentinel.

Both processes must be exclusive, and for the same reason: they own the same
two windows, the same tray icon and the same event-bridge port.  Two copies is
not a degraded mode, it is two pets fighting over one corner of the desktop -
and because the second one cannot bind the bridge port it just fails silently
and looks like a hang.

The claim is atomic (``O_CREAT | O_EXCL``) so two simultaneous starts cannot
both win, and a lock left behind by a crashed process is reclaimed once its
PID is gone.  Each caller passes its own lock name, because the pet and the
sentinel are allowed to coexist: the sentinel is what starts the pet.
"""

from __future__ import annotations

import errno
import os
from typing import Optional

from . import paths


def lock_path(name: str) -> str:
    return os.path.join(paths.state_dir(), name)


def _read_pid(path: str) -> int:
    try:
        with open(path, "r", encoding="ascii") as handle:
            return int((handle.read() or "0").strip() or 0)
    except (OSError, ValueError):
        return 0


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` still exists.  Never raises."""
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore
        return psutil.pid_exists(pid)
    except Exception:                                      # noqa: BLE001
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True                                    # exists, not ours
        return True
    except AttributeError:                                 # Windows, no os.kill(0)
        return _pid_alive_windows(pid)


def _pid_alive_windows(pid: int) -> bool:
    try:
        import ctypes
    except ImportError:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return True
    finally:
        kernel32.CloseHandle(handle)


class SingleInstance:
    """Context manager around the lock.

    ``acquired`` is False when another live process already holds it.  Nothing
    is raised: "somebody else is already running" is an ordinary outcome, and
    the caller decides what to say about it.
    """

    def __init__(self, name: str) -> None:
        self.path = lock_path(name)
        self.holder: Optional[int] = None
        self.acquired = False
        self._owned = False

    def acquire(self, attempts: int = 3) -> bool:
        os.makedirs(paths.state_dir(), exist_ok=True)
        for _ in range(max(1, attempts)):
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = _read_pid(self.path)
                # Our own PID counts as live: a lock naming this process was
                # claimed by this process, so a second claim is a duplicate.
                # (PID reuse by an unrelated process is the safe direction - we
                # would refuse to start rather than start twice.)
                if holder == os.getpid() or (holder and pid_alive(holder)):
                    self.holder = holder
                    self.acquired = False
                    return False
                # A dead predecessor's lock: reclaim it.
                try:
                    os.unlink(self.path)
                except OSError:
                    self.holder = -1
                    self.acquired = False
                    return False
                continue
            except OSError:
                # Cannot lock at all (read-only install): run anyway rather
                # than refuse to start, and say so via acquired=True.
                self.acquired = True
                return True

            try:
                os.write(handle, str(os.getpid()).encode("ascii"))
            finally:
                os.close(handle)
            self.acquired = True
            self._owned = True
            return True

        self.acquired = False
        return False

    def release(self) -> None:
        """Drop the lock, but only if we are the one holding it."""
        if not self._owned:
            return
        holder = _read_pid(self.path)
        if holder and holder != os.getpid():
            return
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self._owned = False

    def __enter__(self) -> "SingleInstance":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> bool:
        self.release()
        return False
