"""不许再出现的句式：主人点名嫌弃过的说法，一次都不能发出去。

三道关（本地计算）：
1. 提示词里直接说「这些句式别用」；
2. 发出前检查：命中就让她重说一次（和别的重说共用次数）；
3. 重说完还命中（或者已经没有重说机会）：把命中的那半句直接删掉，删完没东西就不发这条。

内置：「这X我服」（「这优先级我服」「这波我服了」「这操作我是服的」）。
主人可以在面板加更多：AI_CHAT_BANNED_PATTERNS，一行（或逗号）一个，用「…」表示中间随便几个字，
比如「这…我服」「不愧是…」「笑死…了」。
"""
from __future__ import annotations

import re

from ...core import env

_GAP = r"[^。！？!?\n]{0,12}?"
BUILTIN = [
    ("这…我服", re.compile(r"这" + _GAP + r"[，,\s]*我(?:是|也|算是|真的?|就|可|倒是)?(?:真的?)?服(?![务装从用药刑役饰侍帖软])")),
]
_CLAUSE = re.compile(r"[^，,。！？!?；;\n～~]+[，,。！？!?；;～~]*")


def _custom() -> list[tuple[str, re.Pattern]]:
    raw = env.get("AI_CHAT_BANNED_PATTERNS", "")
    out = []
    for item in re.split(r"[\n,，；;|]+", raw):
        item = item.strip()
        if len(item.replace("…", "").replace("*", "")) < 2:
            continue
        parts = [re.escape(p) for p in re.split(r"…+|\.{3,}|\*+", item)]
        out.append((item, re.compile(_GAP.join(parts))))
    return out


def patterns() -> list[tuple[str, re.Pattern]]:
    return BUILTIN + _custom()


def hit(text: str) -> str:
    """命中了哪个不许用的句式（返回句式名），没命中返回空串。"""
    t = str(text or "")
    for name, rx in patterns():
        if rx.search(t):
            return name
    return ""


def strip(text: str) -> str:
    """把命中的那半句删掉；整句都是它就返回空串。"""
    t = str(text or "")
    if not hit(t):
        return t
    pats = [rx for _n, rx in patterns()]
    kept = [c for c in _CLAUSE.findall(t) if not any(rx.search(c) for rx in pats)]
    out = "".join(kept).strip()
    out = re.sub(r"[，,；;]+$", "", out).strip()
    if hit(out):  # 跨逗号命中（「这优先级，我服」）：前后半句都去掉
        out = ""
    return out


def prompt_line() -> str:
    names = [n for n, _rx in patterns()]
    return "- 这些句式主人明确说过不许再出现，一次都别用：" + "、".join(f"「{n}」" for n in names) + "（…是随便什么词）。"


RETRY_HINT = "你刚才用了主人明确禁止的句式「{name}」，这种说法一次都不许出现。换一种完全不同的说法重说同一个意思，短一点。"
