"""她主动去找人（参考 ai-companion-kit 的 PROACTIVE_OWNER 与小鲸鱼截图里的「追问」）。

两件事，都走「惦记常聊的人」那个群权限（miss），都在活跃时段内：

1. 追一句：TA 说「等我洗个澡」「先去吃饭」，过了 30~90 分钟还没回来说话 → 在那个群艾特 TA 随口问一句
   （「洗完没」「吃个饭吃这么久」），每件事只追一次（people.due_pending / mark_chased）。

2. 想找群主（AI_CHAT_OWNER_SEEK）：每 45~110 分钟想一次要不要去找他——
   她 20 分钟内刚说过话就不找；心里有记着的事 ×1.4，他 30 分钟内刚理过她 ×0.5，深夜 ×0.3；
   一天最多 AI_CHAT_OWNER_SEEK_DAILY_MAX 次；上次找他他没理，这次更轻、更短，连着两次没理今天就不找了；
   每次随机一个由头（想他了 / 无聊 / 刚干了件事想说 / 接他上次说的……），一天最多一次深夜问「睡没睡」；
   绝不说「我回来了」「醒了醒了」这种开场。
"""
from __future__ import annotations

import random
import time

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import MessageSegment

from . import config, dice, diary, emotion, humanize, llm, memory, ownlife, people
from ...core import db, live_groups, permissions, scheduler, usage as daily_limits
from ...core.botref import bind as bind_bot

NS = "seek"
FEATURE = "miss"
JOB = "主动找人"
_next: dict = {}
_tasks: dict = {}

ANGLES = [
    "就是突然有点想他了，但嘴上不承认",
    "有点无聊，想找他说两句",
    "你刚干了件小事（看你此刻的状态），想跟他说",
    "接他上次说过的事，问问后来怎么样",
    "随口吐槽一件你自己的事",
    "问他在干嘛（换个不像查岗的说法）",
    "刚想到一个跟他有关的小东西",
]

CHASE_PROMPT = """【你想起来一件事】{name} {ago}前说「{text}」，到现在还没回来说话。
你想像熟人那样在群里艾特 TA 随口问一句（比如「洗完没」「吃个饭吃这么久」「人呢」这种），别像在查岗，别肉麻。
只写一行，不超过十五个字；不要写 TA 的名字（会自动艾特 TA），不要括号动作，不要任何 [标记]。"""

SEEK_PROMPT = """【你想去找{name}了】现在 {now}。{gap}
{life}{diary}{pend}
群里最近的几句（从旧到新，"你"是你自己说的，别重复你说过的）：
{recent}
这次的由头：{angle}
{prev}{night}
在群里艾特他说一两句，像真人随手发的消息；可以说你自己的事，不一定要问他问题。
别说「我回来了」「醒了醒了」「在吗」「好久不见」这种开场；别提你在看记录、别提时间统计。
写一到两行，每行一条消息、每条不超过二十个字；不要写他的名字（会自动艾特他），不要括号动作，不要任何 [标记]。"""


