"""The session card stack that floats next to the pet.

One card per conversation, in urgency order.  A finished task keeps its card
until it is clicked - retention is by acknowledgement, not by a timer, so
nothing disappears quietly while you are away.

Collapsed, only the top card shows and a badge names the rest ("2 more
conversations (1 running)").  Expanded, every surfaced conversation gets its
own row with live progress and freshness.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Tuple

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter,
                         QPen)
from PyQt5.QtWidgets import QWidget

from petcore import i18n
from petcore.reducer import ERROR, RUNNING_STATES, WAITING

CARD_W = 268
CARD_H = 62
CARD_GAP = 8
BADGE_H = 26
MARGIN = 10
ROW_H = 46
PAD = 11
RADIUS = 10

BG = QColor("#FFFFFF")
BORDER = QColor(0xD8, 0xD8, 0xD6)
TEXT = QColor("#22201F")
MUTED = QColor("#7C7A78")
SHADOW = QColor(0, 0, 0, 34)

STATE_COLOR = {
    "idle": QColor("#9AA0A6"),
    "thinking": QColor("#F5C518"),
    "executing": QColor("#3B82F6"),
    "longtask": QColor("#3B82F6"),
    "waiting": QColor("#F59E0B"),
    "done": QColor("#22C55E"),
    "error": QColor("#EF4444"),
    "drag": QColor("#6B6B6B"),
    "offline": QColor("#5A5A5A"),
}


class CardStack(QWidget):
    """A frameless popup that renders beside the pet and flips when needed."""

    card_clicked = pyqtSignal(str)          # sessionID
    expand_toggled = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool |
                            Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)
        self.setWindowTitle("OpenCode Pet cards")
        self.setMouseTracking(True)

        self.records: List[Dict] = []
        self.expanded = False
        self.show_titles = True
        self.hover_index = -1
        self.placement = "left"          # which side of the pet
        self.flip = False                # True when drawn below the pet
        self._rows: List[Tuple[str, QRectF]] = []
        self._badge_rect = QRectF()

    # -- data ------------------------------------------------------------
    def set_records(self, records: List[Dict], expanded: bool,
                    show_titles: bool) -> None:
        self.records = records
        self.expanded = bool(expanded) and len(records) > 1
        self.show_titles = bool(show_titles)
        self._apply_size()
        self.update()

    def _desired_size(self) -> Tuple[int, int]:
        width = CARD_W
        if self.expanded:
            rows = min(len(self.records), 12)
            height = PAD + rows * ROW_H + max(0, rows - 1) * CARD_GAP + PAD
            if height > 420:
                height = 420
        else:
            # One card, plus the "N more conversations" badge when there is one.
            badge = (CARD_GAP + BADGE_H) if len(self.records) > 1 else 0
            height = PAD + CARD_H + badge + PAD
        return width, int(height)

    def _apply_size(self) -> None:
        width, height = self._desired_size()
        if (self.width(), self.height()) != (width, height):
            self.setFixedSize(width, height)

    # -- geometry --------------------------------------------------------
    def place(self, pet_rect, screen_rect, prefer_below: bool = False) -> None:
        """Park the stack centred directly above the pet, or below when it must.

        Above rather than beside, because the pet usually lives in a corner and
        "above" is the only direction with free screen in all four of them.

        Horizontally the card is **centred on the pet**, not edge-aligned with
        it.  The card is 268 px wide against the pet's 132, so aligning their
        right edges left 136 px of card hanging out to the left and none at all
        on the right - which reads as "upper-left" rather than "above".
        Centring keeps the overhang symmetric, so the card sits squarely over
        its character.

        The width is fixed.  Shrinking the card so it can always centre was
        tried and rejected: it made the card visibly change size as the pet
        moved, which is worse than the asymmetry it removed.

        When the pet is against a screen edge, centring does not fit and the
        clamp wins - the card sits flush with that edge, which is the sensible
        reading there.

        Geometry only: showing and hiding belong to the caller, so this stays
        testable without touching window visibility.
        """
        width, height = self.width(), self.height()
        area = screen_rect
        gap = 10

        centred = pet_rect.center().x() - width // 2
        # Centre, then keep it on screen.  With room on both sides the overhang
        # is symmetric, (width - pet_width) / 2 each way; against an edge the
        # clamp wins and the card lies flush with that edge.
        x = min(max(centred, area.left()), max(area.left(), area.right() - width))

        above_y = pet_rect.top() - height - gap
        below_y = pet_rect.bottom() + gap
        room_above = above_y >= area.top()
        room_below = below_y + height <= area.bottom()

        self.flip = False
        if prefer_below and room_below:
            self.placement = "below"
            y = below_y
        elif room_above:
            self.placement = "above"
            y = above_y
        elif room_below:
            self.placement = "below"
            self.flip = True
            y = below_y
        else:
            # Neither side fits: keep the whole card on screen.
            self.placement = "above"
            y = min(max(area.top(), above_y),
                    max(area.top(), area.bottom() - height))

        self.move(int(x), int(y))

    # -- painting --------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        self._rows = []
        if not self.records:
            painter.end()
            return

        title_font = QFont("Segoe UI", 9, QFont.DemiBold)
        body_font = QFont("Segoe UI", 9)
        small_font = QFont("Segoe UI", 8)

        if self.expanded:
            self._paint_expanded(painter, title_font, body_font, small_font)
        else:
            self._paint_collapsed(painter, title_font, body_font, small_font)
        painter.end()

    def _card(self, painter: QPainter, rect: QRectF, hovered: bool = False) -> None:
        painter.save()
        # A soft drop shadow, drawn as three translucent rounded rects.
        for index, alpha in ((3, 18), (2, 26), (1, 40)):
            shade = QColor(0, 0, 0, alpha)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(shade))
            painter.drawRoundedRect(rect.adjusted(-index * 0.6, 1, index * 0.6,
                                                  index * 1.4), RADIUS, RADIUS)
        painter.setPen(QPen(QColor("#B9B7B4") if hovered else BORDER, 1))
        painter.setBrush(QBrush(QColor("#FFFFFF") if not hovered else QColor("#FAFAF8")))
        painter.drawRoundedRect(rect, RADIUS, RADIUS)
        painter.restore()

    def _paint_collapsed(self, painter: QPainter, title_font, body_font, small_font) -> None:
        top = self.records[0]
        rect = QRectF(PAD, PAD, CARD_W - PAD * 2, CARD_H)
        self._card(painter, rect, self.hover_index == 0)
        self._rows.append((top["id"], rect))
        self._draw_row(painter, rect, top, title_font, body_font, small_font,
                       compact=True)

        rest = len(self.records) - 1
        if rest > 0:
            running = sum(1 for r in self.records[1:] if r["running"])
            badge = QRectF(PAD, PAD + CARD_H + CARD_GAP,
                           CARD_W - PAD * 2, BADGE_H)
            self._badge_rect = badge
            painter.save()
            painter.setPen(QPen(QColor(0xC9, 0xC9, 0xC6), 1))
            painter.setBrush(QBrush(QColor(0xF4, 0xF4, 0xF2)))
            painter.drawRoundedRect(badge, 13, 13)
            painter.restore()
            self._rows.append(("", badge))

            text = i18n.t("card.more", n=rest)
            if running:
                text += "  ·  " + i18n.t("card.more_detail",
                                          total=len(self.records),
                                          running=running)
            painter.setFont(body_font)
            painter.setPen(QPen(TEXT))
            painter.drawText(badge.adjusted(10, 0, -10, 0),
                             Qt.AlignVCenter | Qt.AlignLeft, text)
        else:
            self._badge_rect = QRectF()

    def _paint_expanded(self, painter: QPainter, title_font, body_font, small_font) -> None:
        top = PAD
        for index, record in enumerate(self.records):
            if top + ROW_H > self.height() - PAD:
                break
            rect = QRectF(PAD, top, CARD_W - PAD * 2, ROW_H)
            self._card(painter, rect, self.hover_index == index)
            self._rows.append((record["id"], rect))
            self._draw_row(painter, rect, record, title_font, body_font, small_font,
                           compact=False)
            top += ROW_H + CARD_GAP

    # -- one card --------------------------------------------------------
    def _draw_row(self, painter: QPainter, rect: QRectF, record: Dict,
                  title_font, body_font, small_font, compact: bool) -> None:
        state = record.get("state") or "idle"
        colour = STATE_COLOR.get(state, MUTED)
        dot = QRectF(rect.left() + PAD, rect.top() + (10 if compact else 8), 9, 9)
        if state in RUNNING_STATES:
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(colour))
            painter.drawEllipse(dot.adjusted(-2.5, -2.5, 2.5, 2.5))
            painter.restore()
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(colour))
        painter.drawEllipse(dot)
        painter.restore()

        text_left = rect.left() + PAD + 18
        avail = rect.right() - PAD - text_left
        if avail <= 0:
            return

        label = record.get("label") or i18n.t("card.untitled")
        metrics = QFontMetrics(title_font)
        painter.setFont(title_font)
        painter.setPen(QPen(TEXT))
        title_y = rect.top() + (8 if compact else 6)
        painter.drawText(QRectF(text_left, title_y, avail, 15),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         metrics.elidedText(label, Qt.ElideRight, int(avail)))

        sub = self._subtitle(record)
        painter.setFont(body_font)
        painter.setPen(QPen(colour if state in (WAITING, ERROR) else MUTED))
        sub_y = title_y + 16
        painter.drawText(QRectF(text_left, sub_y, avail, 14),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetrics(body_font).elidedText(
                             sub, Qt.ElideRight, int(avail)))

        if compact:
            meta = self._meta(record)
            if meta:
                painter.setFont(small_font)
                painter.setPen(QPen(QColor("#A9A7A5")))
                painter.drawText(QRectF(text_left, sub_y + 15, avail, 13),
                                 Qt.AlignVCenter | Qt.AlignLeft, meta)

    def _subtitle(self, record: Dict) -> str:
        state = record.get("state") or "idle"
        if state == WAITING:
            action = record.get("permission") or ""
            return i18n.t("card.permission", action=action) if action else \
                i18n.t("state.waiting")
        if state == ERROR:
            code = record.get("error_code") or ""
            base = i18n.t("state.error")
            return ("%s · %s" % (base, code)) if code else base
        if record.get("tool") and state == "executing":
            return i18n.t("card.tool", tool=record["tool"])
        return i18n.t("state." + state)

    def _meta(self, record: Dict) -> str:
        parts = []
        steps = int(record.get("steps") or 0)
        if steps:
            parts.append(i18n.t("card.steps", n=steps))
        now = time.time() * 1000.0
        if record.get("running"):
            started = record.get("started") or record.get("updated") or now
            parts.append(i18n.format_duration((now - started) / 1000.0))
        elif record.get("finished"):
            parts.append(i18n.format_age(now - record["finished"]))
        agent = record.get("agent")
        if agent:
            parts.append(agent)
        return "  ·  ".join(parts)

    # -- interaction -----------------------------------------------------
    def index_at(self, point) -> int:
        for index, (_sid, rect) in enumerate(self._rows):
            if rect.contains(point):
                return index
        return -1

    def mouseMoveEvent(self, event) -> None:
        index = self.index_at(event.pos())
        if index != self.hover_index:
            self.hover_index = index
            self.setCursor(Qt.PointingHandCursor if index >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _event) -> None:
        if self.hover_index != -1:
            self.hover_index = -1
            self.update()

    def mousePressEvent(self, event) -> None:
        """Hit-test by row rectangle rather than trusting the sender.

        A row is drawn from several widgets, so ``self.sender()`` is whatever
        child happened to be under the pointer - often a label with no idea
        which conversation it belongs to.  The rows are recorded during paint
        precisely so the click can be resolved here, and an unmatched click is
        ignored instead of falling through to the top card.
        """
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        index = self.index_at(event.pos())
        if index < 0:
            event.ignore()
            return
        session_id, _rect = self._rows[index]
        if not session_id:
            self.expand_toggled.emit(True)
            event.ignore()
            return
        self.card_clicked.emit(session_id)
        event.ignore()
