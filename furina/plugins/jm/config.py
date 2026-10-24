# ============ JM 下载配置区（从 .env 读取，可在控制面板「JM 下载」里改） ============
"""JM 本子下载（/jm 车号 → PDF 群文件）的全部可调参数。

键名沿用旧版 ``JM_*``，全部通过 ``furina.core.env`` 每次现读：面板改完 .env 立即生效，
不需要重启机器人。这里只写配置键名，具体值统一放在 .env。
"""
from __future__ import annotations

import math
import os
import re

from ...core import env, paths, roles

_DEFAULT_CACHE_DIR = str(paths.DATA_DIR / "jm")


def enabled() -> bool:
    """功能总开关（关闭后 /jm 一律不响应）。"""
    return env.get_bool("JM_ENABLED", True)


def proxy() -> str:
    """下载代理；留空表示按 jmcomic 默认走系统代理。"""
    return env.get("JM_PROXY", "")


def timeout() -> int:
    """单本下载总超时（秒）：整本下载 + 合成 PDF。"""
    return env.get_int("JM_TIMEOUT", 900)


def max_images() -> int:
    """单本页数上限（0=不限制）；超过直接拒绝，防止大长篇拖垮队列。"""
    return env.get_int("JM_MAX_IMAGES", 0)


def cache_max_mb() -> int:
    """缓存目录容量上限（MB，0=不限制）。

    正常情况下每本发完就删了，这个上限只是留给历史残留/手动清理用的兜底；
    面板「缓存概况」里的占用统计同样不看它。
    """
    raw = env.get_float("JM_CACHE_MAX_MB", 20480)
    if raw <= 0:
        return 0
    return max(1, int(math.ceil(raw)))


def daily_user_limit() -> int:
    """同一用户每日下载次数上限（0=不限制）。"""
    return env.get_int("JM_DAILY_USER_LIMIT", 5)


def daily_group_limit() -> int:
    """同一群每日下载次数上限（0=不限制）。"""
    return env.get_int("JM_DAILY_GROUP_LIMIT", 20)


def progress_interval() -> int:
    """下载中的页数进度提示间隔（秒）。"""
    return env.get_int("JM_PROGRESS_INTERVAL", 45)


def cache_dir() -> str:
    """缓存根目录的绝对路径（图片 + PDF 都放在这里，同一本再点直接复用）。"""
    return os.path.abspath(env.get("JM_CACHE_DIR", "") or _DEFAULT_CACHE_DIR)


# 命令头："/jm 350234"、"jm 350234"、"JM350234"、"/jm350234" 都算；
# 后面只能跟非字母数字（空格/标点）或数字，避免把 "jmd"、"jmabc" 这种当成车号
_CMD_HEAD = re.compile(r"^\s*/?jm(?=\d|[^0-9a-zA-Z]|\s|$)\s*", re.IGNORECASE)
# 车号：取命令头之后的第一个数字串（兼容 jmv 风格的混合文本 "350谁还没看过234"）
_ALBUM_DIGITS = re.compile(r"\d+")
# 紧跟数字的拉丁字母（"350abc"）：不当成车号（中文夹在数字里仍按粘贴车号处理）
_LATIN_AFTER_DIGITS = re.compile(r"\d[a-z]", re.IGNORECASE)


def parse_album_id(raw_text: str) -> str:
    """从消息文本里解析车号，失败返回空串。

    只接受命令头后面的第一个数字串；"jm 你好"、"jmabc" 这类没有车号的内容返回空串，
    以免把闲聊当成下载请求。
    """
    text = roles.strip_bot_at(raw_text or "").strip()
    if not text:
        return ""
    head = _CMD_HEAD.match(text)
    if not head:
        return ""
    tail = text[head.end():].strip()
    if not tail:
        return ""
    m = _ALBUM_DIGITS.search(tail)
    if not m:
        return ""
    return m.group(0) if not _LATIN_AFTER_DIGITS.search(tail) else ""


def has_jm_head(raw_text: str) -> bool:
    """文本是否以 /jm 或 jm 命令头开头（用于判断要不要回应帮助语）。"""
    return bool(_CMD_HEAD.match(roles.strip_bot_at(raw_text or "")))
