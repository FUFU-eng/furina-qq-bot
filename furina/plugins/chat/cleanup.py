"""回复清洗与频控工具。"""

import difflib
import random
import re
import time

from . import config
from .state import _desc_stats, _sticker_stats

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"


def drop_periods(text: str) -> str:
    """聊天单句号：中间换逗号、末尾去掉；代码/网址/小数/文件名及无语省略号保留。"""
    if not text or not any(c in text for c in "。｡．."):
        return text
    spans = []
    token = re.compile(r"\ue000(\d+)\ue001")

    def hold(value: str, kind: str = "literal") -> str:
        spans.append((value, kind))
        return f"\ue000{len(spans) - 1}\ue001"

    def protect(match):
        return hold(match.group(0))

    # 先屏蔽机器内容，避免把网页、版本、代码中的点误当标点。
    masked = re.sub(r"```[\s\S]*?(?:```|$)", protect, text)
    masked = re.sub(r"`[^`\n]+`", protect, masked)
    masked = re.sub(r"\[CQ:[^\]\n]+\]|<<STICKER:\d+>>", lambda m: hold(m.group(0), "marker"), masked)

    def url(match):
        value, suffix = match.group(0), ""
        while value.endswith("."):
            value, suffix = value[:-1], "." + suffix
        # Markdown 外层右括号不属于网址；URL 自身成对的括号保留。
        while value.endswith(")") and value.count(")") > value.count("("):
            value, suffix = value[:-1], ")" + suffix
        return hold(value) + suffix

    masked = re.sub(r"(?:https?://|www\.)[^\s<>\"'。｡，！？；、]+", url, masked)
    masked = re.sub(r"[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,63}\b", protect, masked)
    masked = re.sub(r"(?:[A-Za-z]:[\\/])[^\s<>\"'。｡，！？；、]+", protect, masked)
    masked = re.sub(r"[\w-]+(?:\.[\w-]+)*\.(?:py|json|txt|md|log|jpg|jpeg|png|gif|webp|apk|zip|pdf|exe|bat|env|toml|ya?ml|html?|js|ts|css|mp3|wav|mp4|csv|ckpt|pth)\b", protect, masked, flags=re.I)
    masked = re.sub(r"(?:[A-Za-z0-9-]+\.)+(?:com|cn|net|org|io|dev|app|ai|cc|me|edu|gov|co|uk|de|jp)\b", protect, masked, flags=re.I)
    masked = re.sub(r"\d+(?:\.\d+)+", protect, masked)
    masked = re.sub(r"。{2,}|\.{2,}|．{2,}|｡{2,}", protect, masked)

    out = []
    for line in masked.split("\n"):
        def punctuation(match):
            tail = line[match.end():].lstrip(" \t\u200b\ufeff")
            if not tail or tail[0] in "\"'」』”’）)]】}>，,！？!?；;":
                return ""
            # 表情标记或emoji收尾，不要在表情前留下一个孤零零的逗号。
            logical = token.sub(lambda m: "" if spans[int(m.group(1))][1] == "marker" else "字", tail)
            return "，" if any(c.isalnum() for c in logical) else ""
        line = re.sub(r"(?:[。｡．]|(?<!\.)\.(?!\.))[ \t]*", punctuation, line)
        out.append(line.rstrip())
    cleaned = "\n".join(out)
    return token.sub(lambda m: spans[int(m.group(1))][0], cleaned).strip()


