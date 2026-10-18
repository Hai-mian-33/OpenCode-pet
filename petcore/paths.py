"""Filesystem layout for OpenCode Pet.

Everything the pet owns lives underneath ``APP_ROOT`` so that an installed
copy is completely self-contained: copy that one folder anywhere and it runs,
delete it and nothing is left behind.  Nothing in this module ever points at a
development directory.
"""

from __future__ import annotations

import os
import sys

# ...\petcore\paths.py -> ...\          (the app root, wherever it was installed)
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APP_NAME = "OpenCode Pet"


# --------------------------------------------------------------------------
# environment helpers
# --------------------------------------------------------------------------
def home() -> str:
    return os.path.expanduser("~")


def _xdg(var: str, fallback: str) -> str:
    value = os.environ.get(var)
    if value and os.path.isabs(value):
        return value
    return os.path.join(home(), fallback)


# --------------------------------------------------------------------------
# OpenCode's own directories (read-only for us)
# --------------------------------------------------------------------------
def opencode_config_dir() -> str:
    """``~/.config/opencode`` - where OpenCode keeps its config and password."""
    override = os.environ.get("OPENCODE_CONFIG_DIR")
    if override:
        return os.path.abspath(override)
    return os.path.join(_xdg("XDG_CONFIG_HOME", os.path.join(".config")), "opencode")


def opencode_data_dir() -> str:
    """``~/.local/share/opencode`` - logs and other runtime data."""
    override = os.environ.get("OPENCODE_DATA_DIR")
    if override:
        return os.path.abspath(override)
    return os.path.join(_xdg("XDG_DATA_HOME", os.path.join(".local", "share")), "opencode")


def service_file() -> str:
    return os.path.join(opencode_config_dir(), "service.json")


def opencode_log_file() -> str:
    return os.path.join(opencode_data_dir(), "log", "opencode.log")


# --------------------------------------------------------------------------
# directories this project owns
# --------------------------------------------------------------------------
def state_dir() -> str:
    override = os.environ.get("OPENCODE_PET_STATE_DIR")
    if override:
        return os.path.abspath(override)
    return os.path.join(APP_ROOT, "state")


def settings_file() -> str:
    return os.path.join(APP_ROOT, "pet_state.json")


def log_file() -> str:
    return os.path.join(state_dir(), "pet.log")


def watcher_log_file() -> str:
    return os.path.join(state_dir(), "watcher.log")


def app_root() -> str:
    return APP_ROOT


def assets_dir() -> str:
    return os.path.join(APP_ROOT, "assets")


def quit_marker() -> str:
    """The file whose mere existence means "the user asked the pet to stay off".

    A latch rather than a preference, and deliberately *not* part of
    ``pet_state.json``:

    - it must be machine-global, like the locks.  ``pet_state.json`` lives inside
      the application folder, so a checkout on the Desktop and the installation
      in OpenCode's config have separate ones - and "I quit the pet" has to mean
      "the pet is off", not "the copy I happened to click was off";
    - it must survive a corrupt or missing settings file, because the one moment
      it matters most is when something has already gone wrong;
    - its meaning has to be obvious to whoever finds it.  A file called
      ``pet-quit`` sitting in OpenCode's config directory explains itself; a
      boolean buried in a JSON blob does not.

    It lives in OpenCode's config directory rather than the install folder for
    the same reason the locks do: everything that starts the pet has to agree
    about whether it should.
    """
    return os.path.join(opencode_config_dir(), "pet-quit")


def user_quit() -> bool:
    """Whether the user has asked for the pet to stay off."""
    return os.path.exists(quit_marker())


def set_user_quit(quit_requested: bool) -> bool:
    """Latch or clear the request.  Returns whether the state changed."""
    try:
        if quit_requested:
            if user_quit():
                return False
            os.makedirs(os.path.dirname(quit_marker()), exist_ok=True)
            with open(quit_marker(), "w", encoding="utf-8") as handle:
                handle.write("The desktop pet was closed from its tray menu.\n"
                             "Delete this file to have it come back.\n")
            return True
        if not user_quit():
            return False
        os.unlink(quit_marker())
        return True
    except OSError:
        return False


def lock_dir() -> str:
    """Where the single-instance locks live - deliberately *not* ``APP_ROOT``.

    The locks used to sit in ``<APP_ROOT>/state``, which made them
    **per-copy**.  That is wrong for a lock whose entire purpose is "only one
    of me may run": every extra copy of this project on the machine - a
    checkout on the Desktop, a zip someone extracted, a folder left over from
    last week - got its own private lock and therefore its own private pet.
    Two pets then shared one desktop, and whichever sentinel noticed a blip
    would stop whichever pet it found first.

    Anchoring the locks to OpenCode's own config directory makes them shared by
    every copy on the machine, which is the property that actually matters.  The
    *installed* copy's location is unchanged in practice, because it lives at
    ``~/.config/opencode/pet``; a copy run from anywhere else now contends for
    the same lock instead of quietly becoming a second pet.

    Logs deliberately stay per-copy (``state_dir()``): a developer's log belongs
    next to the code they are running, not in the installation.

    ``OPENCODE_PET_LOCK_DIR`` overrides it, exactly as ``OPENCODE_PET_STATE_DIR``
    overrides ``state_dir()``.  The test suite needs that because it starts real
    subprocesses to prove the single-instance behaviour, and a monkeypatch in
    the parent cannot reach a child process.
    """
    override = os.environ.get("OPENCODE_PET_LOCK_DIR")
    if override:
        return os.path.abspath(override)
    return os.path.join(opencode_config_dir(), "pet-locks")


def docs_dir() -> str:
    return os.path.join(APP_ROOT, "docs")


def startup_dir() -> str:
    return os.path.join(
        os.environ.get("APPDATA", os.path.join(home(), "AppData", "Roaming")),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )


def ensure_dirs() -> None:
    for path in (APP_ROOT, state_dir(), assets_dir()):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


def pythonw() -> str:
    """Path to ``pythonw.exe`` next to the running interpreter (no console)."""
    exe = sys.executable or "python"
    candidate = os.path.join(os.path.dirname(os.path.abspath(exe)), "pythonw.exe")
    if os.path.isfile(candidate):
        return candidate
    return exe


def desktop_exe() -> str:
    """Best guess at the OpenCode desktop executable, for click-to-focus."""
    override = os.environ.get("OPENCODE_DESKTOP_EXE")
    if override and os.path.isfile(override):
        return override
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                     "@opencodedesktop", "OpenCode.exe"),
        os.path.join(os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
                     "@opencodedesktop", "OpenCode.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def describe() -> dict:
    """A small diagnostic map; the ``--doctor`` command prints it."""
    return {
        "app_root": APP_ROOT,
        "state_dir": state_dir(),
        "settings_file": settings_file(),
        "opencode_config_dir": opencode_config_dir(),
        "opencode_data_dir": opencode_data_dir(),
        "service_file": service_file(),
        "opencode_log_file": opencode_log_file(),
        "startup_dir": startup_dir(),
        "python": sys.executable,
        "pythonw": pythonw(),
        "desktop_exe": desktop_exe(),
    }
