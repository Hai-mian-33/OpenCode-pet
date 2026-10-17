"""OpenCode Pet - a bilingual desktop pet for the OpenCode desktop app.

This package holds everything that does not need Qt: paths, i18n, service
discovery, the OpenCode API client, the pure session reducer and the local
HTTP event bridge.  ``pet_watcher`` must stay importable without Qt, so it
only ever touches this package.
"""

__version__ = "1.0.0"
__all__ = ["paths", "i18n", "endpoint", "client", "reducer", "settings", "bridge"]
