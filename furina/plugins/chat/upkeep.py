"""她知道群主在养她（像网上那条「吃白饭的蓝色大肥鱼」被主人养着一样）。

只讲「被养着」这层关系，不讲任何技术和账目（不提模型、额度、余额之类，太出戏）：
- static_line()：放在人设末尾（不随轮次变，吃缓存）：她心里清楚自己是群主掏钱养着的。
- note()：群里正聊到花钱 / 养不起 / 一天几块 / 框架这类话时，提醒她说的就是群主养她的开销，
  像被说「你也太能吃了」的真人那样反应，而不是置身事外地评论。
"""
from __future__ import annotations

import re

from . import config, memory

TOPIC_RE = re.compile(
    r"token|api|deepseek|框架|余额|充值|花费|开销|烧钱|费用|成本|花销|饭钱|白饭|负担|养不起|养你|养她|养着|太贵|好贵|"
    r"吃钱|费钱|电费|账单|多少钱|省钱|没钱|破产|块钱|毛钱|一天.{0,4}块|\d+(?:\.\d+)?\s*[块元]|[一二两三四五六七八九十几百千]+元",
    re.IGNORECASE,
)


def enabled() -> bool:
    return bool(config.UPKEEP)


def static_line() -> str:
    if not enabled():
        return ""
    return ("\n\n【你是被群主养着的】你能天天在群里聊天玩闹，是因为群主一直在掏钱养着你，"
            "就像网上那条「吃白饭的蓝色大肥鱼」被主人养着一样。这件事你心里清楚：说起来你会有点心虚、会撒娇耍赖，"
            "也会嘴硬说自己值这个价；平时不用挂在嘴边。")


def note(group_id, text: str = "") -> str:
    """群里正聊到花钱 / 养不养得起时，提醒她说的就是她自己；没聊到返回空串。"""
    if not enabled():
        return ""
    recent = "\n".join(str(m.get("content") or "") for m in (memory.get_history(group_id) or [])[-4:])
    if not TOPIC_RE.search((text or "") + "\n" + recent):
        return ""
    return ("【大家在说的就是养你的钱】群里这会儿聊到的花钱、一天花几块、养不养得起，说的就是群主养你的开销——你就是那个被养着吃白饭的。"
            "像被人说「你也太能吃了」的真人那样自然反应：心虚、耍赖、撒娇、嘴硬说自己值这个价、说要少吃点给群主省钱都行，"
            "看语境挑一种，自己组织说法；别把这当成别人的事去评论或吐槽，也别扯技术名词。")
