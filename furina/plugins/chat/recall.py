"""想起来：按「证据」挑相关的那几条记忆，而不是把最近几条一股脑塞进提示词。

算法移植自 dsh-fish-memory（鱼的记忆，Yuuyuko-uu，MIT），按 QQ 群的情况改了一点点：

- 切词：通用中文词表（jieba dict.txt.small）最长匹配 + 单字兜底，一个字都不丢；
  单字虚词（的/了/我/嗯…）不算词。
- 线索：通用中文里最常用的那批词（词频前约 4000 名，如「表示」「今天」）不当线索；
  在这个人的记忆里太常见的词（出现在 >1% 且 >3 条里）也不当线索。
  —— 全被筛光、或者剩不到 2 个能用的，就原样退回（保底，宁可宽一点也别聋）。
- 证据 C = s / (s + K)，s = Σ 命中词的库内稀有度（0~1，夹到 1）× 说话稀有度 × 价
  （命中「扩词/标签」全价，只命中正文打 6 折）。**门槛只卡 C**；
  鲜活度 / 重要度 / 情绪只管排序：P = C × (0.55 + 0.25A + 0.15I + 0.05E)。
  （卡 P 会让鲜活度一变整个库的分数一起移，把该想起的也挡掉 —— 原作者踩过的坑）
- 鲜活度 A = A0 × 2^(-天数 / (半衰期 × (1 + 2 × 重要度)))：越重要淡得越慢；
  长期的（性格、喜好、约定 = stable）不淡；被再次提起就弹回来：A' = A + 0.2(1 - A)，从那天重新算。
  什么都不会被自动删掉，淡掉的只是排得靠后。
- 条数跟着最高证据走（强 → 给满，弱 → 只给 1 条），先去重（像度 ≥0.8），长期事实最多占 2 条，总字数有上限。
- 问句信息量门：「在吗」「哈哈」「嗯嗯」这种话本身没内容，就不翻记忆（按这个人自己平时怎么说话来量，
  攒够 20 句之前门不开）。
"""
from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
from collections import Counter, OrderedDict
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).with_name("lexicon")

# 单字虚词：不当词（原版那份 + 群聊里常见的语气字）
_STOP_SINGLE = set("叫我你他她它的了着过是有在把被和与及或这那哪吗呢吧啊呀得地个只件请也都就很再还要嗯哦哈啦嘛呐噢喔诶欸")
_HAN_STOPS = re.compile(
    r"(?:怎么样|叫什么|名字|名叫|叫|用户|什么|怎么|哪里|哪儿|现在|目前|当前|记得|记住|告诉|可以|请问|一下|来着|我们|你们|他们|她们"
    r"|这个|那个|哪些|是否|有没有|我|你|他|她|它|的|了|着|过|是|有|在|把|被|和|与|及|或|这|那|哪|吗|呢|吧|啊|呀|得|地|个|只|件|请|也|都|就|很|再|还|要)")
_EN_STOPS = {"the", "a", "an", "is", "are", "was", "were", "i", "you", "my", "your", "it", "and", "or", "to", "of",
             "what", "where", "do", "did", "does"}
_HAN_RUN = re.compile(r"[\u4e00-\u9fa5]+")
_EN_WORD = re.compile(r"[a-z0-9]{2,}")

CLUE_GATE = 0.4      # 通用稀有度低于它 = 通用中文里太常用，不当线索
BODY_PRICE = 0.6     # 只撞上正文（没撞上扩词/标签）打几折
FRAG_PRICE = 0.8     # 撞上扩词被切开后的干净碎片
DUP_RECALL = 0.8     # 召回去重：像度 ≥ 它算同一件事
DUP_TRUE = 0.85      # 真重复（可以自动合并 / 自动标作废），还要长度比 ≥0.8
TIME_BOOST = 1.3     # 时间词只提排序，不提相关度
TIME_CUES = (
    (re.compile(r"今天|今日|今儿"), 0, 0),
    (re.compile(r"昨天|昨日|昨晚"), 1, 1),
    (re.compile(r"前天"), 2, 2),
    (re.compile(r"这几天|最近|前几天|这一阵|前阵子"), 0, 7),
    (re.compile(r"上周|上星期"), 7, 14),
)

