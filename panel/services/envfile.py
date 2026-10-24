"""读写 v2 的 .env：保留注释与顺序，原子替换（机器人会随时热读取）。只处理键名=值的行。"""
from __future__ import annotations

import os
import tempfile
import threading
import time

from furina.core import paths

_lock = threading.Lock()
_cache: dict = {"stamp": None, "values": {}}


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def values() -> dict[str, str]:
    """按 mtime 缓存的全量键值（面板逐行计算上限时不反复读盘）。"""
    try:
        st = paths.ENV_PATH.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    with _lock:
        if _cache["stamp"] != stamp:
            try:
                _cache["values"] = _parse(paths.ENV_PATH.read_text(encoding="utf-8"))
                _cache["stamp"] = stamp
            except OSError:
                return dict(_cache["values"])
        return dict(_cache["values"])


def get(key: str, default: str = "") -> str:
    v = values().get(key, "")
    return v if v.strip() else default


def _atomic_write(path, text: str) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        for _ in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def set_values(updates: dict[str, str]) -> None:
    """已有键原地替换，没有的追加到末尾。值里不允许换行。"""
    for k, v in updates.items():
        if "\n" in str(v) or "\r" in str(v) or not k or "=" in k:
            raise ValueError(f"无效的配置项：{k}")
    with _lock:
        try:
            text = paths.ENV_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        nl = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        remaining = {k: str(v) for k, v in updates.items()}
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("#") or "=" not in s:
                continue
            key = s.split("=", 1)[0].strip()
            if key in remaining:
                lines[i] = f"{key}={remaining.pop(key)}"
        for k, v in remaining.items():
            lines.append(f"{k}={v}")
        _atomic_write(paths.ENV_PATH, nl.join(lines) + nl)
        _cache["stamp"] = None
