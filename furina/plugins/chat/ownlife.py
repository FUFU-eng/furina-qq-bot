"""她自己的生活（参考 ai-companion-kit 的 own-life.js「定时静默唤醒」）。

群聊里只有「有人说话」才会叫醒她，所以角色卡里写了「你会自己写日记、刷 B 站」她也不会真去做。
这里每隔约 AI_CHAT_OWN_LIFE_HOURS（默认 2.5）小时给她一段「你自己的时间」：
- 静默：只在她自己的世界里发生，不往任何群发消息、不汇报；
- 不打扰：凌晨 3~8 点不叫；一天最多 6 次；23:30 后有一次「半夜」的邀请（一天一次）；
- 断电续得上：上次时间存在数据库里，重启不会一次补一堆；启动 90 秒后才第一次看；
- 她自己决定干什么：写日记 / 刷会儿 B 站（B 站开着才有这个选项）/ 发呆什么都不干；
- 干了什么会成为她「此刻的状态」，群里有人问「在干嘛」她答得出来（见 huoren.life_note）；
- 另外深夜（22~02 点，或 20 点后今天还没写）会想起来写日记，一天最多 2 次、至少隔 3 小时。
"""
from __future__ import annotations

import json
import random
import re
import time

from nonebot import get_driver, logger

from . import config, diary, llm
from ...core import db, roles, scheduler

NS = "ownlife"
JOB = "她自己的时间"
QUIET = (3, 8)
MAX_PER_DAY = 6
MIDNIGHT = (23, 30)
_tasks: dict = {}

PROMPT = """现在是你（{me}）的时间，没人找你。现在 {now}。
{state}
想干嘛干嘛，下面这些都行，也可以什么都不干：
{options}
不用回谁，也不用汇报。只输出 JSON：{{"do": "{keys}", "doing": "一句话：你现在在干嘛（第一人称、具体、口语，15 字以内，例如「窝在沙发上吃布丁发呆」）"}}"""

MIDNIGHT_PROMPT = """半夜了，没人打扰。现在 {now}。
{state}
想干点什么吗——下面这些都行，也可以就坐着什么都不干：
{options}
这段时间别去找谁说话，大家都睡了。只输出 JSON：{{"do": "{keys}", "doing": "一句话：你现在在干嘛（第一人称、具体，15 字以内）"}}"""


def _bili_ready() -> bool:
    try:
        from ...core import env
        from ...services.bilibili import store as bili_store
        return env.get_bool("BILI_ENABLED", True) and bili_store.logged_in() and not bili_store.halted()[0]
    except Exception:  # noqa: BLE001
        return False


def options(now: float) -> dict[str, str]:
    opts = {}
    if config.DIARY and diary.today_count(now) < max(0, config.DIARY_DAILY_MAX):
        opts["diary"] = "写日记：记一件今天的小事或此刻的感受（今天已经写了 %d 条）" % diary.today_count(now)
    if _bili_ready() and time.localtime(now).tm_hour >= 8:
        opts["bili"] = "刷会儿 B 站"
    opts["idle"] = "发呆 / 吃点东西 / 干点别的，什么都不用做"
    return opts


def _state() -> dict:
    return db.get(NS, "state") or {}


def _put_state(st: dict) -> None:
    db.put(NS, "state", st)


def current(now: float | None = None, within: float = 2 * 3600) -> dict | None:
    """她最近一次「自己的时间」干了什么（2 小时内有效）。"""
    ts = time.time() if now is None else now
    act = db.get(NS, "activity") or {}
    if act and ts - float(act.get("ts") or 0) <= within and act.get("doing"):
        return act
    return None


