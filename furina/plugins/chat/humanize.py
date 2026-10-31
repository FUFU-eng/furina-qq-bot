"""拟人发送：按字数「打字」再发、句子长了分几条、条间停顿；外加发送限速和熔断（防风控）。

参考 ai-companion-kit 的 humanize.js 与其防风控章节：
- 打字时间跟字数成正比但带抖动，凌晨更慢；模型生成已经花掉的时间从第一条里扣掉；
- 每群每分钟 / 每群每小时条数上限（各群分开算），超了就不发（不排队、不重试）；
- 连续 3 次发送失败熔断 3 分钟，期间 AI 聊天不再发消息，避免「重试风暴」。
"""
import asyncio
import random
import time
from collections import defaultdict, deque

from nonebot import logger

from . import config

NIGHT = (2, 7)
MAX_FIRST_DELAY = 6.0
MAX_GAP = 6.0


def typing_seconds(text: str) -> float:
    """打这条字要多久：1.2 秒 + 每字 TYPING_MS_PER_CHAR（默认 110）毫秒，夹在 1.2~6 秒，±20% 抖动，凌晨 2~7 点 ×1.6。"""
    n = len((text or "").strip())
    ms = 1200 + n * max(0, config.TYPING_MS_PER_CHAR)
    ms = min(6000, max(1200, ms)) * (1 + random.uniform(-0.2, 0.2))
    if NIGHT[0] <= time.localtime().tm_hour < NIGHT[1]:
        ms *= 1.6
    return ms / 1000.0


def first_delay(text: str, already_spent: float) -> float:
    """第一条前的等待：打字时间扣掉模型已经思考的时间（消息瞬间完整到达最像机器人）。"""
    if not config.HUMANIZE:
        return 0.0
    return max(0.0, min(MAX_FIRST_DELAY, typing_seconds(text) - max(0.0, already_spent)))


def _long_gap(rng=random) -> float:
    """偶尔像被什么打断了一样，多愣 4~12 秒（AI_CHAT_LONG_GAP_PROBABILITY，默认 0.3）。"""
    p = max(0.0, min(1.0, config.LONG_GAP_PROBABILITY))
    return rng.uniform(4.0, 12.0) if rng.random() < p else 0.0


def gap_before(text: str) -> float:
    """后续每条前的等待：0.7~2.6 秒 + 打这条字的时间的一部分，偶尔长停顿。"""
    if not config.HUMANIZE:
        return 0.0
    s = min(MAX_GAP, random.uniform(0.7, 2.6) + typing_seconds(text) * 0.5)
    return s + _long_gap()


def burst_gap(text: str) -> float:
    """连发几条时，后面每条前的停顿。开着「模拟打字」按打字速度停；关掉就只隔不到 1 秒（保证顺序，不刻意拖）。"""
    if config.HUMANIZE:
        return gap_before(text)
    return random.uniform(0.4, 0.9)


async def pause(seconds: float) -> None:
    if seconds > 0:
        await asyncio.sleep(seconds)


# ---------------- 发送限速 + 熔断 ----------------
# 每个群一条队列，存最近 1 小时内的发送时间；每分钟、每小时都按本群自己算，群与群互不占额度
_group_sent: dict[int, deque] = defaultdict(deque)
_breaker = {"fails": 0, "open_until": 0.0}
BREAKER_FAILS = 3
BREAKER_SECONDS = 180


def _prune(dq: deque, window: float, now: float) -> None:
    while dq and now - dq[0] > window:
        dq.popleft()


def allow_send(group_id) -> tuple[bool, str]:
    now = time.time()
    if now < _breaker["open_until"]:
        return False, f"发送熔断中（还剩 {int(_breaker['open_until'] - now)} 秒）"
    g = _group_sent[int(group_id or 0)]
    _prune(g, 3600, now)
    if not config.SEND_LIMIT_ENABLED:
        # 设置里一键关掉了发言次数限制：不看每分钟 / 每小时条数（发送记录照记，重新打开立刻按真实数量算）
        return True, ""
    per_min, per_hour = config.MAX_SEND_PER_MINUTE, config.MAX_SEND_PER_HOUR
    last_min = sum(1 for t in g if now - t <= 60)
    if per_min > 0 and last_min >= per_min:
        return False, f"本群 1 分钟内已发 {last_min} 条"
    if per_hour > 0 and len(g) >= per_hour:
        return False, f"本群 1 小时内已发 {len(g)} 条"
    return True, ""


def at_limit(group_id) -> str:
    """发送已到上限（每分钟 / 每小时 / 熔断）时返回原因，否则返回空串。
    要发消息的流程在调用模型前先问一句：反正发不出去，就别白花模型的钱。"""
    ok, why = allow_send(group_id)
    return "" if ok else why


def skip_if_limited(group_id, tag: str = "AI聊天") -> bool:
    """到上限就记一行日志并返回 True（调用方直接放弃这次生成）。"""
    why = at_limit(group_id)
    if why:
        logger.info(f"[{tag}] 群{group_id} 发送已到上限（{why}），这次不调用模型")
        return True
    return False


