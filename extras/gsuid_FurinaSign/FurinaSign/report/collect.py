"""收集签到订阅与今日签到状态（只读查询，不执行签到）。"""
from __future__ import annotations

import sys
import json
import asyncio
import sqlite3
from pathlib import Path
from dataclasses import field, dataclass
from typing import Dict, List, Tuple, Optional

from gsuid_core.logger import logger
from gsuid_core.subscribe import gs_subscribe
from gsuid_core.utils.api.mys_api import mys_api
from gsuid_core.utils.database.models import GsBind, GsUser, CoreUser, Subscribe

from .config import FS_CONFIG

# 卡片列顺序：原神 | 星铁 | 崩坏3 | 绝区零
GAMES: Tuple[Tuple[str, str, str], ...] = (
    ("gs", "原神", "[原神] 自动签到"),
    ("sr", "星铁", "[星铁] 自动签到"),
    ("bbb", "崩坏3", "[崩坏3] 自动签到"),
    ("zzz", "绝区零", "[绝区零] 自动签到"),
)
LOGIN_EXPIRED = {10001, -100, 10103, -10001}


@dataclass
class Cell:
    state: str  # ok / pending / fail / off
    label: str
    detail: str = ""


@dataclass
class Member:
    bot_id: str
    user_id: str
    name: str = ""
    subs: Dict[str, Subscribe] = field(default_factory=dict)
    cells: Dict[str, Cell] = field(default_factory=dict)
    groups: set = field(default_factory=set)


def mask_qq(qq: str) -> str:
    qq = str(qq or "")
    return qq[:2] + "***" + qq[-2:] if len(qq) > 4 else qq


async def load_members() -> Dict[Tuple[str, str], Member]:
    """所有开启了任一游戏「自动签到」订阅的用户。"""
    members: Dict[Tuple[str, str], Member] = {}
    for game, _, task in GAMES:
        rows = await gs_subscribe.get_subscribe(task) or []
        for sub in rows:
            if not sub.uid or not sub.user_id:
                continue
            key = (str(sub.bot_id), str(sub.user_id))
            member = members.setdefault(key, Member(bot_id=key[0], user_id=key[1]))
            member.subs.setdefault(game, sub)
    return members


async def resolve_groups(member: Member) -> set:
    groups = set()
    for sub in member.subs.values():
        if sub.user_type == "group" and sub.group_id:
            groups.add(str(sub.group_id))
    try:
        groups.update(g for g in await GsBind.get_bind_group_list(member.user_id, member.bot_id) if g)
    except Exception as exc:
        logger.debug(f"[FurinaSign] 读取绑定群失败: {exc}")
    try:
        for row in await CoreUser.select_rows(user_id=member.user_id) or []:
            if row.group_id and str(row.group_id) != "1":
                groups.add(str(row.group_id))
    except Exception as exc:
        logger.debug(f"[FurinaSign] 读取群成员记录失败: {exc}")
    member.groups = groups
    return groups


async def resolve_name(member: Member, group_id: Optional[str] = None) -> str:
    best = ""
    try:
        for row in await CoreUser.select_rows(user_id=member.user_id) or []:
            name = (row.user_name or "").strip()
            if not name or name == "1":
                continue
            if group_id and str(row.group_id) == str(group_id):
                return name
            best = best or name
    except Exception:
        pass
    return best or "群友"


def _error_cell(code) -> Cell:
    if isinstance(code, int) and code in LOGIN_EXPIRED:
        return Cell("fail", "登录失效", "@芙芙 绑定账号")
    return Cell("fail", "查询失败", f"错误码 {code}" if isinstance(code, int) else "")


def _bbb_until():
    """复用已由 GsCore 加载的 BBBUID 签到模块，避免二次 import 造成重复注册。"""
    for name, module in list(sys.modules.items()):
        if name.endswith("bbb_sign.until") and "BBBUID" in name and module is not None:
            return module
    return None


async def _bbb_cell(uid: str) -> Cell:
    until = _bbb_until()
    if until is None:
        return Cell("fail", "插件未加载")
    cookie = await GsUser.get_user_cookie_by_uid(uid, "bbb")
    if not cookie:
        return Cell("fail", "缺少登录", "@芙芙 绑定账号")
    accounts = await until.get_account_list(cookie)
    if isinstance(accounts, int) or not accounts:
        return _error_cell(accounts if isinstance(accounts, int) else -1)
    region = None
    for item in (accounts.get("data") or {}).get("list", []) or []:
        if str(item.get("game_uid")) == str(uid):
            region = item.get("region")
            break
    if not region:
        return Cell("fail", "未找到角色")
    data = await until.is_sign(region=region, uid=uid, cookie=cookie)
    if isinstance(data, int) or not data:
        return _error_cell(data if isinstance(data, int) else -1)
    info = data.get("data") or {}
    total = int(info.get("total_sign_day") or 0)
    if info.get("is_sign"):
        return Cell("ok", "今日已签", f"第{total}天")
    return Cell("pending", "尚未签到", f"本月{total}天")


async def query_cell(game: str, uid: str) -> Cell:
    try:
        if game == "bbb":
            return await _bbb_cell(uid)
        info = await mys_api.get_sign_info(uid, game)
        if isinstance(info, int):
            return _error_cell(info)
        total = int(info.get("total_sign_day") or 0)
        if info.get("is_sign"):
            return Cell("ok", "今日已签", f"第{total}天")
        return Cell("pending", "尚未签到", f"本月{total}天")
    except Exception as exc:
        logger.warning(f"[FurinaSign] 查询 {game} 签到状态失败: {exc!r}")
        return Cell("fail", "查询失败")


async def fill_cells(member: Member) -> None:
    for game, _, _ in GAMES:
        sub = member.subs.get(game)
        if sub is None:
            member.cells[game] = Cell("off", "未开启")
            continue
        member.cells[game] = await query_cell(game, str(sub.uid))
        await asyncio.sleep(0.4)


# ---------------- 目标群 ----------------

def _furina_db() -> Optional[Path]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "furina.db"
        if candidate.is_file() and (parent / "furina").is_dir():
            return candidate
    return None


def furina_sign_groups() -> Optional[set]:
    """芙宁娜面板里「米游社签到」开启的群；读不到时返回 None（不做过滤）。"""
    path = _furina_db()
    if path is None:
        return None
    try:
        uri = "file:" + path.as_posix() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            rows = conn.execute("SELECT key, value FROM docs WHERE ns='perm'").fetchall()
    except Exception as exc:
        logger.warning(f"[FurinaSign] 读取面板群权限失败，改为不过滤: {exc}")
        return None
    out = set()
    for key, value in rows:
        try:
            data = json.loads(value)
        except ValueError:
            continue
        features = data.get("features") or {}
        if data.get("enabled", True) and features.get("miyoushe_sign", True):
            out.add(str(key))
    return out


async def group_members(members: Dict[Tuple[str, str], Member]) -> Dict[str, List[Member]]:
    for member in members.values():
        await resolve_groups(member)
    configured = [str(g).strip() for g in (FS_CONFIG.get_config("ReportGroups").data or []) if str(g).strip()]
    allowed = set(configured) if configured else furina_sign_groups()
    result: Dict[str, List[Member]] = {}
    for member in members.values():
        for gid in member.groups:
            if allowed is not None and gid not in allowed:
                continue
            result.setdefault(gid, []).append(member)
    return result
