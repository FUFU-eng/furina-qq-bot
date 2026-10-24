"""睡醒补看（qq-bridge 的「时间感」：超过两小时的旧消息默认不回，除非是直接找你的，那就说「才看到」）。

她离线（「睡了一觉」）期间有人艾特她，以前醒来就当没发生过。现在：
- 启动灌历史时记下「她最后一次说话之后、艾特她的消息」（history.py → collect）；
- 醒来 1~3 分钟后，每个群最多补回一条——最新的那条（太多条全回像机器人在清待办）；
- 只补 12 小时内的；对方是别的机器人不补；这个群没开 AI 聊天不补；
- 回的时候带上时间感（「才看到」「睡过头了」），引用那条消息并艾特 TA；
- 设置 → 活人感 →「睡醒补看」可关（AI_CHAT_CATCH_UP）。
"""
from __future__ import annotations

import asyncio
import random
import time

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from . import config, humanize, llm, memory
from ...core import db, permissions

KEEP_HOURS = 12
_pending: dict[str, list[dict]] = {}
_task: dict = {}

PROMPT = """【你刚醒，翻到一条之前没看到的消息】
{name} 在 {when}（{ago}前）艾特你说：「{text}」
那时候你不在（睡着了 / 没看手机），一直没回。群里后来的几句（从旧到新，"你"是你自己说的）：
{recent}
现在回 TA 一句：开头带一点时间感（「才看到」「刚醒」「睡过头了」这类，别照抄），然后正常回 TA 那句话的意思；
如果那件事明显已经过去了，就随口带过。别道歉一大段，别提机器人、服务器、掉线、重启。
写一到两行，每行一条消息、每条不超过二十个字；不要写 TA 的名字（会自动艾特 TA），不要括号动作，不要任何 [标记]。"""


def _ago(sec: float) -> str:
    m = max(1, int(sec // 60))
    return f"{m} 分钟" if m < 60 else f"{m // 60} 个多小时"


def collect(group_id, items: list[dict]) -> None:
    if not config.CATCH_UP or not items:
        return
    _pending[str(group_id)] = list(items)
    if not _task.get("t") or _task["t"].done():
        try:
            _task["t"] = asyncio.get_running_loop().create_task(_later(random.uniform(60, 180)))
        except RuntimeError:
            pass


def pick(group_id, now: float | None = None) -> dict | None:
    """这个群要补回的那一条（纯判断，方便测试）。"""
    ts = time.time() if now is None else now
    items = _pending.get(str(group_id)) or []
    wake = db.get("presence", "wake") or {}
    if not wake or ts - float(wake.get("at") or 0) > 1800:
        return None  # 不是「睡了一觉刚醒」（只是短暂重启）就不补
    start = float(wake.get("from") or 0)
    peers = set(config.PEER_BOTS)
    ok = [x for x in items if ts - float(x.get("ts") or 0) <= KEEP_HOURS * 3600 and str(x.get("uid")) not in peers
          and float(x.get("ts") or 0) >= start - 120]
    return max(ok, key=lambda x: float(x.get("ts") or 0)) if ok else None


async def _later(delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await run()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[睡醒补看] 失败: {e}")


async def run(now: float | None = None) -> None:
    from . import proactive
    from .cleanup import drop_periods
    ts = time.time() if now is None else now
    bot, sid = proactive._pick_bot()
    if bot is None:
        return
    for gid in list(_pending):
        item = pick(gid, ts)
        _pending.pop(gid, None)
        if not item or not permissions.allowed(gid, "chat", True):
            continue
        if memory.replied_text(int(gid), item.get("mid")) is not None:
            continue
        hist = memory.get_history(int(gid))
        if any(e.get("role") == "assistant" and float(e.get("ts") or 0) > float(item["ts"]) for e in hist):
            continue  # 她醒来以后已经说过话了，就不专门补了
        recent = "\n".join(("你：" if e.get("role") == "assistant" else "") + str(e.get("content") or "")[:60]
                           for e in hist if float(e.get("ts") or 0) >= float(item["ts"]))[-600:] or "（没人再说话）"
        prompt = PROMPT.format(name=item.get("name") or "TA", when=time.strftime("%H:%M", time.localtime(item["ts"])),
                               ago=_ago(ts - float(item["ts"])), text=str(item.get("text") or "")[:80], recent=recent)
        try:
            raw = await llm.chat([{"role": "system", "content": proactive.base_system(gid, sid)},
                                  {"role": "user", "content": prompt}])
        except llm.LLMError as e:
            logger.warning(f"[睡醒补看] 群{gid} 生成失败: {e}")
            continue
        text = proactive._clean(raw, 2)
        if config.NO_PERIOD:
            text = drop_periods(text)
        if len(text) < 2:
            continue
        gid_int = int(gid)
        sender = humanize.guarded(lambda m, g=gid_int: bot.send_group_msg(group_id=g, message=m), gid_int)
        for idx, line in enumerate(text.split("\n")):
            await humanize.pause(humanize.typing_seconds(line) if idx == 0 else humanize.burst_gap(line))
            if idx == 0:
                segs = [MessageSegment.reply(int(item["mid"]))] if item.get("mid") else []
                segs += [MessageSegment.at(int(item["uid"])), MessageSegment.text(" " + line)]
                msg = Message(segs)
            else:
                msg = Message(MessageSegment.text(line))
            if await sender(msg) is None:
                break
        memory.add_message(gid_int, "assistant", text, meta={"to": item.get("mid")})
        memory.mark_replied(gid_int, [item.get("mid")], text)
        logger.info(f"[睡醒补看] 群{gid} 补回了 {item.get('name')} {_ago(ts - float(item['ts']))}前的艾特：{text}")
        await asyncio.sleep(random.uniform(20, 90))
