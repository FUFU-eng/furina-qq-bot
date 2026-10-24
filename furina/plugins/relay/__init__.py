"""群主发图自动转发插件：主群（源群）里群主发「1」开启后，他发的任何图片原样转发到分群（目标群）。

触发条件：
- 事件来自源群（RELAY_SOURCE_GROUP）
- 发消息的人是群主（RELAY_OWNER_QQ）
- 群主已在源群发过开启词「1」（单独发「.」、发停止词或超时后关闭）
- 消息里包含图片段（纯文字/视频不触发）
转发方式：取图片原始字节（优先 NapCat 本地缓存，其次 URL 下载），
以图片形式原样发到目标群，不压缩、不改图；全部发完后用视觉模型看图，
在目标群补一句简短评价。
"""

import asyncio
import io
import os
import re
import tempfile
import time
from pathlib import Path

import httpx
from nonebot import get_bot, logger, on_message
from ...core.botref import bot_for
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.rule import Rule
from PIL import Image

from . import config
from ...core import images as ai_vision, llm as ai_llm, roles

# 发送锁：避免群主连续发图时并发发送触发频率限制
_send_lock = asyncio.Lock()
# 短时去重：同一图片文件（file_id）60 秒内只转发一次，防 NapCat 重连重复投递
_recent_files = {}

# ==== 转发"开关"状态 ====
# 群主发"1"= 开启本轮；之后他发的图才转发；
# 超时未继续发图或群主说停止词 = 自动关闭本轮。
_armed = False
_last_activity_at = 0.0
# 后台任务保活（评价/超时看门狗）；asyncio 只弱引用任务，不保存引用可能被中途回收
_bg_tasks: set = set()
_timeout_task: asyncio.Task | None = None
# 结束评价只看最后 3 张，本轮图片字节最多留这么多（旧版整轮全部保留，发几十张大图会占用大量内存）
_ROUND_KEEP = 3


def _spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def _timeout_watch():
    """本轮开启后按 RELAY_ARM_TIMEOUT 主动收尾。

    旧版只在群主“下一次发图”时才检查超时：群主发完图不说停止词就离开时，本轮不会结束，
    评价要等到几小时甚至几天后群主再发图时才突然冒出来，而那张新图反而不会被转发。
    """
    global _armed
    try:
        while _armed:
            remaining = config.RELAY_ARM_TIMEOUT - (time.time() - _last_activity_at)
            if remaining <= 0:
                _armed = False
                logger.info("[转发] 距上次发图超过 {}s 未继续，本轮自动停止".format(config.RELAY_ARM_TIMEOUT))
                if _round_images:
                    # 单独起任务发评价，看门狗立即结束，下一轮开启时能马上起新的看门狗
                    _spawn(_send_round_comment(bot=None))
                return
            await asyncio.sleep(remaining + 0.5)
    except asyncio.CancelledError:
        pass


def _ensure_timeout_watch():
    global _timeout_task
    if _timeout_task is None or _timeout_task.done():
        _timeout_task = _spawn(_timeout_watch())


def _stop_timeout_watch():
    global _timeout_task
    if _timeout_task is not None and not _timeout_task.done():
        _timeout_task.cancel()
    _timeout_task = None


def _is_stop_word(text: str) -> bool:
    """整句就是停止词（可带“了/啦/咯”和标点）才算。

    旧版按子串匹配：转发过程中群主随口一句“游戏结束了”“停止内卷”也会把本轮结束掉。
    """
    words = [w for w in config.RELAY_STOP_TRIGGERS if w]
    if not words:
        return False
    pattern = "(?:" + "|".join(re.escape(w) for w in words) + r")[了啦咯喽呀啊哦~～!！。.\s]*"
    return re.fullmatch(pattern, (text or "").strip()) is not None


def _iter_segments(msg):
    """统一遍历消息段：兼容 Message 对象、列表、dict 和纯文本。"""
    if msg is None:
        return
    if isinstance(msg, str):
        yield ("text", {"text": msg})
        return
    for seg in msg:
        if isinstance(seg, dict):
            yield (seg.get("type", ""), seg.get("data", {}) or {})
        else:
            yield (getattr(seg, "type", ""), getattr(seg, "data", {}) or {})


def _is_recent_duplicate(file_id: str) -> bool:
    if not file_id:
        return False
    now = time.time()
    if file_id in _recent_files and now - _recent_files[file_id] < 60:
        return True
    _recent_files[file_id] = now
    if len(_recent_files) > 500:
        _recent_files.pop(next(iter(_recent_files)))
    return False


def _extract_text(msg) -> str:
    """提取消息内的纯文本内容。"""
    parts = []
    for stype, sdata in _iter_segments(msg):
        if stype == "text":
            parts.append(str(sdata.get("text", "") or ""))
    return " ".join(parts)


