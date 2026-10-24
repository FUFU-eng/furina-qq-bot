"""记住群友提过的近况（明天考试、周五面试、生病了……），过后找机会顺口问一句结果。

- 只用正则识别「本人 + 时间词 + 事件」的陈述句，不调用模型；问句、假设句（如果/要是）、
  否定句（明天不考了）和说别人的事（他/她/你）都不记；
- 存 SQLite（命名空间 life_events，键 "群号:QQ"），每人最多保留 5 件；
- 事情过去后 7 天内，TA 再说话时把「可以顺口关心一下」写进提示词；每件事只问一次，
  每人每天最多问一次。真问没问由模型看语境决定（对方在聊正事或心情差就不硬问）。
"""
from __future__ import annotations

import datetime as _dt
import re
import time

from nonebot import logger

from . import config
from ...core import db

_NS = "life_events"
_MAX_PER_USER = 5
_EXPIRE_DAYS = 7

_EVENTS: list[tuple[str, str]] = [
    ("考试", r"期末考?|期中考?|考研|四级|六级|考试|月考|模考|笔试|考科目[一二三四]|科目[一二三四]|路考|驾照考试|考驾照"),
    ("面试", r"面试|复试|二面|三面|终面|群面"),
    ("答辩", r"答辩"),
    ("生日", r"生日"),
    ("搬家", r"搬家"),
    ("看病", r"体检|手术|复查|拔牙|看牙|去医院|看医生|打疫苗|做胃镜"),
    ("出门", r"出差|旅游|旅行|出去玩|回老家|回家|坐飞机|赶飞机|坐高铁"),
    ("演出比赛", r"演唱会|音乐节|比赛|决赛|演出|漫展|cos"),
    ("新开始", r"入职|报到|报道|开学|上班第一天|实习"),
    ("约会", r"约会|相亲|见网友|面基|见家长"),
    ("出成绩", r"出成绩|查成绩|出分|放榜|出结果"),
]
_EVENT_RE = re.compile("|".join(f"(?P<e{i}>{p})" for i, (_, p) in enumerate(_EVENTS)), re.I)

_SICK_RE = re.compile(r"(?:我|俺|本人).{0,4}(?:感冒|发烧|发热|生病|病了|肚子疼|胃疼|头疼|头好疼|嗓子疼|喉咙疼|不舒服|中暑|过敏)")

_CN_WD = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "末": 5}
_TIME_RE = re.compile(
    r"(?P<rel>大后天|后天|明天|明早|明晚|明儿|今天|今晚|今早|今儿|今夜|待会|等会|一会儿?)"
    r"|(?P<next>下个?)?(?:周|星期|礼拜)(?P<wd>[一二三四五六日天末])"
    r"|(?P<nextweek>下周|下星期|下礼拜)"
    r"|(?P<dom>\d{1,2})[号日]"
)
_DAYPART_RE = re.compile(r"上午|早上|中午|下午|待会|等会|一会")
_SKIP_RE = re.compile(r"[?？]|吗|么$|怎么|什么时候|要不要|是不是|如果|要是|假如|万一|假设|梦到|梦见")
_NEG_RE = re.compile(r"(?:不|没|别|不用|取消|推迟|延期)(?:用|要|去|参加|考|是)?\s*$")
_OTHER_RE = re.compile(r"[他她你]|别人|朋友|同学|室友|舍友|我妈|我爸|老妈|老爸|对象|男朋友|女朋友|老婆|老公|儿子|女儿")


def _offset_days(m: re.Match, now: _dt.datetime) -> int | None:
    if m.group("rel"):
        rel = m.group("rel")
        if rel.startswith("大后"):
            return 3
        if rel.startswith("后"):
            return 2
        if rel.startswith("明"):
            return 1
        return 0
    if m.group("wd"):
        wd = _CN_WD[m.group("wd")]
        delta = (wd - now.weekday()) % 7
        if m.group("next"):
            delta = (7 - now.weekday()) + wd
        return delta
    if m.group("nextweek"):
        return 7
    if m.group("dom"):
        day = int(m.group("dom"))
        if not 1 <= day <= 31:
            return None
        if day >= now.day:
            return day - now.day
        first_next = (now.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)
        try:
            target = first_next.replace(day=day)
        except ValueError:
            return None
        delta = (target.date() - now.date()).days
        return delta if delta <= 31 else None
    return None


