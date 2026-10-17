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
