"""烦躁值：她会被找烦。

不是每轮现演的台词，而是一个会累积、会消退、会改变她行为的状态（纯本地计算，零 token）：
- 每个人一根烦躁值（0~100）+ 一根「群里整体吵不吵」的总烦躁值；
- 涨：被同一个人连着艾特（10 分钟内越密涨得越快）、同一句话反复问、她说好要安静 / 在忙还被艾特、被骂；
- 消：没人吵就按半衰期慢慢回落（默认 60 分钟淡一半）；被哄（谢谢 / 抱抱 / 对不起 / 夸她）会降一截；
- 用：烦了回得短、冲、敷衍（提示词里只给「底子」，不命令口气）；对某人烦透了会有一定概率直接不理 TA 这条
  （不叫醒她 = 不花 token）；群里整体吵时，她随机插话也少一些。主人永远不会被「不理」。

数据在 SQLite annoy/state，机器人和面板两个进程都能读（面板只读，用 snapshot()）。
"""
from __future__ import annotations

import math
import random
import re
import threading
import time

from ..core import db, env

NS = "annoy"
KEY = "state"
_lock = threading.Lock()

GLOBAL_HALF_MINUTES = 45
RECENT_WINDOW = 600          # 「连着艾特」看最近 10 分钟
REPEAT_WINDOW = 1800         # 同一句话 30 分钟内再问算重复
MAX_EVENTS = 40

BANDS = ((85, "烦透了"), (60, "很烦"), (35, "有点烦"), (15, "有一点点不耐烦"))

_INSULT = re.compile(r"蠢|笨蛋|白痴|废物|垃圾|弱智|傻逼|傻b|sb|滚|闭嘴|烦死|讨厌你|没用|菜鸡|菜狗|神经病|有病")
_SOOTHE = re.compile(r"谢谢|辛苦|抱抱|摸摸|对不起|错了|不吵你|不烦你|爱你|可爱|好棒|厉害|乖|别生气|消消气")


def conf() -> dict:
    return {
        "enabled": env.get_bool("AI_CHAT_ANNOY", True),
        "ignore": env.get_bool("AI_CHAT_ANNOY_IGNORE", True),
        "half": max(10, min(600, env.get_int("AI_CHAT_ANNOY_HALF_MINUTES", 60))),
        "sens": max(0.2, min(3.0, env.get_float("AI_CHAT_ANNOY_SENSITIVITY", 1.0))),
    }


def _load() -> dict:
    d = db.get(NS, KEY)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("users", {})
    d.setdefault("global", {"v": 0.0, "ts": time.time()})
    d.setdefault("events", [])
    return d


def _decayed(v: float, ts: float, now: float, half_minutes: float) -> float:
    dt = max(0.0, now - float(ts or now))
    return float(v or 0) * math.pow(0.5, dt / (half_minutes * 60))


def _norm(text: str) -> str:
    t = re.sub(r"\[[^\]]*\]|@\S+|\s+", "", str(text or ""))
    return re.sub(r"[，。！？!?~～、,.…]+", "", t)[:40]


def band(v: float) -> str:
    for th, name in BANDS:
        if v >= th:
            return name
    return ""


def note_at(gid, uid, name: str, text: str, *, owner: bool = False, napping: bool = False,
            now: float | None = None) -> dict:
    """有人艾特她（或叫她）：按「连着找 / 重复问 / 她在潜水 / 骂人 / 哄人」算这一下涨多少。返回 {level, delta, why}。"""
    c = conf()
    ts = time.time() if now is None else now
    if not c["enabled"] or not str(uid or ""):
        return {"level": 0.0, "delta": 0.0, "why": ""}
    uid = str(uid)
    with _lock:
        d = _load()
        u = d["users"].get(uid) or {"v": 0.0, "ts": ts, "ats": [], "name": name}
        v = _decayed(u.get("v", 0), u.get("ts", ts), ts, c["half"])
        ats = [a for a in (u.get("ats") or []) if ts - float(a[0]) <= REPEAT_WINDOW]
        recent = sum(1 for a in ats if ts - float(a[0]) <= RECENT_WINDOW)
        fp = _norm(text)
        delta, why = 1.5 + 1.0 * min(recent, 6), []
        if recent >= 3:
            why.append(f"10 分钟里找了你 {recent + 1} 次")
        if fp and len(fp) >= 2 and any(a[1] == fp for a in ats):
            delta += 6
            why.append("同一句话又来一遍")
        if napping:
            delta += 5
            why.append("你说了要安静还在叫你")
        if _INSULT.search(text or ""):
            delta += 8
            why.append("说话难听")
        delta *= c["sens"]
        if owner:
            delta *= 0.5
        if _SOOTHE.search(text or ""):
            delta -= 8 + v * 0.15
            why.append("在哄你")
        v = max(0.0, min(100.0, v + delta))
        ats.append([ts, fp])
        u.update(v=v, ts=ts, ats=ats[-20:], name=name or u.get("name", ""), gid=str(gid))
        if delta > 0 and why:
            u["why"] = "，".join(w for w in why if w != "在哄你")[:40]
        d["users"][uid] = u
        g = d["global"]
        gv = _decayed(g.get("v", 0), g.get("ts", ts), ts, GLOBAL_HALF_MINUTES)
        g.update(v=max(0.0, min(100.0, gv + max(0.0, delta) * 0.4 + 0.5)), ts=ts)
        if abs(delta) >= 4 or band(v) != band(v - delta):
            d["events"] = (d["events"] + [{"ts": ts, "uid": uid, "name": name, "d": round(delta, 1),
                                           "v": round(v, 1), "why": "，".join(why)[:40]}])[-MAX_EVENTS:]
        _prune(d, ts, c["half"])
        db.put(NS, KEY, d)
    return {"level": v, "delta": delta, "why": "，".join(why)}