def detect(text: str, now: float | None = None) -> dict | None:
    """识别一句话里本人提到的近期安排，返回 {event, text, due}；不是就返回 None。"""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if not t or len(t) > 80 or _SKIP_RE.search(t):
        return None
    ts = time.time() if now is None else now
    now_dt = _dt.datetime.fromtimestamp(ts)

    sick = _SICK_RE.search(t)
    if sick and not _OTHER_RE.search(t):
        return {"event": "身体不舒服", "text": t[:40], "due": ts + 20 * 3600}

    em = _EVENT_RE.search(t)
    tm = _TIME_RE.search(t)
    if not em or not tm:
        return None
    if _NEG_RE.search(t[: em.start()]):
        return None
    # 说的是别人的事（他/她/你/朋友…）不记；没有主语、或主语是「我」才算本人
    if _OTHER_RE.search(t[: em.start()]):
        return None
    if "我" not in t and tm.start() > 6:
        return None
    label = next(_EVENTS[i][0] for i in range(len(_EVENTS)) if em.group(f"e{i}"))
    offset = _offset_days(tm, now_dt)
    if offset is None or offset > 31:
        return None
    day = (now_dt + _dt.timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
    daypart = bool(_DAYPART_RE.search(t))
    if offset == 0:
        due = ts + 3 * 3600 if daypart else (day + _dt.timedelta(days=1, hours=7)).timestamp()
    else:
        due = (day + _dt.timedelta(hours=20)).timestamp() if daypart else (day + _dt.timedelta(days=1, hours=7)).timestamp()
    return {"event": label, "text": t[:40], "due": float(due)}


def _key(group_id, user_id) -> str:
    return f"{group_id}:{user_id}"


def _today(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))


def record(group_id, user_id, nickname: str, text: str, now: float | None = None) -> dict | None:
    """有近况就记下来（同类事件且时间相近的覆盖旧的），返回记下的事件。"""
    if not config.LIFE_EVENTS:
        return None
    ev = detect(text, now)
    if not ev:
        return None
    ts = time.time() if now is None else now
    ev.update(said=ts, asked=False, name=(nickname or "")[:20])

    def _u(data):
        data = dict(data or {})
        items = [e for e in (data.get("events") or []) if isinstance(e, dict)
                 and float(e.get("due") or 0) + _EXPIRE_DAYS * 86400 > ts]
        items = [e for e in items if not (e.get("event") == ev["event"] and abs(float(e.get("due") or 0) - ev["due"]) < 86400)]
        items.append(ev)
        data["events"] = items[-_MAX_PER_USER:]
        return data

    try:
        db.update(_NS, _key(group_id, user_id), _u)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AI聊天] 记录群友近况失败: {e}")
        return None
    logger.info(f"[AI聊天] 群{group_id} 记下 {nickname} 的近况：{ev['event']}「{ev['text']}」")
    return ev


def pending(group_id, user_id, now: float | None = None) -> dict | None:
    """事情已经过去、还没问过、今天也还没问过 TA：返回该事件，否则 None。"""
    if not config.LIFE_EVENTS:
        return None
    ts = time.time() if now is None else now
    data = db.get(_NS, _key(group_id, user_id)) or {}
    if data.get("asked_day") == _today(ts):
        return None
    for e in data.get("events") or []:
        if not isinstance(e, dict) or e.get("asked"):
            continue
        due = float(e.get("due") or 0)
        if due <= ts <= due + _EXPIRE_DAYS * 86400:
            return e
    return None


def mark_asked(group_id, user_id, ev: dict, now: float | None = None) -> None:
    ts = time.time() if now is None else now

    def _u(data):
        data = dict(data or {})
        for e in data.get("events") or []:
            if isinstance(e, dict) and e.get("event") == ev.get("event") and e.get("said") == ev.get("said"):
                e["asked"] = True
        data["asked_day"] = _today(ts)
        return data

    try:
        db.update(_NS, _key(group_id, user_id), _u)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AI聊天] 更新群友近况失败: {e}")


def _ago(said: float, now: float) -> str:
    d = (_dt.date.fromtimestamp(now) - _dt.date.fromtimestamp(said)).days
    if d <= 0:
        return "今天早些时候"
    if d == 1:
        return "昨天"
    if d == 2:
        return "前天"
    return f"{d} 天前"


def followup_note(nickname: str, ev: dict, now: float | None = None) -> str:
    ts = time.time() if now is None else now
    return (
        f"【可以顺口关心一下】{nickname} {_ago(float(ev.get('said') or ts), ts)}说过「{ev.get('text', '')}」，"
        "那件事现在应该已经过去了。如果这次的话题允许，就像朋友突然想起来一样随口问一句结果"
        "（比如考得咋样、面试顺不顺、好点没），一句就够，别像查岗；"
        "对方正在问你正事、或者情绪不好时就先别问。"
    )
