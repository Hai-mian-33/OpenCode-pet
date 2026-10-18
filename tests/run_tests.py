#!/usr/bin/env python
"""Unit tests for OpenCode Pet.

    python tests/run_tests.py

Plain ``unittest`` so it runs anywhere, including inside a sandbox that denies
child-process spawning (where ``node --test`` would fail).
"""

from __future__ import annotations

import ast
import builtins
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from petcore import i18n, reducer                                   # noqa: E402
from petcore import paths as petcore_paths                         # noqa: E402
from petcore.reducer import (DONE, ERROR, EXECUTING, IDLE, LONG,     # noqa: E402
                             OFFLINE, THINKING, WAITING)

# Captured so tests can redirect the lock directory without leaking the patch.
paths_state_dir = petcore_paths.state_dir


def ev(kind: str, **data):
    payload = {"id": "evt", "created": 0, "type": kind, "data": data}
    return payload


class Rect:
    """The QRect surface CardStack.place() consumes, without needing Qt."""

    def __init__(self, left, top, width, height):
        self._l, self._t, self._w, self._h = left, top, width, height

    def left(self):
        return self._l

    def top(self):
        return self._t

    def width(self):
        return self._w

    def height(self):
        return self._h

    def right(self):
        return self._l + self._w

    def bottom(self):
        return self._t + self._h

    def center(self):
        class _C:
            def __init__(self, x):
                self._x = x

            def x(self):
                return self._x
        return _C(self._l + self._w // 2)


def _fill(key: str, lang: str) -> str:
    """Translate ``key`` with every placeholder it declares satisfied."""
    raw = i18n.TABLES[lang].get(key)
    if isinstance(raw, (list, tuple)):
        raw = raw[0]
    text = raw if isinstance(raw, str) else ""
    named = {name: 2 if name.isdigit() else "x"
             for name in re.findall(r"\{(\w+)\}", text)}
    return i18n.t(key, lang=lang, **named)


class FakeClock:
    def __init__(self):
        self.t = 1_000_000

    def tick(self, ms: int = 1000) -> int:
        self.t += ms
        return self.t


# ---------------------------------------------------------------------------
class TestEvents(unittest.TestCase):
    def test_step_start_is_thinking(self):
        clock = FakeClock()
        s = {}
        reducer.apply_event(s, ev("session.step.started", sessionID="a",
                                  agent="build", started=clock.t), clock.t)
        self.assertEqual(s["a"]["state"], THINKING)
        self.assertTrue(s["a"]["running"])
        self.assertEqual(s["a"]["agent"], "build")

    def test_tool_lifecycle(self):
        clock = FakeClock()
        s = {}
        reducer.apply_event(s, ev("session.step.started", sessionID="a"), clock.t)
        t = clock.tick()
        reducer.apply_event(s, ev("session.tool.input.started", sessionID="a",
                                  name="shell"), t)
        self.assertEqual(s["a"]["state"], EXECUTING)
        self.assertEqual(s["a"]["tool"], "shell")
        t = clock.tick()
        reducer.apply_event(s, ev("session.tool.called", sessionID="a",
                                  input={"command": "rm -rf /"}), t)
        self.assertEqual(s["a"]["steps"], 1)
        t = clock.tick()
        reducer.apply_event(s, ev("session.tool.success", sessionID="a",
                                  content=[{"type": "text", "text": "ok"}]), t)
        self.assertEqual(s["a"]["tool"], "")
        self.assertEqual(s["a"]["state"], THINKING)

    def test_tool_failure_is_error_but_still_running(self):
        clock = FakeClock()
        s = {}
        reducer.apply_event(s, ev("session.step.started", sessionID="a"), clock.t)
        t = clock.tick()
        reducer.apply_event(s, ev("session.tool.failed", sessionID="a",
                                  error={"name": "ENOENT"}), t)
        self.assertEqual(s["a"]["state"], ERROR)
        self.assertEqual(s["a"]["error_code"], "ENOENT")
        self.assertTrue(s["a"]["running"], "a failed tool does not end the turn")

    def test_idle_succeeded_is_done(self):
        clock = FakeClock()
        s = {}
        reducer.apply_event(s, ev("session.step.started", sessionID="a"), clock.t)
        t = clock.tick()
        reducer.apply_event(s, ev("session.idle", sessionID="a",
                                  outcome="succeeded"), t)
        self.assertEqual(s["a"]["state"], DONE)
        self.assertFalse(s["a"]["running"])
        self.assertFalse(s["a"]["acknowledged"])

    def test_idle_failed_is_error(self):
        clock = FakeClock()
        s = {}
        reducer.apply_event(s, ev("session.idle", sessionID="a",
                                  outcome="failed"), clock.t)
        self.assertEqual(s["a"]["state"], ERROR)

    def test_idle_interrupted_is_quiet_idle(self):
        clock = FakeClock()
        s = {}
        reducer.apply_event(s, ev("session.idle", sessionID="a",
                                  outcome="interrupted"), clock.t)
        self.assertEqual(s["a"]["state"], IDLE)
        self.assertTrue(s["a"]["acknowledged"])

    def test_form_created_means_waiting(self):
        clock = FakeClock()
        s = {}
        reducer.apply_event(s, ev("form.created", sessionID="a",
                                  title="Run shell command"), clock.t)
        self.assertEqual(s["a"]["permission"], "Run shell command",
                         "the action is known straight away")
        # Not announced yet - see TestWaitConfirmation.
        self.assertNotEqual(s["a"]["state"], WAITING)
        clock.tick(reducer.WAIT_CONFIRM_MS + 1)
        reducer.apply_event(s, ev("form.created", sessionID="a",
                                  title="Run shell command"), clock.t)
        self.assertEqual(s["a"]["state"], WAITING,
                         "a form that survives the confirmation is announced")

    def test_unknown_events_are_ignored(self):
        s = {}
        reducer.apply_event(s, ev("totally.made.up", sessionID="a"), 1)
        reducer.apply_event(s, {"type": "no.data"}, 1)
        reducer.apply_event(s, "not a dict", 1)
        self.assertEqual(s["a"]["state"], IDLE)

    def test_events_without_a_session_id_are_ignored(self):
        s = {}
        reducer.apply_event(s, ev("session.step.started"), 1)
        self.assertEqual(s, {})


# ---------------------------------------------------------------------------
class TestPrivacyBoundary(unittest.TestCase):
    def test_prompt_and_tool_arguments_never_survive(self):
        clock = FakeClock()
        s = {}
        secret = "sk-TOPSECRET-abcdef123456"
        reducer.apply_event(s, ev("session.step.started", sessionID="a"), clock.t)
        t = clock.tick()
        reducer.apply_event(s, ev("session.tool.input.started", sessionID="a",
                                  name="shell"), t)
        t = clock.tick()
        reducer.apply_event(s, ev("session.tool.input.ended", sessionID="a",
                                  text='{"command": "echo %s"}' % secret), t)
        t = clock.tick()
        reducer.apply_event(s, ev("session.tool.called", sessionID="a",
                                  input={"command": "echo " + secret}), t)
        t = clock.tick()
        reducer.apply_event(s, ev("session.tool.success", sessionID="a",
                                  content=[{"type": "text",
                                            "text": "wrote %s" % secret}]), t)
        reducer.apply_event(s, ev("session.idle", sessionID="a",
                                  outcome="succeeded"), t)
        blob = repr(s)
        self.assertNotIn(secret, blob)
        self.assertNotIn("command", blob)
        self.assertEqual(s["a"]["tool"], "")          # only the name survives
        self.assertEqual(s["a"]["steps"], 1)

    def test_only_the_final_path_segment_is_kept(self):
        s = {}
        info = {"id": "a", "location": {"directory": r"C:\Users\someone\secret\project"}}
        reducer.apply_event(s, {"type": "session.updated",
                                "data": {"sessionID": "a", "info": info}}, 1)
        self.assertEqual(s["a"]["project"], "project")
        self.assertNotIn("someone", s["a"]["project"])

    def test_titles_are_separate_from_the_project_label(self):
        s = {}
        info = {"id": "a", "title": "Refactor the parser",
                "location": {"directory": r"C:\work\parser"}}
        reducer.apply_event(s, {"type": "session.updated",
                                "data": {"sessionID": "a", "info": info}}, 1)
        self.assertEqual(reducer.session_label(s["a"], True), "Refactor the parser")
        self.assertEqual(reducer.session_label(s["a"], False), "parser")
        self.assertEqual(reducer.session_label(reducer.new_session("z"), False), "")

    def test_error_code_is_bounded(self):
        s = {}
        reducer.apply_event(s, ev("session.error", sessionID="a",
                                  error="failed with ENOSPC and a long tail " * 40), 1)
        self.assertEqual(s["a"]["error_code"], "ENOSPC")


# ---------------------------------------------------------------------------
class TestReconcile(unittest.TestCase):
    def test_running_session_adopts_the_api_truth(self):
        s = {}
        now = 100_000
        reducer.reconcile(s, {"a": {"type": "running"}},
                          [{"id": "a", "title": "t", "location": {"directory": "/p"}}],
                          {}, now)
        self.assertTrue(s["a"]["running"])
        self.assertIn(s["a"]["state"], (THINKING, EXECUTING))

    def test_not_active_any_more_finishes_the_turn(self):
        s = {}
        now = 100_000
        reducer.apply_event(s, ev("session.step.started", sessionID="a"), now)
        reducer.reconcile(s, {}, [{"id": "a", "outcome": "succeeded"}], {}, now + 5000)
        self.assertFalse(s["a"]["running"])
        self.assertEqual(s["a"]["state"], DONE)

    def test_pending_permission_promotes_to_waiting(self):
        s = {}
        now = 100_000
        pending = {"a": [{"id": "p", "action": "bash", "resources": ["*"]}]}
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          pending, now)
        self.assertEqual(s["a"]["permission"], "bash")
        self.assertNotEqual(s["a"]["state"], WAITING,
                            "not announced until it survives confirmation")
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          pending, now + reducer.WAIT_CONFIRM_MS + 1)
        self.assertEqual(s["a"]["state"], WAITING)

    def test_permission_cleared_returns_to_work(self):
        s = {}
        now = 100_000
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          {"a": [{"id": "p", "action": "bash", "resources": []}]}, now)
        # An entry with an empty list means "polled, nothing pending".
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          {"a": []}, now + 10)
        self.assertNotEqual(s["a"]["state"], WAITING)

    def test_unpolled_waiting_session_is_left_alone(self):
        """A missing key means "not polled", so a confirmed request survives."""
        s = {}
        now = 100_000
        pending = {"a": [{"id": "p", "action": "bash", "resources": []}]}
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          pending, now)
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          pending, now + reducer.WAIT_CONFIRM_MS + 1)
        self.assertEqual(s["a"]["state"], WAITING)
        # Now stop polling permissions entirely: it must not be cleared.
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}], {},
                          now + reducer.WAIT_CONFIRM_MS + 500)
        self.assertEqual(s["a"]["state"], WAITING)

    def test_the_server_idle_stamp_is_kept_separately(self):
        """``finished`` alone cannot survive a restart; ``server_finished`` can.

        ``finished`` is the local clock at the moment this pet noticed the task
        end, so it differs on every start.  Any "has this been read?" comparison
        made against it therefore fails after a restart, which is why finished
        tasks kept replaying.
        """
        idle = 1_700_000_000_000
        s = {}
        reducer.reconcile(s, {}, [{"id": "a", "outcome": "succeeded",
                                   "time": {"idle": idle}}], {}, 999_999)
        self.assertEqual(s["a"]["server_finished"], idle)
        self.assertEqual(s["a"]["finished"], idle,
                         "a finished session must report the server's stamp")

    def test_a_running_session_has_no_completion_stamp_yet(self):
        idle = 1_700_000_000_000
        s = {}
        reducer.reconcile(s, {"a": {"type": "running"}},
                          [{"id": "a", "outcome": "succeeded",
                            "time": {"idle": idle}}], {}, 999_999)
        self.assertTrue(s["a"]["running"])
        self.assertEqual(s["a"]["server_finished"], 0,
                         "a live session must not look already finished")


# ---------------------------------------------------------------------------
class TestWaitConfirmation(unittest.TestCase):
    """"Waiting for your authorization" must not be said on a rumour.

    It is the only claim the pet makes that demands an action from the user, and
    it is also the one that flickers.  When OpenCode starts up its service
    briefly reports a pending form while the app settles; the pet announced it,
    blinked the lamp, and about a second later - when the form was gone - quietly
    changed its mind.  Measured in the pet's own log, one second of "waiting"
    on every wake-up.
    """

    PENDING = {"a": [{"id": "p", "action": "bash", "resources": ["*"]}]}

    def _running(self, session_id="a"):
        s = {}
        reducer.reconcile(s, {session_id: {"type": "running"}},
                          [{"id": session_id}], {}, 100_000)
        return s

    def test_a_form_that_vanishes_is_never_announced(self):
        s = self._running()
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING, 101_000)
        # Gone again before the confirmation window closes - start-up noise.
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          {"a": []}, 101_500)
        self.assertNotEqual(s["a"]["state"], WAITING,
                            "a form that vanished must never have been announced")

    def test_a_form_that_stays_is_announced(self):
        s = self._running()
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING, 101_000)
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING,
                          101_000 + reducer.WAIT_CONFIRM_MS + 100)
        self.assertEqual(s["a"]["state"], WAITING)

    def test_the_action_is_known_immediately_even_before_announcing(self):
        """Confirmation delays the claim, not the information."""
        s = self._running()
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING, 101_000)
        self.assertEqual(s["a"]["permission"], "bash")

    def test_the_session_still_counts_as_running_while_unconfirmed(self):
        s = self._running()
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING, 101_000)
        self.assertTrue(s["a"]["running"])

    def test_a_reposted_form_does_not_restart_the_clock(self):
        """Re-reading the same pending form must not keep postponing it."""
        s = self._running()
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING, 101_000)
        moment = 101_000 + reducer.WAIT_CONFIRM_MS + 100
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING, moment)
        self.assertEqual(s["a"]["state"], WAITING)
        # Ten more polls of the same form, long after: still waiting, no flapping.
        for step in range(10):
            reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                              self.PENDING, moment + step * 2000)
            self.assertEqual(s["a"]["state"], WAITING)

    def test_a_cleared_request_clears_the_pending_mark(self):
        s = self._running()
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING, 101_000)
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          {"a": []}, 101_500)
        self.assertEqual(s["a"]["pending_since"], 0)
        # And a fresh one has to be confirmed again.
        reducer.reconcile(s, {"a": {"type": "running"}}, [{"id": "a"}],
                          self.PENDING, 102_000)
        self.assertNotEqual(s["a"]["state"], WAITING)

    def test_the_confirmation_is_longer_than_the_poll_interval(self):
        """Otherwise a form that vanished still gets announced.

        ``form.created`` arrives over SSE immediately, but the *absence* of a
        form is only noticed by the poll - 2.5 s.  A confirmation shorter than
        that cannot tell a live request from one that was already gone, which is
        exactly how every wake-up produced a ~1.5 s "waiting" flash.
        """
        poll_seconds = 2.5
        self.assertGreater(reducer.WAIT_CONFIRM_MS, poll_seconds * 1000,
                           "must outlast the poll, or every artefact is announced")

    def test_the_confirmation_is_short_against_a_real_request(self):
        """A real approval waits for minutes; a few seconds costs nothing."""
        self.assertLess(reducer.WAIT_CONFIRM_MS, 15_000)


