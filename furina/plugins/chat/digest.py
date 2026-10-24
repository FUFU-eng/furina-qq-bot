"""每日群聊纪要 + 长期事实：给芙宁娜"记得昨天发生过什么"的能力。

- 运行时把群消息文本攒在内存（每群每天上限 DIGEST_MAX_LINES 条，单条截短）
- 每天 DIGEST_HOUR:DIGEST_MINUTE 用 LLM 把当天文本压成纪要 + 抽取长期事实
- 纪要/事实存 digest.json（保留最近 DIGEST_KEEP_DAYS 天），回复时注入
  最近 3 天纪要 + 最新 10 条长期事实（控制注入量，约几百 token/次）
- 成本：每天每群约 1 万~2 万 token 输入（flash 级，约 0.01 元/群/天）
"""

import threading
import time

from nonebot import get_driver, logger
from ...core import scheduler

from ...core import db

from . import config, llm
from ...core import permissions

_NS = "digest"            # key=日期，value={群号: {digest, facts}}
_BUF_NS = "digest_buffer"  # key="buffer"，当天攒下的消息（重启不丢）

LINE_CHARS = 60          # 每条记录最多截多少字符
NOTE_DAYS = 3            # 注入最近几天纪要
NOTE_DIGEST_CHARS = 500  # 单天纪要注入上限
NOTE_FACTS = 10          # 注入长期事实条数

_buffer: dict[int, list[str]] = {}
_lock = threading.Lock()
_task = None  # scheduler.Job
# 当天攒下的消息定期落盘：旧版只放内存，控制面板重启机器人后当天已收集的内容全部丢失，
# 晚上生成的纪要只剩重启之后的几句话。
_buffer_dirty = False
_NOTE_TTL = 60
_note_cache: dict[str, tuple[float, str]] = {}


