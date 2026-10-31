"""DeepSeek 花了多少钱：给控制面板「模型花费」页用，不用再去 DeepSeek 网站看。

不依赖 nonebot，面板进程也能 import。三个来源：

1. 官方余额接口：``GET {base}/user/balance``，用聊天本来就填好的 ``AI_CHAT_LLM_API_KEY``，不用另外登录。
2. DeepSeek 网站「用量信息」页背后的接口（每天花了多少、请求数、token、累计消费）。DeepSeek 没有给 API key
   开放这些，只认网站登录后的令牌，所以要在面板里填一次 ``DEEPSEEK_WEB_TOKEN``（可选；不填只是少了官方账单）。
   这几个是网站自己用的内部接口，DeepSeek 改版时可能失效，失效时面板会说清楚，不影响机器人。
3. 本地记账：机器人每次调用模型都按「哪个功能调的」记下 token 和折算金额（furina/core/llm.py，``llmtok:*``），
   这里按天、按功能汇总，用来看钱花在哪。

所有网络请求都是同步的（面板在后台线程里调），超时 12 秒。令牌和 key 不会写进日志或返回值。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from furina.core import db, env

PLATFORM = "https://platform.deepseek.com"
TOKEN_KEY = "DEEPSEEK_WEB_TOKEN"
TIMEOUT = 12
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FurinaPanel/1.0"

# 本地记账的功能标签（模块前缀）→ 人话
FEATURE_NAMES = (
    ("dsh.sim2", "二代仿真内核"),
    ("dsh.agent", "Agent"),
    ("chat.kernel", "二代仿真内核"),
    ("chat.stickerlib", "表情包（看图写备注）"),
    ("chat.planner", "判断要不要接话 / 接谁"),
    ("chat.chat", "回复"),
    ("chat.handlers", "回复"),
    ("chat.turns", "回复"),
    ("chat.emotion", "心情判断"),
    ("chat.mood", "心情判断"),
    ("chat.people", "每个人一本账（记忆整理）"),
    ("chat.memory", "记忆整理"),
    ("chat.learning", "学说话 / 记梗"),
    ("chat.recall", "按相关度想起"),
    ("chat.life_events", "记住群友近况"),
    ("chat.proactive", "冷场主动开话题"),
    ("chat.seek", "主动来找你"),
    ("chat.presence", "惦记常聊的人"),
    ("chat.catchup", "睡醒补看"),
    ("chat.ownlife", "她自己的时间"),
    ("chat.diary", "写日记"),
    ("chat.gallery", "自己的相册"),
    ("chat.websearch", "联网搜索"),
    ("svc.websearch", "联网搜索"),
    ("chat.commands", "指令（总结等）"),
    ("chat.voice", "语音"),
    ("chat.boundary", "边界感"),
    ("chat.quotes", "引用 / 转发"),
    ("chat.reactions", "表情回应"),
    ("bilibili", "B 站生活"),
    ("reminder", "提醒"),
    ("svc.reminder", "提醒"),
    ("core.vision", "看图"),
)


class SpendError(RuntimeError):
    pass


class TokenExpired(SpendError):
    pass


# ---------------- 小工具 ----------------
def _num(v) -> float:
    try:
        return float(str(v).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TokenExpired(f"HTTP {e.code}") from None
        raise SpendError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise SpendError(f"连不上：{getattr(e, 'reason', e)}") from None
    except TimeoutError:
        raise SpendError("超时") from None
    try:
        data = json.loads(body)
    except ValueError:
        raise SpendError("返回的不是 JSON（接口可能改版了）") from None
    if not isinstance(data, dict):
        raise SpendError("返回格式不对")
    # 网站接口：{"code":0,"data":{"biz_code":0,"biz_data":...}}；40002/40003 = 登录失效
    codes = {data.get("code"), (data.get("data") or {}).get("biz_code") if isinstance(data.get("data"), dict) else None}
    if codes & {40002, 40003, 40001}:
        raise TokenExpired(str(data.get("msg") or "登录已失效"))
    return data


def _biz(data: dict):
    d = data.get("data")
    if isinstance(d, dict) and "biz_data" in d:
        return d.get("biz_data")
    return d


def api_base() -> str:
    base = (env.get("AI_CHAT_LLM_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    for tail in ("/chat/completions", "/v1"):
        if base.endswith(tail):
            base = base[: -len(tail)]
    return base or "https://api.deepseek.com"


def web_token() -> str:
    t = (env.get(TOKEN_KEY) or "").strip().strip('"').strip("'")
    if t.startswith("{"):  # 整段 localStorage.userToken 粘进来了
        try:
            t = str(json.loads(t).get("value") or "")
        except ValueError:
            pass
    if t.lower().startswith("bearer "):
        t = t[7:]
    return t.strip()


# ---------------- 1. 官方余额（API key） ----------------
def fetch_balance(api_key: str | None = None) -> dict:
    key = (api_key if api_key is not None else env.get("AI_CHAT_LLM_API_KEY")).strip()
    if not key:
        raise SpendError("没填 AI_CHAT_LLM_API_KEY")
    data = _get_json(api_base() + "/user/balance", {"Authorization": f"Bearer {key}"})
    infos = data.get("balance_infos") or []
    if not infos:
        raise SpendError("余额接口没返回余额（这个 key 可能不是 DeepSeek 官方的）")
    pick = next((i for i in infos if str(i.get("currency")).upper() == "CNY"), infos[0])
    return {
        "currency": str(pick.get("currency") or "CNY"),
        "total": _num(pick.get("total_balance")),
        "granted": _num(pick.get("granted_balance")),
        "topped_up": _num(pick.get("topped_up_balance")),
        "available": bool(data.get("is_available", True)),
    }


# ---------------- 2. 网站用量（登录令牌） ----------------
def _window(now: float | None = None, days: int = 30) -> tuple[int, int, int]:
    ts = time.time() if now is None else now
    local = datetime.fromtimestamp(ts).astimezone()
    tz = int(local.utcoffset().total_seconds()) if local.utcoffset() else 0
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    end = today + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp()), tz


def _bucket_day(t, tz: int) -> str:
    t = _num(t)
    if t > 1e12:
        t /= 1000
    return datetime.fromtimestamp(t + tz, tz=timezone.utc).date().isoformat()


def parse_by_key_cost(data: dict, tz: int) -> tuple[dict[str, float], dict[str, float], str]:
    """by_api_key/cost → ({日期: 元}, {模型: 元}, 币种)。"""
    biz = _biz(data) or {}
    blocks = biz.get("data") if isinstance(biz, dict) else None
    if not isinstance(blocks, list):
        blocks = [biz] if isinstance(biz, dict) and biz.get("series") is not None else []
    if not blocks:
        return {}, {}, "CNY"

    def spend(b):
        return sum(_num(k.get("cost")) for s in (b.get("series") or []) for k in (s.get("buckets") or []))

    cny = [b for b in blocks if str(b.get("currency") or "").upper() == "CNY"]
    block = max(cny or blocks, key=spend)
    days: dict[str, float] = {}
    models: dict[str, float] = {}
    for s in block.get("series") or []:
        m = str(s.get("model") or "?")
        for k in s.get("buckets") or []:
            c = _num(k.get("cost"))
            d = _bucket_day(k.get("time"), tz)
            days[d] = days.get(d, 0.0) + c
            models[m] = models.get(m, 0.0) + c
    return days, models, str(block.get("currency") or "CNY")


def parse_by_key_amount(data: dict, tz: int) -> dict[str, dict]:
    """by_api_key/amount → {日期: {requests, hit, miss, out}}。"""
    biz = _biz(data) or {}
    out: dict[str, dict] = {}
    for s in (biz.get("series") if isinstance(biz, dict) else None) or []:
        for k in s.get("buckets") or []:
            d = out.setdefault(_bucket_day(k.get("time"), tz), {"requests": 0, "hit": 0, "miss": 0, "out": 0})
            u = k.get("usage") or {}
            if isinstance(u, list):
                u = {str(x.get("type")): x.get("amount") for x in u if isinstance(x, dict)}
            d["requests"] += int(_num(u.get("REQUEST")))
            d["hit"] += int(_num(u.get("PROMPT_CACHE_HIT_TOKEN")))
            d["miss"] += int(_num(u.get("PROMPT_CACHE_MISS_TOKEN")))
            d["out"] += int(_num(u.get("RESPONSE_TOKEN")))
    return out


def _monthly(token: str, kind: str, year: int, month: int) -> list:
    data = _get_json(f"{PLATFORM}/api/v0/usage/{kind}?month={month}&year={year}", _web_headers(token))
    biz = _biz(data)
    if isinstance(biz, dict):
        return [biz]
    return biz if isinstance(biz, list) else []


def _sum_usage(items) -> dict[str, float]:
    tot: dict[str, float] = {}
    for m in items or []:
        for u in m.get("usage") or []:
            tot[str(u.get("type"))] = tot.get(str(u.get("type")), 0.0) + _num(u.get("amount"))
    return tot


def parse_monthly(cost_blocks: list, amount_blocks: list) -> tuple[dict[str, float], dict[str, dict], dict[str, float]]:
    """按月接口（备用）→ ({日期: 元}, {日期: 用量}, {模型: 元})。"""
    days: dict[str, float] = {}
    models: dict[str, float] = {}
    cny = [b for b in cost_blocks if str(b.get("currency") or "").upper() == "CNY"] or cost_blocks
    for b in cny[:1]:
        for d in b.get("days") or []:
            days[str(d.get("date"))] = days.get(str(d.get("date")), 0.0) + sum(_sum_usage(d.get("data")).values())
        for m in b.get("total") or []:
            models[str(m.get("model") or "?")] = models.get(str(m.get("model") or "?"), 0.0) + sum(_sum_usage([m]).values())
    amounts: dict[str, dict] = {}
    for b in amount_blocks:
        for d in b.get("days") or []:
            u = _sum_usage(d.get("data"))
            amounts[str(d.get("date"))] = {"requests": int(u.get("REQUEST", 0)), "hit": int(u.get("PROMPT_CACHE_HIT_TOKEN", 0)),
                                           "miss": int(u.get("PROMPT_CACHE_MISS_TOKEN", 0)), "out": int(u.get("RESPONSE_TOKEN", 0))}
    return days, amounts, models


def parse_summary(data: dict) -> dict:
    """get_user_summary → {balance, bonus, costs:{名字: 元}}（字段名网站没公开，能认出来的才显示）。"""
    biz = _biz(data) or {}
    if not isinstance(biz, dict):
        return {}

    def wallets(key):
        return sum(_num(w.get("balance")) for w in (biz.get(key) or []) if str(w.get("currency") or "CNY").upper() == "CNY")

    costs = {}
    for k, v in biz.items():
        if "cost" not in k.lower():
            continue
        if isinstance(v, (int, float, str)):
            costs[k] = _num(v)
        elif isinstance(v, list):
            vals = [x for x in v if isinstance(x, dict)]
            pick = [x for x in vals if str(x.get("currency") or "CNY").upper() == "CNY"] or vals
            if pick:
                x = pick[0]
                costs[k] = _num(x.get("amount", x.get("cost", x.get("balance", x.get("value")))))
        elif isinstance(v, dict):
            costs[k] = _num(v.get("amount", v.get("cost", v.get("value"))))
    return {"balance": wallets("normal_wallets"), "bonus": wallets("bonus_wallets"), "costs": costs}


def _web_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "x-client-platform": "web",
            "Origin": PLATFORM, "Referer": PLATFORM + "/usage"}


def fetch_web(token: str | None = None, now: float | None = None) -> dict:
    tok = web_token() if token is None else token
    if not tok:
        raise SpendError("没填网站令牌")
    h = _web_headers(tok)
    start, end, tz = _window(now)
    out: dict = {"source": "by_api_key"}
    try:
        cost = _get_json(f"{PLATFORM}/api/v0/usage/by_api_key/cost?start={start}&end={end}&tz={tz}", h)
        amount = _get_json(f"{PLATFORM}/api/v0/usage/by_api_key/amount?start={start}&end={end}&tz={tz}", h)
        days, models, cur = parse_by_key_cost(cost, tz)
        amounts = parse_by_key_amount(amount, tz)
        if not days and not amounts:
            raise SpendError("按 key 的用量是空的")
    except TokenExpired:
        raise
    except SpendError:
        # 备用：按月的接口（本月 + 上月，再裁成近 30 天）
        out["source"] = "monthly"
        today = datetime.fromtimestamp(time.time() if now is None else now).date()
        prev = today.replace(day=1) - timedelta(days=1)
        cb, ab = [], []
        for y, m in {(today.year, today.month), (prev.year, prev.month)}:
            cb += _monthly(tok, "cost", y, m)
            ab += _monthly(tok, "amount", y, m)
        days, amounts, models = parse_monthly(cb, ab)
        cur = "CNY"
        lo = (today - timedelta(days=29)).isoformat()
        days = {d: v for d, v in days.items() if d >= lo}
        amounts = {d: v for d, v in amounts.items() if d >= lo}
    out.update({"days": days, "amounts": amounts, "models": models, "currency": cur})
    try:
        out["summary"] = parse_summary(_get_json(f"{PLATFORM}/api/v0/users/get_user_summary", h))
    except TokenExpired:
        raise
    except SpendError:
        out["summary"] = {}
    return out


# ---------------- 3. 本地记账 ----------------
THINK_MARK = "#思考"
THINK_NAMES = {"low": "低", "high": "高", "max": "最高"}


def feature_name(tag: str) -> str:
    """调用来源 → 面板显示的功能名；开了思考的调用后面带「（思考·档位）」单列。"""
    base, _, lvl = (tag or "").partition(THINK_MARK)
    suffix = f"（思考·{THINK_NAMES.get(lvl, lvl)}）" if lvl else ""
    for prefix, name in FEATURE_NAMES:
        if base.startswith(prefix):
            return name + suffix
    return (base or "?") + suffix


def local_days(days: int = 30, today: date | None = None) -> dict[str, dict]:
    """{日期: {cost, calls, models}}：cost 只有开始记账（llmtok）之后才有；models = 当天模型调用总次数。"""
    t = today or date.today()
    since = (t - timedelta(days=days - 1)).isoformat()
    out: dict[str, dict] = {}
    for day, kind, _g, _u, n in db.usage_rows(since):
        d = out.setdefault(day, {"cost": 0.0, "calls": 0, "models": 0})
        if kind == "llmtok:cost_u":
            d["cost"] += int(n) / 1e6
        elif kind == "llmtok:calls":
            d["calls"] += int(n)
        elif kind.startswith("model:"):
            d["models"] += int(n)
    return out


def local_features(day: str | None = None) -> list[dict]:
    """某天按功能合并的花费：[{name, tags, calls, hit, miss, out, cost}]，花得多的在前。"""
    d = day or date.today().isoformat()
    tags: dict[str, dict] = {}
    for _day, kind, tag, _u, n in db.usage_rows(d):
        if _day != d or not kind.startswith("llmtok:"):
            continue
        tags.setdefault(tag, {})[kind[7:]] = int(n)
    merged: dict[str, dict] = {}
    for tag, r in tags.items():
        if tag.partition(THINK_MARK)[0] == "?":
            continue
        name = feature_name(tag)
        m = merged.setdefault(name, {"name": name, "tags": [], "calls": 0, "hit": 0, "miss": 0, "out": 0, "cost": 0.0})
        m["tags"].append(tag)
        for f in ("calls", "hit", "miss", "out"):
            m[f] += r.get(f, 0)
        m["cost"] += r.get("cost_u", 0) / 1e6
    return sorted(merged.values(), key=lambda r: -r["cost"])


def thinking_summary(day: str | None = None) -> dict:
    """某天开了思考的调用：{calls, think, cost, by_level: {档位: {calls, think, cost}}, total_cost, sim2_cost, agent_cost}。
    think = 思考用掉的 token（按输出价算钱，已经算在 cost 里）。"""
    d = day or date.today().isoformat()
    out = {"calls": 0, "think": 0, "cost": 0.0, "by_level": {}, "total_cost": 0.0, "sim2_cost": 0.0, "agent_cost": 0.0}
    for _day, kind, tag, _u, n in db.usage_rows(d):
        if _day != d or not kind.startswith("llmtok:"):
            continue
        field = kind[7:]
        val = int(n)
        cost = val / 1e6 if field == "cost_u" else 0.0
        out["total_cost"] += cost
        if tag.startswith("dsh.sim2"):
            out["sim2_cost"] += cost
        elif tag.startswith("dsh.agent"):
            out["agent_cost"] += cost
        _base, _, lvl = (tag or "").partition(THINK_MARK)
        if not lvl:
            continue
        lv = out["by_level"].setdefault(THINK_NAMES.get(lvl, lvl), {"calls": 0, "think": 0, "cost": 0.0})
        if field == "calls":
            out["calls"] += val
            lv["calls"] += val
        elif field == "think":
            out["think"] += val
            lv["think"] += val
        elif field == "cost_u":
            out["cost"] += cost
            lv["cost"] += cost
    return out


# ---------------- 合在一起 ----------------
def snapshot(with_web: bool = True) -> dict:
    """面板一次要的全部数据；各来源失败互不影响，错误写在 errors 里（人话）。"""
    snap: dict = {"ts": time.time(), "errors": {}, "has_token": bool(web_token())}
    try:
        snap["balance"] = fetch_balance()
    except SpendError as e:
        snap["errors"]["balance"] = f"余额读不到：{e}"
    if with_web and snap["has_token"]:
        try:
            snap["web"] = fetch_web()
        except TokenExpired:
            snap["errors"]["web"] = "网站令牌失效了：重新登录 DeepSeek 网站后再复制一次（点「网站令牌」看怎么弄）"
        except SpendError as e:
            snap["errors"]["web"] = f"网站用量读不到：{e}"
    try:
        snap["local_days"] = local_days()
        snap["features"] = local_features()
    except Exception as e:  # noqa: BLE001  数据库被占用等
        snap["errors"]["local"] = f"本地记账读不到：{e}"
    try:
        snap["board"] = board()
    except Exception as e:  # noqa: BLE001
        snap["errors"]["board"] = f"按群花费读不到：{e}"
    try:  # 留一份，下次打开页面先显示上次的
        db.put("panel", "llm_spend_last", {k: v for k, v in snap.items() if k in ("ts", "balance", "web")})
    except Exception:  # noqa: BLE001
        pass
    return snap


def _group_names() -> dict:
    try:
        from furina.core import docfile

        d = docfile.load("group_members.json")
        return dict((d or {}).get("group_names") or {}) if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def board(days: int = 7, rounds_limit: int = 80) -> dict:
    """二代仿真花费看板：今天每个群、最近几天每个群、最近每一轮、积极性曲线（不联网）。"""
    from furina.core import budget, rounds

    names = _group_names()
    rows = rounds.by_group(days)
    today = date.today().isoformat()
    for r in rows:
        r["name"] = names.get(r["group"]) or (f"群 {r['group']}" if r["group"] else "（私聊 / 其他）")
    recent = rounds.recent(limit=rounds_limit)
    for r in recent:
        r["name"] = names.get(r.get("g", "")) or f"群 {r.get('g', '')}"
    return {"today": [r for r in rows if r["day"] == today], "days": rows, "recent": recent,
            "hourly": rounds.hourly(), "budget": budget.status(), "curve": budget.curve()}


def last_snapshot() -> dict:
    try:
        v = db.get("panel", "llm_spend_last", {}) or {}
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