# ---------------------------------------------------------------------------
class TestAckMemory(unittest.TestCase):
    """A finished task the user has read must not come back on the next start.

    Acknowledgement used to live only in memory, so every wake-up rebuilt the
    whole session list from the API with ``acknowledged = False`` and replayed
    every dismissed card - the same tasks, again, every single time.
    """

    IDLE = 1_700_000_000_000

    def _finished(self, session_id="a", idle=None, title="t"):
        return {"id": session_id, "title": title, "outcome": "succeeded",
                "time": {"idle": idle if idle is not None else self.IDLE}}

    def _ack_it(self, sessions, session_id="a"):
        for record in sessions.values():
            if record["id"] == session_id:
                reducer.acknowledge(sessions, session_id)
                return reducer.ack_key(record)
        self.fail("no record for %s" % session_id)

    def test_a_read_task_stays_read_after_a_restart(self):
        acked = {}
        first = {}
        reducer.reconcile(first, {}, [self._finished()], {}, self.IDLE - 10)
        self.assertEqual(first["a"]["state"], DONE)
        reducer.remember_ack(acked, "a", self._ack_it(first))

        # A restart: a brand-new, empty session map built from the same API.
        second = {}
        reducer.reconcile(second, {}, [self._finished()], {}, self.IDLE + 99_999)
        self.assertFalse(second["a"]["acknowledged"],
                         "precondition: the API knows nothing about being read")
        reducer.restore_ack(second, acked)
        self.assertTrue(second["a"]["acknowledged"],
                        "the task must stay dismissed across a restart")
        self.assertEqual(second["a"]["state"], IDLE)
        self.assertEqual(reducer.visible(second, self.IDLE), [],
                         "and must not appear on the card stack")

    def test_the_same_conversation_running_again_does_come_back(self):
        acked = {}
        first = {}
        reducer.reconcile(first, {}, [self._finished()], {}, self.IDLE - 10)
        reducer.remember_ack(acked, "a", self._ack_it(first))

        # The same session, run again later: a newer completion stamp.
        later = self.IDLE + 5_000
        second = {}
        reducer.reconcile(second, {}, [self._finished(idle=later)], {},
                          later + 10)
        reducer.restore_ack(second, acked)
        self.assertFalse(second["a"]["acknowledged"],
                         "new work on an old conversation must still be shown")
        self.assertEqual(len(reducer.visible(second, later + 10)), 1)

    def test_another_session_is_not_affected(self):
        acked = {"a": self.IDLE}
        sessions = {}
        reducer.reconcile(sessions, {}, [self._finished("a"), self._finished("b")],
                          {}, self.IDLE + 1)
        reducer.restore_ack(sessions, acked)
        self.assertTrue(sessions["a"]["acknowledged"])
        self.assertFalse(sessions["b"]["acknowledged"],
                         "only the session that was actually read is suppressed")

    def test_a_running_session_is_never_suppressed(self):
        acked = {"a": self.IDLE}
        s = {}
        reducer.reconcile(s, {"a": {"type": "running"}},
                          [self._finished("a")], {}, self.IDLE + 1)
        reducer.restore_ack(s, acked)
        self.assertFalse(s["a"]["acknowledged"],
                         "work in progress must stay visible")

    def test_a_never_finished_session_is_never_suppressed(self):
        acked = {"a": self.IDLE}
        s = {}
        reducer.reconcile(s, {}, [{"id": "a", "title": "t"}], {}, self.IDLE + 1)
        reducer.restore_ack(s, acked)
        self.assertFalse(s["a"]["acknowledged"])

    def test_garbage_in_the_saved_file_cannot_break_the_pet(self):
        sessions = {}
        reducer.reconcile(sessions, {}, [self._finished()], {}, self.IDLE + 1)
        for junk in ({"a": "not a number"}, {"a": None}, {"a": []}, [], "junk"):
            reducer.restore_ack(sessions, junk)          # must not raise
            sessions["a"]["acknowledged"] = False

    def test_the_memory_is_bounded(self):
        acked = {}
        limit = reducer.ACK_MEMORY_LIMIT
        for i in range(limit + 40):
            reducer.remember_ack(acked, "s%d" % i, 1000 + i)
        self.assertLessEqual(len(acked), limit,
                             "the file must not grow without bound")
        self.assertIn("s%d" % (limit + 39), acked,
                      "the newest memories are the ones worth keeping")

    def test_remembering_nothing_is_a_no_op(self):
        acked = {}
        reducer.remember_ack(acked, "", 123)
        self.assertEqual(acked, {})


# ---------------------------------------------------------------------------
class TestMood(unittest.TestCase):
    def _record(self, state, **kw):
        record = reducer.new_session("x")
        record["state"] = state
        record.update(kw)
        return record

    def test_waiting_outranks_everything(self):
        s = {"a": self._record(DONE), "b": self._record(EXECUTING),
             "c": self._record(WAITING), "d": self._record(ERROR)}
        self.assertEqual(reducer.mood(s)["state"], WAITING)

    def test_error_outranks_running(self):
        s = {"a": self._record(EXECUTING), "b": self._record(ERROR)}
        self.assertEqual(reducer.mood(s)["state"], ERROR)

    def test_unread_done_outranks_idle(self):
        s = {"a": self._record(IDLE), "b": self._record(DONE)}
        self.assertEqual(reducer.mood(s)["state"], DONE)

    def test_long_task_wins_over_plain_thinking(self):
        start = 1_000_000
        long_one = self._record(THINKING, started=start, updated=start, running=True)
        fresh = self._record(THINKING, started=start + 500_000, updated=start + 500_000,
                             running=True)
        self.assertEqual(reducer.mood({"a": long_one}, start + 1_000)["state"],
                         THINKING)
        moment = start + reducer.LONG_TASK_MS + 1
        self.assertEqual(reducer.mood({"a": long_one, "b": fresh}, moment)["state"],
                         LONG)

    def test_recent_update_breaks_ties(self):
        base = 1_000_000
        moment = base + 100_000
        stale = self._record(EXECUTING, started=base, updated=base, running=True)
        fresh = self._record(EXECUTING, started=base, updated=base + 5_000,
                             running=True)
        self.assertIs(reducer.mood({"a": stale, "b": fresh}, moment)["session"], fresh)
        self.assertIs(reducer.mood({"b": fresh, "a": stale}, moment)["session"], fresh)

    def test_urgent_state_beats_a_fresher_calm_one(self):
        base = 1_000_000
        moment = base + 100_000
        calm = self._record(IDLE, started=base, updated=moment)
        urgent = self._record(WAITING, started=base, updated=base, running=True)
        self.assertIs(reducer.mood({"a": calm, "b": urgent}, moment)["session"], urgent)

    def test_dragging_always_wins(self):
        s = {"a": self._record(WAITING)}
        self.assertEqual(reducer.mood(s, dragging=True)["state"], "drag")

    def test_no_sessions_is_idle(self):
        self.assertEqual(reducer.mood({})["state"], IDLE)


# ---------------------------------------------------------------------------
class TestAppClosed(unittest.TestCase):
    """The service outlives the window; the pet must not pretend otherwise.

    Closing the OpenCode window leaves ``opencode-cli serve --service`` running
    and it keeps reporting the session exactly as it was - including a pending
    permission.  Rendering that verbatim produced a card shouting "waiting for
    your permission" at someone with no window in which to give it, with a
    timer ticking for work that cannot progress.  It was still on screen for a
    moment after reopening, too, because the state it showed was simply stale.
    """

    def _live(self, session_id="a", state=WAITING, **kw):
        record = reducer.new_session(session_id, 1_000)
        record.update({"state": state, "running": True, "started": 1_000,
                       "updated": 1_000, "permission": "bash"})
        record.update(kw)
        return record

    def _finished(self, session_id="b", state=DONE):
        record = reducer.new_session(session_id, 1_000)
        record.update({"state": state, "running": False, "updated": 1_000,
                       "finished": 2_000, "acknowledged": False})
        return record

    def test_a_closed_window_never_produces_a_live_state(self):
        for state in (THINKING, EXECUTING, WAITING, LONG, DONE, ERROR):
            s = {"a": self._live(state=state)}
            self.assertEqual(reducer.mood(s, app_open=False)["state"],
                             reducer.CLOSED,
                             "%s must not survive a closed window" % state)

    def test_a_pending_permission_is_silenced_when_closed(self):
        """The reported symptom: "等待授权" with nobody able to answer."""
        s = {"a": self._live(WAITING)}
        self.assertEqual(reducer.mood(s, app_open=True)["state"], WAITING)
        self.assertEqual(reducer.mood(s, app_open=False)["state"],
                         reducer.CLOSED)

    def test_live_cards_are_withheld_while_closed(self):
        s = {"a": self._live(), "b": self._live("c", EXECUTING)}
        self.assertEqual(len(reducer.visible(s, 1_000)), 2)
        self.assertEqual(reducer.visible(s, 1_000, app_open=False), [],
                         "a card claiming to run under a closed window is a lie")

    def test_finished_cards_still_show_while_closed(self):
        """A task that really finished is still worth showing, and still waits
        to be acknowledged."""
        s = {"b": self._finished()}
        self.assertEqual(len(reducer.visible(s, 1_000, app_open=False)), 1)

    def test_the_real_mix_closed(self):
        s = {"a": self._live(), "b": self._finished()}
        shown = reducer.visible(s, 1_000, app_open=False)
        self.assertEqual([r["id"] for r in shown], ["b"])

    def test_dragging_still_wins_over_closed(self):
        s = {"a": self._live()}
        self.assertEqual(
            reducer.mood(s, dragging=True, app_open=False)["state"], "drag")

    def test_closed_wins_even_with_nothing_to_report(self):
        """With the window shut, "OpenCode closed" is the more useful face.

        "Idle" implies "connected and nothing to do"; naming the actual reason
        tells the user why the pet is resting and invites the click that brings
        the app back.  Pinned because the ordering is deliberate.
        """
        self.assertEqual(reducer.mood({}, app_open=False)["state"],
                         reducer.CLOSED)
        # With the window open and nothing to say, it is plain idle.
        self.assertEqual(reducer.mood({})["state"], IDLE)

    def test_app_open_defaults_to_true_so_old_callers_are_unchanged(self):
        s = {"a": self._live()}
        self.assertEqual(reducer.mood(s)["state"], reducer.mood(s, 0, False,
                                                                True)["state"])
        self.assertEqual(len(reducer.visible(s, 1_000)),
                         len(reducer.visible(s, 1_000, app_open=True)))

    def test_both_languages_can_name_the_state(self):
        for lang in i18n.available():
            self.assertTrue(i18n.t("state.closed", lang=lang).strip(),
                            lang)
            self.assertTrue(i18n.t("hover.app_closed", lang=lang).strip(), lang)

    def test_the_character_actually_draws_the_state(self):
        """A state the renderer does not know would silently fall back to idle."""
        from petui.openo import LAMP
        self.assertIn("closed", LAMP, "the closed state needs a lamp colour")


# ---------------------------------------------------------------------------
class TestStaleClaims(unittest.TestCase):
    """A window reopening is not evidence that anything is running.

    This was the real cause of the remaining flash, and it survived two earlier
    rounds of fixing.  The session records in memory are the state as it was when
    the OpenCode window closed; the service keeps reporting them unchanged, so
    they still look live long afterwards.  The moment the window came back the
    pet rendered them again - a card saying "waiting for your authorization"
    with its timer frozen at the value it had before the close, while the API's
    own ``time.idle`` was three minutes in the past.  Captured on screen: the
    same card, with the same frozen 3 m 26 s, in every frame from 3 ms to 692 ms
    after the click.
    """

    def _live(self, session_id="a", state=WAITING):
        record = reducer.new_session(session_id, 1_000)
        record.update({"state": state, "running": True, "permission": "bash",
                       "started": 1_000, "updated": 1_000})
        return record

    def test_closing_the_window_drops_the_claims(self):
        s = {"a": self._live()}
        self.assertEqual(reducer.invalidate_live(s), 1)
        self.assertFalse(s["a"]["verified"])

    def test_invalidate_is_idempotent(self):
        s = {"a": self._live()}
        reducer.invalidate_live(s)
        self.assertEqual(reducer.invalidate_live(s), 0,
                         "a record already dropped must not be counted again")

    def test_a_dropped_record_is_not_shown_when_the_window_returns(self):
        s = {"a": self._live()}
        reducer.invalidate_live(s)
        self.assertEqual(reducer.visible(s, 1_000, app_open=True), [],
                         "the pre-close claim must not reappear on reopen")
        self.assertEqual(reducer.mood(s, 1_000, app_open=True)["state"], IDLE,
                         "and the pet must not claim to be waiting again")

    def test_a_reconcile_re_verifies(self):
        """Dropping the claim is only safe if the next read restores it."""
        s = {"a": self._live()}
        reducer.invalidate_live(s)
        reducer.reconcile(s, {"a": {"type": "running"}},
                          [{"id": "a", "outcome": "succeeded"}], {}, 5_000)
        self.assertTrue(s["a"]["verified"])
        self.assertEqual(len(reducer.visible(s, 5_000, app_open=True)), 1)

    def test_a_live_event_also_re_verifies(self):
        s = {"a": self._live()}
        reducer.invalidate_live(s)
        reducer.apply_event(s, ev("session.step.started", sessionID="a"), 6_000)
        self.assertTrue(s["a"]["verified"], "a live event is proof of life")

    def test_the_round_trip_leaves_nothing_behind(self):
        """Close, reopen, and the truth is whatever the API says - not the past."""
        s = {}
        reducer.reconcile(s, {"a": {"type": "running"}},
                          [{"id": "a", "outcome": "succeeded"}], {}, 1_000)
        self.assertTrue(s["a"]["running"])
        reducer.invalidate_live(s)
        # The app comes back, but the task is long finished.
        reducer.reconcile(s, {}, [{"id": "a", "outcome": "succeeded"}], {}, 9_000)
        self.assertFalse(s["a"]["running"])
        self.assertNotEqual(s["a"]["state"], WAITING)

    def test_the_frozen_timer_case(self):
        """The exact shape that was photographed: running, but long idle."""
        s = {"a": self._live(state=EXECUTING)}
        s["a"]["tool"] = "build"
        reducer.invalidate_live(s)
        # Reopened: nothing is claimed, so the frozen "3 m 26 s" cannot show.
        self.assertEqual(reducer.visible(s, 200_000, app_open=True), [])
        self.assertEqual(reducer.mood(s, 200_000, app_open=True)["state"], IDLE)


class TestLivenessLatency(unittest.TestCase):
    """How *fast* the pet notices the window is gone.

    The first attempt at this was correct but useless in practice: liveness was
    read once per 2.5 s reconcile, and only after the API calls.  Measured, the
    window was gone in 100 ms and the stale "waiting for authorization" card was
    still on screen 1.7 s later.  A closing app is exactly when the pet must stop
    claiming work is happening, so these pin the latency, not just the rule.
    """

    def test_a_visible_window_is_ready(self):
        from petcore import client as client_mod
        original = client_mod.opencode_window_visible
        client_mod.opencode_window_visible = lambda: True
        try:
            self.assertTrue(client_mod.window_ready())
        finally:
            client_mod.opencode_window_visible = original

    def test_a_lingering_process_does_not_make_a_closed_window_ready(self):
        """Regression: the process fallback re-introduced the whole bug.

        During shutdown the window disappears ~1.5 s before ``OpenCode.exe``
        does.  A fallback that believed "the process has been up a while"
        reported *open* for that whole 1.5 s, so the stale "waiting for
        authorization" card stayed on screen - the exact bug it was added to
        guard against a future window-title change.
        """
        from petcore import client as client_mod
        original = client_mod.opencode_window_visible
        client_mod.opencode_window_visible = lambda: False
        try:
            self.assertFalse(client_mod.window_ready(process_up=True),
                             "no window means closed, whatever the process says")
            self.assertFalse(client_mod.window_ready(process_up=False))
        finally:
            client_mod.opencode_window_visible = original

    def test_the_process_is_used_only_when_the_window_cannot_be_examined(self):
        """``None`` means "could not look", which is not the same as "no"."""
        from petcore import client as client_mod
        original = client_mod.opencode_window_visible
        client_mod.opencode_window_visible = lambda: None
        try:
            self.assertTrue(client_mod.window_ready(process_up=True),
                            "if we cannot look, the process is all we have")
            self.assertFalse(client_mod.window_ready(process_up=False))
        finally:
            client_mod.opencode_window_visible = original

    def test_the_ui_rechecks_liveness_before_the_early_return(self):
        """``refresh`` returns early when nothing changed - which is exactly the
        case when a window closing still has to force a redraw."""
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("def refresh(")
        body = source[start:source.index("def log_mood(", start)]
        liveness_at = body.index("observer.liveness()")
        early_at = body.index('if not snapshot["changed"]')
        self.assertLess(liveness_at, early_at,
                        "liveness must be checked before the early return, or "
                        "a closed window changes nothing and nothing redraws")

    def test_only_a_real_transition_forces_a_rebuild(self):
        """The 500 ms tick must not defeat the "nothing moved" optimisation."""
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("def refresh(")
        body = source[start:source.index("def log_mood(", start)]
        self.assertIn("if app_open_now != last_app_open:", body)
        self.assertIn("observer.touch()", body)

    def test_routine_refreshes_are_coalesced_but_urgent_ones_are_not(self):
        """Event traffic must not delay hiding a stale card.

        A busy task produces a steady stream of SSE events, and each one rebuilt
        the cards and re-rendered the pet.  That work queued ahead of the
        liveness change, so the card lingered behind traffic that was not urgent.
        Routine refreshes are collapsed; the timer and the liveness watcher must
        both bypass the collapse.
        """
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("REFRESH_COALESCE_S", source)
        self.assertIn("def refresh(urgent", source,
                      "refresh must be able to skip the coalescing")
        self.assertIn("refresh(urgent=True)", source,
                      "the timer and the liveness watcher must go straight through")

    def test_a_closed_window_hides_cards_without_the_dirty_flag(self):
        """The intermittent one-second flash.

        Hiding the card used to sit *after* ``if not snapshot["changed"]: return``.
        That flag is consumed by whichever path reads the snapshot first, so a
        close could be swallowed entirely and the "waiting for authorization"
        card stayed up.  Measured at 50 ms on some runs and 1.6 s on others -
        the tell that the race, not the latency, was the problem.

        A closed window is a fact about the world, not a rendering of session
        data, so it has to be applied before that early return.
        """
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("def refresh(")
        body = source[start:source.index("def log_mood(", start)]
        closed_at = body.index("if not app_open_now:")
        early_at = body.index('if not snapshot["changed"]')
        self.assertLess(closed_at, early_at,
                        "a closed window must be handled before the early "
                        "return, or a consumed dirty flag keeps the card up")
        hide_at = body.index("cards.hide()", closed_at)
        self.assertLess(hide_at, early_at)

    def test_reconcile_reads_liveness_before_the_api_calls(self):
        """Otherwise a window that closes mid-pass is paired with a flag read
        after the fact, and the pass records live work for a closed app."""
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("def _reconcile(")
        body = source[start:source.index("def _mark_all_idle(", start)]
        self.assertLess(body.index("self.liveness("),
                        body.index("pending_permissions"),
                        "liveness must be read first")


