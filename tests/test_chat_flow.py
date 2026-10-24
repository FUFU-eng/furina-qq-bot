"""端到端：模拟一条 @机器人 的群消息，走完整聊天流程（模型与 QQ 接口都是假的）。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import random
import time
import unittest
from unittest import mock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from furina.plugins.chat import chat as chat_mod
from furina.plugins.chat import handlers, llm as chat_llm

BOT = "30000001"


def make_event(text: str, user_id: int = 20001, group_id: int = 100000004, mid: int = 1) -> GroupMessageEvent:
    msg = Message(text)
    return GroupMessageEvent(
        time=int(time.time()), self_id=int(BOT), post_type="message", message_type="group",
        sub_type="normal", user_id=user_id, message_id=mid, message=msg, original_message=msg,
        raw_message=str(msg), font=0, sender=Sender(user_id=user_id, nickname="测试群友", card=""),
        to_me=False, group_id=group_id,
    )


class FakeBot:
    self_id = BOT

    def __init__(self):
        self.sent = []
        self.apis = []

    async def send_group_msg(self, **kw):
        self.sent.append(kw)
        return {"message_id": 1}

    async def call_api(self, api, **kw):
        self.apis.append(api)
        if api == "get_group_member_info":
            return {"card": "", "nickname": "测试群友"}
        return []

    async def get_image(self, **kw):
        return {}


class ChatFlowTest(unittest.TestCase):
    def test_at_bot_gets_reply(self):
        _env.write_env(f"BOT_QQ={BOT}\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"
                       "AI_CHAT_REPLY_SPLIT_PROBABILITY=0\nAI_CHAT_POKE_PROBABILITY=0\nAI_CHAT_STICKER_PROBABILITY=0\n"
                       "AI_CHAT_MERGE_SECONDS=0\nAI_CHAT_HUMANIZE=0\n")
        bot = FakeBot()
        sent = []

        async def fake_send(message, **kw):
            sent.append(str(message))

        prompts = []

        async def fake_llm(messages, timeout=None, max_tokens=None, **kw):
            prompts.append(messages)
            if max_tokens == 160:  # 记忆意图识别
                return '{"action":"none"}'
            return "哼，本水神当然记得你呀"

        matcher = mock.MagicMock()
        matcher.skip = mock.AsyncMock(side_effect=RuntimeError("skip"))
        random.seed(1)
        with mock.patch("nonebot.get_bots", return_value={BOT: bot}), \
                mock.patch("nonebot.get_bot", return_value=bot), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm), \
                mock.patch.object(chat_mod._chat, "send", side_effect=fake_send):
            asyncio.run(handlers.handle_chat(make_event(f"[CQ:at,qq={BOT}] 你还记得我吗"), matcher))
        out = " ".join(sent) + " ".join(str(x.get("message")) for x in bot.sent)
        self.assertIn("本水神当然记得你呀", out)
        system = prompts[-1][0]["content"]
        self.assertIn("芙宁娜", system)


if __name__ == "__main__":
    unittest.main()
