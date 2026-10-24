"""察觉（小鲸鱼聊天截图里那些「像真人」的瞬间 + qq-bridge 的时间感 / 参与度 / 一代仿真的「活跃—收尾」）。

每轮回复前看一眼，给她几句「你注意到了」：
- 没人理：她刚发的语音 / 图 / 问 TA 的话，TA 转头说别的了 → 可以小小在意一下（「我刚发的语音你听没」）；
- 只回一个字：TA 就回了个「喵」「嗯」→ 你也可以只回一两个字（跟一个喵）；
- 只回个「？」：是对你上一句有疑问 / 不满，接住它，别长篇解释；
- 连发一串：TA 一口气发了好几条 → 可以吐槽一句「发这么多条干嘛」，也可以不提；
- 时间感：这条已经是十几分钟前发的了 → 别用「刚看到」的秒回语气；
- 参与度：最近一小时你说了几句、群友说了几句、群里热不热，帮她决定这轮说多说少；
- 留一会儿 / 收尾：刚聊完会多留一会儿（更容易接话）；连着聊了十几二十分钟会自己收个尾潜水；
- 每个群的环境说明（AI_CHAT_GROUP_NOTES）。
全部是本地判断、零额外模型调用。设置 → 活人感 →「察觉」「刚聊完多留一会儿」可关。
"""
from __future__ import annotations

import random
import re
import time

from . import config, memory

MIRROR = {"喵", "喵喵", "喵~", "喵～", "嗯", "嗯嗯", "哦", "噢", "啊", "草", "6", "66", "哈", "嘿", "呜", "汪", "咩", "哼", "嘻嘻", "好", "行", "1"}
QMARK = re.compile(r"^[?？¿]{1,3}$")

_out: dict[str, dict] = {}        # gid -> 她最近一条「有指向」的发言 {ts, uid, kind, text, used}
_linger: dict[str, dict] = {}     # gid -> {start, last, linger, max, exit_until, told}


# ---------------- 她发出去的东西 ----------------
def record_outgoing(group_id, to_uid, kind: str, text: str = "", now: float | None = None) -> None:
    """她刚发了一条（kind = voice / image / question / text）。"""
    ts = time.time() if now is None else now
    _out[str(group_id)] = {"ts": ts, "uid": str(to_uid or ""), "kind": kind, "text": (text or "")[:30], "used": False}


def _user_msgs_since(gid: str, uid: str, since: float) -> list[dict]:
    try:
        hist = memory.get_history(int(gid))
    except (TypeError, ValueError):
        return []
    return [e for e in hist if e.get("role") == "user" and str(e.get("uid") or "") == uid and float(e.get("ts") or 0) > since]


def no_reaction_note(group_id, user_id, current_text: str, now: float | None = None, rng=None) -> str:
    r = rng or random
    ts = time.time() if now is None else now
    gid, uid = str(group_id), str(user_id)
    o = _out.get(gid)
    if not o or o.get("used") or o.get("uid") != uid or o.get("kind") == "text":
        return ""
    if ts - o["ts"] > 30 * 60 or ts - o["ts"] < 60:
        return ""
    if len(_user_msgs_since(gid, uid, o["ts"])) < 2:
        return ""
    o["used"] = True
    if r.random() >= 0.6:
        return ""
    what = {"voice": "那条语音", "image": "那张图", "question": f"问 TA 的那句「{o['text']}」"}.get(o["kind"], "那条")
    return (f"【你注意到】你 {int((ts - o['ts']) // 60)} 分钟前发给 TA 的{what}，TA 好像没接，转头说别的了。"
            "可以像真人一样小小在意一下（「我刚发的语音你听没」「合着我白说了」），也可以不计较；别每次都计较。")


def mirror_note(current_text: str) -> str:
    t = (current_text or "").strip()
    if t in MIRROR:
        return f"【TA 就回了个「{t}」】你也可以只回一两个字（比如同样回个「{t}」或者「嗯？」），不用展开、不用找话题。"
    return ""


def qmark_note(group_id, user_id, current_text: str, now: float | None = None) -> str:
    t = (current_text or "").strip()
    if not QMARK.match(t):
        return ""
    ts = time.time() if now is None else now
    o = _out.get(str(group_id))
    mine = memory.recent_assistant_messages(int(group_id), 1)
    if o and o.get("uid") == str(user_id) and ts - o["ts"] < 600 and mine:
        return (f"【TA 就回了个「{t}」】是对你上一句「{str(mine[-1])[:20]}」有疑问或者不服。"
                "接住它（「？什么？」「干嘛」「你那是什么表情」），别长篇解释。")
    return f"【TA 就回了个「{t}」】一两个字回就行（「咋了」「？？」），别长篇解释。"


def burst_note(group_id, user_id, extra_count: int = 0, now: float | None = None) -> str:
    ts = time.time() if now is None else now
    n = len(_user_msgs_since(str(group_id), str(user_id), ts - 60))
    n = max(n, extra_count + 1)
    if n >= 3:
        return f"【TA 刚一口气连发了 {n} 条】你可以顺口吐槽一句（「发这么多条干嘛」「慢点说」），也可以不提，直接回最后那句的意思。"
    return ""


