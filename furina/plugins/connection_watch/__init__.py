"""Log transport changes and NapCat's QQ logout notice as separate events.

Intentionally no automatic relogin, process restart or message replay.
"""
from nonebot import get_driver, logger, on_notice
from nonebot.adapters.onebot.v11 import Bot, Event

_driver = get_driver()
_offline = on_notice(priority=1, block=False)


def _safe(value):
    return str(value or "").replace("\r", " ").replace("\n", " ")[:400]


@_offline.handle()
async def _on_offline(bot: Bot, event: Event):
    data = event.model_dump()
    if data.get("notice_type") != "bot_offline":
        return
    logger.error(
        "[QQ登录失效] qq={} tag={} reason={}；需人工确认登录，WS 重连不能恢复已失效凭据",
        bot.self_id, _safe(data.get("tag")), _safe(data.get("message")),
    )


@_driver.on_bot_connect
async def _connected(bot: Bot):
    logger.info("[OneBot连接] qq={} 传输连接建立（不等于 QQ 登录有效）", bot.self_id)


@_driver.on_bot_disconnect
async def _disconnected(bot: Bot):
    logger.warning("[OneBot断连] qq={} 传输连接断开；请结合 NapCat 下线通知区分原因", bot.self_id)
