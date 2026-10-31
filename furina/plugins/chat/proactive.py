"""冷场主动开话题：群里安静太久时，像群友一样主动冒个泡（每群按「功能权限 → 冷场主动开话题」开关，默认关）。

条件（全部满足才发，每分钟检查一次）：
- 群开了 proactive 权限，且机器人当前在群里；
- 在活跃时段内（AI_CHAT_PROACTIVE_HOURS，如 10-23）；
- 最后一条消息不是机器人自己说的，已经安静了 AI_CHAT_PROACTIVE_IDLE_MINUTES 分钟，
  但又没安静到 AI_CHAT_PROACTIVE_MAX_IDLE_HOURS 小时以上（那时上下文早就凉了）；
- 最近 24 小时群里至少有 AI_CHAT_PROACTIVE_MIN_MESSAGES 条群友消息（死群不去自言自语）；
- 今天这个群主动发言还没到 AI_CHAT_PROACTIVE_DAILY_MAX 次，距离上次主动发言至少 AI_CHAT_PROACTIVE_GAP_MINUTES 分钟；
- 满足后每分钟只有一定概率真的开口，避免整点准时冒出来像定时任务。
"""
from __future__ import annotations

import random
import re
import time

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import MessageSegment

from . import actions, cleanup, config, dice, emotion, huoren, learning, humanize, llm, memory, voice
from .cleanup import _scrub_fixed_template, _strip_action_parentheses, _strip_context_markers, _too_similar, _trim_reply
from ...core import brain, db, live_groups, permissions, scheduler, usage as daily_limits
from ...core.botref import bind as bind_bot

FEATURE = "proactive"
JOB = "冷场开话题"
_NS = "proactive"
FIRE_CHANCE = 0.2  # 条件满足后每分钟开口的概率（平均晚几分钟，像人一样不准点）

_task = None

PROMPT = """【现在的情况】群里已经安静了{idle}，没人说话。你想像普通群友一样主动冒个泡，打破一下安静。
可以：接着刚才没聊完的话题随口补一句；分享一件你自己今天的小事、刚刷到的东西或小感受；偶尔问大家一个轻松的问题。
最好用陈述的口气说自己的事（「刚吃完一大碗螺蛳粉」），比直接提问自然。
要求：写一到两行，每行就是一条消息、十来个字到二三十字，像随手发的消息。
想找某位群友可以自主艾特，不限主人：用 [AT:昵称或QQ:要对TA说的话]，会实际艾特；说现在去找谁就要一起带上，别只嘴上说，也别猜不清的「他/她」是谁。
不要说「大家好」「怎么没人说话」「好安静啊」「冷场了」这类话，不要用括号写动作，除了真实艾特不要输出其他 [标记] 或表情包标记。"""


def _hours_window() -> tuple[int, int]:
    raw = str(config.PROACTIVE_HOURS or "").strip()
    m = re.match(r"^\s*(\d{1,2})\s*[-~～到至]\s*(\d{1,2})\s*$", raw)
    if not m:
        return 10, 23
    a, b = int(m.group(1)) % 24, int(m.group(2)) % 25
    return a, b


def in_active_hours(now: float | None = None) -> bool:
    h = time.localtime(now or time.time()).tm_hour
    a, b = _hours_window()
    if a == b:
        return True
    return a <= h < b if a < b else (h >= a or h < b)


def _state(gid: str) -> dict:
    return db.get(_NS, gid) or {}


