"""活人感 P1：结构化记录 / 先判断再回复 / 连发合并 / 拟人发送与限速。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from unittest import mock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from furina.plugins.chat import chat as chat_mod
from furina.plugins.chat import handlers, humanize, memory, planner, transcript, turns
from furina.plugins.chat import llm as chat_llm

BOT = "30000001"
GID = 100000004
BASE_ENV = (f"BOT_QQ={BOT}\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"
            "AI_CHAT_REPLY_SPLIT_PROBABILITY=0\nAI_CHAT_POKE_PROBABILITY=0\nAI_CHAT_STICKER_PROBABILITY=0\n"
            "AI_CHAT_HUMANIZE=0\nAI_CHAT_PROBABILITY=0\n")


def make_event(text, user_id=20001, mid=1, name="测试群友"):
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


class TranscriptTest(unittest.TestCase):
    def test_numbering_reply_refs_and_same_name(self):
        now = time.time()
        hist = [
            {"role": "user", "content": "小明：今晚吃啥", "ts": now - 30, "uid": "1001", "mid": "m1", "name": "小明"},
            {"role": "user", "content": "小明：火锅", "ts": now - 20, "uid": "1002", "mid": "m2", "name": "小明"},
            {"role": "assistant", "content": "火锅好", "ts": now - 10, "to": "m2"},
            {"role": "user", "content": "阿成：+1", "ts": now - 5, "uid": "1003", "mid": "m3", "reply": "m1", "name": "阿成"},
        ]
        text, index = transcript.render(hist, limit=30)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("#1 "))
        self.assertIn("小明(尾号1001)", lines[0])
        self.assertIn("小明(尾号1002)", lines[1])
        self.assertIn("你 ↩#2：火锅好", lines[2])
        self.assertIn("阿成 ↩#1：+1", lines[3])
        self.assertEqual(set(index), {"#1", "#2", "#3", "#4"})

    def test_gap_and_exclude(self):
        now = time.time()
        hist = [
            {"role": "user", "content": "甲：早", "ts": now - 4000, "mid": "a"},
            {"role": "user", "content": "乙：晚上好", "ts": now - 10, "mid": "b"},
            {"role": "user", "content": "乙：在吗", "ts": now - 5, "mid": "c"},
        ]
        text, _ = transcript.render(hist, exclude_mids=["c"])
        self.assertIn("—— 过了 1 小时 ——", text)
        self.assertNotIn("在吗", text)
        self.assertIn("乙：晚上好", text)

    def test_strip_tags(self):
        self.assertEqual(transcript.strip_tags("你说的#12那个 好像不对"), "你说的那个 好像不对")
        self.assertEqual(transcript.strip_tags("2024 年"), "2024 年")


class PlannerParseTest(unittest.TestCase):
    def test_parse_variants(self):
        r = planner._parse('好的 {"reply": false, "related": ["#3", 5], "refers": "他=阿成", "direction": "别插话"} 完')
        self.assertFalse(r["reply"])
        self.assertEqual(r["related"], ["#3", "#5"])
        self.assertIsNone(planner._parse("没有 JSON"))
        note = planner.note({"related": ["#3", "#9"], "refers": "", "direction": "接火锅的话"}, {"#3": "阿成：+1"})
        self.assertIn("#3 阿成：+1", note)
        self.assertNotIn("#9", note)
        self.assertIn("接火锅的话", note)


class TurnsTest(unittest.TestCase):
    def test_merge_and_wait(self):
        t = turns.open_turn(1, "u")
        self.assertIsNotNone(t)
        self.assertIsNone(turns.open_turn(1, "u"))
        self.assertTrue(turns.append(1, "u", "还有", mid="m9"))
        self.assertFalse(turns.append(1, "other", "x"))
        self.assertEqual(turns.combined_text("第一句", t), "第一句\n还有")
        self.assertEqual(t.mids(), ["m9"])
        start = time.monotonic()
        asyncio.run(turns.wait_quiet(t, window=0.3, max_total=1.0))
        self.assertLess(time.monotonic() - start, 1.2)
        turns.close(t)
        self.assertFalse(turns.is_open(1, "u"))


class GuardTest(unittest.TestCase):
    def setUp(self):
        humanize.reset()

    def test_rate_limit_and_breaker(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_MINUTE=2\n")
        sent = []

        async def ok(m):
            sent.append(m)
            return {"message_id": 1}

        g = humanize.guarded(ok, 7)

        async def run():
            for _ in range(3):
                await g("hi")
        asyncio.run(run())
        self.assertEqual(len(sent), 2)

        async def boom(m):
            raise RuntimeError("timeout")

        b = humanize.guarded(boom, 8)

        async def run2():
            for _ in range(3):
                await b("x")
        asyncio.run(run2())
        self.assertTrue(humanize.breaker_status()["open"])
        self.assertFalse(humanize.allow_send(9)[0])
        humanize.reset()

    def test_first_delay_subtracts_spent(self):
        _env.write_env(BASE_ENV.replace("AI_CHAT_HUMANIZE=0", "AI_CHAT_HUMANIZE=1"))
        self.assertEqual(humanize.first_delay("短", 30), 0.0)
        self.assertGreater(humanize.first_delay("这是一句稍微长一点的回复内容", 0), 0.5)


class MergeFlowTest(unittest.TestCase):
    def setUp(self):
        memory.reset(GID)
        humanize.reset()

    def _run(self, events_with_delay, env_extra="", llm_delay=0.05):
        _env.write_env(BASE_ENV + env_extra)
        bot = FakeBot()
        sent, prompts = [], []

        async def fake_send(message, **kw):
            sent.append(str(message))
            return {"message_id": 99}

        async def fake_llm(messages, timeout=None, max_tokens=None, **kw):
            prompts.append((max_tokens, messages))
            if max_tokens == 160:
                return '{"action":"none"}'
            if max_tokens == 200:
                return '{"reply": true, "related": [], "refers": "", "direction": "回答吃什么"}'
            await asyncio.sleep(llm_delay)
            return "那就火锅吧"

        matcher = mock.MagicMock()
        matcher.skip = mock.AsyncMock(side_effect=RuntimeError("skip"))

        async def main():
            tasks = []
            for delay, ev in events_with_delay:
                await asyncio.sleep(delay)
                tasks.append(asyncio.create_task(handlers.handle_chat(ev, matcher)))
            await asyncio.gather(*tasks)

        with mock.patch("nonebot.get_bots", return_value={BOT: bot}), \
                mock.patch("nonebot.get_bot", return_value=bot), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm), \
                mock.patch.object(chat_mod._chat, "send", side_effect=fake_send):
            asyncio.run(main())
        return sent, prompts

    def test_followup_merged_into_one_reply(self):
        sent, prompts = self._run([
            (0, make_event(f"[CQ:at,qq={BOT}] 今晚吃啥", mid=11)),
            (0.2, make_event("要辣的", mid=12)),
        ], "AI_CHAT_MERGE_SECONDS=0.6\nAI_CHAT_MERGE_MAX_SECONDS=3\n")
        self.assertEqual(len(sent), 1)
        self.assertIn("那就火锅吧", sent[0])
        main_prompt = [m for t, m in prompts if t not in (160, 200)][-1]
        user_text = main_prompt[-1]["content"] if isinstance(main_prompt[-1]["content"], str) else ""
        self.assertIn("今晚吃啥", user_text)
        self.assertIn("要辣的", user_text)
        self.assertIn("回答吃什么", user_text)   # 先判断的结果交给了正式回复
        self.assertIn("【现在要你回复的消息】", user_text)
        self.assertTrue(any(t == 200 for t, _ in prompts))
        self.assertIsNotNone(memory.replied_text(GID, "12"))

    def test_duplicate_event_replied_once(self):
        ev = make_event(f"[CQ:at,qq={BOT}] 在吗", mid=21)
        sent, _ = self._run([(0, ev)], "AI_CHAT_MERGE_SECONDS=0\n")
        sent2, _ = self._run([(0, make_event(f"[CQ:at,qq={BOT}] 在吗", mid=21))], "AI_CHAT_MERGE_SECONDS=0\n")
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent2), 0)

    def test_followup_during_generation_triggers_regenerate(self):
        sent, prompts = self._run([
            (0, make_event(f"[CQ:at,qq={BOT}] 推荐个游戏", mid=41)),
            (0.4, make_event("要单机的", mid=42)),
        ], "AI_CHAT_MERGE_SECONDS=0.1\n", llm_delay=0.6)
        self.assertEqual(len(sent), 1)
        mains = [m for t, m in prompts if t not in (160, 200)]
        self.assertEqual(len(mains), 2)
        self.assertNotIn("要单机的", mains[0][-1]["content"])
        self.assertIn("要单机的", mains[1][-1]["content"])
        # 旧稿没写进记忆：助手只有一条
        self.assertEqual(sum(1 for e in memory.get_history(GID) if e["role"] == "assistant"), 1)

    def test_memory_meta_and_mid_dedupe(self):
        memory.add_message(GID, "user", "甲：你好", meta={"uid": "1", "mid": "x1", "secret": "no"})
        memory.add_message(GID, "user", "甲：你好", meta={"uid": "1", "mid": "x1"})
        hist = [e for e in memory.get_history(GID) if e.get("mid") == "x1"]
        self.assertEqual(len(hist), 1)
        self.assertNotIn("secret", hist[0])

    def test_seed_keeps_meta(self):
        memory.seed(GID, [
            {"role": "user", "content": "甲：1", "ts": 1, "uid": "1", "mid": "s1", "name": "甲"},
            {"role": "user", "content": "甲：1", "ts": 2, "uid": "1", "mid": "s2", "name": "甲"},
            {"role": "user", "content": "甲：1", "ts": 2, "uid": "1", "mid": "s2", "name": "甲"},
            {"role": "assistant", "content": "嗯", "ts": 3, "mid": "s3", "reply": "s2"},
        ])
        hist = memory.get_history(GID)
        self.assertEqual([e.get("mid") for e in hist], ["s1", "s2", "s3"])
        self.assertEqual(hist[-1]["reply"], "s2")

    def test_seed_group_history_from_napcat(self):
        from furina.plugins.chat import history as hist_mod

        class SeedBot(FakeBot):
            async def call_api(self, api, **kw):
                return {"messages": [
                    {"user_id": 1001, "message_id": 501, "time": 100, "sender": {"nickname": "甲", "card": ""},
                     "message": [{"type": "text", "data": {"text": "今天下雨"}}]},
                    {"user_id": 1002, "message_id": 502, "time": 110, "sender": {"nickname": "乙", "card": ""},
                     "message": [{"type": "reply", "data": {"id": "501"}}, {"type": "text", "data": {"text": "带伞了吗"}}]},
                ]}

        _env.write_env(BASE_ENV)
        bot = SeedBot()
        with mock.patch.object(hist_mod, "get_bot", return_value=bot), \
                mock.patch("nonebot.get_bots", return_value={BOT: bot}):
            asyncio.run(hist_mod._seed_group_history(GID))
        hist = memory.get_history(GID)
        self.assertEqual(hist[-1]["mid"], "502")
        self.assertEqual(hist[-1]["reply"], "501")
        self.assertEqual(hist[-1]["uid"], "1002")
        text, _ = transcript.render(hist)
        self.assertIn("乙 ↩#1：带伞了吗", text)

    def test_defaults_reply_immediately_without_merge(self):
        # 默认值按小鲸鱼教程：等 6 秒说完、先「打字」再发；这里关掉等待验证「不合并时立刻回」
        _env.write_env(BASE_ENV.replace("AI_CHAT_HUMANIZE=0\n", ""))
        from furina.plugins.chat import config as cfg
        self.assertEqual(cfg.MERGE_SECONDS, 6.0)
        self.assertTrue(cfg.HUMANIZE)
        self.assertGreater(humanize.first_delay("一句挺长的回复内容内容内容", 0), 0.5)
        self.assertEqual(humanize.first_delay("一句挺长的回复内容内容内容", 30), 0.0)
        sent, _ = self._run([(0, make_event(f"[CQ:at,qq={BOT}] 早", mid=51))], "AI_CHAT_MERGE_SECONDS=0\nAI_CHAT_HUMANIZE=0\n")
        self.assertEqual(len(sent), 1)
        self.assertFalse(turns.is_open(GID, "20001"))

    def test_classic_mode_still_works(self):
        sent, prompts = self._run([(0, make_event(f"[CQ:at,qq={BOT}] 你好", mid=31))],
                                  "AI_CHAT_MERGE_SECONDS=0\nAI_CHAT_STRUCTURED_CONTEXT=0\nAI_CHAT_PLANNER=0\n")
        self.assertEqual(len(sent), 1)
        self.assertFalse(any(t == 200 for t, _ in prompts))


if __name__ == "__main__":
    unittest.main()