def _split_reply_parts(reply: str) -> list[str] | None:
    """按概率把一句回复拆成两句发，模拟真人分两条消息。

    - 太短（<16 字）不拆；句子越长拆分概率越高；
    - 在换行/句尾标点/逗号处断，拆出的两段都不少于 4 字；
    - 没有合适断点就不拆，保持一条。
    """
    if not reply:
        return None
    n = len(reply)
    if n < 16:
        return None
    if n <= 40:
        prob = config.REPLY_SPLIT_PROBABILITY * 0.6
    elif n <= 60:
        prob = config.REPLY_SPLIT_PROBABILITY
    else:
        prob = min(0.9, config.REPLY_SPLIT_PROBABILITY * 1.4)
    if random.random() >= prob:
        return None

    mid = n // 2
    lo, hi = max(1, int(n * 0.25)), min(n - 1, int(n * 0.75))
    # 断点选换行、句尾标点或逗号（回退到原来的断句规则），两段仍不能太短
    best, best_dist = -1, n
    for i in range(lo, hi):
        if reply[i] == "\n":
            d = abs(i - mid)
            if d < best_dist:
                best, best_dist = i, d
    if best < 0:
        for i in range(lo, hi):
            if reply[i] in "，,。！？!?；;、…":
                d = abs(i - mid)
                if d < best_dist:
                    best, best_dist = i, d
    if best < 0:  # 模型自己用空格隔开两句时：在两个汉字之间的空格处断
        for i in range(lo, hi):
            if reply[i] == " " and re.match(rf"[{_CJK}]", reply[i - 1]) and re.match(rf"[{_CJK}]", reply[i + 1:i + 2] or "x"):
                d = abs(i - mid)
                if d < best_dist:
                    best, best_dist = i, d
    if best < 0:
        return None
    cut = best + 1
    while cut < n and reply[cut] in " \t\n":
        cut += 1
    part1 = reply[:cut].strip()
    part2 = reply[cut:].strip()
    # 拆成两句时，第一句末尾不留逗号/顿号这类「话没说完」的标点
    part1 = re.sub(r"(?<!。)。$", "", part1.rstrip("，,、:：;；"))
    if len(part1) < 4 or len(part2) < 4:
        return None
    return [part1, part2]


def _tame_dashes(text: str) -> str:
    """压住 AI 腔破折号：句尾的删掉，句中最多留一个，其余换成逗号。"""
    if not text or "——" not in text:
        return text
    # 句尾破折号（可带波浪号/空格）直接删掉
    text = re.sub(r"——+\s*[~～]?\s*$", "", text).strip()
    if "——" not in text:
        return text
    # 句中：最多保留一个破折号，多余的换成逗号
    parts = text.split("——")
    if len(parts) <= 2:
        return text
    kept = parts[0] + "——" + parts[1]
    rest = "，".join(p for p in parts[2:] if p)
    if rest:
        kept += "，" + rest
    return kept.strip()


def _should_send_sticker(group_id: int, wanted: bool = True) -> bool:
    """每条（有收藏表情可用的）回复调一次；wanted = 她自己挑了一张 / 随机兜底掷中了。
    不管是她挑的还是兜底配的，都记在同一本账上：附带表情的回复占比不超过「附带表情包概率」。"""
    st = _sticker_stats.setdefault(group_id, {"replies": 0, "stickers": 0})
    st["replies"] += 1
    if not wanted or st["stickers"] / max(st["replies"], 1) >= config.STICKER_PROBABILITY:
        return False
    st["stickers"] += 1
    return True


def _tail_too_similar(a: str, b: str, tail_ratio: float = 0.85, min_len: int = 8) -> bool:
    """只比较两条回复去掉开头 40% 后的后半截，抓「前半句不同、后半句复读」的情况。"""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    tail_a = a[max(0, int(len(a) * 0.4)):]
    tail_b = b[max(0, int(len(b) * 0.4)):]
    if len(tail_a) < min_len or len(tail_b) < min_len:
        return False
    return difflib.SequenceMatcher(None, tail_a, tail_b).ratio() >= tail_ratio


def _too_similar(a: str, b: str, threshold: float = 0.8) -> bool:
    """判断两条回复是否过于雷同（用于避免每次都回同样的话）。"""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    return (
        difflib.SequenceMatcher(None, a, b).ratio() >= threshold
        or _tail_too_similar(a, b)
    )


# 绕口病句模式：模型偶尔会输出「得看毛茸茸度够不够才算好福瑞」这类句子，
# 检测到就重试一次，要求换成普通直白的人话
_AWKWARD_SENTENCE_RES = [
    re.compile(r"[得需]看?.*?度够不够.*?才算"),
    re.compile(r"度够不够才"),
]


def _is_awkward_sentence(text: str) -> bool:
    return any(r.search(text or "") for r in _AWKWARD_SENTENCE_RES)


