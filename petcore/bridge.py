"""A loopback-only HTTP bridge so anything can push work onto the pet.

    curl -X POST http://127.0.0.1:8767/event \\
      -H "Content-Type: application/json" \\
      -d '{"state":"executing","task":"Deploy the release","steps":12}'

Recognised fields: ``state`` (idle / thinking / executing / waiting / done /
error), ``task``, ``project``, ``steps``, ``errors``, ``tool``, ``notify``,
``session``.  Injected sessions are tagged ``external`` and never touch
OpenCode's own data, so they can never be confused with a real conversation.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional

DEFAULT_PORTS = (8767, 8768, 8776)
MAX_BODY = 64 * 1024

VALID_STATES = ("idle", "thinking", "executing", "waiting", "done", "error")


class EventBridge:
    """Runs the HTTP server on a daemon thread."""

    def __init__(self, on_event: Callable[[Dict], None],
                 on_status: Optional[Callable[[bool], None]] = None,
                 port: int = 0):
        self.on_event = on_event
        self.on_status = on_status
        self.port = int(port or 0)
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle --------------------------------------------------------
    def start(self) -> int:
        """Bind and serve on a background thread.  Returns the bound port."""
        if self._server is not None:
            return self.port
        ports = [self.port] if self.port else list(DEFAULT_PORTS)
        last: Optional[Exception] = None
        for candidate in ports:
            try:
                server = ThreadingHTTPServer(("127.0.0.1", candidate),
                                             _make_handler(self))
                server.daemon_threads = True
            except OSError as exc:
                last = exc
                continue
            self._server = server
            self.port = server.server_address[1]
            self._thread = threading.Thread(target=server.serve_forever,
                                            kwargs={"poll_interval": 0.5},
                                            name="pet-bridge", daemon=True)
            self._thread.start()
            if self.on_status:
                self.on_status(True)
            return self.port
        if last:
            return 0
        return 0

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        try:
            server.shutdown()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            server.server_close()
        except Exception:                                  # noqa: BLE001
            pass
        if self.on_status:
            self.on_status(False)

    @property
    def running(self) -> bool:
        return self._server is not None


def _make_handler(bridge: EventBridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenCodePet/" + "1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):                     # noqa: A003
            pass                                           # keep the desktop clean

        # -- helpers -----------------------------------------------------
        def _send(self, code: int, payload: Dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if length <= 0 or length > MAX_BODY:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8", "replace"))
            except (ValueError, OSError):
                return {}

        # -- routes ------------------------------------------------------
        def do_OPTIONS(self):                              # noqa: N802
            self._send(204, {})

        def do_GET(self):                                  # noqa: N802
            if self.path.rstrip("/") in ("", "/health"):
                self._send(200, {"ok": True, "port": bridge.port})
                return
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):                                 # noqa: N802
            if self.path.rstrip("/") != "/event":
                self._send(404, {"ok": False, "error": "not found"})
                return
            payload = self._read_json()
            if not isinstance(payload, dict) or not payload:
                self._send(400, {"ok": False, "error": "expected a JSON object"})
                return
            state = str(payload.get("state") or "").lower()
            if state not in VALID_STATES:
                self._send(400, {"ok": False,
                                 "error": "state must be one of %s" % (VALID_STATES,)})
                return
            bridge.on_event(payload)
            self._send(200, {"ok": True})

    return Handler
