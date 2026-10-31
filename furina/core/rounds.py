"""二代仿真每一轮花了多少（面板「花费看板」用，参考 bluefishovo 的「令牌与花费」）。

不依赖 nonebot，面板也能 import。
- 按群按天：usage 表 kind = ``llmgrp:{calls|rounds|hit|miss|out|cost_u}``，group_id = 群号；
- 逐轮明细：docs 表 ns = ``dsh_rounds``，key = 日期，value = 当天每一轮的列表（每天最多 800 轮，保留 8 天）。
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from . import db

NS = "dsh_rounds"
KIND = "llmgrp:"
DAY_MAX = 800
KEEP_DAYS = 8
_pruned = {"day": ""}


def add(group_id, *, reason: str, rows: list[dict], effort: str = "", steps: int = 0, tools=None,
        said: int = 0, silent: bool = False, ok: bool = True, seconds: float = 0.0, route: str = "") -> dict:
    """记一轮。rows = 这一轮每次模型请求的 {hit, miss, out, cost}。返回汇总。"""
    tot = {"calls": len(rows), "hit": 0, "miss": 0, "out": 0, "cost": 0.0}
    for r in rows:
        tot["hit"] += int(r.get("hit") or 0)
        tot["miss"] += int(r.get("miss") or 0)
        tot["out"] += int(r.get("out") or 0)
        tot["cost"] += float(r.get("cost") or 0)
    day = date.today().isoformat()
    gid = str(group_id or "")
    try:
        for field, n in (("rounds", 1), ("calls", tot["calls"]), ("hit", tot["hit"]), ("miss", tot["miss"]),
                         ("out", tot["out"]), ("cost_u", int(round(tot["cost"] * 1e6)))):
            if n:
                db.usage_add(day, KIND + field, gid, "", int(n))
        entry = {"t": round(time.time(), 1), "g": gid, "why": reason, "eff": effort, "steps": int(steps),
                 "tools": list(tools or [])[:12], "said": int(said), "silent": bool(silent), "ok": bool(ok),
                 "sec": round(float(seconds), 1), "route": route, "max_in": max((int(r.get("hit") or 0) + int(r.get("miss") or 0) for r in rows), default=0),
                 **{k: (round(v, 6) if k == "cost" else v) for k, v in tot.items()}}

        def _upd(cur):
            cur = list(cur or [])
            cur.append(entry)
            return cur[-DAY_MAX:]

        db.update(NS, day, _upd, default=[])
        _prune(day)
    except Exception:  # noqa: BLE001  记账失败不影响聊天
        pass
    return tot


def _prune(today: str) -> None:
    if _pruned["day"] == today:
        return
    _pruned["day"] = today
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    for k in list((db.items(NS) or {}).keys()):
        if k < cutoff:
            db.delete(NS, k)


def recent(day: str | None = None, group_id=None, limit: int = 200) -> list[dict]:
    rows = db.get(NS, day or date.today().isoformat(), []) or []
    if group_id not in (None, ""):
        rows = [r for r in rows if r.get("g") == str(group_id)]
    return list(reversed(rows[-limit:]))


def by_group(days: int = 7) -> list[dict]:
    """最近几天每个群每天花了多少：[{day, group, rounds, calls, hit, miss, out, cost}]，新的在前。"""
    since = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    acc: dict[tuple[str, str], dict] = {}
    for d, kind, gid, _u, n in db.usage_rows(since):
        if not kind.startswith(KIND):
            continue
        r = acc.setdefault((d, gid), {"day": d, "group": gid, "rounds": 0, "calls": 0, "hit": 0, "miss": 0,
                                      "out": 0, "cost_u": 0})
        r[kind[len(KIND):]] = r.get(kind[len(KIND):], 0) + int(n)
    out = []
    for r in acc.values():
        r["cost"] = round(r.pop("cost_u", 0) / 1e6, 4)
        inp = r["hit"] + r["miss"]
        r["hit_rate"] = round(r["hit"] / inp, 3) if inp else 0.0
        r["per_round"] = round(r["cost"] / r["rounds"], 4) if r["rounds"] else 0.0
        out.append(r)
    return sorted(out, key=lambda r: (r["day"], r["cost"]), reverse=True)


def hourly(day: str | None = None) -> list[dict]:
    """某天按小时：[{hour, rounds, cost}]（看哪几个小时烧得凶）。"""
    rows = db.get(NS, day or date.today().isoformat(), []) or []
    acc = [{"hour": h, "rounds": 0, "cost": 0.0} for h in range(24)]
    for r in rows:
        h = time.localtime(float(r.get("t") or 0)).tm_hour
        acc[h]["rounds"] += 1
        acc[h]["cost"] = round(acc[h]["cost"] + float(r.get("cost") or 0), 6)
    return acc
