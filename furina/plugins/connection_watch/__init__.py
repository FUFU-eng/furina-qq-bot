"""Log transport changes and NapCat's QQ logout notice as separate events.

QQ 掉线（下线通知）后：NAPCAT_AUTO_RECOVER 打开时（默认开），等一会儿仍掉线就先用快速登录（免扫码）重启
机器人用的 NapCat，登不上再出二维码（furina/services/napcat_recover.py，30 分钟内最多自动一次）。
不重放消息。
"""
import asyncio

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
    from furina.services import napcat_recover

    tag, msg = _safe(data.get("tag")), _safe(data.get("message"))
    planned = napcat_recover.schedule_auto(str(bot.self_id), tag, msg)
    logger.error(
        "[QQ登录失效] qq={} tag={} reason={}；{}",
        bot.self_id, tag, msg,
        f"{napcat_recover.delay_seconds()} 秒后仍掉线就自动用快速登录（免扫码）重启 NapCat，不行再出二维码"
        if planned else "需人工扫码登录（自动恢复未开启或冷却中）",
    )


@_driver.on_bot_connect
async def _connected(bot: Bot):
    logger.info("[OneBot连接] qq={} 传输连接建立（不等于 QQ 登录有效）", bot.self_id)
    from furina.services import napcat_recover

    # 自动恢复时 NapCat 一登上就会连过来，可能比恢复线程写下「已成功」还早一点：恢复进行中就多等几秒
    for _ in range(10):
        if napcat_recover.pop_owner_notice():
            owner = _owner_qq()
            if owner:
                try:
                    await bot.send_private_msg(user_id=int(owner), message="刚才 QQ 掉线了，已经自己免扫码重新登上来啦")
                except Exception as e:  # noqa: BLE001
                    logger.warning("[QQ自动恢复] 提醒主人失败: {}", e)
            return
        if not napcat_recover.recovering():
            return
        await asyncio.sleep(2)


def _owner_qq() -> str:
    from furina.core import env

    q = env.get("AI_CHAT_GROUP_OWNER_QQ").strip()
    return q if q.isdigit() else ""


@_driver.on_bot_disconnect
async def _disconnected(bot: Bot):
    logger.warning("[OneBot断连] qq={} 传输连接断开；请结合 NapCat 下线通知区分原因", bot.self_id)
