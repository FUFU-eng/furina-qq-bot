"""表情回应（QQ「贴表情」）：像真人一样偶尔给群消息点个赞、贴个笑哭，不必每次都打字。

用 NapCat 扩展接口 ``set_msg_emoji_like(message_id, emoji_id)``；emoji_id 是 QQ 系统表情 ID
（如 76=赞、66=爱心）或 Unicode 码点（如 128077=👍）。接口失败只记日志，调用方照常走文字回复。

两种用法：
- 普通群消息：按关键词挑一个表情，低概率顺手贴一下（每群有冷却，不会刷）；
- 被艾特但只是「谢谢 / 好的 / 晚安 / 哈哈」这类收尾话：较高概率只贴表情、不再回一句文字。
"""
from __future__ import annotations

import random
import re
import time

from nonebot import logger

from . import config, memory, selfacts

# (关键词正则, 候选表情 ID)
_RULES: list[tuple[re.Pattern, tuple[int, ...]]] = [
    (re.compile(r"生日快乐|恭喜|上岸|通过了|考过了|出货|欧皇|成功了|拿到offer", re.I), (320, 144, 127881)),
    (re.compile(r"晚安|睡了|睡觉去|去睡"), (75,)),
    (re.compile(r"早安|早上好|^早[呀啊安~～!！]*$|起床了"), (74,)),
    (re.compile(r"[哈嘿]{3,}|笑死|绷不住|xswl|233|草{1,}$|乐死", re.I), (182, 178, 128514)),
    (re.compile(r"谢谢|谢啦|感谢|多谢|辛苦了|thx|thanks", re.I), (66, 76, 128077)),
    (re.compile(r"牛|厉害|好强|太强|nb|666|绝了|佩服|大佬", re.I), (76, 318, 99, 128077)),
    (re.compile(r"好的|收到|^ok$|^好[滴哒的嘞]?$|^行$|嗯嗯|知道了|明白", re.I), (124, 76)),
    (re.compile(r"爱你|贴贴|么么|喜欢你|比心"), (66, 319)),
    (re.compile(r"呜呜|难过|想哭|好累|emo|破防|心累|不开心", re.I), (49, 66)),
    (re.compile(r"[?？]{2,}|啥意思|什么意思|看不懂"), (32,)),
    (re.compile(r"吃瓜|八卦|有瓜|大瓜"), (271,)),
]
_IMAGE_ONLY = (76, 178, 66, 182)

# 被艾特时可以「只贴表情不说话」的收尾话（整句就是这些）
_ACK_RE = re.compile(
    r"^(?:谢谢|谢啦|谢了|多谢|感谢|thx|thanks|好的|好滴|好哒|好嘞|好吧|收到|ok|okk|行|嗯+|嗯嗯|知道了|明白了?|"
    r"晚安|晚安啦|睡了|[哈嘿]{2,}|笑死|草|6+|牛|辛苦了|爱你|贴贴|么么)"
    r"(?:你|啦|啊|呀|哦|喔|~|～|!|！|。|\.)*$",
    re.I,
)

_last_react: dict[int, float] = {}
# 表情 ID -> 她自己能看懂的名字（记进「你做过的小动作」和群聊记录）
EMOJI_NAMES = {
    76: "赞", 66: "爱心", 74: "太阳（早安）", 75: "月亮（晚安）", 182: "笑哭", 178: "斜眼笑", 320: "庆祝", 144: "喝彩",
    318: "崇拜", 99: "鼓掌", 124: "OK", 319: "比心", 49: "拥抱", 32: "疑问", 271: "吃瓜",
    128514: "😂", 128077: "👍", 127881: "🎉",
}
_last_emoji: dict[tuple[int, str], str] = {}


def emoji_name(emoji_id) -> str:
    try:
        return EMOJI_NAMES.get(int(emoji_id)) or f"QQ表情{emoji_id}"
    except (TypeError, ValueError):
        return str(emoji_id)


def _target(group_id, message_id) -> tuple[str, str, str]:
    """从群聊记忆里找被贴表情的那条消息：(说话人, 内容, QQ号)。找不到返回空串。"""
    for e in reversed(memory.get_history(int(group_id))[-60:]):
        if str(e.get("mid") or "") == str(message_id) and e.get("role") == "user":
            content = str(e.get("content") or "")
            name = e.get("name") or content.partition("：")[0]
            body = content[len(name) + 1:] if name and content.startswith(name + "：") else content
            return name, body, str(e.get("uid") or "")
    return "", "", ""


