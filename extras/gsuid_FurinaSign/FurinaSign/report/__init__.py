"""芙宁娜米游社签到战报。

签到仍由四个上游插件的定时任务完成；这里只在之后只读查询状态，
按群汇总成「问候 + 总结卡片」发送，替代上游逐游戏的私聊刷屏。
"""
from __future__ import annotations

import random
import sys
import asyncio
import datetime as dt
from typing import Dict, List, Optional

from gsuid_core.sv import SV
from gsuid_core.gss import gss
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.utils.sign.sign import sign_in
from gsuid_core.utils.database.models import GsBind, Subscribe

from .card import render_card
from .config import FS_CONFIG, load_state, save_state, report_time, latest_sign_time
from .collect import GAMES, Cell, Member, _bbb_until, fill_cells, load_members, resolve_name, group_members

sv_sign = SV("芙芙米游社签到")
sv_report = SV("芙芙签到战报", pm=3)
sv_push = SV("芙芙签到战报推送", pm=1)

BIND_HINT = "还没有找到你绑定的游戏账号哦～\n@芙芙 绑定账号，用米游社扫个码就能一次绑定全部游戏"
WINDOW_MINUTES = 30  # GsCore 恰好在发送时刻重启时，30 分钟内仍会补发一次
_running = False


def _mys_bind():
    """FurinaMys 的扫码绑定模块（同一 GsCore 里加载的兄弟插件）；找不到时返回 None。"""
    for name, mod in list(sys.modules.items()):
        if mod is not None and name.endswith("FurinaMys.mys.bind") and hasattr(mod, "start_bind"):
            return mod
    return None


def _bot_name() -> str:
    return str(FS_CONFIG.get_config("BotName").data or "芙芙")


def _sign_time_text() -> str:
    hour, minute = latest_sign_time()
    return f"{hour:02d}:{minute:02d}"


def _greeting(now: Optional[dt.datetime] = None) -> str:
    hour = (now or dt.datetime.now()).hour
    if 5 <= hour < 11:
        hello = "早上好呀"
    elif 11 <= hour < 14:
        hello = "中午好呀"
    elif 14 <= hour < 18:
        hello = "下午好呀"
    elif 18 <= hour < 23:
        hello = "晚上好呀"
    else:
        hello = "夜深啦"
    return f"💧 {hello}！{_bot_name()}已为大家完成今日 {_sign_time_text()} 米游社自动签到，请查收今日战报："


def _card_kwargs(title: str = "米游社自动签到总结") -> dict:
    return {
        "title": title,
        "subtitle": "芙宁娜的沙龙女仆团已为您完成今日米游社四游巡礼签到",
        "sign_time": _sign_time_text(),
        "bot_name": _bot_name(),
    }


def _group_target(group_id: str, ref: Subscribe) -> Optional[Subscribe]:
    ws_id = ref.WS_BOT_ID if ref.WS_BOT_ID in gss.active_bot else None
    if ws_id is None:
        ws_id = next((k for k, b in gss.active_bot.items() if b.bot_id == ref.bot_id), None)
    if ws_id is None:
        return None
    return Subscribe(
        user_id=ref.user_id,
        bot_id=ref.bot_id,
        group_id=group_id,
        task_name="FurinaSign",
        bot_self_id=ref.bot_self_id,
        user_type="group",
        WS_BOT_ID=ws_id,
    )


async def _collect() -> Dict[str, List[Member]]:
    members = await load_members()
    for member in members.values():
        await fill_cells(member)
    return await group_members(members)


async def _send_group(group_id: str, members: List[Member], greeting: bool = True) -> bool:
    ordered = []
    for member in members:
        member.name = await resolve_name(member, group_id)
        ordered.append(member)
    ordered.sort(key=lambda m: (m.name, m.user_id))
    image = await render_card(ordered, **_card_kwargs())
    ref = next(iter(ordered[0].subs.values()))
    target = _group_target(group_id, ref)
    if target is None:
        logger.warning(f"[FurinaSign] 没有在线的机器人连接，跳过群 {group_id}")
        return False
    message = [MessageSegment.image(image)]
    if greeting:
        message.insert(0, MessageSegment.text(_greeting() + "\n"))
    return await target.send(message) != -1


