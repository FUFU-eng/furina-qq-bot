"""增量读取日志：机器人 logs/bot.log（或 console.log）与当前 QQ 接入端的最新文件日志。

网页带着上次的读取位置来取新内容；文件变小（轮转）就从头读，首次只读末尾一段。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from furina.core import paths, qq_transport

from . import napcat

SOURCES = {"bot": paths.LOG_DIR / "bot.log", "console": paths.LOG_DIR / "console.log"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def transport_name() -> str:
    """Human-readable name of the currently selected QQ transport."""
    return "SnowLuma" if qq_transport.uses_snowluma() else "NapCat"


def _level_bot(line: str) -> str:
    if "ERROR" in line or "CRITICAL" in line or "Traceback" in line:
        return "error"
    if "WARNING" in line or "WARN" in line:
        return "warn"
    if "SUCCESS" in line:
        return "success"
    return "info"


def _level_napcat(line: str) -> str:
    low = line.lower()
    if "error" in low or "错误" in line or "失败" in line:
        return "error"
    if "warn" in low or "offline" in low or any(w in line for w in ("掉线", "失效", "下线")):
        return "warn"
    if "online" in low or "上线" in line or "success" in low:
        return "success"
    return "info"


def _read(path: Path, pos: int | None, head: int, max_bytes: int = 512 * 1024) -> tuple[list[str], int]:
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    if pos is None or pos < 0:
        pos = max(0, size - head)
    if size < pos:
        pos = 0
    if size - pos > max_bytes:
        pos = size - max_bytes
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read(size - pos)
    # 只消费到最后一个换行，半行留给下一次
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], pos
    chunk = data[:cut + 1]
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if pos > 0 and pos == max(0, size - head) and lines:
        lines = lines[1:]  # 首次从中间开始，第一行多半是半行
    return lines, pos + len(chunk)


def bot_lines(source: str, pos: int | None) -> dict:
    path = SOURCES.get(source, SOURCES["bot"])
    lines, new_pos = _read(path, pos, 24000)
    return {"pos": new_pos, "file": path.name, "exists": path.exists(),
            "lines": [{"t": _level_bot(x), "s": x} for x in lines if x.strip()]}


def latest_transport_log() -> Path | None:
    """Return the newest file log from the currently selected QQ transport."""
    d = napcat.log_dir()  # Compatibility service resolves SnowLuma vs NapCat dynamically.
    try:
        files = [d / x for x in os.listdir(d) if x.lower().endswith(".log")] if d else []
        return max(files, key=lambda p: p.stat().st_mtime) if files else None
    except OSError:
        return None


def latest_napcat_log() -> Path | None:
    """Backward-compatible name; the log directory follows the active transport."""
    return latest_transport_log()


def napcat_lines(name: str | None, pos: int | None) -> dict:
    path = latest_transport_log()
    if path is None:
        return {"file": None, "pos": 0, "lines": []}
    if name != path.name:
        pos = None  # 跨天/重启换了文件：只读尾部一小段
    lines, new_pos = _read(path, pos, 12000)
    out = []
    for raw in lines:
        s = _ANSI.sub("", raw).rstrip()
        if s.strip():
            out.append({"t": _level_napcat(s), "s": s})
    return {"file": path.name, "pos": new_pos, "lines": out}


def end_positions() -> dict:
    """Return tail positions for app and active QQ transport logs."""
    res = {k: (p.stat().st_size if p.exists() else 0) for k, p in SOURCES.items()}
    transport_log = latest_transport_log()
    # Keep the historical API key for existing panel clients; its path is the active transport.
    res["napcat"] = {"file": transport_log.name if transport_log else None,
                     "pos": transport_log.stat().st_size if transport_log else 0}
    res["transport_name"] = transport_name()
    return res