def remember(group_id, message_id, who: str = "", text: str = "", uid: str = "") -> None:
    """把「我给这条贴了什么表情」记进群聊记录和她的小动作清单（贴成功之后调用；消息后入记忆时可再调一次补标记）。"""
    if group_id is None or not message_id:
        return
    name = _last_emoji.get((int(group_id), str(message_id)))
    if not name:
        return
    memory.annotate(int(group_id), message_id, "reacted", name)
    t_who, t_text, t_uid = _target(group_id, message_id)
    who, text, uid = t_who or who, t_text or text, t_uid or uid
    snippet = (text or "").replace("\n", " ").strip()
    snippet = f"的「{snippet[:24]}」" if snippet else "的消息"
    selfacts.record(group_id, "react", f"给{who or '群友'}{snippet}贴了个「{name}」表情", target_uid=uid, mid=message_id)


def _sender_info(event) -> tuple[str, str]:
    sender = getattr(event, "sender", None)
    name = (getattr(sender, "card", "") or getattr(sender, "nickname", "")) if sender is not None else ""
    return str(name or ""), str(getattr(event, "user_id", "") or "")


def pick_emoji(text: str, has_image: bool = False, rng: random.Random | None = None) -> int | None:
    """按消息内容挑一个合适的表情 ID；挑不出来返回 None（不硬贴）。"""
    r = rng or random
    t = (text or "").strip()
    for pat, ids in _RULES:
        if pat.search(t):
            return r.choice(ids)
    if not t and has_image:
        return r.choice(_IMAGE_ONLY)
    return None


def is_ack(text: str) -> bool:
    return bool(_ACK_RE.match((text or "").strip()))


async def react(bot, message_id, emoji_id: int, group_id=None, who: str = "", text: str = "", uid: str = "") -> bool:
    """给一条消息贴表情，成功返回 True（并记下来：她之后知道是自己贴的）。"""
    if not message_id or emoji_id is None:
        return False
    try:
        await bot.call_api("set_msg_emoji_like", message_id=int(message_id), emoji_id=str(emoji_id))
    except Exception as e:  # noqa: BLE001  协议端不支持/表情 ID 不可用：只记日志
        logger.warning(f"[AI聊天] 群{group_id} 贴表情失败（emoji={emoji_id}）: {e}")
        return False
    if group_id is not None:
        _last_react[int(group_id)] = time.time()
        _last_emoji[(int(group_id), str(message_id))] = emoji_name(emoji_id)
        while len(_last_emoji) > 500:
            _last_emoji.pop(next(iter(_last_emoji)))
        try:
            remember(group_id, message_id, who, text, uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[AI聊天] 记录自己贴的表情失败: {e}")
    logger.info(f"[AI聊天] 群{group_id} 给消息 {message_id} 贴了表情 {emoji_id}（{emoji_name(emoji_id)}）")
    return True


def _cooled(group_id) -> bool:
    return time.time() - _last_react.get(int(group_id), 0.0) >= max(0.0, config.REACTION_COOLDOWN)


async def maybe_react(bot, event, text: str, has_image: bool, rng: random.Random | None = None) -> bool:
    """普通群消息：低概率顺手贴个表情（每群冷却）。"""
    r = rng or random
    if not config.REACTION_ENABLED or config.REACTION_PROBABILITY <= 0:
        return False
    group_id = getattr(event, "group_id", None)
    if group_id is None or not _cooled(group_id):
        return False
    emoji = pick_emoji(text, has_image, r)
    if emoji is None or r.random() >= config.REACTION_PROBABILITY:
        return False
    who, uid = _sender_info(event)
    return await react(bot, getattr(event, "message_id", None), emoji, group_id, who, text, uid)


async def react_instead(bot, event, text: str, rng: random.Random | None = None) -> bool:
    """被艾特但只是收尾话：按概率只贴表情、不回文字。贴成功返回 True（调用方就不再回复）。"""
    r = rng or random
    if not config.REACTION_ENABLED or config.REACTION_INSTEAD_PROBABILITY <= 0:
        return False
    if not is_ack(text) or r.random() >= config.REACTION_INSTEAD_PROBABILITY:
        return False
    emoji = pick_emoji(text, False, r) or r.choice((76, 66, 124))
    who, uid = _sender_info(event)
    return await react(bot, getattr(event, "message_id", None), emoji, getattr(event, "group_id", None), who, text, uid)


def last_emoji_name(group_id, message_id) -> str:
    return _last_emoji.get((int(group_id), str(message_id)), "") if group_id is not None and message_id else ""
