"""口癖 / 句式疲劳：她最近反复用的句式和口头禅，点名让这轮别再用。

用户反馈「每次夸人的句式、说话的句式都很机械，AI 味很重」。翻她的回复记录，毛病不在单个词，而在固定句架子：
- 「这图挺好看的，就是有点晃眼」「眼睛画得是挺绝的，就是你这连发的架势有点吓人」——先夸再「就是……有点……」挑个刺，
  每句都要凑一个对仗的包袱，真人夸东西就一个反应（「好看」「眼睛好好看」「我存了」）；
- 「光影和花衬得人特别温柔」——评论家腔，群里没人这么夸图；
- 「行行行」「好好好」「才、才」「你这……得我有点……」「你这是在……」「……，别乱说」「啊？」开头……
  单看都正常，一天说十几次就成了机器人的口癖；「别戳啦，痒」原样说了五次。

做法（本地计算，不额外调模型）：
1. hard_template()：「夸一句 + 就是……有点……」和评论家腔，出现就让模型重说一次；
2. overused()：跨群取她最近 40 条回复，固定句式 ≥3 次、或者同一个 3~6 字短语在 ≥3 条不同回复里出现（且不是同一段
   聊天里的话题词）就算口癖；
3. note()：把口癖写进本轮提示「最近你老说……这轮别用」；
4. reply_hits()：发出前这句又用了口癖 → 重说一次（和其他重试共用「最多一次」）。
开关：AI_CHAT_DEAI（和去 AI 味一起）。
"""
from __future__ import annotations

import re

from . import memory

_POSITIVE = r"(?:挺|确实|真|蛮|好看|漂亮|绝|不错|爽|可爱|好听|帅|喜欢|厉害|值得)"
HARD = [
    ("先夸一句再「就是……有点……」挑个刺的对仗句",
     re.compile(_POSITIVE + r"[^。！？!?\n]{0,22}[，,]\s*(?:就是|只是|不过|但是?)[^，,。！？!?\n]{0,26}(?:有点|有些|有一点|太|怪|略)")),
    ("评论家腔（光影、构图、氛围、衬得……）",
     re.compile(r"光影|构图|配色|笔触|线条感|氛围感|质感|层次感|张力|衬得|画工|神韵|细节拉满|很有感觉|拍得真(?:漂亮|好看)")),
]

# (说给她听的名字, 正则, 最近 40 条里出现几次算口癖)
HABITS = [
    ("「行行行」「好好好」「行吧行吧」", re.compile(r"行行行|好好好|行吧行吧|好嘛好嘛|催催催"), 3),
    ("「才、才」这种结巴", re.compile(r"([^\W\d_])、\1"), 2),
    ("拿「你这……」点评对方（你这是在……/你这盯得我……）", re.compile(r"你这(?:是|就|人|要求|话|算|[^，,。！？!?\n]{0,8}(?:得|让|把)我)"), 3),
    ("句尾叮嘱「……，别乱说」「别笑我」「别得寸进尺」", re.compile(r"[，,]\s*(?:别|不许)[^，,。！？!?\n]{1,9}[啊呀嘛了]?$", re.M), 3),
    ("开头先来个「啊？」「哈？」", re.compile(r"^(?:啊|哈)[？?]", re.M), 4),
    ("「确实」", re.compile(r"确实"), 3),
    ("「反正」", re.compile(r"反正"), 3),
    ("「就这一句/就这一次」", re.compile(r"就这一(?:句|次)"), 2),
    ("「有点……」", re.compile(r"有点"), 4),
]

_NGRAM_STOP = {"什么", "怎么", "这么", "那么", "我们", "你们", "他们", "自己", "一个", "没有", "不是", "就是", "还是", "可以"}
_WINDOW = 40
_TOPIC_SPAN = 30 * 60  # 同一个短语都挤在半小时内 → 多半是那段聊天的话题词（「凯撒」「系鞋带」），不算口癖