def _sent_ok(group_id) -> None:
    now = time.time()
    _group_sent[int(group_id or 0)].append(now)
    _breaker["fails"] = 0


def _sent_fail(err) -> None:
    _breaker["fails"] += 1
    if _breaker["fails"] >= BREAKER_FAILS:
        _breaker["open_until"] = time.time() + BREAKER_SECONDS
        _breaker["fails"] = 0
        logger.warning(f"[AI聊天] 连续 {BREAKER_FAILS} 次发送失败，熔断 {BREAKER_SECONDS} 秒（期间不再发聊天消息）: {err}")


def _plain(message) -> str:
    try:
        if hasattr(message, "extract_plain_text"):
            return message.extract_plain_text().strip()
        data = getattr(message, "data", None)
        if isinstance(data, dict):
            return str(data.get("text") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return str(message or "").strip() if isinstance(message, str) else ""


def _note_unsent(group_id, message, why: str) -> None:
    """没发出去的话记下来，下次回复时告诉她「这几句群里没人看到」（见 presence）。"""
    try:
        from . import presence
        presence.record_unsent(group_id, _plain(message), why)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[AI聊天] 记录发送失败出错: {e!r}")


def prepare_message(message):
    """聊天发送前的文字闸门：清占位串和标点，不改媒体、引用或转发原文。"""
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    from .cleanup import drop_periods, strip_qq_placeholders

    removed_placeholder = False

    def clean_text(value):
        nonlocal removed_placeholder
        if not value or not value.strip():
            return value
        cleaned = strip_qq_placeholders(value)
        changed = cleaned != value
        removed_placeholder = removed_placeholder or changed
        if not config.NO_PERIOD and not changed:
            return value
        leading = value[:len(value) - len(value.lstrip())]
        trailing = value[len(value.rstrip()):]
        body = cleaned.strip()
        if config.NO_PERIOD:
            body = drop_periods(body)
        return leading + body + trailing if body else ""

    if isinstance(message, str):
        return clean_text(message)
    if isinstance(message, MessageSegment):
        if message.type != "text":
            return message
        value = clean_text(str(message.data.get("text") or ""))
        if not config.NO_PERIOD and not removed_placeholder:
            return message
        return MessageSegment("text", {**message.data, "text": value})
    if isinstance(message, Message):
        out, run = [], []
        def flush():
            if run:
                value = clean_text("".join(str(seg.data.get("text") or "") for seg in run))
                if value:
                    out.append(MessageSegment("text", {**run[0].data, "text": value}))
                run.clear()
        for seg in message:
            if seg.type == "text":
                run.append(seg)
            else:
                flush()
                out.append(seg)
        flush()
        # 纯占位串被剥掉后，即使还留着 at/reply，也不能变成空艾特发出去。
        # 本来就有意单发 at 的消息不受影响，真实图片/表情/转发也照常发送。
        if removed_placeholder and not any(
            seg.type not in ("at", "reply", "text")
            or (seg.type == "text" and str(seg.data.get("text") or "").strip())
            for seg in out
        ):
            return Message()
        if not config.NO_PERIOD and not removed_placeholder:
            return message
        return Message(out)
    return message


def _empty_message(message):
    if isinstance(message, str):
        return not message.strip()
    if getattr(message, "type", "") == "text":
        return not str(message.data.get("text") or "").strip()
    try:
        return len(message) == 0
    except TypeError:
        return False


def guarded(sender, group_id):
    """包装发送函数：超限/熔断时不发并返回 None；发送异常只记日志，不再往上抛。
    没发出去的文字会记账，让她知道「刚才那句群里没人看到」。"""
    async def _send(message):
        message = prepare_message(message)
        if _empty_message(message):
            return None  # 只剩占位串或句号时不发空消息，也不计为发送失败
        ok, why = allow_send(group_id)
        if not ok:
            logger.warning(f"[AI聊天] 群{group_id} 跳过发送：{why}")
            _note_unsent(group_id, message, why)
            return None
        try:
            result = await sender(message)
        except Exception as e:  # noqa: BLE001  发送失败交给熔断器计数
            logger.warning(f"[AI聊天] 群{group_id} 发送失败: {e!r}")
            _sent_fail(e)
            _note_unsent(group_id, message, f"发送失败 {type(e).__name__}")
            return None
        _sent_ok(group_id)
        return result
    return _send


def breaker_status() -> dict:
    now = time.time()
    return {"open": now < _breaker["open_until"], "seconds_left": max(0, int(_breaker["open_until"] - now)),
            "fails": _breaker["fails"],
            "sent_last_hour": {gid: sum(1 for t in dq if now - t <= 3600) for gid, dq in _group_sent.items() if dq}}


def reset() -> None:
    _group_sent.clear()
    _breaker.update(fails=0, open_until=0.0)