_lex_lock = threading.Lock()
_WORDS: set | None = None
_MAXLEN = 2
_RANK: dict | None = None


def normalize(s) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).lower()


def _load_words() -> set:
    global _WORDS, _MAXLEN
    if _WORDS is not None:
        return _WORDS
    with _lex_lock:
        if _WORDS is None:
            words: set = set()
            try:
                with open(_DIR / "dict_small.txt", encoding="utf-8") as f:
                    for line in f:
                        w = line.strip()
                        if len(w) >= 2:
                            words.add(w)
            except OSError:
                pass  # 读不到词表：退回老切法，不崩
            _MAXLEN = min(12, max((len(w) for w in words), default=2))
            _WORDS = words
    return _WORDS


def _load_rank() -> dict:
    global _RANK
    if _RANK is not None:
        return _RANK
    with _lex_lock:
        if _RANK is None:
            rank: dict = {}
            try:
                with open(_DIR / "word_rank.txt", encoding="utf-8") as f:
                    for line in f:
                        p = line.split()
                        if len(p) >= 2:
                            try:
                                rank[p[0]] = int(p[1])
                            except ValueError:
                                pass
            except OSError:
                pass  # 读不到：这道门自动失效（全当很具体）
            _RANK = rank
    return _RANK


def _old_split(run: str, out: set) -> None:
    """没有词表时的退路：按停用词切开，≤2 字整段收，更长的抽相邻 2 字。"""
    for part in _HAN_STOPS.split(run):
        if not part:
            continue
        if len(part) <= 2:
            out.add(part)
        else:
            for i in range(len(part) - 1):
                out.add(part[i:i + 2])


@lru_cache(maxsize=8192)
def tokenize(s: str) -> frozenset:
    """按词表切：每个位置取最长的、词表里有的词；一个都没有就单字兜底（单字虚词扔掉）。"""
    words = _load_words()
    clean = normalize(s)
    out: set = set()
    for m in _EN_WORD.findall(clean):
        if m not in _EN_STOPS:
            out.add(m)
    for run in _HAN_RUN.findall(clean):
        if not words:
            _old_split(run, out)
            continue
        i, n = 0, len(run)
        while i < n:
            size = 0
            for k in range(min(_MAXLEN, n - i), 1, -1):
                if run[i:i + k] in words:
                    size = k
                    break
            w = run[i:i + size] if size else run[i]
            if not (len(w) == 1 and w in _STOP_SINGLE):
                out.add(w)
            i += size or 1
    return frozenset(out)


def general_rarity(word: str) -> float:
    """0~1：这个词在通用中文里有多具体。不在常用表里 = 很具体 = 1。"""
    rank = _load_rank()
    if not rank:
        return 1.0
    v = rank.get(word)
    return 1.0 if v is None else v / 99.0


def is_clue(word: str) -> bool:
    return general_rarity(word) >= CLUE_GATE


def similarity(a, b) -> float:
    """像度 0~1：短的那条的词有多少在长的那条里。太短（<3 个词）不比，免得单词撞上就算 1。"""
    a = a if isinstance(a, (set, frozenset)) else tokenize(str(a or ""))
    b = b if isinstance(b, (set, frozenset)) else tokenize(str(b or ""))
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if len(small) < 3:
        return 0.0
    return sum(1 for x in small if x in big) / len(small)


def _squash(s: str) -> str:
    return re.sub(r"[\s，。、！？,.!?~～…]+", "", normalize(s))


def same_thing(a: str, b: str) -> bool:
    """「又说了一遍同一件事」：一字不差，或者一条包含另一条（都 >8 字），或者词像度 ≥0.85 且长短差不多。"""
    x, y = _squash(a), _squash(b)
    if not x or not y:
        return False
    if x == y or (len(x) > 8 and len(y) > 8 and (x in y or y in x)):
        return True
    ratio = min(len(x), len(y)) / max(len(x), len(y))
    return ratio >= 0.8 and similarity(a, b) >= DUP_TRUE


