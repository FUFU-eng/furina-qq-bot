"""拟人发送：按字数「打字」再发、句子长了分几条、条间停顿；外加发送限速和熔断（防风控）。

参考 ai-companion-kit 的 humanize.js 与其防风控章节：
- 打字时间跟字数成正比但带抖动，凌晨更慢；模型生成已经花掉的时间从第一条里扣掉；
- 每群每分钟 / 全局每小时条数上限，超了就不发（不排队、不重试）；
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
    """连发几条时，后面每条前的停顿（不管开没开「模拟打字」都停一下，像真人一条条打出来）。"""
    if config.HUMANIZE:
        return gap_before(text)
    n = len((text or "").strip())
    s = min(4.0, 0.6 + n * 0.085) * (1 + random.uniform(-0.2, 0.2))
    if random.random() < 0.12:
        s += random.uniform(2.0, 6.0)
    if NIGHT[0] <= time.localtime().tm_hour < NIGHT[1]:
        s *= 1.4
    return s


async def pause(seconds: float) -> None:
    if seconds > 0:
        await asyncio.sleep(seconds)


# ---------------- 发送限速 + 熔断 ----------------
_group_sent: dict[int, deque] = defaultdict(deque)
_all_sent: deque = deque()
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
    _prune(g, 60, now)
    _prune(_all_sent, 3600, now)
    per_min, per_hour = config.MAX_SEND_PER_MINUTE, config.MAX_SEND_PER_HOUR
    if per_min > 0 and len(g) >= per_min:
        return False, f"本群 1 分钟内已发 {len(g)} 条"
    if per_hour > 0 and len(_all_sent) >= per_hour:
        return False, f"1 小时内已发 {len(_all_sent)} 条"
    return True, ""


def _sent_ok(group_id) -> None:
    now = time.time()
    _group_sent[int(group_id or 0)].append(now)
    _all_sent.append(now)
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


def guarded(sender, group_id):
    """包装发送函数：超限/熔断时不发并返回 None；发送异常只记日志，不再往上抛。
    没发出去的文字会记账，让她知道「刚才那句群里没人看到」。"""
    async def _send(message):
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
            "fails": _breaker["fails"], "sent_last_hour": len(_all_sent)}


def reset() -> None:
    _group_sent.clear()
    _all_sent.clear()
    _breaker.update(fails=0, open_until=0.0)
