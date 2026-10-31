"""做文件（参考大肥鱼的 maker）：群友让她「写一份 / 整理一份 / 做个清单」，她认真想一遍，写成文件当 QQ 群文件发出来。

用 DeepSeek 开思考写（AI_CHAT_MAKER_EFFORT，默认 high），写成 Markdown / 纯文本放在 data/maker/，
每群每天最多 AI_CHAT_MAKER_DAILY 份（默认 0 = 不限，可在面板「设置 → 认真做文件」里改）。功能权限 maker 默认开。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from ..core import db, env, llm, paths

NS = "maker"
EXT = {"md": ".md", "txt": ".txt", "csv": ".csv", "html": ".html", "py": ".py", "json": ".json"}

SYSTEM = ("你在帮群友认真做一份文件。直接输出文件正文，不要寒暄、不要解释你要怎么写、不要用代码块包住整个文件。"
          "内容要实在、结构清楚、可以直接拿去用；不确定的地方标注「待确认」，不要编造数据和来源。")


def daily_cap() -> int:
    return max(0, env.get_int("AI_CHAT_MAKER_DAILY", 0))


def used_today(group_id) -> int:
    v = db.get(NS, f"count:{time.strftime('%Y-%m-%d')}:{group_id}")
    return int(v or 0)


def _bump(group_id) -> None:
    db.update(NS, f"count:{time.strftime('%Y-%m-%d')}:{group_id}", lambda v: int(v or 0) + 1, default=0)


def safe_name(title: str, fmt: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", (title or "").strip())[:40] or "文件"
    return base + EXT.get(fmt, ".md")


def out_dir() -> Path:
    d = Path(paths.ROOT) / "data" / "maker"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _strip_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```[a-zA-Z0-9]*\n(.*)\n```$", t, re.S)
    return m.group(1) if m else t


async def make(group_id, title: str, request: str, fmt: str = "md") -> dict:
    """返回 {"ok", "path", "name", "chars"} 或 {"ok": False, "reason"}。"""
    cap = daily_cap()
    if cap and used_today(group_id) >= cap:
        return {"ok": False, "reason": f"今天这个群已经做了 {cap} 份文件了，明天再说"}
    req = (request or "").strip()[:2000]
    if len(req) < 4:
        return {"ok": False, "reason": "要做什么还没说清楚"}
    fmt = fmt if fmt in EXT else "md"
    effort = env.get("AI_CHAT_MAKER_EFFORT", "high").strip().lower() or "high"
    effort = effort if effort in ("off", "low", "high", "max") else "high"
    try:
        text = await llm.chat([{"role": "system", "content": SYSTEM + f"（输出格式：{fmt}）"},
                               {"role": "user", "content": f"文件名：{title}\n要求：{req}"}],
                              max_tokens=6000, temperature=0.4, effort=None if effort == "off" else effort,
                              timeout=240)
    except llm.LLMError as e:
        return {"ok": False, "reason": f"没写出来：{str(e)[:80]}"}
    body = _strip_fence(text or "")
    if len(body) < 20:
        return {"ok": False, "reason": "没写出像样的内容"}
    name = safe_name(title, fmt)
    path = out_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
    path.write_text(body, encoding="utf-8")
    _bump(group_id)
    return {"ok": True, "path": str(path), "name": name, "chars": len(body)}