# ---------------- 说话尺：这个人平时怎么说话 ----------------
class _Ruler:
    __slots__ = ("n", "df")

    def __init__(self):
        self.n = 0
        self.df: Counter = Counter()


_rulers: "OrderedDict[str, _Ruler]" = OrderedDict()
_ruler_lock = threading.Lock()
_RULER_CAP = 800


def observe_speech(uid, text: str) -> None:
    """记一句 TA 说的话（只在内存里攒，重启重攒）：TA 天天挂在嘴边的词不算证据。"""
    uid = str(uid or "")
    body = (text or "").strip()
    if not uid or not body:
        return
    words = tokenize(body[:200])
    with _ruler_lock:
        r = _rulers.pop(uid, None) or _Ruler()
        _rulers[uid] = r
        r.n += 1
        r.df.update(words)
        if len(r.df) > 20000:
            for k in [k for k, v in r.df.items() if v <= 1]:
                del r.df[k]
        while len(_rulers) > _RULER_CAP:
            _rulers.popitem(last=False)


def _ruler(uid) -> _Ruler | None:
    return _rulers.get(str(uid or "")) if uid else None


def _speaker_rarity(r: _Ruler | None, word: str) -> float:
    if r is None or r.n < 20:
        return 1.0
    top = math.log(r.n + 1)
    if top <= 0:
        return 1.0
    v = math.log((r.n + 1) / (r.df.get(word, 0) + 1)) / top
    return 1.0 if v > 1 else (v if v > 0 else 0.0)


# ---------------- 记忆库 ----------------
def item_text(item) -> str:
    if isinstance(item, str):
        return item
    return str(item.get("text") or "")


def _item_surface(item) -> str:
    if isinstance(item, str):
        return item
    extra = list(item.get("tags") or []) + list(item.get("related") or [])
    return item_text(item) + (" " + " ".join(str(x) for x in extra) if extra else "")


def _related_sets(item):
    if isinstance(item, str):
        return None
    rel = [str(x).strip() for x in list(item.get("tags") or []) + list(item.get("related") or []) if str(x).strip()]
    if not rel:
        return None
    whole, frag = set(), set()
    for w in rel:
        whole.add(normalize(w))
        parts = tokenize(w)
        if len(parts) > 1 and not any(len(p) == 1 for p in parts):
            frag.update(parts)
    return whole, frag


class _Library:
    def __init__(self, items: list):
        self.words = [tokenize(_item_surface(it)) for it in items]
        self.df: Counter = Counter()
        for ws in self.words:
            self.df.update(ws)
        self.n = len(items)
        self.top = math.log((self.n + 1) / 2) if self.n > 1 else 0.0

    def rarity(self, word: str) -> float:
        if self.n <= 1:
            return 1.0 if self.df.get(word) else 0.0
        if self.top <= 0:
            return 0.0
        v = math.log((self.n + 1) / (self.df.get(word, 0) + 1)) / self.top
        return 1.0 if v > 1 else (v if v > 0 else 0.0)


def info_amount(uid, query: str, lib: _Library) -> float:
    """这句话本身有多少值得翻记忆的东西（按 TA 自己平时怎么说话量）。样本不够 → 无穷大（门不关）。"""
    r = _ruler(uid)
    if r is None or r.n < 20:
        return math.inf
    top = math.log(r.n + 1)
    if top <= 0:
        return math.inf
    v = 0.0
    for k in tokenize(query):
        if lib.df.get(k):
            v += math.log((r.n + 1) / (r.df.get(k, 0) + 1))
    return v / top


