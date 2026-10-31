"""全局UP图片订阅：明确@的管理命令 + 不依赖聊天心情的定时检查/推送。"""
from __future__ import annotations

import asyncio
import base64
import math
import re
import time

from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.rule import Rule

from ...core import env, live_groups, permissions, roles, scheduler, usage
from ...core.botref import bind
from ...services.bilibili import pictures as pics

FEATURE = pics.FEATURE
JOB = "B站画师图片订阅"
_job = None
_busy = False
_last_prune = 0.0
HELP = "给UP的UID或主页链接就能订，比如「订阅UP UID」\n查看：订阅名单 / 订阅名单 第2页\n取消：取消订阅 UID\n名单所有群共用，只追后续新图，不查历史作品"


def parse_command(text: str):
    text = (text or "").strip()
    match = re.fullmatch(r"(?:查看)?(?:订阅名单|画师名单|UP名单|我们订阅了谁|本群订阅了谁)(?:\s*(?:第\s*)?(\d+)\s*页?)?[。！？!?]?", text, re.I)
    if match:
        return "list", int(match[1] or 1)
    if re.fullmatch(r"(?:画师|UP)?订阅帮助|订阅UP|订阅画师|取消订阅", text, re.I):
        return "help", ""
    match = re.fullmatch(r"(?:取消订阅|退订|删除订阅)(?:UP|画师)?\s*(.+)", text, re.I)
    if match:
        return "remove", match[1].strip()
    match = re.fullmatch(r"(?:订阅(?:UP|画师)?|添加(?:UP|画师)(?:订阅)?)\s*(.+)", text, re.I)
    if match:
        return "add", match[1].strip()
    return None


def _text(event) -> str:
    return roles.strip_bot_at(event.get_plaintext(), str(event.self_id)).strip()


def _rule(event):
    if not isinstance(event, GroupMessageEvent) or str(event.user_id) == str(event.self_id):
        return False
    message = getattr(event, "original_message", None) or event.message
    direct = any(seg.type == "at" and str(seg.data.get("qq")) == str(event.self_id) for seg in message)
    if not direct and not roles.is_bot_at(event):
        return False
    if not permissions.get_group(event.group_id)["enabled"]:
        return False
    return parse_command(_text(event)) is not None


matcher = on_message(rule=Rule(_rule), priority=3, block=True)


async def handle_command(bot, event) -> None:
    bind(event)
    cmd = parse_command(_text(event))
    if cmd is None:
        return
    group = permissions.get_group(event.group_id)
    uid = str(event.user_id)
    blocked = uid in set(map(str, group["disabled_users"])) or (group["user_features"].get(uid) or {}).get(FEATURE) is False
    if blocked or not group["enabled"]:
        reply = "你这边暂时不能管理订阅名单"
    else:
        action, value = cmd
        try:
            if action == "help":
                reply = HELP
            elif action == "add":
                row = await pics.subscribe(value)
                reply = ("订好了：" if row["created"] else "已经订过了：") + row["name"] + "\n名单所有群共用，只追后续新图"
            elif action == "remove":
                reply = "已取消，所有群的名单一起更新了" if pics.unsubscribe(value) else "名单里没有这个UP"
            else:
                rows = pics.snapshot()["subscriptions"]
                pages = max(1, math.ceil(len(rows) / 10))
                page = max(1, min(pages, value))
                reply = f"全群共用的UP名单（{page}/{pages}页）\n" + ("\n".join(f'{r["name"]} · UID {r["uid"]}' for r in rows[(page - 1) * 10:page * 10]) if rows else "还没订UP，给我UID或主页链接就能加")
            if action == "add" and not permissions.allowed(event.group_id, FEATURE):
                reply += "\n本群接收关着，其他开启的群照常收"
        except ValueError as exc:
            reply = str(exc)
        except Exception as exc:
            logger.warning(f"[画师订阅] 管理操作失败：{type(exc).__name__}")
            reply = "这次没操作成功，稍后再试"
    await bot.send_group_msg(group_id=event.group_id, message=Message([MessageSegment.at(event.user_id), MessageSegment.text(" " + reply)]))


@matcher.handle()
async def _handle(bot: Bot, event: GroupMessageEvent):
    await handle_command(bot, event)
    await matcher.finish()


def _pick_bot():
    bots = get_bots()
    if not bots:
        return None
    return bots.get(str(env.bot_qq())) or next(iter(bots.values()))


async def tick() -> None:
    global _busy, _last_prune
    if _busy or not pics.settings()["enabled"]:
        return
    _busy = True
    try:
        bot = _pick_bot()
        if bot is None:
            return
        bind(self_id=bot.self_id)
        online = False
        try:
            status = await asyncio.wait_for(bot.get_status(), timeout=4)
            online = isinstance(status, dict) and status.get("online") is True
        except Exception:
            pass
        fresh = False
        try:
            rows = await asyncio.wait_for(bot.get_group_list(), timeout=4)
            if isinstance(rows, list):
                joined = {str(r["group_id"]) for r in rows if isinstance(r, dict) and r.get("group_id")}
                fresh = True
            else:
                joined = set()
        except Exception:
            joined = set()
        if not fresh or not online:
            # 离线也保留上次已知群的待发送记录，但绝不向没确认在线的QQ发图。
            joined |= live_groups.cached() | pics.registered_groups()
        groups = {g for g in joined if permissions.allowed(g, FEATURE)}
        pics.sync_groups(groups)
        await pics.scan_due()
        prepared = {}
        async def send(job, gid):
            if not permissions.allowed(gid, FEATURE) or not pics.is_current(job, gid):
                raise pics.DeliveryRejected("订阅或接收已关闭")
            if job["id"] not in prepared:
                msg = Message(MessageSegment.text(job["caption"]))
                for url in job["images"]:
                    data = await pics.load_image(url)
                    msg += MessageSegment.image("base64://" + base64.b64encode(data).decode("ascii"))
                prepared[job["id"]] = msg
            if not permissions.allowed(gid, FEATURE) or not pics.is_current(job, gid):
                raise pics.DeliveryRejected("下载期间订阅或接收已关闭")
            try:
                result = await asyncio.wait_for(bot.send_group_msg(group_id=int(gid), message=prepared[job["id"]]), timeout=90)
            except ActionFailed:
                raise pics.DeliveryRejected("QQ明确拒绝了这次发送") from None
            if isinstance(result, dict) and result.get("message_id") is not None:
                usage.track(FEATURE, gid)
                logger.info(f'[画师订阅] 已向群{gid}推送 {job["caption"]} 的 {len(job["images"])} 张图')
            return result
        await pics.dispatch(send, groups, online=online and fresh)
        if time.time() - _last_prune >= 3600:
            _last_prune = time.time()
            await asyncio.to_thread(pics.prune_cache)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"[画师订阅] 本轮暂时没完成：{type(exc).__name__}")
    finally:
        _busy = False


@get_driver().on_startup
async def _start():
    global _job
    if _job is None:
        _job = scheduler.every(JOB, 60, tick, first_delay=12)


@get_driver().on_shutdown
async def _stop():
    global _job
    if _job is not None:
        await scheduler.stop(JOB)
        _job = None
