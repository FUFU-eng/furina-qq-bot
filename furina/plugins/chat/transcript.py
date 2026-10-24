"""结构化群聊记录：每条带编号、时间、说话人、引用了哪条（参考 MaiBot 的 msg_id/quote 标注）。

模型看到的不再是一串「昵称：内容」，而是
    #12 14:03 阿成 ↩#9：那家店关门了
    #13 14:03 你：……
这样谁在接谁的话、谁是谁一目了然；重名的人会带上 QQ 尾号区分。

省钱：窗口「锚定」而不是每条都滑动。以前永远取最后 N 条，每来一条新消息整段记录的编号和内容都往前挪一位，
模型那边的前缀缓存一个字都命中不了（判断 + 回复两次调用全按未命中计费）。现在每个群记一个起点，
新消息只往后追加，起点之前的记录原样不动；攒到超过上限才把起点挪到最近 60% 左右重新开始。
这样连着几次调用，记录前面那一大段都能按缓存命中价（约 1/50）计费。
"""
import re
import time

from . import config

_REPLY_RE = re.compile(r"\[(?:CQ:)?reply[,:]id=(-?\d+)\]")
_TAG_RE = re.compile(r"[#＃]\d{1,3}(?!\d)")


def event_meta(event, nickname: str) -> dict:
    """从群消息事件里取结构化字段（QQ 号 / 消息 ID / 引用的消息 ID / 显示名）。"""
    reply_mid = ""
    m = _REPLY_RE.search(str(getattr(event, "raw_message", "") or ""))
    if m:
        reply_mid = m.group(1)
    else:
        rep = getattr(event, "reply", None)
        if rep is not None and getattr(rep, "message_id", None):
            reply_mid = str(rep.message_id)
    return {"uid": str(getattr(event, "user_id", "") or ""), "mid": str(getattr(event, "message_id", "") or ""),
            "reply": reply_mid, "name": nickname}


def _split_name(entry: dict) -> tuple[str, str]:
    content = entry.get("content") or ""
    name = entry.get("name") or ""
    if name and content.startswith(name + "："):
        return name, content[len(name) + 1:]
    if "：" in content:
        head, body = content.split("：", 1)
        if 0 < len(head) <= 24:
            return name or head, body
    return name or "群友", content


def _clock(ts) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return "--:--"


def _gap(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"—— 过了 {minutes} 分钟 ——"
    hours = minutes // 60
    return f"—— 过了 {hours} 小时 ——" if hours < 24 else "—— 隔了很久 ——"


_anchors: dict[str, str] = {}


def _rid(e: dict) -> str:
    if e.get("mid"):
        return "m" + str(e["mid"])
    return f"t{e.get('ts')}|{e.get('role')}|{str(e.get('content') or '')[:16]}"


def _window(rows: list, limit: int, anchor: str | None) -> list:
    """取要给模型看的那一段：有锚点就从锚点开始只往后追加，超过上限才重新起锚。"""
    if not limit or limit <= 0:
        return rows
    if not anchor:
        return rows[-limit:]
    a = _anchors.get(anchor)
    if a is not None:
        for i, e in enumerate(rows):
            if _rid(e) == a:
                if len(rows) - i <= limit:
                    return rows[i:]
                break
    if len(rows) <= limit and a is None:
        new = rows
    else:
        new = rows[-max(6, int(limit * 0.6)):]
    if new:
        _anchors[anchor] = _rid(new[0])
        if len(_anchors) > 500:
            _anchors.pop(next(iter(_anchors)))
    return new


def render(history: list, exclude_mids=(), limit: int | None = None,
           anchor: str | None = None) -> tuple[str, dict]:
    """返回 (记录文本, {"#k": 这一行的简短文本})。anchor：群号之类，给了就用锚定窗口（见模块说明）。"""
    limit = config.TRANSCRIPT_LIMIT if limit is None else limit
    skip = {str(x) for x in exclude_mids or () if x}
    rows = [e for e in history if isinstance(e, dict) and not (e.get("mid") and str(e["mid"]) in skip)]
    rows = _window(rows, limit, anchor)
    # 重名不同人：显示名后面带 QQ 尾号
    owners: dict[str, set] = {}
    for e in rows:
        if e.get("role") == "user":
            n, _ = _split_name(e)
            if e.get("uid"):
                owners.setdefault(n, set()).add(e["uid"])
    mid_to_no: dict[str, int] = {}
    lines, index, prev_ts = [], {}, None
    for no, e in enumerate(rows, 1):
        ts = e.get("ts")
        try:
            ts = float(ts) if ts else None
        except (TypeError, ValueError):
            ts = None
        if ts and prev_ts and ts - prev_ts >= config.HISTORY_GAP_SECONDS:
            lines.append(_gap(ts - prev_ts))
        if ts:
            prev_ts = ts
        if e.get("role") == "assistant":
            who = "你"
            body = (e.get("content") or "")[:config.HISTORY_SEND_CHARS_SELF]
        else:
            who, body = _split_name(e)
            if len(owners.get(who, ())) > 1 and e.get("uid"):
                who = f"{who}(尾号{e['uid'][-4:]})"
            body = body[:config.HISTORY_SEND_CHARS]
        ref = ""
        rmid = str(e.get("reply") or e.get("to") or "")
        if rmid:
            ref = f" ↩#{mid_to_no[rmid]}" if rmid in mid_to_no else " ↩更早的消息"
        if e.get("mid"):
            mid_to_no[str(e["mid"])] = no
        body = body.replace("\n", " ").strip()
        if e.get("reacted"):
            body += f"（你给这条贴了「{e['reacted']}」表情）"
        lines.append(f"#{no} {_clock(ts)} {who}{ref}：{body}")
        index[f"#{no}"] = f"{who}：{body[:60]}"
    return "\n".join(lines), index


def strip_tags(reply: str) -> str:
    """模型偶尔会把「#12」这种编号带进回复，发出去前去掉。"""
    out = _TAG_RE.sub("", reply or "")
    return re.sub(r"[ \t]{2,}", " ", out).strip()
