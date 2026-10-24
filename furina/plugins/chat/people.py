"""按「人」记一本账（参考 ai-companion-kit 的 memory.js / qq-bridge 的跨会话记忆）。

记忆挂在人身上（QQ 号当 key），不挂在群上：TA 在哪个群说的都记到同一本账里，换个群她也还认识 TA。
每个人一本账：
- traits   你了解 TA 的（性格、习惯，记具体小事和证据，不记空泛评价）
- facts    TA 告诉过你的事（养了只猫、在读高三、下周考试）
- topics   你们聊过的（「TA 说的 → 你回的」）
  traits / facts 每条带：记下的时间、重要度、事实键（「对象.方面」，同一个键只留最新）、鲜活度（按半衰期淡，
  又被提起就弹回来），过时的标「已被取代」但不删（参考 dsh-fish-memory 鱼的记忆）
- pending  心里还记着、还没说出口 / 还没问的事（TA 说「等我洗个澡」「明天告诉你结果」）
- impression 一句话总印象（定期压缩时更新）
- 认识第几天、互动过几次、TA 平时几点出现（时间感）
写入：她每回 TA 一次记一笔；攒够几次或隔了一阵，后台让模型把最近的对话提炼进账本（不阻塞回复）；
每 7 天或 30 次互动压缩一次，去重、删过时的。
读取：回复 TA 时按 TA 这句话「想起」相关的那几条（recall.py：证据门槛 + 鲜活度排序），不相关的不塞；可选附上「TA 刚在别的群说过什么」（只当背景，不说破）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time

from nonebot import logger

from . import config, llm, memory, recall
from ...core import db

NS = "people"
MAX = {"traits": 80, "facts": 200, "topics": 20, "pending": 12}  # 记下的不轻易删：淡掉的只是排得靠后（鱼的记忆）
SHOW = {"traits": 6, "facts": 6, "topics": 4, "pending": 3}
EXTRACT_EVERY = 4           # 每回 TA 几次提炼一次
EXTRACT_IDLE = 2 * 3600     # 或者上次提炼后过了这么久且有新对话
COMPRESS_DAYS = 7
COMPRESS_TALKS = 30
BUFFER = 12

_tasks: set = set()
_busy: set = set()

# 「等我一下」「去洗个澡」这类：TA 说要离开一会儿 / 答应等下给你看 → 心里记着，过一阵 TA 还没回来就问一句
_AWAY = re.compile(
    r"(?:等我|等下|等会|一会儿?|待会儿?|马上|晚点|过会儿?)[^，。！？\n]{0,8}(?:回来|再聊|发你|给你看|告诉你|说|弄|看)"
    r"|(?:去|先去)(?:洗澡|洗个澡|吃饭|吃个饭|做饭|上课|上班|开会|睡|睡觉|午睡|打游戏|打会儿?游戏|写作业|拿外卖|取快递|跑步|健身)"
    r"|(?:先|我先)(?:下了|溜了|撤了|忙了|去忙)"
)
_BYE = re.compile(r"晚安|睡了|明天见|拜拜|下线了|886")


# ---------------- 存取 ----------------
def _blank(uid: str, name: str, now: float) -> dict:
    return {"uid": uid, "name": name, "names": [name] if name else [], "traits": [], "facts": [], "topics": [],
            "pending": [], "impression": "", "talks": 0, "first": now, "last": now, "hours": [0] * 24,
            "groups": {}, "buf": [], "since_extract": 0, "extract_ts": now, "compress_ts": now, "talks_at_compress": 0}


def get(uid) -> dict | None:
    rec = db.get(NS, str(uid))
    return rec if isinstance(rec, dict) else None


def _save(rec: dict) -> None:
    db.put(NS, str(rec["uid"]), rec)


def _skip(uid: str) -> bool:
    return not uid or uid == str(config.BOT_QQ or "") or uid in set(config.PEER_BOTS)


def observe(group_id, user_id, nickname: str, text: str = "", now: float | None = None) -> None:
    """群友说了一句话（任何群）：更新最后出现时间、常出现的钟点、在哪些群；说要离开一会儿就记进心里。"""
    if not config.PEOPLE:
        return
    uid = str(user_id)
    if _skip(uid):
        return
    ts = time.time() if now is None else now
    rec = get(uid) or _blank(uid, nickname, ts)
    if nickname:
        rec["name"] = nickname
        names = [n for n in rec.get("names") or [] if n != nickname]
        rec["names"] = (names + [nickname])[-4:]
    hours = list(rec.get("hours") or [0] * 24)
    hours = (hours + [0] * 24)[:24]
    hours[time.localtime(ts).tm_hour] += 1
    rec["hours"] = hours
    rec["prev_last"] = float(rec.get("last") or 0)
    rec["last"] = ts
    groups = dict(rec.get("groups") or {})
    groups[str(group_id)] = ts
    rec["groups"] = groups
    body = (text or "").strip()
    if body:
        recall.observe_speech(uid, body)  # TA 平时怎么说话：挂在嘴边的词不算「想起来」的证据
    if body and config.PENDING_CHASE:
        if _AWAY.search(body):
            _add_pending(rec, f"TA 说「{body[:30]}」", ts, due_min=random.uniform(30, 90), kind="away", gid=str(group_id))
        elif _BYE.search(body):  # 说晚安/下线了：之前那些「等我一下」就不追了
            rec["pending"] = [p for p in rec.get("pending") or [] if p.get("kind") != "away"]
    # TA 回来说话了：之前 TA「离开一会儿」的那条就算回来了（留在心里但不再追问）
    for p in rec.get("pending") or []:
        if p.get("kind") == "away" and ts - float(p.get("ts") or 0) > 120 and not p.get("back"):
            if not _AWAY.search(body):
                p["back"] = ts
    _save(rec)


def _add_pending(rec: dict, text: str, now: float, due_min: float = 0.0, kind: str = "note", gid: str = "") -> None:
    pend = [p for p in rec.get("pending") or [] if p.get("text") != text]
    pend.append({"text": text[:60], "ts": now, "due": now + due_min * 60 if due_min else 0, "kind": kind, "gid": gid})
    rec["pending"] = pend[-MAX["pending"]:]


def note_interaction(group_id, user_id, nickname: str, user_text: str, reply: str, now: float | None = None) -> None:
    """她回了 TA 一次：互动 +1，记进最近对话缓冲；攒够了后台提炼进账本。"""
    if not config.PEOPLE:
        return
    uid = str(user_id)
    if _skip(uid):
        return
    ts = time.time() if now is None else now
    rec = get(uid) or _blank(uid, nickname, ts)
    rec["talks"] = int(rec.get("talks") or 0) + 1
    u, a = (user_text or "").strip().replace("\n", " ")[:80], (reply or "").strip().replace("\n", " ")[:80]
    if u or a:
        rec["buf"] = ((rec.get("buf") or []) + [{"u": u, "a": a, "ts": ts, "g": str(group_id)}])[-BUFFER:]
        rec["topics"] = ((rec.get("topics") or []) + [f"{u[:30]} → {a[:30]}"])[-MAX["topics"]:]
    rec["since_extract"] = int(rec.get("since_extract") or 0) + 1
    # 她回过 TA 之后，「等我一下」类的就不用再追了
    rec["pending"] = [p for p in rec.get("pending") or [] if not (p.get("kind") == "away" and float(p.get("ts") or 0) < ts - 180)]
    _save(rec)
    due = rec["since_extract"] >= EXTRACT_EVERY or (rec["since_extract"] and ts - float(rec.get("extract_ts") or 0) > EXTRACT_IDLE)
    if due:
        _spawn(_later(extract, uid))
    elif _compress_due(rec, ts):
        _spawn(_later(compress, uid))


async def _later(fn, uid: str) -> None:
    """回完话过一会儿再在后台提炼（不跟正式回复抢模型、也不拖慢回复）。"""
    await asyncio.sleep(random.uniform(20, 60))
    await fn(uid)


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


EXTRACT_PROMPT = """今天是 {today}。下面是你（{me}）最近和「{name}」的几段对话（TA 说的 → 你回的）：
{buf}

