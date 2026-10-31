"""启动时灌入群历史上下文。"""

import asyncio

from nonebot import get_bot, get_driver, logger

from . import config, memory
from ...core.live_groups import get_live_groups
from .cleanup import _scrub_fixed_template
from .quotes import _iter_segments
from .state import _seeded_groups

driver = get_driver()

def _history_segment_text(msg) -> str:
    """把历史消息的消息段转成一行摘要（用于灌初始上下文）。"""
    parts = []
    for stype, sdata in _iter_segments(msg):
        if stype == "text":
            t = str(sdata.get("text") or "").strip()
            if t:
                parts.append(t)
        elif stype == "image":
            parts.append("[图片]")
        elif stype == "face":
            parts.append("[表情]")
        elif stype == "video":
            parts.append("[视频]")
        elif stype == "at":
            parts.append("[@某人]")
        elif stype == "forward":
            parts.append("[合并转发]")
    return " ".join(parts)[:200] if parts else ""


async def _seed_group_history(group_id: int):
    """启动时拉取群最近聊天记录，灌入记忆当作初始上下文。"""
    try:
        bot = get_bot()
        ret = await bot.call_api(
            "get_group_msg_history",
            group_id=str(group_id),
            count=config.HISTORY_SEED_COUNT,
            reverse_order=False,
        )
        msgs = (ret or {}).get("messages") or []
    except Exception as e:
        _seeded_groups.discard(group_id)  # 没拉到：下次连上再试
        logger.warning(f"[AI聊天] 加载群{group_id}历史记录失败: {e}")
        return
    if not msgs:
        logger.warning(f"[AI聊天] 群{group_id}没有可加载的历史记录")
        return

    history = []
    at_me = []  # 她上次说话之后、艾特她的消息（睡醒补看用）
    self_ids = {str(config.BOT_QQ or ""), str(getattr(bot, "self_id", "") or "")} - {""}
    for m in msgs:
        user_id = str(m.get("user_id") or "")
        sender = m.get("sender") or {}
        if user_id == config.GROUP_OWNER_QQ:
            name = "我超爱芙啦（群主）"
        elif isinstance(sender, dict):
            name = sender.get("card") or sender.get("nickname") or ""
        else:
            name = getattr(sender, "card", "") or getattr(sender, "nickname", "") or ""
        text = _history_segment_text(m.get("message"))
        if not text:
            continue
        reply_mid = next((str(d.get("id") or "") for t, d in _iter_segments(m.get("message")) if t == "reply"), "")
        meta = {"mid": m.get("message_id"), "reply": reply_mid}
        if user_id == config.BOT_QQ or user_id in self_ids:
            history.append({"role": "assistant", "content": _scrub_fixed_template(text), "ts": m.get("time"), **meta})
            at_me.clear()
        else:
            if any(t == "at" and str(d.get("qq") or "") in self_ids for t, d in _iter_segments(m.get("message"))):
                at_me.append({"uid": user_id, "name": name or user_id, "text": text, "ts": float(m.get("time") or 0),
                              "mid": m.get("message_id")})
            history.append({"role": "user", "content": f"{name or user_id}：{text}", "ts": m.get("time"),
                            "uid": user_id, "name": name or user_id, **meta})

    memory.seed(group_id, history)
    logger.info(f"[AI聊天] 群{group_id} 已加载最近 {len(history)} 条消息作为初始上下文")
    if at_me:
        try:
            from . import catchup
            catchup.collect(group_id, at_me)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[AI聊天] 群{group_id} 记录离线期间的艾特失败: {e}")



_seed_tasks: set = set()


async def _wait_account_confirmed(bot, step: float = 15.0, limit: float = 6 * 3600) -> bool:
    """SnowLuma 账号确认前所有接口都被拦（返回 None）：这时灌历史只会得到空记录、还把群标成「已灌过」。
    等面板确认后再灌；机器人断开或等太久就放弃（下次连上会重新来）。"""
    import nonebot

    from ...core import qq_transport

    sid = str(getattr(bot, "self_id", "") or "")
    waited = 0.0
    logged = False
    while not qq_transport.account_confirmed(sid):
        if not logged:
            logger.info("[AI聊天] 机器人账号还没在面板确认，确认后再加载群聊历史")
            logged = True
        if waited >= limit or sid not in nonebot.get_bots():
            return False
        await asyncio.sleep(step)
        waited += step
    return True


@driver.on_bot_connect
async def _load_history_on_connect(bot):
    """机器人连上协议端后，给每个群灌最近聊天记录（只灌一次，不阻塞连接）。"""
    task = asyncio.create_task(_seed_when_ready(bot))
    _seed_tasks.add(task)
    task.add_done_callback(_seed_tasks.discard)


async def _seed_when_ready(bot):
    await asyncio.sleep(2)
    if not await _wait_account_confirmed(bot):
        return
    gids = []
    _live = await get_live_groups()
    for gid_str in sorted(_live):
        try:
            gid = int(gid_str)
        except (TypeError, ValueError):
            continue
        if gid in _seeded_groups:
            continue
        _seeded_groups.add(gid)
        gids.append(gid)
    if not gids:
        return
    results = await asyncio.gather(
        *(_seed_group_history(g) for g in gids),
        return_exceptions=True,
    )
    for gid, result in zip(gids, results):
        if isinstance(result, Exception):
            logger.warning(f"[AI聊天] 加载群{gid}历史失败: {result}")

