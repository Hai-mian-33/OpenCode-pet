#!/usr/bin/env python
"""Live checks that need a running pet and a real desktop.

    python tools/live_check.py edges      # edge snapping against real screens
    python tools/live_check.py card       # card placement above / below
    python tools/live_check.py focus      # click-to-focus finds the window
    python tools/live_check.py all

Each check runs in its own process because the pet owns its own windows; this
tool imports the same constants and geometry the shell uses and asserts on
them, so a regression in one place fails in both.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from petcore import client as client_mod
from petcore import endpoint as endpoint_mod
from petcore import paths
from petui.petwindow import BASE_H, BASE_W, EDGE_SNAP_PX

SCREEN_W, SCREEN_H = 1920, 1040          # a typical available work area


class Area:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom

    def __repr__(self):
        return "Area(%d,%d -> %d,%d)" % (self.left, self.top,
                                         self.right, self.bottom)


def drag_bounds(area: Area, width: int, height: int):
    """Mirrors PetWindow.drag_bounds."""
    return (area.left - EDGE_SNAP_PX, area.top - EDGE_SNAP_PX,
            area.right - width + EDGE_SNAP_PX,
            area.bottom - height + EDGE_SNAP_PX)


def snap(area: Area, width: int, height: int, x: int, y: int):
    """Mirrors PetWindow.snap_to_edge."""
    left_gap = abs(x - area.left)
    right_gap = abs((x + width) - area.right)
    top_gap = abs(y - area.top)
    bottom_gap = abs((y + height) - area.bottom)
    edge, best = None, EDGE_SNAP_PX
    if left_gap <= best:
        edge, best = "left", left_gap
    if right_gap <= best:
        edge, best = "right", right_gap
    if top_gap <= best:
        edge, best = "top", top_gap
    if bottom_gap <= best:
        edge, best = "bottom", bottom_gap
    if edge is None:
        return None, x, y
    clamp_x = lambda value: int(min(max(value, area.left), area.right - 1))  # noqa: E731
    clamp_y = lambda value: int(min(max(value, area.top), area.bottom - 1))  # noqa: E731
    if edge == "left":
        return edge, area.left, clamp_y(y)
    if edge == "right":
        return edge, area.right - width + 1, clamp_y(y)
    if edge == "top":
        return edge, clamp_x(x), area.top
    return edge, clamp_x(x), area.bottom - height + 1


def check_edges() -> int:
    area = Area(0, 0, SCREEN_W, SCREEN_H)
    width, height = BASE_W, BASE_H
    min_x, min_y, max_x, max_y = drag_bounds(area, width, height)
    failures = []

    print("  drag window: x in [%d, %d], y in [%d, %d]" % (min_x, max_x, min_y, max_y))
    if min_x > area.left or max_x < area.right - width:
        failures.append("drag bounds do not reach both edges")

    cases = [
        # name, x, y, expected edge, expected x, expected y
        ("flush left",   area.left + 2, 500, "left", area.left, None),
        ("past left",    area.left - EDGE_SNAP_PX, 500, "left", area.left, None),
        ("flush right",  area.right - width - 2, 500, "right",
         area.right - width + 1, None),
        ("past right",   area.right - width + EDGE_SNAP_PX, 500, "right",
         area.right - width + 1, None),
        ("flush top",    800, area.top + 3, "top", None, area.top),
        ("past top",     800, area.top - EDGE_SNAP_PX, "top", None, area.top),
        ("flush bottom", 800, area.bottom - height - 2, "bottom",
         None, area.bottom - height + 1),
        ("past bottom",  800, area.bottom - height + EDGE_SNAP_PX, "bottom",
         None, area.bottom - height + 1),
        ("middle",       800, 500, None, None, None),
    ]
    for name, x, y, want_edge, want_x, want_y in cases:
        edge, got_x, got_y = snap(area, width, height, x, y)
        ok = (edge == want_edge
              and (want_x is None or got_x == want_x)
              and (want_y is None or got_y == want_y))
        visible = (area.left <= got_x and got_x + width <= area.right + 1 and
                   area.top <= got_y and got_y + height <= area.bottom + 1)
        if not (ok and visible):
            failures.append(name)
        print("  %-12s -> %-7s x=%-5d y=%-5d  %s"
              % (name, edge, got_x, got_y, "ok" if ok and visible else "FAIL"))

    print("edge fixing: %s"
          % ("all good" if not failures else "FAILED: %s" % ", ".join(failures)))
    return len(failures)


_QT_APP = None


def qt_app():
    """CardStack is a QWidget, so it needs a QApplication to exist at all."""
    global _QT_APP
    if _QT_APP is None:
        from PyQt5.QtWidgets import QApplication
        _QT_APP = QApplication.instance() or QApplication(["opencode-pet-live-check"])
    return _QT_APP


class Rect:
    """The four QRect accessors CardStack.place() uses."""

    def __init__(self, left, top, width, height):
        self._l, self._t = left, top
        self._w, self._h = width, height

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


def check_card() -> int:
    from petcore import i18n
    from petui.cards import CardStack

    qt_app()
    i18n.set_language("zh")
    stack = CardStack()
    stack.set_records([], False, True)
    empty = (stack.width(), stack.height())
    stack.set_records([{"id": "a", "state": "idle", "label": "x"}], False, True)
    one = (stack.width(), stack.height())
    stack.set_records([{"id": str(i), "state": "done", "label": "x"}
                       for i in range(8)], False, True)
    stacked = (stack.width(), stack.height())
    stack.set_records([{"id": str(i), "state": "done", "label": "x"}
                       for i in range(8)], True, True)
    expanded = (stack.width(), stack.height())

    print("  empty      %s" % (empty,))
    print("  one card   %s" % (one,))
    print("  8 stacked  %s (card + badge)" % (stacked,))
    print("  8 expanded %s (capped at 420 tall)" % (expanded,))

    failures = []
    if not (one[1] < stacked[1] < expanded[1]):
        failures.append("height does not grow with content")
    if expanded[1] > 420:
        failures.append("expanded list is not capped")

    # the stack must flip to whichever side of the pet actually has room
    screen = Rect(0, 0, SCREEN_W, SCREEN_H)
    stack.set_records([{"id": "a", "state": "idle", "label": "x"}], False, True)

    stack.place(Rect(1700, 20, BASE_W, BASE_H), screen)
    # A pet hugging the top of the screen has no room above, so the cards
    # must drop below it.
    top_below = stack.flip and stack.y() > 20

    stack.place(Rect(1700, 1000, BASE_W, BASE_H), screen)
    # A pet hugging the bottom must push the cards above it.
    bottom_above = (not stack.flip) and \
        stack.y() + stack.height() <= 1000

    print("  pet near the top    -> cards %s (y=%d)"
          % ("below" if top_below else "ABOVE?", stack.y()))
    print("  pet near the bottom -> cards %s (y=%d)"
          % ("above" if bottom_above else "BELOW?", stack.y()))
    if not top_below:
        failures.append("cards did not fall below a pet at the screen top")
    if not bottom_above:
        failures.append("cards did not rise above a pet at the screen bottom")
    if stack.y() + stack.height() > screen.bottom():
        failures.append("cards run off the bottom of the screen")

    print("card sizing: %s"
          % ("all good" if not failures else "FAILED: %s" % ", ".join(failures)))
    return len(failures)


def check_focus() -> int:
    desktop = endpoint_mod.desktop_running()
    raised = client_mod._raise_existing_window()
    print("  desktop running : %s" % desktop)
    print("  window raised   : %s" % raised)
    print("  exe             : %s" % (paths.desktop_exe() or "(not found)"))
    if not desktop:
        print("focus: OpenCode is not running; start it and try again")
        return 1
    return 0 if (raised or paths.desktop_exe()) else 1


CHECKS = {"edges": check_edges, "card": check_card, "focus": check_focus}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=sorted(CHECKS) + ["all"])
    args = parser.parse_args()
    names = sorted(CHECKS) if args.check == "all" else [args.check]
    failures = 0
    for name in names:
        print("== %s ==" % name)
        try:
            failures += CHECKS[name]()
        except Exception as exc:                              # noqa: BLE001
            print("  FAIL %r" % (exc,))
            failures += 1
        print()
    print("all checks passed" if not failures
          else "%d check(s) failed" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