def _ago(sec: float) -> str:
    m = max(1, int(sec // 60))
    return f"{m} 分钟" if m < 60 else f"{m // 60} 个多小时"


def _groups() -> set[str]:
    groups = {str(g) for g in permissions.enabled_groups(FEATURE)}
    live = live_groups.cached()
    if live:
        groups &= {str(g) for g in live}
    return groups


def _she_spoke_within(gid: str, seconds: float, now: float) -> bool:
    try:
        hist = memory.get_history(int(gid))
    except (TypeError, ValueError):
        return False
    return any(e.get("role") == "assistant" and now - float(e.get("ts") or 0) < seconds for e in hist[-30:])


async def _send(bot, gid: str, uid: str, text: str) -> bool:
    gid_int = int(gid)
    sender = humanize.guarded(lambda m, g=gid_int: bot.send_group_msg(group_id=g, message=m), gid_int)
    ok = False
    for idx, line in enumerate(text.split("\n")):
        await humanize.pause(humanize.typing_seconds(line) if idx == 0 else humanize.burst_gap(line))
        msg = (MessageSegment.at(int(uid)) + MessageSegment.text(" " + line)) if idx == 0 else MessageSegment.text(line)
        if await sender(msg) is None:
            break
        ok = True
    return ok


async def _compose(gid: str, self_id: str, prompt: str, temperature: float, max_lines: int) -> str:
    from . import proactive
    from .cleanup import drop_periods
    try:
        raw = await llm.chat([{"role": "system", "content": proactive.base_system(gid, self_id)},
                              {"role": "user", "content": prompt}], temperature=temperature)
    except llm.LLMError as e:
        logger.warning(f"[主动找人] 群{gid} 生成失败: {e}")
        return ""
    text = proactive._clean(raw, max_lines)
    if config.NO_PERIOD:
        text = drop_periods(text)
    return text if len(text.replace("\n", "")) >= 2 else ""


# ---------------- 1. 追一句 ----------------
async def chase_tick(bot, self_id: str, now: float) -> None:
    if not (config.PEOPLE and config.PENDING_CHASE):
        return
    groups = _groups()
    if not groups:
        return
    for uid, rec in (people.all_people() or {}).items():
        if not isinstance(rec, dict) or not rec.get("pending"):
            continue
        item = people.due_pending(uid, now)
        if not item:
            continue
        gid = str(item.get("gid") or "")
        people.mark_chased(uid, item, now)  # 不管发没发成都只追一次
        if gid not in groups or int(rec.get("talks") or 0) < 1 or _she_spoke_within(gid, 180, now):
            continue
        name = rec.get("name") or "TA"
        text = await _compose(gid, self_id, CHASE_PROMPT.format(name=name, ago=_ago(now - float(item.get("ts") or now)),
                                                                text=str(item.get("text") or "").replace("TA 说「", "").rstrip("」")),
                              1.1, 1)
        if text and await _send(bot, gid, uid, text):
            memory.add_message(int(gid), "assistant", f"（艾特了{name}）{text}")
            daily_limits.track(FEATURE, int(gid))
            logger.info(f"[主动找人] 群{gid} {name} 说要离开一会儿一直没回来，她追了一句：{text}")
            emotion.spawn_judge(gid, uid, name, "TA 说要离开一会儿，一直没回来", text, mode="talk")
        return  # 一次只追一个人


# ---------------- 2. 想找群主 ----------------
def _owner_state(now: float) -> dict:
    st = db.get(NS, "owner") or {}
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if st.get("day") != day:
        st = {"day": day, "count": 0, "ignored": int(st.get("ignored") or 0), "last": st.get("last"),
              "last_text": st.get("last_text"), "night_day": st.get("night_day")}
    # ignored = 连着找了他几次他都没再说过话；他一说话就清零
    rec = people.get(str(config.GROUP_OWNER_QQ or ""))
    if st.get("last") and rec and float(rec.get("last") or 0) > float(st["last"]):
        st["ignored"] = 0
    return st


def owner_chance(now: float, owner_rec: dict | None, st: dict, rng=None) -> float:
    """这一次想去找他的概率（纯函数，方便测试）。"""
    r = rng or random
    p = 0.35
    try:
        from . import emotion
        mood = emotion.get_mood()
        p *= 1.2 if mood >= 75 else (0.8 if mood <= 40 else 1.0)
    except Exception:  # noqa: BLE001
        pass
    p *= r.uniform(0.8, 1.2)  # 无聊程度
    if owner_rec:
        if [x for x in owner_rec.get("pending") or [] if not x.get("chased")]:
            p *= 1.4
        if now - float(owner_rec.get("last") or 0) < 30 * 60:
            p *= 0.5
    h = time.localtime(now).tm_hour
    if h >= 23 or h < 8:
        p *= 0.3
    if int(st.get("ignored") or 0) >= 1:
        p *= 0.6
    return min(1.0, p)


def _owner_group(owner_rec: dict | None, groups: set[str]) -> str:
    if not owner_rec:
        return ""
    seen = sorted(((float(ts or 0), g) for g, ts in (owner_rec.get("groups") or {}).items() if g in groups), reverse=True)
    return seen[0][1] if seen else ""


async def owner_tick(bot, self_id: str, now: float, rng=None) -> None:
    r = rng or random
    owner = str(config.GROUP_OWNER_QQ or "")
    if not config.OWNER_SEEK or not owner:
        return
    nxt = _next.get("owner")
    if nxt is None:
        _next["owner"] = now + r.uniform(20, 60) * 60
        return
    if now < nxt:
        return
    _next["owner"] = now + r.uniform(45, 110) * 60
    st = _owner_state(now)
    rec = people.get(owner)
    db.put(NS, "owner", st)
    cap = config.OWNER_SEEK_DAILY_MAX
    if (cap > 0 and int(st.get("count") or 0) >= cap) or int(st.get("ignored") or 0) >= 2:
        return
    groups = _groups()
    gid = _owner_group(rec, groups)
    if not gid or any(_she_spoke_within(g, 20 * 60, now) for g in groups):
        return
    since = now - float(st["last"]) if st.get("last") else None
    hit, why = dice.roll("owner", owner_chance(now, rec, st, r), now, since, r)  # 本地掷骰子，没中就不花一个 token
    logger.debug(f"[主动找人] 想不想找群主：{why}")
    if not hit:
        return
    name = (rec or {}).get("name") or "群主"
    h = time.localtime(now).tm_hour
    night = ""
    if (h >= 22 or h < 1) and st.get("night_day") != st["day"]:
        night = "现在挺晚了，可以顺口问他睡没睡（一天就问这一次）。"
        st["night_day"] = st["day"]
    act = ownlife.current(now)
    life = f"你此刻：{act['doing']}。\n" if act else ""
    dnote = "\n".join(f"- 《{e.get('title', '')}》{e.get('text', '')[:50]}" for e in diary.entries(2))
    dnote = f"你最近写的日记：\n{dnote}\n" if dnote else ""
    pend = [x.get("text", "") for x in (rec or {}).get("pending") or [] if not x.get("chased")][-2:]
    pend_txt = ("你心里还记着跟他有关的事：" + "；".join(pend) + "\n") if pend else ""
    gap = f"他上次说话是{_ago(now - float(rec['last']))}前。" if rec and rec.get("last") else ""
    prev = ""
    if int(st.get("ignored") or 0) >= 1 and st.get("last_text"):
        prev = f"你上次找他说「{st['last_text']}」，他还没理你：这次更轻、更短一点，或者装作不在意随口一句。\n"
    try:
        hist = memory.get_history(int(gid))[-8:]
    except (TypeError, ValueError):
        hist = []
    recent = "\n".join(("你：" if e.get("role") == "assistant" else "") + str(e.get("content") or "")[:60] for e in hist) or "（没什么）"
    prompt = SEEK_PROMPT.format(name=name, now=time.strftime("%H:%M", time.localtime(now)), gap=gap, life=life, diary=dnote,
                                pend=pend_txt, recent=recent, angle=r.choice(ANGLES), prev=prev, night=night)
    text = await _compose(gid, self_id, prompt, 1.35, 2)
    if not text or not await _send(bot, gid, owner, text):
        return
    memory.add_message(int(gid), "assistant", f"（艾特了{name}）{text}")
    st.update(count=int(st.get("count") or 0) + 1, last=now, last_text=text.replace("\n", " ")[:40],
              ignored=int(st.get("ignored") or 0) + 1)
    db.put(NS, "owner", st)
    daily_limits.track(FEATURE, int(gid))
    logger.info(f"[主动找人] 群{gid} 她去找{name}了：{text}")
    emotion.spawn_judge(gid, owner, name, "想找他说说话", text, mode="talk")  # 她自己主动去找人：判她自己的心意


async def tick(now: float | None = None) -> None:
    from . import proactive
    ts = time.time() if now is None else now
    if not proactive.in_active_hours(ts) and not (time.localtime(ts).tm_hour >= 22 or time.localtime(ts).tm_hour < 1):
        return
    bot, sid = proactive._pick_bot()
    if bot is None:
        return
    bind_bot(self_id=sid)
    try:
        await chase_tick(bot, sid, ts)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[主动找人] 追一句失败: {e}")
    if proactive.in_active_hours(ts) or time.localtime(ts).tm_hour >= 22:
        try:
            await owner_tick(bot, sid, ts)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[主动找人] 找群主失败: {e}")


@get_driver().on_startup
async def _start():
    if not _tasks:
        _tasks["job"] = scheduler.every(JOB, 120, tick, first_delay=240)


@get_driver().on_shutdown
async def _stop():
    if _tasks:
        await scheduler.stop(JOB)
        _tasks.clear()