# ---------------------------------------------------------------------------
class TestRetention(unittest.TestCase):
    def test_visible_keeps_unread_and_running(self):
        s = {}
        done = reducer.new_session("d")
        done.update({"state": DONE, "acknowledged": False})
        acked = reducer.new_session("a")
        acked.update({"state": DONE, "acknowledged": True})
        running = reducer.new_session("r")
        running.update({"state": EXECUTING, "running": True})
        s.update({"d": done, "a": acked, "r": running})
        ids = [r["id"] for r in reducer.visible(s)]
        self.assertIn("d", ids)
        self.assertIn("r", ids)
        self.assertNotIn("a", ids)

    def test_acknowledge_is_refused_while_running(self):
        s = {"r": dict(reducer.new_session("r"), running=True, state=EXECUTING)}
        self.assertFalse(reducer.acknowledge(s, "r"))
        self.assertTrue(s["r"]["acknowledged"] is False)

    def test_acknowledge_clears_the_finished_card(self):
        s = {"d": dict(reducer.new_session("d"), state=DONE)}
        self.assertTrue(reducer.acknowledge(s, "d"))
        self.assertEqual(s["d"]["state"], IDLE)
        self.assertEqual(reducer.visible(s), [])

    def test_prune_keeps_what_opencode_still_lists(self):
        s = {}
        for name in ("keep", "gone"):
            record = reducer.new_session(name)
            record.update({"state": DONE, "acknowledged": True})
            s[name] = record
        reducer.prune(s, keep_ids=["keep"])
        self.assertEqual(list(s), ["keep"])

    def test_prune_caps_the_number_of_records(self):
        s = {}
        for index in range(80):
            s[str(index)] = reducer.new_session(str(index))
        reducer.prune(s, keep_ids=[], max_records=60)
        self.assertLessEqual(len(s), 60)


# ---------------------------------------------------------------------------
class TestNotices(unittest.TestCase):
    def test_notice_fires_once_per_transition(self):
        s = {}
        record = reducer.new_session("a")
        record["state"] = WAITING
        s["a"] = record
        self.assertEqual(reducer.new_notice(s, record), "notif.waiting_title")
        self.assertEqual(reducer.new_notice(s, record), "")

    def test_new_step_re_notifies(self):
        s = {}
        record = reducer.new_session("a")
        record.update({"state": EXECUTING, "running": True, "steps": 1})
        s["a"] = record
        self.assertEqual(reducer.new_notice(s, record), "")
        record["steps"] = 2
        self.assertEqual(reducer.new_notice(s, record), "")

    def test_done_notice_then_quiet(self):
        s = {}
        record = reducer.new_session("a")
        record["state"] = DONE
        s["a"] = record
        self.assertEqual(reducer.new_notice(s, record), "notif.done_title")
        self.assertEqual(reducer.new_notice(s, record), "")


# ---------------------------------------------------------------------------
class TestI18n(unittest.TestCase):
    def test_both_languages_define_the_same_keys(self):
        self.assertEqual(set(i18n.TABLES["zh"]), set(i18n.TABLES["en"]))

    def test_every_key_renders_in_both_languages(self):
        for key in i18n.keys():
            for lang in i18n.LANGS:
                value = _fill(key, lang)
                self.assertIsInstance(value, str)
                self.assertTrue(value)
                self.assertNotIn("{", value, "%s/%s left a hole" % (key, lang))

    def test_templates_never_use_numbered_placeholders(self):
        """Numbered placeholders are how "{tool}" once shipped to the screen."""
        for lang in i18n.LANGS:
            for key, value in i18n.TABLES[lang].items():
                branches = value if isinstance(value, (list, tuple)) else (value,)
                for branch in branches:
                    for name in re.findall(r"\{(\w*)\}", branch):
                        self.assertFalse(name.isdigit(),
                                         "%s/%s uses a numbered placeholder"
                                         % (key, lang))

    def test_the_two_languages_agree_on_placeholder_names(self):
        for key in i18n.keys():
            zh = i18n.TABLES["zh"][key]
            en = i18n.TABLES["en"][key]
            zh_names = [set(re.findall(r"\{(\w+)\}", b))
                        for b in (zh if isinstance(zh, (list, tuple)) else (zh,))]
            en_names = [set(re.findall(r"\{(\w+)\}", b))
                        for b in (en if isinstance(en, (list, tuple)) else (en,))]
            self.assertEqual(len(zh_names), len(en_names), key)
            for a, b in zip(zh_names, en_names):
                self.assertEqual(a, b, "%s: zh%s vs en%s" % (key, a, b))

    def test_language_normalisation(self):
        for value in ("zh", "ZH-CN", "zh_CN", "zh-Hans", "Chinese"):
            self.assertEqual(i18n.normalize_language(value), "zh")
        for value in ("en", "en-GB", "en_US", "English"):
            self.assertEqual(i18n.normalize_language(value), "en")
        for value in (None, "", "fr", 42):
            self.assertIn(i18n.normalize_language(value), i18n.LANGS)

    def test_unknown_key_degrades_to_the_key(self):
        self.assertEqual(i18n.t("definitely.not.a.key", lang="zh"),
                         "definitely.not.a.key")

    def test_plural_forms(self):
        self.assertIn("1 step", i18n.t("card.steps", n=1, lang="en"))
        self.assertIn("5 steps", i18n.t("card.steps", n=5, lang="en"))
        self.assertIn("1 步", i18n.t("card.steps", n=1, lang="zh"))
        self.assertIn("5 步", i18n.t("card.steps", n=5, lang="zh"))

    def test_named_arguments(self):
        self.assertIn("project", i18n.t("notif.done_body", project="project",
                                        steps="1 step", lang="en"))

    def test_every_card_string_fills_its_placeholders(self):
        """The exact call shapes the card stack uses."""
        cases = [
            (("card.more",), {"n": 3}),
            (("card.more_detail",), {"total": 4, "running": 2}),
            (("card.steps",), {"n": 7}),
            (("card.tool",), {"tool": "shell"}),
            (("card.permission",), {"action": "bash"}),
            (("card.longtask",), {"duration": "12m"}),
            (("about.tray_text",), {"state": "Idle"}),
            (("notif.waiting_body",), {"project": "p", "action": "bash"}),
            (("notif.done_body",), {"project": "p", "steps": "3 steps"}),
            (("hover.connected",), {"version": "2.0.31"}),
        ]
        for lang in i18n.LANGS:
            for args, named in cases:
                value = i18n.t(args[0], lang=lang, **named)
                self.assertNotIn("{", value, "%s/%s" % (args[0], lang))
                self.assertTrue(value.strip())

    def test_duration_and_age(self):
        self.assertEqual(i18n.format_duration(5, lang="en"), "5s")
        self.assertEqual(i18n.format_duration(65, lang="en"), "1m 5s")
        self.assertEqual(i18n.format_duration(3660, lang="en"), "1h 1m")
        self.assertEqual(i18n.format_duration(-5, lang="en"), "0s")
        self.assertEqual(i18n.format_age(1000, lang="en"), "just now")
        self.assertEqual(i18n.format_age(120_000, lang="en"), "2 min ago")
        self.assertEqual(i18n.format_age(7_200_000, lang="en"), "2 h ago")
        self.assertEqual(i18n.format_age(3 * 86_400_000, lang="en"), "3 d ago")
        self.assertEqual(i18n.format_age(0, lang="zh"), "刚刚")

    def test_states_exist_for_every_reducer_state(self):
        for state in reducer.STATES:
            self.assertIn("state." + state, i18n.keys(), state)


# ---------------------------------------------------------------------------
class TestSettings(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "pet_state.json")
            from petcore.settings import Settings
            settings = Settings(path)
            settings.set("scale", 1.5)
            settings.set_lang("en")
            settings.save()
            again = Settings(path)
            self.assertEqual(again["scale"], 1.5)
            self.assertEqual(again.lang, "en")
            again.set_lang("zh")
            self.assertEqual(again.lang, "zh")

    def test_settings_file_is_valid_json_and_utf8(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "pet_state.json")
            from petcore.settings import Settings
            settings = Settings(path)
            settings.set("silent", True)
            settings.save()
            with open(path, "rb") as handle:
                raw = handle.read()
            json.loads(raw.decode("utf-8"))
            self.assertEqual(len(raw), len(raw))


# ---------------------------------------------------------------------------
class TestEndpointHelpers(unittest.TestCase):
    def test_password_reading_is_forgiving(self):
        import json
        import tempfile
        from petcore import endpoint
        with tempfile.TemporaryDirectory() as folder:
            missing = os.path.join(folder, "nope.json")
            self.assertEqual(endpoint.read_password(missing), "")
            good = os.path.join(folder, "service.json")
            with open(good, "w", encoding="utf-8") as handle:
                json.dump({"password": "hunter2"}, handle)
            self.assertEqual(endpoint.read_password(good), "hunter2")
            broken = os.path.join(folder, "broken.json")
            with open(broken, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            self.assertEqual(endpoint.read_password(broken), "")

    def test_endpoint_auth_header(self):
        from petcore.endpoint import Endpoint
        header = Endpoint("http://127.0.0.1:1", "pw").auth_header()
        self.assertTrue(header.startswith("Basic "))
        import base64
        self.assertEqual(base64.b64decode(header[6:]).decode(),
                         "opencode:pw")


# ---------------------------------------------------------------------------
class TestBridgeValidation(unittest.TestCase):
    def test_state_vocabulary(self):
        from petcore.bridge import VALID_STATES
        self.assertEqual(VALID_STATES,
                         ("idle", "thinking", "executing", "waiting",
                          "done", "error"))


# ---------------------------------------------------------------------------
# Renderer tests.  They need a QApplication, so they are only defined when
# PyQt5 is importable - the rest of the suite runs anywhere.
# ---------------------------------------------------------------------------
_QT_APP = None            # must outlive every test, or Qt crashes at exit


def _define_renderer_tests():
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import QImage, QPainter
    from PyQt5.QtWidgets import QApplication

    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication(["opencode-pet-tests"])

    WIDTH = 132

    def render(state, t=900.0):
        """Render one state at the pet's real on-screen size."""
        from petui.openo import DESIGN_H, DESIGN_W, OpenO
        height = int(round(WIDTH * DESIGN_H / DESIGN_W))
        image = QImage(WIDTH, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        OpenO().paint(painter, QRectF(0, 0, WIDTH, height), state, t)
        painter.end()
        return image

    def sample(image, design_x, design_y):
        """Read a pixel using the design coordinate system, not pixel indices."""
        from petui.openo import DESIGN_H, DESIGN_W
        return image.pixelColor(int(round(design_x * image.width() / DESIGN_W)),
                                int(round(design_y * image.height() / DESIGN_H)))

    def opaque_pixels(image, step=3):
        return sum(1
                   for y in range(0, image.height(), step)
                   for x in range(0, image.width(), step)
                   if image.pixelColor(x, y).alpha() > 20)

    class TestRenderer(unittest.TestCase):
        def test_every_state_draws_something(self):
            for state in reducer.STATES:
                image = render(state)
                self.assertFalse(image.isNull(), state)
                self.assertGreater(opaque_pixels(image), 400, state)

        def test_body_is_permanently_charcoal(self):
            """Black/white/grey never change; colour is only spent on the lamp.

            (80, 60) is inside the O's left wall - body only, never the face,
            never the lamp, whatever the state.
            """
            from petui.openo import BODY
            for state in reducer.STATES:
                for t in (0.0, 700.0, 1800.0):
                    colour = sample(render(state, t), 80, 60).name()
                    self.assertIn(colour, (BODY.name(), "#323232"),
                                  "%s @ %d" % (state, t))

        def test_face_panel_is_greyscale_in_every_state(self):
            for state in reducer.STATES:
                for design_point in ((120, 60), (120, 120)):
                    colour = sample(render(state, 300.0), *design_point)
                    spread = max(colour.red(), colour.green(), colour.blue()) - \
                        min(colour.red(), colour.green(), colour.blue())
                    self.assertLess(spread, 6,
                                    "%s at %s should stay greyscale"
                                    % (state, design_point))

        def test_face_panel_has_the_two_tones_of_the_logo(self):
            from petui.openo import FACE_LOW, FACE_TOP
            image = render("idle", 900.0)
            self.assertEqual(sample(image, 120, 62).name(), FACE_TOP.name())
            self.assertEqual(sample(image, 120, 124).name(), FACE_LOW.name())

        def test_lamp_carries_the_state_colour(self):
            from petui.openo import OpenO
            char = OpenO()
            waiting = char._lamp_colour(WAITING, 0)
            done = char._lamp_colour(DONE, 0)
            idle = char._lamp_colour(IDLE, 0)
            self.assertNotEqual(waiting.name(), done.name())
            self.assertNotEqual(waiting.name(), idle.name())

        def test_waiting_lamp_blinks(self):
            from petui.openo import OpenO
            char = OpenO()
            alphas = {char._lamp_colour(WAITING, t).alpha()
                      for t in range(0, 700, 25)}
            self.assertGreater(len(alphas), 1, "the waiting lamp must pulse")

        def test_drag_leans_toward_the_cursor(self):
            from petui.openo import OpenO
            char = OpenO()
            self.assertGreater(char._pose("drag", 0, drag_vx=900)["tilt"], 0)
            self.assertLess(char._pose("drag", 0, drag_vx=-900)["tilt"], 0)

        def test_drag_tucks_the_feet(self):
            from petui.openo import OpenO
            char = OpenO()
            self.assertLess(char._pose("drag", 0)["dy"], 0)

        def test_contact_sheet_has_a_cell_for_every_state(self):
            """Every state must actually appear in the sheet, not be overpainted.

            The state list is read from the state machine rather than repeated
            here.  When this test carried its own copy it passed happily while
            ``offline`` had no picture in the documentation at all, because the
            sheet and the test agreed with each other and with nobody else.
            """
            from petcore.reducer import STATES
            from petui.openo import DESIGN_H, DESIGN_W, render_contact_sheet
            sheet = render_contact_sheet()
            self.assertFalse(sheet.isNull())
            image = sheet.toImage()
            cell_w = 132
            gap, label_h = 18, 26
            cell_h = int(round(cell_w * DESIGN_H / DESIGN_W))
            cols = 4
            for index, state in enumerate(STATES):
                x = gap + (index % cols) * (cell_w + gap)
                y = gap + (index // cols) * (cell_h + label_h + gap)
                dark = sum(1
                           for yy in range(y, y + cell_h)
                           for xx in range(x, x + cell_w)
                           if image.pixelColor(xx, yy).lightness() < 90)
                self.assertGreater(dark, 1000,
                                   "cell %d (%s) is blank" % (index, state))

    return TestRenderer


# ---------------------------------------------------------------------------
class TestBridgeServer(unittest.TestCase):
    """The loopback event bridge, exercised over a real socket."""

    def setUp(self):
        import socket
        from petcore import bridge as bridge_mod

        # Ask the OS for a free port instead of guessing, so the test never
        # collides with a pet that is already running on 8767.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        self.seen = []
        self.bridge = bridge_mod.EventBridge(on_event=self.seen.append, port=port)
        self.port = self.bridge.start()
        self.addCleanup(self.bridge.stop)

    def post(self, payload):
        import json
        import urllib.error
        import urllib.request
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:%d/event" % self.port, data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_it_binds_a_loopback_port(self):
        self.assertGreater(self.port, 0)

    def test_a_valid_event_reaches_the_callback(self):
        self.assertEqual(self.post({"state": "executing", "task": "x"}), 200)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0]["state"], "executing")

    def test_an_unknown_state_is_rejected(self):
        self.assertEqual(self.post({"state": "wat"}), 400)
        self.assertEqual(self.seen, [])

    def test_an_empty_body_is_rejected(self):
        self.assertEqual(self.post({}), 400)

    def test_health_endpoint(self):
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port,
                                    timeout=3) as response:
            self.assertEqual(response.status, 200)


