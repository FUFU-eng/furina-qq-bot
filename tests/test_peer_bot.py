"""机器人互聊收尾：和其他 AI 机器人互相艾特时，几轮后自然收尾、不再艾特，冷却期内不再接话。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from unittest import mock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from furina.plugins.chat import bot_dialog, handlers
from furina.plugins.chat import chat as chat_mod
from furina.plugins.chat import llm as chat_llm

BOT = "30000001"
PEER = 40000002
HUMAN = 20009
GID = 1102042777
BASE_ENV = (f"BOT_QQ={BOT}\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"
            "AI_CHAT_REPLY_SPLIT_PROBABILITY=0\nAI_CHAT_POKE_PROBABILITY=0\nAI_CHAT_STICKER_PROBABILITY=0\n"
            "AI_CHAT_HUMANIZE=0\nAI_CHAT_PROBABILITY=0\nAI_CHAT_MERGE_SECONDS=0\nAI_CHAT_PLANNER=0\n"
            f"AI_CHAT_PEER_BOTS={PEER}\nAI_CHAT_PEER_MAX_ROUNDS=3\nAI_CHAT_MAX_SEND_PER_MINUTE=100\n")


def make_event(text, user_id, mid, name):
    msg = Message(text)
    return GroupMessageEvent(
        time=int(time.time()), self_id=int(BOT), post_type="message", message_type="group",
        sub_type="normal", user_id=user_id, message_id=mid, message=msg, original_message=msg,
        raw_message=str(msg), font=0, sender=Sender(user_id=user_id, nickname=name, card=""),
        to_me=False, group_id=GID,
    )


class FakeBot:
    self_id = BOT

    async def send_group_msg(self, **kw):
        return {"message_id": 1}

    async def call_api(self, api, **kw):
        return []


class PeerBotDialogTest(unittest.TestCase):
    def setUp(self):
        bot_dialog.reset()

    def _run(self, events, env_extra=""):
        _env.write_env(BASE_ENV + env_extra)
        bot = FakeBot()
        sent, prompts = [], []

        async def fake_send(message, **kw):
            sent.append(str(message))
            return {"message_id": 99}

        async def fake_llm(messages, timeout=None, max_tokens=None, **kw):
            prompts.append(messages)
            if max_tokens == 160:
                return '{"action":"none"}'
            replies = ["行，那就这么定", "好嘞，听你的", "哈哈先这样，回头聊", "嗯嗯", "没问题", "可以可以"]
            return replies[(len(prompts) - 1) % len(replies)]

        matcher = mock.MagicMock()
        matcher.skip = mock.AsyncMock(side_effect=RuntimeError("skip"))

        async def main():
            for ev in events:
                await handlers.handle_chat(ev, matcher)

        with mock.patch("nonebot.get_bots", return_value={BOT: bot}), \
                mock.patch("nonebot.get_bot", return_value=bot), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm), \
                mock.patch.object(chat_mod._chat, "send", side_effect=fake_send):
            asyncio.run(main())
        return sent, prompts

    @staticmethod
    def _system_text(messages):
        return "\n".join(str(m.get("content")) for m in messages if m.get("role") == "system")

    def test_wrapup_then_silent(self):
        evs = [make_event(f"[CQ:at,qq={BOT}] 第{i}句", PEER, 900 + i, "昔涟") for i in range(1, 6)]
        sent, prompts = self._run(evs)
        self.assertEqual(len(sent), 3)                     # 第 4、5 句不再回
        self.assertIn(f"[CQ:at,qq={PEER}]", sent[0])        # 前两轮照常艾特
        self.assertIn(f"[CQ:at,qq={PEER}]", sent[1])
        self.assertNotIn("[CQ:at", sent[2])                 # 收尾这句不艾特
        self.assertNotIn("[CQ:reply", sent[2])              # 也不引用
        mains = [self._system_text(m) for m in prompts
                 if not str(m[-1].get("content")).startswith("你刚才")]   # 去掉防重复的重试调用
        self.assertEqual(len(mains), 3)
        self.assertNotIn("慢慢收住", mains[0])
        self.assertNotIn("【自然收尾", mains[0])
        self.assertIn("自然地慢慢收住", mains[1])
        self.assertIn("【自然收尾", mains[2])

    def test_humans_unaffected(self):
        evs = [make_event(f"[CQ:at,qq={BOT}] 第{i}句", HUMAN, 950 + i, "群友") for i in range(1, 6)]
        sent, _ = self._run(evs)
        self.assertEqual(len(sent), 5)
        self.assertTrue(all(f"[CQ:at,qq={HUMAN}]" in s for s in sent))

    def test_disabled_switch(self):
        evs = [make_event(f"[CQ:at,qq={BOT}] 第{i}句", PEER, 970 + i, "昔涟") for i in range(1, 5)]
        sent, _ = self._run(evs, "AI_CHAT_PEER_END=0\n")
        self.assertEqual(len(sent), 4)

    def test_state_machine_reset_and_cooldown(self):
        _env.write_env(BASE_ENV + "AI_CHAT_PEER_RESET_SECONDS=60\nAI_CHAT_PEER_COOLDOWN=100\n")
        b = lambda t: bot_dialog.begin(GID, BOT, PEER, now=t)  # noqa: E731
        self.assertEqual(b(0), (bot_dialog.NORMAL, 1))
        self.assertEqual(b(10), (bot_dialog.NORMAL, 2))
        self.assertEqual(b(200), (bot_dialog.NORMAL, 1))    # 隔太久算新话题
        self.assertEqual(b(210), (bot_dialog.NORMAL, 2))
        self.assertEqual(b(220)[0], bot_dialog.WRAPUP)
        self.assertEqual(b(250)[0], bot_dialog.SILENT)
        self.assertTrue(bot_dialog.is_silent(GID, BOT, PEER, now=250))
        self.assertEqual(b(330), (bot_dialog.NORMAL, 1))    # 冷却结束后重新开始
        self.assertEqual(bot_dialog.begin(GID, BOT, HUMAN, now=0), (bot_dialog.HUMAN, 0))

    def test_peer_list_accepts_notes_and_hot_reload(self):
        self.assertEqual(bot_dialog.parse_qq_list(["200000005(昔涟)", "昔涟2:3333344444", " 55555 ", "abc"]),
                         {"200000005", "3333344444", "55555"})
        _env.write_env(BASE_ENV.replace(f"AI_CHAT_PEER_BOTS={PEER}", f"AI_CHAT_PEER_BOTS={PEER}(昔涟)"))
        self.assertTrue(bot_dialog.is_peer(PEER, BOT))
        _env.write_env(BASE_ENV.replace(f"AI_CHAT_PEER_BOTS={PEER}", "AI_CHAT_PEER_BOTS=12345678"))
        self.assertFalse(bot_dialog.is_peer(PEER, BOT))      # 面板改了名单，立刻按新的算
        self.assertTrue(bot_dialog.is_peer(12345678, BOT))
        self.assertFalse(bot_dialog.is_peer(BOT, BOT))        # 自己永远不算


if __name__ == "__main__":
    unittest.main()