def _desc_allowed(group_id: int) -> bool:
    """每个群每小时最多生成 CHAT_IMAGE_DESC_MAX_PER_HOUR 次图片描述。"""
    now = time.time()
    ts_list = _desc_stats.setdefault(group_id, [])
    ts_list[:] = [t for t in ts_list if now - t < 3600]
    if len(ts_list) >= config.CHAT_IMAGE_DESC_MAX_PER_HOUR:
        return False
    ts_list.append(now)
    return True


def _strip_context_markers(text: str) -> str:
    """清理模型从上下文历史里学来的占位符（[图片]、[@某人]、[表情]、[视频]、[合并转发]等）
    以及"图片"尾巴、昵称前缀、元信息复述。这些只是历史记录的格式标记，模型误模仿到回复里要删掉。"""
    t = (text or "").strip()
    # 0) 去掉模型复述的引用描述（如"（引用了XXX的消息：「...」）"、"引用了XXX的消息：..."）
    t = re.sub(r"[（(]\s*引用了[^）)]*的消息[^）)]*[）)]\s*", "", t)
    t = re.sub(r"^引用了[^：:]*的消息[：:]\s*", "", t)
    # 0.1) 去掉模型复述的艾特说明（如"（艾特了某个群友）"）
    t = re.sub(r"[（(]\s*艾特了[^）)]*[）)]\s*", "", t)
    # 0.2) 去掉开头的昵称前缀（如"小木屑：你这问题..."、"群主：..."）
    #      模型偶尔会把历史消息里的昵称当成回复开头，需要剥掉
    #      （纯数字开头的是时间，如“8:30 叫你”，不能当昵称剥掉，否则会变成“30 叫你”）
    t = re.sub(r"^(?![0-9]+[：:])[\u4e00-\u9fa5a-zA-Z0-9_]{1,12}[：:]\s*", "", t).strip()
    # 0.3) 去掉元信息复述：包含"随机接话""自然接话""当前场景""说话人是"等词的句子
    for meta_word in ("随机接话", "自然接话", "当前场景", "说话人是", "说话人：", "不是TA在特意找你"):
        if meta_word in t:
            # 去掉包含这个词的整个句子
            t = re.sub(r"[^。！？~～…]*" + re.escape(meta_word) + r"[^。！？~～…]*[。！？~～…]?\s*", "", t)
    t = t.strip()
    # 1) 上下文占位符（图片/艾特/表情/视频/合并转发）出现在回复里一律删掉
    for marker in ("图片", "@某人", "表情", "视频", "合并转发", "@群主"):
        t = re.sub(r"\s*\[" + marker + r"\]\s*", " ", t)
    # 2) 去掉开头的占位符残留（如"[图片] 晚安"），但不动以"图片"开头的正常句子
    t = re.sub(r"^\s*\[(图片|@某人|表情|视频|合并转发)\]\s*[,，。~～、]?\s*", "", t).strip()
    # 3) 去掉结尾的 [图片] 占位符，以及"图片"前有空格/标点的尾巴（如"晚安 [图片]"、"晚安 图片"）
    t = re.sub(r"[\s，,。~～、]*\[图片\]\s*$", "", t).strip()
    t = re.sub(r"[\s，,。~～、]+图片\s*$", "", t).strip()
    # 4) 整理多余空白（换行保留：一行就是一条消息）
    t = _squash_spaces(t)
    return t


def _squash_spaces(t: str) -> str:
    """压掉多余空格，但保留单个换行（分条发送靠换行）。"""
    t = re.sub(r"[ \t\u3000]{2,}", " ", t or "")
    t = re.sub(r"[ \t\u3000]*\n\s*", "\n", t)
    return t.strip()


def _trim_reply(text: str, max_len: int) -> str:
    """超长回复截断到 max_len 字符，尽量在标点处断开，避免硬切。max_len<=0 表示不限。"""
    text = (text or "").strip()
    if max_len <= 0 or len(text) <= max_len:
        return re.sub(r"(?<!。)。$", "", text)
    cut = text[:max_len]
    for punct in ("。", "！", "？", "～", "~", "…", "，", ","):
        idx = cut.rfind(punct)
        if idx >= max_len * 0.5:
            return re.sub(r"(?<!。)。$", "", cut[: idx + 1].strip())
    return re.sub(r"(?<!。)。$", "", cut.strip())


