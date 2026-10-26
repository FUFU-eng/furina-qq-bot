"""AI 聊天（芙宁娜人设）。

触发规则与旧版完全一致：
- 艾特机器人：必定回复；未艾特按 AI_CHAT_PROBABILITY 随机接话；
- 引用图片 + 艾特 + “搜图/强化” 交给对应插件；
- 读取引用和合并转发内容；启动时从 NapCat 拉取群最近聊天记录灌入上下文；
- 表情包收藏、戳一戳回复、入群欢迎、即时总结、每日纪要、情绪、好感、群词条记忆。
"""
from nonebot.plugin import PluginMetadata

from . import digest, history, ownlife, presence, proactive, seek, tts  # noqa: F401  注册启动/连接钩子
from .handlers import handle_chat, handle_group_increase, handle_poke, handle_summary
from . import kernel  # noqa: F401  二代仿真 / Agent 内核（注册回调接口）
from .matchers import _agent_private, _chat, _join, _poke, _summary

__plugin_meta__ = PluginMetadata(
    name="AI 聊天",
    description="芙宁娜人设群聊：艾特回复、随机接话、看图、记忆、好感、情绪、戳一戳、入群欢迎、总结",
    usage="@芙宁娜 + 任意内容；@芙宁娜 总结",
)


@_chat.handle()
async def _chat_entry(event):
    await handle_chat(event, _chat)


@_summary.handle()
async def _summary_entry(event):
    await handle_summary(event, _summary)


@_poke.handle()
async def _poke_entry(event):
    await handle_poke(event)


@_join.handle()
async def _join_entry(event):
    await handle_group_increase(event)


@_agent_private.handle()
async def _agent_private_entry(bot, event):
    await kernel.run_agent(bot, str(event.user_id), event.get_plaintext().strip())