def _clean(text: str) -> str:
    t = re.sub(r"<<STICKER:\d+>>|\[VOICE(?::[^\]]*)?\]|\[[A-Z_]+:[^\]]*\]", "", text or "")
    return t.strip()


def recent_replies(n: int = _WINDOW) -> list[tuple[float, int, str]]:
    """跨群她最近 n 条回复 (ts, 群, 文本)，从旧到新。"""
    out = []
    for gid, dq in list(memory._histories.items()):
        for e in list(dq):
            if e.get("role") == "assistant":
                txt = _clean(str(e.get("content") or ""))
                if txt and not txt.startswith("（"):
                    out.append((float(e.get("ts") or 0), gid, txt))
    out.sort(key=lambda x: x[0])
    return out[-n:]


def hard_template(text: str) -> str:
    t = _clean(text)
    for name, rx in HARD:
        if rx.search(t):
            return name
    return ""


def _grams(text: str) -> set[str]:
    s = set()
    for seg in re.split(r"[^\w]+", text):
        for n in (3, 4, 5, 6):
            for i in range(len(seg) - n + 1):
                g = seg[i:i + n]
                if not g.isascii() and not any(w == g for w in _NGRAM_STOP):
                    s.add(g)
    return s


def overused(recent: list[tuple[float, int, str]] | None = None, topic: str = "") -> dict:
    """{"habits": [名字...], "phrases": [短语...]}；topic 是对方这轮说的话，里面出现的词不算口癖（是在聊这个）。"""
    recent = recent_replies() if recent is None else recent
    texts = [t for _, _, t in recent]
    habits = [name for name, rx, k in HABITS if sum(1 for t in texts if rx.search(t)) >= k]
    seen: dict[str, list[float]] = {}
    for ts, _, t in recent:
        for g in _grams(t):
            seen.setdefault(g, []).append(ts)
    cands = [g for g, tss in seen.items()
             if len(tss) >= 3 and (max(tss) - min(tss)) > _TOPIC_SPAN and g not in (topic or "")]
    # 只留最长的：「戳我干嘛」在就不要「戳我干」「我干嘛」
    cands.sort(key=lambda g: (-len(g), -len(seen[g])))
    phrases: list[str] = []
    for g in cands:
        if not any(g in p for p in phrases):
            phrases.append(g)
    phrases.sort(key=lambda g: -len(seen[g]))
    return {"habits": habits, "phrases": phrases[:6]}


def note(topic: str = "") -> str:
    o = overused(topic=topic)
    bits = []
    if o["phrases"]:
        bits.append("「" + "」「".join(o["phrases"]) + "」")
    bits.extend(o["habits"][:5])
    if not bits:
        return ""
    return ("【最近的口癖，这轮别用】你这两天嘴边老挂着：" + "、".join(bits) +
            "。真人不会一天到晚一个句式：换个说法，或者干脆只回一个反应。")


def reply_hits(reply: str, topic: str = "") -> list[str]:
    """这句回复用到了哪些已经说烂的口癖（给重试提示用）。"""
    t = _clean(reply)
    if not t:
        return []
    o = overused(topic=topic)
    hits = [p for p in o["phrases"] if p in t]
    for name, rx, _ in HABITS:
        if name in o["habits"] and rx.search(t):
            hits.append(name)
    return hits


RETRY_HINT = ("你刚才这句又是老一套（{why}），像机器人在套模板。用真人随手打字的样子重说同一个意思："
              "一条只说一个意思，夸就直接夸（「好看」「眼睛好好看」「这个我存了」），想吐槽就单独一句短的，"
              "别先夸后转折、别拿「你这……」点评对方、别在句尾叮嘱人，也别用刚才那几个口头禅。")


def trim_template(reply: str) -> str:
    """重说后还是「夸，就是……」：只留前半句（一个反应就够了）。"""
    t = reply or ""
    m = re.search(r"[，,]\s*(?:就是|只是|不过|但是?)[^，,。！？!?\n]{0,26}(?:有点|有些|有一点|太|怪|略)[^\n]*", t)
    if m and m.start() >= 2:
        return t[:m.start()].rstrip("，, ")
    return t

