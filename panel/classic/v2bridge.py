"""经典面板 ↔ v2 数据层的桥。

经典面板沿用旧版的「按文件名读写 JSON」写法；这里把那些旧文件名（带 V2DOC 前缀的虚拟路径）
映射到 v2 的 SQLite（与机器人同一个库，机器人热读取），路径、进程、NapCat 相关也都走 v2 的服务模块。
"""
from __future__ import annotations

import datetime as _dt
import os

from furina.core import db, docfile, paths

from ..services import assets, control, data, napcat

V2DOC = "v2doc:"

ROOT_DIR = str(paths.ROOT)
CONSOLE_LOG = str(control.CONSOLE_LOG)
BOT_LOG = str(paths.LOG_DIR / "bot.log")
PYTHON_EXE = control.python_exe()
NAPCAT_DIR = str(napcat.napcat_dir() or "")
PERSONA_PATH = str(paths.ROOT / "furina" / "plugins" / "chat" / "persona.py")
CUSTOM_BG_DIR = str(paths.DATA_DIR / "wallpapers")


def asset(*parts: str) -> str:
    """素材路径：优先 ASSETS_ROOT（v2 的 assets 目录），找不到再看项目根目录。"""
    for base in (assets.root(), paths.ROOT):
        p = os.path.join(str(base), *parts)
        if os.path.exists(p):
            return p
    return os.path.join(str(assets.root()), *parts)


def log_path(name: str) -> str:
    os.makedirs(paths.LOG_DIR, exist_ok=True)
    return str(paths.LOG_DIR / name)


# ---------------------------------------------------------------- 虚拟文档
_PERSONA = {"roles.json": "roles", "bot_qq_profiles.json": "profiles", "shared_lore.json": "shared_lore"}
_PANEL = {"wallpaper_library.json": "wallpapers", "custom_background.json": "custom_background"}
_FILES = {"group_members.json", "fengshen_data.json"}


def _name(path) -> str:
    s = str(path)
    return s[len(V2DOC):] if s.startswith(V2DOC) else os.path.basename(s)


def _daily_limits() -> dict:
    """按旧 daily_limits.json 的结构从 usage 表拼出来：今日每人次数、今日各群功能统计、近 40 天历史。"""
    today = _dt.date.today()
    since = (today - _dt.timedelta(days=39)).isoformat()
    chat: dict[str, int] = {}
    by_day: dict[str, dict[str, dict[str, int]]] = {}
    for day, kind, gid, uid, count in db.usage_rows(since):
        n = int(count or 0)
        if kind == "chat" and day == today.isoformat() and uid:
            key = f"{gid}:{uid}"
            chat[key] = chat.get(key, 0) + n
        elif kind.startswith("stat:"):
            g = by_day.setdefault(day, {}).setdefault(str(gid), {})
            g[kind[5:]] = g.get(kind[5:], 0) + n
    t = today.isoformat()
    history = [{"date": d, "stats": st} for d, st in sorted(by_day.items()) if d != t]
    return {"date": t, "chat": chat, "stats": by_day.get(t, {}), "history": history}


def read_doc(path):
    name = _name(path)
    if name == "daily_limits.json":
        return _daily_limits()
    if name == "feature_permissions.json":
        return {"groups": {g: v for g, v in db.items("perm").items() if isinstance(v, dict)}}
    if name in _PERSONA:
        return db.get("persona", _PERSONA[name])
    if name in _PANEL:
        return db.get("panel", _PANEL[name])
    if name in _FILES:
        return docfile.load(name)
    return docfile.load(name)


def write_doc(path, value) -> None:
    name = _name(path)
    if name == "daily_limits.json":
        raise RuntimeError("每日次数由机器人记录，面板不直接改写")
    if name == "feature_permissions.json":
        groups = (value or {}).get("groups") or {}
        db.put_many("perm", {str(g): v for g, v in groups.items() if isinstance(v, dict)})
        return
    if name in _PERSONA:
        db.put("persona", _PERSONA[name], value)
        return
    if name in _PANEL:
        db.put("panel", _PANEL[name], value)
        return
    docfile.save(name, value)


def delete_doc(path) -> None:
    name = _name(path)
    if name in _PANEL:
        db.delete("panel", _PANEL[name])


# ---------------------------------------------------------------- 业务
def baseline_miyoushe(gid) -> int:
    return data.baseline_miyoushe(str(gid))


def favor_line(uid) -> str:
    try:
        return data.favor_line(str(uid))
    except Exception:
        return ""


def model_usage_today() -> dict:
    return data.model_usage_today()


def refresh_groups() -> dict:
    return napcat.groups.refresh_sync()


# ---------------------------------------------------------------- 进程 / NapCat
def bot_pids() -> list[int]:
    return sorted({pid for pid, _ in control.find_bot_processes()})


def bot_port() -> int:
    return control.bot_port()


def napcat_running() -> bool:
    return napcat.is_running()


def start_qq_transport(qq=None):
    return napcat.start(qq)


def stop_napcat() -> None:
    napcat.stop()


def webui_url() -> str:
    return napcat.webui_url()


def webui_port() -> int:
    return napcat.webui_port()


def ensure_napcat_config(qq) -> bool:
    return napcat.ensure_onebot_config(str(qq))
