"""群里发语音：决定这句回复要不要用芙宁娜的声音发，合成并发送（失败就退回发文字）。

什么时候发语音（都要群开了「语音消息」权限、当前角色是芙宁娜、.env 配好了 FURINA_TTS_*）：
- 群友明确想听她的声音（「发条语音」「想听你说话」……）→ 这一句必定用语音，引擎没开会等它开好；
- 模型自己在回复最前面写了 [VOICE] → 用语音（她觉得这句用声音说更有味道）；
- 其他短句按 AI_CHAT_VOICE_PROBABILITY 随机用语音，但只在引擎已经开着时（不为随机的一句冷启动等半分钟）；
- 条数默认不限（AI_CHAT_VOICE_HOURLY_MAX=0）；太长、带链接、全是英文数字的句子不念。
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from collections import defaultdict, deque

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from . import config
from ...core import permissions, roles, scheduler, usage as daily_limits
from ...services import voice_tts

FEATURE = "voice"
_MARK_RE = re.compile(r"\s*[\[【](?:VOICE|voice|Voice|语音)(?:\s*[:：]\s*([^\]】]{1,6}))?[\]】]\s*")
_ASK_RE = re.compile(
    r"(?:发|来|说|录|整)(?:一|个|条|段|句|点)*语音|语音(?:说|讲|发|版|回|念)|用语音|"
    r"(?:想|要|让我)听(?:听)?你(?:的)?(?:声音|说话|讲话|唱|念)|听听你的声音|你的声音|开口说(?:句|个)?话|念(?:一下|出来|给我听)"
)
_sent: dict[int, deque] = defaultdict(deque)

VOICE_NOTE = ("【这次用语音回】对方想听你的声音，你这句话会被念成你本人的语音发出去："
              "像平时说话那样写口语，十几到四十个字，可以带「诶」「哼」「嘛」这类语气词；"
              "直接说那句话本身，别预告「给你发一句」「听好了」「这就发」，也别说「我在用语音」；"
              "不要表情符号、颜文字、括号动作、链接和任何标记。")
NO_VOICE_NOTE = ("【你这会儿发不了语音】对方想听你的声音，但你现在不方便开口（自己挑个像真人的理由：在外面、旁边有人、"
                 "嗓子有点哑、懒得录……别提内存、引擎、电脑这些）。就用文字自然地推掉或者说晚点再说，"
                 "别答应「这就发」「听好了」，也别假装已经发了语音、别问「听见了吧」。")

# 最近一次查到的「现在能不能开口」：hot = 引擎开着；cold = 没开但资源够、可以现开；no = 资源不够/在打游戏
_state = {"ts": 0.0, "group": None, "state": ""}


async def refresh_state(group_id) -> str:
    """群友要听语音时先查一次（查内存/显存要跑外部命令，放线程里）：结果给 voice_note() 用。"""
    st = ""
    try:
        if available(group_id):
            if await voice_tts.is_online():
                st = "hot"
            else:
                ok, _why = await asyncio.to_thread(voice_tts.can_start)
                st = "cold" if ok else "no"
    except Exception:  # noqa: BLE001
        st = ""
    _state.update(ts=time.time(), group=str(group_id), state=st)
    return st


def voice_note(group_id) -> str:
    """群友想听语音时这一轮该加哪条提示（发不了就让她自己推掉，别答应了又只发文字）。"""
    if not available(group_id):
        return ""
    st = _state["state"] if _state["group"] == str(group_id) and time.time() - _state["ts"] < 60 else ""
    return NO_VOICE_NOTE if st == "no" else VOICE_NOTE


def extract(reply: str) -> tuple[str, bool, str | None]:
    """剥掉 [VOICE] / [VOICE:傲娇] 标记，返回 (干净文字, 是否标了, 指定的语气 key 或 None)。"""
    if not reply:
        return reply, False, None
    emotion = None
    for m in _MARK_RE.finditer(reply):
        if m.group(1) and emotion is None:
            emotion = voice_tts.emotion_key(m.group(1))
    cleaned, n = _MARK_RE.subn("", reply)
    return cleaned.strip(), n > 0, emotion


def extract_marker(reply: str) -> tuple[str, bool]:
    """剥掉 [VOICE] 标记，返回 (干净文字, 是否标了)（旧接口）。"""
    cleaned, marked, _emo = extract(reply)
    return cleaned, marked


# ---------------- 用哪种语气说（四段参考音频：平静 / 神气 / 日常 / 傲娇） ----------------
_TSUN_RE = re.compile(r"哼|才不|才没|才、才|谁要|谁稀罕|别误会|勉强|笨蛋|不许|讨厌|随便你|原谅你|算你|哪有|我才|不是因为")
_PROUD_RE = re.compile(r"本芙宁娜|芙宁娜大人|本大明星|本导演|我宣布|不愧是我|那是自然|那当然|当然了|观众|各位|舞台|主角|看我的|交给我|"
                       r"哈哈哈|完美|一锤定音|精彩|厉害吧|谢幕")
_CALM_RE = re.compile(r"别难过|没事的|抱抱|辛苦了|晚安|早点睡|慢慢来|我在|别哭|不要紧|好好休息|其实|说真的|认真的")


def classify(text: str) -> str:
    """按这句话本身挑语气：傲娇 > 神气 > 平静（安慰/认真/长句）> 日常（默认）。纯本地正则，零 token。"""
    t = text or ""
    if _TSUN_RE.search(t):
        return "tsun"
    if _PROUD_RE.search(t) or t.count("！") + t.count("!") >= 2:
        return "proud"
    if _CALM_RE.search(t) or (len(t) >= 28 and "！" not in t and "!" not in t):
        return "calm"
    return "daily"


def asks_for_voice(text: str) -> bool:
    return bool(_ASK_RE.search(text or ""))


def available(group_id) -> bool:
    if not config.VOICE_ENABLED or not voice_tts.enabled():
        return False
    try:
        if not roles.is_furina():
            return False  # 声音是芙宁娜的：换了别的角色就不发语音
    except Exception:
        pass
    return permissions.allowed(str(group_id), FEATURE)


def _hour_count(group_id: int, now: float) -> int:
    q = _sent[int(group_id)]
    while q and now - q[0] > 3600:
        q.popleft()
    return len(q)


def suitable(reply: str, explicit: bool = False) -> bool:
    words = voice_tts.speakable(reply)
    limit = max(10, config.VOICE_MAX_CHARS) * (2 if explicit else 1)
    if not (2 <= len(words) <= limit):
        return False
    if re.search(r"https?://|www\.|\d{5,}", reply or ""):
        return False
    han = len(re.findall(r"[\u4e00-\u9fff]", words))
    return han >= max(2, len(words) // 3)


def decide(group_id, reply: str, current_text: str, marked: bool, *, engine_hot: bool,
           rng: random.Random | None = None, now: float | None = None) -> str:
    """返回 ""（发文字）/ "explicit"（群友要听）/ "marked"（模型标了）/ "random"（随机）。纯判断，方便测试。"""
    r = rng or random
    ts = time.time() if now is None else now
    if not reply or not available(group_id):
        return ""
    explicit = asks_for_voice(current_text)
    if not suitable(reply, explicit):
        return ""
    if config.VOICE_HOURLY_MAX > 0 and _hour_count(group_id, ts) >= config.VOICE_HOURLY_MAX:
        return ""  # 0 = 不限条数
    if explicit:
        return "explicit"
    if marked:
        return "marked" if engine_hot or r.random() < 0.5 else ""
    if engine_hot and r.random() < max(0.0, config.VOICE_PROBABILITY):
        return "random"
    return ""


async def maybe_send(sender, group_id, reply: str, current_text: str, marked: bool, *, trust_none: bool = False,
                     emotion: str | None = None) -> bool:
    """按规则决定并发送语音；真的发出去了返回 True，否则 False（调用方照常发文字）。
    trust_none：自定义发送函数（如戳一戳）本来就返回 None，这时 None 也算发出去了。"""
    try:
        hot = await voice_tts.is_online() if available(group_id) else False
        why = decide(group_id, reply, current_text, marked, engine_hot=hot)
        if not why:
            if marked and not hot and available(group_id):
                await voice_tts.warm_up()  # 这次来不及，先把引擎热起来
            return False
        emo = emotion or (classify(reply) if voice_tts.settings()["emotions"] else None)
        wav = await voice_tts.synthesize(reply, wait=90.0 if why != "random" else 5.0, emotion=emo)
        if not wav:
            return False
        result = await sender(Message(MessageSegment.record(wav)))
        if result is None and not trust_none:
            return False
        _sent[int(group_id)].append(time.time())
        daily_limits.track(FEATURE, group_id)
        logger.info(f"[语音] 群{group_id} 用语音回了一句（{why}，语气 {voice_tts.emotion_label(emo)}）：{reply[:40]}")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[语音] 群{group_id} 发语音失败，改发文字: {type(e).__name__}: {e}")
        return False


JOB_IDLE = "语音引擎内存检查"


async def _idle_tick() -> None:
    # 查内存/显存/游戏要跑 tasklist、nvidia-smi，放到线程里，别卡住消息处理
    if voice_tts.settings()["enabled"]:
        await asyncio.to_thread(voice_tts.resources, 0)
        voice_tts.idle_check()


@get_driver().on_startup
async def _start_idle_job():
    # 每 30 秒看一眼：内存紧张就关引擎，常开模式下宽裕了再开回来
    scheduler.every(JOB_IDLE, 30, _idle_tick, first_delay=20)


@get_driver().on_shutdown
async def _stop_engine():
    await scheduler.stop(JOB_IDLE)
    voice_tts.stop()  # 只关机器人自己拉起的引擎