# ---------------------------------------------------------------------------
class TestModuleSelfSufficiency(unittest.TestCase):
    """Regression: a helper crashed on its own missing import.

    ``_raise`` used ``ctypes`` without importing it in its own scope - it
    worked while only reachable through ``_raise_existing_window``, which did
    import it, so nothing failed until the raise path was called directly from
    a click.  Then every click raised ``NameError`` out of a Qt slot, which
    aborted the event loop and killed the pet.

    So: every module's globals must include every name it uses.
    """

    def _modules(self):
        import importlib
        names = ["petcore.bridge", "petcore.client", "petcore.endpoint",
                 "petcore.i18n", "petcore.paths", "petcore.reducer",
                 "petcore.settings"]
        return [(n, importlib.import_module(n)) for n in names]

    def test_no_module_uses_an_unimported_global(self):
        import builtins
        import types
        problems = []
        for name, module in self._modules():
            path = getattr(module, "__file__", None)
            if not path:
                continue
            with open(path, "r", encoding="utf-8") as handle:
                tree = compile(handle.read(), path, "exec", ast.PyCF_ONLY_AST)
            # Names bound anywhere in the module (imports, defs, assignments)
            bound = set(dir(module)) | set(dir(builtins))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        bound.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        bound.add(alias.asname or alias.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)):
                    bound.add(node.name)
                elif isinstance(node, ast.Name) and isinstance(node.ctx,
                                                               ast.Store):
                    bound.add(node.id)
                elif isinstance(node, ast.arg):
                    bound.add(node.arg)
                elif isinstance(node, ast.ExceptHandler) and node.name:
                    bound.add(node.name)
                elif isinstance(node, (ast.comprehension,)):
                    pass
            # Only module-level *reads* can be wrong: a name never bound
            # anywhere is a NameError waiting to happen.
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx,
                                                              ast.Load):
                    if node.id not in bound and not node.id.isupper():
                        problems.append("%s:%d %s"
                                        % (os.path.basename(path), node.lineno,
                                           node.id))
        self.assertEqual(problems, [], "unbound names: %s" % sorted(set(problems)))

    def test_a_signal_guard_forwards_its_arguments(self):
        """Regression: the click guard swallowed the card's session id.

        ``once_per_click`` was declared as ``Callable[[], None]`` and called
        ``slot()``, so every card click raised ``TypeError: ... missing 1
        required positional argument`` - from inside a Qt slot, which aborts
        the event loop and kills the pet.  A wrapper must forward arguments.
        """
        seen = []

        def make_guard():
            last = [0.0]

            def once_per_click(slot):
                def guarded(*args, **kwargs):
                    import time as _t
                    now = _t.time()
                    if now - last[0] < 0.4:
                        return
                    last[0] = now
                    slot(*args, **kwargs)
                return guarded
            return once_per_click

        once = make_guard()
        slot = lambda session_id: seen.append(session_id)      # noqa: E731
        guarded = once(slot)

        guarded("ses_abc")
        self.assertEqual(seen, ["ses_abc"], "the argument must be forwarded")
        guarded("ses_abc")          # too soon: suppressed
        self.assertEqual(seen, ["ses_abc"])

    def test_a_zero_argument_slot_still_works(self):
        seen = []
        last = [0.0]

        def once_per_click(slot):
            def guarded(*args, **kwargs):
                import time as _t
                now = _t.time()
                if now - last[0] < 0.4:
                    return
                last[0] = now
                slot(*args, **kwargs)
            return guarded

        guarded = once_per_click(lambda: seen.append("clicked"))
        guarded()
        self.assertEqual(seen, ["clicked"])

    def test_the_pet_declares_a_forwarding_guard(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("def once_per_click(slot) -> Callable[..., None]:", source,
                      "the guard must accept and forward arbitrary arguments")
        self.assertIn("slot(*args, **kwargs)", source)

    def test_the_raise_helper_imports_what_it_uses(self):
        from petcore import client as client_mod
        source = _source_of(client_mod._raise)
        self.assertIn("import ctypes", source)
        self.assertIn("os.name", source)

    def test_every_helper_is_reachable_without_its_caller(self):
        """Calling a helper must not depend on an import in *its caller*.

        ``ctypes`` is imported inside a function, so its siblings are invisible
        from any other function - which is exactly how ``_raise`` came to use
        an ``os`` that only ``_raise_existing_window`` had imported.  A
        module-level import does not help either: the whole point is that the
        helper may be called from a path its caller never takes.

        So each helper is analysed in isolation, with only the module's real
        globals plus its own imports and locals.
        """
        import inspect
        from petcore import client as client_mod
        for func in (client_mod._raise, client_mod._raise_existing_window,
                     client_mod.focus_desktop, client_mod.window_title_score):
            source = inspect.getsource(func)
            tree = compile(source, "<f>", "exec", ast.PyCF_ONLY_AST)
            local = set(dir(builtins)) | set(dir(client_mod))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        local.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        local.add(alias.asname or alias.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)):
                    local.add(node.name)
                elif isinstance(node, ast.Name) and isinstance(node.ctx,
                                                               ast.Store):
                    local.add(node.id)
                elif isinstance(node, ast.arg):
                    local.add(node.arg)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx,
                                                              ast.Load):
                    if node.id not in local:
                        self.fail("%s uses unbound %r"
                                  % (func.__name__, node.id))


class TestSingleInstanceLock(unittest.TestCase):
    """Two pets share two windows, a tray icon and a bridge port.

    A second copy therefore cannot be a degraded mode - it loses the port race
    and sits there looking broken.  The lock has to make the second start a
    no-op, and it has to survive a crashed predecessor.
    """

    def setUp(self):
        import tempfile
        self._folder = tempfile.mkdtemp(prefix="pet-lock-")
        self._original = paths_state_dir
        self._original_lock_dir = None
        import petcore.paths
        petcore.paths.state_dir = lambda: self._folder
        # The locks moved out of ``state_dir()`` and into ``lock_dir()`` so
        # that every copy of the project on the machine shares them.  The
        # redirection has to follow them, or these tests would write to the real
        # ``~/.config/opencode/pet-locks`` and collide with the installed pet.
        self._original_lock_dir = petcore.paths.lock_dir
        petcore.paths.lock_dir = lambda: self._folder
        self.addCleanup(self._restore)

    def _restore(self):
        import shutil
        import petcore.paths
        petcore.paths.state_dir = self._original
        petcore.paths.lock_dir = self._original_lock_dir
        shutil.rmtree(self._folder, ignore_errors=True)

    def test_a_second_claim_is_refused(self):
        from petcore.lock import SingleInstance
        first = SingleInstance("pet.lock")
        self.assertTrue(first.acquire())
        second = SingleInstance("pet.lock")
        self.assertFalse(second.acquire())
        self.assertEqual(second.holder, os.getpid())
        first.release()

    def test_releasing_allows_a_fresh_claim(self):
        from petcore.lock import SingleInstance
        first = SingleInstance("pet.lock")
        first.acquire()
        first.release()
        self.assertFalse(os.path.exists(first.path))
        second = SingleInstance("pet.lock")
        self.assertTrue(second.acquire())
        second.release()

    def test_a_lock_from_a_dead_process_is_reclaimed(self):
        from petcore.lock import SingleInstance
        path = SingleInstance("pet.lock").path
        with open(path, "w", encoding="ascii") as handle:
            handle.write("999999999")          # a PID that cannot exist
        guard = SingleInstance("pet.lock")
        self.assertTrue(guard.acquire(), "a stale lock must not block start-up")
        guard.release()

    def test_a_lock_held_by_a_live_peer_blocks_a_fake_holder(self):
        from petcore.lock import SingleInstance
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(8)"])
        try:
            path = SingleInstance("pet.lock").path
            with open(path, "w", encoding="ascii") as handle:
                handle.write(str(child.pid))
            guard = SingleInstance("pet.lock")
            self.assertFalse(guard.acquire(),
                             "a live peer must keep the lock")
            self.assertEqual(guard.holder, child.pid)
        finally:
            child.terminate()
            child.wait(timeout=8)

    def test_release_never_drops_someone_elses_lock(self):
        from petcore.lock import SingleInstance
        guard = SingleInstance("pet.lock")
        with open(guard.path, "w", encoding="ascii") as handle:
            handle.write("4242")
        guard._owned = True                    # pretend we claimed it
        guard.release()
        self.assertTrue(os.path.exists(guard.path),
                        "must not unlink a lock we do not own")

    def test_the_pet_takes_a_lock_and_a_second_start_exits(self):
        """The real entry point: one pet, and the second says so and stops."""
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env["OPENCODE_PET_STATE_DIR"] = self._folder
        # The locks are shared machine-wide now, so this test has to redirect
        # them too - otherwise it collides with the real, running pet.  It is an
        # env var rather than a monkeypatch because the fixtures are real
        # subprocesses.
        env["OPENCODE_PET_LOCK_DIR"] = self._folder
        env["PYTHONIOENCODING"] = "utf-8"
        # A first process that holds the lock and waits.
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys,time;sys.path.insert(0,%r);"
             "from petcore.lock import SingleInstance;"
             "g=SingleInstance('pet.lock');"
             "print('held' if g.acquire() else 'busy', flush=True);"
             "time.sleep(20)" % root],
            stdout=subprocess.PIPE, env=env, text=True)
        try:
            line = holder.stdout.readline().strip()
            self.assertEqual(line, "held", "the fixture must hold the lock")

            result = subprocess.run(
                [sys.executable, os.path.join(root, "opencode_pet.py"),
                 "--always-show"],
                capture_output=True, env=env, timeout=60, text=True,
                encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0,
                             "a second pet must exit quietly")
            self.assertIn("already running", (result.stdout or "")
                          + (result.stderr or ""))
        finally:
            holder.terminate()
            holder.wait(timeout=10)

    def test_the_sentinel_and_the_pet_use_different_locks(self):
        """They must coexist: the sentinel is what starts the pet.

        Sharing one lock would mean the sentinel's own claim blocks the pet it
        just launched, and the desktop would simply stay empty.
        """
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sources = {}
        for key, filename in (("sentinel", "pet_watcher.py"),
                              ("pet", "opencode_pet.py")):
            with open(os.path.join(root, filename), "r",
                      encoding="utf-8") as handle:
                sources[key] = ast.parse(handle.read())

        # What must differ is the lock each one *claims*, not every name it
        # mentions: the sentinel legitimately knows the pet's lock name,
        # because it has to read and clear it.
        self.assertEqual(_claimed_locks(sources["pet"]), {PET_LOCK},
                         "the pet must claim pet.lock and nothing else")
        self.assertEqual(_claimed_locks(sources["sentinel"]), {SENTINEL_LOCK},
                         "the sentinel must claim sentinel.lock and nothing "
                         "else")
        # The sentinel must know the pet's lock name too, to read and clear it.
        self.assertIn(PET_LOCK, _lock_names(sources["sentinel"]))

    def test_the_sentinel_stops_the_pet_by_its_lock(self):
        """Both paths must consult the pet's lock.

        A command-line match alone is not enough: the pet is started as
        ``opencode_pet.py``, ``...\\opencode_pet.py`` or with extra flags
        depending on who launched it, and the argv the sentinel sees is not
        guaranteed to be the one it can match.
        """
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "pet_watcher.py"), "r",
                  encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        functions = {n.name: n for n in tree.body
                     if isinstance(n, ast.FunctionDef)}
        constants = _module_lock_constants(tree)

        def uses_pet_lock(name):
            node = functions[name]
            # Resolve the constant names it reads, including via a helper.
            called = {c.func.id for c in ast.walk(node)
                      if isinstance(c, ast.Call)
                      and isinstance(c.func, ast.Name)}
            if "_pet_lock_holder" in called:
                helper = functions.get("_pet_lock_holder")
                self.assertIsNotNone(helper)
                used = {n.id for n in ast.walk(helper) if isinstance(n, ast.Name)}
                return PET_LOCK_NAME_ID in used
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            return PET_LOCK_NAME_ID in used

        for name in ("kill_pet", "pet_running"):
            self.assertTrue(uses_pet_lock(name),
                            "%s must read the pet's lock, not only the argv"
                            % name)

        # And the helper it leans on really does point at pet.lock.
        self.assertEqual(constants.get(PET_LOCK_NAME_ID), PET_LOCK)

    def test_status_reports_a_duplicate(self):
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "pet_watcher.py"), "r",
                  encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        main_fn = next(n for n in tree.body
                       if isinstance(n, ast.FunctionDef) and n.name == "main")
        self.assertIn("DUPLICATE", ast.dump(main_fn),
                      "--status must say when there is more than one pet")


PET_LOCK = "pet.lock"
SENTINEL_LOCK = "sentinel.lock"
PET_LOCK_NAME_ID = "PET_LOCK_NAME"


def _module_lock_constants(tree) -> dict:
    """Map module-level ``NAME = "x.lock"`` constants to their values."""
    found = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 \
                and isinstance(statement.targets[0], ast.Name) \
                and isinstance(statement.value, ast.Constant) \
                and isinstance(statement.value.value, str) \
                and statement.value.value.endswith(".lock"):
            found[statement.targets[0].id] = statement.value.value
    return found


def _claimed_locks(tree) -> set:
    """The lock names a module actually claims for itself.

    A claim is a constant named ``*LOCK*`` handed to ``SingleInstance(...)``.
    Merely mentioning a name - the sentinel reading ``pet.lock`` - is not a
    claim, and must not look like one.
    """
    constants = _module_lock_constants(tree)
    claimed = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "SingleInstance"
                and node.args):
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant):
            claimed.add(argument.value)
        elif isinstance(argument, ast.Name) and argument.id in constants:
            claimed.add(constants[argument.id])
    return claimed


def _lock_names(node) -> set:
    """Every lock name a module body declares or refers to."""
    constants = _module_lock_constants(node) \
        if isinstance(node, ast.Module) else {}
    names = {n.value for n in ast.walk(node)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.endswith(".lock")}
    names |= set(constants.values())
    used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    names |= {constants[n] for n in used if n in constants}
    return names


class TestSettingsApi(unittest.TestCase):
    """Regression: the pet died on every drag.

    ``persist_pos`` called ``Settings.update(..., save_pos=False)`` but the
    method's parameter is ``save``.  The resulting TypeError was raised inside a
    Qt signal handler, which aborts the whole Qt event loop - so releasing the
    mouse after a drag killed the process, and the sentinel dutifully
    relaunched it.  That is what "the pet restarts when I drag it" was.
    """

    def test_update_accepts_save_false(self):
        import tempfile
        from petcore.settings import Settings
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(os.path.join(folder, "pet_state.json"))
            settings.update({"x": 10, "y": 20}, save=False)
            self.assertFalse(os.path.exists(settings.path),
                             "save=False must not write the file")
            settings.save()
            self.assertEqual(settings["x"], 10)

    def test_set_accepts_save_false(self):
        import tempfile
        from petcore.settings import Settings
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(os.path.join(folder, "pet_state.json"))
            settings.set("scale", 1.5, save=False)
            self.assertFalse(os.path.exists(settings.path))
            self.assertEqual(settings["scale"], 1.5)

    def test_the_call_shapes_the_pet_actually_uses_are_all_valid(self):
        """Signature check by name, so a renamed parameter cannot slip through."""
        import inspect
        from petcore.settings import Settings
        for name in ("set", "update"):
            params = inspect.signature(getattr(Settings, name)).parameters
            self.assertIn("save", params, name)
            self.assertNotIn("save_pos", params, name)

    def test_persist_pos_in_the_pet_uses_the_valid_keywords(self):
        """Static check of the real call sites, not a hand-written copy."""
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in ("set", "update"):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "settings":
                continue
            for keyword in node.keywords:
                if keyword.arg not in ("save",):
                    bad.append("%s=%s at line %d"
                               % (keyword.arg, ast.dump(keyword.value)[:20],
                                  node.lineno))
        self.assertEqual(bad, [], "invalid settings keyword arguments: %s" % bad)