def _scrub_fixed_template(text: str) -> str:
    """去掉模型反复模仿的固定开场模板（历史记忆和回复里都做一层兜底清理）。

    只清理「宝~ 你怕不是个铸币哦～……」这类固定连招、开头的「宝~」称呼，
    以及不带「宝」前缀的「你怕不是个铸币哦，这都戳不穿我？」这类固定开场，
    不影响正常回复，也不禁止「铸币」这个词本身在其他地方使用。
    """
    t = (text or "").strip()
    orig = t
    # 1) 完整固定连招：哼/切/呵 等语气词 + 宝 + 你怕不是个铸币哦（含后面的波浪线/省略号）
    t = re.sub(r"(?:哼|切|呵|啧)?[\s，,。~～…]*宝[~～，,]\s*你怕不是个铸币哦[~～…，,\s]*", "", t)
    # 2) 单独开头的「宝~」「宝，」称呼
    t = re.sub(r"^[\s，,。~～…]*宝[~～，,：:]\s*", "", t)
    # 3) 不带「宝」前缀的固定开场「你怕不是个铸币哦」及其常见连招（这都戳不穿我？）也删掉
    t = re.sub(
        r"^(?:哼|切|呵|啧)?[\s，,。~～…]*你怕不是个铸币哦"
        r"(?:[~～…，,\s]*(?:这都?戳不穿我?[？?！!。~～…]*)?)?"
        r"[，,。~～…！!？?]*$",
        "",
        t,
    )
    # 4) 整理空白；只在确实清理掉模板时顺手去掉边缘残留标点，避免误伤正常结尾的波浪号
    t = _squash_spaces(t)
    if t != orig:
        t = t.strip(" ，,。~～…")
    return t.strip()

# 网络梗括号白名单：这些括号内容保留，其他长括号（动作/神态/心理描述）一律删除
_MEME_BRACKETS = {
    "不是", "bushi", "狗头", "手动狗头", "雾", "doge", "笑哭", "滑稽", "摊手",
    "无奈", "抠鼻", "汗", "冷汗", "擦汗", "黑线", "无语", "晕", "震惊", "惊讶",
    "害怕", "生气", "开心", "难过", "伤心", "害羞", "脸红", "偷笑", "坏笑", "得意",
    "傲慢", "闭嘴", "睡", "困", "累", "饿", "渴", "冷", "热", "病", "死", "吐", "呕",
    "微笑", "呵呵", "嘻嘻", "哈哈", "嘿嘿", "汪汪", "喵喵", "耶", "耶耶", "冲", "冲冲",
    "加油", "奥利给", "绝了", "离谱", "好家伙", "真的假的", "真的吗", "是吗", "对吗",
    "好吧", "行吧", "算了", "罢了", "而已", "罢辽", "辣鸡", "垃圾", "菜", "菜鸡",
    "大佬", "大神", "牛", "牛逼", "牛批", "牛啊", "强", "强啊", "厉害", "666", "6",
    "233", "2333", "hhh", "hhhh", "xswl", "awsl", "yyds", "nsdd", "bdjw", "zqsg",
    "skr", "pick", "respect", "瑞思拜", "瑞斯拜", "真香", "打脸", "翻车", "躺平",
    "内卷", "emo", "emmm", "emm", "em", "哦", "噢", "喔", "嗯", "嗯嗯", "呐", "呢",
    "啦", "呀", "啊", "吧", "嘛", "呗", "哟", "喽", "咧", "哩", "哒", "滴", "咩", "喵",
    "汪", "嗷", "呜", "哇", "呀", "咿", "呀", "唷", "嗬", "嘿", "嗨", "哈", "哼", "呸",
    "呼", "嘘", "唰", "嗖", "叮", "咚", "当", "砰", "啪", "嚓", "嘎", "吱", "嗡", "哗",
    "啦", "啦", "啦", "啦", "啦", "啦", "啦", "啦", "啦", "啦", "啦", "啦", "啦", "啦",
}


