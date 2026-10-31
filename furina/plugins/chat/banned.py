"""不许再出现的句式：主人点名嫌弃过的说法，一次都不能发出去。

三道关（本地计算）：
1. 提示词里直接说「这些句式别用」；
2. 发出前检查：命中就让她重说一次（和别的重说共用次数）；
3. 重说完还命中（或者已经没有重说机会）：把命中的那半句直接删掉，删完没东西就不发这条。

内置：「这X我服」（「这优先级我服」「这波我服了」「这操作我是服的」），
以及换壳的「……，我服」（「你们还认真讨论野史不野史，我服」）。
主人可以在面板加更多：AI_CHAT_BANNED_PATTERNS，一行（或逗号）一个，用「…」表示中间随便几个字，
比如「这…我服」「不愧是…」「笑死…了」。
"""
from __future__ import annotations

import re

from ...core import env

_GAP = r"[^。！？!?\n]{0,12}?"
_FU = r"我(?:是|也|算是|真的?|就|可|倒是)?(?:真的?)?服"
BUILTIN = [
    ("这…我服", re.compile(r"这" + _GAP + r"[，,\s]*" + _FU + r"(?![务装从用药刑役饰侍帖软])")),
    # 同一个套路换了个壳：「你们还认真讨论野史不野史，我服」——前面评一句，逗号后面甩个「我服」收尾
    ("…，我服", re.compile(r"(?:[，,]\s*" + _FU + r"了?|^\s*" + _FU + r")(?=\s*(?:[。！？!?～~…]|$))")),
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


# ---------------- 最后一道关：所有发到 QQ 的文字 ----------------
# 一代 / 二代 / 冒泡 / 欢迎 / 补话……哪条路发出去的都要过这里，漏网的直接删掉那半句。
_SEND_APIS = {"send_group_msg", "send_private_msg", "send_msg"}


def _clean_message(msg):
    """返回 (新消息, 是否改过, 是否还剩内容)。支持 str / Message / 段列表。"""
    if isinstance(msg, str):
        if not hit(msg):
            return msg, False, bool(msg.strip())
        new = "\n".join(x for x in (strip(line) if hit(line) else line for line in msg.split("\n")) if x.strip())
        return new, True, bool(new.strip())
    try:
        segs = list(msg)
    except TypeError:
        return msg, False, True
    changed, keep = False, []
    for seg in segs:
        stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
        data = seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", None)
        if stype == "text" and isinstance(data, dict) and hit(str(data.get("text") or "")):
            new, _c, left = _clean_message(str(data.get("text") or ""))
            changed = True
            if not left:
                continue
            data["text"] = new
        keep.append(seg)
    if not changed:
        return msg, False, True
    has_content = any((s.get("type") if isinstance(s, dict) else getattr(s, "type", "")) != "text"
                      or str(((s.get("data") if isinstance(s, dict) else getattr(s, "data", {})) or {}).get("text") or "").strip()
                      for s in keep)
    try:
        new_msg = type(msg)(keep)
    except Exception:  # noqa: BLE001
        new_msg = keep
    return new_msg, True, has_content


def _register_send_guard() -> None:
    try:
        from nonebot import logger
        from nonebot.adapters import Bot
        from nonebot.exception import MockApiException
    except Exception:  # noqa: BLE001
        return

    @Bot.on_calling_api
    async def _banned_send_guard(bot, api: str, data: dict) -> None:  # noqa: ANN001
        if api not in _SEND_APIS or "message" not in data:
            return
        try:
            new, changed, left = _clean_message(data["message"])
        except Exception:  # noqa: BLE001  检查出错不影响发送
            return
        if not changed:
            return
        if not left:
            logger.info("[禁用句式] 整条都是主人禁止的句式，不发")
            raise MockApiException(result={"message_id": 0})
        data["message"] = new
        logger.info("[禁用句式] 发出前删掉了主人禁止的那半句")


_register_send_guard()
