#!/usr/bin/env python
"""Install, uninstall and inspect OpenCode Pet.

The pet is a portable folder.  Installing copies it into OpenCode's own config
directory (``~/.config/opencode/pet``) and drops one hidden launcher into the
Windows Startup folder.  Uninstalling removes both and nothing else - there is
no registry key, no service, no code injected into OpenCode.

    python install.py install      # copy + autostart + start now
    python install.py install --no-autostart
    python install.py status
    python install.py uninstall
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from petcore import __version__, paths

LAUNCHER_NAME = "OpenCode-Pet.vbs"
SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))

# Never copied: caches, logs and VCS metadata that belong to the source tree.
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "state", ".pytest_cache",
             # Design documentation: how the character was drawn.  It belongs in
             # the repository, not in an installed copy - nothing at runtime may
             # read it, and the curated artwork under ``assets/brand/`` is what
             # actually ships.
             "art"}
SKIP_FILES = {"pet_state.json", ".DS_Store", "Thumbs.db"}
SKIP_SUFFIX = (".pyc", ".pyo", ".log", ".tmp", ".lnk")

# Repository-only files at the top level.  They document the project and must
# not end up inside an installation.
DEV_ONLY_ROOT_FILES = {
    ".gitignore",
}
DEV_ONLY_ROOT_PREFIXES = ("下载", "AI建议")


def _skip(name: str, relative_dir: str = ".") -> bool:
    if name in SKIP_FILES or name.endswith(SKIP_SUFFIX):
        return True
    if os.path.normpath(relative_dir) == ".":
        if name in DEV_ONLY_ROOT_FILES:
            return True
        base = os.path.splitext(name)[0]
        return any(base.startswith(prefix) for prefix in DEV_ONLY_ROOT_PREFIXES)
    return False


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------
def target_root() -> str:
    return os.path.join(paths.opencode_config_dir(), "pet")


def launcher_path(startup: str = None) -> str:
    return os.path.join(startup or paths.startup_dir(), LAUNCHER_NAME)


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------
def clear_destination(destination: str, preserve_state: bool = True) -> int:
    """Make the install directory a clean mirror.

    A plain copy leaves files behind forever: delete a module, reinstall, and
    the stale copy is still on the import path.  Only the user's own settings
    and logs survive - that is the point of installing rather than re-cloning.
    """
    if not os.path.isdir(destination):
        return 0
    removed = 0
    keep = {"pet_state.json", "state"} if preserve_state else set()
    for name in os.listdir(destination):
        if name in keep:
            continue
        path = os.path.join(destination, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
            removed += 1
        except OSError as exc:
            print("  ! could not remove %s (%s)" % (name, exc))
    return removed


def copy_tree(source: str, destination: str, verbose: bool = True) -> int:
    copied = 0
    os.makedirs(destination, exist_ok=True)
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, source)
        target_root_dir = destination if rel == "." else os.path.join(destination, rel)
        os.makedirs(target_root_dir, exist_ok=True)
        for name in files:
            if _skip(name, rel):
                continue
            src = os.path.join(root, name)
            dst = os.path.join(target_root_dir, name)
            shutil.copy2(src, dst)
            copied += 1
            if verbose:
                print("  + %s" % os.path.relpath(dst, destination))
    return copied


# ---------------------------------------------------------------------------
# autostart
# ---------------------------------------------------------------------------
# WScript.Shell.Run takes ONE command string.  VBScript escapes a quote inside
# a literal as "", so the command  "<pythonw>" "<script>"  has to be written as
#   """<pythonw>"" ""<script>"""
# which decodes, left to right, to the single string  "<pythonw>" "<script>" :
#   "  open | ""  escaped quote | <pythonw> | ""  escaped quote | close
#   "  open | ""  escaped quote | <script> | ""  escaped quote | "  close
# Writing the two paths as adjacent literals instead is a syntax error, and
# wscript.exe fails *silently* - the sentinel simply never starts at sign-in.
LAUNCHER_TEMPLATE = (
    "' OpenCode Pet - sign-in sentinel (pythonw, no console window).\r\n"
    "' Generated by install.py.  Delete this file to remove the automatic start.\r\n"
    "Set oShell = CreateObject(\"WScript.Shell\")\r\n"
    "oShell.Run \"\"\"{pythonw}\"\" \"\"{script}\"\"\", 0, False\r\n"
)


def write_launcher(root: str, startup: str = None) -> str:
    startup = startup or paths.startup_dir()
    os.makedirs(startup, exist_ok=True)
    pythonw = paths.pythonw()
    script = os.path.join(root, "pet_watcher.py")
    for path in (pythonw, script):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
    if '"' in pythonw or '"' in script:
        raise ValueError("a quote in the install path cannot be escaped here")
    body = LAUNCHER_TEMPLATE.format(pythonw=pythonw, script=script)
    path = launcher_path(startup)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(body)
    return path


def launcher_command(root: str) -> str:
    """The exact command line the generated launcher runs - for tests and docs."""
    return '"%s" "%s"' % (paths.pythonw(), os.path.join(root, "pet_watcher.py"))


def remove_launcher() -> bool:
    path = launcher_path()
    if os.path.isfile(path):
        os.unlink(path)
        return True
    return False


# ---------------------------------------------------------------------------
# the watchdog: the one thing that makes the sentinel recoverable
# ---------------------------------------------------------------------------
WATCHDOG_TASK = "OpenCode Pet Sentinel"
WATCHDOG_MINUTES = 3


def _schtasks(*args: str):
    """Run ``schtasks`` quietly; never raise.

    The watchdog is an improvement, not a requirement: a machine that refuses
    to create a scheduled task (a locked-down policy, a stripped edition) must
    still get a working pet, so every failure here is reported and swallowed.
    """
    flags = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW
    try:
        return subprocess.run(["schtasks", *args], capture_output=True,
                              text=True, timeout=30, creationflags=flags)
    except Exception:                                  # noqa: BLE001
        return None


def watchdog_command(root: str) -> str:
    """The one-liner the scheduled task runs.

    Starting the sentinel is idempotent: it takes the shared lock and exits
    immediately if another copy already holds it.  So the watchdog can run
    every few minutes unconditionally and cost nothing when everything is
    healthy - which is the only shape that actually heals, because a watchdog
    that has to decide whether to act has to be right every time, and this one
    only has to be *called*.
    """
    script = os.path.join(root, "pet_watcher.py")
    return '"%s" "%s"' % (paths.pythonw(), script)


def install_watchdog(root: str) -> bool:
    """Register a periodic task that keeps the sentinel alive.

    The sentinel was started once, at sign-in, and nothing ever restarted it.
    That made it a single point of failure: the moment it exited - for any
    reason at all, including a hard crash it could not report - the desktop
    stayed empty until the user logged out and back in.  This happened twice.

    A periodic task removes that whole failure class, whatever the underlying
    cause turns out to be.  It is deliberately independent of the pet, of the
    sentinel and of anything this project writes at runtime: Windows runs it.
    """
    result = _schtasks(
        "/Create", "/TN", WATCHDOG_TASK,
        "/TR", watchdog_command(root),
        "/SC", "MINUTE", "/MO", str(WATCHDOG_MINUTES),
        "/F")
    if result is None or result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if result else ""
        print("  ! could not register the watchdog task: %s" % (detail or "?"))
        print("    the pet still works, but nothing will restart the sentinel "
              "if it dies")
        return False
    return True


def remove_watchdog() -> bool:
    result = _schtasks("/Delete", "/TN", WATCHDOG_TASK, "/F")
    return bool(result is not None and result.returncode == 0)


def watchdog_installed() -> bool:
    result = _schtasks("/Query", "/TN", WATCHDOG_TASK)
    return bool(result is not None and result.returncode == 0)


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------
def _is_our_pet(proc, target: str) -> bool:
    """Whether a process is a copy of *this* pet, pet or sentinel.

    Matching the install path in the command line is not enough: a pet launched
    from the install directory has the bare argv ``opencode_pet.py`` and a
    relative path, so its command line contains no directory at all.  Those are
    exactly the copies that survived a reinstall and ended up as a second pet
    on the desktop, so the working directory and the lock file are checked too.

    The sentinel is a target for the same reason as the pet.  Leaving it running
    across a reinstall is worse than leaving a stale pet: the new sentinel fails
    its single-instance claim and exits, so the old process - running the code
    from *before* the install - stays in charge of the pet forever.
    """
    try:
        cmdline = " ".join(proc.info.get("cmdline") or [])
    except Exception:                                      # noqa: BLE001
        return False
    lowered = cmdline.lower()
    # The installer is never a victim: it runs from the source folder and would
    # otherwise match by name, stopping the very install that is in progress.
    if "install.py" in lowered:
        return False
    # A ``--status`` run reports on the pet and must not stop it, so never match
    # the process doing the asking.
    if proc.pid == os.getpid():
        return False
    names = ("opencode_pet.py", "pet_watcher.py")
    if not any(name in lowered for name in names):
        return False
    if target.lower() in lowered:
        return True
    try:
        cwd = (proc.cwd() or "").lower()
    except Exception:                                      # noqa: BLE001
        return False
    return cwd == target.lower() or cwd.lower().startswith(target.lower())


def stop_running(wait: float = 1.5) -> int:
    """Stop every copy of the pet, so an install never leaves an orphan."""
    stopped = 0
    try:
        import psutil  # type: ignore
    except ImportError:
        return stopped
    target = target_root()
    victims = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if _is_our_pet(proc, target):
                victims.append(proc)
        except Exception:                                  # noqa: BLE001
            continue
    for proc in victims:
        try:
            proc.terminate()
            stopped += 1
        except Exception:                                  # noqa: BLE001
            pass
    gone, alive = psutil.wait_procs(victims, timeout=wait)
    for proc in alive:
        try:
            proc.kill()
            stopped += 1
        except Exception:                                  # noqa: BLE001
            pass

    # Give the terminated processes a moment to actually release their files
    # before anything below starts overwriting them.  Without this the copy can
    # race a process that has been asked to exit but has not finished.
    if stopped:
        time.sleep(1.0)

    # A surviving lock file would keep the next start from claiming it, so it
    # has to go once the processes it names are gone.  These live in the shared
    # lock directory, not under the install folder - see ``paths.lock_dir()``.
    from petcore import paths as paths_mod

    for name in ("pet.lock", "sentinel.lock"):
        for candidate in (os.path.join(target, "state", name),
                          os.path.join(paths_mod.lock_dir(), name)):
            try:
                os.unlink(candidate)
            except OSError:
                pass
    return stopped


def start_now(root: str) -> bool:
    """Start the sentinel, exactly the way sign-in does.

    Starting ``opencode_pet.py`` directly looks equivalent and is not: the pet
    would then have no supervisor, so it would survive OpenCode closing (lingering
    as an "offline" pet) and nothing would restart it after a crash.  The
    sentinel owns that lifecycle - it launches the pet when the desktop appears
    and kills it when the desktop goes away - so going through it is the only
    way the freshly installed state matches what the next sign-in will produce.
    """
    script = os.path.join(root, "pet_watcher.py")
    if not os.path.isfile(script):
        return False
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        subprocess.Popen([paths.pythonw(), script], cwd=root, creationflags=flags)
        return True
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_install(args) -> int:
    destination = target_root()
    print("Installing OpenCode Pet %s" % __version__)
    print("  source : %s" % SOURCE_ROOT)
    print("  target : %s" % destination)

    # Installing is a request for the pet, so it clears any earlier "stay off".
    # Otherwise reinstalling after a deliberate quit would leave the user
    # staring at an installation that never shows anything, with no indication
    # why.
    if paths.set_user_quit(False):
        print("  cleared the earlier 'stay closed' request")

    if os.path.abspath(destination) == os.path.abspath(SOURCE_ROOT):
        print("  ! already running from the install location; nothing to copy")
    else:
        stop_running()
        stale = clear_destination(destination, preserve_state=not args.fresh)
        if stale:
            print("  removed %d stale entr%s" % (stale, "y" if stale == 1 else "ies"))
        count = copy_tree(SOURCE_ROOT, destination)
        print("  %d files copied" % count)

    if args.no_autostart:
        remove_launcher()
        print("  autostart : removed")
    else:
        path = write_launcher(destination)
        print("  autostart : %s" % path)

    if args.no_autostart:
        remove_watchdog()
        print("  watchdog  : removed")
    elif install_watchdog(destination):
        print("  watchdog  : %s (every %d min, keeps the sentinel alive)"
              % (WATCHDOG_TASK, WATCHDOG_MINUTES))

    if args.no_start:
        print("  started   : no (--no-start)")
    else:
        ok = start_now(destination)
        print("  started   : %s" % ("yes" if ok else "no (run scripts\\run_pet.bat)"))

    print("\nDone. Right-click the pet for the menu; it exits with OpenCode.")
    return 0


def cmd_uninstall(args) -> int:
    destination = target_root()
    print("Removing OpenCode Pet")
    print("  stopped : %d process(es)" % stop_running())
    if remove_launcher():
        print("  launcher : removed")
    else:
        print("  launcher : not present")
    if remove_watchdog():
        print("  watchdog : removed")
    else:
        print("  watchdog : not present")
    if args.keep_files:
        print("  files    : kept at %s (--keep-files)" % destination)
    elif os.path.isdir(destination):
        shutil.rmtree(destination, ignore_errors=True)
        print("  files    : removed %s" % destination)
    else:
        print("  files    : nothing to remove")
    return 0


def cmd_status(_args) -> int:
    destination = target_root()
    launcher = launcher_path()
    installed = os.path.isdir(destination)
    entry = os.path.join(destination, "opencode_pet.py")
    print("OpenCode Pet %s" % __version__)
    print("  installed     : %s" % ("yes" if installed else "no"))
    print("  install dir   : %s" % destination)
    print("  entry point   : %s" % ("present" if os.path.isfile(entry) else "MISSING"))
    print("  autostart     : %s" % (launcher if os.path.isfile(launcher) else "off"))
    print("  user closed   : %s"
          % ("YES - it will stay closed (delete %s to bring it back)"
             % paths.quit_marker() if paths.user_quit() else "no"))
    print("  watchdog      : %s"
          % ("%s (every %d min)" % (WATCHDOG_TASK, WATCHDOG_MINUTES)
             if watchdog_installed() else "OFF - the sentinel will not recover"))
    print("  state file    : %s" % os.path.join(destination, "pet_state.json"))
    extra = _load_extra_endpoints()
    print("  extra services: %s"
          % (", ".join(sorted(extra)) if extra else "none registered"))
    if extra:
        from petcore import endpoint as endpoint_mod
        try:
            forwards = endpoint_mod.ssh_forwards()
            for key in sorted(extra):
                if "://" in key:
                    found = endpoint_mod.probe(key, extra[key], timeout=4.0)
                    print("    %-30s %s" % (key, "reachable" if found
                                           else "*** unreachable ***"))
                    continue
                # A tunnel target: report the port it currently lives on,
                # which is the whole point of keying by target.
                live = [f for f in forwards if f.target == key]
                if not live:
                    print("    %-30s tunnel not connected" % key)
                    continue
                for forward in live:
                    url = "http://127.0.0.1:%d" % forward.local_port
                    found = endpoint_mod.probe(url, extra[key], timeout=4.0)
                    print("    %-30s %s  (now on 127.0.0.1:%d)"
                          % (key, "reachable" if found else "*** unreachable ***",
                             forward.local_port))
        except Exception as exc:                           # noqa: BLE001
            print("    (could not check: %r)" % (exc,))
    print("  settings file : %s" % paths.settings_file())
    try:
        from petcore import endpoint as endpoint_mod
        print("  opencode      : %s" % ("running" if endpoint_mod.desktop_running()
                                       else "not running"))
    except Exception:                                      # noqa: BLE001
        pass
    return 0


def _load_extra_endpoints() -> dict:
    from petcore import endpoint as endpoint_mod
    try:
        with open(endpoint_mod.credentials_file(), "r", encoding="utf-8") as h:
            data = json.load(h)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_extra_endpoints(data: dict) -> bool:
    from petcore import endpoint as endpoint_mod
    path = endpoint_mod.credentials_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        try:
            os.chmod(path, 0o600)          # a secret: owner read/write only
        except OSError:
            pass                          # not supported on every filesystem
        return True
    except OSError as exc:
        print("  ! could not write %s: %s" % (path, exc))
        return False


def cmd_add_endpoint(args) -> int:
    """Register a second OpenCode service - usually a remote workspace.

    A remote workspace runs its own ``opencode serve --service`` with its own
    password in its own ``service.json``, and it is reached over an SSH forward
    whose local port belongs to ``ssh.exe``.  The pet can therefore find it -
    the port is in the tunnel scan - but cannot authenticate to it: the local
    password is simply rejected, in milliseconds, with no hint that the
    endpoint was real.  That is why a conversation open in a remote workspace
    could be completely invisible to the pet.

    So the password has to be supplied once.  It is stored in OpenCode's config
    directory, **not** in this repository: a secret has no business in git.
    """
    url = (args.url or "").strip().rstrip("/")
    password = args.password
    if not url:
        print("Usage: python install.py add-endpoint <url> [password]")
        return 2
    if "://" not in url:
        url = "http://" + url

    if not password:
        # Read it without echoing it.
        try:
            import getpass
            password = getpass.getpass("Password for %s: " % url)
        except Exception:                                  # noqa: BLE001
            print("  ! no password given; nothing was changed")
            return 2
    if not password:
        print("  ! empty password; nothing was changed")
        return 2

    from petcore import endpoint as endpoint_mod
    proven = endpoint_mod.probe(url, password, timeout=6.0)
    data = _load_extra_endpoints()
    if proven is None:
        print("  ! %s did not accept that password as an OpenCode service" % url)
        print("    nothing was written - a wrong entry would be re-probed every "
              "few seconds forever")
        return 1

    # Key the secret by *what the endpoint is*, not by the port it happens to
    # use today.  ssh allocates a new local port every time the tunnel is
    # re-established - and OpenCode re-establishes it on every restart - so a
    # port-keyed secret stops matching within hours and the remote workspace
    # silently disappears from the pet again.
    target = endpoint_mod.tunnel_target(url)
    if target:
        for stale in [key for key in data if "://" in key
                      and endpoint_mod.tunnel_target(key) == target]:
            data.pop(stale, None)
        data.pop(url, None)
        key = target
        print("  added  : %s -> %s  (v%s, pid %s)"
              % (target, url, proven.version, proven.pid))
        print("  follows: that tunnel, whatever local port it uses next time")
    else:
        key = url
        print("  added  : %s  (v%s, pid %s)" % (url, proven.version, proven.pid))

    data[key] = password
    if not _save_extra_endpoints(data):
        return 1
    print("  stored : %s" % endpoint_mod.credentials_file())
    print("\nRestart the pet to pick it up:  python install.py enable")
    return 0


def cmd_remove_endpoint(args) -> int:
    from petcore import endpoint as endpoint_mod
    key = (args.url or "").strip().rstrip("/")
    if not key:
        print("Usage: python install.py remove-endpoint <url-or-target>")
        return 2
    data = _load_extra_endpoints()
    candidates = {key}
    if "://" not in key:
        candidates.add("http://" + key)
    else:
        candidates.add(key.split("://", 1)[1])
    # Accept the current tunnel URL even when the secret is stored by target.
    url = key if "://" in key else "http://" + key
    target = endpoint_mod.tunnel_target(url)
    if target:
        candidates.add(target)

    removed = [c for c in candidates if c in data]
    if not removed:
        print("  %s was not registered" % key)
        return 1
    for entry in removed:
        data.pop(entry, None)
    _save_extra_endpoints(data)
    print("  removed : %s" % ", ".join(sorted(removed)))
    return 0


def cmd_enable(_args) -> int:
    """Bring the pet back after a deliberate quit.

    The counterpart to the tray's Exit, and the one a person can actually find.
    Clearing the latch is not enough on its own if the pet was not running when
    the request was made, so the sentinel is started the same way sign-in would
    - it will start the pet within a poll or two.
    """
    changed = paths.set_user_quit(False)
    print("OpenCode Pet is welcome back"
          if changed else "the pet was not closed - nothing to change")
    root = target_root()
    if os.path.isfile(os.path.join(root, "pet_watcher.py")):
        print("  started   : %s" % ("yes" if start_now(root) else "no"))
    else:
        print("  not installed yet - run: python install.py install")
    return 0


def cmd_disable(_args) -> int:
    """Close the pet and keep it closed, the same as the tray's Exit.

    Useful for stopping it without a right-click, and for stopping it in a way
    that survives: no watchdog tick, no sign-in and no relaunch will bring it
    back, because the sentinel checks this before it ever starts anything.
    """
    print("OpenCode Pet will stay closed")
    print("  stopped   : %d process(es)" % stop_running())
    paths.set_user_quit(True)
    print("  marker    : %s" % paths.quit_marker())
    print("\nBring it back with:  python install.py enable")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="install.py", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    install = sub.add_parser("install", help="copy the pet and set up autostart")
    install.add_argument("--no-autostart", action="store_true")
    install.add_argument("--no-start", action="store_true")
    install.add_argument("--fresh", action="store_true",
                         help="also reset position, scale and language")
    install.set_defaults(func=cmd_install)
    remove = sub.add_parser("uninstall", help="remove the pet and autostart")
    remove.add_argument("--keep-files", action="store_true")
    remove.set_defaults(func=cmd_uninstall)
    status = sub.add_parser("status", help="show what is installed")
    status.set_defaults(func=cmd_status)
    sub.add_parser("enable",
                   help="bring the pet back after a deliberate quit"
                   ).set_defaults(func=cmd_enable)
    sub.add_parser("disable",
                   help="close the pet and keep it closed"
                   ).set_defaults(func=cmd_disable)

    add_ep = sub.add_parser(
        "add-endpoint",
        help="register another OpenCode service, e.g. a remote workspace")
    add_ep.add_argument("url", help="the local URL the tunnel exposes, "
                                     "e.g. 127.0.0.1:62701")
    add_ep.add_argument("password", nargs="?", default=None,
                        help="that service's password (prompted for if omitted)")
    add_ep.set_defaults(func=cmd_add_endpoint)

    rm_ep = sub.add_parser("remove-endpoint", help="forget a registered service")
    rm_ep.add_argument("url")
    rm_ep.set_defaults(func=cmd_remove_endpoint)
    return parser


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