def _is_owner_event(event: GroupMessageEvent) -> bool:
    """触发条件：启用 + 源群 + 群主（文字或图片消息都触发，用于识别开启词「1」）。"""
    if not config.RELAY_ENABLED:
        return False
    if event.group_id != config.RELAY_SOURCE_GROUP:
        return False
    if str(event.user_id) != config.RELAY_OWNER_QQ:
        return False
    return True


def _is_auto_user_event(event: GroupMessageEvent) -> bool:
    """触发条件：启用 + 源群 + 在自动转发群友列表中（只要发图就转发，不需要开关）。"""
    if not config.RELAY_ENABLED:
        return False
    if event.group_id != config.RELAY_SOURCE_GROUP:
        return False
    if not config.RELAY_AUTO_USERS:
        return False
    if str(event.user_id) not in config.RELAY_AUTO_USERS:
        return False
    return True


relay_matcher = on_message(rule=Rule(_is_owner_event), priority=20, block=False)
auto_relay_matcher = on_message(rule=Rule(_is_auto_user_event), priority=21, block=False)


async def _fetch_image_bytes(bot, file_id: str, url: str) -> bytes | None:
    """取图片原始字节：优先 NapCat 本地缓存，失败再 httpx 下载。"""
    if file_id:
        try:
            info = await bot.get_image(file=file_id)
            if isinstance(info, dict):
                local_path = info.get("file", "") or ""
            else:
                local_path = getattr(info, "file", "") or ""
            if local_path and os.path.exists(local_path):
                with open(local_path, "rb") as f:
                    data = f.read()
                if data:
                    return data
        except Exception as e:
            logger.debug(f"[转发] 本地缓存获取失败: {e}")
    if not url:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and resp.content and len(resp.content) > 100:
                return resp.content
    except Exception as e:
        logger.debug(f"[转发] 图片下载失败: {e}")
    return None


def _guess_suffix(url: str, data: bytes) -> str:
    """根据 URL 和内容猜测后缀，GIF 必须保真才能保持动图。"""
    low = (url or "").lower()
    if ".gif" in low:
        return ".gif"
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "jpg").lower()
            return ".gif" if fmt == "gif" else (".png" if fmt == "png" else ".jpg")
    except Exception:
        return ".jpg"


