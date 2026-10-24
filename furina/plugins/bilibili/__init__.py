"""芙宁娜的 B 站生活：用小号自己刷视频，按她自己的意愿点赞/弹幕/评论/发动态，刷到好玩的会分享到群里，聊天时也会提起。

指令（只有主人能用；群里要先艾特机器人，私聊直接发）：
  B站登录        发登录二维码，用 B 站小号扫码（成功后自动撤回二维码）
  B站状态        看登录账号、今天评论/动态/点赞次数、最近刷到的视频
  B站刷一下      立刻刷一轮
  B站发动态      立刻按今天的经历写一条动态
  B站看回复      立刻看一眼评论区有没有人回她，想回就回
  B站暂停 / B站恢复 / B站退出登录

定时：每 5 分钟检查一次，在 BILI_HOURS 时段内随机找时间刷；每 10 秒看一眼面板「B站生活」有没有点按钮；
每 3 小时取一次 B 站热搜（聊天时偶尔能聊到）；每隔几分钟看一眼评论区有没有人回她（replies.py）。详见 surf.py 顶部说明。
分享到 QQ 群在面板「功能权限」里按群开关「B站刷到分享」（默认开）。
"""
from __future__ import annotations

import asyncio
import io
import re
import time

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from . import replies, surf
from ...core import env, live_groups, permissions, roles, scheduler, usage as daily_limits
from ...core.botref import bind as bind_bot
from ...services.bilibili import store
from ...services.bilibili.client import BiliError, Client

__plugin_meta__ = PluginMetadata(
    name="B站冲浪",
    description="芙宁娜用 B 站小号自己刷视频、评论、发动态，刷到好玩的分享到群里",
    usage="@芙宁娜 B站登录 / B站状态 / B站刷一下 / B站发动态 / B站暂停 / B站恢复",
)

SHARE_FEATURE = "bili_share"
JOB_SURF = "B站冲浪"
JOB_PANEL = "B站面板请求"
JOB_TREND = "B站热搜"
JOB_REPLY = "B站回复"
_busy = asyncio.Lock()
_CMD_RE = re.compile(r"^\s*(?:B站|b站|哔哩哔哩|bilibili)\s*(登录|登陆|状态|刷一下|刷视频|发动态|看回复|回复|暂停|恢复|退出登录)\s*$", re.I)
_login_task: asyncio.Task | None = None


def _is_owner(uid) -> bool:
    owner = env.owner_qq()
    supers = {str(s) for s in (getattr(get_driver().config, "superusers", None) or set())}
    return bool(str(uid) and (str(uid) == owner or str(uid) in supers))


def _cmd_text(event) -> str:
    raw = str(getattr(event, "raw_message", "") or "")
    text = roles.strip_bot_at(raw, str(getattr(event, "self_id", "") or ""))
    return re.sub(r"\[(?:CQ:)?at[^\]]*\]", "", text).strip()


def _rule(event) -> bool:
    if isinstance(event, GroupMessageEvent):
        if not roles.is_bot_at(event):
            return False
    elif not isinstance(event, PrivateMessageEvent):
        return False
    return bool(_CMD_RE.match(_cmd_text(event))) and _is_owner(event.user_id)


_cmd = on_message(rule=Rule(_rule), priority=5, block=True)


