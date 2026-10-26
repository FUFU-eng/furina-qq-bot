"""NoneBot matcher 定义：聊天、总结、戳一戳、入群欢迎。"""

import re

from nonebot import on_message, on_notice
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
from nonebot.rule import Rule

from . import config
from ...core import brain, env, roles
from ...core.permissions import allowed as _allowed

# NapCat 新版用无 CQ: 前缀的现代格式 [at:qq=X]，旧版是 [CQ:at,qq=X]，都要兼容
_AT_RE = re.compile(r"\[(?:CQ:)?at[:,]qq=(\d+)\]")


def _chat_rule(event) -> bool:
    """只监听配置群里的群消息，且忽略机器人自己发的消息。"""
    if not isinstance(event, GroupMessageEvent):
        return False
    if not _allowed(str(event.group_id), "chat", True):
        return False
    if str(event.user_id) in {str(getattr(event, "self_id", "")), str(config.BOT_QQ)}:
        return False
    return True


_chat = on_message(rule=Rule(_chat_rule), priority=8, block=False)


def _summary_trigger(event) -> bool:
    """触发即时总结：艾特机器人 + 输入'总结'或'总结一下'"""
    if not isinstance(event, GroupMessageEvent):
        return False
    if not _allowed(str(event.group_id), "chat", True):
        return False
    if str(event.user_id) in {str(getattr(event, "self_id", "")), str(config.BOT_QQ)}:
        return False
    if not roles.is_bot_at(event):
        return False
    text = roles.strip_bot_at(event.raw_message, str(getattr(event, "self_id", "")))
    text = re.sub(r"\[(?:CQ:)?at[^\]]*\]", "", text).strip()
    return text in ("总结", "总结一下", "总结下", "群总结", "聊天总结")


_summary = on_message(rule=Rule(_summary_trigger), priority=7, block=True)


_poke = on_notice(priority=8, block=False)


_join = on_notice(priority=8, block=False)



def _agent_private_rule(event) -> bool:
    """主人私聊发文字 + 面板打开了「允许 Agent 操作电脑」→ 交给 Agent（其它插件先处理，没人接才轮到这里）。"""
    if not isinstance(event, PrivateMessageEvent) or not brain.agent_enabled():
        return False
    owner = env.owner_qq()
    if not owner or str(event.user_id) != str(owner):
        return False
    return bool(event.get_plaintext().strip())


_agent_private = on_message(rule=Rule(_agent_private_rule), priority=25, block=True)
