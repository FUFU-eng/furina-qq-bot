"""Fail closed until the operator confirms the QQ account selected in SnowLuma.

The transport client may connect before its account identity has been checked.
Until that explicit hand-off, every inbound event is ignored before matchers run,
and every outbound OneBot action (read-only ones included) is answered with a
mocked ``None`` instead of reaching the transport. An invalid ``QQ_TRANSPORT``
value is treated the same way (fail closed) instead of raising on every event.
"""
from __future__ import annotations

import time

from nonebot import logger, on_message
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Event
from nonebot.exception import IgnoredException, MockApiException
from nonebot.message import event_preprocessor
from nonebot.rule import Rule

from furina.core import qq_transport
from furina.core.qq_transport import account_confirmed

__all__ = ["account_confirmed"]

_last_log = [0.0]


def _blocked(event: Event) -> bool:
    return not account_confirmed(str(getattr(event, "self_id", "") or ""))


def _log_blocked() -> None:
    now = time.monotonic()
    if now - _last_log[0] > 60:
        _last_log[0] = now
        try:
            qq_transport.provider()
        except ValueError:
            logger.error("[QQ接入] QQ_TRANSPORT 只能是 napcat 或 snowluma；配置改对之前暂停一切收发")
            return
        logger.warning("[SnowLuma] 拦截了账号确认前的入站事件/出站操作；请在本机面板确认机器人账号")


@event_preprocessor
async def _hold_unconfirmed_event(bot: Bot, event: Event) -> None:
    self_id = str(getattr(bot, "self_id", "") or getattr(event, "self_id", "") or "")
    if not account_confirmed(self_id):
        _log_blocked()
        raise IgnoredException("SnowLuma account confirmation required")


_guard = on_message(rule=Rule(_blocked), priority=0, block=True)


@_guard.handle()
async def _hold_unconfirmed_message(event: Event):
    return


@Bot.on_calling_api
async def _block_unconfirmed_outbound(bot, api: str, data: dict) -> None:  # noqa: ANN001
    if account_confirmed(str(getattr(bot, "self_id", "") or "")):
        return
    _log_blocked()
    raise MockApiException(result=None)