class TestFocusMatching(unittest.TestCase):
    """Regression: clicking the pet raised the pet, not the app.

    ``_raise_existing_window`` matched any visible window whose title merely
    *contained* "opencode".  The pet's own windows are titled "OpenCode Pet"
    and "OpenCode Pet cards", so a click focused the pet instead of the desktop
    app - which looks exactly like "clicking does nothing".

    Windows cannot be faked on every platform, so the matcher itself is
    factored out and tested directly.
    """

    def _score(self, title: str) -> int:
        from petcore.client import window_title_score
        return window_title_score(title)

    def test_the_pets_own_titles_are_rejected_outright(self):
        """The pet's windows must never be raisable, on their own merit.

        They used to score 70 and were only kept out by the caller's PID
        filter.  That made correctness depend on the lookup happening to run
        inside the pet's own process: any other caller - a helper script, a
        test, a future tool - would happily raise another desktop pet.
        """
        for title in ("OpenCode Pet", "OpenCode Pet cards",
                      "opencode pet", "  OpenCode Pet  "):
            self.assertEqual(self._score(title), 0,
                             "the pet's own window must score 0: %r" % title)

    def test_attach_thread_input_must_come_from_user32(self):
        """Regression: ``kernel32.AttachThreadInput`` does not exist.

        ``AttachThreadInput`` is exported by **user32**; kernel32 has no such
        symbol, so the foreground retry raised ``AttributeError`` every time
        Windows refused the first change - which is exactly and only when the
        retry was needed.  On the click path that meant a dead button, and
        because the call sits in a Qt slot the exception would also have killed
        the event loop.
        """
        import os
        if os.name != "nt":
            self.skipTest("Win32 only")
        import ast
        import textwrap
        from petcore import client as client_mod

        # Inspect the *attribute accesses*, not the text: the docstring names
        # the wrong DLL on purpose, to explain the bug, and both ``unparse`` and
        # a substring search trip over their own explanation.
        tree = ast.parse(textwrap.dedent(_source_of(client_mod._raise)))
        owners = []
        for node in ast.walk(tree):
            # Either a direct attribute access, or the defensive getattr() form.
            if isinstance(node, ast.Attribute) and node.attr == "AttachThreadInput":
                owners.append(ast.unparse(node.value))
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "getattr" and len(node.args) >= 2
                  and isinstance(node.args[1], ast.Constant)
                  and node.args[1].value == "AttachThreadInput"):
                owners.append(ast.unparse(node.args[0]))
        self.assertTrue(owners, "AttachThreadInput must still be called")
        for owner in owners:
            self.assertIn("user32", owner,
                          "AttachThreadInput lives in user32, got %r" % owner)

        # And prove it against the real OS rather than trusting the comment.
        import ctypes
        self.assertTrue(hasattr(ctypes.windll.user32, "AttachThreadInput"))
        self.assertFalse(hasattr(ctypes.windll.kernel32, "AttachThreadInput"))

    def test_raise_never_lets_an_exception_escape(self):
        """``_raise`` runs in a Qt slot; anything raised there is fatal."""
        import os
        if os.name != "nt":
            self.skipTest("Win32 only")
        from petcore import client as client_mod
        # A nonsense handle must produce a bool, never an exception.
        for bad in (None, 0, -1, 2 ** 40):
            try:
                result = client_mod._raise(bad)
            except Exception as exc:                          # noqa: BLE001
                self.fail("_raise(%r) raised %r - it must be total" % (bad, exc))
            self.assertIsInstance(result, bool)

    def test_the_pid_filter_still_exists_as_a_second_line_of_defence(self):
        from petcore import client as client_mod
        source = _source_of(client_mod._raise_existing_window)
        self.assertIn("own_pid", source,
                      "keep excluding our own process as well")

    def test_an_exact_title_outranks_a_prefixed_one(self):
        self.assertGreater(self._score("OpenCode"),
                           self._score("OpenCode - my project"))
        self.assertGreater(self._score("OpenCode - my project"),
                           self._score("OpenCode something else"))
        self.assertGreater(self._score("OpenCode something else"),
                           self._score("some other app (opencode notes)"))

    def test_unrelated_windows_score_zero(self):
        for title in ("Notepad", "", "Visual Studio Code", "微信",
                      "claude - terminal"):
            self.assertEqual(self._score(title), 0, title)

    def test_matching_is_case_and_space_insensitive(self):
        self.assertEqual(self._score("opencode"), self._score("OpenCode"))
        self.assertEqual(self._score("Open Code"), self._score("opencode"))

    def test_the_raiser_filters_out_our_own_pid(self):
        from petcore import client as client_mod
        source = _source_of(client_mod._raise_existing_window)
        self.assertIn("own_pid", source, "must exclude our own process")
        self.assertIn("GetWindowThreadProcessId", source)

    def test_focus_falls_back_to_launching_the_app(self):
        from petcore import client as client_mod
        focus = _source_of(client_mod.focus_desktop)
        self.assertIn("_launch_exe", focus,
                      "must still launch OpenCode when no window is found")
        launch = _source_of(client_mod._launch_exe)
        self.assertIn("subprocess.Popen", launch)
        self.assertIn("paths.desktop_exe()", launch)

    def test_raise_only_is_a_genuinely_separate_attempt(self):
        """``launch_if_missing=False`` must not start the app.

        ``open_desktop`` needs to distinguish "there is a window, raise it" from
        "there is no window, so start the app".  The old code called
        ``focus_desktop()`` and then, as a "fallback", called the *same* thing
        again - the default already included the launch - so a failure was
        repeated rather than handled, and the user got nothing.
        """
        from petcore import client as client_mod
        source = _source_of(client_mod.focus_desktop)
        self.assertIn("if not launch_if_missing:", source)
        self.assertIn("return False", source,
                      "raise-only must be able to fail without launching")

    def test_launching_waits_for_the_window_instead_of_claiming_success(self):
        """``Popen`` succeeding is not "the user can see OpenCode".

        A cold Electron start takes a second or two to map its window, so
        returning straight after the spawn told the caller "done" while nothing
        was on screen yet.
        """
        from petcore import client as client_mod
        source = _source_of(client_mod.launch_desktop_and_wait)
        self.assertIn("_raise_existing_window()", source,
                      "must wait for a real window and raise it")
        self.assertIn("deadline", source, "the wait must be bounded")

    def test_a_zero_scoring_window_can_never_be_raised(self):
        """Regression: clicking the pet raised an unrelated window.

        The running best started at ``-1`` while ``window_title_score`` returns
        ``0`` for "definitely not OpenCode", so a zero beat the initial value.
        With the OpenCode window closed - the exact reported situation - the pet
        raised whatever unrelated window was enumerated first (another desktop
        pet, here) and returned success, so the launch step never ran and the
        click did nothing useful.
        """
        from petcore import client as client_mod
        source = _source_of(client_mod._raise_existing_window)
        self.assertIn('best = {"hwnd": None, "score": 0}', source,
                      "the running best must start at 0, not -1")

    def test_the_pet_click_does_not_block_the_qt_thread(self):
        """Waiting for a cold Electron start in a slot freezes the pet.

        ``open_desktop`` runs in a Qt slot; a blocking multi-second wait there
        means no repaint and no dragging until it finishes.
        """
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("launch_desktop_and_wait", source)
        self.assertIn('name="pet-launch"', source,
                      "the wait must run on a worker thread")
        self.assertIn("bridge.launched", source,
                      "and report back onto the Qt thread by signal")

    def test_a_refused_foreground_change_is_reported_as_failure(self):
        """Windows can refuse the raise; the result must say so.

        The retry lives in ``_raise`` (thread attachment), so both the helper
        and the caller that depends on it are checked.
        """
        from petcore import client as client_mod
        self.assertIn("GetForegroundWindow() == hwnd",
                      _source_of(client_mod._raise))
        self.assertIn("_raise(", _source_of(client_mod._raise_existing_window))

    def test_a_refused_raise_is_retried_with_thread_attachment(self):
        from petcore import client as client_mod
        source = _source_of(client_mod._raise)
        self.assertIn("AttachThreadInput", source,
                      "must borrow the foreground thread's rights")
        # Detaching again matters: leaving two threads attached corrupts the
        # input queue and shows up as stuck keystrokes much later.
        self.assertIn("our_thread, fg_thread, False", source)
        self.assertIn("finally:", source)


def _source_of(func) -> str:
    import inspect
    return inspect.getsource(func)


