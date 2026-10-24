"""她自己做过的小动作（贴表情 / 点赞 / 爱心 / 戳一戳）：记下来，聊天时让她知道是自己干的。

以前贴完表情就忘了，群友问「你是不是给我点赞了」她还否认。现在：
- 每做一次就记一条（存 SQLite，机器人重启也不丢），只留最近 40 条、12 小时内的；
- 群聊记录里那条消息后面会标「（你给这条贴了「赞」）」；
- 回复时附一小段「你刚做过的小动作」，并告诉她：是你做的就大方承认，可以打趣，但不许否认。
设置 → 活人感 →「记得自己做过的小动作」可关（AI_CHAT_SELF_ACTS）。
"""
from __future__ import annotations

import time

from . import config
from ...core import db

NS = "chat_selfacts"
KEEP = 40
SHOW_HOURS = 12.0


def record(group_id, kind: str, text: str, *, target_uid: str = "", mid=None, now: float | None = None) -> None:
    """记一条：kind = react / poke；text 是给她看的一句话（如「给阿成的『谢谢』贴了个『赞』」）。"""
    if group_id is None or not text:
        return
    item = {"kind": kind, "text": str(text)[:80], "ts": float(now or time.time())}
    if target_uid:
        item["uid"] = str(target_uid)
    if mid:
        item["mid"] = str(mid)

    def _u(lst):
        lst = list(lst or [])
        lst.append(item)
        return lst[-KEEP:]
    try:
        db.update(NS, str(group_id), _u)
    except Exception:  # noqa: BLE001  记不下来也不影响聊天
        pass


def recent(group_id, hours: float = SHOW_HOURS, now: float | None = None) -> list[dict]:
    ts = time.time() if now is None else now
    try:
        items = list(db.get(NS, str(group_id)) or [])
    except Exception:  # noqa: BLE001
        return []
    return [x for x in items if ts - float(x.get("ts") or 0) <= hours * 3600]


def _ago(ts: float, now: float) -> str:
    sec = max(0.0, now - float(ts or 0))
    if sec < 90:
        return "刚刚"
    if sec < 3600:
        return f"{int(sec // 60)} 分钟前"
    return f"{int(sec // 3600)} 小时前"


def note(group_id, user_id=None, now: float | None = None, limit: int = 6) -> str:
    """给模型看的一段「你刚做过的小动作」；关掉或没有就返回空串。对当前说话人做过的排在前面。"""
    if not config.SELF_ACTS:
        return ""
    ts = time.time() if now is None else now
    items = recent(group_id, now=ts)
    if not items:
        return ""
    uid = str(user_id or "")
    mine = [x for x in items if uid and x.get("uid") == uid]
    others = [x for x in items if x not in mine]
    picked = (mine[-3:] + others[-(limit - min(3, len(mine))):])[-limit:]
    picked.sort(key=lambda x: float(x.get("ts") or 0))
    lines = [f"- {_ago(x.get('ts'), ts)}你{x['text']}" for x in picked]
    return ("【你最近亲手做的小动作（全是你自己做的，记得清清楚楚）】\n" + "\n".join(lines) + "\n"
            "有人问「是你给我点的赞吗」「刚才谁贴的表情」「你戳我干嘛」时，大方承认是你，"
            "可以顺势傲娇、打趣、找个借口（比如「手滑」「觉得你说得好嘛」），但绝不能说不是你、也别说自己做不到。"
            "没人问就别主动提。")
