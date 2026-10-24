"""主人私聊发图给机器人 → 自动强化，再私聊发回（不用艾特、不用说「强化」）。

只对主人（.env 的 AI_CHAT_GROUP_OWNER_QQ / RELAY_OWNER_QQ）生效；QQ 表情包不处理。
强化本身沿用群里的同一套：Real-ESRGAN ncnn-vulkan + 同一个强化队列（不会和群里的强化抢显卡）。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.rule import Rule

from ...core import env
from ...core.task_queue import enqueue_upscale
from .upscale import run_upscale

MAX_IMAGES = 30


def private_images(event) -> list[tuple[str, str]]:
    """私聊消息里要强化的图片 [(url, file)]；表情包（sub_type=1 / 「动画表情」）跳过。"""
    out: list[tuple[str, str]] = []
    for seg in getattr(event, "message", None) or []:
        if getattr(seg, "type", "") != "image":
            continue
        data = getattr(seg, "data", None) or {}
        if str(data.get("sub_type", "0")) == "1" or "表情" in str(data.get("summary", "") or ""):
            continue
        url, fid = str(data.get("url", "") or ""), str(data.get("file", "") or "")
        if url or fid:
            out.append((url, fid))
    return out


def is_owner_private_image(event) -> bool:
    if not isinstance(event, PrivateMessageEvent):
        return False
    owner = str(env.owner_qq() or "").strip()
    uid = str(event.user_id)
    if not owner or uid != owner or uid == str(getattr(event, "self_id", "")):
        return False
    return bool(private_images(event))


private_upscale = on_message(rule=Rule(is_owner_private_image), priority=5, block=True)


async def _say(bot: Bot, uid: int, message) -> bool:
    try:
        await bot.send_private_msg(user_id=uid, message=message)
        return True
    except ActionFailed as exc:
        # NapCat 偶发：图其实已经发出去了，但接口报错；不再重复发，免得刷屏
        logger.warning(f"[私聊强化] 发送返回异常（可能已发出）: {getattr(exc, 'message', exc)}")
        return False


@private_upscale.handle()
async def handle_private_upscale(bot: Bot, event: PrivateMessageEvent):
    from . import _ensure_sendable, _fetch_image_bytes, _first_frame_rgb, _make_image_segment_from_path

    uid = int(event.user_id)
    images = private_images(event)
    if len(images) > MAX_IMAGES:
        await _say(bot, uid, f"一次最多强化 {MAX_IMAGES} 张，这次发了 {len(images)} 张，分几次发吧~")
        return
    logger.info(f"[私聊强化] 主人私聊发来 {len(images)} 张图，开始强化")
    ticket = enqueue_upscale()
    temp: list[str] = []
    try:
        head = f"收到 {len(images)} 张图，" if len(images) > 1 else "收到，"
        if ticket.position > 1:
            await _say(bot, uid, f"⏳ {head}前面还有 {ticket.position - 1} 个强化在排队，轮到了马上开始~")
        else:
            await _say(bot, uid, f"🖼️ {head}芙芙这就帮你强化~")
        done = failed = 0
        async with ticket:
            for i, (url, fid) in enumerate(images, 1):
                try:
                    raw = await _fetch_image_bytes(bot, fid, url)
                    if not raw:
                        failed += 1
                        continue
                    fd, src = tempfile.mkstemp(prefix="upscale_pm_", suffix=".png")
                    with os.fdopen(fd, "wb") as f:
                        f.write(_first_frame_rgb(raw))
                    dst = os.path.splitext(src)[0] + "_out.png"
                    temp += [src, dst]
                    t0 = time.time()
                    await run_upscale(src, dst)
                    send = _ensure_sendable(dst)
                    if send != dst:
                        temp.append(send)
                    logger.info(f"[私聊强化] 第 {i}/{len(images)} 张完成，耗时 {time.time() - t0:.1f}s")
                    await _say(bot, uid, Message([_make_image_segment_from_path(send)]))
                    done += 1
                except asyncio.TimeoutError:
                    logger.error(f"[私聊强化] 第 {i} 张超时")
                    failed += 1
                except Exception as exc:
                    logger.error(f"[私聊强化] 第 {i} 张失败: {exc}")
                    failed += 1
        if not done:
            await _say(bot, uid, "❌ 强化失败了，图可能太大或者没下载下来，换一张再试试~")
        elif failed:
            await _say(bot, uid, f"✨ 强化好了 {done} 张，失败 {failed} 张")
        elif len(images) > 1:
            await _say(bot, uid, f"✨ {done} 张都强化好啦")
    finally:
        ticket.cancel()
        for p in temp:
            try:
                if p and os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass
