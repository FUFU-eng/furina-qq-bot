"""活着的痕迹（参考「小鲸鱼」ai-companion-kit 的心跳 / 发送失败记账 / 主动找你）。

- 时间感：记下每个人上次在群里说话的时间；隔了很久才回来，她会知道「TA 上次说话是几天前」；
- 睡了多久：每分钟写一次心跳；重启时发现离线超过 AI_CHAT_PRESENCE_SLEEP_MINUTES，就当自己「睡了一觉」，
  之后 3 小时内每个群最多两轮回复里提醒她（有人问去哪了就像真人一样说睡着了，不提机器人/服务器）；
  不发「我上线了」，也不把离线期间的消息挨个补回；
- 没发出去的话：限速 / 熔断 / 发送异常没发出去的句子记下来，下一两次回复时告诉她「这几句群里没人看到，不是你的错」，
  24 小时后自动忘掉；（连续 3 次失败熔断 3 分钟在 humanize 里）
- 惦记常聊的人：14 天里常艾特她的人，超过 AI_CHAT_MISS_AFTER_HOURS 没在群里出现，她会在群里艾特 TA 随口说一两句；
  每群 45~110 分钟才「想起来」一次、想起来也只有一定概率真去找（心情好更容易想起人）；
  同一个人至少隔几天；连着 3 次没回就不再找 TA，等 TA 自己出现再说。各群在「功能权限 → 惦记常聊的人」开关（默认开）。
"""
from __future__ import annotations

import random
import time

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import MessageSegment

from . import boundary, config, dice, humanize, llm, memory
from ...core import db, live_groups, permissions, scheduler, usage as daily_limits
from ...core.botref import bind as bind_bot

NS = "presence"
FEATURE = "miss"
JOB_HB = "在线心跳"
JOB_MISS = "惦记常聊的人"
RETURN_HOURS = 12          # 隔了这么久再说话，算「好久没出现」
RETURN_NOTE_TTL = 600      # 回来后多久内的回复里会提到
WAKE_NOTE_HOURS = 3        # 醒来后多久内会提醒她刚睡醒
WAKE_NOTES_PER_GROUP = 2
UNSENT_KEEP_HOURS = 24
UNSENT_TELL_TIMES = 2
FREQUENT_DAYS = 14
KEEP_PEOPLE_DAYS = 30

_people: dict[str, dict[str, dict]] = {}
_dirty: set[str] = set()
_returning: dict[tuple[str, str], dict] = {}
_next_check: dict[str, float] = {}
_freq_cache: dict = {"ts": 0.0, "map": {}}
_tasks: dict = {}


# ---------------- 通用 ----------------
def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 3600:
        return f"{max(1, s // 60)} 分钟"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h} 个小时" if m < 15 else (f"{h} 个半小时" if m < 45 else f"{h + 1} 个小时")
    d = s // 86400
    return f"{d} 天" if d < 30 else "一个多月"


def _hm(ts: float) -> str:
    lt = time.localtime(ts)
    today = time.localtime().tm_yday
    prefix = "" if lt.tm_yday == today else ("昨天" if (today - lt.tm_yday) % 366 == 1 else time.strftime("%m月%d日", lt))
    return prefix + time.strftime("%H:%M", lt)


