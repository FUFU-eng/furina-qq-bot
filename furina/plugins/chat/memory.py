"""按群维护最近对话上下文（内存）。"""

import time
from collections import defaultdict, deque

from . import config

_histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=config.HISTORY_LIMIT))
# 每群最近几张图的引用（只存 file/url/时间/发送人，不存字节），供“这谁/这是啥”类追问时补看
_recent_images: dict[int, deque] = defaultdict(lambda: deque(maxlen=6))


def same(entry, role: str, content: str) -> bool:
    """条目是否为同一条消息（只比 role/content，忽略 ts 等附加字段）。"""
    return isinstance(entry, dict) and entry.get("role") == role and entry.get("content") == content


# 结构化上下文用的附加字段：uid=QQ号, mid=消息ID, reply=引用的消息ID, name=显示名, to=回复的目标消息ID
META_KEYS = ("uid", "mid", "reply", "name", "to")


def _meta(meta: dict | None) -> dict:
    out = {}
    for k in META_KEYS:
        v = (meta or {}).get(k)
        if v not in (None, "", 0, "0"):
            out[k] = str(v)
    return out


def add_message(group_id: int, role: str, content: str, ts: float | None = None, meta: dict | None = None):
    content = (content or "").strip()
    if not content:
        return
    dq = _histories[group_id]
    extra = _meta(meta)
    # 同一条 QQ 消息（mid 相同）只记一次
    if extra.get("mid") and any(e.get("mid") == extra["mid"] and e.get("role") == role for e in list(dq)[-30:]):
        return
    # 连续两条完全相同的消息视为重复（启动加载历史 + 实时消息可能重叠），丢弃后一条
    if dq and same(dq[-1], role, content) and not (extra.get("mid") and dq[-1].get("mid") and dq[-1]["mid"] != extra["mid"]):
        if extra and not any(k in dq[-1] for k in extra):
            dq[-1].update(extra)
        return
    entry = {"role": role, "content": content, "ts": float(ts or time.time())}
    entry.update(extra)
    dq.append(entry)


def seed(group_id: int, messages: list[dict]):
    """批量灌入历史消息（按时间从旧到新），用于启动时加载群聊天记录。"""
    dq = _histories[group_id]
    for msg in messages:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        extra = _meta(msg)
        if extra.get("mid") and any(e.get("mid") == extra["mid"] for e in list(dq)[-60:]):
            continue
        if dq and same(dq[-1], role, content) and not (extra.get("mid") and dq[-1].get("mid") and dq[-1]["mid"] != extra["mid"]):
            continue
        entry = {"role": role, "content": content}
        entry.update(extra)
        try:
            if msg.get("ts"):
                entry["ts"] = float(msg["ts"])
        except (TypeError, ValueError):
            pass
        dq.append(entry)


def annotate(group_id: int, mid, key: str, value) -> bool:
    """给某条消息（按 mid 找）加个附注，如她给这条贴了什么表情：reacted="赞"。找不到返回 False。"""
    if not mid or not value:
        return False
    dq = _histories[group_id]
    for e in reversed(list(dq)[-80:]):
        if str(e.get("mid") or "") == str(mid):
            e[key] = value
            return True
    return False


def get_history(group_id: int) -> list:
    return list(_histories[group_id])


def pop_last(group_id: int):
    dq = _histories[group_id]
    if dq:
        return dq.pop()
    return None


def replace_last_if(group_id: int, old_content: str, new_content: str) -> bool:
    """如果最后一条消息内容等于 old_content，就原地替换成 new_content（用于后台补图片描述）。"""
    dq = _histories[group_id]
    if dq and dq[-1]["content"] == old_content:
        dq[-1]["content"] = new_content
        return True
    return False


def replace_content(group_id: int, old_content: str, new_content: str) -> bool:
    """从后往前找内容等于 old_content 的那条消息，替换成 new_content。
    群消息多时图片描述是后台异步生成的，图片那条早就不在队尾了，按内容定位更可靠。"""
    dq = _histories[group_id]
    for i in range(len(dq) - 1, -1, -1):
        if dq[i]["content"] == old_content:
            dq[i]["content"] = new_content
            return True
    return False


# 已回复过的消息：mid -> 当时的回复（防止同一条消息回两次；补充回复时提醒别重复）
_replied: dict[int, dict] = defaultdict(dict)


def mark_replied(group_id: int, mids, reply: str) -> None:
    box = _replied[group_id]
    for mid in mids or ():
        if mid:
            box[str(mid)] = (reply or "")[:120]
    while len(box) > 300:
        box.pop(next(iter(box)))


def replied_text(group_id: int, mid) -> str | None:
    return _replied[group_id].get(str(mid)) if mid else None


def recent_assistant_messages(group_id: int, n: int = 5) -> list:
    """取最近 n 条机器人自己的回复（用于避免说重复的话）。"""
    out = [m["content"] for m in _histories[group_id] if m["role"] == "assistant"]
    return out[-n:]


def remember_images(group_id: int, sender: str, refs: list):
    """记下本条消息里的图片引用 [(file, url), ...]。"""
    now = time.time()
    for file_id, url in refs:
        if file_id or url:
            _recent_images[group_id].append({"ts": now, "sender": sender, "file": file_id, "url": url})


def recent_images(group_id: int, within: float) -> list:
    """最近 within 秒内的图片引用，按时间从新到旧。"""
    now = time.time()
    return [x for x in reversed(_recent_images[group_id]) if now - x["ts"] <= within]


def reset(group_id: int):
    _histories[group_id].clear()
    _replied[group_id].clear()
    _recent_images[group_id].clear()