你已经知道的关于 TA 的：
你了解 TA：{traits}
TA 告诉过你：{facts}
心里还记着的：{pending}
已经在用的事实键：{keys}

从这几段对话里挑出值得长期记住的东西，只输出 JSON：
{{"traits": [{{"text": "TA 的性格/习惯/喜好（具体小事和证据，如「熬夜打游戏到两点」，不要「人很好」这种空话）", "importance": 0.5, "key": ""}}],
 "facts": [{{"text": "TA 明确说过的事（如「养了只橘猫叫煤球」「10月24日要去面试」）", "importance": 0.5, "key": "", "replaces": ""}}],
 "pending": [{{"text": "还没了结、下次可以问起的事（如「TA 周五面试，还不知道结果」「答应给 TA 看你的蛋糕照片」）", "after_hours": 多少小时后适合问}}],
 "done": ["已经了结、可以从「心里还记着」里删掉的事（原文）"]}}
规则：
- 一条只装一件事：「TA 身高一米七、在读高三」是两条。
- 不写「明天」「下周」「刚才」「最近」这种相对时间，换成具体日期（今天是 {today}）；写在句首的日期才算事情发生的那天，生日不算。
- 提到别人就写名字，别把别人也写成 TA。
- importance：0 = 随口一提；0.5 = 比较重要；1 = 非常重要、要记很久（约定、身体状况、重要的人和事）。拿不准写 0.5。
- key（事实键）可选，只给「会变的那一个值」用，写成「对象.方面」，如「TA.学校」「TA.猫」「TA.对象」；同一个键以后只留最新那条。
  键里不许写具体的值（写「TA.身高」，不写「TA.身高170」）；不许用「…内容」「…相关」「…其他」「…的事」「…记录」这种什么都能装的键；
  每天都会变的流水（吃了什么、几点睡、今天干了啥）不给键；一次性的事不给键；拿不准就留空。能用上面已经在用的键就用它，别另起一个。
