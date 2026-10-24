"""按事件取机器人：多账号同时在线时，回复必须由收到这条消息的那个号发出。

``nonebot.get_bot()`` 只会返回“随便一个在线的号”；这里优先按事件的 self_id 取，
取不到（单号、定时任务、测试）再退回 get_bot()。

``bind(event)``：在事件处理开头调用，记下“这次是哪个号在处理”。之后同一调用链（含它派生的
后台任务）里的 ``env.bot_qq()`` / ``roles.current_role()`` 都以这个号为准，
角色人格、自称、“艾特我”的识别就不会串到另一个号上。
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_current: ContextVar[str] = ContextVar("furina_self_id", default="")


def bind(event: Any = None, self_id: Any = None) -> str:
    sid = str(self_id or getattr(event, "self_id", "") or "")
    if sid:
        _current.set(sid)
    return sid


def current_self_id() -> str:
    return _current.get()


def bot_for(event: Any = None, self_id: Any = None):
    from nonebot import get_bot, get_bots

    sid = str(self_id or getattr(event, "self_id", "") or "")
    if sid:
        bot = get_bots().get(sid)
        if bot is not None:
            return bot
    return get_bot()
