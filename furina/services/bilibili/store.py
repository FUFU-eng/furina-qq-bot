"""B 站模块的持久状态（全部存在 SQLite 的 bili 命名空间，面板/聊天插件都能直接读）。

- auth：小号登录 Cookie（SESSDATA / bili_jct / DedeUserID / buvid3 …）与昵称、mid；
- watched：最近看过的视频与当时的感受（聊天里「最近刷到…」就读这里）；
- posts：自己发过的评论/动态（防重复、给聊天当经历）；
- day：当天计数（评论、动态、点赞、刷视频轮数、分享）；
- halt：触发风控后暂停到什么时候、原因；
- live / activity：正在看哪个视频、最近在干什么（面板「B站生活」实时显示）；
- request：面板点的「刷一下 / 发动态 / 看回复 / 暂停 / 恢复」，机器人每 10 秒来取一次；
- reply_seen：评论区里别人回她的消息，已经看过（回或不回）的编号。
"""
from __future__ import annotations

import time

from ...core import db

NS = "bili"
MAX_WATCHED = 300
MAX_POSTS = 100


def _today(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))


# ---------------- 登录 ----------------
def get_auth() -> dict:
    return db.get(NS, "auth") or {}


def save_auth(cookies: dict, uname: str = "", mid: str = "") -> None:
    db.put(NS, "auth", {"cookies": dict(cookies), "uname": uname, "mid": str(mid or cookies.get("DedeUserID", "")),
                        "ts": time.time()})


def update_auth(**kw) -> None:
    db.update(NS, "auth", lambda a: {**(a or {}), **kw})


def clear_auth() -> None:
    db.delete(NS, "auth")


def logged_in() -> bool:
    c = get_auth().get("cookies") or {}
    return bool(c.get("SESSDATA") and c.get("bili_jct"))


# ---------------- 当天计数 ----------------
def day_counts(ts: float | None = None) -> dict:
    d = db.get(NS, "day") or {}
    return d if d.get("day") == _today(ts) else {"day": _today(ts)}


def bump(kind: str, n: int = 1, ts: float | None = None) -> int:
    def _u(d):
        d = dict(d or {})
        if d.get("day") != _today(ts):
            d = {"day": _today(ts)}
        d[kind] = int(d.get(kind) or 0) + n
        d[kind + "_last"] = ts or time.time()
        return d
    return int(db.update(NS, "day", _u).get(kind) or 0)


def used(kind: str, ts: float | None = None) -> int:
    return int(day_counts(ts).get(kind) or 0)


def last(kind: str, ts: float | None = None) -> float:
    return float(day_counts(ts).get(kind + "_last") or 0)


# ---------------- 风控暂停 ----------------
def halt(reason: str, hours: float) -> None:
    db.put(NS, "halt", {"until": time.time() + hours * 3600, "reason": reason, "ts": time.time()})


def halted(now: float | None = None) -> tuple[bool, str]:
    h = db.get(NS, "halt") or {}
    if float(h.get("until") or 0) > (now or time.time()):
        return True, str(h.get("reason") or "")
    return False, ""


def resume() -> None:
    db.delete(NS, "halt")


# ---------------- 看过的视频 ----------------
def watched_ids() -> set[str]:
    return {w.get("bvid") for w in (db.get(NS, "watched") or []) if w.get("bvid")}


def add_watched(items: list[dict]) -> None:
    def _u(lst):
        lst = list(lst or [])
        have = {w.get("bvid") for w in lst}
        for it in items:
            if it.get("bvid") and it["bvid"] not in have:
                lst.append({**it, "ts": it.get("ts") or time.time()})
        return lst[-MAX_WATCHED:]
    db.update(NS, "watched", _u)


def recent_watched(hours: float = 36, limit: int = 3, now: float | None = None, min_interest: int = 5) -> list[dict]:
    ts = now or time.time()
    lst = [w for w in (db.get(NS, "watched") or [])
           if ts - float(w.get("ts") or 0) <= hours * 3600 and int(w.get("interest") or 0) >= min_interest]
    lst.sort(key=lambda w: (int(w.get("interest") or 0), float(w.get("ts") or 0)), reverse=True)
    return sorted(lst[:limit], key=lambda w: float(w.get("ts") or 0))


