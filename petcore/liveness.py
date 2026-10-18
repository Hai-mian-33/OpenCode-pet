"""Turning "not seen right now" into "really gone".

The bug this module exists to prevent
------------------------------------

Both halves of the pet used to act on a **single** failed process scan:

    alive = endpoint.app_alive()
    if alive and not app_up:  ...
    elif not alive and app_up:
        app_up = False
        kill_pet()

``app_alive()`` is a live enumeration of running processes
(``petcore/endpoint.py``).  It is not a fact about the world; it is one
observation, and it can come back empty for reasons that have nothing to do
with OpenCode closing - the service restarting during an update, a plugin
reload, an antivirus scan holding a handle, a moment where ``psutil`` throws
and the ``tasklist`` fallback also comes back empty.

Acting on one such sample is how the pet disappeared.  The sentinel's own log
recorded it, in the same second:

    20:13:43 opencode detected (service only)
    20:13:43 opencode and its background service are both gone

It saw OpenCode, then one poll later it "knew" OpenCode was gone, stopped the
pet - and, because the sentinel only starts at sign-in, nothing brought the
pet back for the rest of the session.

The rule
--------

A real shutdown is *sustained*.  A blip is not.  So absence has to be observed
repeatedly, over a wall-clock grace period, before anything irreversible
happens.  This is the same reasoning as ``WAIT_CONFIRM_MS`` on the other side
of the pet - a claim that something is happening needs confirming before it is
announced, and a claim that something has *ended* needs confirming before it
is acted on.

Both the pet and the sentinel use this one class, so "how long is long enough"
is decided in exactly one place instead of two that can drift apart.
"""

from __future__ import annotations

import time

# How long OpenCode must be continuously unobserved before it counts as gone.
#
# Long enough to ride out a service restart or a transient scan failure, short
# enough that a genuine quit still tears the pet down promptly.  The pet hides
# itself the instant the *window* closes (that path is measured in
# milliseconds), so this delay is invisible in normal use - it only governs the
# final teardown.
GONE_GRACE_SECONDS = 6.0

# ...and never act on fewer observations than this, however fast they arrive.
# A grace period measured only in seconds would be defeated by a polling loop
# that spins; the sample count is the floor.
GONE_MIN_POLLS = 4

# How long to distrust the very first process scan after the machine has been
# asleep.  See ``SleepWatch``.
RESUME_SETTLE_SECONDS = 20.0


class SleepWatch:
    """Notice that the machine suspended between two observations.

    This is the fourth failure mode, and the one that explains the pet dying
    overnight while everything looked healthy.

    ``psutil.process_iter`` walks the whole process table.  On a laptop that
    has just resumed, that walk can come back empty or raise - the system is
    still rebuilding its process state, and the scheduler may not have run
    every other process yet.  A liveness check that trusts such a sample sees
    "OpenCode is gone" when OpenCode is very much alive, and the debounce above
    cannot tell the difference: the machine really *was* unreachable for the
    length of the resume.

    The giveaway is that the two clocks disagree.  ``time.time`` advances
    across a suspend; a monotonic clock read back-to-back barely moves.  So
    sampling both and watching the *difference* detects a suspend with no
    privileges, no event log and no guessing - and costs one extra call per
    poll.

    This laptop sleeps five times an hour.  That is not an edge case here.
    """

    def __init__(self, tolerance: float = 2.0,
                 settle: float = RESUME_SETTLE_SECONDS) -> None:
        # Anything above this many seconds of disagreement between the wall
        # clock and the monotonic clock is a suspend, not a slow poll.
        self.tolerance = float(tolerance)
        self.settle = float(settle)
        self._last = None              # type: tuple | None
        self._settle_until = None      # type: float | None

    @property
    def settling(self) -> bool:
        """True while a resume is still being given time to settle."""
        return self.settling_at()

    def settling_at(self, mono: float = None) -> bool:
        """``settling``, against an explicit clock.

        The ``now`` argument is what lets the tests drive a resume without
        sleeping: they advance a fake monotonic clock, so the deadline has to be
        compared against that same fake clock rather than the real one.
        """
        if self._settle_until is None:
            return False
        mono = time.monotonic() if mono is None else mono
        return mono < self._settle_until

    def update(self, wall: float = None, mono: float = None) -> bool:
        """Record a sample; return True if the machine had been asleep.

        Both clocks are passed in rather than read here so the tests can drive
        a suspend deterministically.
        """
        wall = time.time() if wall is None else float(wall)
        mono = time.monotonic() if mono is None else float(mono)

        slept = False
        if self._last is not None:
            d_wall = wall - self._last[0]
            d_mono = mono - self._last[1]
            if d_wall - d_mono > self.tolerance:
                slept = True
                # The gap we just observed is the sleep itself, so start
                # settling from *now* rather than from the stale sample.
                self._settle_until = mono + self.settle
        self._last = (wall, mono)
        return slept


class AbsentConfirm:
    """Latch "OpenCode has been gone long enough" until it is seen again.

    Pure: it takes the observation and a clock, and holds no state beyond the
    counter.  Callers pass ``time.monotonic`` in, so the tests drive it with a
    fake clock instead of sleeping.
    """

    def __init__(self, grace: float = GONE_GRACE_SECONDS,
                 min_polls: int = GONE_MIN_POLLS) -> None:
        self.grace = float(grace)
        self.min_polls = max(1, int(min_polls))
        self._missing_since = None      # type: float | None
        self._samples = 0
        self._confirmed = False

    # -- queries ----------------------------------------------------------
    @property
    def confirmed(self) -> bool:
        """True once absence has been sustained; stays true until seen again."""
        return self._confirmed

    @property
    def samples(self) -> int:
        """Consecutive unobserved polls so far."""
        return self._samples

    def pending(self, now: float = None) -> float:
        """Seconds of sustained absence still required.  0 once confirmed."""
        if self._confirmed or self._missing_since is None:
            return 0.0
        now = time.monotonic() if now is None else now
        by_time = self.grace - (now - self._missing_since)
        # The sample floor is expressed as time too, so both conditions can be
        # reported by one number: a caller polling faster than the floor still
        # has to wait for it.
        by_polls = max(0, self.min_polls - self._samples) * (
            self.grace / float(self.min_polls))
        return max(0.0, by_time, by_polls)

    # -- the one method that matters --------------------------------------
    def update(self, alive: bool, now: float = None, trust: bool = True) -> bool:
        """Record one observation; return True on the transition to confirmed.

        Returning the *transition* rather than the level is deliberate: both
        callers act by tearing the pet down, and doing that on every poll after
        the fact would be noise.  One absence, one teardown.

        ``trust=False`` marks the observation as unreliable - the machine has
        just resumed from sleep and the process table cannot be believed yet.
        Such a sample is discarded completely: it neither counts towards the
        grace period nor resets an absence already in progress, because it says
        nothing either way.  Discarding it is the whole point: the alternative
        is treating a resume as OpenCode closing.
        """
        if not trust:
            return False

        now = time.monotonic() if now is None else now

        if alive:
            self._missing_since = None
            self._samples = 0
            self._confirmed = False
            return False

        if self._missing_since is None:
            self._missing_since = now
        self._samples += 1

        if self._confirmed:
            return False
        if (self._samples >= self.min_polls
                and (now - self._missing_since) >= self.grace):
            self._confirmed = True
            return True
        return False
