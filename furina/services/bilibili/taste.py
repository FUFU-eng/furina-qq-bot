"""她在 B 站的口味：看过的东西真的会改变她。

- 每看完一个视频，按她打的兴趣分（0~10）给这条视频的 UP 主 / 分区 / 标签 / 从哪搜来的词 加减分：
  喜欢的越来越喜欢，无聊的越来越不想点；分数按半衰期慢慢淡（默认 10 天），口味会随时间漂移。
- 同一种东西 3 天里看太多会腻（再看就扣分），所以她不会永远只刷一样东西。
- 特别喜欢（≥8 分）或特别讨厌（≤2 分）的视频记成「看法」，连同当时的感受一起留着：
  以后群里聊到相关的 UP 主 / 话题，她能想起来「我前两天刷到过，那个 UP 主我挺喜欢的」。
- 刷 B 站时：一部分轮次按她现在最迷的东西去搜；给视频打分时也会告诉她「你最近的口味」。

纯本地计算，零 token。数据在 SQLite bili/taste，面板只读（snapshot()）。
"""
from __future__ import annotations

import math
import random
import re
import time

from ...core import db, env
from . import store

KEY = "taste"
BORED_WINDOW = 3 * 86400
BORED_AFTER = 5            # 3 天里同一样东西看超过这么多次就开始腻
MAX_TERMS = 300
MAX_OPINIONS = 80
WEIGHTS = {"up": 1.0, "tname": 0.6, "tag": 0.5, "kw": 0.4}
KIND_NAME = {"up": "UP主", "tname": "分区", "tag": "标签", "kw": "搜索词"}


def conf() -> dict:
    return {
        "enabled": env.get_bool("BILI_TASTE", True),
        "prob": max(0.0, min(1.0, env.get_float("BILI_TASTE_PROBABILITY", 0.25))),
        "half_days": max(1, min(90, env.get_int("BILI_TASTE_HALF_DAYS", 10))),
    }


def _load() -> dict:
    d = db.get(store.NS, KEY)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("terms", {})
    d.setdefault("opinions", [])
    return d


def _decayed(score: float, ts: float, now: float, half_days: float) -> float:
    return float(score or 0) * math.pow(0.5, max(0.0, now - float(ts or now)) / (half_days * 86400))


def _terms_of(item: dict) -> list[tuple[str, str]]:
    out = []
    if item.get("up"):
        out.append(("up", str(item["up"]).strip()))
    if item.get("tname"):
        out.append(("tname", str(item["tname"]).strip()))
    tags = item.get("tags") or ""
    if isinstance(tags, str):
        tags = re.split(r"[,，、|]", tags)
    for t in [str(x).strip() for x in tags if str(x).strip()][:4]:
        out.append(("tag", t))
    src = str(item.get("source") or "")
    m = re.search(r"[「“]([^」”]+)[」”]", src)
    if m:
        out.append(("kw", m.group(1)))
    seen, uniq = set(), []
    for kind, term in out:
        if 1 < len(term) <= 20 and term.lower() not in seen:
            seen.add(term.lower())
            uniq.append((kind, term))
    return uniq


def learn(items: list[dict], now: float | None = None) -> list[str]:
    """看完一轮：按兴趣分改口味，强烈的喜欢 / 讨厌记成看法。返回给日志看的变化。"""
    c = conf()
    if not c["enabled"] or not items:
        return []
    ts = time.time() if now is None else now
    notes = []

    def _u(d):
        d = d if isinstance(d, dict) else {}
        d.setdefault("terms", {})
        d.setdefault("opinions", [])
        for it in items:
            try:
                interest = max(0, min(10, int(it.get("interest") or 0)))
            except (TypeError, ValueError):
                continue
            base = (interest - 5) / 5.0
            for kind, term in _terms_of(it):
                key = f"{kind}:{term}"
                t = d["terms"].get(key) or {"kind": kind, "term": term, "score": 0.0, "ts": ts, "seen": []}
                score = _decayed(t.get("score", 0), t.get("ts", ts), ts, c["half_days"])
                seen = [s for s in (t.get("seen") or []) if ts - float(s) <= BORED_WINDOW]
                delta = base * WEIGHTS[kind]
                if len(seen) >= BORED_AFTER:
                    delta -= 0.3 * (len(seen) - BORED_AFTER + 1)  # 看腻了
                seen.append(ts)
                t.update(score=max(-10.0, min(10.0, score + delta)), ts=ts, seen=seen[-12:], n=int(t.get("n") or 0) + 1)
                d["terms"][key] = t
            if interest >= 8 or interest <= 2:
                d["opinions"].append({"ts": ts, "bvid": it.get("bvid", ""), "title": str(it.get("title") or "")[:60],
                                      "up": it.get("up", ""), "tname": it.get("tname", ""),
                                      "tags": [x for k, x in _terms_of(it) if k == "tag"],
                                      "interest": interest, "thought": str(it.get("thought") or "")[:80],
                                      "love": interest >= 8})
                notes.append(f"{'记住了喜欢' if interest >= 8 else '记住了不喜欢'}《{str(it.get('title') or '')[:16]}》")
        if len(d["terms"]) > MAX_TERMS:
            ranked = sorted(d["terms"].items(), key=lambda kv: abs(_decayed(kv[1].get("score", 0), kv[1].get("ts", ts),
                                                                             ts, c["half_days"])))
            for k, _v in ranked[:len(d["terms"]) - MAX_TERMS]:
                del d["terms"][k]
        d["opinions"] = d["opinions"][-MAX_OPINIONS:]
        return d

    db.update(store.NS, KEY, _u)
    return notes