- replaces 可选：这条是在更新以前记过的某条（状态变了：还没→已经、在读→毕业了），把旧那条的原文或大意填这儿；不是在更新就留空。
- TA 又提到你已经知道的事，就把那条原样再写一遍（会记得更牢）；没再提到的不要重复写。
没有就给空数组。不要编；不要记你自己说的玩笑当成 TA 的事实。"""

_WEEK = "一二三四五六日"


def _today_text(now: float) -> str:
    lt = time.localtime(now)
    return f"{lt.tm_year}年{lt.tm_mon}月{lt.tm_mday}日（周{_WEEK[lt.tm_wday]}）"


def _text(x) -> str:
    return x if isinstance(x, str) else str((x or {}).get("text") or "") if isinstance(x, dict) else ""


def items(rec: dict, key: str) -> list:
    """traits / facts 统一成带元数据的 dict（老账本里是纯字符串，读到就顺手升级）。"""
    out = []
    first = float(rec.get("first") or time.time())
    for x in rec.get(key) or []:
        if isinstance(x, str):
            x = {"text": x[:80], "created": first, "stable": key == "traits"}
        elif not isinstance(x, dict) or not str(x.get("text") or "").strip():
            continue
        out.append(x)
    rec[key] = out
    return out


def texts(rec: dict, key: str, limit: int = 0) -> list:
    """还作数的那些条目的文字（已被取代的不算），最新的在后面。"""
    live = [_text(x) for x in (rec or {}).get(key) or [] if _text(x) and not (isinstance(x, dict) and x.get("superseded"))]
    return live[-limit:] if limit else live


def _parse_item(raw) -> tuple:
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()
        try:
            imp = float(raw.get("importance") if raw.get("importance") is not None else 0.5)
        except (TypeError, ValueError):
            imp = 0.5
        return text[:80], min(1.0, max(0.0, imp)), recall.norm_key(raw.get("key")), str(raw.get("replaces") or "").strip()
    return str(raw or "").strip()[:80], 0.5, "", ""


def _merge_item(lst: list, raw, now: float, stable_default: bool) -> str:
    """并进一条：又提了一遍 → 记得更牢；同一个键 / 说明了取代谁 / 几乎一样的新版本 → 旧的标「已被取代」（不删）。"""
    text, imp, key, replaces = _parse_item(raw)
    if not text:
        return ""
    half = config.MEMORY_HALF_LIFE_DAYS
    live = [x for x in lst if not x.get("superseded")]
    for it in live:
        a, b = recall._squash(it.get("text", "")), recall._squash(text)
        if a and b and (a == b or (len(a) > 8 and len(b) > 8 and (a in b or b in a))):
            recall.reinforce(it, now, half)
            if imp > float(it.get("importance") or 0):
                it["importance"] = imp
            if key and not it.get("key"):
                it["key"] = key
            return "reinforced"
    new = {"text": text, "at": now, "created": now, "importance": imp, "stable": bool(stable_default or imp >= 1)}
    if key:
        new["key"] = key
    for it in live:
        why = ""
        if key and recall.norm_key(it.get("key")) == key:
            why = "同一个事实键只留最新"
        elif recall.same_thing(it.get("text", ""), text):
            why = "跟新记的几乎一样"
        if why:
            it["superseded"] = now
            it["by"] = text[:30]
            it["why"] = why
    if replaces:
        best, best_sim = None, 0.0
        target = recall._squash(replaces)
        for it in live:
            if it.get("superseded"):
                continue
            a = recall._squash(it.get("text", ""))
            if a and target and (a == target or (len(a) > 6 and len(target) > 6 and (a in target or target in a))):
                best, best_sim = it, 1.0
                break
            sim = recall.similarity(replaces, it.get("text", ""))
            if sim > best_sim:
                best, best_sim = it, sim
        if best is not None and best_sim >= 0.5:
            best["superseded"] = now
            best["by"] = text[:30]
            best["why"] = "新记的这条说它取代了这条"
    lst.append(new)
    return "added"


def _cap(lst: list, cap: int, now: float) -> list:
    """超了上限才挪走：先挪已被取代的，再挪最淡的那些（长期的不挪）。"""
    if len(lst) <= cap:
        return lst
    half = config.MEMORY_HALF_LIFE_DAYS
    order = sorted(range(len(lst)), key=lambda i: (0 if lst[i].get("superseded") else 1 if not lst[i].get("stable") else 2,
                                                  recall.activation(lst[i], now, half) + float(lst[i].get("importance") or 0)))
    drop = set(order[:len(lst) - cap])
    return [x for i, x in enumerate(lst) if i not in drop]


def _compress_due(rec: dict, now: float) -> bool:
    return (now - float(rec.get("compress_ts") or now) >= COMPRESS_DAYS * 86400
            or int(rec.get("talks") or 0) - int(rec.get("talks_at_compress") or 0) >= COMPRESS_TALKS)


def _json(raw: str):
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def known_keys(rec: dict) -> list:
    keys = []
    for key in ("traits", "facts"):
        for x in rec.get(key) or []:
            k = recall.norm_key(x.get("key")) if isinstance(x, dict) and not x.get("superseded") else ""
            if k and k not in keys:
                keys.append(k)
    return keys[-30:]


async def extract(uid: str) -> None:
    if uid in _busy:
        return
    rec = get(uid)
    if not rec or not rec.get("buf"):
        return
    _busy.add(uid)
    try:
        from ...core import roles
        now = time.time()
        buf = "\n".join(f"- {b.get('u') or '…'} → {b.get('a') or '…'}" for b in rec["buf"][-8:])
        prompt = EXTRACT_PROMPT.format(
            today=_today_text(now), me=roles.display_name(), name=rec.get("name") or uid, buf=buf,
            traits="；".join(texts(rec, "traits", 20)) or "（暂无）", facts="；".join(texts(rec, "facts", 30)) or "（暂无）",
            pending="；".join(p.get("text", "") for p in rec.get("pending") or []) or "（暂无）",
            keys="、".join(known_keys(rec)) or "（还没有）")
        try:
            raw = await llm.chat([{"role": "user", "content": prompt}])
        except llm.LLMError as e:
            logger.warning(f"[人物账本] 提炼 {uid} 失败: {e}")
            return
        data = _json(raw) or {}
        now = time.time()
        rec = get(uid) or rec
        merge(rec, data, now)
        rec["since_extract"] = 0
        rec["extract_ts"] = now
        _save(rec)
        logger.info(f"[人物账本] 更新了 {rec.get('name') or uid}：{json.dumps(data, ensure_ascii=False)[:160]}")
        if _compress_due(rec, now):
            await compress(uid, _locked=True)
    finally:
        _busy.discard(uid)


def merge(rec: dict, data: dict, now: float) -> None:
    """把模型提炼出的条目并进账本（又提一遍 = 记得更牢；新版本取代旧版本但不删；超上限才挪最淡的）。纯函数，方便测试。"""
    for key in ("traits", "facts"):
        lst = items(rec, key)
        for raw in data.get(key) or []:
            _merge_item(lst, raw, now, stable_default=(key == "traits"))
        rec[key] = _cap(lst, MAX[key], now)
    done = [str(x or "") for x in data.get("done") or []]
    if done:
        rec["pending"] = [p for p in rec.get("pending") or []
                          if not any(d and (d in p.get("text", "") or p.get("text", "") in d) for d in done)]
    for item in data.get("pending") or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            try:
                hours = float(item.get("after_hours") or 0)
            except (TypeError, ValueError):
                hours = 0.0
        else:
            text, hours = str(item or "").strip(), 0.0
        if text and all(text not in p.get("text", "") for p in rec.get("pending") or []):
            _add_pending(rec, text, now, due_min=max(0.0, hours) * 60, kind="note")


COMPRESS_PROMPT = """这是你对「{name}」记的一本账：
你了解 TA：{traits}
TA 告诉过你：{facts}
用一句话说 TA 在你心里是个什么样的人（口语，20 字以内，别用「很好」「不错」这种空话）。只输出 JSON：{{"impression": "..."}}"""


def tidy(rec: dict, now: float) -> int:
    """本地整理（不调模型、不删东西）：几乎一样的两条，旧的标「已被取代」。返回标了几条。"""
    marked = 0
    for key in ("traits", "facts"):
        lst = items(rec, key)
        live = [x for x in lst if not x.get("superseded")]
        for i, old in enumerate(live):
            for newer in live[i + 1:]:
                if not newer.get("superseded") and recall.same_thing(old.get("text", ""), newer.get("text", "")):
                    old["superseded"] = now
                    old["by"] = str(newer.get("text", ""))[:30]
                    old["why"] = "整理时发现跟后来记的几乎一样"
                    marked += 1
                    break
    return marked


async def compress(uid: str, _locked: bool = False) -> None:
    """定期整理：本地去重（标作废，不删），再让模型更新一句话总印象。条目本身不再让模型重写 —— 那会丢掉时间和重要度。"""
    if not _locked and uid in _busy:
        return
    rec = get(uid)
    if not rec:
        return
    if not _locked:
        _busy.add(uid)
    try:
        prompt = COMPRESS_PROMPT.format(name=rec.get("name") or uid, traits="；".join(texts(rec, "traits", 20)) or "（暂无）",
                                        facts="；".join(texts(rec, "facts", 30)) or "（暂无）")
        try:
            data = _json(await llm.chat([{"role": "user", "content": prompt}])) or {}
        except llm.LLMError as e:
            logger.warning(f"[人物账本] 压缩 {uid} 失败: {e}")
            data = {}
        rec = get(uid) or rec
        now = time.time()
        marked = tidy(rec, now)
        if data.get("impression"):
            rec["impression"] = str(data["impression"])[:40]
        rec["compress_ts"] = now
        rec["talks_at_compress"] = int(rec.get("talks") or 0)
        _save(rec)
        logger.info(f"[人物账本] 整理了 {rec.get('name') or uid} 的账本（并掉 {marked} 条重复）：{rec.get('impression')}")
    finally:
        if not _locked:
            _busy.discard(uid)


# ---------------- 渲染 ----------------
def _days_known(rec: dict, now: float) -> int:
    first = float(rec.get("first") or now)
    return max(1, int((now - first) // 86400) + 1)


def typical_hours(rec: dict) -> list[int]:
    hours = list(rec.get("hours") or [])
    total = sum(hours)
    if total < 30:
        return []
    ranked = sorted(range(24), key=lambda h: -hours[h])
    out, acc = [], 0
    for h in ranked:
        out.append(h)
        acc += hours[h]
        if acc >= total * 0.7:
            break
    return sorted(out)


def _hours_text(hs: list[int]) -> str:
    if not hs:
        return ""
    spans, start, prev = [], hs[0], hs[0]
    for h in hs[1:] + [None]:
        if h is not None and h == prev + 1:
            prev = h
            continue
        spans.append(f"{start}点" if start == prev else f"{start}~{prev + 1}点")
        if h is not None:
            start = prev = h
    return "、".join(spans[:3])


def unusual_time_note(rec: dict, now: float) -> str:
    """TA 平时很少这个点出现（比如夜猫子大早上冒出来）：给她一个时间感。"""
    hours = list(rec.get("hours") or [])
    total = sum(hours)
    if total < 30:
        return ""
    h = time.localtime(now).tm_hour
    if hours[h] / total >= 0.03:
        return ""
    prev = float(rec.get("prev_last") or 0)
    if prev and now - prev < 3 * 3600:  # 一直在线聊着，就不算「突然冒出来」
        return ""
    return (f"TA 平时一般{_hours_text(typical_hours(rec))}出现，很少这个点（{h}点）冒出来——"
            "可以像熟人那样顺口说一句（「今天起这么早？」「这个点还没睡？」），也可以不提。")


def recall_rows(rec: dict, query: str, now: float, uid=None) -> list:
    """按这句话从 TA 的账本里想起相关的几条（性格喜好 + TA 说过的事）。"""
    pool = [x for x in items(rec, "traits") + items(rec, "facts") if not x.get("superseded")]
    return recall.recall(pool, query, now, speaker=uid, max_items=config.RECALL_MAX_ITEMS,
                         max_chars=config.RECALL_MAX_CHARS, half_life_days=config.MEMORY_HALF_LIFE_DAYS)


def note(user_id, nickname: str = "", group_id=None, now: float | None = None, query: str = "") -> str:
    """回复 TA 时注入的「关于 TA 的一本账」。开了「按相关度想起」就只放跟这句话有关的那几条。"""
    if not config.PEOPLE:
        return ""
    uid = str(user_id)
    rec = get(uid)
    if not rec:
        return ""
    ts = time.time() if now is None else now
    name = nickname or rec.get("name") or uid
    lines = [f"【关于 {name} 的一本账（你自己记的，换了哪个群都是同一个人）】",
             f"认识第 {_days_known(rec, ts)} 天，互动过 {int(rec.get('talks') or 0)} 次。"]
    if rec.get("impression"):
        lines.append(f"你对 TA 的印象：{rec['impression']}")
    if config.RECALL:
        rows = recall_rows(rec, query, ts, uid) if (query or "").strip() else []
        if rows:
            lines.append("这句话让你想起的（你自己记下的；带时间的是当时记下的时候，长期的不写时间）：")
            lines.extend(recall.render_lines(rows, ts))
            for r in rows:  # 被想起一次就回暖一点（鱼的记忆：A' = A + 0.2 × (1 − A)）
                if isinstance(r.get("item"), dict):
                    recall.reinforce(r["item"], ts, config.MEMORY_HALF_LIFE_DAYS)
            _save(rec)
    else:
        if texts(rec, "traits"):
            lines.append("你了解 TA：" + "；".join(texts(rec, "traits", SHOW["traits"])))
        if texts(rec, "facts"):
            lines.append("TA 告诉过你：" + "；".join(texts(rec, "facts", SHOW["facts"])))
    if rec.get("topics"):
        lines.append("你们最近聊过：" + "；".join(rec["topics"][-SHOW["topics"]:]))
    pend = [p for p in rec.get("pending") or [] if not p.get("back") or p.get("kind") != "away"]
    if pend:
        lines.append("⚠️ 心里还记着、还没说出口的事：" + "；".join(p.get("text", "") for p in pend[-SHOW["pending"]:])
                     + "（时机合适再自然提，别硬塞，也别一次全倒出来）")
    unusual = unusual_time_note(rec, ts)
    if unusual:
        lines.append(unusual)
    if config.PEOPLE_CROSS_GROUP and group_id is not None:
        other = cross_note(uid, group_id, ts)
        if other:
            lines.append(other)
    lines.append("这些只在自然相关时才用；不要为了用记忆硬聊旧话题，也不要说「我记得你说过」这种像在翻档案的话。")
    return "\n".join(lines)


def cross_note(uid: str, group_id, now: float, hours: float = 24.0, limit: int = 3) -> str:
    """TA 最近在别的群说过的几句（只当背景）。"""
    rec = get(uid) or {}
    picked = []
    for gid, last in (rec.get("groups") or {}).items():
        if gid == str(group_id) or now - float(last or 0) > hours * 3600:
            continue
        try:
            hist = memory.get_history(int(gid))
        except (TypeError, ValueError):
            continue
        for e in hist:
            if e.get("role") == "user" and str(e.get("uid") or "") == uid and now - float(e.get("ts") or 0) <= hours * 3600:
                body = str(e.get("content") or "").partition("：")[2] or str(e.get("content") or "")
                if body.strip():
                    picked.append((float(e.get("ts") or 0), body.strip()[:40]))
    if not picked:
        return ""
    picked.sort()
    lines = " / ".join(f"「{t}」" for _, t in picked[-limit:])
    return f"TA 刚在别的群说过：{lines}（知道就行，别生硬提，也别暴露你两边都看着）"


# ---------------- 惦记：TA 说要离开一会儿，很久没回来 ----------------
def due_pending(user_id, now: float | None = None) -> dict | None:
    """「等我洗个澡」过了 30~90 分钟 TA 还没回来说话：返回那条（只追一次）。"""
    rec = get(str(user_id))
    if not rec:
        return None
    ts = time.time() if now is None else now
    for p in rec.get("pending") or []:
        if p.get("kind") != "away" or p.get("chased") or p.get("back"):
            continue
        due = float(p.get("due") or 0)
        if due and ts >= due and ts - due < 3 * 3600 and float(rec.get("last") or 0) <= float(p.get("ts") or 0) + 60:
            return p
    return None


def mark_chased(user_id, item: dict, now: float | None = None) -> None:
    rec = get(str(user_id))
    if not rec:
        return
    for p in rec.get("pending") or []:
        if p.get("text") == item.get("text") and p.get("ts") == item.get("ts"):
            p["chased"] = time.time() if now is None else now
    _save(rec)


def all_people() -> dict:
    return db.items(NS)