# ---------------------------------------------------------------------------
class TestCardsAboveThePet(unittest.TestCase):
    """Cards belong directly above the pet, not beside it."""

    def setUp(self):
        try:
            from PyQt5.QtWidgets import QApplication
        except ImportError:                                  # pragma: no cover
            self.skipTest("PyQt5 is not available")
        global _QT_APP
        _QT_APP = QApplication.instance() or QApplication(["opencode-pet-tests"])
        from petcore import i18n
        i18n.set_language("zh")

    def _stack(self):
        from petui.cards import CardStack
        stack = CardStack()
        stack.set_records([{"id": "a", "state": "idle", "label": "x"}],
                          False, True)
        return stack

    def test_a_pet_in_the_corner_puts_the_cards_above_it(self):
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        # pet hard against the bottom-right corner
        pet = Rect(1920 - BASE_W, 1032 - BASE_H, BASE_W, BASE_H)
        stack.place(pet, screen)
        self.assertEqual(stack.placement, "above")
        self.assertEqual(stack.y() + stack.height(), pet.top() - 10,
                         "the card must sit directly above, with a 10px gap")
        self.assertGreaterEqual(stack.x(), screen.left())
        self.assertLessEqual(stack.x() + stack.width(), screen.right())
        # Against the right edge the card is clamped, so only the vertical
        # relationship is guaranteed here - the centring is tested separately.
        self.assertLess(stack.x() + stack.width(), pet.right() + BASE_W)

    def test_the_card_is_centred_on_the_pet_not_offset_to_one_side(self):
        """Regression: the card sat 'upper-left' instead of directly above.

        The card is 268 px wide against the pet's 132, and the placement
        right-aligned their edges - so 136 px of card hung out to the left of
        the pet and nothing at all hung out to the right.  Centring the two
        makes the overhang symmetric, which is what "directly above" looks
        like even when the card is wider than the pet.
        """
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)

        pet_centre = 900
        pet = Rect(pet_centre - BASE_W // 2, 500, BASE_W, BASE_H)
        stack.place(pet, screen)

        card_centre = stack.x() + stack.width() // 2
        self.assertEqual(card_centre, pet_centre,
                         "card centre %d != pet centre %d"
                         % (card_centre, pet_centre))

        left_over = pet.left() - stack.x()
        right_over = stack.x() + stack.width() - pet.right()
        self.assertEqual(left_over, right_over,
                         "overhang must be symmetric, got %d/%d"
                         % (left_over, right_over))
        self.assertEqual(left_over, (stack.width() - BASE_W) // 2)

    def test_the_overhang_stays_symmetric_anywhere_on_screen(self):
        """Not just in the middle: the clamp must not break the symmetry."""
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        for pet_left in (60, 400, 900, 1400, 1780):
            pet = Rect(pet_left, 500, BASE_W, BASE_H)
            stack.place(pet, screen)
            left_over = pet.left() - stack.x()
            right_over = stack.x() + stack.width() - pet.right()
            clamped = stack.x() in (screen.left(), screen.right() - stack.width())
            if clamped:
                # At an edge the screen wins; only on-screen-ness is required.
                self.assertGreaterEqual(stack.x(), screen.left())
                self.assertLessEqual(stack.x() + stack.width(), screen.right())
            else:
                self.assertEqual(left_over, right_over,
                                 "pet at %d: %d/%d" % (pet_left, left_over,
                                                       right_over))

    def test_the_card_is_never_pushed_off_screen(self):
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        for pet_left in (0, 100, 1788, 1850):
            pet = Rect(pet_left, 500, BASE_W, BASE_H)
            stack.place(pet, screen)
            self.assertGreaterEqual(stack.x(), screen.left(),
                                    "pet at %d" % pet_left)
            self.assertLessEqual(stack.x() + stack.width(), screen.right(),
                                 "pet at %d" % pet_left)

    def test_at_an_edge_the_card_is_flush_with_that_edge(self):
        """Centring cannot fit against an edge; the clamp must win cleanly.

        This is the documented compromise, so it is pinned: a pet hard against
        the right edge gets a card flush with the right edge, and one hard
        against the left gets a card flush with the left.  The pet must never be
        dragged out of reach to make the card symmetrical.
        """
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)

        right_pet = Rect(screen.right() - BASE_W, 500, BASE_W, BASE_H)
        stack.place(right_pet, screen)
        self.assertEqual(stack.x() + stack.width(), screen.right(),
                         "at the right edge the card must be flush right")

        left_pet = Rect(screen.left(), 500, BASE_W, BASE_H)
        stack.place(left_pet, screen)
        self.assertEqual(stack.x(), screen.left(),
                         "at the left edge the card must be flush left")

    def test_the_card_never_changes_width(self):
        """Regression: an adaptive width was tried and reverted.

        Shrinking the card so it could always centre made it visibly resize as
        the pet moved, which was worse than the edge asymmetry it removed.  The
        width is therefore fixed, and this pins that.
        """
        from petui.cards import CARD_W
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        for pet_left in (4, 900, 1764):
            pet = Rect(pet_left, 500, BASE_W, BASE_H)
            stack.place(pet, screen)
            self.assertEqual(stack.width(), CARD_W,
                             "the card must stay %d px wide" % CARD_W)

    def test_the_card_keeps_its_full_width_in_open_space(self):
        from petui.cards import CARD_W
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        pet = Rect(900, 500, BASE_W, BASE_H)
        stack.place(pet, screen)
        self.assertEqual(stack.width(), CARD_W)
        offset = abs((stack.x() + stack.width() // 2)
                     - (pet.left() + pet.right()) // 2)
        self.assertLessEqual(offset, 2, "with room on both sides it centres")

    def test_the_title_column_stays_usable(self):
        from petui.cards import CARD_W, PAD
        stack = self._stack()
        text_width = stack.width() - (PAD * 2 + 9 + 18)
        self.assertGreater(text_width, 200,
                           "a fixed 268px card has plenty of title room")
        self.assertEqual(stack.width(), CARD_W)

    def test_the_card_is_centred_wherever_there_is_room(self):
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        for pet_left in (100, 300, 900, 1500, 1700):
            pet = Rect(pet_left, 500, BASE_W, BASE_H)
            stack.place(pet, screen)
            offset = abs((stack.x() + stack.width() // 2)
                         - (pet.left() + pet.right()) // 2)
            self.assertLessEqual(offset, 2,
                                 "pet at %d is not centred (offset %d)"
                                 % (pet_left, offset))

    def test_a_pet_at_the_top_flips_the_cards_below(self):
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        pet = Rect(900, 0, BASE_W, BASE_H)
        stack.place(pet, screen)
        self.assertEqual(stack.placement, "below")
        self.assertTrue(stack.flip)
        self.assertEqual(stack.y(), pet.bottom() + 10)
        self.assertGreaterEqual(stack.y() + stack.height(), 0)
        self.assertLessEqual(stack.y() + stack.height(), screen.bottom())

    def test_prefer_below_is_honoured_when_there_is_room(self):
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        pet = Rect(900, 400, BASE_W, BASE_H)
        stack.place(pet, screen, prefer_below=True)
        self.assertEqual(stack.placement, "below")
        self.assertEqual(stack.y(), pet.bottom() + 10)

    def test_the_card_never_leaves_the_screen(self):
        from petui.petwindow import BASE_H, BASE_W
        stack = self._stack()
        screen = Rect(0, 0, 1920, 1032)
        for x, y in ((0, 0), (1900, 0), (0, 900), (1900, 900), (800, 400)):
            pet = Rect(x, y, BASE_W, BASE_H)
            stack.place(pet, screen)
            self.assertGreaterEqual(stack.x(), screen.left(),
                                    "pet at %d,%d" % (x, y))
            self.assertLessEqual(stack.x() + stack.width(), screen.right(),
                                 "pet at %d,%d" % (x, y))
            self.assertGreaterEqual(stack.y(), screen.top(),
                                    "pet at %d,%d" % (x, y))
            self.assertLessEqual(stack.y() + stack.height(), screen.bottom(),
                                 "pet at %d,%d" % (x, y))


# ---------------------------------------------------------------------------
class TestInstallerRules(unittest.TestCase):
    """A stale or dev-only file must never reach the install directory."""

    @staticmethod
    def _install():
        import importlib.util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "_install_under_test", os.path.join(root, "install.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    def test_design_references_never_ship(self):
        """``art/`` is documentation; an installed copy must not carry it.

        The design notes used to sit loose in the repository root and were
        excluded by name.  They now live in ``art/`` beside the curated
        artwork, so the exclusion is the whole directory - fewer names to
        forget when one is renamed, which is how the old rule leaked.

        ``SKIP_DIRS`` is consulted while walking the tree, not per file, so that
        is what has to carry it.
        """
        module = self._install()
        self.assertIn("art", module.SKIP_DIRS,
                      "art/ is design documentation and must not be installed")
        # The repository-only root file is still excluded by name.
        self.assertTrue(module._skip(".gitignore", "."))

    def test_the_curated_copies_under_assets_still_ship(self):
        module = self._install()
        self.assertFalse(module._skip("opencode-logo-light.svg",
                                     os.path.join("assets", "brand")))
        self.assertFalse(module._skip("openo-16.png",
                                     os.path.join("assets", "icons")))

    def test_runtime_state_never_ships(self):
        module = self._install()
        self.assertTrue(module._skip("pet_state.json", "."))
        self.assertTrue(module._skip("pet.log", "."))
        self.assertTrue(module._skip("reducer.pyc", "."))

    def test_reinstall_stops_pets_started_with_a_relative_path(self):
        """Regression: reinstalling left an orphan pet on the desktop.

        ``stop_running`` matched the install directory inside the command line,
        but a pet launched from that directory has the bare argv
        ``opencode_pet.py`` - no directory at all - so it survived every
        reinstall and a second copy appeared.
        """
        module = self._install()
        self.assertTrue(hasattr(module, "_is_our_pet"))

        class FakeProc:
            def __init__(self, cmdline, cwd=None, pid=1):
                self.pid = pid
                self.info = {"pid": pid, "cmdline": cmdline}
                self._cwd = cwd

            def cwd(self):
                if self._cwd is None:
                    raise Exception("access denied")
                return self._cwd

        # A literal, because os.path.join("C:", ...) is drive-relative on
        # Windows ("C:Users") and would not compare like a real install path.
        target = r"C:\Users\x\.config\opencode\pet"

        def mine(cmdline, cwd=None, pid=1):
            return module._is_our_pet(FakeProc(cmdline, cwd, pid), target)

        def absolute(name="opencode_pet.py"):
            return [target + "\\" + name]

        # absolute path - matched by the old code
        self.assertTrue(mine(absolute()))
        # bare relative argv, run from the install directory - the orphan case
        self.assertTrue(mine(["opencode_pet.py"], target))
        self.assertTrue(mine([r"python.exe", "opencode_pet.py"], target + "\\"))
        # a real command line, e.g. pythonw.exe C:\...\opencode_pet.py
        self.assertTrue(mine([r"C:\Python\pythonw.exe"] + absolute()))
        # not ours
        self.assertFalse(mine([r"python.exe", r"C:\other\opencode_pet.py"],
                              r"C:\other"))
        self.assertFalse(mine(["python.exe", "install.py"], target))
        self.assertFalse(mine(["notepad.exe"], target))

    def test_reinstall_also_stops_the_sentinel(self):
        """A stale sentinel supervises the pet with pre-install code.

        Leaving it alive is worse than leaving a stale pet: the freshly started
        sentinel fails its single-instance claim and exits, so the *old*
        process - running the code from before the install - keeps launching the
        pet, and the fix that was just installed never takes effect.
        """
        module = self._install()

        class FakeProc:
            def __init__(self, cmdline, cwd=None, pid=1):
                self.pid = pid
                self.info = {"pid": pid, "cmdline": cmdline}
                self._cwd = cwd

            def cwd(self):
                if self._cwd is None:
                    raise Exception("access denied")
                return self._cwd

        target = r"C:\Users\x\.config\opencode\pet"

        def mine(cmdline, cwd=None, pid=1):
            return module._is_our_pet(FakeProc(cmdline, cwd, pid), target)

        # How the generated launcher really starts it: absolute script path.
        self.assertTrue(mine([r"C:\Python\pythonw.exe",
                              target + r"\pet_watcher.py"]))
        # And the bare argv the single-instance guard makes the common case.
        self.assertTrue(mine(["pet_watcher.py"], target))
        # A sentinel belonging to someone else's install is not ours.
        self.assertFalse(mine([r"C:\Python\pythonw.exe",
                               r"C:\other\pet_watcher.py"], r"C:\other"))

    def test_the_installer_never_stops_itself(self):
        """``stop_running`` must not kill the install that is in progress."""
        module = self._install()
        source = open(module.__file__, encoding="utf-8").read()
        self.assertIn("proc.pid == os.getpid()", source,
                      "a --status run reports on the pet; it must not stop it")
        self.assertIn('"install.py" in lowered', source,
                      "the installer matches by name; it has to opt itself out")

    def test_start_now_goes_through_the_sentinel(self):
        """Installing must not leave the pet unsupervised.

        Starting ``opencode_pet.py`` directly looks equivalent and is not: nothing
        would kill the pet when OpenCode closes, and nothing would restart it
        after a crash.  The sentinel owns that lifecycle, and it is what the
        sign-in launcher runs, so an install has to produce the same shape.
        """
        import ast
        import textwrap
        import inspect
        module = self._install()
        # Strip the docstring: it *mentions* opencode_pet.py to explain why that
        # path is wrong, and a text search would trip over its own explanation.
        tree = ast.parse(textwrap.dedent(inspect.getsource(module.start_now)))
        if ast.get_docstring(tree):
            tree.body = tree.body[1:]
        literals = {n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertIn("pet_watcher.py", literals,
                      "start_now must launch the sentinel")
        self.assertNotIn("opencode_pet.py", literals,
                         "launching the pet directly skips the supervisor")

    def test_reinstall_clears_the_lock_files(self):
        """A surviving lock would stop the next pet from ever starting."""
        import ast
        module = self._install()
        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        stop = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "stop_running")
        source = ast.dump(stop)
        self.assertIn("pet.lock", source)
        self.assertIn("sentinel.lock", source)
        self.assertIn("kill", source, "terminate alone can leave a survivor")

    def test_the_target_is_inside_the_opencode_config_dir(self):
        target = self._install().target_root()
        self.assertTrue(target.endswith(os.path.join("opencode", "pet")))
        self.assertNotIn("Desktop", target)

    def test_clear_destination_keeps_user_state(self):
        import tempfile
        module = self._install()
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "stale.py"), "w").close()
            open(os.path.join(folder, "pet_state.json"), "w").close()
            os.makedirs(os.path.join(folder, "state"))
            open(os.path.join(folder, "state", "pet.log"), "w").close()
            os.makedirs(os.path.join(folder, "__pycache__"))
            removed = module.clear_destination(folder)
            self.assertEqual(removed, 2)
            self.assertTrue(os.path.isfile(os.path.join(folder, "pet_state.json")))
            self.assertTrue(os.path.isdir(os.path.join(folder, "state")))
            self.assertFalse(os.path.exists(os.path.join(folder, "stale.py")))

    def test_the_startup_launcher_is_valid_vbscript(self):
        """A malformed launcher fails silently at sign-in - so check the shape.

        ``oShell.Run`` takes ONE command string.  Writing the executable and
        the script as two adjacent literals parses as two arguments and the
        sentinel never starts; the quotes therefore have to be doubled.
        """
        import tempfile
        module = self._install()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        with tempfile.TemporaryDirectory() as startup:
            path = module.write_launcher(root, startup=startup)
            with open(path, "rb") as handle:
                raw = handle.read()
        # Read as bytes: text mode would silently fold the CRLF away, and
        # wscript.exe is unhappy with a bare-LF .vbs.
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""),
                         "launcher must use CRLF line endings only")
        text = raw.decode("utf-8")

        self.assertIn('CreateObject("WScript.Shell")', text)
        runs = [line.strip() for line in text.splitlines()
                if line.strip().startswith("oShell.Run")]
        self.assertEqual(len(runs), 1)
        call = runs[0]

        match = re.match(r'^oShell\.Run """(.+?)"" ""(.+?)""", 0, False$', call)
        self.assertIsNotNone(match, "malformed Run call: %s" % call)
        pythonw, script = match.group(1), match.group(2)
        self.assertEqual(pythonw, module.paths.pythonw())
        self.assertEqual(script, os.path.join(root, "pet_watcher.py"))
        # Exactly one argument string, then 0 and False.  Two adjacent
        # literals are a VBScript syntax error and fail silently.
        self.assertEqual(call.count(", 0, False"), 1)
        self.assertNotIn('""', pythonw)
        self.assertNotIn('""', script)

    def test_launcher_command_matches_the_generated_launcher(self):
        module = self._install()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        command = module.launcher_command(root)
        self.assertTrue(command.startswith('"'))
        self.assertTrue(command.endswith('.py"'))
        self.assertEqual(command.count('"'), 4)


# ---------------------------------------------------------------------------
class TestNoDevPathDependency(unittest.TestCase):
    """The whole point of the install: nothing may point outside APP_ROOT."""

    def _sources(self):
        import glob
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for pattern in ("*.py", "petcore/*.py", "petui/*.py", "tools/*.py"):
            for path in glob.glob(os.path.join(root, pattern)):
                yield path

    def test_no_source_hardcodes_an_absolute_development_path(self):
        offenders = []
        for path in self._sources():
            with open(path, "r", encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if "Desktop" in line and "OpenCode-pet" in line:
                        offenders.append("%s:%d" % (os.path.basename(path), number))
        self.assertEqual(offenders, [], "hardcoded dev paths: %s" % offenders)

    def test_app_root_is_derived_from_the_module_location(self):
        from petcore import paths
        root = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
        self.assertEqual(os.path.normcase(paths.APP_ROOT), os.path.normcase(root))
        self.assertTrue(paths.settings_file().startswith(paths.APP_ROOT))
        self.assertTrue(paths.log_file().startswith(paths.APP_ROOT))
        self.assertTrue(paths.state_dir().startswith(paths.APP_ROOT))


# ---------------------------------------------------------------------------
class TestWatcher(unittest.TestCase):
    def setUp(self):
        # The sentinel refuses to start the pet while the user has closed it,
        # and this machine may well have it closed.
        isolate_quit_latch(self)

    def test_the_sentinel_never_imports_qt(self):
        """It has to be able to run on a machine with no Qt at all."""
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "pet_watcher.py"), "r",
                  encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("PyQt5", imported)
        self.assertNotIn("petui", imported)

    def test_process_detection_agrees_with_the_os(self):
        from petcore import endpoint
        self.assertIsInstance(endpoint.desktop_running(), bool)
        self.assertIsInstance(endpoint.service_running(), bool)
        self.assertIsInstance(endpoint.app_alive(), bool)

    def test_the_sentinel_outlives_the_window_and_leaves_with_the_service(self):
        """The reported bug, driven through the real loop.

        Closing the OpenCode window leaves ``opencode-cli serve --service``
        running.  The sentinel used to stop the pet on that, which took away the
        only way back into a closed app - so the pet now leaves only when the
        background service ends too.

        The whole sequence runs against fake process probes and a fake poll
        sleep, so it is deterministic and starts nothing for real.
        """
        import pet_watcher
        from petcore import endpoint as endpoint_mod

        # (window, service) at each poll, in order.  The tail is a *sustained*
        # absence on purpose: the sentinel used to tear the pet down on the
        # first empty scan, which is the bug this test now also guards.
        script = [
            (False, False),     # OpenCode not started
            (False, True),      # service up, window closed
            (True, True),       # window opened
            (False, True),      # window closed again, service still up
            (False, True),
            (False, False),     # service finally ended
            (False, False),
            (False, False),
            (False, False),
            (False, False),     # ...and stayed ended
        ]
        index = {"i": 0}
        original = (endpoint_mod.desktop_running, endpoint_mod.service_running,
                    endpoint_mod.app_alive, pet_watcher.endpoint_mod,
                    pet_watcher.pet_running, pet_watcher.launch_pet,
                    pet_watcher.kill_pet, pet_watcher.time.sleep)

        class Done(Exception):
            pass

        # Each poll is POLL_SECONDS apart, so the fake clock has to move with the
        # fake sleep or the grace period never elapses.
        now = {"t": 1000.0}

        def fake_sleep(seconds):
            now["t"] += seconds
            index["i"] += 1
            if index["i"] >= len(script):
                raise Done

        pet_up = [False]
        actions = []
        endpoint_mod.desktop_running = lambda: script[index["i"]][0]
        endpoint_mod.service_running = lambda: script[index["i"]][1]
        endpoint_mod.app_alive = lambda: (endpoint_mod.desktop_running()
                                          or endpoint_mod.service_running())
        pet_watcher.endpoint_mod = endpoint_mod
        pet_watcher.pet_running = lambda: pet_up[0]
        pet_watcher.launch_pet = lambda lang="": (actions.append("launch"),
                                                  pet_up.__setitem__(0, True))
        pet_watcher.kill_pet = lambda: (actions.append("stop"),
                                        pet_up.__setitem__(0, False))
        pet_watcher.time.sleep = fake_sleep

        class Args:
            always_show = False
            once = False

        try:
            pet_watcher._loop(Args(), "en", clock=lambda: now["t"])
        except Done:
            pass
        finally:
            (endpoint_mod.desktop_running, endpoint_mod.service_running,
             endpoint_mod.app_alive, pet_watcher.endpoint_mod,
             pet_watcher.pet_running, pet_watcher.launch_pet,
             pet_watcher.kill_pet, pet_watcher.time.sleep) = original

        self.assertEqual(actions, ["launch", "stop"],
                         "launch once, stop once, and never stop just because "
                         "the window closed (got %r)" % (actions,))

    def test_app_alive_covers_the_service_as_well_as_the_window(self):
        """The pet must outlive the *window*, not just the process group.

        Closing the OpenCode window leaves ``opencode-cli serve --service``
        running, holding every session.  Gating on the window alone made the pet
        quit while OpenCode was still alive underneath, removing the only way
        back into a closed app.
        """
        from petcore import endpoint
        window = endpoint.desktop_running()
        service = endpoint.service_running()
        self.assertEqual(endpoint.app_alive(), window or service,
                         "app_alive must be true when *either* process lives")
        if service and not window:
            self.assertTrue(endpoint.app_alive(),
                            "a service-only OpenCode is still OpenCode")

    def test_the_pet_and_sentinel_both_watch_app_alive(self):
        """One definition of "OpenCode is running", used by both halves.

        If the two halves disagreed the pet would exit on the sentinel's next
        poll and come straight back - a restart loop the user sees as the pet
        flickering whenever the window is closed.
        """
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("pet_watcher.py", "opencode_pet.py"):
            with open(os.path.join(root, name), "r", encoding="utf-8") as handle:
                source = handle.read()
            tree = ast.parse(source)
            uses = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Attribute)
                    and n.attr in ("app_alive", "desktop_running")]
            self.assertTrue(uses, "%s checks OpenCode's liveness" % name)
            # Wherever the window check appears, app_alive must appear too.
            self.assertTrue(any(n.attr == "app_alive" for n in uses),
                            "%s must watch OpenCode as a whole" % name)

    def test_it_knows_where_its_own_entry_point_is(self):
        import pet_watcher
        entry = pet_watcher.pet_entry()
        self.assertTrue(entry.endswith(os.path.join("", "opencode_pet.py")))
        self.assertTrue(os.path.isfile(entry))
        self.assertTrue(entry.startswith(pet_watcher.paths.APP_ROOT))
    def test_pet_detection_can_see_this_process(self):
        import pet_watcher
        self.assertIsInstance(pet_watcher.pet_running(), bool)

    def test_only_one_sentinel_may_run(self):
        """Two sentinels would fight over the same pet."""
        import tempfile
        import pet_watcher
        with tempfile.TemporaryDirectory() as folder:
            import petcore.paths
            original = petcore.paths.state_dir
            original_lock = petcore.paths.lock_dir
            petcore.paths.state_dir = lambda: folder
            # The sentinel lock is machine-wide now, so it has to be redirected
            # as well - otherwise this test finds the real sentinel already
            # holding it, which is the shared lock doing its job.
            petcore.paths.lock_dir = lambda: folder
            try:
                self.assertIsNone(pet_watcher.acquire_single_instance())
                self.assertTrue(os.path.isfile(pet_watcher.lock_path()))
                # A second claim sees our own live PID and refuses.
                self.assertEqual(pet_watcher.acquire_single_instance(),
                                 os.getpid())

                # A lock left by a dead sentinel is reclaimed, not respected.
                for guard in pet_watcher._SENTINEL_GUARD:
                    guard._owned = False
                with open(pet_watcher.lock_path(), "w") as handle:
                    handle.write("999999999")
                self.assertIsNone(pet_watcher.acquire_single_instance())

                # ... and releasing only drops our own lock.
                pet_watcher._SENTINEL_GUARD[-1].release()
                self.assertFalse(os.path.exists(pet_watcher.lock_path()))
            finally:
                petcore.paths.state_dir = original
                petcore.paths.lock_dir = original_lock
                pet_watcher._SENTINEL_GUARD.clear()