def soothe(uid, amount: float = 10.0, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    c = conf()
    with _lock:
        d = _load()
        u = d["users"].get(str(uid))
        if not u:
            return
        u["v"] = max(0.0, _decayed(u.get("v", 0), u.get("ts", ts), ts, c["half"]) - amount)
        u["ts"] = ts
        db.put(NS, KEY, d)


def _prune(d: dict, now: float, half: float) -> None:
    for uid in list(d["users"]):
        u = d["users"][uid]
        if _decayed(u.get("v", 0), u.get("ts", now), now, half) < 0.5 and now - float(u.get("ts") or 0) > 6 * 3600:
            del d["users"][uid]


def level(uid, now: float | None = None) -> float:
    ts = time.time() if now is None else now
    u = (db.get(NS, KEY) or {}).get("users", {}).get(str(uid))
    if not u or not conf()["enabled"]:
        return 0.0
    return _decayed(u.get("v", 0), u.get("ts", ts), ts, conf()["half"])


def global_level(now: float | None = None) -> float:
    ts = time.time() if now is None else now
    if not conf()["enabled"]:
        return 0.0
    g = (db.get(NS, KEY) or {}).get("global") or {}
    return _decayed(g.get("v", 0), g.get("ts", ts), ts, GLOBAL_HALF_MINUTES)


def should_ignore(uid, owner: bool = False, rng: random.Random | None = None, now: float | None = None) -> bool:
    """烦透了：这条有一定概率直接不理（不叫醒她）。主人永远不会被不理。"""
    c = conf()
    if owner or not c["enabled"] or not c["ignore"]:
        return False
    v = level(uid, now)
    if v < 75:
        return False
    p = min(0.6, 0.2 + (v - 75) / 50)
    return (rng or random).random() < p


def short_reply(uid, now: float | None = None) -> bool:
    """很烦了：这一轮只回一句。"""
    return level(uid, now) >= 60


def random_factor(now: float | None = None) -> float:
    """群里整体吵：随机插话的概率乘这个数（越烦越少插嘴）。"""
    return max(0.3, 1.0 - global_level(now) / 140)


def note(uids, now: float | None = None, include_global: bool = True) -> str:
    """提示词里的一句「底子」：她此刻对谁有多烦、群里吵不吵。没什么好说的返回空串。"""
    if not conf()["enabled"]:
        return ""
    ts = time.time() if now is None else now
    d = db.get(NS, KEY) or {}
    rows = []
    for uid in dict.fromkeys(str(u) for u in uids if u):
        u = (d.get("users") or {}).get(uid)
        if not u:
            continue
        v = _decayed(u.get("v", 0), u.get("ts", ts), ts, conf()["half"])
        b = band(v)
        if v >= 35 and b:
            rows.append(f"你对 {u.get('name') or uid} {b}" + (f"（{u['why']}）" if u.get("why") else ""))
    g = global_level(ts) if include_global else 0.0
    if g >= 45:
        rows.append("群里今天吵得你有点心烦" if g < 70 else "被群里吵得脑仁疼")
    if not rows:
        return ""
    return ("【你的烦躁】" + "；".join(rows) + "。这是你此刻的底子，不是非要发火："
            "烦的时候自然回得短、冲、敷衍，或者懒得接；对方哄你、说正事，你也会慢慢缓过来。别说出「烦躁值」这种词。")


def snapshot(now: float | None = None) -> dict:
    """给面板看：总烦躁、对谁有多烦、最近的起伏。"""
    ts = time.time() if now is None else now
    d = db.get(NS, KEY) or {}
    half = conf()["half"]
    users = []
    for uid, u in (d.get("users") or {}).items():
        v = _decayed(u.get("v", 0), u.get("ts", ts), ts, half)
        if v >= 1:
            users.append({"uid": uid, "name": u.get("name") or uid, "level": round(v, 1), "band": band(v),
                          "why": u.get("why", ""), "ts": u.get("ts", 0)})
    users.sort(key=lambda x: -x["level"])
    return {"enabled": conf()["enabled"], "global": round(global_level(ts), 1), "global_band": band(global_level(ts)),
            "users": users[:12], "events": list(d.get("events") or [])[-15:][::-1]}