def ranked(now: float | None = None) -> list[dict]:
    c = conf()
    ts = time.time() if now is None else now
    out = []
    for t in _load()["terms"].values():
        s = _decayed(t.get("score", 0), t.get("ts", ts), ts, c["half_days"])
        recent = sum(1 for x in (t.get("seen") or []) if ts - float(x) <= BORED_WINDOW)
        out.append({"kind": t.get("kind"), "term": t.get("term"), "score": round(s, 2), "n": t.get("n", 0),
                    "bored": recent >= BORED_AFTER})
    out.sort(key=lambda x: -x["score"])
    return out


def likes(n: int = 5, now: float | None = None) -> list[dict]:
    return [t for t in ranked(now) if t["score"] >= 0.8 and not t["bored"]][:n]


def dislikes(n: int = 4, now: float | None = None) -> list[dict]:
    return sorted([t for t in ranked(now) if t["score"] <= -0.8], key=lambda x: x["score"])[:n]


def bored(n: int = 3, now: float | None = None) -> list[dict]:
    return [t for t in ranked(now) if t["bored"]][:n]


def _fmt(t: dict) -> str:
    return f"UP主「{t['term']}」" if t["kind"] == "up" else f"「{t['term']}」"


def taste_line(now: float | None = None) -> str:
    """一句话说她现在的口味；还没形成口味返回空串。"""
    if not conf()["enabled"]:
        return ""
    lk, dk, bd = likes(5, now), dislikes(3, now), bored(3, now)
    parts = []
    if lk:
        parts.append("最近在迷 " + "、".join(_fmt(t) for t in lk))
    if bd:
        parts.append("这几天看太多有点腻了的 " + "、".join(_fmt(t) for t in bd))
    if dk:
        parts.append("不太爱看 " + "、".join(_fmt(t) for t in dk))
    return "；".join(parts)


def pick_keyword(rng: random.Random | None = None, now: float | None = None) -> str:
    """按她现在最迷的东西挑一个去搜（分越高越容易挑中）；没有就返回空串。"""
    pool = [t for t in likes(8, now) if t["kind"] in ("tag", "kw", "tname", "up")]
    if not pool:
        return ""
    r = rng or random
    total = sum(t["score"] for t in pool)
    x = r.random() * total
    for t in pool:
        x -= t["score"]
        if x <= 0:
            return t["term"]
    return pool[-1]["term"]


def recall(text: str, limit: int = 2, now: float | None = None) -> list[str]:
    """群里聊到的东西和她的看法对得上（UP 主名、分区、标签、标题里的词）：想起当时的感受。"""
    if not conf()["enabled"] or not text:
        return []
    ts = time.time() if now is None else now
    low = str(text).lower()
    hits = []
    for o in reversed(_load()["opinions"]):
        keys = [o.get("up"), o.get("tname"), *(o.get("tags") or [])]
        if any(k and len(str(k)) >= 2 and str(k).lower() in low for k in keys):
            hits.append(o)
        if len(hits) >= limit:
            break
    out = []
    for o in hits:
        days = int((ts - float(o.get("ts") or ts)) // 86400)
        when = "今天" if days == 0 else "昨天" if days == 1 else f"{days} 天前"
        up = f"UP主「{o['up']}」的" if o.get("up") else ""
        feel = "挺喜欢" if o.get("love") else "不太喜欢"
        out.append(f"{when}刷到过{up}《{o.get('title', '')}》，你{feel}" + (f"：{o['thought']}" if o.get("thought") else ""))
    return out


def snapshot(now: float | None = None) -> dict:
    """给面板看：在迷什么、腻了什么、讨厌什么、最近记下的看法。"""
    ts = time.time() if now is None else now
    return {"enabled": conf()["enabled"], "likes": likes(8, ts), "dislikes": dislikes(5, ts), "bored": bored(5, ts),
            "opinions": list(_load()["opinions"])[-10:][::-1], "line": taste_line(ts)}