# ---------------------------------------------------------------------------
class TestSustainedAbsence(unittest.TestCase):
    """Why the pet vanished on 2026-10-17, and the rule that prevents it.

    The sentinel's own log recorded the whole failure in one second::

        20:13:43 opencode detected (service only)
        20:13:43 opencode and its background service are both gone

    ``app_alive()`` is a live process enumeration, not a fact.  It returned an
    empty result for one poll - a service restart, a plugin reload, a ``psutil``
    throw with an empty ``tasklist`` fallback - and both halves of the pet acted
    on that single observation.  The pet quit, the sentinel quit, and because
    the sentinel is only started at sign-in nothing brought either back for the
    rest of the session.
    """

    def test_one_empty_scan_is_not_enough_to_conclude_anything(self):
        from petcore.liveness import AbsentConfirm
        gone = AbsentConfirm(grace=6.0, min_polls=4)
        t = 1000.0
        self.assertFalse(gone.update(True, t))
        # A single miss, and then OpenCode is right back.
        self.assertFalse(gone.update(False, t + 1.5))
        self.assertFalse(gone.update(True, t + 3.0))
        self.assertFalse(gone.confirmed,
                         "one failed scan must never count as OpenCode closing")

    def test_absence_must_persist_before_it_counts(self):
        from petcore.liveness import AbsentConfirm
        gone = AbsentConfirm(grace=6.0, min_polls=4)
        t = 1000.0
        gone.update(True, t)
        fired = [gone.update(False, t + 1.5 * n) for n in range(1, 5)]
        self.assertFalse(any(fired),
                         "four fast samples are not six seconds of absence")
        self.assertFalse(gone.confirmed)

    def test_a_real_shutdown_is_acted_on_exactly_once(self):
        from petcore.liveness import AbsentConfirm
        gone = AbsentConfirm(grace=6.0, min_polls=4)
        t = 1000.0
        gone.update(True, t)
        fired = 0
        for n in range(1, 12):
            if gone.update(False, t + 1.5 * n):
                fired += 1
        self.assertEqual(fired, 1,
                         "teardown must happen once, not on every later poll")
        self.assertTrue(gone.confirmed)

    def test_seeing_opencode_again_resets_everything(self):
        from petcore.liveness import AbsentConfirm
        gone = AbsentConfirm(grace=6.0, min_polls=4)
        t = 1000.0
        gone.update(True, t)
        for n in range(1, 4):
            gone.update(False, t + 1.5 * n)
        gone.update(True, t + 10.0)
        self.assertFalse(gone.confirmed)
        self.assertEqual(gone.samples, 0)
        # ...and the clock starts over from scratch.
        self.assertFalse(gone.update(False, t + 11.0))

    def test_a_fast_polling_loop_cannot_outrun_the_sample_floor(self):
        """A grace period measured only in seconds is defeated by a hot loop."""
        from petcore.liveness import AbsentConfirm
        gone = AbsentConfirm(grace=6.0, min_polls=4)
        t = 1000.0
        gone.update(True, t)
        for n in range(1, 60):          # 60 polls spread over 6 seconds
            if gone.update(False, t + 0.1 * n):
                break
        self.assertFalse(gone.confirmed,
                         "60 polls in 6s is not 4 observations plus 6s")

    def test_a_resume_is_not_mistaken_for_opencode_closing(self):
        """The overnight failure, and the rule that prevents it.

        This laptop sleeps five times an hour.  Straight after a resume the
        process table cannot be trusted, so ``app_alive()`` returns False while
        OpenCode is very much alive.  The debounce cannot help - the machine
        really was unreachable for the length of the resume - so the sample has
        to be rejected outright.
        """
        from petcore.liveness import AbsentConfirm, SleepWatch

        watch = SleepWatch(tolerance=2.0, settle=20.0)
        wall, mono = 1_000_000.0, 5_000.0
        self.assertFalse(watch.update(wall, mono), "the first sample compares "
                                                     "against nothing")
        # Ten seconds pass normally.
        self.assertFalse(watch.update(wall + 10.0, mono + 10.0))
        # The machine suspends for 40 minutes; wall clock jumps, monotonic
        # barely moves.
        slept = watch.update(wall + 2410.0, mono + 10.05)
        self.assertTrue(slept, "a 40-minute gap between the clocks is a sleep")

        gone = AbsentConfirm(grace=6.0, min_polls=4)
        gone.update(True, 100.0)
        # Four empty scans during the settling window must not count.
        fake_mono = 5010.05
        for n in range(1, 5):
            self.assertFalse(gone.update(False, 100.0 + n * 2.0,
                                         trust=not watch.settling_at(fake_mono
                                                                   + n * 2.0)))
        self.assertFalse(gone.confirmed,
                         "a resume must not be able to close OpenCode")
        # Once settling is over, absence counts normally again.
        self.assertFalse(watch.settling_at(fake_mono + 60.0))

    def test_an_untrusted_sample_does_not_reset_a_real_absence(self):
        """Discarding a sample means discarding it, in both directions."""
        from petcore.liveness import AbsentConfirm
        gone = AbsentConfirm(grace=6.0, min_polls=4)
        gone.update(True, 100.0)
        gone.update(False, 102.0)
        self.assertEqual(gone.samples, 1)
        gone.update(True, 104.0, trust=False)
        self.assertEqual(gone.samples, 1,
                         "a sample we cannot trust says nothing either way")

    def test_a_slow_poll_is_not_mistaken_for_a_sleep(self):
        """Only a clock *disagreement* is a suspend, not a slow poll."""
        from petcore.liveness import SleepWatch
        watch = SleepWatch(tolerance=2.0)
        wall, mono = 100.0, 100.0
        watch.update(wall, mono)
        # A poll that took 30 seconds of wall time also took 30 of monotonic.
        self.assertFalse(watch.update(wall + 30.0, mono + 30.0),
                         "30 seconds of work is not a suspend")
        # A small, harmless discrepancy stays under the tolerance.
        self.assertFalse(watch.update(wall + 32.0, mono + 31.0))

    def test_the_sentinel_can_be_restarted_by_something_outside_it(self):
        """The single point of failure, closed.

        The sentinel used to run only at sign-in, so when it exited for any
        reason at all the desktop stayed empty until the next logon - which is
        exactly what happened twice.  The installer now registers a periodic
        Windows task that re-runs the sentinel; starting it is idempotent
        because it exits at once if the lock is already held.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "install.py"), "r", encoding="utf-8") as h:
            source = h.read()
        self.assertIn("schtasks", source,
                      "the watchdog is a Windows scheduled task")
        self.assertIn("WATCHDOG_MINUTES", source)
        tree = ast.parse(source)
        functions = {n.name for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)}
        for name in ("install_watchdog", "remove_watchdog", "watchdog_installed"):
            self.assertIn(name, functions)
        # Uninstall has to take it away again, or it would keep starting a pet
        # whose files are gone.
        uninstall = source.split("def cmd_uninstall", 1)[-1]
        self.assertIn("remove_watchdog", uninstall)

    def test_a_pet_off_the_desktop_is_brought_back(self):
        """The reported bug, 2026-10-18: a pet that nobody could see.

        It sat at x=3306 on a 2560-wide desktop - alive, unlocked, topmost,
        and entirely outside the virtual desktop.  Its card was visible, so the
        user saw a card with no pet, and ``install.py status`` cheerfully
        reported a healthy installation.  ``clamp_to_screen`` would have fixed
        it, but it only runs on restore, on scale and at the end of a drag: a
        pet that is simply *sitting there* is never re-checked.

        This drives the real geometry rather than a mock, because the whole
        difficulty is that Qt's own answers are wrong for a window nowhere near
        a screen - ``window.screen()`` reports the nearest screen, which is the
        question being asked in the first place.
        """
        from PyQt5.QtWidgets import QApplication
        from petui.petwindow import PetWindow

        app = QApplication.instance() or QApplication(["pet-test"])
        window = PetWindow(scale=1.0)
        try:
            area = QApplication.primaryScreen().availableGeometry()

            window.move(area.left() + 40, area.top() + 40)
            self.assertTrue(window.is_onscreen(),
                            "a pet inside the desktop is on screen")

            # Far outside every screen, exactly as reported.
            window.move(area.right() + 700, area.bottom() + 100)
            self.assertFalse(window.is_onscreen(),
                             "a pet at x=%d on a %d-wide desktop is not on screen"
                             % (window.x(), area.width()))

            self.assertTrue(window.ensure_on_screen(),
                            "the pet must come back on its own")
            self.assertTrue(window.is_onscreen())
            self.assertTrue(area.contains(window.geometry()),
                            "and it must land somewhere visible, not merely "
                            "nearer the edge (got %r)" % (window.geometry(),))

            # Idempotent: a second call must not move a healthy pet.
            before = window.pos()
            self.assertFalse(window.ensure_on_screen())
            self.assertEqual(window.pos(), before,
                             "a visible pet must never be dragged around")
        finally:
            window.close()
            window.deleteLater()

    def test_being_one_pixel_over_an_edge_is_not_on_screen(self):
        """A sliver of pet on the edge is, to the user, gone."""
        from PyQt5.QtWidgets import QApplication
        from petui.petwindow import ONSCREEN_MARGIN_PX, PetWindow

        app = QApplication.instance() or QApplication(["pet-test"])
        window = PetWindow(scale=1.0)
        try:
            area = QApplication.primaryScreen().availableGeometry()
            # Hanging off the right edge with only a sliver showing.
            window.move(area.right() - 3, area.top() + 40)
            self.assertFalse(window.is_onscreen(),
                             "%d visible pixels is not 'on screen'"
                             % ONSCREEN_MARGIN_PX)
        finally:
            window.close()
            window.deleteLater()

    def test_the_onscreen_check_runs_periodically_not_only_on_restore(self):
        """The check has to be on a timer, or it is only a startup nicety."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("screen_timer", source,
                      "the pet must be re-checked while it is simply running")
        body = source.split("def keep_on_screen", 1)[-1]
        self.assertIn("ensure_on_screen", body,
                      "the timer has to be the thing that calls it")
        # A rescued pet must take its cards with it, so a card is not left
        # stranded on the far edge where the pet used to be.
        self.assertIn("persist_pos", body,
                      "and the new position has to be remembered")

    def test_the_watchdog_command_points_at_the_installation(self):
        import install
        import petcore.paths as paths
        command = install.watchdog_command(paths.opencode_config_dir() + r"\pet")
        self.assertIn("pet_watcher.py", command)
        self.assertIn("pythonw", command.lower())
        # Never the directory the installer happens to be run from.
        self.assertNotIn("Desktop", command)

    def test_every_exit_path_leaves_a_trace(self):
        """``pythonw.exe`` discards stderr, so silence used to mean nothing.

        The pet and the sentinel both died with no record at all, which is how
        this cost a whole day of guessing.  An exception on any thread, and a
        clean exit, must all reach the log file.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("opencode_pet.py", "pet_watcher.py"):
            with open(os.path.join(root, name), "r", encoding="utf-8") as handle:
                source = handle.read()
            tree = ast.parse(source)
            hooked = set()
            registered = False
            for node in ast.walk(tree):
                # sys.excepthook = ...  /  threading.excepthook = ...
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute):
                            hooked.add(target.attr)
                # atexit.register(...)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "register":
                        registered = True
            self.assertIn("excepthook", hooked,
                          "%s must catch uncaught main-thread exceptions" % name)
            self.assertIn("excepthook", hooked | set(dir(__import__("threading"))),
                          "%s" % name)
            self.assertTrue(registered,
                            "%s must record its own clean exit (atexit)" % name)

    def setUp(self):
        # The sentinel asks this before starting anything, and the real machine
        # may well have the pet closed on purpose.
        isolate_quit_latch(self)

    def test_the_sentinel_survives_a_poll_that_raises(self):
        """One failing poll must not be the sentinel's last act.

        The sentinel starts once, at sign-in.  An exception escaping the loop
        ends the only process that can restart the pet, silently - which is
        exactly the "the pet disappeared and never came back" symptom, with
        nothing in any log.
        """
        import pet_watcher
        from petcore import endpoint as endpoint_mod

        script = [True, "boom", True, True]
        index = {"i": 0}
        original = (pet_watcher.endpoint_mod, pet_watcher.launch_pet,
                    pet_watcher.pet_running, pet_watcher.kill_pet,
                    pet_watcher.time.sleep, endpoint_mod.app_alive,
                    endpoint_mod.desktop_running)

        class Done(Exception):
            pass

        now = {"t": 1000.0}

        def fake_sleep(seconds):
            now["t"] += seconds
            index["i"] += 1
            if index["i"] >= len(script):
                raise Done

        def flaky_alive():
            step = script[index["i"]]
            if step == "boom":
                raise RuntimeError("psutil exploded")
            return bool(step)

        pet_up = [False]
        actions = []
        endpoint_mod.app_alive = flaky_alive
        endpoint_mod.desktop_running = lambda: False
        pet_watcher.endpoint_mod = endpoint_mod
        pet_watcher.launch_pet = lambda lang="": (actions.append("launch"),
                                                  pet_up.__setitem__(0, True))
        pet_watcher.pet_running = lambda: pet_up[0]
        pet_watcher.kill_pet = lambda: actions.append("stop")
        pet_watcher.time.sleep = fake_sleep

        class Args:
            always_show = False
            once = False

        try:
            pet_watcher._loop(Args(), "en", clock=lambda: now["t"])
            reached_end = True
        except Done:
            reached_end = True
        except Exception:                                # noqa: BLE001
            reached_end = False
        finally:
            (pet_watcher.endpoint_mod, pet_watcher.launch_pet,
             pet_watcher.pet_running, pet_watcher.kill_pet,
             pet_watcher.time.sleep, endpoint_mod.app_alive,
             endpoint_mod.desktop_running) = original

        self.assertTrue(reached_end,
                        "the sentinel loop let an exception escape")
        self.assertEqual(actions, ["launch"],
                         "it must keep working after a failed poll, and the "
                         "failed poll must not stop the pet (got %r)" % (actions,))


# ---------------------------------------------------------------------------
def isolate_quit_latch(case):
    """Stop a test from seeing the real machine's "stay closed" request.

    The sentinel now asks ``paths.user_quit()`` before it starts anything, which
    is the behaviour we want and also means a test suite run on a machine where
    the user has actually closed the pet silently changes what the sentinel
    tests assert.  A test that depends on the developer's own settings is not a
    test, it is a coin toss - so every test that drives the sentinel gets its
    own empty latch.
    """
    import petcore.paths as paths
    original = paths.user_quit
    paths.user_quit = lambda: False
    case.addCleanup(lambda: setattr(paths, "user_quit", original))


class TestUserQuitSticks(unittest.TestCase):
    """Exit from the tray menu has to mean exit.

    The pet is restarted by the sentinel whenever it disappears, which is exactly
    right for a crash and exactly wrong for a deliberate quit - the two were
    indistinguishable, so choosing Exit brought the pet back about two seconds
    later.  There was no way to make it stay gone, which made the whole Exit
    item a lie.

    So a user quit is latched, and only a start the user asked for clears it.
    """

    def setUp(self):
        import tempfile
        import petcore.paths as paths
        self._folder = tempfile.mkdtemp(prefix="pet-quit-")
        self._original = paths.opencode_config_dir
        paths.opencode_config_dir = lambda: self._folder
        self.addCleanup(self._restore)

    def _restore(self):
        import shutil
        import petcore.paths as paths
        paths.opencode_config_dir = self._original
        shutil.rmtree(self._folder, ignore_errors=True)

    def test_the_latch_is_a_file_whose_existence_is_the_whole_meaning(self):
        from petcore import paths
        self.assertFalse(paths.user_quit())
        self.assertTrue(paths.set_user_quit(True))
        self.assertTrue(paths.user_quit())
        self.assertTrue(os.path.isfile(paths.quit_marker()))
        self.assertTrue(paths.set_user_quit(False))
        self.assertFalse(paths.user_quit())

    def test_it_lives_in_opencodes_config_not_in_the_install_folder(self):
        """A checkout on the Desktop and the installation must agree.

        ``pet_state.json`` lives inside the application folder, so the two copies
        have separate ones.  "I quit the pet" has to mean the pet is off, not
        "the copy I happened to click was off".
        """
        from petcore import paths
        self.assertTrue(os.path.normcase(paths.quit_marker()).startswith(
            os.path.normcase(paths.opencode_config_dir())))

    def test_the_tray_quit_latches_and_other_exits_do_not(self):
        """Only a *user* quit may leave a 'stay off' note behind.

        The pet also quits by itself when OpenCode closes.  If that wrote the
        latch, the next sign-in would bring back a pet nobody asked to keep -
        the opposite bug, and a worse one.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('if reason == "user":', source,
                      "only a user quit may latch")
        self.assertIn("set_user_quit(True)", source)
        # The lifecycle quit must say why, and must not say "user".
        self.assertIn('quit_app("opencode-closed")', source)

    def test_the_sentinel_tells_the_pet_who_asked_for_the_start(self):
        """A background restart and a human running the script are not the same.

        ``--from-sentinel`` is the whole mechanism: without it the pet cannot
        tell "start the pet, it crashed" from "start the pet, I want it back",
        and has to either always obey the latch (making the pet impossible to
        bring back) or always ignore it (making Exit useless again).
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "pet_watcher.py"), "r",
                  encoding="utf-8") as handle:
            watcher = handle.read()
        self.assertIn('args.append("--from-sentinel")', watcher,
                      "the sentinel must mark its own launches")
        self.assertIn("paths.user_quit()", watcher,
                      "and must not launch the pet while the latch is set")

        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            pet = handle.read()
        self.assertIn('"--from-sentinel"', pet,
                      "the pet must accept that marker")
        self.assertIn("args.from_sentinel", pet,
                      "and use it to decide whether to obey the latch")

    def test_the_sentinel_really_refuses_to_start_a_closed_pet(self):
        """Driven through the real loop, not asserted from the source.

        This is the behaviour the whole feature exists for: a quit that the
        sentinel quietly undoes two seconds later is not a quit.
        """
        import pet_watcher
        from petcore import paths as real_paths

        launched = []
        original_quit = real_paths.user_quit
        original_launch = pet_watcher.launch_pet
        original_sleep = pet_watcher.time.sleep
        real_paths.user_quit = lambda: True
        pet_watcher.launch_pet = lambda lang="": launched.append("launch")

        class Done(Exception):
            pass

        count = {"n": 0}

        def fake_sleep(_seconds):
            count["n"] += 1
            if count["n"] >= 6:
                raise Done

        pet_watcher.time.sleep = fake_sleep

        class Args:
            always_show = False
            once = False

        try:
            try:
                pet_watcher._loop(Args(), "en", clock=lambda: 1000.0 + count["n"])
            except Done:
                pass
        finally:
            real_paths.user_quit = original_quit
            pet_watcher.launch_pet = original_launch
            pet_watcher.time.sleep = original_sleep

        self.assertEqual(launched, [],
                         "the sentinel started the pet while the user had "
                         "closed it - that is the bug this whole change is for")

    def test_a_manual_start_clears_the_latch(self):
        """Otherwise 'stay off' would be permanent and unrecoverable."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("set_user_quit(False)", source,
                      "starting by hand must clear the request")

    def test_reinstalling_and_enable_both_bring_the_pet_back(self):
        import install
        from petcore import paths
        paths.set_user_quit(True)
        self.assertTrue(paths.user_quit())
        install.cmd_enable(None)
        self.assertFalse(paths.user_quit(),
                         "install.py enable must clear the latch")
        # And installing clears it too, so a reinstall is never a silent no-op.
        with open(os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))), "install.py"),
                "r", encoding="utf-8") as handle:
            self.assertIn("set_user_quit(False)", handle.read())


