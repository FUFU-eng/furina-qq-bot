"""说话方式的「活人感」：每轮随机定一个说话方式、点名最近的口头禅、压住自称刷屏。

只做本地字符串处理，不调用模型、不占额外 token（每次只多几十个字的提示）。

- turn_note()：这一轮回多长、要不要玩梗/傲娇（真人不会每句都抖机灵、每句都二三十个字）；
- habit_note()：从最近几条自己的回复里找出反复出现的开头、句尾语气词、自称，点名让这次避开；
- guard_self_titles()：最近已经频繁用「本XX」「XX大人」这类自称时，把这次回复里的换成「我」。
"""
from __future__ import annotations

import random
import re
from collections import Counter

from ...core import roles

# 很短、没什么信息量的话：回一个反应就够了
_LOW_CONTENT_RE = re.compile(
    r"^(?:[哈嘿呵嘻hHaA]{2,}|[草艹]+|笑死|绷不住了?|啊这|确实|真的|好家伙|离谱|6+|牛+|绝了|好耶|呜+|"
    r"[?？!！.。…~～]+|嗯+|哦+|噢+|啊+|好+|行|对|是|ok|OK|好的|好吧|晚安|早安?|午安|在吗?)[\s?？!！.。~～]*$"
)
_QUESTION_RE = re.compile(r"[?？]|吗|么|什么|怎么|为什么|为啥|咋|谁|哪|几|多少|是不是|能不能|会不会|有没有")

_SHORT = (
    "这次看你心情：想酷酷地甩几个字（「笑死」「？」「啧」「就这」）也行，想顺手多补两句吐槽也行，条数随你。",
    "这次随手接话就行，每条短一点，发一条还是连发几条看你兴致。",
)
_NORMAL = (
    "这次按你心情回：一条一个意思、每条十几二十个字，想发一条还是换行连发好几条完全随你。",
    "这次直接说重点，想吐槽或想逗人可以多拆几条连发，不限条数。",
    "这次像平时泡群一样自然接招，发几条看你自己心情。",
)
_LONG = (
    "对方这句很有聊头：尽管放开说，换行拆成多条连发（三五条、七八条都行，每条别超过二三十字），怎么痛快怎么来。",
)
_PLAIN = (
    "语气平常一点：这次别玩梗、别怼人、别傲娇，就是普通朋友聊天。",
    "这次不用表现自己，认真接住对方的话就行。",
)
_SLANG = "可以自然带一点网络口语（比如 笑死、绷不住、啊这、确实、离谱、好家伙），顺口就用，别硬塞。"


def turn_note(text: str = "", forced: bool = True, rng: random.Random | None = None) -> str:
    """本轮说话方式提示：长短 + 偶尔「平常一点」/「带点网络口语」。"""
    r = rng or random
    t = (text or "").strip()
    low = bool(_LOW_CONTENT_RE.match(t)) or (0 < len(t) <= 3 and not _QUESTION_RE.search(t))
    meaty = len(t) >= 12 or bool(_QUESTION_RE.search(t))
    x = r.random()
    if low:
        length = r.choice(_SHORT) if x < 0.6 else r.choice(_NORMAL)
    elif meaty:
        length = r.choice(_SHORT) if x < 0.15 else (r.choice(_LONG) if x > 0.75 else r.choice(_NORMAL))
    else:
        length = r.choice(_SHORT) if x < 0.3 else (r.choice(_LONG) if x > 0.85 else r.choice(_NORMAL))
    parts = [length]
    if r.random() < 0.35:
        parts.append(r.choice(_PLAIN))
    elif r.random() < 0.3:
        parts.append(_SLANG)
    return "【这次的说话方式】" + "".join(parts)


# ---------------- 最近的口头禅 ----------------
_PARTICLES = ("呀", "嘛", "啦", "哦", "呢", "哼", "诶", "喔", "咯", "噢")
_CLAUSE_END_RE = re.compile(r"([\u4e00-\u9fa5])(?=[，,。！!？?～~…\s]|$)")
_OPENER_STRIP_RE = re.compile(r"^[\s，,。！!？?～~…、\"“”'‘’]+")