def record_message(group_id, user_id, nickname, text, has_image):
    """记录一条当天消息文本（只记有内容的文字，图片记 [图片] 占位）。"""
    if not config.DIGEST_ENABLED:
        return
    if not permissions.allowed(str(group_id), "chat", True):
        return
    parts = []
    if has_image:
        parts.append("[图片]")
    t = (text or "").strip()
    if t:
        parts.append(t[: LINE_CHARS])
    if not parts:
        return
    line = f"{nickname}：{' '.join(parts)}"
    global _buffer_dirty
    with _lock:
        buf = _buffer.setdefault(group_id, [])
        if len(buf) >= config.DIGEST_MAX_LINES:
            # 满了就丢最早四分之一，保留最近内容
            del buf[: max(1, len(buf) // 4)]
        buf.append(line)
        _buffer_dirty = True


def _save_buffer(force: bool = False):
    """把当天缓冲写盘（有改动才写；后台循环每 30 秒调用一次，退出时强制写）。"""
    global _buffer_dirty
    with _lock:
        if not (_buffer_dirty or force):
            return
        snapshot = {"date": time.strftime("%Y-%m-%d"), "groups": {str(g): list(v) for g, v in _buffer.items() if v}}
        _buffer_dirty = False
    try:
        db.put(_BUF_NS, "buffer", snapshot)
    except Exception as e:
        logger.warning(f"[纪要] 保存当天消息缓冲失败: {e}")


def _load_buffer():
    """启动时恢复缓冲：只恢复今天或昨天的（昨天纪要时间之后的消息本来就并入下一天）。"""
    try:
        data = db.get(_BUF_NS, "buffer")
        if not isinstance(data, dict):
            return
        saved = str(data.get("date") or "")
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        if saved not in (time.strftime("%Y-%m-%d"), yesterday):
            return
        restored = 0
        with _lock:
            for gid, lines in (data.get("groups") or {}).items():
                if not isinstance(lines, list):
                    continue
                buf = _buffer.setdefault(int(gid), [])
                buf[:0] = [str(x) for x in lines][-config.DIGEST_MAX_LINES:]
                del buf[: max(0, len(buf) - config.DIGEST_MAX_LINES)]
                restored += len(lines)
        if restored:
            logger.info(f"[纪要] 已恢复重启前攒下的 {restored} 条当天消息")
    except Exception as e:
        logger.warning(f"[纪要] 恢复当天消息缓冲失败: {e}")


def _load_data() -> dict:
    try:
        return {k: v for k, v in db.items(_NS).items() if isinstance(v, dict)}
    except Exception as e:
        logger.warning(f"[纪要] 读取纪要失败: {e}")
        return {}


def _save_data(data: dict):
    try:
        db.put_many(_NS, data)
        _note_cache.clear()
    except Exception as e:
        logger.warning(f"[纪要] 保存纪要失败: {e}")


def _prune(data: dict):
    days = sorted(data.keys())
    for d in days[: -config.DIGEST_KEEP_DAYS]:
        data.pop(d, None)
        db.delete(_NS, d)


def get_digest_note(group_id) -> str:
    """生成注入提示词的记忆片段：最近几天纪要 + 长期事实。"""
    if not config.DIGEST_ENABLED:
        return ""
    gid = str(group_id)
    now = time.time()
    cached = _note_cache.get(gid)
    if cached and now - cached[0] < _NOTE_TTL:
        return cached[1]
    # 先查缓存再读文件：旧版每次回复都先整份读取解析 digest.json，60 秒缓存形同虚设
    data = _load_data()
    parts = []

    # 最近 N 天纪要
    days = sorted(data.keys())[-NOTE_DAYS:]
    digest_lines = []
    for d in days:
        rec = (data.get(d) or {}).get(gid) or {}
        text = rec.get("digest") or ""
        if text:
            digest_lines.append(f"[{d}] {text[: NOTE_DIGEST_CHARS]}")
    if digest_lines:
        parts.append(
            "最近几天群里发生过的事（模糊记忆：可以自然提起，但别照搬原句，也别编造细节）：\n"
            + "\n".join(digest_lines)
        )

    # 长期事实（按时间顺序去重，取最新 N 条）
    facts: list[str] = []
    seen = set()
    for d in sorted(data.keys()):
        rec = (data.get(d) or {}).get(gid) or {}
        for f in rec.get("facts") or []:
            f = (f or "").strip()
            if f and f not in seen:
                seen.add(f)
                facts.append(f)
    if facts:
        parts.append(
            "你记住的长期事实（重要的事情别忘了，聊天时可自然用上）：\n"
            + "\n".join(f"- {f}" for f in facts[-NOTE_FACTS:])
        )
    note = "\n\n".join(parts) if parts else ""
    _note_cache[gid] = (now, note)
    return note


def _split_digest(reply: str) -> tuple[str, list[str]]:
    """解析 LLM 输出：分隔【纪要】和【事实】两部分。"""
    reply = (reply or "").strip()
    digest_text, facts = reply, []
    if "【事实】" in reply:
        head, _, tail = reply.partition("【事实】")
        digest_text = head.replace("【纪要】", "").strip()
        for ln in tail.splitlines():
            ln = ln.strip().lstrip("-•0123456789.、 ")
            if ln and ln != "无" and len(ln) <= 40:
                facts.append(ln)
    digest_text = digest_text.replace("【纪要】", "").strip()
    return digest_text[: 1500], facts[:5]


async def _summarize_group(gid: int, lines: list[str]) -> tuple[str, list[str]]:
    user_content = "\n".join(lines)
    if len(user_content) > 8000:
        user_content = user_content[:8000]
    messages = [
        {
            "role": "system",
            "content": (
                "你是群聊纪要助手。把下面的群聊记录压缩成两部分，严格遵守输出格式：\n"
                "第一部分以【纪要】开头，列出今天群里发生的重要事情，5~10 条，"
                "每条一行、不超过 30 字，只记真实发生的事，不要编造；\n"
                "第二部分以【事实】开头，列出值得长期记住的事实（群友喜好、约定、"
                "外号、重要信息），最多 5 条，每条一行、不超过 25 字；"
                "没有就写【事实】无。"
            ),
        },
        {"role": "user", "content": user_content},
    ]
    reply = await llm.chat(messages)
    return _split_digest(reply)


async def _summarize_today():
    """把今天攒下的消息生成纪要（每群一次 LLM 调用）。"""
    with _lock:
        groups = {g: list(buf) for g, buf in _buffer.items()}
        _buffer.clear()
    _save_buffer(force=True)  # 已取走的内容不再留在磁盘缓冲里
    if not groups:
        return
    if not config.get_llm_key():
        logger.warning("[纪要] 未配置 LLM Key，跳过纪要生成")
        return
    today = time.strftime("%Y-%m-%d")
    data = _load_data()
    day_rec = data.setdefault(today, {})
    for gid, lines in groups.items():
        try:
            digest_text, facts = await _summarize_group(gid, lines)
            rec = day_rec.setdefault(str(gid), {})
            rec["digest"] = digest_text
            rec["facts"] = facts
            _save_data(data)
            logger.info(f"[纪要] 群{gid} 生成纪要 {len(digest_text)} 字，事实 {len(facts)} 条")
        except Exception as e:
            logger.error(f"[纪要] 群{gid} 纪要生成失败: {e}", exc_info=True)
    _prune(data)
    _save_data(data)


_digest_state = {"last_day": ""}


async def _digest_tick():
    """每天到点自动生成纪要（由统一定时任务每 30 秒调用一次）；每轮顺手落盘缓冲。"""
    try:
        now = time.localtime()
        today = time.strftime("%Y-%m-%d")
        if _digest_state["last_day"] != today and (now.tm_hour, now.tm_min) >= (config.DIGEST_HOUR, config.DIGEST_MINUTE):
            _digest_state["last_day"] = today
            await _summarize_today()
    finally:
        _save_buffer()


@get_driver().on_startup
async def _start_digest():
    global _task
    if config.DIGEST_ENABLED and _task is None:
        _load_buffer()
        logger.info(f"[纪要] 后台任务启动：每天 {config.DIGEST_HOUR:02d}:{config.DIGEST_MINUTE:02d} 生成群聊纪要")
        _task = scheduler.every("群聊纪要", 30, _digest_tick)


@get_driver().on_shutdown
async def _stop_digest():
    global _task
    if _task:
        await scheduler.stop("群聊纪要")
        _task = None
    if config.DIGEST_ENABLED:
        _save_buffer()