# ---------------- 时间感：每个人上次说话 ----------------
def _load(gid: str) -> dict[str, dict]:
    if gid not in _people:
        data = db.get(NS, "people:" + gid) or {}
        _people[gid] = {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
    return _people[gid]


def flush(now: float | None = None) -> None:
    ts = time.time() if now is None else now
    for gid in list(_dirty):
        recs = _load(gid)
        for uid in [u for u, r in recs.items() if ts - float(r.get("last") or 0) > KEEP_PEOPLE_DAYS * 86400]:
            recs.pop(uid, None)
        db.put(NS, "people:" + gid, recs)
        _dirty.discard(gid)


def _history_last(gid: str, uid: str, before: float) -> float:
    """内存里的群聊记录中 TA 最后一次说话的时间（不含刚收到的这条）。"""
    try:
        hist = memory.get_history(int(gid))
    except Exception:  # noqa: BLE001
        return 0.0
    for e in reversed(hist):
        if e.get("role") == "user" and str(e.get("uid") or "") == uid:
            ts = float(e.get("ts") or 0)
            if ts and ts < before - 2:
                return ts
    return 0.0


def seen(group_id, user_id, nickname: str, text: str = "", now: float | None = None) -> None:
    """群友说了一句话：更新 TA 的最后出现时间；隔了很久才回来就记一笔，供这轮回复参考。"""
    if not config.PRESENCE and not config.MISS_ENABLED:
        return
    ts = time.time() if now is None else now
    gid, uid = str(group_id), str(user_id)
    recs = _load(gid)
    rec = recs.get(uid) or {}
    prev = float(rec.get("last") or 0) or _history_last(gid, uid, ts)
    if prev and ts - prev >= RETURN_HOURS * 3600:
        _returning[(gid, uid)] = {"gap": ts - prev, "ts": ts, "missed": int(rec.get("miss_unanswered") or 0) > 0,
                                  "miss_text": rec.get("miss_text") or "", "name": nickname}
    lines = list(rec.get("lines") or [])
    body = (text or "").strip().replace("\n", " ")
    if body:
        lines = (lines + [body[:60]])[-3:]
    rec.update(name=nickname, last=ts, lines=lines, miss_unanswered=0)
    recs[uid] = rec
    _dirty.add(gid)


def _fresh_return(gid: str, uid: str, now: float) -> dict | None:
    r = _returning.get((gid, uid))
    if r and now - r["ts"] <= RETURN_NOTE_TTL:
        return r
    if r:
        _returning.pop((gid, uid), None)
    return None


def welcome_back_probability(group_id, user_id, now: float | None = None) -> float:
    """常聊的人隔了很久回来：随机接话概率提到多少（她惦记过 TA 就更想理 TA）。"""
    ts = time.time() if now is None else now
    gid, uid = str(group_id), str(user_id)
    r = _fresh_return(gid, uid, ts)
    if not r:
        return 0.0
    if r["missed"]:
        return max(0.0, min(1.0, float(config.WELCOME_BACK_MISSED_PROBABILITY)))
    return max(0.0, min(1.0, float(config.WELCOME_BACK_PROBABILITY))) if uid in frequent(gid, ts) else 0.0


def absence_note(group_id, user_id, nickname: str, now: float | None = None) -> str:
    ts = time.time() if now is None else now
    gid, uid = str(group_id), str(user_id)
    r = _fresh_return(gid, uid, ts)
    if not r:
        return ""
    _returning.pop((gid, uid), None)
    note = f"【时间感】{nickname} 上次在群里说话是{_ago(r['gap'])}前，隔了挺久才冒出来。"
    if r["missed"] and r["miss_text"]:
        note += f"你前阵子还在群里艾特过 TA（「{r['miss_text']}」），TA 一直没回。"
    note += "可以像熟人那样自然带一句（比如嫌 TA 消失太久、问 TA 去忙啥了，别照抄），也可以不提；别像在查考勤。"
    return note


# ---------------- 常聊的人 ----------------
def frequent(group_id, now: float | None = None) -> dict[str, int]:
    """14 天里艾特她至少 MISS_MIN_TALKS 次、分布在至少 MISS_MIN_DAYS 天的人：{uid: 次数}。"""
    ts = time.time() if now is None else now
    if ts - _freq_cache["ts"] > 600:
        since = time.strftime("%Y-%m-%d", time.localtime(ts - FREQUENT_DAYS * 86400))
        agg: dict[tuple[str, str], list] = {}
        try:
            rows = db.usage_rows(since, ("chat",))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[惦记] 读取聊天统计失败: {e}")
            rows = []
        for day, _kind, g, u, n in rows:
            if not u:
                continue
            item = agg.setdefault((str(g), str(u)), [0, set(), ""])
            item[0] += int(n or 0)
            item[1].add(day)
            item[2] = max(item[2], day)
        _freq_cache.update(ts=ts, map=agg)
    skip = set(config.PEER_BOTS) | {str(config.BOT_QQ or "")}
    out = {}
    for (g, u), (n, days, _last) in _freq_cache["map"].items():
        if g == str(group_id) and u not in skip and n >= config.MISS_MIN_TALKS and len(days) >= config.MISS_MIN_DAYS \
                and not boundary.ignoring(u):
            out[u] = n
    return out


def _last_day_ts(gid: str, uid: str) -> float:
    item = _freq_cache["map"].get((gid, uid))
    if not item or not item[2]:
        return 0.0
    try:
        return time.mktime(time.strptime(item[2], "%Y-%m-%d")) + 12 * 3600
    except ValueError:
        return 0.0


def _last_seen(gid: str, uid: str, now: float) -> float:
    rec = _load(gid).get(uid) or {}
    return float(rec.get("last") or 0) or _history_last(gid, uid, now + 10) or min(now, _last_day_ts(gid, uid))


def candidates(group_id, now: float | None = None) -> list[tuple[str, int, float]]:
    """现在可以惦记的人：[(uid, 常聊次数, 离开秒数)]。纯判断，方便测试。"""
    ts = time.time() if now is None else now
    gid = str(group_id)
    out = []
    for uid, talks in frequent(gid, ts).items():
        last = _last_seen(gid, uid, ts)
        if not last:
            continue
        gap = ts - last
        if gap < config.MISS_AFTER_HOURS * 3600 or gap > FREQUENT_DAYS * 86400:
            continue
        rec = _load(gid).get(uid) or {}
        if ts - float(rec.get("miss_ts") or 0) < config.MISS_GAP_DAYS * 86400:
            continue
        if int(rec.get("miss_unanswered") or 0) >= max(1, config.MISS_MAX_UNANSWERED):
            continue
        out.append((uid, talks, gap))
    return out


def _day_state(gid: str, now: float) -> dict:
    st = db.get(NS, "miss_day:" + gid) or {}
    return st if st.get("day") == time.strftime("%Y-%m-%d", time.localtime(now)) else {}


def _mood_factor() -> float:
    try:
        from . import emotion
        mood = emotion.get_mood()
    except Exception:  # noqa: BLE001
        return 1.0
    return 1.3 if mood >= 75 else (0.7 if mood <= 40 else 1.0)


MISS_PROMPT = """【你突然想起一个人】{name} 已经{ago}没在群里说话了，平时 TA 挺常找你聊的。
{lines}{life}{prev}{tone}
你想像真人朋友那样，在群里艾特 TA 随口说一两句，流露一点点惦记：可以接 TA 上次说的事、问那件事后来怎么样、吐槽 TA 最近消失了，或者说你刚好看到什么想起了 TA。
别肉麻，别说「我好想你」「好久不见」「最近还好吗」这种客套话；别提你在看记录、别提数据和天数统计。
写一到两行，每行就是一条消息、每条不超过二十五个字；不要写 TA 的名字（会自动艾特 TA），不要用括号写动作，不要输出任何 [标记]。"""


async def compose_miss(gid: str, self_id: str, uid: str, gap: float) -> str:
    from . import proactive  # 延迟导入：共用人设拼装

    rec = _load(gid).get(uid) or {}
    name = rec.get("name") or "TA"
    lines = rec.get("lines") or []
    lines_txt = ("TA 最后说过的几句：\n" + "\n".join(f"- {x}" for x in lines) + "\n") if lines else ""
    life = ""
    try:
        from . import life_events
        ev = life_events.pending(int(gid), uid)
        if ev:
            life = f"TA 之前提过「{ev.get('text', '')}」，那件事现在应该已经过去了，可以问问结果。\n"
    except Exception:  # noqa: BLE001
        pass
    prev = f"你上次找 TA 说的是「{rec.get('miss_text')}」，这次换个说法。\n" if rec.get("miss_text") else ""
    tone = ""
    try:
        from . import emotion, favor
        tone = "\n".join(x for x in (emotion.get_emotion_note(), emotion.get_emotion_for_user(uid), favor.favor_note(uid)) if x)
        tone = (tone + "\n") if tone else ""
    except Exception:  # noqa: BLE001
        pass
    system = proactive.base_system(gid, self_id)
    user = MISS_PROMPT.format(name=name, ago=_ago(gap), lines=lines_txt, life=life, prev=prev, tone=tone)
    try:
        raw = await llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    except llm.LLMError as e:
        logger.warning(f"[惦记] 群{gid} 生成失败: {e}")
        return ""
    text = proactive._clean(raw, 2)
    if config.NO_PERIOD:
        from .cleanup import drop_periods
        text = drop_periods(text)
    return text if len(text.replace("\n", "")) >= 2 else ""


def _mark_missed(gid: str, uid: str, text: str, now: float) -> None:
    recs = _load(gid)
    rec = recs.setdefault(uid, {"last": _last_seen(gid, uid, now)})
    rec["miss_ts"] = now
    rec["miss_unanswered"] = int(rec.get("miss_unanswered") or 0) + 1
    rec["miss_text"] = text.replace("\n", " ")[:40]
    _dirty.add(gid)

    def _u(st):
        st = dict(st or {})
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if st.get("day") != day:
            st = {"day": day, "count": 0}
        st["count"] = int(st.get("count") or 0) + 1
        return st
    db.update(NS, "miss_day:" + gid, _u)
    flush(now)


async def miss_tick(now: float | None = None, rng: random.Random | None = None) -> None:
    r = rng or random
    if not config.MISS_ENABLED:
        return
    from . import proactive

    ts = time.time() if now is None else now
    if not proactive.in_active_hours(ts):
        return
    groups = set(permissions.enabled_groups(FEATURE))
    live = live_groups.cached()
    if live:
        groups &= live
    if not groups:
        return
    bot, sid = proactive._pick_bot()
    if bot is None:
        return
    bind_bot(self_id=sid)
    for gid in sorted(groups):
        due = _next_check.get(gid)
        if due is None:  # 刚启动：先等一会儿再想人，别一上线就挨个艾特
            _next_check[gid] = ts + r.uniform(20, 60) * 60
            continue
        if ts < due:
            continue
        _next_check[gid] = ts + r.uniform(45, 110) * 60
        cap = config.MISS_DAILY_MAX
        if cap > 0 and int(_day_state(gid, ts).get("count") or 0) >= cap:
            continue
        try:
            hist = memory.get_history(int(gid))
        except (TypeError, ValueError):
            continue
        if hist and hist[-1].get("role") == "assistant" and ts - float(hist[-1].get("ts") or 0) < 600:
            continue  # 她刚说过话，等等再说
        cands = candidates(gid, ts)
        if not cands:
            continue
        since = ts - float(hist[-1].get("ts") or 0) if hist and hist[-1].get("role") == "assistant" else None
        hit, why = dice.roll(f"miss:{gid}", config.MISS_PROBABILITY * _mood_factor(), ts, since, r)  # 本地掷，零 token
        logger.debug(f"[惦记] 群{gid} {why}")
        if not hit:
            continue
        uid, _talks, gap = r.choices(cands, weights=[c[1] for c in cands])[0]
        text = await compose_miss(gid, sid, uid, gap)
        if not text:
            continue
        gid_int = int(gid)
        sender = humanize.guarded(lambda m, g=gid_int: bot.send_group_msg(group_id=g, message=m), gid_int)
        ok = False
        for idx, line in enumerate(text.split("\n")):
            if idx:
                await humanize.pause(humanize.burst_gap(line))
            msg = (MessageSegment.at(int(uid)) + MessageSegment.text(" " + line)) if idx == 0 else MessageSegment.text(line)
            if await sender(msg) is None:
                break
            ok = True
        if not ok:
            continue
        name = (_load(gid).get(uid) or {}).get("name") or uid
        memory.add_message(gid_int, "assistant", f"（艾特了{name}）" + text)
        _mark_missed(gid, uid, text, ts)
        daily_limits.track(FEATURE, gid_int)
        logger.info(f"[惦记] 群{gid} {name} 已经{_ago(gap)}没出现，她艾特了 TA：{text}")
        try:
            from . import emotion
            emotion.spawn_judge(gid, uid, name, f"{name} 好久没出现，想起了 TA", text, mode="talk")
        except Exception:  # noqa: BLE001
            pass


# ---------------- 睡了多久 ----------------
def check_wake(now: float | None = None) -> dict | None:
    """启动时调用：离线够久就记一笔「睡了一觉」。"""
    ts = time.time() if now is None else now
    last = float(db.get(NS, "hb") or 0)
    db.put(NS, "hb", ts)
    if not config.PRESENCE or not last or ts - last < max(1, config.PRESENCE_SLEEP_MINUTES) * 60:
        return None
    wake = {"from": last, "at": ts, "told": {}}
    db.put(NS, "wake", wake)
    logger.info(f"[活着的痕迹] 离线了 {_ago(ts - last)}（{_hm(last)} → {_hm(ts)}），醒来后会当自己睡了一觉")
    return wake


def wake_note(group_id, now: float | None = None) -> str:
    if not config.PRESENCE:
        return ""
    ts = time.time() if now is None else now
    w = db.get(NS, "wake") or {}
    if not w or ts - float(w.get("at") or 0) > WAKE_NOTE_HOURS * 3600:
        return ""
    gid = str(group_id)
    told = dict(w.get("told") or {})
    if int(told.get(gid) or 0) >= WAKE_NOTES_PER_GROUP:
        return ""
    told[gid] = int(told.get(gid) or 0) + 1
    w["told"] = told
    db.put(NS, "wake", w)
    start, end = float(w.get("from") or ts), float(w.get("at") or ts)
    return (f"【你刚醒】你从{_hm(start)}到{_hm(end)}一直不在（睡了{_ago(end - start)}），这段时间群里说了什么你都没看到、也没回。"
            "有人问你去哪了、怎么不回，就像真人一样随口说睡着了 / 没看手机 / 刚忙完；没人问就不用主动提。"
            "绝对不要提机器人、服务器、程序、掉线、重启这些词。")


# ---------------- 没发出去的话 ----------------
def record_unsent(group_id, text: str, why: str = "", now: float | None = None) -> None:
    text = (text or "").strip()
    if not config.PRESENCE or not text or not group_id:
        return
    ts = time.time() if now is None else now

    def _u(lst):
        lst = [x for x in (lst or []) if ts - float(x.get("ts") or 0) <= UNSENT_KEEP_HOURS * 3600]
        lst.append({"text": text[:80], "why": why[:40], "ts": ts, "told": 0})
        return lst[-5:]
    db.update(NS, f"unsent:{group_id}", _u)


def unsent_note(group_id, now: float | None = None) -> str:
    if not config.PRESENCE:
        return ""
    ts = time.time() if now is None else now
    key = f"unsent:{group_id}"
    lst = [x for x in (db.get(NS, key) or []) if ts - float(x.get("ts") or 0) <= UNSENT_KEEP_HOURS * 3600
           and int(x.get("told") or 0) < UNSENT_TELL_TIMES]
    if not lst:
        db.delete(NS, key)
        return ""
    for x in lst:
        x["told"] = int(x.get("told") or 0) + 1
    db.put(NS, key, [x for x in lst if x["told"] < UNSENT_TELL_TIMES])
    items = "\n".join(f"- 「{x['text']}」" for x in lst[-3:])
    return ("【没发出去的话】你之前这几句其实没发出去，群里没人看到——不是你的错，也不是别人不理你：\n" + items +
            "\n还想说的话可以自然地再说一遍（换个说法），不想说就算了；最多像真人那样提一句「刚才没发出去」，别提系统、程序。")


def turn_notes(group_id, user_id, nickname: str) -> list[str]:
    """这一轮回复要带上的「活着的痕迹」提示。"""
    notes = []
    for fn in (lambda: wake_note(group_id), lambda: unsent_note(group_id),
               lambda: absence_note(group_id, user_id, nickname)):
        try:
            text = fn()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[活着的痕迹] 生成提示失败: {e}")
            text = ""
        if text:
            notes.append(text)
    return notes


async def heartbeat() -> None:
    db.put(NS, "hb", time.time())
    flush()


@get_driver().on_startup
async def _start():
    try:
        check_wake()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[活着的痕迹] 读取心跳失败: {e}")
    if not _tasks:
        _tasks["hb"] = scheduler.every(JOB_HB, 60, heartbeat, first_delay=60, quiet=True)
        _tasks["miss"] = scheduler.every(JOB_MISS, 60, miss_tick, first_delay=180)


@get_driver().on_shutdown
async def _stop():
    try:
        await heartbeat()
    except Exception:  # noqa: BLE001
        pass
    for name in (JOB_HB, JOB_MISS):
        await scheduler.stop(name)
    _tasks.clear()