def _clean(reply: str) -> str:
    return re.sub(r"<<?STICKER:\d+>>?|\[[A-Z_]+[^\]]*\]", "", reply or "").strip()


def _is_memo(reply: str) -> bool:
    """记忆里「（发了张表情包）」「（艾特了谁）」这种动作备注不算说话。"""
    return (reply or "").strip().startswith(("（", "("))


def title_words() -> list[str]:
    """当前角色「本XX」「XX大人」式的自称（像「芙芙」这种昵称不算，别人也会这么叫她）。"""
    try:
        words = roles.self_words()
    except Exception:
        words = []
    return sorted({w for w in words if len(w) >= 2 and (w.startswith("本") or w.endswith("大人"))},
                  key=len, reverse=True)


def _openers(replies: list[str]) -> list[str]:
    out = []
    for r in replies:
        t = _OPENER_STRIP_RE.sub("", _clean(r))
        if len(t) < 4:
            continue
        m = re.match(r"([\u4e00-\u9fa5]{1,3}?)(?=[，,。！!？?～~…\s])", t)
        out.append(m.group(1) if m else t[:2])
    return out


def habit_note(recent_replies: list[str]) -> str:
    """最近 6 条自己的回复里反复出现的开头 / 句尾语气词 / 自称，点名让这次避开。"""
    replies = [r for r in (recent_replies or [])[-6:] if r and not _is_memo(r)]
    if len(replies) < 2:
        return ""
    bits = []
    openers = [w for w, c in Counter(_openers(replies)).items() if c >= 2]
    if openers:
        bits.append("开头老是「" + "」「".join(openers[:3]) + "」")
    pc = Counter()
    for r in replies:
        ends = {m.group(1) for m in _CLAUSE_END_RE.finditer(_clean(r))}
        for p in _PARTICLES:
            if p in ends:
                pc[p] += 1
    parts = [p for p, c in pc.most_common() if c >= max(2, len(replies) // 2)]
    if parts:
        bits.append("句尾老带「" + "」「".join(parts[:3]) + "」")
    titles = title_words()
    used = [w for w in titles if sum(1 for r in replies if w in r) >= 1]
    if titles and sum(1 for r in replies if any(w in r for w in titles)) >= 2:
        bits.append("老用「" + "」「".join(used[:3]) + "」自称")
    tilde = sum(1 for r in replies if re.search(r"[~～]\s*$", _clean(r)))
    if tilde >= 2:
        bits.append("老拿「～」结尾")
    if not bits:
        return ""
    return ("【最近的口头习惯，这次避开】你最近" + "，".join(bits)
            + "。这次换个开头，句尾不加语气词也行，自称就用「我」。")


# ---------------- 自称刷屏兜底 ----------------
def guard_self_titles(reply: str, recent_replies: list[str], window: int = 5, limit: int = 2) -> str:
    """最近 window 条里已有 limit 条用过「本XX/XX大人」自称：这次全部换成「我」；
    没超标时一条回复里也最多留一个。"""
    if not reply:
        return reply
    titles = title_words()
    if not titles:
        return reply
    pat = re.compile("|".join(re.escape(w) for w in titles))
    if not pat.search(reply):
        return reply
    recent = [r for r in (recent_replies or [])[-window:] if r and not _is_memo(r)]
    overused = sum(1 for r in recent if pat.search(r)) >= limit
    seen = {"n": 0}

    def _sub(m):
        seen["n"] += 1
        return "我" if overused or seen["n"] > 1 else m.group(0)

    out = pat.sub(_sub, reply)
    out = re.sub(r"我{2,}", "我", out)
    return out


def sticker_only_candidate(text: str, has_image: bool = False) -> bool:
    """这句话适不适合只回一张表情包：很短、没信息量、不是在问问题（纯图片也算）。"""
    t = (text or "").strip()
    if not t:
        return bool(has_image)
    if _QUESTION_RE.search(t) and not re.fullmatch(r"[?？!！]+", t):
        return False
    return bool(_LOW_CONTENT_RE.match(t)) or len(t) <= 4
