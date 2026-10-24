"""机器人互聊收尾：群里另一个 AI 机器人和我们互相艾特时，不再无限接龙。

两个机器人都是「被艾特必回、回复必带艾特」，一旦搭上话就会你一句我一句停不下来。
这里按「群 + 本机器人号 + 对方号」记一条对话链：

1. 前几轮正常聊；从第 2 轮起提示模型回复简短、别反问、别抛新话题，让话题自然收拢；
2. 到第 AI_CHAT_PEER_MAX_ROUNDS 轮，让模型顺着对方的话自然收个尾，这一句不艾特、不引用、
   不戳一戳、不执行动作标记（对方机器人没有被点名，通常就不会再接）；
3. 收尾后 AI_CHAT_PEER_COOLDOWN 秒内，对方再艾特也不回、也不随机接 TA 的话；
4. 两轮之间隔了 AI_CHAT_PEER_RESET_SECONDS 秒以上，算新话题，重新计数。

哪些号算机器人：.env 的 AI_CHAT_PEER_BOTS（逗号分隔 QQ）+ 面板里登记的本项目其他机器人号
（多号同时在线时互相也会聊起来）。真人群友完全不受影响。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import config
from ...core import db

HUMAN, NORMAL, WRAPUP, SILENT = "human", "normal", "wrapup", "silent"


@dataclass
class _Chain:
    rounds: int = 0
    last: float = 0.0
    ended_at: float = 0.0


_chains: dict[tuple[int, str, str], _Chain] = {}


def reset() -> None:
    """清空所有对话链（测试用）。"""
    _chains.clear()


_QQ_RE = re.compile(r"\d{5,12}")


def parse_qq_list(items) -> set[str]:
    """设置里每一项可以带备注：「200000005」「200000005(昔涟)」「昔涟:200000005」都认。"""
    out: set[str] = set()
    for item in items or ():
        out.update(_QQ_RE.findall(str(item)))
    return out


def peer_bots(self_id: str = "") -> set[str]:
    """.env 的 AI_CHAT_PEER_BOTS 每条消息现读：面板里改了机器人号，下一条消息就按新的来，不用重启。"""
    ids = parse_qq_list(config.PEER_BOTS)
    try:
        profiles = db.get_cached("persona", "profiles")
        for p in (profiles if isinstance(profiles, list) else []):
            if isinstance(p, dict) and p.get("qq"):
                ids.add(str(p["qq"]).strip())
    except Exception:
        pass
    return ids - {str(self_id or ""), str(config.BOT_QQ or ""), ""}


def is_peer(user_id, self_id: str = "") -> bool:
    return bool(config.PEER_END_ENABLED) and str(user_id) in peer_bots(self_id)


def _key(group_id, self_id, user_id) -> tuple[int, str, str]:
    return int(group_id), str(self_id or ""), str(user_id)


def is_silent(group_id, self_id, user_id, now: float | None = None) -> bool:
    """已经和这个机器人收过尾、还在冷却期内。"""
    if not is_peer(user_id, self_id):
        return False
    c = _chains.get(_key(group_id, self_id, user_id))
    now = time.monotonic() if now is None else now
    return bool(c and c.ended_at and now - c.ended_at < max(0.0, config.PEER_COOLDOWN))


def begin(group_id, self_id, user_id, now: float | None = None) -> tuple[str, int]:
    """对方艾特了我们：返回 (状态, 第几轮)。状态见模块常量。

    WRAPUP 在决定的那一刻就记为已收尾，生成期间对方再发的消息直接按 SILENT 处理。
    """
    if not is_peer(user_id, self_id):
        return HUMAN, 0
    now = time.monotonic() if now is None else now
    k = _key(group_id, self_id, user_id)
    c = _chains.setdefault(k, _Chain())
    if c.ended_at and now - c.ended_at < max(0.0, config.PEER_COOLDOWN):
        return SILENT, c.rounds
    if c.ended_at or (c.last and now - c.last > max(0.0, config.PEER_RESET_SECONDS)):
        c.rounds, c.ended_at = 0, 0.0
    c.rounds += 1
    c.last = now
    if c.rounds >= max(1, config.PEER_MAX_ROUNDS):
        c.ended_at = now
        return WRAPUP, c.rounds
    return NORMAL, c.rounds


CONVERGE_NOTE = """【和机器人闲聊（优先级高）】
{name} 也是群里的 AI 机器人，你们已经来回聊了几句。这次回复简短一点，顺着话头应一句就好：
不要反问、不要用问句结尾、不要抛新话题，让这段闲聊自然地慢慢收住。"""

WRAPUP_NOTE = """【自然收尾（优先级最高）】
你和 {name}（群里另一个 AI 机器人）已经你来我往聊了好几轮，这是你这段闲聊的最后一句。
顺着对方刚才那句话自然地收个尾：可以随口应一句，再表示先去忙别的、就聊到这、回头再说之类，
像真人结束闲聊那样随意。不要提问、不要抛新话题、不要留话头、不要艾特任何人，
也不要说「结束话题」「不聊了」这种生硬的字眼。一句话，越短越好。"""


def reply_options(state: str, name: str) -> dict:
    """按状态给 _reply 的额外参数（真人或第 1 轮返回空 dict，行为与原来完全一致）。"""
    name = (name or "对方").strip() or "对方"
    if state == WRAPUP:
        return {
            "context_note": WRAPUP_NOTE.format(name=name),
            "mention": False,       # 不艾特、不引用：对方机器人没被点名就不会再接
            "allow_poke": False,
            "allow_actions": False,
        }
    return {}


def converge_options(state: str, rounds: int, name: str) -> dict:
    if state == NORMAL and rounds >= 2:
        return {"context_note": CONVERGE_NOTE.format(name=(name or "对方").strip() or "对方"),
                "allow_poke": False}
    if state == NORMAL:
        return {"allow_poke": False}  # 戳一戳也可能触发对方回复，和机器人聊天时不戳
    return {}


def options(state: str, rounds: int, name: str) -> dict:
    return reply_options(state, name) or converge_options(state, rounds, name)
