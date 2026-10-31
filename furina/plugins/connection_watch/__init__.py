"""Log transport changes and NapCat's QQ logout notice as separate events.

QQ 掉线（下线通知）后：NAPCAT_AUTO_RECOVER 打开时（默认开），等一会儿仍掉线就先用快速登录（免扫码）重启
机器人用的 NapCat，登不上再出二维码（furina/services/napcat_recover.py，30 分钟内最多自动一次）。
连接断开却没收到下线通知（NapCat 崩了 / 卡死）：断开 NAPCAT_WS_LOST_MINUTES 分钟还没连回来，也走同一套
自动恢复（schedule_auto 会先查状态，确实没登录 / NapCat 没在跑才重启，冷却同样生效）。
机器人自己重启（比如更新代码）把等待中的自动恢复打断时，连上后会接着做（resume_pending）。
刚出过二维码的几分钟内不自动重启 NapCat，免得把别人正在扫的二维码作废。
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
    from furina.core import qq_transport
    if qq_transport.uses_snowluma():
        logger.error("[QQ登录失效] SnowLuma transport notice: qq={}；本插件不会自动重试个人QQ登录", bot.self_id)
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
    old = _lost_tasks.pop(str(bot.self_id), None)
    if old:
        old.cancel()
    from furina.core import qq_transport
    if qq_transport.uses_snowluma():
        return
    from furina.services import napcat_recover

    # 机器人重启把「掉线后自动恢复」打断了：接着做（只认掉线时记下的那个号）
    if napcat_recover.resume_pending(str(bot.self_id)):
        logger.warning("[QQ自动恢复] qq={} 重启前掉线后的自动恢复还没做，已接着安排", bot.self_id)
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


_lost_tasks: dict[str, asyncio.Task] = {}


def ws_lost_minutes() -> int:
    from furina.core import env

    try:
        return max(0, int(env.get("NAPCAT_WS_LOST_MINUTES", "5") or 0))
    except ValueError:
        return 5


async def _watch_lost(qq: str, minutes: int) -> None:
    await asyncio.sleep(minutes * 60)
    import nonebot

    if qq in nonebot.get_bots():
        return
    from furina.services import napcat_recover

    planned = napcat_recover.schedule_auto(qq, "ws_lost", f"OneBot 连接断开 {minutes} 分钟没连回来")
    logger.error("[OneBot断连] qq={} 断开 {} 分钟没回来；{}", qq, minutes,
                 "已安排检查 QQ 状态并自动恢复" if planned else "自动恢复未开启或冷却中")


@_driver.on_bot_disconnect
async def _disconnected(bot: Bot):
    from furina.core import qq_transport
    if qq_transport.uses_snowluma():
        logger.warning("[OneBot断连] qq={} SnowLuma到NoneBot的连接断开；不会自动重启或登录QQ客户端", bot.self_id)
        return
    logger.warning("[OneBot断连] qq={} 传输连接断开；请结合 NapCat 下线通知区分原因", bot.self_id)
    minutes = ws_lost_minutes()
    qq = str(bot.self_id)
    old = _lost_tasks.pop(qq, None)
    if old:
        old.cancel()
    if minutes:
        _lost_tasks[qq] = asyncio.create_task(_watch_lost(qq, minutes))