def _today(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def should_speak(gid: str, history: list, now: float, rng: random.Random | None = None) -> tuple[bool, str]:
    """返回 (是否开口, 原因/空闲描述)。纯判断，不发消息，方便测试。"""
    r = rng or random
    if not in_active_hours(now):
        return False, "不在活跃时段"
    if not history:
        return False, "没有上下文"
    last = history[-1]
    if last.get("role") == "assistant":
        return False, "最后一句是自己说的"
    try:
        last_ts = float(last.get("ts") or 0)
    except (TypeError, ValueError):
        last_ts = 0
    if not last_ts:
        return False, "没有时间戳"
    idle = now - last_ts
    if idle < max(1, config.PROACTIVE_IDLE_MINUTES) * 60:
        return False, "还不够安静"
    if idle > max(1, config.PROACTIVE_MAX_IDLE_HOURS) * 3600:
        return False, "安静太久了，上下文已凉"
    recent_users = sum(1 for m in history if m.get("role") == "user"
                       and now - float(m.get("ts") or 0) <= 86400)
    if recent_users < max(0, config.PROACTIVE_MIN_MESSAGES):
        return False, "群里最近不活跃"
    st = _state(gid)
    if st.get("day") == _today(now) and int(st.get("count") or 0) >= max(0, config.PROACTIVE_DAILY_MAX):
        return False, "今天次数已用完"
    if now - float(st.get("last_ts") or 0) < max(0, config.PROACTIVE_GAP_MINUTES) * 60:
        return False, "离上次主动发言太近"
    if r.random() >= FIRE_CHANCE * dice.peak_factor(now):  # 工作日高峰少开口（主动搭话的峰谷因子），本地掷零 token
        return False, "这一分钟先不开口"
    minutes = int(idle // 60)
    return True, (f"{minutes} 分钟" if minutes < 60 else f"{minutes // 60} 个多小时")


def _transcript(history: list, limit: int = 20) -> str:
    lines = []
    for m in history[-limit:]:
        content = (m.get("content") or "").replace("\n", " ")[:80]
        if not content:
            continue
        lines.append(("你：" + content) if m.get("role") == "assistant" else content)
    return "\n".join(lines)


def _clean_line(text: str) -> str:
    text = (text or "").strip().strip("\"“”")
    text = re.sub(r"<<?STICKER:\d+>>?", "", text)
    text, act = actions.parse(text, 0)
    if not text.strip() and act.at_content:
        text = act.at_content  # 模型自己写了 [AT:某人:内容]：艾特由调用方加，内容留下
    text = _strip_context_markers(text)
    text = _strip_action_parentheses(text)
    text = _scrub_fixed_template(text)
    text = re.sub(r"\[@?[^\]]{0,20}\]|@\S+", "", text).strip()
    text = re.sub(r"\[[A-Z_]+(?::[^\]\n]*)?\]?", "", text).strip()  # 没写完整的 [AT:… / [VOICE 之类
    if cleanup._JUNK_LINE.fullmatch(text or " "):
        return ""  # 只剩「[」「]」这种残渣
    text = text.strip()
    text = re.sub(r"[~～]+$", "", text).strip()
    cap = config.AI_CHAT_MAX_REPLY_CHARS
    text = _trim_reply(text, min(60, cap) if cap > 0 else 60)
    return cleanup.drop_periods(text) if config.NO_PERIOD else text


def _clean(text: str, max_lines: int = 1) -> str:
    """清洗模型输出；最多保留 max_lines 行（每行发成一条消息）。"""
    # 先处理整段：占位串可能被模型换行，不能让残片占掉正常消息的行数。
    lines = [_clean_line(x) for x in cleanup.strip_qq_placeholders(text).split("\n")]
    lines = [x for x in lines if x]
    return "\n".join(lines[:max(1, max_lines)])


def base_system(gid: str, self_id: str, query: str | None = None) -> str:
    """主动说话时用的人设 + 长期记忆 + 此刻生活（冷场开话题、惦记群友共用）。"""
    from .role_persona import build_persona  # 延迟导入：避免循环

    system = build_persona(self_id)
    summary = learning.get_group_summary(int(gid), query=query)
    if summary:
        system += "\n\n【你的长期记忆】" + summary
    if config.HUOREN_V3:
        for extra in (huoren.life_note(), huoren.bili_note()):
            if extra:
                system += "\n\n" + extra
    system = (system.replace("{CURRENT_DATE}", time.strftime("%Y-%m-%d"))
              .replace("{GROUP_OWNER_QQ}", config.GROUP_OWNER_QQ).replace("{SPECIAL_QQ}", config.SPECIAL_QQ))
    return system


async def compose(gid: str, self_id: str, history: list, idle_text: str, *, with_at: bool = False) -> str | tuple[str, str]:
    """生成一两句开话题的话；with_at 同时返回真实艾特目标，失败返回空串。"""
    if humanize.skip_if_limited(gid, "冷场开话题"):
        return ""
    system = base_system(gid, self_id, query=_transcript(history, 8))
    recent_self = memory.recent_assistant_messages(int(gid), 6)
    habit = voice.habit_note(recent_self)
    if config.DEAI:
        try:
            from . import kouchan
            kc = kouchan.note()
            habit = (habit + "\n" + kc).strip() if kc else habit
        except Exception:  # noqa: BLE001
            pass
    user = ("【最近的群聊记录（从旧到新，“你”是你自己说的）】\n" + _transcript(history)
            + "\n\n" + PROMPT.format(idle=idle_text) + ("\n" + habit if habit else ""))
    try:
        raw = await llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    except llm.LLMError as e:
        logger.warning(f"[冷场开话题] 群{gid} 生成失败: {e}")
        return ""
    at_target = ""
    if with_at:
        raw, act = actions.parse(raw, gid)
        at_target = act.at_target
        raw = "\n".join(x for x in (raw, act.at_content) if x)
    text = voice.guard_self_titles(_clean(raw, 2), recent_self)
    if len(text) < 2:
        return ""
    if config.DEAI:
        from . import kouchan
        if kouchan.hard_template(text):
            logger.info(f"[冷场开话题] 群{gid} 套了先夸后挑刺的老句式，这次不发: {text}")
            return ""
    for old in (_state(gid).get("recent") or []) + recent_self:
        if _too_similar(text, old, 0.7):
            logger.info(f"[冷场开话题] 群{gid} 生成的话和之前太像，这次不发: {text}")
            return ""
    return (text, at_target) if with_at else text


def _pick_bot():
    from nonebot import get_bots

    bots = get_bots()
    if not bots:
        return None, ""
    want = str(config.BOT_QQ or "")
    if want and want in bots:
        return bots[want], want
    sid, bot = next(iter(bots.items()))
    return bot, str(sid)


def _remember(gid: str, text: str, now: float) -> None:
    def _u(st):
        st = dict(st or {})
        if st.get("day") != _today(now):
            st["day"], st["count"] = _today(now), 0
        st["count"] = int(st.get("count") or 0) + 1
        st["last_ts"] = now
        st["recent"] = ((st.get("recent") or []) + [text])[-8:]
        return st
    db.update(_NS, gid, _u)


async def tick(now: float | None = None) -> None:
    if not config.PROACTIVE_ENABLED:
        return
    groups = set(permissions.enabled_groups(FEATURE))
    if not groups:
        return
    live = live_groups.cached()
    if live:
        groups &= live
    bot, sid = _pick_bot()
    if bot is None:
        return
    bind_bot(self_id=sid)
    ts = time.time() if now is None else now
    for gid in sorted(groups):
        if not brain.proactive_allowed(gid):  # 聊天 / Agent 模式不主动冒泡
            continue
        try:
            gid_int = int(gid)
        except (TypeError, ValueError):
            continue
        history = memory.get_history(gid_int)
        ok, why = should_speak(gid, history, ts)
        if not ok:
            continue
        proposal = await compose(gid, sid, history, why, with_at=True)
        text, at_target = proposal if isinstance(proposal, tuple) else (proposal, "")
        # 生成期间有人说话了：不用冒泡了
        latest = memory.get_history(gid_int)
        if not text or (latest and latest[-1] is not history[-1]):
            continue
        from . import kernel

        routed = bool(at_target or kernel._contact_now_target(text))
        result = None
        if routed:
            # 冷场找人也走同一条「目标校验 + 真实艾特 + 正文」路径，不能只留下台词。
            ctx = kernel.RunCtx("group", sid, gid_int, reason="idle")
            delivered = await kernel._say(ctx, text.split("\n"), at=at_target)
            result = delivered if delivered.get("sent") else None
        else:
            sender = humanize.guarded(lambda m, g=gid_int: bot.send_group_msg(group_id=g, message=m), gid_int)
            for idx, line in enumerate(text.split("\n")):
                if idx:
                    await humanize.pause(humanize.burst_gap(line))
                sent = await sender(MessageSegment.text(line))
                if sent is None:
                    break
                result = sent
        if result is None:
            continue
        if not routed:  # 找人路径已经按实际发送内容记过了
            memory.add_message(gid_int, "assistant", text)
        _remember(gid, text, ts)
        daily_limits.track(FEATURE, gid_int)
        logger.info(f"[冷场开话题] 群{gid} 安静了{why}，主动说：{text}")
        emotion.spawn_judge(gid, None, "", f"群里安静了{why}", text, mode="talk")


@get_driver().on_startup
async def _start():
    global _task
    if _task is None:
        _task = scheduler.every(JOB, 60, tick, first_delay=120)


@get_driver().on_shutdown
async def _stop():
    global _task
    if _task is not None:
        await scheduler.stop(JOB)
        _task = None
