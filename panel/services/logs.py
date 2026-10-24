"""增量读取日志：机器人 logs/bot.log（或 console.log）与 NapCat 最新的文件日志。

网页带着上次的读取位置来取新内容；文件变小（轮转）就从头读，首次只读末尾一段。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from furina.core import paths

from . import napcat

SOURCES = {"bot": paths.LOG_DIR / "bot.log", "console": paths.LOG_DIR / "console.log"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


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


def latest_napcat_log() -> Path | None:
    d = napcat.log_dir()
    try:
        files = [d / x for x in os.listdir(d) if x.lower().endswith(".log")] if d else []
        return max(files, key=lambda p: p.stat().st_mtime) if files else None
    except OSError:
        return None


def napcat_lines(name: str | None, pos: int | None) -> dict:
    path = latest_napcat_log()
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
    """「清空日志」：返回各文件当前末尾位置，之后只显示新内容。"""
    res = {k: (p.stat().st_size if p.exists() else 0) for k, p in SOURCES.items()}
    nap = latest_napcat_log()
    res["napcat"] = {"file": nap.name if nap else None, "pos": nap.stat().st_size if nap else 0}
    return res
