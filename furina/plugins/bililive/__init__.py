"""芙宁娜当主播：主人在「直播姬」里开播后，她自己听弹幕、用 AI 声音和观众聊天。

- 画面：直播姬里加一个「浏览器 / 网页」素材，地址 http://127.0.0.1:<机器人端口>/live/stage（stage.py）
- 声音：微软免费 AI 女声（voice.py），不用角色原声；画面上一直挂着「AI 虚拟主播 · 同人非官方」
- 保险：safety.py（不接涉政涉黄、骂人、广告、套话；刷屏复读不理；有人带节奏就岔开话题并私聊主人）
- 开关：面板「B站生活 → 直播（她当主播）」里的 BILI_LIVE_ENABLED（默认关，改了几秒生效）

主人私聊指令：闭麦 / 开麦 / 直播状态（群里艾特也行）
"""
from __future__ import annotations

import asyncio
import re

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from ...core import env, roles
from . import host, stage

__plugin_meta__ = PluginMetadata(
    name="B站直播",
    description="主人开播后芙宁娜当 AI 虚拟主播，听弹幕用声音聊天",
    usage="私聊：闭麦 / 开麦 / 直播状态",
)

_CMD_RE = re.compile(r"^\s*(?:直播)?\s*(闭麦|开麦|状态)\s*$|^\s*直播状态\s*$")
_tasks: list[asyncio.Task] = []


def _mount() -> None:
    try:
        from nonebot import get_app
        stage.mount(get_app())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[B站直播] 舞台网页没挂上（驱动不是 FastAPI？）：{e}")


_mount()


@get_driver().on_startup
async def _start() -> None:
    _tasks.append(asyncio.create_task(host.HOST.run_forever()))
    _tasks.append(asyncio.create_task(host.HOST.speaker_loop()))


@get_driver().on_shutdown
async def _stop() -> None:
    for t in _tasks:
        t.cancel()


def _is_owner(uid) -> bool:
    owner = env.owner_qq()
    supers = {str(s) for s in (getattr(get_driver().config, "superusers", None) or set())}
    return bool(str(uid) and (str(uid) == owner or str(uid) in supers))


def _text(event) -> str:
    raw = str(getattr(event, "raw_message", "") or "")
    t = roles.strip_bot_at(raw, str(getattr(event, "self_id", "") or ""))
    return re.sub(r"\[(?:CQ:)?at[^\]]*\]", "", t).strip()


def _rule(event) -> bool:
    if isinstance(event, GroupMessageEvent):
        if not roles.is_bot_at(event):
            return False
    elif not isinstance(event, PrivateMessageEvent):
        return False
    t = _text(event)
    if not _CMD_RE.match(t) or not _is_owner(event.user_id):
        return False
    # 「状态」两个字太常见：只有正在直播、或者明说「直播状态」才接
    return "麦" in t or "直播" in t or host.HOST.live


_cmd = on_message(rule=Rule(_rule), priority=4, block=True)


@_cmd.handle()
async def _(event) -> None:
    t = _text(event)
    h = host.HOST
    if "闭麦" in t:
        h.muted = True
        stage.push({"type": "status", "status": "muted"})
        host.save_state(muted=True)
        await _cmd.finish("好，我先不说话了（弹幕还在听）。发「开麦」我再继续。")
    if "开麦" in t:
        h.muted = False
        h.busy_until = 0
        stage.push({"type": "status", "status": "live" if h.live else "idle"})
        host.save_state(muted=False)
        await _cmd.finish("开麦啦～" if h.live else "开麦了，不过现在没在直播，等你开播我就上。")
    await _cmd.finish(status_text())


def status_text() -> str:
    h, c, st = host.HOST, host._conf(), host.get_state()
    if not c["enabled"]:
        return "直播功能现在是关着的（面板「B站生活 → 直播（她当主播）」里打开）。"
    room = h.room or st.get("room") or "还没找到"
    if not h.live:
        return f"现在没在直播（直播间 {room}）。你在直播姬里点开始直播，我半分钟内就上场。"
    s = h.stats
    extra = "；我现在闭麦中" if h.muted else ""
    if stage.clients() == 0:
        extra += "；直播姬里好像没加我的舞台画面，观众听不到我"
    return (f"正在直播（直播间 {room}），收到 {s['danmaku']} 条弹幕，我回了 {s['replies']} 次，"
            f"挡掉 {s['blocked']} 条{extra}。")
