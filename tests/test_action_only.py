"""回复里只有动作标记（如整句就是 [AT:目标:内容]）时，动作必须照常执行，不能被吞掉。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from unittest import mock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from furina.plugins.chat import handlers, memory
from furina.plugins.chat import chat as chat_mod
from furina.plugins.chat import llm as chat_llm

BOT = "30000001"
GID = 1102042888
BASE_ENV = (f"BOT_QQ={BOT}\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"
            "AI_CHAT_REPLY_SPLIT_PROBABILITY=0\nAI_CHAT_POKE_PROBABILITY=0\nAI_CHAT_STICKER_PROBABILITY=0\n"
            "AI_CHAT_HUMANIZE=0\nAI_CHAT_PROBABILITY=0\nAI_CHAT_MERGE_SECONDS=0\nAI_CHAT_PLANNER=0\n"
            "AI_CHAT_MAX_SEND_PER_MINUTE=100\n")


def make_event(text, mid):
    msg = Message(text)
    return GroupMessageEvent(
        time=int(time.time()), self_id=int(BOT), post_type="message", message_type="group",
        sub_type="normal", user_id=20011, message_id=mid, message=msg, original_message=msg,
        raw_message=str(msg), font=0, sender=Sender(user_id=20011, nickname="群主", card=""),
        to_me=False, group_id=GID,
    )


class FakeBot:
    self_id = BOT

    def __init__(self):
        self.group_msgs = []

    async def send_group_msg(self, **kw):
        self.group_msgs.append(str(kw.get("message")))
        return {"message_id": 1}

    async def get_group_member_list(self, **kw):
        return [{"user_id": 55501, "card": "昔涟", "nickname": "cyrene"},
                {"user_id": 55502, "card": "", "nickname": "路人"}]

    async def call_api(self, api, **kw):
        return []


class ActionOnlyReplyTest(unittest.TestCase):
    def _run(self, llm_reply, text, mid):
        _env.write_env(BASE_ENV)
        bot = FakeBot()
        sent = []

        async def fake_send(message, **kw):
            sent.append(str(message))
            return {"message_id": 99}

        async def fake_llm(messages, timeout=None, max_tokens=None, **kw):
            if max_tokens == 160:
                return '{"action":"none"}'
            return llm_reply

        matcher = mock.MagicMock()
        matcher.skip = mock.AsyncMock(side_effect=RuntimeError("skip"))
        with mock.patch("nonebot.get_bots", return_value={BOT: bot}), \
                mock.patch("nonebot.get_bot", return_value=bot), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm), \
                mock.patch.object(chat_mod._chat, "send", side_effect=fake_send):
            asyncio.run(handlers.handle_chat(make_event(text, mid), matcher))
        return sent, bot.group_msgs

    def test_marker_only_at_is_executed(self):
        sent, group_msgs = self._run("[AT:昔涟:那维莱特是枫丹的最高审判官]",
                                     f"[CQ:at,qq={BOT}] 艾特昔涟，向她介绍一下那维莱特", 7001)
        self.assertEqual(sent, [])                                   # 没有多余的「嗯？」
        self.assertEqual(len(group_msgs), 1)
        self.assertIn("[CQ:at,qq=55501]", group_msgs[0])
        self.assertIn("那维莱特是枫丹的最高审判官", group_msgs[0])
        last = [e for e in memory.get_history(GID) if e["role"] == "assistant"][-1]
        self.assertIn("艾特了昔涟", last["content"])

    def test_target_not_found_says_so(self):
        _sent, group_msgs = self._run("[AT:不存在的人:你好]", f"[CQ:at,qq={BOT}] 艾特不存在的人说你好", 7002)
        self.assertEqual(group_msgs, ["没找到叫不存在的人的人~"])

    def test_text_plus_marker_still_sends_both(self):
        sent, group_msgs = self._run("[AT:昔涟:群主找你]好嘞", f"[CQ:at,qq={BOT}] 帮我喊下昔涟", 7003)
        self.assertEqual(len(sent), 1)
        self.assertIn("好嘞", sent[0])
        self.assertIn("[CQ:at,qq=55501]", group_msgs[0])


if __name__ == "__main__":
    unittest.main()
