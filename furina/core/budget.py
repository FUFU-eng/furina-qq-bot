"""花钱感知（参考 fatfish-bot 的 budget.py）：最近 24 小时花得越多，她越懒得「没人叫也开口」。

不依赖 nonebot，面板也能 import。数据来自 llm.py 记的本地账（``llmtok:cost_u``，按天）和二代仿真按群记的账
（``llmgrp:cost_u``，kernel 写入）。

- 积极性 w(u) = f + (1 − f) / (1 + (u / u½)^k)，u = 最近 24 小时花了多少元（今天 + 昨天按剩余小时折算）。
- 不同醒法用不同的指数：被艾特 / 叫名字 / 引用永远照回（只是花超了少想一点），接话 w^2.5，随手刷到 / 潜水久了 w^3。
- 每日上限（AI_CHAT_DAILY_CAP_YUAN，0 = 不限）：超了就只回被叫到的，且不开思考；
  每群每日上限（AI_CHAT_GROUP_DAILY_CAP_YUAN，0 = 不限）：这个群超了同样只回被叫到的。
全部热读取 .env（面板「模式与内核」页可改）。
"""
from __future__ import annotations

import random
import time
from datetime import date, timedelta

from . import db, env

GROUP_KIND = "llmgrp:"
EXPONENTS = {"at": 0.0, "name": 0.0, "reply": 0.0, "back": 0.0, "limited": 0.0,
             "follow": 2.5, "random": 3.0, "idle": 3.0}
_cache: dict = {"ts": 0.0, "day": "", "today": 0.0, "yesterday": 0.0, "groups": {}}


def settings() -> dict:
    return {
        "enabled": env.get_bool("AI_CHAT_BUDGET_ENABLED", True),
        "half": max(0.05, env.get_float("AI_CHAT_BUDGET_HALF_YUAN", 1.0)),
        "k": max(0.2, min(12.0, env.get_float("AI_CHAT_BUDGET_K", 4.0))),
        "floor": max(0.0, min(1.0, env.get_float("AI_CHAT_BUDGET_FLOOR", 0.1))),
        "daily_cap": max(0.0, env.get_float("AI_CHAT_DAILY_CAP_YUAN", 3.0)),
        "group_cap": max(0.0, env.get_float("AI_CHAT_GROUP_DAILY_CAP_YUAN", 0.0)),
    }


def _load(force: bool = False) -> dict:
    now = time.time()
    today = date.today().isoformat()
    if not force and now - _cache["ts"] < 20 and _cache["day"] == today:
        return _cache
    yday = (date.today() - timedelta(days=1)).isoformat()
    tot = {today: 0, yday: 0}
    groups: dict[str, int] = {}
    try:
        for d, kind, gid, _u, n in db.usage_rows(yday, ("llmtok:cost_u", GROUP_KIND + "cost_u")):
            if kind == "llmtok:cost_u" and d in tot:
                tot[d] += int(n)
            elif kind == GROUP_KIND + "cost_u" and d == today:
                groups[gid] = groups.get(gid, 0) + int(n)
    except Exception:  # noqa: BLE001  账本读不了就当没花钱，不挡她说话
        pass
    _cache.update(ts=now, day=today, today=tot[today] / 1e6, yesterday=tot[yday] / 1e6, groups=groups)
    return _cache


def spent_today() -> float:
    return _load()["today"]


def spent_24h() -> float:
    c = _load()
    lt = time.localtime()
    left = 1.0 - (lt.tm_hour + lt.tm_min / 60) / 24
    return c["today"] + c["yesterday"] * left


def group_spent_today(group_id) -> float:
    return _load()["groups"].get(str(group_id), 0) / 1e6


def willingness(u: float | None = None) -> float:
    s = settings()
    u = spent_24h() if u is None else max(0.0, float(u))
    return s["floor"] + (1 - s["floor"]) / (1 + (u / s["half"]) ** s["k"])


def over_cap(group_id=None) -> str:
    """超了哪个上限：''（没超）/ 'daily' / 'group'。"""
    s = settings()
    if s["daily_cap"] > 0 and spent_today() >= s["daily_cap"]:
        return "daily"
    if group_id is not None and s["group_cap"] > 0 and group_spent_today(group_id) >= s["group_cap"]:
        return "group"
    return ""


def allow(reason: str, group_id=None, rng=random.random) -> tuple[bool, str]:
    """这次醒来要不要真叫醒模型。被叫到的永远 True；其他按上限和积极性掷骰子。"""
    exp = EXPONENTS.get(reason, 3.0)
    if exp <= 0:
        return True, ""
    cap = over_cap(group_id)
    if cap:
        return False, "今天花到上限了" if cap == "daily" else "这个群今天花到上限了"
    if not settings()["enabled"]:
        return True, ""
    p = willingness() ** exp
    if rng() < p:
        return True, ""
    return False, f"最近 24 小时花了 {spent_24h():.2f} 元，积极性 {p:.0%}"


def effort_cap(effort: str, group_id=None) -> str:
    """花超上限时不开思考；积极性很低（< 0.3）时最多「低」。"""
    if over_cap(group_id):
        return "off"
    if settings()["enabled"] and effort in ("high", "max") and willingness() < 0.3:
        return "low"
    return effort


def status(group_id=None) -> dict:
    s = settings()
    out = {"today": round(spent_today(), 4), "last24h": round(spent_24h(), 4),
           "willingness": round(willingness(), 3), "follow_p": round(willingness() ** EXPONENTS["follow"], 3),
           "random_p": round(willingness() ** EXPONENTS["random"], 3), "over_cap": over_cap(group_id), **s}
    if group_id is not None:
        out["group_today"] = round(group_spent_today(group_id), 4)
    return out


def curve(points: int = 13, top: float | None = None) -> list[dict]:
    """面板画「积极性曲线」用：[{u, w, follow, random}]。"""
    s = settings()
    top = top or max(s["half"] * 3, 0.5)
    out = []
    for i in range(points):
        u = top * i / (points - 1)
        w = willingness(u)
        out.append({"u": round(u, 3), "w": round(w, 3), "follow": round(w ** EXPONENTS["follow"], 3),
                    "random": round(w ** EXPONENTS["random"], 3)})
    return out
