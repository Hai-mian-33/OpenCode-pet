"""The single source of truth for every user-facing string.

Nothing in the UI may hardcode text.  Both language tables are keyed
identically; ``tests/run_tests.py`` asserts the key sets match and that every
branch of every formatter renders in both languages.

A value may be a ``(singular, plural)`` pair.  ``t("card.steps", 3)`` then
picks the right form, so no English "1 steps" can ever appear.
"""

from __future__ import annotations

import locale
import os
from typing import Any, Dict, Iterable, Tuple

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

# ---------------------------------------------------------------------------
# zh
# ---------------------------------------------------------------------------
_ZH: Dict[str, Any] = {
    # ---- identity ------------------------------------------------------
    "app.name": "OpenCode Pet",
    "app.nickname": "OpenO",

    # ---- session states ------------------------------------------------
    "state.idle": "待机",
    "state.thinking": "思考中",
    "state.executing": "执行中",
    "state.waiting": "等待授权",
    "state.done": "已完成",
    "state.error": "出错",
    "state.longtask": "长任务",
    "state.drag": "拖拽中",
    "state.offline": "未连接",
    "state.closed": "OpenCode 已关闭",
    "state.unknown": "未知",

    # ---- context menu --------------------------------------------------
    "menu.open": "打开 OpenCode",
    "menu.cards": "会话卡片",
    "menu.expand": "展开全部会话",
    "menu.collapse": "收起会话",
    "menu.no_cards": "暂无会话",
    "menu.scale": "缩放",
    "menu.topmost": "窗口置顶",
    "menu.titles": "显示会话标题",
    "menu.silent": "静音（不弹系统通知）",
    "menu.language": "语言 / Language",
    "menu.demo": "演示动画",
    "menu.reset": "重置位置",
    "menu.about": "关于 OpenCode Pet",
    "menu.quit": "退出桌宠",

    # ---- about box -----------------------------------------------------
    "about.title": "关于 OpenCode Pet",
    "about.body": (
        "OpenCode Pet 是 OpenCode 桌面版的第三方开源桌宠，"
        "与 OpenCode 官方团队无隶属关系。\n\n"
        "形象「OpenO / 小 O」由 OpenCode 官方几何标志拟人化而来，"
        "仅供个人非商业使用。\n\n"
        "版本 {version}"
    ),
    "about.tray_title": "OpenCode Pet {version}",
    "about.tray_text": "OpenCode 桌宠 · {state}",

    # ---- session cards -------------------------------------------------
    "card.untitled": "未命名会话",
    "card.more": ("还有 {n} 个会话", "还有 {n} 个会话"),
    "card.more_detail": "{total} 个会话（{running} 个运行中）",
    "card.steps": ("{n} 步", "{n} 步"),
    "card.tool": "正在调用 {tool}",
    "card.permission": "需要你的授权：{action}",
    "card.longtask": "已持续 {duration}",
    "card.acknowledged": "已确认",

    # ---- durations and ages --------------------------------------------
    "dur.s": "{n} 秒",
    "dur.m": "{n} 分 {s} 秒",
    "dur.h": "{n} 小时 {m} 分",
    "dur.d": "{n} 天 {h} 小时",

    "age.now": "刚刚",
    "age.min": ("{n} 分钟前", "{n} 分钟前"),
    "age.hour": ("{n} 小时前", "{n} 小时前"),
    "age.day": ("{n} 天前", "{n} 天前"),

    # ---- notifications -------------------------------------------------
    "notif.waiting_title": "OpenCode 正在等待你的授权",
    "notif.waiting_body": "{project} 需要确认：{action}",
    "notif.done_title": "OpenCode 任务完成",
    "notif.done_body": "{project} · {steps}",
    "notif.error_title": "OpenCode 任务出错",
    "notif.error_body": "{project} 执行失败",

    # ---- hover line ----------------------------------------------------
    "hover.connected": "已连接 OpenCode {version}",
    "hover.offline": "未连接到 OpenCode 服务",
    "hover.app_closed": "OpenCode 窗口已关闭 · 单击重新打开",
    "hover.open": "单击打开 OpenCode · 右键菜单",

    # ---- errors / logs -------------------------------------------------
    "err.no_password": "找不到 OpenCode 服务密码（%s）",
    "err.no_endpoint": "无法定位 OpenCode 服务端口",
    "err.auth": "OpenCode 服务拒绝了连接（密码不匹配）",
    "err.cannot_open_desktop": "无法打开 OpenCode 桌面版，请检查它是否已安装",
    "http.ok": "OpenCode Pet ok  connected={connected}  sessions={sessions}",

    # ---- CLI -----------------------------------------------------------
    "cli.daemon_exit": "OpenCode 已退出，桌宠随之关闭",
}

