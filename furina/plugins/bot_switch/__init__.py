"""换机器人 QQ 号后，米游社签到 / 推送 / 验证提醒等功能照常可用。

GsCore 记订阅（每天自动签到、签到报告、体力推送……）时，会把「当时的机器人 QQ」一起存下来；
官方适配器发消息时只认这个 QQ，换号以后这些消息会被直接丢掉。这里做两件事：
1. 发送兜底：GsCore 要求的机器人不在线时，改由当前在线的机器人发出；
2. 换号同步：新机器人连上后，通知 GsCore 把订阅里记的旧 QQ 改成新号（上游部分「改订阅」操作按 QQ 精确匹配）。
账号绑定、Cookie、签到开关都是按群友 QQ 存的，与机器人 QQ 无关，换号不受影响。
"""
from __future__ import annotations

import asyncio

import httpx
from nonebot import get_bots, get_driver, logger, require
from nonebot.adapters import Bot

require("GenshinUID")

driver = get_driver()
_told: set[str] = set()
_bg: set = set()


def _mask(qq: str) -> str:
    qq = str(qq or "")
    return f"{qq[:2]}***{qq[-2:]}" if len(qq) > 4 else qq


def _onebot_bots() -> list[Bot]:
    return [b for b in get_bots().values() if "onebot" in b.adapter.get_name().lower()]


def fallback_bot(bot_id: str, bot_self_id: str) -> Bot | None:
    """原机器人不在线时选一个替补：同平台（OneBot）当前在线的机器人。"""
    if bot_self_id and bot_self_id in get_bots():
        return None
    if str(bot_id or "onebot") not in ("onebot", "onebot_v11", "onebotv11"):
        return None
    online = _onebot_bots()
    return online[0] if online else None


def install_send_fallback() -> bool:
    try:
        from GenshinUID import client as gs_client
    except Exception as exc:
        logger.warning(f"[换号兼容] 找不到 GsCore 适配器，跳过: {exc}")
        return False
    orig = gs_client._pick_bots
    if getattr(orig, "_furina", False):
        return True

    def _pick_bots(msg):
        bots = orig(msg)
        if bots:
            return bots
        pick = fallback_bot(getattr(msg, "bot_id", ""), getattr(msg, "bot_self_id", ""))
        if pick is None:
            return bots
        old = str(getattr(msg, "bot_self_id", ""))
        if old not in _told:
            _told.add(old)
            logger.info(f"[换号兼容] 机器人 {_mask(old)} 不在线，GsCore 的消息改由当前机器人 {_mask(pick.self_id)} 发送")
        return [pick]

    _pick_bots._furina = True  # type: ignore[attr-defined]
    gs_client._pick_bots = _pick_bots
    return True


async def sync_gscore(self_id: str, tries: int = 12) -> None:
    """把 GsCore 订阅里记的旧机器人 QQ 改成当前这个（GsCore 没启动就隔 30 秒再试）。"""
    try:
        from GenshinUID.config import gs_config

        port = gs_config().gsuid_core_port
    except Exception:
        port = 8765
    url = f"http://127.0.0.1:{port}/furina/bot-self"
    for i in range(tries):
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                r = await client.post(url, json={"bot_id": "onebot", "self_id": str(self_id)})
            if r.status_code == 200:
                n = int((r.json() or {}).get("moved", 0))
                if n:
                    logger.info(f"[换号兼容] 已把 GsCore 里 {n} 条订阅改到机器人 {_mask(self_id)}")
                return
        except Exception:
            pass
        await asyncio.sleep(30 if i else 10)


@driver.on_startup
async def _startup() -> None:
    install_send_fallback()


@driver.on_bot_connect
async def _on_connect(bot: Bot) -> None:
    if "onebot" not in bot.adapter.get_name().lower():
        return
    task = asyncio.create_task(sync_gscore(str(bot.self_id)))
    _bg.add(task)
    task.add_done_callback(_bg.discard)