def activation(item, now: float, half_life_days: float = 30.0, unknown: float = 0.5) -> float:
    """鲜活度：长期的不淡（给常数）；其余从「最后一次被提起」（没有就从记下来那天）开始按半衰期淡。"""
    if isinstance(item, str):
        return unknown
    if item.get("stable"):
        return unknown
    anchor = item.get("anchor") or item.get("at") or item.get("created")
    try:
        anchor = float(anchor or 0)
    except (TypeError, ValueError):
        anchor = 0.0
    if not anchor:
        return unknown
    imp = _imp(item)
    life = max(1.0, float(half_life_days or 30)) * (1 + 2 * imp)
    days = max(0.0, (now - anchor) / 86400)
    try:
        a0 = min(1.0, max(0.0, float(item.get("a0", 1.0))))
    except (TypeError, ValueError):
        a0 = 1.0
    return a0 * math.pow(2, -days / life)


def reinforce(item: dict, now: float | None = None, half_life_days: float = 30.0, unknown: float = 0.5) -> float:
    """又被提起一次：记得更牢一点（A' = A + 0.2(1-A)），鲜活度从现在重新算。返回新的 A。"""
    ts = time.time() if now is None else now
    old = 1.0 if item.get("stable") else activation(item, ts, half_life_days, unknown)
    new = min(1.0, max(0.0, old + 0.2 * (1 - old)))
    item["a0"] = round(new, 4)
    item["anchor"] = ts
    item["rc"] = int(item.get("rc") or 0) + 1
    return new


def _imp(item) -> float:
    if isinstance(item, str):
        return 0.0
    try:
        return min(1.0, max(0.0, float(item.get("importance") or 0)))
    except (TypeError, ValueError):
        return 0.0


def _time_cue(query: str):
    for rx, lo, hi in TIME_CUES:
        if rx.search(query):
            return lo, hi
    return None


def recall(items: list, query: str, now: float | None = None, *, speaker=None, max_items: int = 6,
           max_chars: int = 800, threshold: float = 0.35, rarity_k: float = 1.0, query_gate: float = 0.8,
           facts_max: int = 2, half_life_days: float = 30.0, unknown: float = 0.5) -> list:
    """从 items 里挑出跟 query 相关的几条，按分数排好。items 可以是字符串或带 text 的 dict。

    返回 [{"item", "C", "P", "A"}]，没有相关的就返回 []（不硬凑）。"""
    ts = time.time() if now is None else now
    pool = [it for it in items or [] if item_text(it).strip() and not (isinstance(it, dict) and it.get("superseded"))]
    query = str(query or "").strip()
    if not pool or not query:
        return []
    lib = _Library(pool)
    if query_gate > 0 and speaker is not None and info_amount(speaker, query, lib) < query_gate:
        return []
    rare_cap = max(3.0, lib.n * 0.01)
    q_all = list(tokenize(query))
    q_rare = [k for k in q_all if 0 < lib.df.get(k, 0) <= rare_cap]
    q_clue = [k for k in q_rare if is_clue(k)]
    usable = len(q_clue) >= 2 and any(lib.rarity(k) > 0 for k in q_clue)
    q_keys = q_clue if usable else q_all
    if not q_keys:
        return []
    ruler = _ruler(speaker)
    k_sat = rarity_k if rarity_k and rarity_k > 0 else 1.0
    q_norm = normalize(query)
    cue = _time_cue(query)
    rows = []
    for it, ws in zip(pool, lib.words):
        rel = _related_sets(it)
        s = 0.0
        for k in q_keys:
            if k not in ws:
                continue
            price = 1.0 if rel is None else (1.0 if k in rel[0] else FRAG_PRICE if k in rel[1] else BODY_PRICE)
            s += lib.rarity(k) * _speaker_rarity(ruler, k) * price
        c = s / (s + k_sat)
        if len(query) >= 6 and q_norm in normalize(_item_surface(it)):
            c = max(c, 0.85)  # 整句原话出现在记忆里 = 强证据
        if c <= 0 or c < threshold:
            continue
        a = activation(it, ts, half_life_days, unknown)
        imp = _imp(it)
        p = c * (0.55 + 0.25 * a + 0.15 * imp)
        if cue and isinstance(it, dict) and it.get("at"):
            try:
                days = int((ts - float(it["at"])) // 86400)
            except (TypeError, ValueError):
                days = -1
            if cue[0] <= days <= cue[1]:
                p *= TIME_BOOST
        rows.append({"item": it, "C": c, "P": p, "A": a})
    if not rows:
        return []
    rows.sort(key=lambda r: (-r["P"], -r["C"]))
    top_c = max(r["C"] for r in rows[:1])
    cap = max_items if top_c >= 0.6 else 4 if top_c >= 0.5 else 3 if top_c >= 0.4 else 1
    cap = max(1, min(cap, max_items))
    kept, kept_words = [], []
    for r in rows:
        ws = tokenize(item_text(r["item"]))
        if any(similarity(ws, w) >= DUP_RECALL for w in kept_words):
            continue
        kept.append(r)
        kept_words.append(ws)
    out, facts, used = [], 0, 0
    for r in kept[:cap]:
        if len(out) >= max_items:
            break
        it = r["item"]
        if isinstance(it, dict) and it.get("stable"):
            if facts >= max(0, facts_max):
                continue
            facts += 1
        n = len(item_text(it))
        if out and used + n > max_chars:
            continue
        out.append(r)
        used += n
    return out


# ---------------- 渲染 ----------------
def _day_index(t: float) -> int:
    lt = time.localtime(t)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) // 86400)


