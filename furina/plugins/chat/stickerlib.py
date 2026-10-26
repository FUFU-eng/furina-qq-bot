"""表情包本子（机器人侧）：导入 QQ 收藏、看图补备注、记下群友刚发的表情包、按备注发。

存储在 furina/services/stickerbook.py（面板也能管理）。设置在「模式与内核 → 表情包」：
- 总开关 AI_CHAT_STICKERBOOK：关掉就回到旧的「发 QQ 收藏第 n 张」；
- 收藏群友表情 AI_CHAT_STICKER_COLLECT（每天上限 AI_CHAT_STICKER_COLLECT_DAILY_MAX）；
- 看图补备注 AI_CHAT_STICKER_AUTO_NOTE（每小时上限 AI_CHAT_STICKER_NOTE_PER_HOUR）；
- 导入 QQ 收藏 AI_CHAT_STICKER_IMPORT_FAV。
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import deque

from nonebot import logger

from ...core import brain, images
from ...core import llm as core_llm
from ...services import stickerbook

_SAVE_RE = re.compile(r"[<>]{1,2}\s*SAVE_STICKER\s*[:：]\s*([^<>]{0,30}?)\s*[<>]{1,2}", re.I)
# 群里最近的表情包 / 图片：gid -> deque[(ts, mid, uid, name, file, url, is_sticker)]
_recent: dict[int, deque] = {}
_note_times: deque = deque(maxlen=200)
_import_state = {"ts": 0.0, "running": False}
_tasks: set = set()


def enabled() -> bool:
    return brain.sticker_settings()["enabled"]


def _spawn(coro) -> None:
    t = asyncio.ensure_future(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# ---------------- 群里刚发的表情包 ----------------
def note_images(group_id, message, mid, uid, name) -> None:
    """记下这条消息里的图片引用（不下载）：sub_type=1 / 「[动画表情]」是表情包。"""
    try:
        dq = _recent.setdefault(int(group_id), deque(maxlen=30))
        for seg in message:
            if seg.type != "image":
                continue
            d = seg.data or {}
            sticker = str(d.get("sub_type", "")) == "1" or "表情" in str(d.get("summary") or "")
            dq.append((time.time(), str(mid or ""), str(uid), str(name or ""), str(d.get("file") or ""),
                       str(d.get("url") or ""), sticker))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[表情包] 记录图片失败: {e}")


def find_image(group_id, mid: str):
    mid = str(mid or "").lstrip("mM#")
    for item in reversed(_recent.get(int(group_id), ())):
        if item[1] == mid:
            return item
    return None


def recent_sticker(group_id, within: float = 180):
    now = time.time()
    for item in reversed(_recent.get(int(group_id), ())):
        if now - item[0] > within:
            break
        if item[6]:
            return item
    return None


# ---------------- 看图 / 收藏 ----------------
def _note_quota_ok() -> bool:
    cap = brain.sticker_settings()["note_hourly"]
    now = time.time()
    while _note_times and now - _note_times[0] > 3600:
        _note_times.popleft()
    return cap > 0 and len(_note_times) < cap


async def describe(data: bytes, sticker: bool = True) -> str:
    """看一眼这张图（表情包就给几个字的备注）。"""
    if not _note_quota_ok():
        return ""
    _note_times.append(time.time())
    try:
        url = await images.encode_data_url_async(data, max_side=512)
        if sticker:
            return stickerbook.clean_note(await core_llm.describe_image(
                url, max_len=16, prompt="这是一张聊天表情包。用不超过10个字概括它表达的情绪或用途"
                                        "（例如：无语翻白眼、得意叉腰、晚安、哭哭求抱），只输出这几个字。"))
        return await core_llm.describe_image(url, max_len=80)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[表情包] 看图失败: {e}")
        return ""


async def collect(bot, group_id, item, note: str = "", who: str = "") -> dict:
    """把群里的一张表情包收进本子。返回 {ok, note, reason}。"""
    st = brain.sticker_settings()
    if not (st["enabled"] and st["collect"]):
        return {"ok": False, "reason": "主人关掉了收藏表情包"}
    if st["collect_daily"] and stickerbook.collected_today() >= st["collect_daily"]:
        return {"ok": False, "reason": f"今天已经收藏了 {st['collect_daily']} 张，明天再收"}
    _ts, mid, uid, name, file_id, url, _is_sticker = item
    data = await images.fetch_image_bytes(bot, file_id, url)
    if not data:
        return {"ok": False, "reason": "图片拿不到了（可能过期）"}
    note = stickerbook.clean_note(note)
    if not note and st["auto_note"]:
        note = await describe(data)
    saved = await asyncio.to_thread(stickerbook.add, data, note, "collected", url, who or name, group_id)
    if not saved:
        return {"ok": False, "reason": "存不下"}
    logger.info(f"[表情包] 群{group_id} 收藏了 {name} 发的表情包，备注「{saved.get('note') or '（没备注）'}」")
    return {"ok": True, "id": saved["id"], "note": saved.get("note") or ""}


def extract_save(reply: str) -> tuple[str, str | None]:
    """一代仿真：回复里的 <<SAVE_STICKER:备注>> → (去掉标记的正文, 备注 或 None)。"""
    m = _SAVE_RE.search(reply or "")
    if not m:
        return reply, None
    return _SAVE_RE.sub("", reply).strip(), stickerbook.clean_note(m.group(1)) or ""


def save_hint(group_id) -> str:
    """一代仿真提示：群友刚发了表情包，喜欢可以收藏。"""
    st = brain.sticker_settings()
    if not (st["enabled"] and st["collect"]) or not recent_sticker(group_id, 120):
        return ""
    if st["collect_daily"] and stickerbook.collected_today() >= st["collect_daily"]:
        return ""
    return ("（群友刚发了张表情包。你要是真心喜欢、想以后自己也用，可以在回复末尾加 <<SAVE_STICKER:几个字的备注>> 收藏；"
            "一般不用收。）")


def spawn_save(bot, group_id, note: str) -> None:
    item = recent_sticker(group_id, 300)
    if item:
        _spawn(collect(bot, group_id, item, note))


# ---------------- 一代仿真：给模型的表情清单 ----------------
class StickerList(list):
    """和旧的 URL 列表兼容（元素仍是能直接发送的地址），多带一份备注给提示词用。"""
    notes: list


def prompt_stickers() -> StickerList | None:
    if not enabled():
        return None
    rows = stickerbook.prompt_list(brain.sticker_settings()["prompt_max"])
    if not rows:
        return None
    out = StickerList(stickerbook.uri_of(r) for r in rows)
    out.notes = [r.get("note") or "" for r in rows]
    return out


def mark_used_uri(uri: str) -> None:
    m = re.search(r"([0-9a-f]{16})\.(?:jpg|png|gif|webp)$", str(uri or ""))
    if m:
        stickerbook.mark_used(m.group(1))


# ---------------- 导入 QQ 收藏 + 补备注（后台，慢慢来） ----------------
async def import_favourites(bot, urls: list[str]) -> int:
    st = brain.sticker_settings()
    if not (st["enabled"] and st["import_fav"]) or _import_state["running"]:
        return 0
    _import_state["running"] = True
    added = 0
    try:
        for url in urls:
            if not url or stickerbook.has_origin(url):
                continue
            data = await images.fetch_image_bytes(bot, "", url)
            if data and await asyncio.to_thread(stickerbook.add, data, "", "fav", url):
                added += 1
            await asyncio.sleep(0.3)
    finally:
        _import_state["running"] = False
        _import_state["ts"] = time.time()
    if added:
        logger.info(f"[表情包] 从 QQ 收藏导入了 {added} 张")
    return added


async def fill_notes(limit: int = 4) -> int:
    """给没备注的表情补备注（看图模型，每小时有上限）。"""
    st = brain.sticker_settings()
    if not (st["enabled"] and st["auto_note"]):
        return 0
    n = 0
    for item in stickerbook.unnoted(limit):
        if not _note_quota_ok():
            break
        try:
            data = await asyncio.to_thread(stickerbook.path_of(item).read_bytes)
        except OSError:
            continue
        note = await describe(data)
        if note:
            stickerbook.set_note(item["id"], note)
            n += 1
    if n:
        logger.info(f"[表情包] 看图补了 {n} 张表情的备注")
    return n


async def maintain(bot) -> None:
    """定时：每 6 小时导入一次 QQ 收藏；每次补几张备注。"""
    if not enabled():
        return
    if time.time() - _import_state["ts"] > 6 * 3600:
        try:
            from . import stickers

            urls = await stickers.fetch_sticker_urls(bot)
            await import_favourites(bot, list(urls or []))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[表情包] 导入 QQ 收藏失败: {e}")
            _import_state["ts"] = time.time()
    await fill_notes()