@_cmd.handle()
async def _on_cmd(bot, event):
    bind_bot(event)
    action = _CMD_RE.match(_cmd_text(event)).group(1)
    if action in ("登录", "登陆"):
        await _start_login(bot, event)
    elif action == "状态":
        await _cmd.send(_status_text())
    elif action in ("刷一下", "刷视频"):
        if not store.logged_in():
            await _cmd.send("还没登录 B 站小号，先艾特我说「B站登录」扫个码。")
            return
        if _busy.locked():
            await _cmd.send("我正在刷呢，等这轮看完～")
            return
        await _cmd.send("好，我去刷一会儿～")
        async with _busy:
            result = await surf.run_round(_notify_owner, _share_to_groups, manual=True)
        await _cmd.send(result)
    elif action == "发动态":
        if not store.logged_in():
            await _cmd.send("还没登录 B 站小号。")
            return
        await _cmd.send(await surf.post_dynamic(_life(), _notify_owner, manual=True))
    elif action in ("看回复", "回复"):
        if not store.logged_in():
            await _cmd.send("还没登录 B 站小号。")
            return
        if _busy.locked():
            await _cmd.send("我正在刷 B 站呢，等会儿再看消息～")
            return
        async with _busy:
            await _cmd.send(await replies.run_replies(_notify_owner, manual=True))
    elif action == "暂停":
        store.halt("主人手动暂停", 24 * 365)
        store.log_activity("主人让我先别刷了", "end")
        await _cmd.send("好，B 站先不刷了，想恢复就说「B站恢复」。")
    elif action == "恢复":
        store.resume()
        await _cmd.send("恢复刷 B 站啦。")
    elif action == "退出登录":
        store.clear_auth()
        await _cmd.send("已经把 B 站小号的登录信息删掉了。")


def _status_text() -> str:
    st = store.status()
    c = surf.cfg()
    if not st["logged_in"]:
        return "B站：还没登录（艾特我说「B站登录」扫码）"
    t = st["today"]

    def q(kind, cap):
        return f"{t.get(kind, 0)}/{cap}" if cap > 0 else f"{t.get(kind, 0)}"
    lines = [f"B站账号：{st['uname'] or '（未知昵称）'}" + ("（登录可能失效了）" if store.get_auth().get("invalid") else ""),
             f"今天：刷了 {q('rounds', c['rounds'])} 轮，点赞 {q('likes', c['like_max'])}，弹幕 {q('danmaku', c['danmaku_max'])}，"
             f"评论 {q('comments', c['comment_max'])}，回复 {q('replies', replies.cfg()['max'])}，动态 {q('dynamics', c['dynamic_max'])}",
             f"主人（B站昵称）：{c['mention'] or '未设置（BILI_MENTION_NAME）'}" + ("　｜　演练模式（只看不发）" if c["dry_run"] else "")]
    if st["halted"]:
        lines.append(f"⚠ 暂停中：{st['halt_reason']}")
    for w in st["watched"][-3:]:
        lines.append(f"· 《{w.get('title', '')[:24]}》{w.get('interest', 0)}分　{w.get('thought', '')[:30]}")
    return "\n".join(lines)


