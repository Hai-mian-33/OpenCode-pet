"""The pure core: events and API reads in, pet mood and cards out.

Nothing in this module performs I/O, reads a clock or touches a global, so
every behaviour worth trusting is a plain function with a unit test.

Privacy boundary
----------------
OpenCode's event payloads carry prompts, tool arguments and tool results.  The
fold below *recognises* those fields but never keeps them.  What survives is
exactly: a state, tool **names**, step counts, timestamps, the final segment
of the working directory (used as the project label) and a short machine error
code.  Session titles are opt-in, because they are derived from your prompts;
``tests/run_tests.py`` folds an event carrying a secret and asserts the secret
is absent from the resulting facts.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# states
# ---------------------------------------------------------------------------
IDLE = "idle"
THINKING = "thinking"
EXECUTING = "executing"
WAITING = "waiting"
DONE = "done"
ERROR = "error"
DRAG = "drag"
LONG = "longtask"
OFFLINE = "offline"
# The service is alive but the OpenCode *window* is not.  Nothing can be
# answered, nothing can progress, so the pet says so instead of pretending.
CLOSED = "closed"

STATES = (IDLE, THINKING, EXECUTING, WAITING, DONE, ERROR, DRAG, LONG, OFFLINE,
          CLOSED)

# Higher wins when several sessions compete for the pet's single expression.
PRIORITY = {
    WAITING: 100,     # a human is blocked - the loudest thing on screen
    ERROR: 90,
    EXECUTING: 70,
    LONG: 60,
    THINKING: 50,
    DONE: 40,         # unread completions must not vanish silently
    DRAG: 30,
    IDLE: 10,
    OFFLINE: 5,
}

RUNNING_STATES = (THINKING, EXECUTING, WAITING, LONG)
LIVE_STATES = RUNNING_STATES + (DONE, ERROR)

BAND = 1_000_000                  # width of one priority band in rank points
LONG_TASK_MS = 10 * 60 * 1000        # "busy more than ten minutes"

# How long a pending approval request must survive before the pet tells the user
# it is waiting.  See ``_request_wait``: this is the only claim that demands an
# action, so it is the only one worth being sure about.
#
# It has to be comfortably longer than the poll interval.  ``form.created``
# arrives over SSE the instant it happens, but the *absence* of a form can only
# be noticed by the poll - so a request that vanished straight away still looks
# pending for up to one poll cycle.  With a confirmation shorter than the poll,
# every one of those artefacts was announced: measured, every "waiting" episode
# on a wake-up lasted ~1.5 s, which was the confirmation expiring before the poll
# could contradict it.  A genuine approval sits for minutes, so this costs the
# user nothing in practice.
WAIT_CONFIRM_MS = 4000
TOOL_GRACE_MS = 1500                 # ignore tool churn shorter than this

_ERROR_CODE_RE = re.compile(
    r"\b(E[A-Z]{2,12}|HTTP\d{3}|ERR_[A-Z0-9_]+|[A-Z]+Error)\b")


# ---------------------------------------------------------------------------
# a session fact
# ---------------------------------------------------------------------------
def new_session(session_id: str, now: int = 0) -> Dict[str, Any]:
    """A fresh, deliberately boring fact set."""
    return {
        "id": session_id,
        "state": IDLE,
        "tool": "",
        "steps": 0,
        "errors": 0,
        "title": "",
        "project": "",
        "directory": "",
        "agent": "",
        "started": now,
        "updated": now,
        "finished": 0,
        # The server's own completion stamp (``time.idle``).  Unlike
        # ``finished`` this is identical on every restart, which is what makes
        # "has this been read?" answerable across runs.  See ``_merge_info``.
        "server_finished": 0,
        "outcome": "",
        "error_code": "",
        "permission": "",
        "running": False,
        "acknowledged": False,
        # False once the OpenCode window has closed, until the API is read
        # again.  A window reopening is not evidence that anything is running.
        "verified": True,
        # When a pending approval form was first seen.  It only becomes the
        # reported state once it has lasted ``WAIT_CONFIRM_MS``.
        "pending_since": 0,
        "last_step": "",
        "tool_since": 0,
        "notify_state": "",
        "notify_step": -1,
    }


def session_label(record: Dict[str, Any], show_titles: bool = True) -> str:
    """What the pet is allowed to call this conversation."""
    if show_titles:
        title = (record.get("title") or "").strip()
        if title:
            return title
    project = (record.get("project") or "").strip()
    return project or (record.get("directory") or "").strip()


# ---------------------------------------------------------------------------
# event folding
# ---------------------------------------------------------------------------
def apply_event(sessions: Dict[str, Dict[str, Any]], event: Dict[str, Any],
                now: int = 0) -> Dict[str, Dict[str, Any]]:
    """Fold one SSE payload into the fact set.  Mutates and returns ``sessions``.

    Unknown event types are ignored on purpose: the stream is versioned by the
    server, not by us, and a new event must never break the pet.
    """
    if not isinstance(event, dict):
        return sessions
    kind = event.get("type") or ""
    data = event.get("data")
    if not isinstance(data, dict):
        return sessions
    session_id = data.get("sessionID") or data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return sessions

    record = sessions.get(session_id)
    if record is None:
        record = new_session(session_id, now)
        sessions[session_id] = record
    # A live event is proof of life, so it re-verifies the record just as a
    # reconcile does.
    record["verified"] = True
    _touch(record, now)

    if kind == "session.step.started":
        record["state"] = THINKING
        record["running"] = True
        record["outcome"] = ""
        record["error_code"] = ""
        record["permission"] = ""
        record["tool"] = ""
        record["last_step"] = "step"
        record["notify_state"] = ""
        if not record["started"]:
            record["started"] = int(data.get("started") or now)
        agent = data.get("agent")
        if isinstance(agent, str):
            record["agent"] = agent

    elif kind in ("session.reasoning.started", "session.reasoning.delta",
                  "session.reasoning.ended", "session.text.started",
                  "session.text.delta", "session.text.ended",
                  "session.step.streamed"):
        if record["state"] in (IDLE, THINKING) or record["state"] == LONG:
            record["state"] = THINKING
        record["running"] = True

    elif kind == "session.tool.input.started":
        name = data.get("name")
        record["tool"] = name if isinstance(name, str) else record["tool"]
        record["state"] = EXECUTING
        record["running"] = True
        record["last_step"] = "tool"
        record["tool_since"] = now

    elif kind == "session.tool.called":
        record["state"] = EXECUTING
        record["running"] = True
        record["tool_since"] = record["tool_since"] or now
        # ``data["input"]`` holds the tool arguments.  Recognised, never kept.
        record["steps"] += 1

    elif kind == "session.tool.progress":
        if record["state"] in (EXECUTING, LONG):
            record["state"] = EXECUTING

    elif kind == "session.tool.success":
        record["tool"] = ""
        record["tool_since"] = 0
        if record["state"] in (EXECUTING, LONG):
            record["state"] = THINKING

    elif kind == "session.tool.failed":
        record["tool"] = ""
        record["tool_since"] = 0
        record["errors"] += 1
        record["error_code"] = _error_code(data)
        record["state"] = ERROR
        record["running"] = True
        record["notify_state"] = ""
        # The agent usually recovers on its own, so remember that this is a
        # transient stop rather than a finished turn.
        record["last_step"] = "tool-failed"

    elif kind == "session.step.ended":
        finish = data.get("finish") or ""
        record["last_step"] = finish or record["last_step"]
        if finish == "tool-calls":
            record["state"] = THINKING if record["state"] != ERROR else ERROR
            record["running"] = True
        elif finish == "error":
            record["state"] = ERROR
            record["running"] = True
        record["tool"] = ""
        record["tool_since"] = 0

    elif kind == "session.idle":
        outcome = data.get("outcome") or "succeeded"
        _finish(record, str(outcome), now)

    elif kind in ("session.error", "session.failed"):
        record["errors"] += 1
        record["error_code"] = _error_code(data)
        _finish(record, "failed", now)

    elif kind == "session.updated":
        info = data.get("info") or data
        _merge_info(record, info, now)

    elif kind == "form.created":
        # In OpenCode V2 an approval prompt arrives as a form.  We only learn
        # that somebody needs a human; the form body is not ours to read.
        # Confirmed before it is announced - see ``_request_wait``.
        _request_wait(record, now)
        record["running"] = True
        record["permission"] = _permission_action(data)

    elif "permission" in kind:
        if kind.endswith(".replied") or kind.endswith(".removed"):
            _clear_pending(record)
            if record["state"] == WAITING:
                record["state"] = EXECUTING if record["tool"] else THINKING
                record["permission"] = ""
        else:
            _request_wait(record, now)
            record["running"] = True
            record["permission"] = _permission_action(data)

    return sessions


def _touch(record: Dict[str, Any], now: int) -> None:
    record["updated"] = now


def _request_wait(record: Dict[str, Any], now: int) -> None:
    """Note that a human may be needed - and only shout once it is certain.

    "Waiting for your authorization" is the one claim the pet makes that demands
    an action from the user, so it is the one that must not be made on a
    rumour.  When OpenCode starts up its service briefly reports a pending form
    while the app settles, and the pet would announce it, blink the lamp, and
    then - about a second later, when the form was gone - quietly change its
    mind.  Reported as a "waiting for authorization" flash on every wake-up.

    So a pending form only becomes the reported state once it has survived
    ``WAIT_CONFIRM_MS``.  Until then the session is described as what it is
    actually doing - running a tool - which is also true, and costs the user a
    second of patience rather than a second of being misinformed.  A genuine
    approval request sits for minutes, so it is never delayed in practice.
    """
    if not record.get("pending_since"):
        record["pending_since"] = now
        return
    if now - int(record["pending_since"]) < WAIT_CONFIRM_MS:
        return
    if record["state"] != WAITING:
        record["state"] = WAITING
        record["notify_state"] = ""


def _clear_pending(record: Dict[str, Any]) -> None:
    record["pending_since"] = 0


def _finish(record: Dict[str, Any], outcome: str, now: int) -> None:
    outcome = outcome or "succeeded"
    record["running"] = False
    record["finished"] = now
    record["outcome"] = outcome
    record["tool"] = ""
    record["tool_since"] = 0
    record["permission"] = ""
    record["acknowledged"] = False
    if outcome == "failed":
        record["state"] = ERROR
    elif outcome == "interrupted":
        record["state"] = IDLE
        record["acknowledged"] = True
    else:
        record["state"] = DONE
        record["last_step"] = "done"


def _merge_info(record: Dict[str, Any], info: Dict[str, Any], now: int) -> None:
    if not isinstance(info, dict):
        return
    title = info.get("title")
    if isinstance(title, str):
        record["title"] = title
    agent = info.get("agent")
    if isinstance(agent, str) and agent:
        record["agent"] = agent
    outcome = info.get("outcome")
    if isinstance(outcome, str) and outcome and not record["running"]:
        if outcome != record["outcome"] or record["state"] not in (DONE, ERROR, IDLE):
            _finish(record, outcome, now)

    # The server's own completion stamp, read *before* the finish transition so
    # it can be handed to ``_finish`` rather than lost to it.
    idle_at = 0
    time_block = info.get("time")
    if isinstance(time_block, dict):
        created = time_block.get("created")
        if isinstance(created, int) and not record["started"]:
            record["started"] = created
        idle = time_block.get("idle")
        if isinstance(idle, int) and idle > 0:
            idle_at = idle

    if idle_at and not record["running"]:
        # ``finished`` normally holds the local clock at the moment this pet saw
        # the task end, which is *different on every restart* - so it can never
        # be compared against a value remembered from a previous run, and every
        # finished task replayed itself.  The server's ``time.idle`` is the one
        # stamp that is identical before and after a restart, so it is kept
        # separately as the authoritative comparison key.
        record["server_finished"] = idle_at
        if record["state"] in (DONE, ERROR):
            record["finished"] = idle_at
    location = info.get("location")
    directory = None
    if isinstance(location, dict):
        directory = location.get("directory")
    if isinstance(directory, str) and directory:
        record["directory"] = directory
        record["project"] = directory.rstrip("\\/").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]


def _error_code(data: Dict[str, Any]) -> str:
    """A short machine code, extracted with a bounded pattern."""
    error = data.get("error")
    if isinstance(error, dict):
        for key in ("code", "name", "type"):
            value = error.get(key)
            if isinstance(value, str) and value:
                return value[:32]
        message = error.get("message") or error.get("data") or ""
        if isinstance(message, dict):
            message = message.get("message") or ""
    elif isinstance(error, str):
        message = error
    else:
        message = ""
    if not isinstance(message, str) or not message:
        return ""
    match = _ERROR_CODE_RE.search(message[:400])
    return match.group(1) if match else ""


def _permission_action(data: Dict[str, Any]) -> str:
    for key in ("action", "title", "name", "type"):
        value = data.get(key)
        if isinstance(value, str) and value and value not in ("form",):
            return value[:40]
    info = data.get("info")
    if isinstance(info, dict):
        for key in ("title", "name", "action"):
            value = info.get(key)
            if isinstance(value, str) and value:
                return value[:40]
    return ""


# ---------------------------------------------------------------------------
# reconciliation - the slow path that repairs any gap the stream left
# ---------------------------------------------------------------------------
def reconcile(sessions: Dict[str, Dict[str, Any]], active: Dict[str, Any],
              infos: Iterable[Dict[str, Any]], permissions: Dict[str, list],
              now: int = 0, active_ttl_ms: int = 4000) -> Dict[str, Dict[str, Any]]:
    """Fold a full API read over the event-derived facts.

    The SSE stream is volatile by contract: events during a disconnection are
    missed.  Polling ``/api/session``, ``/api/session/active`` and the pending
    permission lists is what makes the pet correct even when it started
    mid-session or was disconnected for a while.
    """
    live = {sid for sid, value in (active or {}).items()
            if isinstance(value, dict) and value.get("type") == "running"}

    for info in infos or ():
        if not isinstance(info, dict):
            continue
        session_id = info.get("id")
        if not isinstance(session_id, str):
            continue
        record = sessions.get(session_id)
        if record is None:
            record = new_session(session_id, now)
            sessions[session_id] = record
        # This record has just been read from the API, so whatever it says is
        # current - which is exactly what ``invalidate_live`` took away.
        record["verified"] = True
        _merge_info(record, info, now)
        _touch(record, now)
        if session_id in live:
            if not record["running"]:
                # We missed the step-start event; adopt the running truth.
                record["running"] = True
                record["outcome"] = ""
                record["acknowledged"] = False
                record["finished"] = 0
                # The API reports a stale ``time.idle`` from the *previous* run
                # of this session.  It was read above, while the record still
                # looked finished, so it has to be cleared here - otherwise a
                # live session carries a completion stamp for work that is still
                # going on, and the read-memory would match it.
                record["server_finished"] = 0
                record["state"] = _recover_state(record)
            record["last_seen_active"] = now
        else:
            record.pop("last_seen_active", None)
            if record["running"] and record["state"] in RUNNING_STATES:
                # Not active any more: the turn is over.
                outcome = info.get("outcome") or "succeeded"
                _finish(record, str(outcome), now)
            elif not record["running"] and not record["finished"]:
                _finish(record, str(info.get("outcome") or "succeeded"), now)

    # ``permissions`` must carry an entry for every session we actually polled
    # - an empty list means "polled, nothing pending".  A missing key means
    # "not polled", so a waiting session is left alone rather than cleared.
    for session_id, pending in (permissions or {}).items():
        record = sessions.get(session_id)
        if record is None:
            continue
        if pending:
            # Confirmed before it is announced: a form that is still there on
            # the next poll is real, one that vanished was start-up noise.
            _request_wait(record, now)
            first = pending[0] if isinstance(pending[0], dict) else None
            record["permission"] = (_permission_action(first) if first
                                    else record["permission"])
            record["running"] = True
        elif record["state"] == WAITING or record.get("pending_since"):
            _clear_pending(record)
            if record["state"] == WAITING:
                record["state"] = EXECUTING if record["tool"] else THINKING
                record["permission"] = ""

    for session_id in list(sessions.keys()):
        record = sessions[session_id]
        if not record.get("running") and record["state"] == WAITING:
            record["state"] = THINKING
            record["permission"] = ""
        last = record.get("last_seen_active")
        if last is not None and now - last > active_ttl_ms:
            record.pop("last_seen_active", None)
    return sessions


def _recover_state(record: Dict[str, Any]) -> str:
    if record["tool"]:
        return EXECUTING
    return THINKING


def prune(sessions: Dict[str, Dict[str, Any]], keep_ids: Iterable[str] = (),
          max_records: int = 60) -> Dict[str, Dict[str, Any]]:
    """Drop sessions nobody can still see, then cap the total.

    A finished task is kept until it is acknowledged, so this only removes
    sessions that OpenCode itself no longer lists.
    """
    keep = set(keep_ids or ())
    for session_id, record in list(sessions.items()):
        if session_id in keep:
            continue
        if record["running"] or not record["acknowledged"]:
            continue
        sessions.pop(session_id, None)
    if len(sessions) > max_records:
        ordered = sorted(sessions.items(), key=lambda kv: kv[1]["updated"])
        for session_id, _record in ordered[:len(sessions) - max_records]:
            sessions.pop(session_id, None)
    return sessions


# ---------------------------------------------------------------------------
# derived views
# ---------------------------------------------------------------------------
def rank(record: Dict[str, Any], now: int = 0) -> int:
    """Tie-breaker: how loudly this session deserves the pet's face.

    Priority bands are a million points wide; within a band the most recently
    touched session wins, so a burst of events on one conversation always
    floats to the top of its own state.
    """
    state = record["state"]
    base = PRIORITY.get(state, 0)
    if _is_long(record, now) and state in RUNNING_STATES:
        base = PRIORITY[LONG]
    updated = int(record.get("updated") or 0)
    age = max(0, int(now) - updated) if now else 0
    return base * BAND - min(age, BAND - 1)


def _is_long(record: Dict[str, Any], now: int) -> bool:
    if not record.get("running"):
        return False
    start = record.get("started")
    if not start:
        start = record.get("updated")
    if not start:
        start = now
    return (now - start) >= LONG_TASK_MS


def invalidate_live(sessions: Dict[str, Dict[str, Any]]) -> int:
    """Mark every session as "not known to be current".  Returns the count.

    Called when the OpenCode window closes.  The session records in memory are
    the state as it was *when the app went away*: the service keeps reporting
    them unchanged, so they look live long after they stopped being so.

    That is harmless while the pet is hiding live cards, but the moment the
    window comes back those records are rendered again - and the card said
    "waiting for your authorization" with a timer frozen at the value it had
    before the close, while the API's own ``time.idle`` was three minutes in the
    past.  The window reopening is not evidence that anything is running; only
    a fresh read of the API is.  So the claims are dropped here and rebuilt by
    the next ``reconcile``.
    """
    count = 0
    for record in sessions.values():
        if not record.get("verified", True):
            continue
        record["verified"] = False
        count += 1
    return count


def mood(sessions: Dict[str, Dict[str, Any]], now: int = 0,
         dragging: bool = False, app_open: bool = True) -> Dict[str, Any]:
    """The single expression the pet shows, plus why.

    ``app_open`` is whether the OpenCode *window* exists.  With the window closed
    the background service keeps reporting the session exactly as it was, so
    rendering that verbatim means claiming work is progressing when it cannot be
    - and, worse, shouting "waiting for your permission" at someone who has no
    window in which to give it.

    Unverified records are excluded as well: those are the ones carried over
    from before the window closed.  Until a ``reconcile`` has read the API again
    the honest answer is "nothing is known to be running", not "still running".
    """
    if dragging:
        return {"state": DRAG, "session": None}
    if not app_open:
        return {"state": CLOSED, "session": None}
    known = {sid: rec for sid, rec in sessions.items()
             if rec.get("verified", True)}
    if not known:
        return {"state": IDLE, "session": None}
    best_id, best = max(known.items(), key=lambda kv: rank(kv[1], now))
    state = best["state"]
    if _is_long(best, now) and state in (THINKING, EXECUTING):
        state = LONG
    return {"state": state, "session": best}


def visible(sessions: Dict[str, Dict[str, Any]], now: int = 0,
            app_open: bool = True) -> List[Dict[str, Any]]:
    """Every session worth a card, most urgent first.

    With the window closed, live cards are withheld.  A finished task is still
    worth showing - it really did finish, and it stays until it is acknowledged
    - but a card claiming to be running, waiting or thinking describes work that
    cannot move until the app is back, and showing it is what made the pet look
    stuck rather than waiting.

    Unverified records are withheld too, on both counts: they are what the app
    looked like before it closed, so neither "it is running" nor "it is waiting
    for you" can be claimed until the API has been read again.
    """
    out = []
    for record in sessions.values():
        if not record.get("verified", True):
            continue
        if not app_open and record["running"]:
            continue
        if record["running"] or not record["acknowledged"]:
            out.append(record)
    out.sort(key=lambda r: rank(r, now), reverse=True)
    return out


def counts(sessions: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    running = sum(1 for r in sessions.values() if r["running"])
    waiting = sum(1 for r in sessions.values() if r["state"] == WAITING)
    done = sum(1 for r in sessions.values()
               if r["state"] == DONE and not r["acknowledged"])
    error = sum(1 for r in sessions.values()
                if r["state"] == ERROR and not r["acknowledged"])
    return {"running": running, "waiting": waiting, "done": done,
            "error": error, "total": len(sessions)}


def acknowledge(sessions: Dict[str, Dict[str, Any]], session_id: str) -> bool:
    record = sessions.get(session_id)
    if not record or record["running"]:
        return False
    record["acknowledged"] = True
    if record["state"] in (DONE, ERROR):
        record["state"] = IDLE
    return True


# ---------------------------------------------------------------------------
# remembering what has been read, across restarts
# ---------------------------------------------------------------------------
# How many sessions the "already read" memory holds.  Each entry is a handful of
# bytes, but it is a file that grows forever if nothing prunes it.
ACK_MEMORY_LIMIT = 300


def ack_key(record: Dict[str, Any]) -> int:
    """The stamp that identifies "this finished run of the session".

    Prefers the server's ``time.idle`` because it is the same number on every
    start.  ``finished`` holds the local clock when this pet happened to notice
    the task end, so after a restart it holds a *later* value and would compare
    as brand new work - which is exactly how finished tasks kept replaying.
    """
    return int(record.get("server_finished") or record.get("finished") or 0)


def remember_ack(acked: Dict[str, Any], session_id: str,
                 finished: int) -> Dict[str, Any]:
    """Record that a finished session has been read.  Returns the same mapping.

    The value is the task's completion stamp, not the moment it was clicked, so
    the memory means exactly one thing: "this session had already finished by
    then, and the user has seen that".  A later run of the *same* session gets a
    later stamp and so does not match - which is what keeps genuinely new work on
    an old conversation from being silently swallowed.
    """
    if not session_id:
        return acked
    acked[session_id] = int(finished or 0)
    if len(acked) > ACK_MEMORY_LIMIT:
        # Drop the oldest memories first: those are the ones whose sessions are
        # least likely to still be around to be re-shown.
        oldest = sorted(acked.items(), key=lambda kv: int(kv[1] or 0))
        for key, _value in oldest[:len(acked) - ACK_MEMORY_LIMIT]:
            acked.pop(key, None)
    return acked


def restore_ack(sessions: Dict[str, Dict[str, Any]], acked: Dict[str, Any]) -> int:
    """Re-apply remembered acknowledgements.  Returns how many were restored.

    Acknowledgement used to live only in memory, so every restart rebuilt the
    whole session list from the API with ``acknowledged = False`` and every
    already-read task popped back onto the card stack - the same finished tasks,
    again, every single time the pet woke up.

    The comparison is deliberately one-sided.  ``seen >= key`` means the user saw
    this finished state; a strictly newer key means the session ran again since,
    so it is new work and must be shown.
    """
    if not isinstance(acked, dict) or not acked:
        return 0
    restored = 0
    for session_id, record in sessions.items():
        if record["running"] or record["acknowledged"]:
            continue
        key = ack_key(record)
        if not key:
            continue                       # never finished: nothing to remember
        try:
            seen = int(acked.get(session_id, 0) or 0)
        except (TypeError, ValueError):
            continue
        if seen and seen >= key:
            record["acknowledged"] = True
            if record["state"] in (DONE, ERROR):
                record["state"] = IDLE
            restored += 1
    return restored


def new_notice(sessions: Dict[str, Dict[str, Any]], record: Dict[str, Any]) -> str:
    """Return a notification key when this transition deserves a toast.

    Fires once per (state, step) pair so a long task does not toast on every
    tool call.
    """
    state = record["state"]
    if state not in (WAITING, DONE, ERROR):
        record["notify_state"] = ""
        return ""
    if record.get("acknowledged") and state != WAITING:
        return ""
    step = int(record.get("steps") or 0)
    if record.get("notify_state") == state and record.get("notify_step") == step:
        return ""
    record["notify_state"] = state
    record["notify_step"] = step
    return {"waiting": "notif.waiting_title",
            "done": "notif.done_title",
            "error": "notif.error_title"}[state]
