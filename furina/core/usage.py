"""每人每日次数 + 各功能每日统计（与旧 daily_limits.py 接口一致，改存 SQLite）。

- 每人次数：kind = "chat" / "poke" ...，按 (日期, 群, 人) 计数。
- 功能统计：kind = "stat:<功能名>"，按 (日期, 群) 计数，保留近 40 天。
- 谁用得多：kind = "who:<功能名>"，按 (日期, 群, 人) 计数，永久保留（面板「谁用得多」页、众筹参考用）。
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db

HISTORY_KEEP_DAYS = 40
_STAT = "stat:"
_WHO = "who:"
NAMES_NS = "usage_names"
# 按人统计的功能：(键, 名称)。upscale 按次，upscale_img 按张
WHO_KINDS = (("chat", "艾特聊天"), ("jm", "JM 本子"), ("upscale", "强化"), ("upscale_img", "强化张数"),
             ("search", "搜图"), ("poke", "戳一戳"))
_last_prune = {"day": ""}


def _today() -> str:
    return date.today().isoformat()


def _maybe_prune() -> None:
    today = _today()
    if _last_prune["day"] != today:
        _last_prune["day"] = today
        db.usage_prune((date.today() - timedelta(days=HISTORY_KEEP_DAYS)).isoformat(), keep_prefix=_WHO)


def consume(kind: str, group_id, user_id, limit: int) -> bool:
    """尝试消耗一次；limit<=0 表示无限。超限返回 False。"""
    _maybe_prune()
    return db.usage_add(_today(), kind, str(group_id), str(user_id), 1, limit if limit > 0 else 0)


def used(kind: str, group_id, user_id) -> int:
    return db.usage_get(_today(), kind, str(group_id), str(user_id))


def track(kind: str, group_id, count: int = 1) -> None:
    _maybe_prune()
    db.usage_add(_today(), _STAT + kind, str(group_id), "", int(count))


def track_user(kind: str, group_id, user_id, count: int = 1, name: str = "") -> None:
    """按人记一次用量（谁用得多）。name 顺手记下昵称，面板显示用。"""
    uid = str(user_id or "").strip()
    if not uid or int(count) <= 0:
        return
    _maybe_prune()
    db.usage_add(_today(), _WHO + kind, str(group_id or ""), uid, int(count))
    name = (name or "").strip()
    if name:
        try:
            old = db.get(NAMES_NS, uid) or {}
            if not isinstance(old, dict) or old.get("name") != name:
                db.put(NAMES_NS, uid, {"name": name[:40]})
        except Exception:  # noqa: BLE001  记名字失败不影响计数
            pass


def who_totals(days: int = 0, group_id=None) -> dict[str, dict[str, int]]:
    """{QQ: {功能: 次数}}；days<=0 表示全部历史，group_id 只看某个群。"""
    start = "0000-00-00" if days <= 0 else (date.today() - timedelta(days=days - 1)).isoformat()
    out: dict[str, dict[str, int]] = {}
    gid = None if group_id in (None, "") else str(group_id)
    for _day, kind, g, uid, count in db.usage_rows(start):
        if not kind.startswith(_WHO) or not uid or (gid is not None and str(g) != gid):
            continue
        b = out.setdefault(str(uid), {})
        k = kind[len(_WHO):]
        b[k] = b.get(k, 0) + int(count)
    return out


def who_first_day() -> dict[str, str]:
    """每种功能最早有按人记录的日期 {功能: YYYY-MM-DD}。"""
    out: dict[str, str] = {}
    for day, kind, _g, uid, _c in db.usage_rows("0000-00-00"):
        if kind.startswith(_WHO) and uid:
            k = kind[len(_WHO):]
            if k not in out or day < out[k]:
                out[k] = day
    return out


def get_stats_range(days: int) -> dict:
    """近 days 天（含今天）：{"groups": {gid: {kind: n}}, "total": N}。"""
    start = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    agg: dict[str, dict[str, int]] = {}
    for _day, kind, gid, _uid, count in db.usage_rows(start):
        if not kind.startswith(_STAT):
            continue
        b = agg.setdefault(gid, {})
        k = kind[len(_STAT):]
        b[k] = b.get(k, 0) + int(count)
    total = sum(v for g in agg.values() for v in g.values())
    return {"groups": agg, "total": total}


def get_stats() -> dict:
    return get_stats_range(1)["groups"]


def parse_limit_overrides(raw: str) -> dict:
    """uid:limit（该用户所有群）或 gid:uid:limit（仅该群）；limit<=0 表示无限。"""
    out: dict[str, int] = {}
    for pair in (raw or "").replace(" ", "").split(","):
        if not pair:
            continue
        parts = pair.split(":")
        try:
            if len(parts) == 2 and parts[0]:
                out[str(parts[0])] = int(parts[1])
            elif len(parts) == 3 and parts[0] and parts[1]:
                out[f"{parts[0]}:{parts[1]}"] = int(parts[2])
        except (ValueError, TypeError):
            continue
    return out


def effective_limit(overrides: dict, unified: int, group_id, user_id) -> int:
    """群专属 > 个人全局 > 统一上限。0=无限。"""
    gid, uid = str(group_id), str(user_id)
    if f"{gid}:{uid}" in (overrides or {}):
        return overrides[f"{gid}:{uid}"]
    if uid in (overrides or {}):
        return overrides[uid]
    return unified
