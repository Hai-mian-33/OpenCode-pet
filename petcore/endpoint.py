"""Locating the running OpenCode service.

The desktop app spawns ``opencode-cli serve --service``, which listens on a
random loopback port and authenticates every request with HTTP Basic using the
password stored in ``~/.config/opencode/service.json``.  Nothing advertises
that port, so we find it the only way the operating system allows: enumerate
the loopback listeners that belong to an OpenCode process, then prove the
candidate by asking it for ``/api/info``.

Discovery order (first one that validates wins):

1. an endpoint cached in the pet's settings file, re-validated;
2. ``OPENCODE_SERVER_URL`` / ``OPENCODE_PORT`` from the environment;
3. listening sockets owned by an OpenCode process (psutil, else netstat);
4. a short list of well-known development ports.

Only stdlib is required; ``psutil`` is used when present purely to make step 3
cheaper and to avoid parsing localized ``netstat``/``tasklist`` output when we
can avoid it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

from . import paths

# Process names that can own the API socket.
SERVICE_PROCESS_NAMES = ("opencode-cli.exe", "opencode-cli", "opencode.exe", "opencode")
# Process names that mean "the desktop app is open" (used by the sentinel).
DESKTOP_PROCESS_NAMES = ("OpenCode.exe", "opencode.exe", "opencode-desktop.exe")

FALLBACK_PORTS = (4096, 4097, 3000, 7575, 7777)

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Endpoint:
    """A validated OpenCode service endpoint."""

    __slots__ = ("url", "password", "pid", "version")

    def __init__(self, url: str, password: str, pid: int = 0, version: str = ""):
        self.url = url.rstrip("/")
        self.password = password
        self.pid = int(pid or 0)
        self.version = version or ""

    def auth_header(self) -> str:
        raw = ("opencode:" + (self.password or "")).encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def __eq__(self, other):
        return isinstance(other, Endpoint) and other.url == self.url and other.pid == self.pid

    def __hash__(self):
        return hash((self.url, self.pid))

    def __repr__(self):
        return "Endpoint(%r, pid=%s, version=%r)" % (self.url, self.pid, self.version)


# ---------------------------------------------------------------------------
# password
# ---------------------------------------------------------------------------
def read_password(path: str = None) -> str:
    """The shared service password, or ``""`` when it is not there yet."""
    path = path or paths.service_file()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    if isinstance(data, dict):
        for key in ("password", "token", "secret"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


# Processes that may own a *forwarded* port for a remote workspace.  A remote
# OpenCode reached over SSH is exposed on localhost by an ``ssh -L`` forward,
# and that socket belongs to ``ssh.exe`` - not to any OpenCode process.  So a
# scan that only looks at OpenCode's own listeners can never find a remote
# workspace, which is exactly the conversation a user most wants to see.
TUNNEL_PROCESS_NAMES = ("ssh.exe", "ssh", "pageant.exe", "putty.exe", "plink.exe")

# Ports a forwarded remote workspace commonly lands on.  Probing these costs
# one short HTTP request each and is the difference between "the pet cannot
# see remote workspaces" and "it can".
TUNNEL_FALLBACK_PORTS = (59471, 49374, 59102)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def probe(url: str, password: str, timeout: float = 1.2) -> Optional[Endpoint]:
    """Return an :class:`Endpoint` when ``url`` really is the OpenCode API."""
    request = urllib.request.Request(url.rstrip("/") + "/api/info", method="GET")
    request.add_header("Authorization", "Basic " + base64.b64encode(
        ("opencode:" + (password or "")).encode("utf-8")).decode("ascii"))
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    # /api/info always reports the server version and pid.  Anything else on
    # the port (a dev server, the Electron debug port) is rejected here.
    if not isinstance(payload.get("version"), str) or not payload["version"]:
        return None
    if not isinstance(payload.get("pid"), int):
        return None
    return Endpoint(url, password, payload["pid"], payload["version"])


# ---------------------------------------------------------------------------
# loopback listeners
# ---------------------------------------------------------------------------
def _pids_named(names: Iterable[str]) -> List[int]:
    wanted = {n.lower() for n in names}
    found: List[int] = []
    try:
        import psutil  # type: ignore
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except Exception:
                continue
            if name in wanted:
                found.append(int(proc.info["pid"]))
        return found
    except Exception:
        pass
    return _pids_named_tasklist(names)


def _pids_named_tasklist(names: Iterable[str]) -> List[int]:
    wanted = {n.lower() for n in names}
    try:
        output = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=8, creationflags=_NO_WINDOW,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return []
    pids: List[int] = []
    for line in output.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) < 2:
            continue
        if parts[0].lower() in wanted:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def _listening_ports(pids: Iterable[int]) -> List[int]:
    wanted = {int(p) for p in pids if p}
    if not wanted:
        return []
    try:
        import psutil  # type: ignore
        ports: List[int] = []
        for conn in psutil.net_connections(kind="inet"):
            if conn.pid not in wanted or not conn.laddr:
                continue
            host, port = conn.laddr[0], conn.laddr[1]
            if conn.status == "LISTEN" and host in ("127.0.0.1", "0.0.0.0", "::1", "::"):
                ports.append(int(port))
        if ports:
            return sorted(set(ports))
    except Exception:
        pass
    return _listening_ports_netstat(wanted)


def _listening_ports_netstat(pids: Iterable[int]) -> List[int]:
    wanted = {int(p) for p in pids}
    try:
        output = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, timeout=8, creationflags=_NO_WINDOW,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return []
    ports: List[int] = []
    for line in output.splitlines():
        if "LISTENING" not in line.upper():
            continue
        columns = line.split()
        if len(columns) < 5:
            continue
        if columns[-1] not in wanted and not columns[-1].isdigit():
            continue
        try:
            if int(columns[-1]) not in wanted:
                continue
        except ValueError:
            continue
        local = columns[1]
        if ":" not in local:
            continue
        host, _, port = local.rpartition(":")
        if host not in ("127.0.0.1", "0.0.0.0", "[::1]", "::", "*"):
            continue
        try:
            ports.append(int(port))
        except ValueError:
            continue
    return sorted(set(ports))


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def _env_endpoints() -> List[str]:
    """Endpoints named in the environment.

    ``OPENCODE_SERVER_URL`` accepts a **list**, separated by commas, semicolons
    or whitespace.  That is what makes a remote workspace reachable when its
    tunnel port is not one this module would guess: the user (or a launcher) can
    simply say which ports to ask.  Discovery still *proves* every one of them
    with ``/api/info``, so a wrong entry costs a refused connection and nothing
    more.
    """
    urls = []
    explicit = os.environ.get("OPENCODE_SERVER_URL") or os.environ.get("OPENCODE_URL")
    if explicit:
        for part in re.split(r"[,;\s]+", explicit.strip()):
            if part:
                urls.append(part if "//" in part else "http://" + part)
    port = os.environ.get("OPENCODE_PORT") or os.environ.get("OPENCODE_SERVER_PORT")
    if port and port.isdigit():
        urls.append("http://127.0.0.1:%s" % port)
    return urls


def _candidate_urls(cached: str = "", include_tunnels: bool = True) -> List[str]:
    """Every URL worth asking, in descending order of confidence.

    ``cached`` and the environment come first so the common case - one local
    service - costs exactly what it always cost.  The tunnel ports come last
    because they only exist for remote workspaces and each one costs a probe.
    """
    candidates: List[str] = []
    if cached:
        candidates.append(cached if "//" in cached else "http://" + cached)
    candidates.extend(_env_endpoints())
    candidates.extend("http://127.0.0.1:%d" % p
                      for p in _listening_ports(_pids_named(SERVICE_PROCESS_NAMES)))
    candidates.extend("http://127.0.0.1:%d" % p for p in FALLBACK_PORTS)
    if include_tunnels:
        candidates.extend("http://127.0.0.1:%d" % p
                          for p in _listening_ports(_pids_named(TUNNEL_PROCESS_NAMES)))
        candidates.extend("http://127.0.0.1:%d" % p for p in TUNNEL_FALLBACK_PORTS)
    return candidates


def discover_all(cached: str = "", password: str = None,
                 include_tunnels: bool = True) -> List[Endpoint]:
    """Find **every** OpenCode service reachable from this machine.

    ``discover`` stops at the first endpoint that validates, which is right when
    there is only ever one.  But OpenCode can host more than one at a time: a
    remote workspace reached over SSH is exposed on localhost by the tunnel's
    own forward, and it holds a completely separate set of sessions.  Stopping
    at the first hit makes those conversations invisible to the pet - the
    reported symptom, where a session was plainly open in the app and the pet
    had never heard of it.

    Order is meaningful: ``cached`` first, so with a single endpoint the result
    is identical to what :func:`discover` has always returned, and no extra
    probes are made once a high-confidence candidate answers.
    """
    if password is None:
        password = read_password()
    if not password:
        return []

    found: List[Endpoint] = []
    seen = set()
    candidates = _candidate_urls(cached, include_tunnels)
    # Speculative candidates - guessed ports, other people's listeners - get a
    # much shorter deadline than the cached one.  The cached endpoint is the one
    # we already know works, so it deserves to wait; the rest exist in the hope
    # that one answers, and a port that accepts a connection but never replies
    # must not hold the pet's poll loop for a second.
    for index, url in enumerate(candidates):
        url = url.rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        endpoint = probe(url, password, timeout=1.2 if index == 0 else 0.35)
        if endpoint and endpoint.url not in {e.url for e in found}:
            found.append(endpoint)
    return found


def discover(cached: str = "", password: str = None) -> Tuple[Optional[Endpoint], str]:
    """Find the service.  Returns ``(endpoint, error_key)``.

    ``error_key`` is ``""`` on success, otherwise an i18n key so the caller can
    render the reason in the right language.

    The *primary* endpoint is the first one ``discover_all`` finds, which is the
    same one this function has always returned.  Additional endpoints - remote
    workspaces - are found by :func:`discover_all` and are deliberately not
    reported here, so nothing that depends on "the one endpoint" changes.
    """
    if password is None:
        password = read_password()
    if not password:
        return None, "err.no_password"

    candidates: List[str] = _candidate_urls(cached, include_tunnels=False)
    seen = set()
    for url in candidates:
        url = url.rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        endpoint = probe(url, password)
        if endpoint:
            return endpoint, ""

    # A port that answers but rejects us means the password is stale rather
    # than the endpoint being wrong - say so instead of "not found".
    if probe(candidates[0], password) is None and _any_opencode_listener():
        return None, "err.auth"
    return None, "err.no_endpoint"


def _any_opencode_listener() -> bool:
    return bool(_listening_ports(_pids_named(SERVICE_PROCESS_NAMES)))


def desktop_running() -> bool:
    """True while the OpenCode desktop *window* is open."""
    return bool(_pids_named(DESKTOP_PROCESS_NAMES))


def service_running() -> bool:
    """True while the OpenCode service process exists, whatever its port."""
    return bool(_pids_named(SERVICE_PROCESS_NAMES))


def app_alive() -> bool:
    """True while OpenCode is alive in *any* form - window or background service.

    These are two independent processes with two independent lifetimes.
    ``OpenCode.exe`` is the Electron desktop app; ``opencode-cli serve --service``
    is the background service that owns the session store and the API port, and
    it is *not* a child of the desktop app - it survives the window closing.

    So the window alone is the wrong thing to watch.  Gating on it meant the pet
    vanished the moment the window was closed, while OpenCode was still running
    underneath with every session intact.  This is the check that has to decide
    whether the pet stays on screen.
    """
    return desktop_running() or service_running()


_NETSTAT_PORT_RE = re.compile(r"127\.0\.0\.1:(\d{4,5})")


def ports_from_log(limit: int = 200_000) -> List[int]:
    """Best-effort port hints from OpenCode's own log file."""
    try:
        with open(paths.opencode_log_file(), "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            tail = handle.read()
    except OSError:
        return []
    ports = [int(m) for m in _NETSTAT_PORT_RE.findall(tail)]
    return sorted(set(ports), reverse=True)[:8]