# ---------------------------------------------------------------------------
# en
# ---------------------------------------------------------------------------
_EN: Dict[str, Any] = {
    "app.name": "OpenCode Pet",
    "app.nickname": "OpenO",

    "state.idle": "Idle",
    "state.thinking": "Thinking",
    "state.executing": "Running",
    "state.waiting": "Needs you",
    "state.done": "Done",
    "state.error": "Failed",
    "state.longtask": "Long task",
    "state.drag": "Dragging",
    "state.offline": "Offline",
    "state.closed": "OpenCode closed",
    "state.unknown": "Unknown",

    "menu.open": "Open OpenCode",
    "menu.cards": "Session cards",
    "menu.expand": "Expand all sessions",
    "menu.collapse": "Collapse cards",
    "menu.no_cards": "No sessions",
    "menu.scale": "Scale",
    "menu.topmost": "Always on top",
    "menu.titles": "Show session titles",
    "menu.silent": "Silent (no system notifications)",
    "menu.language": "语言 / Language",
    "menu.demo": "Play demo",
    "menu.reset": "Reset position",
    "menu.about": "About OpenCode Pet",
    "menu.quit": "Quit pet",

    "about.title": "About OpenCode Pet",
    "about.body": (
        "OpenCode Pet is an independent, open-source desktop pet for the "
        "OpenCode desktop app. It is not affiliated with the OpenCode team.\n\n"
        "The character \"OpenO\" is an anthropomorphised take on OpenCode's "
        "own geometric mark, for personal, non-commercial use.\n\n"
        "Version {version}"
    ),
    "about.tray_title": "OpenCode Pet {version}",
    "about.tray_text": "OpenCode pet · {state}",

    "card.untitled": "Untitled session",
    "card.more": ("{n} more conversations", "{n} more conversations"),
    "card.more_detail": "{total} conversations ({running} running)",
    "card.steps": ("{n} step", "{n} steps"),
    "card.tool": "Calling {tool}",
    "card.permission": "Needs your approval: {action}",
    "card.longtask": "Running for {duration}",
    "card.acknowledged": "Acknowledged",

    "dur.s": "{n}s",
    "dur.m": "{n}m {s}s",
    "dur.h": "{n}h {m}m",
    "dur.d": "{n}d {h}h",

    "age.now": "just now",
    "age.min": ("{n} min ago", "{n} min ago"),
    "age.hour": ("{n} h ago", "{n} h ago"),
    "age.day": ("{n} d ago", "{n} d ago"),

    "notif.waiting_title": "OpenCode is waiting for you",
    "notif.waiting_body": "{project} needs a decision: {action}",
    "notif.done_title": "OpenCode task finished",
    "notif.done_body": "{project} · {steps}",
    "notif.error_title": "OpenCode task failed",
    "notif.error_body": "{project} could not finish",

    "hover.connected": "Connected to OpenCode {version}",
    "hover.offline": "Not connected to the OpenCode service",
    "hover.app_closed": "The OpenCode window is closed · click to reopen",
    "hover.open": "Click to open OpenCode · right-click for the menu",

    "err.no_password": "OpenCode service password not found (%s)",
    "err.no_endpoint": "Could not locate the OpenCode service port",
    "err.auth": "The OpenCode service rejected the connection (password mismatch)",
    "err.cannot_open_desktop": "Could not open the OpenCode desktop app; check that it is installed",
    "http.ok": "OpenCode Pet ok  connected={connected}  sessions={sessions}",

    "cli.daemon_exit": "OpenCode exited, so the pet exits with it",
}

