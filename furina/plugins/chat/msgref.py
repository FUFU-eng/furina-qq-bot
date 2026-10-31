"""群消息编号 → 原文：群友引用（回复）一条消息时，让她知道引用的是哪句话。

QQ 的引用只带一个消息编号。她平时只看得到「上次醒来之后的新消息」，引用的要是更早的、或者机器人重启 /
QQ 重新登录之前的消息，她只能看到编号、不知道原文，就会说「你引的那条我没刷到」。NapCat 的 get_msg
在刚重新登录后经常查不到旧消息（日志里「获取不到带记录的引用消息」），所以这里自己记一份：

- 每群最近 KEEP 条（群友的消息 + 她自己发的），每条截短到 TEXT_CHARS 字；
- 存进 SQLite（最多每 SAVE_EVERY 秒写一次），机器人重启后还在；
- 查不到时再问一次 NapCat（get_msg）。
"""
from __future__ import annotations

import threading
import time

from ...core import db

NS = "msgref"
KEEP = 400          # 每群记多少条
TEXT_CHARS = 80     # 每条存多少字
SHOW_CHARS = 50     # 给她看时截多少字
SAVE_EVERY = 15     # 秒

_lock = threading.Lock()
_data: dict[str, dict[str, list]] = {}   # gid -> {mid: [name, text, ts]}
_loaded: set[str] = set()
_dirty: set[str] = set()
_last_save = [0.0]


def _group(gid) -> dict[str, list]:
    g = str(gid)
    if g not in _loaded:
        _loaded.add(g)
        try:
            saved = db.get(NS, g)
        except Exception:  # noqa: BLE001
            saved = None
        box = _data.setdefault(g, {})
        if isinstance(saved, dict):
            for k, v in saved.items():
                if isinstance(v, list) and len(v) >= 2:
                    box.setdefault(str(k), v)
    return _data.setdefault(g, {})


def add(gid, mid, name: str, text: str, ts: float | None = None) -> None:
    mid = str(mid or "").strip()
    text = (text or "").replace("\n", " ").strip()
    if not mid or not text:
        return
    with _lock:
        box = _group(gid)
        box[mid] = [str(name or "群友")[:24], text[:TEXT_CHARS], float(ts or time.time())]
        if len(box) > KEEP:
            for k in sorted(box, key=lambda k: box[k][2])[:len(box) - KEEP]:
                box.pop(k, None)
        _dirty.add(str(gid))
    if time.time() - _last_save[0] >= SAVE_EVERY:
        save()


def save() -> None:
    with _lock:
        _last_save[0] = time.time()
        todo = {g: dict(_data.get(g) or {}) for g in _dirty}
        _dirty.clear()
    for g, box in todo.items():
        try:
            db.put(NS, g, box)
        except Exception:  # noqa: BLE001
            pass


def lookup(gid, mid) -> str:
    """返回「名字：原文」，查不到返回空串。"""
    mid = str(mid or "").strip()
    if not mid:
        return ""
    with _lock:
        v = _group(gid).get(mid)
    return f"{v[0]}：{v[1][:SHOW_CHARS]}" if v else ""


async def resolve(bot, gid, mid, event=None) -> str:
    """先查自己记的，再看事件里适配器带的引用，最后问 NapCat。都查不到返回空串。"""
    got = lookup(gid, mid)
    if got:
        return got
    rep = getattr(event, "reply", None) if event is not None else None
    if rep is not None and str(getattr(rep, "message_id", "") or "") == str(mid):
        try:
            s = getattr(rep, "sender", None)
            name = getattr(s, "card", "") or getattr(s, "nickname", "") or "群友"
            text = rep.message.extract_plain_text().strip() or "[图片/表情]"
            add(gid, mid, name, text, getattr(rep, "time", None))
            return lookup(gid, mid)
        except Exception:  # noqa: BLE001
            pass
    if bot is None:
        return ""
    try:
        import asyncio

        msg = await asyncio.wait_for(bot.get_msg(message_id=int(mid)), timeout=4)
        sender = (msg or {}).get("sender") or {}
        name = sender.get("card") or sender.get("nickname") or "群友"
        raw = msg.get("message")
        if isinstance(raw, list):
            text = "".join(str((seg.get("data") or {}).get("text") or "") for seg in raw
                           if isinstance(seg, dict) and seg.get("type") == "text").strip()
        else:
            text = str(msg.get("raw_message") or raw or "").strip()
        add(gid, mid, name, text or "[图片/表情]", msg.get("time"))
        return lookup(gid, mid)
    except Exception:  # noqa: BLE001
        return ""
