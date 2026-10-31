"""她自己答应过的事（承诺日历，参考大肥鱼 fatfish 的 commitment calendar）。

二代仿真里她用 qq_say 说了「等下发你」「晚点告诉你」「明天帮你看看」这种话，就记一笔、估个到点时间；
到点了叫醒她一次（醒来原因 promise），提示里写清她当时怎么说的、答应了谁：用她自己的话兑现（真去查 / 去做 /
给结果），做不到就说一声；晚了一个多小时先道个歉。每件事只提醒她一次，提醒过就算结了。

群友让她「X 点提醒我」「半小时后叫我」「明天提醒小明交作业」：她用 remind_later 工具记下来（谁都可以，不只主人），
到点同样叫醒她，她在群里艾特那个人、用自己的话提醒。开关 AI_CHAT_COMMITMENTS（默认开）。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from ...core import db, env

NS = "sim2_commit"
MAX_PER_GROUP = 40
MAX_PER_PERSON = 5
MAX_DAYS = 30
LATE_APOLOGY = 3600

# 先把明显不是承诺的排掉：她在讲别人 / 反问 / 拒绝
_NOT = re.compile(r"(不(会|要|想|用)|才不|别等|没空|不帮|你晚点|你明天)")
_DO = r"(告诉你|跟你说|发你|发给你|给你(看|发|找|弄|讲|说|画|写|查)|帮你|找给你|找找|查查|查一下|看看|弄好|弄一下|整理|写好|画好|回你|再说|答复你|研究一下)"
_RULES = [
    (re.compile(r"(\d{1,3})\s*分钟(后|以后|之后)?[^，,。！!？?]{0,10}" + _DO), "min"),
    (re.compile(r"(半小时|半个小时|一小时|一个小时|两小时|两个小时)(后|以后|之后)?[^，,。！!？?]{0,10}" + _DO), "hour"),
    (re.compile(r"(明天|明早|明儿|明晚)[^，,。！!？?]{0,12}" + _DO), "tomorrow"),
    (re.compile(r"(今晚|晚上|今天晚上)[^，,。！!？?]{0,12}" + _DO), "tonight"),
    (re.compile(r"(等下|等一下|等会|等会儿|一会|一会儿|待会|待会儿|晚点|晚些|回头|稍后|过会|过会儿)[^，,。！!？?]{0,12}" + _DO), "soon"),
]
_HOURS = {"半小时": 30, "半个小时": 30, "一小时": 60, "一个小时": 60, "两小时": 120, "两个小时": 120}


def enabled() -> bool:
    return env.get_bool("AI_CHAT_COMMITMENTS", True)


def detect(text: str, now: float | None = None) -> float:
    """她这句话里有没有答应「过一会儿再做」的事：返回到点时间戳，没有返回 0。"""
    now = time.time() if now is None else now
    t = (text or "").strip()
    if not t or len(t) > 80 or _NOT.search(t) or t.endswith(("?", "？", "吗")):
        return 0.0
    for rx, kind in _RULES:
        m = rx.search(t)
        if not m:
            continue
        if kind == "min":
            n = int(m.group(1))
            return now + max(3, min(n, 600)) * 60 if n else 0.0
        if kind == "hour":
            return now + _HOURS.get(m.group(1), 60) * 60
        dt = datetime.fromtimestamp(now)
        if kind == "tomorrow":
            hour = 20 if m.group(1) == "明晚" else 10
            return (dt + timedelta(days=1)).replace(hour=hour, minute=0, second=0).timestamp()
        if kind == "tonight":
            target = dt.replace(hour=20, minute=30, second=0)
            return target.timestamp() if target > dt + timedelta(minutes=20) else now + 40 * 60
        return now + 40 * 60  # 「等下 / 晚点」：按 40 分钟算
    return 0.0


def add(group_id, text: str, to_name: str = "", to_uid: str = "", now: float | None = None) -> dict | None:
    if not enabled():
        return None
    now = time.time() if now is None else now
    due = detect(text, now)
    if not due:
        return None
    item = {"text": text.strip()[:80], "to": to_name[:20], "uid": str(to_uid or ""), "ts": now, "due": due}

    def _u(lst):
        lst = [x for x in lst if x.get("text") != item["text"]]
        return (_fresh(lst, now) + [item])[-MAX_PER_GROUP:]
    db.update(NS, str(group_id), _u, default=[])
    return item


def _fresh(lst, now: float) -> list:
    """去掉坏数据和过期一天还没提醒的（比如机器人那天没开）。"""
    return [x for x in (lst or []) if isinstance(x, dict) and float(x.get("due") or 0) > now - 86400]


# ---------------- 替群友记的提醒（remind_later） ----------------
_CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0}
_WEEK = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "末": 5}


def _num(t: str) -> int | None:
    t = (t or "").strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    if t == "十":
        return 10
    if "十" in t:
        a, _, b = t.partition("十")
        return (_CN.get(a, 1) if a else 1) * 10 + (_CN.get(b, 0) if b else 0)
    if all(c in _CN for c in t):
        n = 0
        for c in t:
            n = n * 10 + _CN[c]
        return n
    return None


_N = r"([0-9]{1,3}|[零〇一二两三四五六七八九十]{1,3})"
_REL = re.compile(_N + r"\s*(分钟|分|min|小时|个小时|钟头|个钟头|h|天)\s*(后|以后|之后)?", re.I)
_HALF = re.compile(r"(半小时|半个小时|半个钟头)")
_DAY = re.compile(r"(今天|今日|今晚|今早|明天|明日|明早|明晚|明儿|后天|大后天|下?周[一二三四五六日天末]|下?星期[一二三四五六日天]|礼拜[一二三四五六日天])")
_CLOCK = re.compile(r"(早上|上午|中午|下午|晚上|傍晚|凌晨|夜里|半夜)?\s*" + _N + r"\s*(?:[:：]\s*([0-9]{1,2})|点\s*(半|一刻|三刻|" + _N + r"\s*分?)?)")
_DATE = re.compile(r"(20\d\d)[-/.年]\s*(\d{1,2})[-/.月]\s*(\d{1,2})日?|(\d{1,2})月(\d{1,2})[日号]")


def parse_when(text: str, now: float | None = None) -> float:
    """「20:30」「8点半」「明天早上9点」「30分钟后」「下周一」「11月3日 10点」→ 时间戳；看不懂或已经过去返回 0。"""
    now = time.time() if now is None else now
    t = (text or "").strip()
    if not t:
        return 0.0
    base = datetime.fromtimestamp(now)
    if _HALF.search(t):
        return now + 1800
    m = _REL.search(t)
    if m and not _CLOCK.search(t.replace(m.group(0), "")):
        n = _num(m.group(1)) or 0
        unit = m.group(2).lower()
        sec = n * (86400 if unit == "天" else 3600 if unit in ("小时", "个小时", "钟头", "个钟头", "h") else 60)
        if unit == "天":
            # 「3天后」不说几点就按早上 10 点
            return _ok((base + timedelta(days=n)).replace(hour=10, minute=0, second=0).timestamp(), now)
        return _ok(now + sec, now) if sec >= 60 else 0.0
    day = base.replace(hour=0, minute=0, second=0, microsecond=0)
    explicit_day = False
    d = _DATE.search(t)
    if d:
        try:
            if d.group(1):
                day = day.replace(year=int(d.group(1)), month=int(d.group(2)), day=int(d.group(3)))
            else:
                day = day.replace(month=int(d.group(4)), day=int(d.group(5)))
                if day < base.replace(hour=0, minute=0, second=0, microsecond=0):
                    day = day.replace(year=day.year + 1)
            explicit_day = True
        except ValueError:
            return 0.0
    dm = _DAY.search(t)
    word = dm.group(1) if dm else ""
    if word and not d:
        explicit_day = True
        if word.startswith(("明", "明儿")):
            day += timedelta(days=1)
        elif word == "后天":
            day += timedelta(days=2)
        elif word == "大后天":
            day += timedelta(days=3)
        elif word[-1] in _WEEK and ("周" in word or "星期" in word or "礼拜" in word):
            target = _WEEK[word[-1]]
            if word.startswith("下"):  # 下周X = 下个星期的那一天
                ahead = 7 - base.weekday() + target
            else:
                ahead = (target - base.weekday()) % 7
            day += timedelta(days=ahead)
    c = _CLOCK.search(t)
    if c:
        part, hour = c.group(1) or "", _num(c.group(2))
        if hour is None or hour > 24:
            return 0.0
        if c.group(3):
            minute = int(c.group(3))
        else:
            tail = (c.group(4) or "").strip()
            minute = {"半": 30, "一刻": 15, "三刻": 45}.get(tail)
            if minute is None:
                minute = _num(tail.rstrip("分")) or 0 if tail else 0
        if minute > 59:
            return 0.0
        if part in ("下午", "晚上", "傍晚", "夜里") and hour < 12:
            hour += 12
        elif part == "中午" and hour < 6:
            hour += 12
        elif part in ("半夜", "凌晨") and hour == 12:
            hour = 0
        elif not part and word in ("今晚", "明晚") and hour < 12:
            hour += 12
        hour %= 24
        due = day.replace(hour=hour, minute=minute).timestamp()
        if not explicit_day and due <= now:
            # 「8点」已经过了：没说下午就先当晚上 8 点，晚上也过了就当明天
            if not part and hour < 12 and day.replace(hour=hour + 12, minute=minute).timestamp() > now:
                due = day.replace(hour=hour + 12, minute=minute).timestamp()
            else:
                due = (day + timedelta(days=1)).replace(hour=hour, minute=minute).timestamp()
        return _ok(due, now)
    if explicit_day:
        hour = {"今晚": 20, "明晚": 20, "今早": 9, "明早": 9}.get(word, 10)
        due = day.replace(hour=hour, minute=0).timestamp()
        return _ok(due if due > now else now + 1800, now)
    return 0.0


def _ok(due: float, now: float) -> float:
    return due if now + 30 <= due <= now + MAX_DAYS * 86400 else 0.0


def add_reminder(group_id, what: str, when: str, who_name: str = "", who_uid: str = "", by_name: str = "",
                 now: float | None = None) -> dict:
    """替群友记一条提醒。返回 {ok, due, ...} 或 {ok: False, reason}。"""
    if not enabled():
        return {"ok": False, "reason": "提醒这个功能没开，跟对方说你记不住就好"}
    now = time.time() if now is None else now
    what = re.sub(r"\s+", " ", str(what or "")).strip()[:80]
    if not what:
        return {"ok": False, "reason": "what 要写清楚提醒什么"}
    due = parse_when(when, now)
    if not due:
        return {"ok": False, "reason": "没看懂时间，when 写成「20:30」「明天早上9点」「30分钟后」「下周一10点」这样（最多 30 天内）"}
    uid = str(who_uid or "")
    items = _fresh(db.get(NS, str(group_id)) or [], now)
    if uid and len([x for x in items if x.get("kind") == "remind" and x.get("uid") == uid]) >= MAX_PER_PERSON:
        return {"ok": False, "reason": f"TA 已经让你记了 {MAX_PER_PERSON} 件事还没到点，先别再记了"}
    item = {"kind": "remind", "text": what, "to": (who_name or "")[:20], "uid": uid, "by": (by_name or "")[:20],
            "ts": now, "due": due}

    def _u(lst):
        lst = [x for x in _fresh(lst, now) if not (x.get("kind") == "remind" and x.get("uid") == uid and x.get("text") == what)]
        return (lst + [item])[-MAX_PER_GROUP:]
    db.update(NS, str(group_id), _u, default=[])
    return {"ok": True, **item}


def due(group_id, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    return [x for x in (db.get(NS, str(group_id)) or []) if isinstance(x, dict) and float(x.get("due") or 0) <= now]


def groups_due(now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    return [str(k) for k, v in (db.items(NS) or {}).items()
            if isinstance(v, list) and any(isinstance(x, dict) and float(x.get("due") or 0) <= now for x in v)]


def take_note(group_id, now: float | None = None) -> str:
    """拿出到点的事写成提示（拿出来就从本子上划掉：每件事只提醒一次）。"""
    now = time.time() if now is None else now
    items = due(group_id, now)
    if not items:
        return ""
    keys = {(x.get("text"), x.get("ts")) for x in items}
    db.update(NS, str(group_id), lambda lst: [x for x in (lst or []) if (x.get("text"), x.get("ts")) not in keys], default=[])
    out = []
    reminds = [x for x in items if x.get("kind") == "remind"][:5]
    promises = [x for x in items if x.get("kind") != "remind"][:3]
    if reminds:
        lines = []
        for x in reminds:
            asked = time.strftime("%m-%d %H:%M", time.localtime(float(x["ts"])))
            who = x.get("to") or "TA"
            by = x.get("by") or ""
            src = f"{by}让你提醒{who}" if by and by != who else f"{who}让你提醒 TA"
            at = f"（at={x['uid']}）" if x.get("uid") else ""
            late = now - float(x["due"]) > LATE_APOLOGY
            lines.append(f"- {asked} {src}：「{x['text']}」{at}" + ("（机器人刚才没开，已经晚了，顺口说一声晚了）" if late else ""))
        out.append("【到点要提醒的人】\n" + "\n".join(lines) + "\n"
                   "用 qq_say 艾特对方，用你自己的话提醒一句（像朋友顺口喊一声，别像闹钟播报，别说「提醒时间到」「根据记录」）。")
    if promises:
        lines = []
        for x in promises:
            when = time.strftime("%H:%M", time.localtime(float(x["ts"])))
            who = f"跟{x['to']}" if x.get("to") else "在群里"
            late = now - float(x["due"]) > LATE_APOLOGY
            lines.append(f"- 你 {when} {who}说过「{x['text']}」" + ("（已经拖了一个多小时，先道个歉）" if late else ""))
        out.append("【你答应过的事，到点了】\n" + "\n".join(lines) + "\n"
                   "用你自己的话兑现：能做的就真去做（查 / 找 / 看 / 想好了再说），给出结果；做不到或者忘了细节就直说一声，"
                   "别假装做了，也别提「日历」「本子」这些词。对方已经不在意或者事情过去了，就自然带过或者 qq_silence。")
    return "\n\n".join(out)


def pending_count(group_id) -> int:
    return len(_fresh(db.get(NS, str(group_id)) or [], time.time()))