def due(now: float, rng: random.Random | None = None) -> str:
    """现在该不该给她一段自己的时间：返回 "normal" / "midnight" / ""。纯判断，方便测试。"""
    if not config.OWN_LIFE:
        return ""
    lt = time.localtime(now)
    if QUIET[0] <= lt.tm_hour < QUIET[1]:
        return ""
    st = _state()
    day = time.strftime("%Y-%m-%d", lt)
    count = int(st.get("count") or 0) if st.get("day") == day else 0
    if count >= MAX_PER_DAY:
        return ""
    if (lt.tm_hour, lt.tm_min) >= MIDNIGHT and st.get("midnight_day") != day:
        return "midnight"
    nxt = float(st.get("next") or 0)
    if not nxt:
        last = float(st.get("last") or 0)
        nxt = last + max(0.5, config.OWN_LIFE_HOURS) * 3600 if last else now
    return "normal" if now >= nxt else ""


def _mark(kind: str, now: float, rng: random.Random | None = None) -> None:
    r = rng or random
    lt = time.localtime(now)
    day = time.strftime("%Y-%m-%d", lt)
    st = _state()
    if st.get("day") != day:
        st = {"day": day, "count": 0, "midnight_day": st.get("midnight_day")}
    st["count"] = int(st.get("count") or 0) + 1
    st["last"] = now
    st["next"] = now + max(0.5, config.OWN_LIFE_HOURS) * 3600 * r.uniform(0.8, 1.2)
    if kind == "midnight":
        st["midnight_day"] = day
    _put_state(st)


def parse(raw: str, allowed) -> tuple[str, str]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return "idle", ""
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return "idle", ""
    do = str(data.get("do") or "idle").strip().lower()
    if do not in allowed:
        do = "idle"
    return do, str(data.get("doing") or "").strip()[:30]


def _state_text(now: float) -> str:
    bits = []
    try:
        from . import emotion
        bits.append(emotion.get_emotion_note())
    except Exception:  # noqa: BLE001
        pass
    prev = db.get(NS, "activity") or {}
    if prev.get("doing"):
        ago = int((now - float(prev.get("ts") or now)) // 60)
        bits.append(f"上次你自己待着的时候在「{prev['doing']}」（{ago} 分钟前）。")
    return "\n".join(b for b in bits if b)


async def live_once(kind: str = "normal", now: float | None = None) -> dict:
    ts = time.time() if now is None else now
    opts = options(ts)
    tmpl = MIDNIGHT_PROMPT if kind == "midnight" else PROMPT
    prompt = tmpl.format(me=roles.display_name(), now=time.strftime("%m月%d日 %H:%M", time.localtime(ts)),
                         state=_state_text(ts), options="\n".join(f"- {k}：{v}" for k, v in opts.items()),
                         keys="|".join(opts))
    try:
        raw = await llm.chat([{"role": "user", "content": prompt}], temperature=1.1)
    except llm.LLMError as e:
        logger.warning(f"[她自己的时间] 生成失败: {e}")
        return {}
    do, doing = parse(raw, opts)
    act = {"ts": ts, "do": do, "doing": doing or {"diary": "在写日记", "bili": "在刷 B 站", "idle": "在发呆"}[do]}
    if do == "diary":
        entry = await diary.write(ts, life=f"你现在：{act['doing']}")
        if entry:
            act["diary"] = entry.get("title")
        else:
            act["do"] = "idle"
    elif do == "bili":
        try:
            from ...services.bilibili import store as bili_store
            bili_store.put_request("surf_life")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[她自己的时间] 想刷 B 站但没成: {e}")
    db.put(NS, "activity", act)
    logger.info(f"[她自己的时间] {kind}：{act['do']} · {act['doing']}")
    return act


async def tick(now: float | None = None) -> None:
    ts = time.time() if now is None else now
    kind = due(ts)
    if kind:
        _mark(kind, ts)
        await live_once(kind, ts)
        return
    if config.DIARY and diary.nudge_due(ts):
        diary.mark_nudged(ts)
        await diary.write(ts, life="夜深了，你想起来今天还可以写点什么（想写就写，不想写也可以；写感受，不复述对话）")


@get_driver().on_startup
async def _start():
    if not _tasks:
        _tasks["job"] = scheduler.every(JOB, 300, tick, first_delay=90)


@get_driver().on_shutdown
async def _stop():
    if _tasks:
        await scheduler.stop(JOB)
        _tasks.clear()
