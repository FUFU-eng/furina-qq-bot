"""热读取 .env：文件一改，下一次读取就生效（沿用旧项目的全部键名）。

读取顺序：.env 最新值（非空） > 进程环境变量 > NoneBot driver.config > 默认值。
"""
from __future__ import annotations

import os
import threading

from dotenv import dotenv_values

from . import paths

_lock = threading.Lock()
_state: dict = {"stamp": None, "values": {}, "version": 0}


def _refresh() -> None:
    try:
        st = paths.ENV_PATH.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return
    if stamp == _state["stamp"]:
        return
    try:
        values = {k: ("" if v is None else str(v)) for k, v in dotenv_values(paths.ENV_PATH, encoding="utf-8").items()}
    except Exception:
        return
    _state.update(stamp=stamp, values=values, version=_state["version"] + 1)


def version() -> int:
    with _lock:
        _refresh()
        return _state["version"]


def get(name: str, default: str = "") -> str:
    with _lock:
        _refresh()
        val = _state["values"].get(name)
    if val is not None and str(val).strip() != "":
        return str(val).strip()
    val = os.environ.get(name)
    if val is not None and val.strip() != "":
        return val.strip()
    try:
        from nonebot import get_driver

        val = getattr(get_driver().config, name.lower(), None)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    except Exception:
        pass
    return default


def get_bool(name: str, default: bool) -> bool:
    raw = get(name, "")
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def get_int(name: str, default: int) -> int:
    try:
        return int(float(get(name, "")))
    except (TypeError, ValueError):
        return default


def get_float(name: str, default: float) -> float:
    try:
        return float(get(name, ""))
    except (TypeError, ValueError):
        return default


def get_set(name: str, default: str = "") -> set[str]:
    return {x.strip() for x in get(name, default).replace("，", ",").split(",") if x.strip()}


class _EnvNamespace:
    """让旧写法 ``getattr(_config, "proxy_url", 默认值)`` 改读 .env（键名取大写）。"""

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        val = get(name.upper(), "")
        if val == "":
            raise AttributeError(name)
        return val


config_ns = _EnvNamespace()


def bot_qq() -> str:
    """机器人 QQ：以当前真正连上的机器人账号为准（换号后不用改任何配置也能用）。

    正在处理某个号收到的事件（见 core.botref.bind）时返回那个号；

    连着的号里有 .env 的 BOT_QQ 就用它；BOT_QQ 没连上（比如直接在 NapCat 换了号）就用实际在线的号；
    机器人还没连上时才退回 .env 的 BOT_QQ。
    """
    qq = get("BOT_QQ", "")
    try:
        from nonebot import get_bots

        from .botref import current_self_id

        online = [str(b) for b in get_bots()]
        handling = current_self_id()  # 正在处理某个号收到的事件时，以那个号为准（多号同时在线）
        if handling and handling in online:
            return handling
        if online:
            return qq if qq in online else online[0]
    except Exception:
        pass
    return qq


def assets_root() -> str:
    """浏览器配置、Real-ESRGAN、站点 cookies 等外部资源所在目录。

    切换完成后指向 v2 的 assets 目录（ASSETS_ROOT=D:\\furina-qq-bot\\assets）。
    """
    from . import paths

    return get("ASSETS_ROOT", str(paths.ROOT))


def owner_qq() -> str:
    """群主/主人 QQ（读取 AI_CHAT_GROUP_OWNER_QQ 或 RELAY_OWNER_QQ）。"""
    return get("AI_CHAT_GROUP_OWNER_QQ", "") or get("RELAY_OWNER_QQ", "")