def _strip_action_parentheses(text: str) -> str:
    """删除回复中括号里的动作/神态/心理描述，但保留网络梗括号。"""
    if not text:
        return text

    def _replace(match):
        content = match.group(1).strip()
        # 白名单中的网络梗保留
        if content in _MEME_BRACKETS:
            return match.group(0)
        # 极短内容（<=3字符）可能是语气词/短梗，保留
        if len(content) <= 3:
            return match.group(0)
        # 长内容（>3字符）大概率是动作/神态/心理描述，删除整个括号
        return ""

    # 先处理中文括号（），再处理英文括号()
    result = re.sub(r'（([^）]*)）', _replace, text)
    result = re.sub(r'\(([^)]*)\)', _replace, result)
    # 清理删除括号后可能留下的多余空格
    result = re.sub(r'  +', ' ', result)
    return result.strip()


# ---------------- 小鲸鱼式分条：一条消息一个意群 ----------------
_SOFT_BREAK = "，,；;：:"
_HARD_BREAK = "！？!?…~～"


def _clauses(line: str) -> list[str]:
    """按标点切成小句（标点留在前一段末尾），连续标点不拆开。"""
    out, buf = [], ""
    for i, ch in enumerate(line):
        buf += ch
        nxt = line[i + 1] if i + 1 < len(line) else ""
        if ch in _SOFT_BREAK + _HARD_BREAK + "。" and nxt not in _SOFT_BREAK + _HARD_BREAK + "。":
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _split_long_line(line: str, soft: int) -> list[str]:
    """一行太长时拆成几条：优先在 ！？… 处断，还长就在逗号处断；没有标点的整句不硬切。"""
    if len(line) <= soft + max(6, soft // 3):
        return [line]
    pieces, cur = [], ""
    for c in _clauses(line):
        if cur and len(cur) + len(c) > soft:
            pieces.append(cur)
            cur = c
        else:
            cur += c
        # 句末语气（！？…）已经够长就单独成条
        if cur and cur[-1] in _HARD_BREAK and len(cur) >= soft * 0.6:
            pieces.append(cur)
            cur = ""
    if cur:
        pieces.append(cur)
    out = []
    for p in pieces:
        p = p.strip().rstrip("，,；;：:").strip()
        if not p:
            continue
        # 自动拆出来的太短的碎片（<6 字）并回前一条，免得「嗯，」单独一条
        if out and len(p) < 6 and len(out[-1]) + len(p) <= soft + 8:
            out[-1] = out[-1] + "，" + p if out[-1][-1:] not in _HARD_BREAK else out[-1] + p
        else:
            out.append(p)
    return out or [line]


_CJK_SPACE = re.compile(r"(?<=[\u4e00-\u9fff])[ \u3000]+(?=[\u4e00-\u9fff])")


# 标记被剥掉后剩下的残渣：只有括号/尖括号/冒号/空白（「？」「。。。」「！」这些是正常回复，不算）
_JUNK_LINE = re.compile(r"[\[\]【】<>{}:：\s]+")


def burst_parts(reply: str, soft_max: int | None = None, burst_max: int | None = None) -> list[str]:
    """把一段回复拆成几条消息（像真人一样一句一条发）。

    - 模型写的每一行就是一条；
    - 某一行太长才自动拆：先在 ！？… 处，再在逗号处；不会把半句话拆开；
    - 不限总条数（burst_max>0 时多出来的并进最后一条）。
    """
    soft = max(12, int(soft_max if soft_max is not None else (config.MSG_SOFT_MAX or 30)))
    cap = int(burst_max if burst_max is not None else (config.BURST_MAX or 0))
    parts: list[str] = []
    for line in (reply or "").split("\n"):
        line = _CJK_SPACE.sub("，", line.strip())  # 一条消息里别用空格隔开几句话：中文之间的空格换成逗号
        if line and not _JUNK_LINE.fullmatch(line):  # 只剩「[」「]」这种标记残渣的行不发
            parts.extend(_split_long_line(line, soft))
    if cap > 0 and len(parts) > cap:
        parts = parts[:cap - 1] + ["，".join(parts[cap - 1:])]
    return parts
