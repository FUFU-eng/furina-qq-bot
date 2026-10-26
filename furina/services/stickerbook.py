"""她的表情包本子：每张表情包都有一句备注，发的时候按备注挑。

- 来源：QQ 收藏表情（自动导入，没有备注的用看图模型补一句）、群友发的表情包她喜欢就收藏（自己写备注）、
  面板里手动改备注 / 删除；
- 图片存在 data/表情包/ 下（群里的图片链接会过期，收藏时就下载下来），索引在数据库 stickerbook 表；
- 发送：按备注关键词挑最贴的一张（同样合适时少用过的优先）；
- 不依赖 nonebot，面板也能 import。
"""
from __future__ import annotations

import hashlib
import random
import re
import time
from pathlib import Path

from ..core import db, paths

NS = "stickerbook"
MAX_ITEMS = 400
_WORD = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")


def folder() -> Path:
    d = paths.DATA_DIR / "表情包"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"GIF8":
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpg"


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


def items() -> list[dict]:
    """全部表情（新的在后）。"""
    rows = [v for v in (db.items(NS) or {}).values() if isinstance(v, dict) and v.get("id")]
    return sorted(rows, key=lambda r: float(r.get("ts") or 0))


def get(sid: str) -> dict | None:
    v = db.get(NS, str(sid))
    return v if isinstance(v, dict) else None


def path_of(item: dict) -> Path:
    return folder() / str(item.get("file") or "")


def uri_of(item: dict) -> str:
    p = path_of(item)
    return p.resolve().as_uri() if p.is_file() else str(item.get("url") or "")


def has_origin(url: str) -> bool:
    if not url:
        return False
    return any(r.get("origin") == url for r in items())


def add(data: bytes, note: str = "", source: str = "collected", origin: str = "", who: str = "",
        group_id=None, now: float | None = None) -> dict | None:
    """存一张；同一张图（内容相同）不重复存，只在原来没备注时补上备注。"""
    if not data or len(data) > 8 * 1024 * 1024:
        return None
    sid = digest(data)
    note = clean_note(note)
    old = get(sid)
    if old:
        if note and not old.get("note"):
            old["note"] = note
            db.put(NS, sid, old)
        return old
    if len(items()) >= MAX_ITEMS:
        _evict()
    name = f"{sid}.{_ext(data)}"
    try:
        (folder() / name).write_bytes(data)
    except OSError:
        return None
    item = {"id": sid, "file": name, "note": note, "source": source, "origin": origin[:500], "who": who[:20],
            "gid": str(group_id or ""), "ts": time.time() if now is None else now, "uses": 0, "last": 0}
    db.put(NS, sid, item)
    return item


def _evict() -> None:
    """满了：删一张最久没用、用得最少的（她自己收藏的优先保留）。"""
    rows = items()
    if not rows:
        return
    rows.sort(key=lambda r: (r.get("source") == "collected", int(r.get("uses") or 0), float(r.get("last") or r.get("ts") or 0)))
    remove(rows[0]["id"])


def clean_note(note: str) -> str:
    note = re.sub(r"\s+", " ", str(note or "")).strip(" 「」『』\"'“”：:，,。.")
    return note[:24]


def set_note(sid: str, note: str) -> bool:
    item = get(sid)
    if not item:
        return False
    item["note"] = clean_note(note)
    db.put(NS, sid, item)
    return True


def remove(sid: str) -> bool:
    item = get(sid)
    if not item:
        return False
    try:
        path_of(item).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(NS, sid)
    return True


def unnoted(limit: int = 10) -> list[dict]:
    return [r for r in items() if not r.get("note")][:limit]


def noted() -> list[dict]:
    return [r for r in items() if r.get("note")]


def mark_used(sid: str) -> None:
    def _u(cur):
        cur = dict(cur or {})
        if cur:
            cur["uses"] = int(cur.get("uses") or 0) + 1
            cur["last"] = time.time()
        return cur
    if get(sid):
        db.update(NS, sid, _u, default={})


def _words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def score(note: str, query: str) -> float:
    if not note or not query:
        return 0.0
    n, q = note.lower(), query.lower().strip()
    if q and q in n:
        return 10.0 + len(q)
    a, b = _words(n), _words(q)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(b)) * 5


def find(query: str, limit: int = 5) -> list[dict]:
    rows = noted()
    scored = [(score(r.get("note", ""), query), r) for r in rows]
    scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: (-x[0], int(x[1].get("uses") or 0), random.random()))
    return [r for _s, r in scored[:limit]]


def pick(query: str) -> dict | None:
    """按备注挑一张：最贴的里面随机一张（别老发同一张）。"""
    hits = find(query, 3)
    if not hits:
        return None
    best = score(hits[0].get("note", ""), query)
    top = [h for h in hits if score(h.get("note", ""), query) >= best * 0.8]
    return random.choice(top)


def prompt_list(limit: int = 40) -> list[dict]:
    """给模型看的清单：有备注的，常用的和新收的优先。"""
    rows = noted()
    rows.sort(key=lambda r: (-int(r.get("uses") or 0) * 0.3 - float(r.get("ts") or 0) / 1e9))
    return rows[:limit]


def collected_today(now: float | None = None) -> int:
    t = time.localtime(time.time() if now is None else now)
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return sum(1 for r in items() if r.get("source") == "collected" and float(r.get("ts") or 0) >= start)


def stats() -> dict:
    rows = items()
    return {"total": len(rows), "noted": sum(1 for r in rows if r.get("note")),
            "collected": sum(1 for r in rows if r.get("source") == "collected"),
            "fav": sum(1 for r in rows if r.get("source") == "fav")}