def _to_image_segment(data: bytes, url: str) -> tuple[MessageSegment, str]:
    """图片字节写入临时文件，返回图片消息段和该任务自己的临时文件路径。"""
    suffix = _guess_suffix(url, data)
    fd, tmp = tempfile.mkstemp(prefix="relay_img_", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return MessageSegment.image(Path(tmp).as_uri()), tmp


async def _make_comment(images: list[bytes]) -> str:
    """用视觉模型看刚转发的图，生成一句简短评价。失败返回空串。"""
    parts = await ai_vision.image_parts(images[-3:])
    if not parts:
        return ""
    parts.append({
        "type": "text",
        "text": (
            "上面是刚转发到群里的图，请用一句简短、自然、口语化的话评价画面内容，"
            "30字以内，不要复述\"转发\"这件事，直接输出，不要任何前缀、引号或表情包标记。"
        ),
    })
    messages = [
        {
            "role": "system",
            "content": roles.flavor(
                "你是「芙宁娜」，一个爱面子、嘴硬心软、喜欢甜点和舞台的女生，说话自然活泼，自称常用「芙芙」「本导演」「本大明星」。",
                "你是「{name}」，说话自然活泼，保持你平时的语气和自称。",
            ),
        },
        {"role": "user", "content": parts},
    ]
    try:
        text = await ai_llm.chat(messages)
        text = (text or "").strip().strip("。").strip()
        if len(text) < 2:
            return ""
        return text[:60]
    except Exception as e:
        logger.warning(f"[转发] 评价生成失败: {e}")
        return ""


@relay_matcher.handle()
async def _handle_owner(event: GroupMessageEvent):
    global _armed, _last_activity_at
    text = _extract_text(event.message)
    images = [sdata for stype, sdata in _iter_segments(event.message) if stype == "image"]

    # 1) 群主发"1"：开启本轮图片转发
    if config.RELAY_ARM_TRIGGER and text.strip() == config.RELAY_ARM_TRIGGER:
        _armed = True
        _last_activity_at = time.time()
        _round_images.clear()
        _ensure_timeout_watch()
        logger.info(f"[转发] 群主发了「{config.RELAY_ARM_TRIGGER}」，开启本轮图片转发（超时 {config.RELAY_ARM_TIMEOUT}s 自动停）")
        return

    # 2) 群主单独发「.」（发完本轮）或说停止词：立即结束本轮
    stripped = text.strip()
    end_now = stripped in (".", "。") or _is_stop_word(stripped)
    if end_now:
        was_armed = _armed
        _armed = False
        _stop_timeout_watch()
        reason = "群主发「.」" if stripped in (".", "。") else "群主说停止词"
        logger.info(f"[转发] {reason}，本轮图片转发结束")
        # 本轮所有图都已转发完毕，统一评价一句后清空
        if was_armed and _round_images:
            _spawn(_send_round_comment(bot=None))
        return

    if not images:
        return

    # 3) 超时未继续发图：自动结束本轮
    if _armed and (time.time() - _last_activity_at) > config.RELAY_ARM_TIMEOUT:
        _armed = False
        logger.info("[转发] 距上次发图超过 {}s 未继续，本轮自动停止".format(config.RELAY_ARM_TIMEOUT))
        # 超时结束也补一句评价（本轮有图才发）
        if _round_images:
            _spawn(_send_round_comment(bot=None))

    # 4) 只有"开启后"的连续发图才转发
    if not _armed:
        return
    _last_activity_at = time.time()
    _ensure_timeout_watch()

    bot = bot_for(event)
    segments = []
    pending = []
    for sdata in images:
        file_id = str(sdata.get("file", "") or "")
        url = str(sdata.get("url", "") or "")
        if _is_recent_duplicate(file_id):
            continue
        data = await _fetch_image_bytes(bot, file_id, url)
        if not data:
            logger.warning(f"[转发] 图片读取失败，跳过: {url or file_id}")
            continue
        seg, tmp = _to_image_segment(data, url)
        segments.append(seg)
        pending.append(tmp)
        _round_images.append(data)
        del _round_images[:-_ROUND_KEEP]

    if not segments:
        return

    logger.info(
        f"[转发] 群 {config.RELAY_SOURCE_GROUP} 群主发图（本轮已开启），转发 {len(segments)} 张到群 {config.RELAY_TARGET_GROUP}"
    )
    async with _send_lock:
        try:
            for i in range(0, len(segments), 9):
                await bot.send_group_msg(
                    group_id=config.RELAY_TARGET_GROUP,
                    message=Message(segments[i : i + 9]),
                )
                if i + 9 < len(segments):
                    await asyncio.sleep(1.0)
        except ActionFailed as e:
            logger.error(
                f"[转发] 发送失败: retcode={getattr(e, 'retcode', '?')} message={getattr(e, 'message', e)}"
            )
        finally:
            # 只清理本任务创建的临时文件，避免并发转发时误删其他任务的图
            for p in pending:
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass

async def _send_round_comment(bot=None):
    """本轮全部转发完成后，只评价一句。"""
    global _round_images
    images = list(_round_images)
    _round_images.clear()
    if not images:
        return
    if bot is None:
        try:
            bot = get_bot()
        except Exception:
            return
    try:
        comment = await _make_comment(images)
        if comment:
            await bot.send_group_msg(
                group_id=config.RELAY_TARGET_GROUP,
                message=Message([MessageSegment.text(comment)]),
            )
            logger.info(f"[转发] 已发送本轮转发后评价: {comment[:50]}")
    except Exception as e:
        logger.warning(f"[转发] 转发后评价发送失败: {e}")
# 本轮已转发的图片字节（用于结束时统一评价一句）
_round_images: list[bytes] = []


@auto_relay_matcher.handle()
async def _handle_auto_user(event: GroupMessageEvent):
    """自动转发群友发图：不需要开关，只要列表中的群友在源群发图就直接转到目标群。"""
    images = [sdata for stype, sdata in _iter_segments(event.message) if stype == "image"]
    if not images:
        return

    bot = bot_for(event)
    segments = []
    pending = []
    for sdata in images:
        file_id = str(sdata.get("file", "") or "")
        url = str(sdata.get("url", "") or "")
        if _is_recent_duplicate(file_id):
            continue
        data = await _fetch_image_bytes(bot, file_id, url)
        if not data:
            logger.warning(f"[自动转发] 图片读取失败，跳过: {url or file_id}")
            continue
        seg, tmp = _to_image_segment(data, url)
        segments.append(seg)
        pending.append(tmp)

    if not segments:
        return

    logger.info(
        f"[自动转发] 群 {config.RELAY_SOURCE_GROUP} 用户 {event.user_id} 发图，转发 {len(segments)} 张到群 {config.RELAY_TARGET_GROUP}"
    )
    async with _send_lock:
        try:
            for i in range(0, len(segments), 9):
                await bot.send_group_msg(
                    group_id=config.RELAY_TARGET_GROUP,
                    message=Message(segments[i : i + 9]),
                )
                if i + 9 < len(segments):
                    await asyncio.sleep(1.0)
        except ActionFailed as e:
            logger.error(
                f"[自动转发] 发送失败: retcode={getattr(e, 'retcode', '?')} message={getattr(e, 'message', e)}"
            )
        finally:
            for p in pending:
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass
