"""The tray icon and its context menu.

Every string here comes from ``petcore.i18n`` - the menu is rebuilt from
scratch on each right-click so switching language is instant and needs no
restart.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QAction, QActionGroup, QMenu, QSystemTrayIcon

from petcore import i18n

SCALES = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)


class Tray:
    """Owns the tray icon and rebuilds its menu on demand."""

    def __init__(self, handlers: Dict[str, Callable], scale: float = 1.0,
                 parent=None) -> None:
        self.handlers = handlers
        self.icon = QSystemTrayIcon(parent)
        self.icon.setIcon(QIcon())
        self.icon.setToolTip(i18n.t("app.name"))
        self.menu: Optional[QMenu] = None
        self.scale = scale
        self.icon.activated.connect(self._on_activated)

    # -- lifecycle --------------------------------------------------------
    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def set_icon(self, pixmap) -> None:
        self.icon.setIcon(QIcon(pixmap))

    def set_tooltip(self, text: str) -> None:
        self.icon.setToolTip(text)

    def notify(self, title: str, body: str, warning: bool = False) -> None:
        from PyQt5.QtWidgets import QSystemTrayIcon as T
        self.icon.showMessage(title, body,
                              T.Warning if warning else T.Information,
                              6000)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            handler = self.handlers.get("open")
            if handler:
                handler()

    # -- menu -------------------------------------------------------------
    def popup(self, at: QPoint = None) -> None:
        menu = self._build()
        self.menu = menu
        self.icon.setContextMenu(menu)
        point = at or QPoint(0, 0)
        menu.exec_(point)

    def _act(self, menu: QMenu, label: str, key: str, checked: bool = None,
             enabled: bool = True, checkable: bool = False) -> QAction:
        # Built through the menu so the action is parented and has its text
        # set by Qt itself; ``QAction(label, parent)`` in PyQt5 does not
        # reliably keep the text.
        action = menu.addAction(label)
        action.setEnabled(enabled)
        if checkable:
            action.setCheckable(True)
            action.setChecked(bool(checked))
        action.triggered.connect(lambda _checked=False, k=key: self._fire(k))
        return action

    def _fire(self, key: str) -> None:
        handler = self.handlers.get(key)
        if handler:
            handler()

    def _build(self) -> QMenu:
        menu = QMenu()
        menu.setAttribute(Qt.WA_DeleteOnClose, False)
        menu.setToolTipsVisible(True)

        def flag(key: str, fallback=False) -> bool:
            getter = self.handlers.get(key)
            return bool(getter()) if getter else fallback

        menu.addAction(self._act(menu, i18n.t("menu.open"), "open"))
        menu.addSeparator()

        if self.handlers.get("has_cards", lambda: False)():
            menu.addAction(self._act(
                menu,
                i18n.t("menu.collapse") if flag("expanded")
                else i18n.t("menu.expand"), "toggle_expand"))
        else:
            menu.addAction(i18n.t("menu.no_cards")).setEnabled(False)
        menu.addSeparator()

        scale_menu = menu.addMenu(i18n.t("menu.scale"))
        group = QActionGroup(scale_menu)
        group.setExclusive(True)
        for value in SCALES:
            action = QAction("%d%%" % round(value * 100), scale_menu)
            action.setCheckable(True)
            action.setChecked(abs(value - self.scale) < 0.02)
            action.triggered.connect(
                lambda _checked=False, v=value: self.handlers["set_scale"](v))
            group.addAction(action)
            scale_menu.addAction(action)
        menu.addSeparator()

        menu.addAction(self._act(menu, i18n.t("menu.topmost"), "toggle_topmost",
                                 checked=flag("topmost", True), checkable=True))
        menu.addAction(self._act(menu, i18n.t("menu.titles"), "toggle_titles",
                                 checked=flag("show_titles", True), checkable=True))
        menu.addAction(self._act(menu, i18n.t("menu.silent"), "toggle_silent",
                                 checked=flag("silent"), checkable=True))

        lang_menu = menu.addMenu(i18n.t("menu.language"))
        for lang in i18n.available():
            action = QAction("中文" if lang == "zh" else "English", lang_menu)
            action.setCheckable(True)
            action.setChecked(flag("lang", "zh") == lang)
            action.triggered.connect(
                lambda _checked=False, v=lang: self.handlers["set_lang"](v))
            lang_menu.addAction(action)

        menu.addSeparator()
        menu.addAction(self._act(menu, i18n.t("menu.demo"), "demo"))
        menu.addAction(self._act(menu, i18n.t("menu.reset"), "reset_pos"))
        menu.addAction(self._act(menu, i18n.t("menu.about"), "about"))
        menu.addSeparator()
        menu.addAction(self._act(menu, i18n.t("menu.quit"), "quit"))
        return menu