class TestMultipleEndpoints(unittest.TestCase):
    """One machine, more than one OpenCode service.

    The reported symptom on 2026-10-18: a conversation open in the app -
    ``ACE离线OS-Interaction实验执行与验收``, in the OpenLoopEvolve workspace - and
    the pet had never shown a card for it.  It was not a filter and not a
    retention rule.  That conversation belonged to a *different* service: a
    remote workspace reached over an SSH forward, whose sessions live on the
    far side of that tunnel.  The pet watched exactly one endpoint and that was
    the only one it could ever have seen.

    These tests use real sockets and real HTTP, because the thing being tested
    is precisely whether a second service is discoverable and readable.
    """

    def _serve(self, title, sessions, port_holder):
        """A minimal stand-in for ``opencode-cli serve --service``."""
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        payload_info = json.dumps({"version": "2.0.32", "pid": port_holder[1],
                                   "urls": [], "paths": {}}).encode()
        payload_sessions = json.dumps({"data": sessions}).encode()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _send(self, body):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/info"):
                    self._send(payload_info)
                elif self.path.startswith("/api/session"):
                    self._send(payload_sessions)
                else:
                    self._send(b'{"data":{}}')

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port_holder[0] = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % port_holder[0]

    @staticmethod
    def _session(session_id, title, directory):
        stamp = 1792290000000
        return {"id": session_id, "title": title,
                "location": {"directory": directory},
                "time": {"created": stamp, "updated": stamp, "idle": stamp}}

    def test_discover_all_finds_more_than_one_service(self):
        from petcore import endpoint
        one = self._serve("local", [self._session("ses_a", "local one", "C:/work")],
                          [0, 100])
        two = self._serve("remote", [self._session("ses_b", "remote one", "/home/x")],
                          [0, 200])
        # A test server sits on an ephemeral port owned by *python*, so it is in
        # neither the OpenCode-owned nor the tunnel-owned set.  Naming it is
        # exactly the supported way to reach a service discovery cannot guess -
        # and it is how a real user points the pet at a forwarded workspace.
        self._name_endpoints(one, two)

        # A non-empty password is required: discovery refuses to probe without
        # one, which is the same rule ``discover`` has always had.
        found = endpoint.discover_all(password="test")
        urls = {e.url for e in found}
        # Both are proven by /api/info, so both must come back.
        self.assertIn(one, urls, "the first service must be found")
        self.assertIn(two, urls,
                      "a second service must be found too - this is the whole "
                      "point: one endpoint is why the remote session was never "
                      "shown")

    def _name_endpoints(self, *urls):
        previous = os.environ.get("OPENCODE_SERVER_URL")
        os.environ["OPENCODE_SERVER_URL"] = ",".join(urls)
        self.addCleanup(lambda: os.environ.__setitem__("OPENCODE_SERVER_URL", previous)
                        if previous is not None
                        else os.environ.pop("OPENCODE_SERVER_URL", None))

    def test_discover_still_reports_exactly_one_endpoint(self):
        """``discover`` is the "the one OpenCode" call; it must not change."""
        from petcore import endpoint
        self._serve("a", [], [0, 100])
        self._serve("b", [], [0, 200])
        ep, err = endpoint.discover()
        self.assertIsNotNone(ep, "discover must still find the primary service")
        self.assertIsInstance(ep.url, str)
        self.assertEqual(err, "")

    def test_a_session_from_a_second_service_reaches_the_pet(self):
        """The end-to-end shape: two services, one merged set of facts."""
        import threading
        from petcore import endpoint, reducer

        local = self._serve("local", [self._session("ses_a", "本地会话", "C:/work")],
                            [0, 100])
        remote = self._serve("remote", [self._session("ses_b", "远程会话", "/home/x")],
                             [0, 200])
        self._name_endpoints(local, remote)
        endpoints = endpoint.discover_all(password="test")
        self.assertGreaterEqual(len(endpoints), 2)

        # Merge exactly as the observer does, then fold with the real reducer.
        infos, origin = [], {}
        for ep in endpoints:
            if ep.url not in (local, remote):
                continue
            from petcore.client import OpenCodeClient
            client = OpenCodeClient(endpoint=ep)
            self.assertTrue(client.connect())
            found = client.list_sessions(limit=40)
            infos.extend(found)
            for item in found:
                if isinstance(item, dict) and item.get("id"):
                    origin[item["id"]] = ep.url

        sessions = {}
        reducer.reconcile(sessions, {}, infos, {}, 1792290000000)
        for session_id, record in sessions.items():
            record["source"] = origin.get(session_id, "")

        self.assertIn("ses_a", sessions, "the local session must survive")
        self.assertIn("ses_b", sessions,
                      "the remote session must be present too, not filtered out")
        self.assertEqual(sessions["ses_a"]["source"], local)
        self.assertEqual(sessions["ses_b"]["source"], remote)
        # And the project label comes from each one's own directory, so the two
        # cards are distinguishable on screen without any new UI.
        self.assertEqual(sessions["ses_b"]["project"], "x")

    def test_a_client_never_wanders_to_another_service(self):
        """The bug that made multi-endpoint support impossible to trust.

        ``OpenCodeClient.connect()`` used to re-run discovery and *overwrite* the
        endpoint it had been constructed with.  With one OpenCode on the machine
        that was invisible - discovery always found the same one.  With two, every
        client snapped back to the first service discovered, so the client built
        for a remote workspace read the local service's sessions, and the remote
        conversation could never appear no matter how many endpoints were found.

        A client is a handle to one endpoint.  This is the whole reason it stays
        one.
        """
        from petcore.client import OpenCodeClient

        local = self._serve("local", [self._session("ses_a", "local", "C:/work")],
                            [0, 100])
        remote = self._serve("remote", [self._session("ses_b", "remote", "/home/x")],
                             [0, 200])
        self._name_endpoints(local, remote)

        from petcore import endpoint
        for ep in endpoint.discover_all(password="test"):
            if ep.url not in (local, remote):
                continue
            client = OpenCodeClient(endpoint=ep)
            self.assertTrue(client.connect(), "must connect to its own endpoint")
            self.assertEqual(client.endpoint.url, ep.url,
                             "connect() must not replace the endpoint it was "
                             "given with whatever discovery finds first")
            wanted = "ses_a" if ep.url == local else "ses_b"
            self.assertEqual([s.get("id") for s in client.list_sessions()], [wanted],
                             "each client must read its own service's sessions")

    def test_pruning_keeps_sessions_from_every_endpoint(self):
        """The trap a multi-endport merge walks straight into.

        ``prune`` keeps only the ids seen in the last poll.  Polling one endpoint
        per cycle - or merging each endpoint's list but keeping only one list -
        silently deletes every other endpoint's sessions on the next tick, so the
        remote card appears for one poll and then vanishes forever.
        """
        from petcore import reducer
        sessions = {}
        reducer.reconcile(sessions, {},
                          [self._session("ses_a", "a", "C:/a"),
                           self._session("ses_b", "b", "/b")],
                          {}, 1792290000000)
        self.assertEqual(len(sessions), 2)
        reducer.prune(sessions, keep_ids=["ses_a", "ses_b"])
        self.assertEqual(sorted(sessions), ["ses_a", "ses_b"],
                         "both endpoints' sessions must be kept")

    def test_the_observer_can_watch_several_endpoints_at_once(self):
        """The wiring, not just the merge."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "opencode_pet.py"), "r",
                  encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        observer = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Observer":
                observer = node
        self.assertIsNotNone(observer)
        methods = {n.name for n in observer.body
                   if isinstance(n, ast.FunctionDef)}
        self.assertIn("_sync_endpoints", methods,
                      "the observer must be able to find extra services")
        self.assertIn("_start_stream", methods,
                      "each service needs its own event stream")
        body = ast.dump(observer)
        self.assertIn("clients", body,
                      "and more than one client has to be kept")


class TestOneInstallationOnly(unittest.TestCase):
    """The pet lives in OpenCode's config, and only there.

    A copy of this project started from anywhere else used to get its own
    private lock, its own private pet - and a sentinel that would stop whichever
    pet it found first.  That is how a development folder on the Desktop killed
    the installed pet: it matched on the bare script name, could not tell whose
    process it had found, and terminated it.
    """

    def test_the_lock_is_shared_by_every_copy_on_the_machine(self):
        from petcore import paths
        import petcore.lock as lock_mod
        lock_dir = paths.lock_dir()
        self.assertTrue(lock_dir, "there must be one shared lock directory")
        self.assertNotIn("state", os.path.basename(lock_dir).lower(),
                         "the lock must not live under a per-copy state dir")
        # It is anchored to OpenCode's config, so it is the same path for every
        # APP_ROOT - including an installed copy and a checkout on the Desktop.
        self.assertTrue(os.path.normcase(lock_dir).startswith(
            os.path.normcase(paths.opencode_config_dir())))

    def test_a_lock_in_the_old_per_copy_location_is_not_consulted(self):
        from petcore.lock import lock_path
        path = lock_path("pet.lock")
        self.assertNotIn(os.path.join("state", "pet.lock"),
                         os.path.normpath(path).lower())

    def test_a_pet_from_another_installation_is_not_matched(self):
        """Process matching is scoped to this installation's root."""
        import pet_watcher
        self.assertFalse(pet_watcher._started_from(
            [r"C:\\somewhere\\else\\opencode_pet.py"],
            r"C:\\Users\\me\\.config\\opencode\\pet"))
        self.assertTrue(pet_watcher._started_from(
            [r"C:\\Users\\me\\.config\\opencode\\pet\\opencode_pet.py"],
            r"C:\\Users\\me\\.config\\opencode\\pet"))
        # A sibling directory that merely shares a prefix is still a stranger.
        self.assertFalse(pet_watcher._started_from(
            [r"C:\\Users\\me\\.config\\opencode\\pet-dev\\opencode_pet.py"],
            r"C:\\Users\\me\\.config\\opencode\\pet"))

    def test_kill_pet_only_ever_targets_this_installation(self):
        """``kill_pet`` must not be a machine-wide switch."""
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "pet_watcher.py"), "r",
                  encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "kill_pet":
                continue
            calls = [n for n in ast.walk(node)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name)
                     and n.func.id == "_running_command_lines"]
            for call in calls:
                self.assertIsNotNone(
                    call.keywords and any(k.arg == "root" for k in call.keywords),
                    "kill_pet must scope its process match with root=")
            return
        self.fail("kill_pet not found")

    def test_the_autostart_launcher_points_only_at_the_installation(self):
        """The pet must come up at sign-in from the config directory alone."""
        import install
        import petcore.paths as paths
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "install.py"), "r", encoding="utf-8") as h:
            source = h.read()
        self.assertIn("pet_watcher.py", source)
        # The launcher is written from ``target_root()``, i.e. the install
        # directory - never from the directory the installer happens to run in.
        tree = ast.parse(source)
        functions = {n.name: n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)}
        self.assertIn("target_root", functions)
        body = ast.dump(functions["target_root"])
        self.assertIn("opencode_config_dir", body)
        self.assertTrue(os.path.isdir(paths.opencode_config_dir()))


# ---------------------------------------------------------------------------
class TestTrayMenu(unittest.TestCase):
    """The tray menu is built from the catalogue, never from literals."""

    def setUp(self):
        try:
            from PyQt5.QtWidgets import QApplication
        except ImportError:                                  # pragma: no cover
            self.skipTest("PyQt5 is not available")
        global _QT_APP
        _QT_APP = QApplication.instance() or QApplication(["opencode-pet-tests"])
        i18n.set_language("zh")

    @staticmethod
    def _handlers(seen):
        return {
            "open": lambda: seen.append("open"),
            "quit": lambda: seen.append("quit"),
            "about": lambda: seen.append("about"),
            "demo": lambda: seen.append("demo"),
            "reset_pos": lambda: seen.append("reset_pos"),
            "toggle_expand": lambda: seen.append("toggle_expand"),
            "toggle_topmost": lambda: seen.append("toggle_topmost"),
            "toggle_titles": lambda: seen.append("toggle_titles"),
            "toggle_silent": lambda: seen.append("toggle_silent"),
            "set_scale": lambda v: seen.append(("scale", v)),
            "set_lang": lambda v: seen.append(("lang", v)),
            "has_cards": lambda: True,
            "expanded": lambda: False,
            "topmost": lambda: True,
            "show_titles": lambda: True,
            "silent": lambda: False,
            "lang": lambda: "zh",
        }

    def test_menu_builds_in_both_languages_with_every_handler_wired(self):
        from petui.tray import SCALES, Tray
        seen = []
        tray = Tray(self._handlers(seen), scale=1.0)
        try:
            for lang in i18n.LANGS:
                i18n.set_language(lang)
                menu = tray._build()
                labels = [a.text() for a in menu.actions() if a.text()]
                for key in ("menu.open", "menu.expand", "menu.quit",
                            "menu.about", "menu.demo"):
                    self.assertIn(i18n.t(key), labels, "%s/%s" % (key, lang))
                for label in labels:
                    self.assertNotIn("{", label)
                    self.assertTrue(label.strip())
                submenus = [a.menu() for a in menu.actions() if a.menu()]
                scale_menu = [m for m in submenus
                              if m.title() == i18n.t("menu.scale")]
                self.assertEqual(len(scale_menu), 1, lang)
                self.assertEqual(
                    [a.text() for a in scale_menu[0].actions() if a.text()],
                    ["%d%%" % round(v * 100) for v in SCALES])
                lang_menu = [m for m in submenus
                             if m.title() == i18n.t("menu.language")]
                self.assertEqual(len(lang_menu), 1, lang)
                self.assertEqual(
                    [a.text() for a in lang_menu[0].actions() if a.text()],
                    ["中文", "English"])
                for menu_item in menu.actions():
                    sub = menu_item.menu()
                    if sub is not None:
                        for child in sub.actions():
                            if child.text() and child.isEnabled():
                                child.trigger()
                    elif menu_item.text() and menu_item.isEnabled():
                        menu_item.trigger()
            i18n.set_language("zh")
        finally:
            i18n.set_language("zh")
        for key in ("open", "quit", "about", "demo", "reset_pos",
                    "toggle_expand", "toggle_topmost", "toggle_titles",
                    "toggle_silent"):
            self.assertIn(key, seen, key)
        self.assertIn(("scale", 2.0), seen)
        self.assertIn(("lang", "en"), seen)

    def test_the_language_menu_uses_endonyms(self):
        self.assertEqual(i18n.language_name("zh"), "中文")
        self.assertEqual(i18n.language_name("en"), "English")


# ---------------------------------------------------------------------------
try:
    import PyQt5  # noqa: F401
    TestRenderer = _define_renderer_tests()
except ImportError:                                         # pragma: no cover
    pass


# ---------------------------------------------------------------------------
def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    ok = result.wasSuccessful()
    # PyQt5 is unhappy about being torn down at interpreter exit, so the
    # application is shut down explicitly while the objects still exist.
    if _QT_APP is not None:
        try:
            _QT_APP.closeAllWindows()
            _QT_APP.quit()
            _QT_APP.processEvents()
        except Exception:                                  # noqa: BLE001
            pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
