"""The OpenO character, drawn live with QPainter.

Design notes
------------
The silhouette is OpenCode's own geometric mark: a heavy square **O** - a dark
outer body with a lighter inner panel and a two-tone split face.  Nothing here
is a redrawn lookalike; the geometry comes from the official light logo
(``assets/opencode-logo-light.svg``), which is a 240x300 dark field with a
120x180 lighter square ring.

Two rules keep the character readable at 100 px tall:

* **Black, white and grey are permanent.**  The body is always ``#242424``,
  the face always ``#F9F9F8`` over ``#CACACA``.  Colour is only ever spent on
  the small status lamp and on the attention marks, never on the body - so the
  brand mark stays recognisable in every state.
* **The O never changes.**  State is carried by the eyes, the mouth, the tilt
  and the lamp, exactly as the three earlier pets in this series carry state
  through posture rather than through a different character.

Every state is a function of ``(state, t)`` where ``t`` is a millisecond
clock, so the renderer holds no state of its own and can be driven offscreen
by ``tools/gen_preview.py``.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPen,
                         QPolygonF)

# ---------------------------------------------------------------------------
# palette - fixed by the design brief
# ---------------------------------------------------------------------------
BODY = QColor("#242424")           # the O: permanent charcoal
BODY_EDGE = QColor("#121212")
FACE_TOP = QColor("#F9F9F8")       # warm white
FACE_LOW = QColor("#CACACA")       # light grey lower half
INK = QColor("#303030")            # eyes / mouth
SHADOW = QColor(0x77, 0x77, 0x77, 33)

# Status lamp colours.  These are the only saturated colours in the character.
LAMP = {
    "idle":      QColor("#9AA0A6"),   # grey
    "thinking":  QColor("#F5C518"),   # yellow
    "executing": QColor("#3B82F6"),   # blue
    "waiting":   QColor("#F59E0B"),   # orange
    "done":      QColor("#22C55E"),   # green
    "error":     QColor("#EF4444"),   # red
    "longtask":  QColor("#3B82F6"),   # blue
    "drag":      QColor("#6B6B6B"),
    "offline":   QColor("#5A5A5A"),
    # Dimmed on purpose: the service is alive but the window is gone, so this is
    # "asleep", not "alert but stuck".
    "closed":    QColor("#7E8288"),
}

# ---------------------------------------------------------------------------
# design-space geometry (the official logo's own coordinate system)
# ---------------------------------------------------------------------------
DESIGN_W = 240.0
DESIGN_H = 208.0

BODY_RECT = QRectF(69, 27, 102, 131)
FACE_RECT = QRectF(92, 50, 56, 82)
FACE_SPLIT_Y = 94.0                 # where the face turns from white to grey
EYE_L = QRectF(102, 66, 9, 12)
EYE_R = QRectF(129, 66, 9, 12)
MOUTH_W = 22.0
MOUTH_Y = 108.0
LAMP_RECT = QRectF(163, 28, 14, 14)
FOOT_L = QRectF(82, 157, 18, 24)
FOOT_R = QRectF(140, 157, 18, 24)
HAND_L = QRectF(43, 86, 9, 10)
HAND_R = QRectF(188, 76, 11, 11)
SHOULDER_Y = 103.0
SHADOW_CY = 191.0
SHADOW_RX = 56.0
SHADOW_RY = 8.0

# Default on-screen size in logical pixels (the design ratio is kept exactly).
BASE_W = 132
BASE_H = int(round(BASE_W * DESIGN_H / DESIGN_W))       # 114


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def _ease_out(t: float) -> float:
    return 1.0 - (1.0 - _clamp(t, 0.0, 1.0)) ** 2


class OpenO:
    """Draws the character into any QRectF at any size."""

    def __init__(self) -> None:
        self.state = "idle"
        self.state_since = 0
        self.lamp_override: QColor = None      # used by the tray and previews
        self.hover = False

    # -- public ----------------------------------------------------------
    def paint(self, painter: QPainter, rect: QRectF, state: str, t: float,
              dragging: bool = False, drag_vx: float = 0.0) -> None:
        """Render one frame.

        ``state`` is one of the reducer's state names; ``t`` is milliseconds
        since an arbitrary epoch (only differences matter).
        """
        state = "drag" if dragging else state
        scale = rect.width() / DESIGN_W
        pose = self._pose(state, t, drag_vx)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.translate(rect.left(), rect.top())
        painter.scale(scale, scale)

        self._shadow(painter, pose)
        painter.translate(pose["cx"], 0)
        painter.rotate(pose["tilt"])
        painter.translate(-pose["cx"], 0)
        painter.translate(0, pose["dy"])

        self._legs(painter, state, t, pose)
        self._body(painter, pose)
        self._arms(painter, state, t, pose)
        self._face(painter, state, t, pose)
        self._lamp(painter, state, t, pose)
        self._marks(painter, state, t, pose)

        painter.restore()

    # -- posture ---------------------------------------------------------
    def _pose(self, state: str, t: float, drag_vx: float = 0.0) -> Dict[str, float]:
        """Everything that moves, resolved from the clock and the state."""
        cx = BODY_RECT.center().x()
        pose = {"cx": cx, "dx": 0.0, "dy": 0.0, "tilt": 0.0,
                "squash": 1.0, "shadow": 1.0, "shadow_dy": 0.0, "arm_lift": 0.0}

        if state == "idle":
            breath = math.sin(t / 1400.0)
            pose["dy"] = breath * 1.3
            pose["squash"] = 1.0 + breath * 0.012
            pose["tilt"] = math.sin(t / 5200.0) * 0.8
            pose["shadow"] = 1.0 - breath * 0.06

        elif state == "closed":
            # Asleep: a much slower breath than idle, no sway, and it settles
            # downward.  It has to read as "resting", because the alternative -
            # carrying on with the live animations - is what made a closed
            # OpenCode look like the pet was urgently working on something.
            breath = math.sin(t / 2600.0)
            pose["dy"] = 2.4 + breath * 0.8
            pose["squash"] = 1.0 + breath * 0.006
            pose["tilt"] = 0.0
            pose["shadow"] = 0.9 - breath * 0.04

        elif state == "thinking":
            pose["dy"] = math.sin(t / 1800.0) * 1.1
            pose["tilt"] = math.sin(t / 1900.0) * 2.4
            pose["arm_lift"] = -2.0
            pose["shadow"] = 0.94

        elif state == "executing":
            # A short, punchy bounce: lift on the rising edge, land on the beat.
            phase = (t % 620.0) / 620.0
            bounce = math.sin(phase * math.pi)
            pose["dy"] = -bounce * 4.2
            pose["squash"] = 1.0 + (1.0 - bounce) * 0.02 - bounce * 0.02
            pose["tilt"] = math.sin(t / 310.0) * 1.4
            pose["shadow"] = 0.88 - bounce * 0.06
            pose["shadow_dy"] = bounce * 1.5

        elif state == "waiting":
            # Held up and a little larger - it wants to be noticed.
            pose["dy"] = -2.0 + math.sin(t / 900.0) * 0.9
            pose["squash"] = 1.04
            pose["tilt"] = math.sin(t / 1500.0) * 1.2
            pose["shadow"] = 0.82
            pose["shadow_dy"] = 2.4

        elif state == "done":
            # One small hop, then settle: a 900 ms parabola repeated rarely.
            cycle = 2600.0
            phase = (t % cycle) / cycle
            if phase < 0.42:
                hop = math.sin((phase / 0.42) * math.pi)
                rise = _ease_out(phase / 0.42)
                pose["dy"] = -hop * 16.0
                pose["shadow"] = 1.0 - hop * 0.35
                pose["shadow_dy"] = hop * 5.0
                pose["squash"] = 1.0 + (1.0 - hop) * 0.03
            else:
                settle = math.sin((phase - 0.42) / 0.18 * math.pi)
                pose["dy"] = settle * 2.4
                pose["squash"] = 1.0 - settle * 0.05
                pose["shadow"] = 1.0 + settle * 0.05
            pose["tilt"] = math.sin(t / 640.0) * 2.0

        elif state == "error":
            wobble = math.sin(t / 430.0)
            pose["tilt"] = wobble * 3.4
            pose["dy"] = 1.6 + wobble * 1.2
            pose["squash"] = 0.97
            pose["arm_lift"] = 1.5
            pose["shadow"] = 0.9

        elif state == "longtask":
            rock = math.sin(t / 2300.0)
            breath = math.sin(t / 1700.0)
            pose["dy"] = rock * 2.6 + breath * 1.4
            pose["tilt"] = rock * 1.8
            pose["squash"] = 1.0 + breath * 0.02
            pose["shadow"] = 0.9 - breath * 0.05

        elif state == "drag":
            # Feet tuck up, the body leans into the throw, the shadow drops.
            lean = _clamp(drag_vx / 900.0, -1.0, 1.0)
            pose["tilt"] = lean * 11.0
            pose["dy"] = -7.0
            pose["squash"] = 1.05
            pose["shadow"] = 0.55
            pose["shadow_dy"] = 7.0
            pose["dx"] = lean * 2.0

        elif state == "offline":
            pose["dy"] = 2.4
            pose["squash"] = 0.96
            pose["tilt"] = 0.0
            pose["shadow"] = 0.85

        pose["dy"] += pose["dx"]
        return pose

    # -- parts -----------------------------------------------------------
    def _shadow(self, p: QPainter, pose: Dict[str, float]) -> None:
        scale = pose["shadow"]
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(SHADOW))
        p.setOpacity(_clamp(scale, 0.2, 1.0))
        p.drawEllipse(QPointF(pose["cx"], SHADOW_CY + pose["shadow_dy"]),
                      SHADOW_RX * scale, SHADOW_RY * scale)
        p.restore()

    def _legs(self, p: QPainter, state: str, t: float, pose: Dict[str, float]) -> None:
        tucked = state == "drag"
        lift = 10.0 if tucked else 0.0
        spread = 1.0 if tucked else 0.0
        swing = 0.0
        if state == "executing":
            swing = math.sin(t / 310.0) * 2.2
        elif state == "longtask":
            swing = math.sin(t / 2300.0) * 1.6
        elif state == "idle":
            swing = math.sin(t / 1400.0) * 0.6

        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(BODY))
        p.translate(0, lift)
        for rect, sign in ((FOOT_L, -1.0), (FOOT_R, 1.0)):
            moved = QRectF(rect)
            moved.translate(0, swing * sign * (0.4 if tucked else 1.0))
            moved.translate(spread * sign * 2.0, 0)
            self._rounded(p, moved, 3.0)
        p.restore()

    def _rounded(self, p: QPainter, rect: QRectF, radius: float) -> None:
        if radius <= 0:
            p.drawRect(rect)
            return
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.drawPath(path)

    def _body(self, p: QPainter, pose: Dict[str, float]) -> None:
        p.save()
        # The O itself: a square ring with a hollow centre.  Drawing the ring
        # as one even-odd path keeps the official silhouette exact.
        squash = pose["squash"]
        body = QRectF(BODY_RECT)
        if squash != 1.0:
            body = QRectF(body.x(), body.y() + (1 - squash) * 14.0,
                          body.width(), body.height() * squash)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(BODY))
        p.drawRect(body)
        p.setPen(QPen(BODY_EDGE, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawRect(body.adjusted(0.8, 0.8, -0.8, -0.8))
        p.restore()

    def _arms(self, p: QPainter, state: str, t: float, pose: Dict[str, float]) -> None:
        lift = pose["arm_lift"]
        p.save()
        p.setPen(QPen(BODY, 9.0, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        p.setBrush(Qt.NoBrush)

        if state == "executing":
            swing_l = -7.0 + math.sin(t / 310.0) * 4.0
            swing_r = 7.0 - math.sin(t / 310.0) * 4.0
        elif state == "done":
            swing_l = -16.0 - math.sin(t / 640.0) * 5.0
            swing_r = 16.0 + math.sin(t / 640.0) * 5.0
        elif state == "waiting":
            swing_l = -11.0
            swing_r = 11.0
        elif state == "error":
            swing_l = 9.0 + math.sin(t / 430.0) * 2.0
            swing_r = 9.0 - math.sin(t / 430.0) * 2.0
        elif state == "drag":
            swing_l = -5.0
            swing_r = 5.0
        elif state == "thinking":
            swing_l = -4.0 + math.sin(t / 1900.0) * 2.0
            swing_r = 4.0 - math.sin(t / 1900.0) * 2.0
        else:
            swing_l = swing_r = 0.0

        # Left arm: shoulder -> elbow -> hand, mirroring the reference SVG.
        p.drawPolyline(QPolygonF([
            QPointF(70, SHOULDER_Y + lift),
            QPointF(47 - swing_l * 0.4, 109 + lift + swing_l * 0.3),
            QPointF(42 - swing_l * 0.7, 94 + lift + swing_l * 0.6),
        ]))
        p.drawPolyline(QPolygonF([
            QPointF(169, SHOULDER_Y + lift),
            QPointF(190 + swing_r * 0.4, 98 + lift - swing_r * 0.3),
            QPointF(195 + swing_r * 0.7, 83 + lift - swing_r * 0.6),
        ]))

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(BODY))
        hand_l = QRectF(HAND_L)
        hand_l.translate(-swing_l * 0.7, lift + swing_l * 0.6)
        p.drawRect(hand_l)
        hand_r = QRectF(HAND_R)
        hand_r.translate(swing_r * 0.7, lift - swing_r * 0.6)
        p.drawRect(hand_r)
        p.restore()

    def _face(self, p: QPainter, state: str, t: float, pose: Dict[str, float]) -> None:
        # Two-tone face panel: the lower half is grey, exactly like the logo.
        p.save()
        p.setPen(Qt.NoPen)
        top = QRectF(FACE_RECT)
        bottom = QRectF(FACE_RECT.x(), FACE_SPLIT_Y,
                        FACE_RECT.width(), FACE_RECT.bottom() - FACE_SPLIT_Y)
        p.setBrush(QBrush(FACE_TOP))
        p.drawRect(top)
        p.setBrush(QBrush(FACE_LOW))
        p.drawRect(bottom)
        p.restore()

        blink, gaze = self._gaze(state, t)
        self._eyes(p, state, t, blink, gaze)
        self._mouth(p, state, t)

    def _gaze(self, state: str, t: float) -> Tuple[float, Tuple[float, float]]:
        """``(blink_amount, (dx, dy))`` - blink in 0..1, gaze in design units."""
        if state == "idle":
            blink = self._blink(t, 4200.0, 130.0)
            return blink, (math.sin(t / 2600.0) * 1.8, 0.0)
        if state == "closed":
            # Eyes mostly shut, no gaze drift: the character is resting, and it
            # must not look like it is watching work it cannot do.
            phase = (t % 6000.0) / 6000.0
            shut = 0.75 if phase < 0.72 else 0.0
            return shut, (0.0, 1.6)
        if state == "thinking":
            return 0.0, (math.sin(t / 2900.0) * 1.0, -2.6)
        if state == "executing":
            return 0.0, (0.0, 0.4)
        if state == "waiting":
            blink = self._blink(t, 900.0, 110.0)
            return blink, (0.0, -1.0)
        if state == "done":
            return 0.0, (0.0, 0.0)
        if state == "error":
            blink = self._blink(t, 5200.0, 90.0)
            return blink, (math.sin(t / 430.0) * 1.2, 1.8)
        if state == "drag":
            return 0.0, (0.0, 0.0)
        if state == "longtask":
            blink = self._blink(t, 6000.0, 120.0)
            return blink, (math.sin(t / 3400.0) * 1.2, 0.8)
        return self._blink(t, 5000.0, 110.0), (0.0, 0.0)

    @staticmethod
    def _blink(t: float, period: float, closed_ms: float) -> float:
        """0 = open, 1 = shut.  Every third blink is a quick double."""
        phase = t % period
        if phase < closed_ms:
            return 1.0
        if period > 3000 and closed_ms + 40 < phase < closed_ms + 40 + closed_ms * 0.7:
            return 1.0
        return 0.0

    def _eyes(self, p: QPainter, state: str, t: float, blink: float,
              gaze: Tuple[float, float]) -> None:
        dx, dy = gaze
        p.save()
        p.setPen(Qt.NoPen)
        if state == "done":
            # Happy squint: two arcs instead of blocks.
            p.setPen(QPen(INK, 3.4, Qt.SolidLine, Qt.RoundCap))
            for rect in (EYE_L, EYE_R):
                cx = rect.center().x()
                cy = rect.center().y() + 2.0
                path = QPainterPath()
                path.moveTo(cx - rect.width() / 2, cy + 2.0)
                path.quadTo(cx, cy - rect.height() * 0.75, cx + rect.width() / 2, cy + 2.0)
                p.drawPath(path)
            p.restore()
            return

        if state == "error":
            # Drooped lids: the block leans and shortens.
            p.setPen(QPen(BODY, 2.6, Qt.SolidLine, Qt.SquareCap))
            for rect, sign in ((EYE_L, -1.0), (EYE_R, 1.0)):
                cx = rect.center().x() + dx
                cy = rect.center().y() + dy
                path = QPainterPath()
                path.moveTo(cx - rect.width() / 2 - 1.0, cy - 2.0 - sign * 0.0)
                path.lineTo(cx + rect.width() / 2 + 1.0, cy - 2.0)
                p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(INK))
            for rect in (EYE_L, EYE_R):
                eye = QRectF(rect)
                eye.translate(dx, dy + 2.0)
                self._rounded(p, eye, 3.0)
            p.restore()
            return

        height_scale = 1.0 - blink
        p.setBrush(QBrush(INK))
        if state == "waiting":
            # Wide eyes, plus a catchlight so they read as "asking".
            for rect in (EYE_L, EYE_R):
                eye = QRectF(rect.x(), rect.y() - 1.5, rect.width(),
                             rect.height() + 3.0)
                eye.translate(dx, dy)
                p.setBrush(QBrush(INK))
                self._rounded(p, eye, 3.4)
                glint = QRectF(eye.x() + eye.width() - 3.2, eye.y() + 1.2, 2.0, 2.0)
                p.setBrush(QBrush(FACE_TOP))
                p.drawEllipse(glint)
            p.restore()
            return

        if state == "executing":
            # Focused: slightly narrowed, squared off.
            p.setBrush(QBrush(INK))
            for rect in (EYE_L, EYE_R):
                eye = QRectF(rect.x(), rect.y() + 1.6, rect.width(),
                             max(1.5, (rect.height() - 3.2) * height_scale))
                eye.translate(dx, dy)
                self._rounded(p, eye, 1.6)
            p.restore()
            return

        for rect in (EYE_L, EYE_R):
            eye = QRectF(rect)
            if height_scale <= 0.06:
                eye = QRectF(rect.x(), rect.center().y() - 0.9, rect.width(), 1.8)
            else:
                eye.setHeight(rect.height() * height_scale)
                eye.moveTop(rect.y() + (rect.height() - eye.height()) / 2.0)
            eye.translate(dx, dy)
            self._rounded(p, eye, 3.0)
        p.restore()

    def _mouth(self, p: QPainter, state: str, t: float) -> None:
        cx = BODY_RECT.center().x()
        p.save()
        if state in ("done", "thinking", "waiting", "drag"):
            # Curved smile; small and round when there is nothing to say.
            depth = 11.0 if state == "done" else 7.0
            if state in ("thinking", "waiting", "drag"):
                # A small "o": talking, or waiting for an answer.
                p.setPen(QPen(INK, 3.0, Qt.SolidLine, Qt.RoundCap))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(cx, MOUTH_Y + 4.0), 4.6, 4.6)
            else:
                p.setPen(QPen(INK, 3.4, Qt.SolidLine, Qt.RoundCap))
                p.setBrush(Qt.NoBrush)
                path = QPainterPath()
                path.moveTo(cx - MOUTH_W / 2, MOUTH_Y)
                path.quadTo(cx, MOUTH_Y + depth, cx + MOUTH_W / 2, MOUTH_Y)
                p.drawPath(path)
            p.restore()
            return

        if state == "error":
            p.setPen(QPen(INK, 3.2, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            wobble = math.sin(t / 430.0) * 1.4
            path = QPainterPath()
            path.moveTo(cx - MOUTH_W / 2, MOUTH_Y + 6.0 + wobble)
            path.quadTo(cx - MOUTH_W / 4, MOUTH_Y + 1.0 - wobble,
                        cx, MOUTH_Y + 5.0)
            path.quadTo(cx + MOUTH_W / 4, MOUTH_Y + 1.0 + wobble,
                        cx + MOUTH_W / 2, MOUTH_Y + 6.0 - wobble)
            p.drawPath(path)
            p.restore()
            return

        if state == "closed":
            # A short, level line.  Not a smile: nothing has happened, and a
            # cheerful face over a closed app would be actively misleading.
            p.setPen(QPen(INK, 3.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - MOUTH_W / 2.4, MOUTH_Y + 1.5),
                       QPointF(cx + MOUTH_W / 2.4, MOUTH_Y + 1.5))
            p.restore()
            return

        # idle / executing / longtask / offline: a calm, level smile
        curve = 4.0 if state != "offline" else 1.5
        p.setPen(QPen(INK, 3.2, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(cx - MOUTH_W / 2, MOUTH_Y)
        path.quadTo(cx, MOUTH_Y + curve, cx + MOUTH_W / 2, MOUTH_Y)
        p.drawPath(path)
        p.restore()

    # -- lamp and attention marks ---------------------------------------
    def _lamp_colour(self, state: str, t: float) -> QColor:
        if self.lamp_override is not None:
            return QColor(self.lamp_override)
        colour = QColor(LAMP.get(state, LAMP["idle"]))
        if state == "waiting":
            # Urgent: a hard blink, because a human is being blocked.
            on = (t % 700.0) < 380.0
            colour.setAlpha(255 if on else 70)
        elif state == "error":
            pulse = 0.5 + 0.5 * math.sin(t / 480.0)
            colour.setAlpha(int(120 + 135 * pulse))
        elif state == "done":
            pulse = 0.5 + 0.5 * math.sin(t / 700.0)
            colour.setAlpha(int(170 + 85 * pulse))
        elif state == "executing":
            pulse = 0.5 + 0.5 * math.sin(t / 310.0)
            colour.setAlpha(int(150 + 105 * pulse))
        elif state == "longtask":
            # "blue breathing lamp": a slow, deep swell
            pulse = 0.5 + 0.5 * math.sin(t / 1700.0)
            colour.setAlpha(int(90 + 165 * pulse))
        elif state == "thinking":
            pulse = 0.5 + 0.5 * math.sin(t / 900.0)
            colour.setAlpha(int(140 + 115 * pulse))
        elif state == "idle":
            pulse = 0.5 + 0.5 * math.sin(t / 2600.0)
            colour.setAlpha(int(120 + 60 * pulse))
        elif state == "closed":
            # Barely lit and steady.  No blink, no urgency: the app is shut, so
            # nothing is being waited on.
            colour.setAlpha(70)
        return colour

    def _lamp(self, p: QPainter, state: str, t: float, pose: Dict[str, float]) -> None:
        colour = self._lamp_colour(state, t)
        rect = QRectF(LAMP_RECT)

        p.save()
        if colour.alpha() > 0:
            glow = QColor(colour)
            glow.setAlpha(int(colour.alpha() * 0.30))
            for spread in (7.0, 4.0):
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(glow))
                p.drawRoundedRect(rect.adjusted(-spread, -spread, spread, spread),
                                  rect.width() / 2 + spread, rect.height() / 2 + spread)
        p.setPen(QPen(FACE_TOP, 2.0))
        p.setBrush(QBrush(colour))
        self._rounded(p, rect, 3.5)
        p.restore()

    def _marks(self, p: QPainter, state: str, t: float, pose: Dict[str, float]) -> None:
        """Floating attention marks: thought dots, a bang, or sparks."""
        if state == "thinking":
            self._thought_dots(p, t)
        elif state == "waiting":
            self._bang(p, t)
        elif state == "done":
            self._sparks(p, t)
        elif state == "longtask":
            self._breath_ring(p, t, pose)

    def _thought_dots(self, p: QPainter, t: float) -> None:
        cx = BODY_RECT.center().x()
        top = BODY_RECT.top()
        p.save()
        p.setPen(Qt.NoPen)
        for index in range(3):
            delay = index * 620.0
            phase = ((t + delay) % 1860.0) / 1860.0
            y = top - 6.0 - phase * 30.0
            size = 5.4 - phase * 2.0
            alpha = int(210 * (1.0 - phase) ** 1.3)
            if size <= 0.6 or alpha <= 6:
                continue
            colour = QColor(LAMP["thinking"])
            colour.setAlpha(alpha)
            p.setBrush(QBrush(colour))
            p.drawEllipse(QPointF(cx + 14.0 - index * 2.0, y), size, size)
        p.restore()

    def _bang(self, p: QPainter, t: float) -> None:
        cx = BODY_RECT.center().x()
        top = BODY_RECT.top()
        bob = math.sin(t / 260.0) * 2.2
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(LAMP["waiting"]))
        p.drawRoundedRect(QRectF(cx - 3.4, top - 30.0 + bob, 6.8, 15.0), 2.6, 2.6)
        p.drawEllipse(QPointF(cx, top - 9.0 + bob), 3.4, 3.4)
        p.restore()

    def _sparks(self, p: QPainter, t: float) -> None:
        cx = BODY_RECT.center().x()
        top = BODY_RECT.top()
        p.save()
        p.setPen(QPen(LAMP["done"], 2.6, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        for index, (ox, oy) in enumerate(((34, -18), (-34, -22))):
            phase = ((t + index * 420.0) % 1200.0) / 1200.0
            fade = 1.0 - phase
            if fade <= 0.05:
                continue
            p.setOpacity(fade)
            x, y = cx + ox, top + oy
            p.drawLine(int(x - 5), int(y), int(x + 5), int(y))
            p.drawLine(int(x), int(y - 5), int(x), int(y + 5))
        p.restore()

    def _breath_ring(self, p: QPainter, t: float, pose: Dict[str, float]) -> None:
        """The 'blue breathing lamp' halo: a slow swell around the whole O."""
        phase = (t % 2300.0) / 2300.0
        colour = QColor(LAMP["longtask"])
        colour.setAlpha(int(110 * (1.0 - phase)))
        grow = 1.0 + phase * 0.16
        p.save()
        p.setPen(QPen(colour, 2.4))
        p.setBrush(Qt.NoBrush)
        p.translate(pose["cx"], BODY_RECT.center().y())
        p.scale(grow, grow)
        p.translate(-pose["cx"], -BODY_RECT.center().y())
        p.drawRoundedRect(BODY_RECT.adjusted(-4, -4, 4, 4), 6, 6)
        p.restore()


# ---------------------------------------------------------------------------
# small renderings used by the tray, the context menu and the previews
# ---------------------------------------------------------------------------
def render_state(size: int, state: str, t: int = 0, device_pixel_ratio: float = 1.0):
    """Render one state into a QPixmap - used by the tray icon and previews."""
    from PyQt5.QtGui import QPixmap

    ratio = device_pixel_ratio or 1.0
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    OpenO().paint(painter, QRectF(0, 0, size, size * DESIGN_H / DESIGN_W),
                  state, t)
    painter.end()
    return pixmap


def render_icon(size: int = 64, device_pixel_ratio: float = 1.0):
    """The app / tray icon: the character plus its lamp, on transparency."""
    return render_state(size, "idle", 0, device_pixel_ratio)


def render_contact_sheet(cell_w: int = 132, gap: int = 18, label_h: int = 26,
                         device_pixel_ratio: float = 1.0):
    """Every state side by side - what ``tools/gen_preview.py`` writes out."""
    from PyQt5.QtGui import QPixmap

    from petcore import i18n
    from petcore.reducer import STATES

    # Derived from the state machine rather than listed here: a hardcoded copy
    # silently omits any state added later, which is exactly what happened to
    # ``offline`` - it shipped with no picture in the documentation at all.
    states = list(STATES)
    ratio = device_pixel_ratio or 1.0
    cell_h = int(round(cell_w * DESIGN_H / DESIGN_W))
    cols = 4
    rows = (len(states) + cols - 1) // cols
    width = cols * cell_w + (cols + 1) * gap
    height = rows * (cell_h + label_h) + (rows + 1) * gap

    sheet = QPixmap(int(width * ratio), int(height * ratio))
    sheet.setDevicePixelRatio(ratio)
    sheet.fill(QColor("#F4F4F2"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setFont(QFont("Segoe UI", 9))

    for index, state in enumerate(states):
        col, row = index % cols, index // cols
        x = gap + col * (cell_w + gap)
        y = gap + row * (cell_h + label_h + gap)
        painter.save()
        painter.translate(x, y)
        painter.setPen(QColor(0xE2, 0xE2, 0xE0))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(QRectF(0, 0, cell_w, cell_h + label_h - 6), 8, 8)
        painter.restore()
        OpenO().paint(painter, QRectF(x, y, cell_w, cell_h), state,
                      900 + index * 260)
        painter.setPen(QColor("#333333"))
        painter.drawText(QRectF(x, y + cell_h + 1, cell_w, label_h - 6),
                         Qt.AlignHCenter | Qt.AlignTop, i18n.t("state." + state))
    painter.end()
    return sheet
