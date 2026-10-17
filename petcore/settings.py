"""Persisted pet preferences.

One JSON file next to the application, written atomically.  It holds only
window geometry and toggles - never anything read out of a session.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict

from . import i18n, paths

DEFAULTS: Dict[str, Any] = {
    "lang": "",
    "scale": 1.0,
    "x": -1,              # -1 = let the pet choose a corner on first run
    "y": -1,
    "topmost": True,
    "show_titles": True,
    "silent": False,
    "expanded": False,
    "follow_app": True,
    "endpoint": "",
    "bridge_port": 0,
    # session id -> the millisecond timestamp at which that finished task was
    # last read.  Not a session title and not any session content: just "this has
    # been seen", so that waking the pet up does not replay every old card.
    "acked": {},
}


def _read(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=folder, prefix=".pet-", suffix=".tmp", delete=False)
    try:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except (OSError, ValueError):
        try:
            os.close(handle.fileno())
            os.unlink(handle.name)
        except OSError:
            pass
        raise


class Settings:
    """A tiny dict wrapper with a persisted backing file."""

    def __init__(self, path: str = None):
        self.path = path or paths.settings_file()
        self._data = dict(DEFAULTS)
        self._data.update(_read(self.path))
        if not self._data.get("lang"):
            self._data["lang"] = i18n.detect_system_language()
        i18n.set_language(self._data["lang"])

    # -- access ----------------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def get(self, key: str, fallback: Any = None) -> Any:
        value = self._data.get(key, DEFAULTS.get(key, fallback))
        return fallback if value is None and key not in self._data else value

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self._data[key] = value
        if save:
            self.save()

    def update(self, values: Dict[str, Any], save: bool = True) -> None:
        self._data.update(values)
        if save:
            self.save()

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    # -- language --------------------------------------------------------
    @property
    def lang(self) -> str:
        return i18n.normalize_language(self._data.get("lang"))

    def set_lang(self, lang: str, save: bool = True) -> str:
        value = i18n.set_language(lang)
        self._data["lang"] = value
        if save:
            self.save()
        return value

    # -- persistence -----------------------------------------------------
    def save(self) -> None:
        try:
            _write(self.path, self._data)
        except OSError:
            pass
