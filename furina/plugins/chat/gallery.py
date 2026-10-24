"""她自己的相册（ai-companion-kit 的 gallery 工具：存一张图，并写下「为什么留它」）。

- 群友发的图她真心喜欢 / 觉得好笑 / 想留着以后用，可以在回复末尾写 <<KEEP:为什么留它>>，图就存进 data/相册/；
- 很少用：大部分图看看就过去了；
- 存了什么、为什么存，会成为她记得的事：聊天时附上最近存的两张（「你相册里最近存的」）；
- 设置 → 活人感 →「自己的相册」可关（AI_CHAT_GALLERY）。
"""
from __future__ import annotations

import re
import time

from nonebot import logger

from . import config
from ...core import db, paths

NS = "gallery"
MARK = re.compile(r"<<\s*KEEP\s*[:：]\s*([^<>]{0,60})>>")
NOTE = ("【这张图你可以存进自己相册】如果你真心喜欢 / 觉得好笑 / 想以后拿来用，就在回复最后写 <<KEEP:为什么留它>>"
        "（十几个字，用你自己的话）；大部分图看看就过去了，不用存，也别在回复里说「我存了」。")


def extract(reply: str) -> tuple[str, str]:
    """从回复里剥出 <<KEEP:...>>：返回 (干净的回复, 留它的理由)。"""
    m = MARK.search(reply or "")
    if not m:
        return reply, ""
    return MARK.sub("", reply).strip(), m.group(1).strip() or "就是想留着"


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


def save(data: bytes, reason: str, who: str = "", group_id=None, now: float | None = None) -> dict:
    if not data:
        return {}
    ts = time.time() if now is None else now
    d = paths.DATA_DIR / "相册"
    d.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts)) + "." + _ext(data)
    try:
        (d / name).write_bytes(data)
    except OSError as e:
        logger.warning(f"[相册] 存图失败: {e}")
        return {}
    item = {"file": name, "why": reason[:60], "who": who[:20], "gid": str(group_id or ""), "ts": ts}
    db.update(NS, "index", lambda lst: (list(lst or []) + [item])[-300:])
    logger.info(f"[相册] 她存了一张 {who} 发的图：{reason}")
    return item


def recent(n: int = 2) -> list[dict]:
    return list(db.get(NS, "index") or [])[-n:]


def context_note() -> str:
    if not config.GALLERY:
        return ""
    items = recent(2)
    if not items:
        return ""
    lines = [f"- {time.strftime('%m月%d日', time.localtime(x['ts']))}存了张{x.get('who') or '群友'}发的图，因为「{x.get('why')}」"
             for x in items]
    return "【你相册里最近存的（你自己存的，记得）】\n" + "\n".join(lines) + "\n被问到或聊到才提。"