TABLES: Dict[str, Dict[str, Any]] = {"zh": _ZH, "en": _EN}

_LOCALE_ALIASES = {
    "zh": "zh", "zh-cn": "zh", "zh-hans": "zh", "zh-sg": "zh", "zh-tw": "zh",
    "zh-hant": "zh", "zh-hk": "zh", "zh-mo": "zh", "cn": "zh", "chinese": "zh",
    "en": "en", "en-us": "en", "en-gb": "en", "english": "en",
}

_current: str = DEFAULT_LANG


# ---------------------------------------------------------------------------
# language handling
# ---------------------------------------------------------------------------
def normalize_language(value: Any) -> str:
    """Map anything locale-shaped onto ``zh`` or ``en``; default to Chinese."""
    if not value:
        return DEFAULT_LANG
    key = str(value).strip().lower().replace("_", "-")
    if key in _LOCALE_ALIASES:
        return _LOCALE_ALIASES[key]
    base = key.split("-")[0]
    return _LOCALE_ALIASES.get(base, DEFAULT_LANG)


def detect_system_language() -> str:
    for var in ("OPENCODE_PET_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return normalize_language(value)
    try:
        value = locale.getdefaultlocale()[0]
    except (ValueError, TypeError):
        value = None
    if value:
        return normalize_language(value)
    return DEFAULT_LANG


def set_language(lang: Any) -> str:
    global _current
    _current = normalize_language(lang)
    return _current


def language() -> str:
    return _current


def language_name(lang: str = None) -> str:
    """The endonym, so the menu reads 中文 / English in both interfaces."""
    return "中文" if normalize_language(lang or _current) == "zh" else "English"


def available() -> Tuple[str, ...]:
    return LANGS


def keys() -> Iterable[str]:
    return TABLES["zh"].keys()


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------
def t(key: str, *args: Any, lang: str = None, **named: Any) -> str:
    """Translate ``key``; unknown keys degrade to the key itself.

    **Every placeholder in every template is named.**  Call sites therefore
    pass keywords only, which removes the whole class of bug where a template
    says ``{n}`` but the caller passes a positional argument and the raw
    placeholder ships to the screen.  ``tests/run_tests.py`` asserts that no
    template mixes numbered and named placeholders.

    A value may be a ``(singular, plural)`` pair, in which case the first
    integer supplied - positionally or by keyword - picks the form.
    """
    lang = normalize_language(lang) if lang else _current
    table = TABLES.get(lang) or TABLES[DEFAULT_LANG]
    value = table.get(key)
    if value is None:
        value = TABLES[DEFAULT_LANG].get(key, key)
    if isinstance(value, (tuple, list)):
        number = next((a for a in args if isinstance(a, int)), None)
        if number is None:
            number = next((v for v in named.values() if isinstance(v, int)), 0)
        branches = list(value) + ["", ""]
        value = branches[1] if number != 1 else branches[0]
    if not isinstance(value, str):
        return str(value)
    if not args and not named:
        return value
    try:
        return value.format(*args, **named)
    except (IndexError, KeyError, ValueError):
        return value


def format_duration(seconds: float, lang: str = None) -> str:
    """``95`` -> ``1 分 35 秒`` / ``1m 35s``."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return t("dur.s", n=seconds, lang=lang)
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return t("dur.m", n=minutes, s=secs, lang=lang)
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return t("dur.h", n=hours, m=mins, lang=lang)
    days, hrs = divmod(hours, 24)
    return t("dur.d", n=days, h=hrs, lang=lang)


def format_age(ms_ago: float, lang: str = None) -> str:
    """Relative age of a finished notification."""
    seconds = max(0, int(ms_ago // 1000)) if ms_ago else 0
    if seconds < 60:
        return t("age.now", lang=lang)
    if seconds < 3600:
        return t("age.min", n=seconds // 60, lang=lang)
    if seconds < 86400:
        return t("age.hour", n=seconds // 3600, lang=lang)
    return t("age.day", n=seconds // 86400, lang=lang)
