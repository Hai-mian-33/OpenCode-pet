#!/usr/bin/env python
"""Push fake sessions onto the pet, for checking the cards and the bridge.

    python tools/demo_events.py                 # one full synthetic task
    python tools/demo_events.py --state waiting # hold one state
    python tools/demo_events.py --bridge-only   # skip the in-process demo

With the pet running (``python opencode_pet.py``) this drives it through
thinking -> executing -> needs-you -> done using the real local HTTP bridge,
so the whole pipeline is exercised without running a real OpenCode task.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from petcore import i18n                                    # noqa: E402

DEFAULT_PORT = 8767


def post(port: int, payload: dict, timeout: float = 2.0) -> bool:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:%d/event" % port, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError) as exc:
        print("  bridge not reachable on %d (%s)" % (port, exc))
        return False


def health(port: int) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1.5) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


SCRIPT = [
    ("idle", 2.0, 0),
    ("thinking", 3.0, 0),
    ("executing", 4.0, 3),
    ("executing", 3.0, 9),
    ("waiting", 4.0, 9),
    ("executing", 3.0, 12),
    ("done", 5.0, 14),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--state", default=None,
                        help="hold a single state instead of the script")
    parser.add_argument("--task", default="")
    parser.add_argument("--project", default="opencode-pet")
    parser.add_argument("--session", default="demo-events")
    parser.add_argument("--bridge-only", action="store_true")
    args = parser.parse_args()

    i18n.set_language(os.environ.get("OPENCODE_PET_LANG") or "zh")
    task = args.task or i18n.t("app.name")

    print("pet bridge: 127.0.0.1:%d  (%s)"
          % (args.port, "up" if health(args.port) else "not running"))
    if not health(args.port):
        print("start the pet first:  python opencode_pet.py")
        return 1

    if args.state:
        ok = post(args.port, {"session": args.session, "state": args.state,
                              "task": task, "project": args.project,
                              "steps": 5})
        print("%-10s %s" % (args.state, "sent" if ok else "failed"))
        return 0 if ok else 1

    for state, seconds, steps in SCRIPT:
        print("%-10s %s  (%s)" % (state, "sent" if post(args.port, {
            "session": args.session, "state": state, "task": task,
            "project": args.project, "steps": steps,
            "tool": "shell" if state == "executing" else "",
        }) else "failed", "%ds" % seconds))
        if not args.bridge_only:
            time.sleep(seconds)

    print("\nclick the card to acknowledge it; the pet will drop that session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
