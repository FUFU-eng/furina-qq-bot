"""面板读写的业务数据：群列表、功能权限、统计、群友次数与上限、好感、角色、共享设定、各功能设置。

全部直接读写 v2 的 SQLite（与机器人同一个库，WAL 模式可并发）与 .env；机器人热读取，不需要重启。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from furina.core import db, docfile, paths
from furina.core.permissions import DEFAULT_ON_FEATURES, FEATURES
from furina.core.usage import effective_limit, parse_limit_overrides

from . import envfile, napcat

KINDS = [
    ("chat", "艾特回复", "#ffd166"), ("auto", "自动接话", "#6ad4ff"), ("search", "搜图", "#b48cf2"),
    ("upscale", "强化", "#3ecf8e"), ("cmd_image", "指令发图", "#f59e0b"), ("poke", "戳一戳", "#fb7eb8"),
    ("jm", "JM 下载", "#f87171"), ("miyoushe_sign", "米游社签到", "#38bdf8"), ("game_query", "游戏战绩与图鉴", "#6ad4ff"),
    ("proactive", "冷场开话题", "#a3e635"), ("voice", "语音消息", "#f472b6"), ("bili_share", "B站分享", "#fb7299"),
    ("websearch", "联网搜索", "#34d399"), ("miss", "惦记群友", "#f9a8d4"), ("pic_inbox", "收图筛选", "#7dd3fc"),
    ("memewiki", "查梗", "#facc15"), ("maker", "做文件", "#a78bfa"),
    ("bili_pics", "B站画师图片", "#fb7299"),
]


# ---------------- 排序 ----------------
def _initial(text: str) -> str:
    first = (text or "").strip()[:1]
    if not first:
        return "~"
    if first.isascii():
        return first.upper() if first.isalpha() else "~"
    try:
        from pypinyin import lazy_pinyin

        py = lazy_pinyin(first)
        return py[0][0].upper() if py and py[0] else "Z"
    except Exception:
        return "Z"


def sort_key(text: str):
    ini = _initial(text)
    return (1 if ini == "~" else 0, ini, (text or "").lower())


# ---------------- 群 ----------------
def _members_doc() -> dict:
    d = docfile.load("group_members.json")
    return d if isinstance(d, dict) else {}


def _fengshen_doc() -> dict:
    d = docfile.load("fengshen_data.json")
    return d if isinstance(d, dict) else {}


def group_name(gid, live: dict | None = None) -> str:
    gid = str(gid)
    live = napcat.groups.get() if live is None else live
    if gid in live:
        return live[gid]
    return (_members_doc().get("group_names") or {}).get(gid) or f"群 {gid}"


def groups_list() -> list[dict]:
    """当前登录 QQ 真实所在的群（NapCat 实时列表优先，离线时用成员缓存兜底）。"""
    live = napcat.groups.get()
    if live:
        gids = set(live)
    else:
        gids = {str(g) for g in (_members_doc().get("groups") or {})} or {str(g) for g in _fengshen_doc()}
    rows = [{"gid": g, "name": group_name(g, live)} for g in gids]
    rows.sort(key=lambda r: sort_key(r["name"]))
    return rows


def live_group_ids() -> set[str]:
    return set(napcat.groups.get())


# ---------------- 功能权限 ----------------
def permission_features() -> list[dict]:
    return [{"key": k, "label": v, "default": k in DEFAULT_ON_FEATURES} for k, v in FEATURES.items()]


def get_permissions(gid: str) -> dict:
    g = db.get("perm", str(gid))
    feats = (g or {}).get("features") if isinstance(g, dict) else None
    feats = feats if isinstance(feats, dict) else {}
    return {"enabled": bool((g or {}).get("enabled", True)) if isinstance(g, dict) else True,
            "features": {k: bool(feats.get(k, k in DEFAULT_ON_FEATURES)) for k in FEATURES}}


def save_permissions(gid: str, features: dict) -> dict:
    gid = str(gid)
    old = get_permissions(gid)["features"]
    new = {k: bool(features.get(k, False)) for k in FEATURES}
    db.update("perm", gid, lambda g: {"enabled": (g or {}).get("enabled", True) if isinstance(g, dict) else True,
                                       "features": new})
    skipped = None
    if new.get("miyoushe") and not old.get("miyoushe"):
        skipped = baseline_miyoushe(gid)
    return {"features": new, "miyoushe_baseline": skipped}


def baseline_miyoushe(gid: str) -> int:
    """米游社推送从关到开：写入该群的时间基线（机器人每轮轮询都会重读），早于它的历史资讯不补推。"""
    gid = str(gid)
    baselines = docfile.load("miyoushe_baselines.json", {})
    baselines = baselines if isinstance(baselines, dict) else {}
    baselines[gid] = time.time()
    docfile.save("miyoushe_baselines.json", baselines)
    known = docfile.load("miyoushe_known_groups.json", [])
    known = [str(g) for g in (known if isinstance(known, list) else [])]
    if gid not in known:
        docfile.save("miyoushe_known_groups.json", sorted(known + [gid]))
    seen = docfile.load("miyoushe_seen.json", {})
    return sum(len(v) for v in seen.values()) if isinstance(seen, dict) else 0


# ---------------- 统计 ----------------
def _today() -> str:
    return _dt.date.today().isoformat()


def _stat_rows(since: str) -> dict[str, dict[str, dict[str, int]]]:
    """{日期: {群: {功能: 次数}}}（只取 stat:*）。"""
    out: dict = {}
    for day, kind, gid, _uid, count in db.usage_rows(since):
        if not kind.startswith("stat:"):
            continue
        k = kind[5:]
        g = out.setdefault(day, {}).setdefault(str(gid), {})
        g[k] = g.get(k, 0) + int(count or 0)
    return out


def stats(days: int) -> dict:
    days = max(1, min(40, int(days)))
    today = _dt.date.today()
    trend_days = 7 if days == 1 else days
    since = (today - _dt.timedelta(days=max(days, trend_days) - 1)).isoformat()
    rows = _stat_rows(since)
    live = napcat.groups.get()
    keep = (lambda g: g in live) if live else (lambda g: True)
    start = (today - _dt.timedelta(days=days - 1)).isoformat()
    agg: dict[str, dict[str, int]] = {}
    for day, groups in rows.items():
        if day < start:
            continue
        for gid, kinds in groups.items():
            if keep(gid):
                b = agg.setdefault(gid, {})
                for k, v in kinds.items():
                    b[k] = b.get(k, 0) + v
    known = [k for k, _, _ in KINDS]
    total = sum(v for g in agg.values() for k, v in g.items() if k in known)
    trend = []
    for i in range(trend_days - 1, -1, -1):
        d = (today - _dt.timedelta(days=i)).isoformat()
        n = sum(v for gid, kinds in rows.get(d, {}).items() if keep(gid) for k, v in kinds.items() if k in known)
        trend.append({"date": d[5:], "value": n, "today": i == 0})
    if trend_days == 30:
        first = 0
        while first < len(trend) - 7 and trend[first]["value"] == 0:
            first += 1
        trend = trend[first:]
    kinds = [{"key": k, "label": lbl, "color": col, "value": sum(g.get(k, 0) for g in agg.values())} for k, lbl, col in KINDS]
    groups = []
    for gid, b in agg.items():
        gt = sum(b.get(k, 0) for k in known)
        if gt > 0:
            groups.append({"gid": gid, "name": group_name(gid, live), "total": gt,
                           "items": sorted([{"label": lbl, "color": col, "value": b.get(k, 0)}
                                            for k, lbl, col in KINDS if b.get(k, 0) > 0], key=lambda x: -x["value"])})
    groups.sort(key=lambda g: (-g["total"], g["gid"]))
    return {"days": days, "total": total, "trend": trend, "kinds": kinds, "groups": groups}


def today_chat_total() -> int:
    return stats(1)["total"]


def model_usage_today() -> dict[str, int]:
    out: dict[str, int] = {}
    for _day, kind, _g, _u, count in db.usage_rows(_today()):
        if kind.startswith("model:"):
            name = kind[6:]
            out[name] = out.get(name, 0) + int(count or 0)
    return dict(sorted(out.items(), key=lambda x: -x[1]))


# ---------------- 群友次数 / 上限 ----------------
def unlimited_qq() -> set[str]:
    v = envfile.values()
    return {q for q in ((v.get("AI_CHAT_GROUP_OWNER_QQ") or "").strip(), (v.get("AI_CHAT_SPECIAL_QQ") or "").strip()) if q}


def unified_limit() -> int:
    try:
        return int(envfile.get("AI_CHAT_DAILY_CHAT_LIMIT", "30"))
    except ValueError:
        return 30


def overrides() -> dict:
    return parse_limit_overrides(envfile.get("AI_CHAT_DAILY_LIMIT_OVERRIDES", ""))


def set_unified_limit(value: int) -> None:
    value = max(0, min(999, int(value)))
    envfile.set_values({"AI_CHAT_DAILY_CHAT_LIMIT": str(value)})


def members() -> dict:
    today = _today()
    used: dict[str, int] = {}
    for _d, kind, gid, uid, count in db.usage_rows(today, ("chat",)):
        used[f"{gid}:{uid}"] = used.get(f"{gid}:{uid}", 0) + int(count or 0)
    doc = (_members_doc().get("groups") or {})
    live = napcat.groups.get()
    real = {g["gid"] for g in groups_list()}
    mem = {g: u for g, u in doc.items() if not real or str(g) in real}
    if not mem:  # 成员缓存还没生成：用封神榜里的昵称兜底
        for gid, gd in _fengshen_doc().items():
            if isinstance(gd, dict) and (not real or gid in real):
                m = {uid: (us.get("nickname") or uid) for uid, us in (gd.get("stats") or {}).items() if isinstance(us, dict)}
                if m:
                    mem[gid] = m
    unl, ov, uni = unlimited_qq(), overrides(), unified_limit()
    rows = []
    for gid, users in mem.items():
        if not isinstance(users, dict):
            continue
        for uid, nick in users.items():
            n = used.get(f"{gid}:{uid}", 0)
            if uid in unl:
                limit = 0
            else:
                limit = effective_limit(ov, uni, gid, uid)
            rows.append({"gid": str(gid), "uid": str(uid), "nick": nick or uid, "used": n,
                         "limit": limit, "remain": None if limit <= 0 else max(0, limit - n),
                         "fixed": uid in unl, "initial": _initial(nick or uid)})
    groups = [{"gid": g, "name": group_name(g, live)} for g in {r["gid"] for r in rows}]
    groups.sort(key=lambda g: sort_key(g["name"]))
    return {"unified": uni, "groups": groups, "rows": rows}


# ---------------- 群友活跃（按人用量） ----------------
USAGE_COLS = [  # (键, 列名, 颜色, 默认权重)
    ("chat", "艾特聊天", "#ffd166", 1.0), ("jm", "JM 本子", "#f87171", 1.0), ("upscale_img", "强化（张）", "#3ecf8e", 1.0),
    ("search", "搜图", "#b48cf2", 1.0), ("poke", "戳一戳", "#fb7eb8", 0.0),
]
_PREFS_NS = "panel_prefs"


def usage_weights() -> dict[str, float]:
    saved = db.get(_PREFS_NS, "usage_weights") or {}
    out = {}
    for k, _l, _c, dv in USAGE_COLS:
        try:
            out[k] = max(0.0, float(saved.get(k, dv))) if isinstance(saved, dict) else dv
        except (TypeError, ValueError):
            out[k] = dv
    return out


def save_usage_weights(weights: dict) -> dict[str, float]:
    cur = usage_weights()
    for k in cur:
        if k in (weights or {}):
            try:
                cur[k] = max(0.0, min(100.0, float(weights[k])))
            except (TypeError, ValueError):
                pass
    db.put(_PREFS_NS, "usage_weights", cur)
    return cur


def usage_total() -> float:
    try:
        return max(0.0, float(db.get(_PREFS_NS, "usage_total") or 0))
    except (TypeError, ValueError):
        return 0.0


def save_usage_total(value) -> float:
    try:
        v = max(0.0, min(1_000_000.0, float(value)))
    except (TypeError, ValueError):
        v = 0.0
    db.put(_PREFS_NS, "usage_total", v)
    return v


def _usage_names() -> dict[str, str]:
    """QQ → 昵称：群名片缓存 > 机器人记下的昵称 > 封神榜。"""
    names: dict[str, str] = {}
    for _gid, gd in _fengshen_doc().items():
        if isinstance(gd, dict):
            for uid, us in (gd.get("stats") or {}).items():
                if isinstance(us, dict) and us.get("nickname"):
                    names[str(uid)] = str(us["nickname"])
    for uid, rec in (db.items("usage_names") or {}).items():
        if isinstance(rec, dict) and rec.get("name"):
            names[str(uid)] = str(rec["name"])
    for _gid, users in ((_members_doc().get("groups") or {}).items()):
        if isinstance(users, dict):
            for uid, nick in users.items():
                if nick:
                    names[str(uid)] = str(nick)
    return names


def member_usage(days: int = 30, gid: str = "", total: float = 0.0) -> dict:
    """每个群友用了机器人多少：艾特聊天、JM、强化、搜图、戳一戳，加权算出占比（填了总额再按占比折算）。
    days<=0 = 全部历史；gid 只看某个群；total>0 时按占比算每人应摊多少钱。"""
    from furina.core import usage as _usage
    days = int(days or 0)
    raw = _usage.who_totals(days, gid or None)
    v = envfile.values()
    bots = {q.strip() for q in ((v.get("BOT_QQ") or "") + "," + (v.get("PEER_BOTS") or "")).split(",") if q.strip()}
    weights = usage_weights()
    names = _usage_names()
    rows = []
    for uid, c in raw.items():
        if uid in bots:
            continue
        counts = {k: int(c.get(k, 0)) for k, *_ in USAGE_COLS}
        counts["upscale"] = int(c.get("upscale", 0))
        if not any(counts.values()):
            continue
        score = sum(counts[k] * weights.get(k, 0.0) for k, *_ in USAGE_COLS)
        rows.append({"uid": uid, "nick": names.get(uid) or uid, "counts": counts, "score": round(score, 2)})
    sum_score = sum(r["score"] for r in rows)
    for r in rows:
        r["share"] = (r["score"] / sum_score) if sum_score > 0 else 0.0
        r["pay"] = round(float(total) * r["share"], 2) if total and total > 0 else None
    rows.sort(key=lambda r: (-r["score"], -sum(r["counts"].values()), r["uid"]))
    totals = {k: sum(r["counts"].get(k, 0) for r in rows) for k, *_ in USAGE_COLS}
    totals["upscale"] = sum(r["counts"].get("upscale", 0) for r in rows)
    first = _usage.who_first_day()
    groups = [{"gid": g["gid"], "name": g["name"]} for g in groups_list()]
    return {"days": days, "gid": gid or "", "rows": rows, "totals": totals, "weights": weights, "score": round(sum_score, 2),
            "cols": [{"key": k, "label": lbl, "color": col} for k, lbl, col, _ in USAGE_COLS],
            "first": first, "groups": groups}


def favor_line(uid: str) -> str:
    rec = db.get("favor", str(uid))
    if not isinstance(rec, dict):
        return ""
    try:
        fav = float(rec.get("favor", 30.0))
    except (TypeError, ValueError):
        return ""
    lvl = "戒备"
    for floor, name in ((99, "满好感"), (90, "格外偏爱"), (75, "很喜欢"), (60, "要好"), (45, "朋友"), (30, "认识"), (15, "初识")):
        if fav >= floor:
            lvl = name
            break
    days = ""
    try:
        d0 = _dt.date.fromisoformat(str(rec.get("first") or ""))
        days = f" · 认识 {(_dt.date.today() - d0).days} 天"
    except ValueError:
        pass
    return f"{lvl}（{fav:.0f} 分{days}）"


def user_limit(gid: str, uid: str) -> dict:
    gid, uid = str(gid), str(uid)
    ov = overrides()
    if f"{gid}:{uid}" in ov:
        mode_v, scope, src = ov[f"{gid}:{uid}"], "group", "本群专属"
    elif uid in ov:
        mode_v, scope, src = ov[uid], "all", "所有群"
    else:
        mode_v, scope, src = None, "group", "统一"
    eff = effective_limit(ov, unified_limit(), gid, uid)
    mode = "follow" if mode_v is None else ("unlimited" if mode_v <= 0 else "custom")
    return {"gid": gid, "uid": uid, "group_name": group_name(gid), "fixed": uid in unlimited_qq(),
            "effective": eff, "source": src, "mode": mode, "scope": scope,
            "value": mode_v if mode == "custom" else (eff if eff > 0 else 30), "favor": favor_line(uid)}


def save_user_limit(gid: str, uid: str, mode: str, scope: str, value: int) -> str:
    gid, uid = str(gid), str(uid)
    ov = overrides()
    ov.pop(f"{gid}:{uid}", None)
    ov.pop(uid, None)
    key = f"{gid}:{uid}" if scope == "group" else uid
    where = "仅本群" if scope == "group" else "所有群"
    if mode == "custom":
        ov[key] = max(1, min(999, int(value)))
        desc = f"{ov[key]} 次/天（{where}）"
    elif mode == "unlimited":
        ov[key] = 0
        desc = f"无限次（{where}）"
    else:
        desc = "跟随统一上限"
    envfile.set_values({"AI_CHAT_DAILY_LIMIT_OVERRIDES": ",".join(f"{k}:{v}" for k, v in sorted(ov.items()))})
    return desc


# ---------------- 角色 / 账号 / 共享设定 ----------------
def roles() -> list[dict]:
    d = db.get("persona", "roles")
    return list(d["roles"]) if isinstance(d, dict) and isinstance(d.get("roles"), list) else []


def save_roles(items: list[dict]) -> None:
    clean = []
    ids = set()
    for r in items:
        rid = str(r.get("id") or "").strip() or f"role_{int(time.time() * 1000)}"
        if rid in ids:
            raise ValueError(f"角色 ID 重复：{rid}")
        ids.add(rid)
        name = str(r.get("name") or "").strip()
        if not name:
            raise ValueError("角色名称不能为空")
        clean.append({**{k: v for k, v in r.items() if k not in ("id", "name", "prompt")},
                      "id": rid, "name": name, "prompt": str(r.get("prompt") or "")})
    db.put("persona", "roles", {"roles": clean})


def profiles() -> list[dict]:
    d = db.get("persona", "profiles")
    return list(d) if isinstance(d, list) else []


def save_profile(qq: str, nickname: str, role_id: str = "") -> None:
    qq = str(qq).strip()
    if not qq.isdigit():
        raise ValueError("QQ 号格式不正确（需纯数字）")
    items = profiles()
    for p in items:
        if str(p.get("qq")) == qq:
            p.update(nickname=nickname, role_id=role_id)
            break
    else:
        items.append({"qq": qq, "nickname": nickname, "role_id": role_id})
    db.put("persona", "profiles", items)


def delete_profile(qq: str) -> bool:
    items = profiles()
    left = [p for p in items if str(p.get("qq")) != str(qq)]
    if len(left) == len(items):
        return False
    db.put("persona", "profiles", left)
    return True


def shared_lore() -> dict:
    d = db.get("persona", "shared_lore")
    items = d.get("items") if isinstance(d, dict) else None
    return {"items": list(items) if isinstance(items, list) else [], "seeded": d is not None}


def save_lore_item(item: dict) -> dict:
    d = shared_lore()
    if not d["seeded"]:
        raise ValueError("机器人还没初始化共享设定（启动一次机器人后再编辑）")
    items = d["items"]
    payload = {"name": str(item.get("name") or "").strip() or "未命名设定",
               "keywords": str(item.get("keywords") or "").strip(),
               "content": str(item.get("content") or "").strip(),
               "role_ids": sorted({str(x) for x in (item.get("role_ids") or [])})}
    all_ids = {str(r.get("id")) for r in roles()}
    if all_ids and set(payload["role_ids"]) >= all_ids:
        payload["role_ids"] = []  # 全选 = 对全部角色生效
    iid = item.get("id")
    for it in items:
        if iid and it.get("id") == iid:
            it.update(payload)
            payload = it
            break
    else:
        payload["id"] = "lore_" + uuid.uuid4().hex[:8]
        items.append(payload)
    db.put("persona", "shared_lore", {"items": items})
    return payload


def delete_lore_item(iid: str) -> None:
    d = shared_lore()
    db.put("persona", "shared_lore", {"items": [it for it in d["items"] if it.get("id") != iid]})


# ---------------- Pixiv ----------------
JOB_LABELS = {"started": "已领取", "running": "发送中", "waiting": "等待榜单公布", "completed": "完成",
              "partial": "部分成功", "skipped": "已跳过", "stopped": "已停止", "interrupted": "已中断"}
PIXIV_DIR = paths.DATA_DIR / "pixiv"
PUSH_NOW = PIXIV_DIR / "push_now.json"


def pixiv_settings() -> dict:
    from furina.services.pixiv import settings

    return settings.load()


def save_pixiv_settings(data: dict) -> dict:
    from furina.services.pixiv import settings

    return settings.save(data)


def validate_pixiv_aliases(aliases) -> str:
    from furina.services.pixiv import settings

    settings.validate({"aliases": aliases})
    return f"格式正确，共 {len(aliases)} 条自定义映射"


def pixiv_jobs() -> dict:
    rows, err = [], ""
    path = PIXIV_DIR / "jobs.sqlite3"
    if path.exists():
        try:
            con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                for k, s, d, u in con.execute("SELECT key,status,detail,updated FROM jobs ORDER BY updated DESC LIMIT 60"):
                    rows.append({"key": k, "status": s, "label": JOB_LABELS.get(s, s), "detail": d or "", "updated": u})
            finally:
                con.close()
        except sqlite3.Error:
            err = "运行记录暂时不可读（数据库忙），请稍后刷新。"
    used = 0
    cache = PIXIV_DIR / "temporary"
    if cache.exists():
        for p in cache.rglob("*"):
            try:
                used += p.stat().st_size if p.is_file() else 0
            except OSError:
                pass
    pending = None
    try:
        pending = json.loads(PUSH_NOW.read_text(encoding="utf-8")) if PUSH_NOW.exists() else None
    except (OSError, ValueError):
        pending = {"broken": True}
    return {"rows": rows, "error": err, "cache_mb": round(used / 1024 / 1024, 1), "pending": pending}


def pixiv_push_now(batches: list[int], groups: list[str] | None, after: str = "") -> dict:
    """手动补推：写 data/pixiv/push_now.json，机器人下一个 tick 领取（避开定时批次前 45 分钟、后 10 分钟）。"""
    batches = sorted({int(b) for b in batches})
    if not batches or any(not 0 <= b < 5 for b in batches):
        raise ValueError("请选择要补推的批次（第 1–5 批）")
    req = {"day": _today(), "batches": batches, "request_id": "panel_" + uuid.uuid4().hex[:10]}
    if groups:
        if any(not str(g).isdigit() for g in groups):
            raise ValueError("群号格式不正确")
        req["groups"] = [str(g) for g in groups]
    if after:
        try:
            hh, mm = after.split(":")
            _dt.time(int(hh), int(mm))
        except ValueError:
            raise ValueError("开始时间格式应为 HH:MM")
        req["after"] = after
    from furina.services.pixiv.settings import atomic_json

    atomic_json(PUSH_NOW, req)
    return req


def pixiv_cancel_push() -> bool:
    try:
        PUSH_NOW.unlink()
        return True
    except FileNotFoundError:
        return False


# ---------------- 定时提醒 ----------------
def reminder_settings() -> dict:
    from furina.services.reminder import settings

    return settings.load()


def save_reminder_settings(data: dict) -> dict:
    from furina.services.reminder import settings

    return settings.save(data)


def reminder_counts() -> dict:
    try:
        con = sqlite3.connect(Path(paths.DB_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            return dict(con.execute("SELECT state,COUNT(*) FROM reminders GROUP BY state").fetchall())
        finally:
            con.close()
    except sqlite3.Error:
        return {"_missing": 1}


# ---------------- JM 下载 ----------------
# 配置存在 .env（键名 JM_*），机器人热读取；缓存目录是纯磁盘数据，这里只读不写，
# 删除某本 / 清空缓存由面板直接删目录（与旧版控制面板行为一致）。
JM_ENV_KEYS = (
    "JM_ENABLED", "JM_CACHE_DIR", "JM_PROXY", "JM_TIMEOUT", "JM_MAX_IMAGES",
    "JM_DAILY_USER_LIMIT", "JM_DAILY_GROUP_LIMIT", "JM_PROGRESS_INTERVAL",
)
_JM_DEFAULTS = {
    "JM_ENABLED": "true",
    "JM_CACHE_DIR": str(paths.DATA_DIR / "jm"),
    "JM_PROXY": "",
    "JM_TIMEOUT": "900",
    "JM_MAX_IMAGES": "0",
    "JM_DAILY_USER_LIMIT": "5",
    "JM_DAILY_GROUP_LIMIT": "20",
    "JM_PROGRESS_INTERVAL": "45",
}


def jm_cache_dir() -> Path:
    raw = envfile.get("JM_CACHE_DIR", "").strip() or _JM_DEFAULTS["JM_CACHE_DIR"]
    return Path(raw)


def _dir_size(path: Path, cap: int = 20000) -> int:
    total, seen = 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            seen += 1
            if seen > cap:
                return total
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def jm_cache() -> dict:
    """缓存概况 + 每本明细，供面板显示与逐条清理。"""
    root = jm_cache_dir()
    rows: list[dict] = []
    if root.is_dir():
        for child in root.iterdir():
            try:
                if not child.is_dir():
                    continue
                size = _dir_size(child)
                mtime = child.stat().st_mtime
            except OSError:
                continue
            pdf = child / "pdf" / f"{child.name}.pdf"
            try:
                has_pdf = pdf.is_file() and pdf.stat().st_size > 1024
            except OSError:
                has_pdf = False
            rows.append({
                "album_id": child.name,
                "size_mb": round(size / 1024 / 1024, 1),
                "has_pdf": has_pdf,
                "mtime": mtime,
                "updated": _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return {
        "dir": str(root),
        "albums": rows,
        "count": len(rows),
        "with_pdf": sum(1 for r in rows if r["has_pdf"]),
        "used_mb": round(sum(r["size_mb"] for r in rows), 1),
    }


def clear_jm_album(album_id: str) -> bool:
    album_id = str(album_id or "").strip()
    if not album_id.isdigit():
        raise ValueError("车号必须是纯数字")
    d = jm_cache_dir() / album_id
    if not d.is_dir():
        return False
    import shutil

    shutil.rmtree(d, ignore_errors=True)
    return True


def clear_jm_cache() -> int:
    root = jm_cache_dir()
    if not root.is_dir():
        return 0
    import shutil

    removed = 0
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def jm_settings() -> dict:
    """当前 JM 配置（值以字符串形式给前端，方便直接塞进输入框）+ 缓存概况。"""
    out = {"settings": {k: envfile.get(k, _JM_DEFAULTS[k]) or _JM_DEFAULTS[k] for k in JM_ENV_KEYS}}
    out["defaults"] = dict(_JM_DEFAULTS)
    out["cache"] = jm_cache()
    return out


def save_jm_settings(data: dict) -> dict:
    """校验并写入 .env。只有需要重启才生效的项才拒改；这里全部热生效。"""
    updates: dict[str, str] = {}
    enabled = data.get("enabled")
    if isinstance(enabled, bool):
        updates["JM_ENABLED"] = "true" if enabled else "false"
    cache_dir = str(data.get("cache_dir", "")).strip()
    if cache_dir:
        if any(c in cache_dir for c in ("\n", "\r")):
            raise ValueError("缓存目录不能包含换行")
        updates["JM_CACHE_DIR"] = cache_dir
    if "proxy" in data:
        updates["JM_PROXY"] = str(data.get("proxy", "")).strip()
    ranges = {
        "timeout": ("JM_TIMEOUT", 30, 7200, "单本超时"),
        "max_images": ("JM_MAX_IMAGES", 0, 100000, "页数上限"),
        "daily_user_limit": ("JM_DAILY_USER_LIMIT", 0, 1000, "每人每日上限"),
        "daily_group_limit": ("JM_DAILY_GROUP_LIMIT", 0, 10000, "每群每日上限"),
        "progress_interval": ("JM_PROGRESS_INTERVAL", 5, 3600, "进度提示间隔"),
    }
    for field, (key, low, high, label) in ranges.items():
        if field not in data:
            continue
        try:
            v = int(data[field])
        except (TypeError, ValueError):
            raise ValueError(f"{label}必须是整数")
        if not low <= v <= high:
            raise ValueError(f"{label}必须在 {low}–{high} 之间")
        updates[key] = str(v)
    if updates:
        envfile.set_values(updates)
    return jm_settings()


# ---------------- GsCore compatibility endpoints ----------------
def mihoyo_settings() -> dict:
    from .games import snapshot
    return snapshot()


def save_mihoyo_settings(body: dict) -> dict:
    from .games import save_settings
    return save_settings(body)


def delete_mihoyo_account(qq: str) -> bool:
    raise ValueError("旧账号删除接口已停用。请在 GsCore 官方控制台管理新账号；旧资料保留供回滚。")


# ---------------- 角色内置人设重置 & 芙芙的小本本 ----------------
def reset_role_prompt(role_id: str) -> dict:
    """把指定角色的提示词恢复为代码内置最新版并立即写入 SQLite（热生效）。"""
    import ast
    from furina.core import roles as _roles
    rid = str(role_id or "furina").strip() or "furina"
    builtin_prompt = ""
    builtin_name = ""
    if rid in ("furina", "furina_black"):
        rid = "furina"
        try:
            src = (paths.ROOT / "furina" / "plugins" / "chat" / "persona.py").read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id == "PERSONA":
                            builtin_prompt = ast.literal_eval(node.value)
                            builtin_name = "芙宁娜"
                            break
        except Exception:
            pass
    if not builtin_prompt:
        for b in getattr(_roles, "_BUILTIN_ROLES", ()):
            if b["id"] == rid:
                builtin_prompt = b["prompt"]
                builtin_name = b["name"]
                break
    if not builtin_prompt:
        raise ValueError(f"未找到角色 {rid} 的内置人设")
    cur_roles = roles()
    found = False
    for item in cur_roles:
        if item.get("id") == rid:
            item["prompt"] = builtin_prompt
            if builtin_name:
                item["name"] = builtin_name
            found = True
    if not found:
        cur_roles.insert(0, {"id": rid, "name": builtin_name or "芙宁娜", "prompt": builtin_prompt, "triggers": [], "lore_ids": []})
    save_roles(cur_roles)
    return {"id": rid, "name": builtin_name or "芙宁娜", "prompt": builtin_prompt}


def black_book() -> list[dict]:
    from furina.plugins.chat import fun_features
    return fun_features.list_black_book()


def add_black_book(who: str, nickname: str, event: str, gid: str = "", uid: str = "") -> dict:
    from furina.plugins.chat import fun_features
    return fun_features.add_black_book_entry(who=who, nickname=nickname, event=event, gid=gid, uid=uid)


def delete_black_book(entry_id: str) -> bool:
    from furina.plugins.chat import fun_features
    return fun_features.delete_black_book_entry(entry_id)


# ---------------- 全群共用的B站画师图片订阅 ----------------
def bili_pics_data() -> dict:
    from furina.services.bilibili import pictures
    return pictures.snapshot()


def save_bili_pics(body: dict) -> dict:
    updates = {}
    if set(body) - {"enabled", "interval_minutes"}:
        raise ValueError("图片订阅设置字段不正确")
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ValueError("启用状态必须是开或关")
        updates["BILI_PICS_ENABLED"] = "true" if body["enabled"] else "false"
    if "interval_minutes" in body:
        value = body["interval_minutes"]
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 1440:
            raise ValueError("检查间隔请填5到1440之间的整数分钟")
        updates["BILI_PICS_INTERVAL_MINUTES"] = str(value)
    if updates:
        envfile.set_values(updates)
    return bili_pics_data()


def add_bili_pic(target: str) -> dict:
    import asyncio
    from furina.services.bilibili import pictures
    return asyncio.run(pictures.subscribe(target))


def remove_bili_pic(uid: str) -> bool:
    from furina.services.bilibili import pictures
    return pictures.unsubscribe(pictures.parse_uid(uid))


def check_bili_pics() -> int:
    from furina.services.bilibili import pictures
    return pictures.make_due()


def retry_bili_pics(confirmed: bool = False) -> int:
    from furina.services.bilibili import pictures
    if confirmed is not True:
        raise ValueError("重试可能重复发图，请先明确确认")
    return pictures.retry_unconfirmed()