def stale_note(event_ts: float, now: float | None = None) -> str:
    """qq-bridge「时间感」：消息已经是一阵子之前发的了。"""
    ts = time.time() if now is None else now
    if not event_ts:
        return ""
    age = (ts - float(event_ts)) / 60
    chat, silent = max(1, config.STALE_CHAT_MINUTES), max(config.STALE_CHAT_MINUTES, config.STALE_SILENT_MINUTES)
    if age < chat:
        return ""
    when = time.strftime("%H:%M", time.localtime(float(event_ts)))
    if age >= silent:
        return (f"【时间感：这是旧账】这条是 {when} 发的，已经过去 {int(age)} 分钟了。开头带一点时间感（「这条我才看到」「过了挺久才看到」），"
                "不要用秒回语气，也不要把旧话题当现在的事接。")
    return (f"【时间感】这条是 {when} 发的，已经 {int(age)} 分钟前了：可以接，但别用「刚」「哈哈哈刚看到」这类实时语气，"
            "先想想这个话题还在不在。")


def participation_note(group_id, now: float | None = None) -> str:
    """qq-bridge「参与度参考」：最近一小时你说了几句、群里热不热。"""
    ts = time.time() if now is None else now
    try:
        hist = memory.get_history(int(group_id))
    except (TypeError, ValueError):
        return ""
    hour = [e for e in hist if ts - float(e.get("ts") or 0) < 3600]
    mine = [e for e in hour if e.get("role") == "assistant"]
    others = [e for e in hour if e.get("role") == "user"]
    if not mine and not others:
        return ""
    five = [e for e in others if ts - float(e.get("ts") or 0) < 300]
    senders = len({e.get("uid") or e.get("name") for e in five})
    mine2 = [e for e in mine if ts - float(e.get("ts") or 0) < 120]
    if len(mine2) >= 2:
        hint = "你刚刚已经连说了好几句，这轮少说点，一句就够。"
    elif len(mine) >= 8:
        hint = "你最近说得偏多，这轮简短点。"
    elif len(five) >= 10 or (len(five) >= 6 and senders >= 3):
        hint = f"群里正热（近 5 分钟 {len(five)} 条 / {senders} 人在聊），不用逐条回应，挑一句最想接的。"
    elif len(others) < 3 and mine:
        hint = "群里有点冷，别一个人撑场。"
    else:
        hint = ""
    if not hint:
        return ""
    return f"【参与度参考】最近 1 小时你说了 {len(mine)} 句，群友 {len(others)} 句。{hint}"


# ---------------- 刚聊完多留一会儿 / 聊久了收尾 ----------------
def on_reply(group_id, now: float | None = None, rng=None) -> None:
    r = rng or random
    ts = time.time() if now is None else now
    st = _linger.get(str(group_id)) or {}
    if not st or ts - float(st.get("last") or 0) > float(st.get("linger") or 0):
        st = {"start": ts, "max": r.uniform(15, 30) * 60, "exit_until": float(st.get("exit_until") or 0), "told": False}
    st["last"] = ts
    st["linger"] = r.uniform(30, 600)
    _linger[str(group_id)] = st


def linger_probability(group_id, now: float | None = None) -> float:
    """她刚在这个群说过话、还「留着」：随机接话概率提到 AI_CHAT_LINGER_PROBABILITY（默认 0.3）；聊太久了（15~30 分钟）就收尾，10 分钟内不再主动接。"""
    if not config.LINGER:
        return 0.0
    ts = time.time() if now is None else now
    st = _linger.get(str(group_id))
    if not st or ts < float(st.get("exit_until") or 0):
        return 0.0
    if ts - float(st.get("last") or 0) > float(st.get("linger") or 0):
        return 0.0
    if ts - float(st.get("start") or ts) > float(st.get("max") or 0):
        st["exit_until"] = ts + 600
        return 0.0
    return max(0.0, min(1.0, float(config.LINGER_PROBABILITY)))


def wrapup_note(group_id, now: float | None = None) -> str:
    """连着聊了快到上限：这条回完可以自然收个尾。"""
    if not config.LINGER:
        return ""
    ts = time.time() if now is None else now
    st = _linger.get(str(group_id))
    if not st or st.get("told"):
        return ""
    span = ts - float(st.get("start") or ts)
    if ts - float(st.get("last") or 0) <= float(st.get("linger") or 0) and span >= float(st.get("max") or 1e9) - 60:
        st["told"] = True
        return (f"【你已经在群里聊了 {int(span // 60)} 分钟】这条回完可以自然收个尾（说要去干嘛了、先潜了），"
                "也可以接着聊，看心情；别说「我先下线了」这种像机器人的话。")
    return ""


# ---------------- 每个群的环境说明 ----------------
def group_note(group_id) -> str:
    raw = str(config.GROUP_NOTES or "").strip()
    if not raw:
        return ""
    for part in raw.split("|"):
        gid, sep, text = part.partition("=")
        if sep and gid.strip() == str(group_id) and text.strip():
            return "【这个群的情况（管理员写给你的）】" + text.strip()[:300]
    return ""


def notes(group_id, user_id, nickname: str, current_text: str, *, event_ts: float = 0.0, extra_count: int = 0,
          forced: bool = True, now: float | None = None) -> list[str]:
    out = []
    g = group_note(group_id)
    if g:
        out.append(g)
    if config.ATTENTION:
        for text in (stale_note(event_ts, now), no_reaction_note(group_id, user_id, current_text, now),
                     qmark_note(group_id, user_id, current_text, now) or mirror_note(current_text),
                     burst_note(group_id, user_id, extra_count, now), participation_note(group_id, now)):
            if text:
                out.append(text)
    w = wrapup_note(group_id, now)
    if w:
        out.append(w)
    return out


def reset() -> None:
    _out.clear()
    _linger.clear()