async def run_daily_report(force: bool = False) -> int:
    """返回成功发送的群数。签到尚未完成时最多再等 3 轮（每轮 10 分钟）。"""
    global _running
    if _running:
        return 0
    _running = True
    try:
        grouped: Dict[str, List[Member]] = {}
        for attempt in range(4):
            grouped = await _collect()
            pending = any(c.state == "pending" for ms in grouped.values() for m in ms for c in m.cells.values())
            if not pending or force or attempt == 3:
                break
            logger.info("[FurinaSign] 仍有账号尚未完成签到，10 分钟后再汇总")
            await asyncio.sleep(600)
        sent = 0
        for group_id, members in sorted(grouped.items()):
            try:
                if await _send_group(group_id, members):
                    sent += 1
                await asyncio.sleep(random.uniform(2, 4))
            except Exception as exc:
                logger.exception(f"[FurinaSign] 群 {group_id} 战报发送失败: {exc}")
        logger.info(f"[FurinaSign] 今日签到战报已发送到 {sent}/{len(grouped)} 个群")
        return sent
    finally:
        _running = False


@scheduler.scheduled_job("cron", minute="*", second=25, id="furina_sign_report_tick", max_instances=1, coalesce=True)
async def furina_sign_report_tick():
    if not FS_CONFIG.get_config("ReportEnable").data:
        return
    now = dt.datetime.now()
    hour, minute = report_time()
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not (start <= now < start + dt.timedelta(minutes=WINDOW_MINUTES)):
        return
    state = load_state()
    today = now.date().isoformat()
    if state.get("last_report") == today:
        return
    state["last_report"] = today
    save_state(state)
    asyncio.create_task(run_daily_report())


# ---------------- 指令 ----------------

@sv_sign.on_fullmatch("签到", block=True, to_me=True)
async def furina_sign_all(bot: Bot, ev: Event):
    uids: Dict[str, str] = {}
    for game, _, _ in GAMES:
        uid = await GsBind.get_uid_by_game(ev.user_id, ev.bot_id, game)
        if uid:
            uids[game] = str(uid)
    if not uids:
        bind_mod = _mys_bind()
        if bind_mod is not None:
            # 没绑定：直接走芙芙的扫码绑定流程，绑定成功后自动接着签到
            return await bind_mod.start_bind(bot, ev, unbound=True, after=lambda: furina_sign_all(bot, ev))
        return await bot.send(BIND_HINT)
    names = "、".join(label for game, label, _ in GAMES if game in uids)
    await bot.send(f"💧 收到～{_bot_name()}正在为你签到：{names}，稍等几十秒哦")
    for game, uid in uids.items():
        try:
            if game == "bbb":
                until = _bbb_until()
                if until is not None:
                    await until.sign_by_uid(uid)
            else:
                await sign_in(uid, game)
        except Exception as exc:
            logger.exception(f"[FurinaSign] {game} 手动签到异常: {exc}")
        await asyncio.sleep(random.uniform(1.5, 3.0))
    member = Member(bot_id=str(ev.bot_id), user_id=str(ev.user_id))
    sender = getattr(ev, "sender", None) or {}
    member.name = str(sender.get("card") or sender.get("nickname") or "") or await resolve_name(member, ev.group_id)
    from .collect import query_cell

    for game, _, _ in GAMES:
        member.cells[game] = await query_cell(game, uids[game]) if game in uids else Cell("off", "未绑定")
    image = await render_card([member], **_card_kwargs("米游社签到结果"))
    await bot.send(image)


@sv_report.on_fullmatch(("签到总结", "战报", "签到战报"), block=True, to_me=True)
async def furina_sign_summary(bot: Bot, ev: Event):
    if not ev.group_id:
        return await bot.send("请在群里发送，芙芙会汇总本群已订阅自动签到的群友～")
    await bot.send("💧 正在汇总本群今日签到情况…")
    grouped = await _collect()
    members = grouped.get(str(ev.group_id)) or []
    if not members:
        return await bot.send("本群还没有开启自动签到的群友（或本群未开启「米游社签到」权限）。")
    for member in members:
        member.name = await resolve_name(member, ev.group_id)
    members.sort(key=lambda m: (m.name, m.user_id))
    await bot.send(await render_card(members, **_card_kwargs()))


@sv_push.on_fullmatch("战报推送", block=True, to_me=True)
async def furina_sign_push(bot: Bot, ev: Event):
    await bot.send("🚩 正在向目标群推送今日签到战报（只查询状态，不重复签到）…")
    sent = await run_daily_report(force=True)
    await bot.send(f"🚩 推送完成：{sent} 个群。")
