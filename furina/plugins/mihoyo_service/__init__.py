"""Official GenshinUID NoneBot connector. No custom game handlers or sign scheduler."""
from __future__ import annotations
import asyncio
from nonebot import get_driver, require
from nonebot.adapters import Event
import re
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.exception import IgnoredException
from nonebot.message import run_preprocessor, event_preprocessor
from nonebot.matcher import Matcher
from nonebot.log import logger
from furina.core import permissions, roles
from furina.services.gscore import ensure_running


@get_driver().on_startup
async def _start_upstream_core():
    try:
        state = await asyncio.to_thread(ensure_running)
        if not state["ready"]:
            logger.warning("GsCore 正在初始化；官方适配器将自动重连。")
    except Exception as exc:
        logger.error("GsCore 启动失败：{}；未启用任何自制游戏回退。", exc)


def _strip_text_at(text: str, self_id: str):
    """消息开头是纯文本「@芙宁娜-白芙」（没点选成真正的艾特）时，返回去掉它之后的文字；否则返回 None。"""
    head = text.lstrip()
    if not head.startswith(("@", "＠")):
        return None
    for name in roles.mention_aliases(self_id):
        m = re.match(rf"^[@＠]{re.escape(name)}(?:[-－—_·•\s]*(?:白芙|黑芙))?(?=\s|$|[,，:：])\s*[,，:：]?\s*", head)
        if m:
            return head[m.end():]
    return None


@event_preprocessor
async def _text_at_as_mention(event: Event):
    """群友手打「@芙宁娜-白芙 米游社签到」时 QQ 不会生成真正的艾特，GsCore 就当成普通聊天忽略了。
    这里把开头的纯文本艾特换成和真艾特完全一样的效果（to_me=True 并去掉开头的 @名字）。"""
    if not isinstance(event, GroupMessageEvent) or event.to_me:
        return
    try:
        msg = event.message
        if not msg or msg[0].type != "text":
            return
        rest = _strip_text_at(str(msg[0].data.get("text") or ""), str(event.self_id))
        if rest is None:
            return
        segs = ([MessageSegment.text(rest)] if rest else []) + list(msg[1:])
        if not segs:
            return
        event.message = Message(segs)
        event.to_me = True
    except Exception as exc:  # 只是兼容层，任何异常都不影响原消息
        logger.debug(f"纯文本艾特兼容处理跳过：{exc}")


@run_preprocessor
async def _preserve_group_gate(matcher: Matcher, event: Event):
    # Only bridge the existing platform access gate; no game command interpretation.
    if matcher.plugin_name == "GenshinUID" and isinstance(event, GroupMessageEvent):
        # Conservative migration: neither old permission can silently become a grant.
        for feature in ("game_query", "miyoushe_sign"):
            if not permissions.is_Allowed(event.group_id, event.user_id, feature):
                raise IgnoredException("当前群或群友未启用 GsCore 游戏服务")


require("GenshinUID")