# ---------------- 扫码登录 ----------------
def _qr_png(url: str) -> bytes | None:
    try:
        import qrcode

        img = qrcode.make(url, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[B站] 生成二维码图片失败：{e}")
        return None


async def _start_login(bot, event):
    global _login_task
    if _login_task is not None and not _login_task.done():
        await _cmd.send("上一个登录二维码还在等扫码呢。")
        return
    try:
        async with Client(cookies={}) as cli:
            url, key = await cli.qr_generate()
    except (BiliError, Exception) as e:  # noqa: BLE001
        await _cmd.send(f"拿不到 B 站登录二维码：{e}")
        return
    png = _qr_png(url)
    tip = "用 B 站小号（别用大号）扫这个码登录，3 分钟内有效～"
    msg = Message([MessageSegment.text(tip + "\n"), MessageSegment.image(png)]) if png else Message(tip + "\n" + url)
    sent = await _cmd.send(msg)
    msg_id = (sent or {}).get("message_id") if isinstance(sent, dict) else None
    _login_task = asyncio.create_task(_poll_login(bot, event, key, msg_id))


async def _reply_to(bot, event, text: str):
    if isinstance(event, GroupMessageEvent):
        return await bot.send_group_msg(group_id=event.group_id, message=text)
    return await bot.send_private_msg(user_id=event.user_id, message=text)


async def _poll_login(bot, event, key: str, msg_id):
    result = "登录二维码过期了，要登录的话再说一次「B站登录」。"
    try:
        async with Client(cookies={}) as cli:
            deadline = time.time() + 180
            while time.time() < deadline:
                await asyncio.sleep(2.5)
                code, cookies = await cli.qr_poll(key)
                if code == 0 and cookies.get("SESSDATA"):
                    cli.cookies = {k: v for k, v in cookies.items() if not k.startswith("_")}
                    await cli.ensure_buvid()
                    nav = await cli.nav()
                    store.save_auth(cli.cookies, uname=nav.get("uname", ""), mid=str(nav.get("mid", "")))
                    if cookies.get("_refresh_token"):
                        store.set_meta("refresh_token", cookies["_refresh_token"])
                    result = f"B站登录好了：{nav.get('uname') or '小号'}。之后我会自己找时间去刷～"
                    logger.info(f"[B站] 小号登录成功：{nav.get('uname')}")
                    break
                if code == 86038:
                    break
    except Exception as e:  # noqa: BLE001
        result = f"登录过程出错了：{e}"
    if msg_id:
        try:
            await bot.delete_msg(message_id=msg_id)
        except Exception:
            pass
    try:
        await _reply_to(bot, event, result)
    except Exception:
        pass


# ---------------- 通知主人 / 分享到群 ----------------
def _pick_bot():
    from nonebot import get_bots

    bots = get_bots()
    if not bots:
        return None, ""
    want = env.bot_qq()
    if want and want in bots:
        return bots[want], want
    sid, bot = next(iter(bots.items()))
    return bot, str(sid)


async def _notify_owner(text: str) -> None:
    owner = env.owner_qq()
    bot, _sid = _pick_bot()
    if not (owner and bot):
        logger.warning(f"[B站] 想通知主人但没配置主人 QQ：{text}")
        return
    try:
        await bot.send_private_msg(user_id=int(owner), message=text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[B站] 私聊通知主人失败：{e}")


async def _share_to_groups(text: str, bvid: str) -> int:
    """分享到开了「B站刷到分享」的群；返回实际分享了几个群。
    每群每天最多 BILI_SHARE_DAILY_MAX 次（默认 2，0 = 不限），同一群两次分享至少隔 2 小时。"""
    groups = set(permissions.enabled_groups(SHARE_FEATURE))
    live = live_groups.cached()
    if live:
        groups &= live
    if not groups:
        return 0
    bot, sid = _pick_bot()
    if bot is None:
        return 0
    bind_bot(self_id=sid)
    from ..chat import humanize, memory  # 延迟导入：聊天插件里的上下文与限速

    today = time.strftime("%Y-%m-%d")
    cap = env.get_int("BILI_SHARE_DAILY_MAX", 2)
    shared = store.get_meta("shared") or {}
    n = 0
    for gid in sorted(groups):
        rec = shared.get(gid)
        rec = rec if isinstance(rec, dict) and rec.get("day") == today else {"day": today, "n": 0, "last": 0}
        if (cap > 0 and rec["n"] >= cap) or time.time() - float(rec.get("last") or 0) < 2 * 3600:
            continue
        hist = memory.get_history(int(gid))
        if not hist or time.time() - float(hist[-1].get("ts") or 0) > 6 * 3600:
            continue  # 群里大半天没人说话就不去自言自语
        msg = f"{text}\nhttps://www.bilibili.com/video/{bvid}"
        sender = humanize.guarded(lambda m, g=int(gid): bot.send_group_msg(group_id=g, message=m), int(gid))
        if await sender(Message(msg)) is None:
            continue
        memory.add_message(int(gid), "assistant", f"{text}（分享了一个B站视频）")
        shared[gid] = {"day": today, "n": rec["n"] + 1, "last": time.time()}
        n += 1
        daily_limits.track(SHARE_FEATURE, int(gid))
        logger.info(f"[B站] 分享到群{gid}：{text} {bvid}")
    store.set_meta("shared", shared)
    return n


def _life() -> str:
    try:
        from ..chat import huoren

        return huoren.life_note().replace("【你此刻的状态】", "")
    except Exception:
        return ""


# ---------------- 定时 ----------------
async def _tick() -> None:
    if _busy.locked():
        return
    ok, _why = surf.should_round()
    if ok:
        async with _busy:
            logger.info(f"[B站] {await surf.run_round(_notify_owner, _share_to_groups)}")
        return
    if surf.should_post_dynamic():
        async with _busy:
            logger.info(f"[B站] {await surf.post_dynamic(_life(), _notify_owner)}")


async def _reply_tick() -> None:
    """每分钟看一下是不是该看消息了（真正的间隔由 BILI_REPLY_CHECK_MINUTES 加随机抖动决定）。"""
    if _busy.locked():
        return
    ok, _why = replies.should_check()
    if not ok:
        return
    async with _busy:
        result = await replies.run_replies(_notify_owner)
    if result != "没有新回复":
        logger.info(f"[B站] {result}")


async def _panel_requests() -> None:
    """面板「B站生活」页上的按钮：写到 db 里，这里每 10 秒取一次。"""
    req = store.take_request()
    if not req:
        return
    action = req.get("action")
    logger.info(f"[B站] 面板请求：{action}")
    if action == "pause":
        store.halt("主人在面板上暂停", 24 * 365)
        store.log_activity("主人在面板上让我先别刷了", "end")
    elif action == "resume":
        store.resume()
        store.log_activity("恢复刷 B 站", "start")
    elif action == "logout":
        store.clear_auth()
        store.log_activity("退出了 B 站登录", "end")
    elif action == "trending":
        await _refresh_trending()
    elif action == "surf_life":
        # 聊天插件「她自己的时间」里她想刷会儿 B 站：照常受时段 / 每日轮数 / 间隔 / 暂停限制，只是不再掷骰子
        ok, why = surf.should_round(chance=1.0)
        if not ok or _busy.locked():
            logger.info(f"[B站] 她想刷会儿，但现在不行：{why or '正在刷'}")
            return
        _bg.add(t := asyncio.create_task(_run_life_round()))
        t.add_done_callback(_bg.discard)
    elif action in ("surf", "dynamic", "replies"):
        if not store.logged_in():
            store.log_activity("还没登录，刷不了", "error")
            return
        if _busy.locked():
            store.log_activity("正在刷呢，等这轮看完再说", "info")
            return
        # 放到后台跑，这样刷的过程中面板点「暂停」也能及时收到
        _bg.add(t := asyncio.create_task(_run_manual(action, str(req.get("keyword") or "")[:30])))
        t.add_done_callback(_bg.discard)


_bg: set = set()


async def _run_life_round() -> None:
    try:
        async with _busy:
            result = await surf.run_round(_notify_owner, _share_to_groups)
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=e).warning("[B站] 她自己想刷的那一轮出错")
        return
    logger.info(f"[B站] （她自己想刷）{result}")


async def _run_manual(action: str, keyword: str) -> None:
    try:
        async with _busy:
            if action == "surf":
                result = await surf.run_round(_notify_owner, _share_to_groups, manual=True, keyword=keyword)
            elif action == "replies":
                result = await replies.run_replies(_notify_owner, manual=True)
            else:
                result = await surf.post_dynamic(_life(), _notify_owner, manual=True)
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=e).warning("[B站] 面板触发的操作出错")
        result = f"出错了：{type(e).__name__}"
    logger.info(f"[B站] {result}")
    store.log_activity(result, "result")


async def _refresh_trending() -> None:
    if not env.get_bool("AI_CHAT_TRENDING", True):
        return
    try:
        async with Client(cookies=store.get_auth().get("cookies") or {}) as cli:
            await cli.ensure_buvid()
            words = await cli.trending()
    except Exception as e:  # noqa: BLE001  热搜拿不到无所谓
        logger.info(f"[B站] 热搜没取到：{e}")
        return
    if words:
        from ...services import slang

        slang.set_trending(words)
        logger.info(f"[B站] 更新热搜 {len(words)} 条")


@get_driver().on_startup
async def _start():
    scheduler.every(JOB_SURF, 300, _tick, first_delay=180)
    scheduler.every(JOB_PANEL, 10, _panel_requests, first_delay=15)
    scheduler.every(JOB_TREND, 3 * 3600, _refresh_trending, first_delay=60)
    scheduler.every(JOB_REPLY, 60, _reply_tick, first_delay=120, quiet=True)
    store.set_live("idle")


@get_driver().on_shutdown
async def _stop():
    for job in (JOB_SURF, JOB_PANEL, JOB_TREND, JOB_REPLY):
        await scheduler.stop(job)