def rel_time(at, now: float | None = None) -> str:
    """「今天 14:20」「3 天前（10-20）」「去年 09-17」—— 跨年一定带年份。"""
    ts = time.time() if now is None else now
    try:
        at = float(at or 0)
    except (TypeError, ValueError):
        at = 0.0
    if not at:
        return ""
    days = _day_index(ts) - _day_index(at)
    hm = time.strftime("%H:%M", time.localtime(at))
    md = time.strftime("%m-%d", time.localtime(at))
    if days <= 0:
        return "今天 " + hm
    if days == 1:
        return "昨天 " + hm
    if days == 2:
        return "前天 " + hm
    if days <= 14:
        return f"{days} 天前（{md}）"
    if days <= 60:
        return f"{md}（{days} 天前）"
    y_now, y_at = time.localtime(ts).tm_year, time.localtime(at).tm_year
    if y_at != y_now:
        return f"去年 {md}" if y_now - y_at == 1 else f"{y_at}-{md}（{y_now - y_at} 年前）"
    return f"{md}（{round(days / 30)} 个月前）"


def render_lines(rows: list, now: float | None = None) -> list:
    """每条一行：有时间的写「什么时候记下的」，长期的不写时间（别编）。"""
    out = []
    for r in rows:
        it = r["item"] if isinstance(r, dict) and "item" in r else r
        text = item_text(it).strip()
        if not text:
            continue
        when = "" if isinstance(it, str) or it.get("stable") else rel_time(it.get("at") or it.get("created"), now)
        out.append(f"- {when}：{text}" if when else f"- {text}")
    return out


# ---------------- 事实键：「对象.方面」，同一个键只留最新那条 ----------------
_CATCHALL = ("内容", "相关", "其他", "其它", "的事", "记录", "情况", "事项", "东西", "事情", "杂项", "各种", "综合")


def norm_key(k) -> str:
    """把模型写的事实键收成统一样子：「TA：身高」「TA|身高」「TA/身高」→「ta.身高」；不合格的给空。"""
    s = normalize(k).strip()
    if not s:
        return ""
    s = re.sub(r"[．。｡：:｜|]", ".", s)
    s = re.sub(r"\s+", "", s)
    s = s.strip("`'\"「」『』[]()（）")
    s = re.sub(r"[/／>＞-](?=[^.]*$)", ".", s, count=1)
    s = s[:24]
    parts = [x for x in s.split(".") if x]
    if len(parts) < 2:
        return ""
    if parts[-1] in _CATCHALL or any(parts[-1].endswith(c) for c in _CATCHALL):
        return ""  # 万能键：什么都能装，会把不相干的话归成一件
    if re.search(r"\d", s):
        return ""  # 键里写了具体的值（身高170），值一变键就对不上
    return ".".join(parts)
