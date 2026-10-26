"""AI 回复里的动作标记：解析、剥离与执行。

模型可以在回复里带这些标记（提示词见 persona）：
``[SEND_IMAGE:N]`` 发图库图、``[AT:目标:内容]`` 艾特群友、``[ESSENCE]`` 设精华、
``[SEARCH_IMAGE]`` / ``[UPSCALE]`` 交给搜图/强化插件、``[ADD_SETTING:目标:内容]`` /
``[DELETE_SETTING:目标]`` 增删词条。

按主人的决定：被艾特的请求里任何群友都能触发动作；随机接话和戳一戳只剥掉标记、不执行。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment

from . import humanize, learning
from .commands import _match_member, send_eagle_images
from ...core.botref import bot_for

_SEND_IMAGE_RE = re.compile(r"\[SEND_IMAGE:(\d+)\]")
_AT_RE = re.compile(r"\[AT:([^:]+):([^\]]+)\]")
_ADD_RE = re.compile(r"\[ADD_SETTING:([^:]+):([^\]]+)\]")
_DEL_RE = re.compile(r"\[DELETE_SETTING:([^\]]+)\]")
_QUOTE_ID_RE = re.compile(r"\[(?:CQ:)?reply[,:]id=(-?\d+)")
# AI 回复里说要帮忙找原图/搜图，且引用里有图：交给搜图插件
_REPLY_SEARCH_RE = re.compile(
    r"(找|搜|查).{0,4}(原图|出处|来源|相似图|高清图|一下|这个图|这张图)|"
    r"(这就帮你找|帮你找|帮你搜|找找原图|搜一下图|帮你找找)"
)


@dataclass
class ReplyActions:
    send_image_count: int = 0
    at_target: str = ""
    at_content: str = ""
    essence: bool = False
    search_image: bool = False
    upscale: bool = False
    add_target: str = ""
    add_value: str = ""
    delete_target: str = ""

    def has_side_effects(self) -> bool:
        return bool(self.send_image_count or self.at_target or self.essence
                    or self.add_target or self.delete_target)

    def drop_side_effects(self) -> None:
        self.send_image_count = 0
        self.at_target = self.at_content = ""
        self.essence = False
        self.add_target = self.add_value = ""
        self.delete_target = ""


def parse(reply: str, group_id) -> tuple[str, ReplyActions]:
    """剥掉回复里的全部动作标记，返回（干净的回复, 动作）。"""
    act = ReplyActions()
    m = _SEND_IMAGE_RE.search(reply)
    if m:
        act.send_image_count = max(1, min(int(m.group(1)), 10))
        reply = re.sub(r"\[SEND_IMAGE:\d+\]\s*", "", reply).strip()
        logger.info(f"[AI聊天] 群{group_id} AI触发发图指令，数量={act.send_image_count}")
    m = _AT_RE.search(reply)
    if m:
        act.at_target, act.at_content = m.group(1).strip(), m.group(2).strip()
        reply = re.sub(r"\[AT:[^:]+:[^\]]+\]\s*", "", reply).strip()
        logger.info(f"[AI聊天] 群{group_id} AI触发艾特指令，目标={act.at_target}, 内容={act.at_content}")
    if "[ESSENCE]" in reply:
        act.essence = True
        reply = reply.replace("[ESSENCE]", "").strip()
        logger.info(f"[AI聊天] 群{group_id} AI触发设精华指令")
    if "[SEARCH_IMAGE]" in reply:
        act.search_image = True
        reply = reply.replace("[SEARCH_IMAGE]", "").strip()
        logger.info(f"[AI聊天] 群{group_id} AI触发搜图指令")
    if "[UPSCALE]" in reply:
        act.upscale = True
        reply = reply.replace("[UPSCALE]", "").strip()
        logger.info(f"[AI聊天] 群{group_id} AI触发强化指令")
    m = _ADD_RE.search(reply)
    if m:
        act.add_target, act.add_value = m.group(1).strip(), m.group(2).strip()
        reply = re.sub(r"\[ADD_SETTING:[^:]+:[^\]]+\]\s*", "", reply).strip()
        logger.info(f"[AI聊天] 群{group_id} AI触发添加设定指令，目标={act.add_target}, 内容={act.add_value}")
    m = _DEL_RE.search(reply)
    if m:
        act.delete_target = m.group(1).strip()
        reply = re.sub(r"\[DELETE_SETTING:[^\]]+\]\s*", "", reply).strip()
        logger.info(f"[AI聊天] 群{group_id} AI触发删除设定指令，目标={act.delete_target}")
    return reply, act


async def execute(act: ReplyActions, event, group_id, sender) -> None:
    """回复发出之后执行有副作用的动作（每个动作独立，失败只记日志）。"""
    if act.send_image_count > 0:
        try:
            sent = await send_eagle_images(sender, act.send_image_count, "ai")
            if sent:
                logger.info(f"[AI聊天] 群{group_id} AI发图成功，共{sent}张")
        except Exception as e:
            logger.warning(f"[AI聊天] 群{group_id} AI发图失败: {e}")

    if act.at_target:
        try:
            bot = bot_for(event)
            members = await bot.get_group_member_list(group_id=group_id)
            target_user_id = _match_member(members, act.at_target)
            if target_user_id:
                at_msg = MessageSegment.at(target_user_id) + MessageSegment.text(f" {act.at_content}" if act.at_content else "")
                await bot.send_group_msg(group_id=group_id, message=humanize.prepare_message(at_msg))
                logger.info(f"[AI聊天] 群{group_id} AI艾特成功，目标={act.at_target}({target_user_id})")
            else:
                logger.info(f"[AI聊天] 群{group_id} AI艾特失败，未找到目标={act.at_target}")
                # 找不到人也要吱一声，别让群友以为机器人没反应
                await bot.send_group_msg(group_id=group_id, message=MessageSegment.text(f"没找到叫{act.at_target}的人~"))
        except Exception as e:
            logger.warning(f"[AI聊天] 群{group_id} AI艾特失败: {e}")

    if act.essence:
        try:
            m = _QUOTE_ID_RE.search(getattr(event, "raw_message", "") or "")
            if m:
                msg_id = int(m.group(1))
                await bot_for(event).set_essence_msg(message_id=msg_id)
                logger.info(f"[AI聊天] 群{group_id} AI设精华成功，msg_id={msg_id}")
            else:
                logger.info(f"[AI聊天] 群{group_id} AI设精华失败，未找到引用消息")
        except Exception as e:
            logger.warning(f"[AI聊天] 群{group_id} AI设精华失败: {e}")

    if act.add_target and act.add_value:
        try:
            changed, fact = learning.add_setting(group_id, act.add_target, act.add_value)
            if fact:
                logger.info(f"[AI聊天] 群{group_id} AI添加设定{'成功' if changed else '已存在'}：{fact}")
        except Exception as e:
            logger.warning(f"[AI聊天] AI添加设定失败: {e}")

    if act.delete_target:
        try:
            count, deleted = learning.delete_settings(group_id, act.delete_target)
            if count:
                logger.info(f"[AI聊天] 群{group_id} AI删除设定成功：{'; '.join(deleted[:3])}")
            else:
                logger.info(f"[AI聊天] 群{group_id} AI删除设定未找到：{act.delete_target}")
        except Exception as e:
            logger.warning(f"[AI聊天] AI删除设定失败: {e}")


def wants_search(reply: str, quote) -> bool:
    """AI 回复表示要帮忙找图，且引用里有图（或读取失败）：交给搜图插件。"""
    return bool(reply and quote is not None and (quote.images or quote.failed)
                and _REPLY_SEARCH_RE.search(reply))
