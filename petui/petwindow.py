"""The pet window: a frameless, always-on-top, translucent square.

Edges are fixtures, not hiding places.  Dragging past any screen edge pins the
pet flush against it at full size and fully visible, then lets it slide along
that edge - the deliberate opposite of a pet that tucks itself away.
"""

from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QCursor, QIcon, QPainter
from PyQt5.QtWidgets import QApplication, QWidget

from petui.openo import BASE_H, BASE_W, DESIGN_H, DESIGN_W, OpenO

FRAME_MS = 33                      # ~30 fps; enough for the fastest motion
EDGE_SNAP_PX = 34                 # how close counts as "against the edge"
MIN_SCALE = 0.5
MAX_SCALE = 2.0


class PetWindow(QWidget):
    """The character itself.  It knows nothing about sessions."""

    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    menu_requested = pyqtSignal(QPoint)
    scrolled = pyqtSignal(float)
    moved = pyqtSignal()
    scaled_moved = pyqtSignal()
    scale_changed = pyqtSignal(float)

    def __init__(self, scale: float = 1.0, topmost: bool = True):
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint |
            (Qt.WindowStaysOnTopHint if topmost else Qt.WindowStaysOnBottomHint))
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, topmost)
        self.setWindowTitle("OpenCode Pet")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

        self.scale = min(MAX_SCALE, max(MIN_SCALE, float(scale or 1.0)))
        self.topmost = topmost
        self.state = "idle"
        self.dragging = False
        self.drag_vx = 0.0
        self.drag_moved = False
        self.pinned_edge: Optional[str] = None
        self.hover_tip = ""

        self._press_pos: Optional[QPoint] = None
        self._press_global: Optional[QPoint] = None
        self._press_time = 0.0
        self._last_drag_x = 0.0
        self._last_drag_at = 0.0
        self._t0 = time.time() * 1000.0

        self._apply_size()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)

    # -- geometry --------------------------------------------------------
    def _apply_size(self) -> None:
        self.resize(int(round(BASE_W * self.scale)),
                    int(round(BASE_H * self.scale)))

    def set_scale(self, value: float, save_pos: bool = None) -> None:
        """Resize around the pet's centre so it does not jump across screen."""
        value = min(MAX_SCALE, max(MIN_SCALE, float(value)))
        if abs(value - self.scale) < 1e-3:
            return
        centre = self.geometry().center()
        self.scale = value
        self._apply_size()
        self.move(centre.x() - self.width() // 2, centre.y() - self.height() // 2)
        self.clamp_to_screen()
        if save_pos is not None:
            self.save_pos(save_pos)
        self.update()
        self.scale_changed.emit(self.scale)
        self.scaled_moved.emit()

    def set_topmost(self, topmost: bool) -> None:
        topmost = bool(topmost)
        if topmost == self.topmost:
            return
        self.topmost = topmost
        visible = self.isVisible()
        flags = (Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint |
                 (Qt.WindowStaysOnTopHint if topmost else Qt.WindowStaysOnBottomHint))
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, topmost)
        if visible:
            self.show()

    def save_pos(self, pos: Tuple[int, int] = None) -> Tuple[int, int]:
        if pos is None:
            point = self.pos()
            pos = (point.x(), point.y())
        self._press_pos = QPoint(pos[0], pos[1])
        return pos

    def restore_pos(self, x: int, y: int) -> None:
        if x < 0 or y < 0:
            self.place_default()
        else:
            self.move(int(x), int(y))
        self.clamp_to_screen()

    def place_default(self) -> None:
        """Bottom-right of the primary screen, the classic pet corner."""
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24,
                  screen.bottom() - self.height() - 12)
        self.pinned_edge = None

    # -- screens ---------------------------------------------------------
    def available_rect(self) -> QRect:
        window = self.windowHandle()
        screen = window.screen() if window else QApplication.primaryScreen()
        return screen.availableGeometry()

    def clamp_to_screen(self) -> None:
        """Keep the whole pet on some screen, even after a resolution change.

        This is the *restore* rule: never leave part of the pet off-screen.
        While dragging the window is allowed a little more slack (see
        :meth:`drag_bounds`) so that a flick at an edge actually snaps.
        """
        area = self.available_rect()
        x = min(max(self.x(), area.left()), max(area.left(), area.right() - self.width()))
        y = min(max(self.y(), area.top()), max(area.top(), area.bottom() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(int(x), int(y))
        # A pinned edge that the new screen no longer has is no longer pinned.
        if self.pinned_edge == "left" and self.x() != area.left():
            self.pinned_edge = None
        elif self.pinned_edge == "right" and self.x() + self.width() - 1 != area.right():
            self.pinned_edge = None
        elif self.pinned_edge == "top" and self.y() != area.top():
            self.pinned_edge = None
        elif self.pinned_edge == "bottom" and self.y() + self.height() - 1 != area.bottom():
            self.pinned_edge = None

    def drag_bounds(self, area=None):
        """Where the window may go mid-drag: ``EDGE_SNAP_PX`` of slack per edge.

        Without that slack a pet dragged "past" an edge would sit further away
        than the snap threshold and simply refuse to stick.
        """
        area = area or self.available_rect()
        return (area.left() - EDGE_SNAP_PX, area.top() - EDGE_SNAP_PX,
                area.right() - self.width() + EDGE_SNAP_PX,
                area.bottom() - self.height() + EDGE_SNAP_PX)

    def snap_to_edge(self) -> Optional[str]:
        """Pin flush to the nearest screen edge; return which one."""
        area = self.available_rect()
        x, y = self.x(), self.y()
        left_gap = abs(x - area.left())
        right_gap = abs((x + self.width()) - area.right())
        top_gap = abs(y - area.top())
        bottom_gap = abs((y + self.height()) - area.bottom())

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
            self.pinned_edge = None
            self.clamp_to_screen()
            return None
        if edge == "left":
            self.move(area.left(), self._clamp_y(y, area))
        elif edge == "right":
            self.move(area.right() - self.width() + 1, self._clamp_y(y, area))
        elif edge == "top":
            self.move(self._clamp_x(x, area), area.top())
        else:
            self.move(self._clamp_x(x, area), area.bottom() - self.height() + 1)
        self.pinned_edge = edge
        return edge

    @staticmethod
    def _clamp_x(x: int, area: QRect) -> int:
        return int(min(max(x, area.left()), area.right() - 1))

    @staticmethod
    def _clamp_y(y: int, area: QRect) -> int:
        return int(min(max(y, area.top()), area.bottom() - 1))

    # -- state -----------------------------------------------------------
    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.update()

    def set_hover_tip(self, text: str) -> None:
        self.hover_tip = text or ""

    # -- painting --------------------------------------------------------
    def _tick(self) -> None:
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        t = (time.time() * 1000.0) - self._t0
        char = OpenO()
        char.paint(painter, QRectF(0, 0, self.width(), self.height()),
                   self.state, t,
                   dragging=self.dragging,
                   drag_vx=self.drag_vx)
        painter.end()

    def grab_pixmap(self, size: int = 0):
        """A clean render of the current state, for the tray and previews."""
        from PyQt5.QtGui import QPixmap
        size = size or max(self.width(), self.height())
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        OpenO().paint(painter, QRectF(0, 0, self.width(), self.height()),
                      self.state, (time.time() * 1000.0) - self._t0)
        painter.end()
        return pixmap

    def icon(self) -> QIcon:
        return QIcon(self.grab_pixmap(64))

    # -- interaction -----------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.menu_requested.emit(event.globalPos())
            return
        if event.button() != Qt.LeftButton:
            return
        self._press_global = QCursor.pos()
        self._press_pos = self.pos()
        self._press_time = time.time()
        self.drag_moved = False
        self._last_drag_x = self._press_global.x()
        self._last_drag_at = time.time()

    def mouseMoveEvent(self, _event) -> None:
        if self._press_global is None:
            return
        now = QCursor.pos()
        if not self.dragging:
            dx = abs(now.x() - self._press_global.x())
            dy = abs(now.y() - self._press_global.y())
            if dx < QApplication.startDragDistance() and dy < QApplication.startDragDistance():
                return
            self.dragging = True
            self.drag_moved = True
            self._press_pos = self.pos()
        dt = max(1e-3, time.time() - self._last_drag_at)
        self.drag_vx = (now.x() - self._last_drag_x) / dt
        self._last_drag_x = now.x()
        self._last_drag_at = time.time()
        self.pinned_edge = None
        min_x, min_y, max_x, max_y = self.drag_bounds()
        x = self._press_pos.x() + (now.x() - self._press_global.x())
        y = self._press_pos.y() + (now.y() - self._press_global.y())
        self.move(int(min(max(x, min_x), max_x)), int(min(max(y, min_y), max_y)))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        was_dragging = self.dragging
        held = time.time() - self._press_time
        self.dragging = False
        self.drag_vx = 0.0
        self._press_global = None
        if was_dragging:
            self.snap_to_edge()
            # One signal for the whole gesture: the release must reach the app
            # even when the pointer never crossed the drag threshold, because
            # that is exactly how the user parks the pet flush against an edge.
            self.moved.emit()
            self.update()
            return
        if self.drag_moved:
            return
        if held > 0.35:
            return
        if event.modifiers() & Qt.ControlModifier:
            return
        self.clicked.emit()

    def mouseDoubleClickEvent(self, _event) -> None:
        self.double_clicked.emit()

    def wheelEvent(self, event) -> None:
        if not (event.modifiers() & Qt.ControlModifier):
            event.ignore()
            return
        delta = event.angleDelta().y() / 120.0
        if not delta:
            return
        event.accept()
        self.set_scale(self.scale * (1.0 + 0.1 * delta))
        self.scrolled.emit(self.scale)