# ---------------- 自己发过的 ----------------
def add_post(kind: str, text: str, **extra) -> None:
    def _u(lst):
        lst = list(lst or [])
        lst.append({"kind": kind, "text": text, "ts": time.time(), **extra})
        return lst[-MAX_POSTS:]
    db.update(NS, "posts", _u)


def recent_posts(hours: float = 36, limit: int = 2, now: float | None = None, kind: str = "") -> list[dict]:
    ts = now or time.time()
    lst = [p for p in (db.get(NS, "posts") or [])
           if ts - float(p.get("ts") or 0) <= hours * 3600 and (not kind or p.get("kind") == kind)]
    return lst[-limit:]


def commented_bvids() -> set[str]:
    return {p.get("bvid") for p in (db.get(NS, "posts") or []) if p.get("kind") == "comment" and p.get("bvid")}


# ---------------- 杂项缓存 ----------------
def get_meta(key: str, default=None):
    return (db.get(NS, "meta") or {}).get(key, default)


def set_meta(key: str, value) -> None:
    db.update(NS, "meta", lambda m: {**(m or {}), key: value})


# ---------------- 实时状态（面板用） ----------------
MAX_ACTIVITY = 60


def set_live(state: str, video: dict | None = None, step: str = "") -> None:
    """state: idle / browsing / watching / thinking / acting；video 是正在看的那条。"""
    v = None
    if video:
        v = {k: video.get(k) for k in ("bvid", "title", "up", "pic", "duration", "source", "tname") if video.get(k)}
    db.put(NS, "live", {"state": state, "video": v, "step": step, "ts": time.time()})


def get_live() -> dict:
    lv = db.get(NS, "live") or {"state": "idle"}
    if lv.get("state") != "idle" and time.time() - float(lv.get("ts") or 0) > 15 * 60:
        lv = {**lv, "state": "idle", "stale": True}  # 机器人中途被重启之类，别一直显示「正在看」
    return lv


def log_activity(text: str, kind: str = "info", **extra) -> None:
    def _u(lst):
        lst = list(lst or [])
        lst.append({"text": str(text)[:200], "kind": kind, "ts": time.time(), **extra})
        return lst[-MAX_ACTIVITY:]
    db.update(NS, "activity", _u)


def activity(limit: int = 40) -> list[dict]:
    return list(db.get(NS, "activity") or [])[-limit:]


# ---------------- 面板请求 ----------------
REQUEST_ACTIONS = ("surf", "dynamic", "replies", "pause", "resume", "logout", "trending")


def put_request(action: str, **extra) -> dict:
    if action not in REQUEST_ACTIONS:
        raise ValueError(f"不支持的操作：{action}")
    req = {"action": action, "id": f"{int(time.time() * 1000)}", "ts": time.time(), **extra}
    db.put(NS, "request", req)
    return req


def take_request(max_age: float = 600) -> dict | None:
    """机器人取走面板请求（取一次就删）；太旧的请求直接丢弃。"""
    req = db.get(NS, "request")
    if not req:
        return None
    db.delete(NS, "request")
    if time.time() - float(req.get("ts") or 0) > max_age:
        return None
    return req


def pending_request() -> dict | None:
    return db.get(NS, "request")


# ---------------- 评论区回复 ----------------
MAX_SEEN = 600


def reply_seen() -> set[str]:
    return set(db.get(NS, "reply_seen") or [])


def mark_replies_seen(ids) -> None:
    ids = [str(i) for i in ids if i]
    if not ids:
        return

    def _u(lst):
        lst = [x for x in (lst or []) if x not in ids] + ids
        return lst[-MAX_SEEN:]
    db.update(NS, "reply_seen", _u)


def all_watched(limit: int = 30) -> list[dict]:
    return list(db.get(NS, "watched") or [])[-limit:][::-1]


def all_posts(limit: int = 30) -> list[dict]:
    return list(db.get(NS, "posts") or [])[-limit:][::-1]


def status() -> dict:
    a = get_auth()
    h, why = halted()
    return {"logged_in": logged_in(), "uname": a.get("uname", ""), "mid": a.get("mid", ""),
            "invalid": bool(a.get("invalid")), "today": day_counts(), "halted": h, "halt_reason": why,
            "watched": recent_watched(hours=72, limit=5, min_interest=0)}
