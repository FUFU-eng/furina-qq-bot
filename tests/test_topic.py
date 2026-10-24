"""串话题：时间挨着不等于同一个话题；不认识的名字别猜成前面聊过的东西（「天选姬已被打入冷宫」被套到「让AI装桌宠」上）。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest
from unittest import mock

from furina.plugins.chat import planner
from furina.plugins.chat import llm as chat_llm

_ENV = "AI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\nAI_CHAT_PLANNER=1\n"


class TopicTest(unittest.TestCase):
    def test_new_topic_drops_links(self):
        r = planner._parse('{"topic": "新话题", "reply": true, "related": ["#15", "#27"], "direction": "按字面接"}')
        self.assertTrue(r["new_topic"])
        self.assertEqual(r["related"], [])
        note = planner.note(r, {"#15": "冷师傅：桌宠怎么下载"})
        self.assertIn("新话题", note)
        self.assertNotIn("#15", note)

    def test_continue_and_echoed_template(self):
        r = planner._parse('{"topic": "接着聊", "related": ["#3"]}')
        self.assertFalse(r["new_topic"])
        self.assertEqual(r["related"], ["#3"])
        # 模型把模板原样抄回来（「接着聊/新话题」）不能当成新话题
        self.assertFalse(planner._parse('{"topic": "接着聊/新话题", "related": ["#3"]}')["new_topic"])
        self.assertFalse(planner._parse('{"reply": true, "related": ["#3"]}')["new_topic"])

    def test_prompt_rules_and_known_words(self):
        seen = []

        async def fake_llm(messages, **kw):
            seen.append(messages[-1]["content"])
            return '{"topic": "新话题", "reply": true}'

        _env.write_env(_ENV)
        with mock.patch.object(chat_llm, "chat", side_effect=fake_llm):
            asyncio.run(planner.plan("#1 你：让AI装桌宠，装坏了算谁的", "路人甲：天选姬已被打入冷宫", False))
            asyncio.run(planner.plan("", "甲：今晚吃啥", True))
        self.assertIn("时间挨着不等于", seen[0])
        self.assertIn("别把它猜成前面聊过的", seen[0])
        self.assertIn("看不懂这条在说什么", seen[0])        # 随机接话：看不懂就不插
        self.assertIn("天选姬：华硕", seen[0])              # 认识的词交给判断
        self.assertNotIn("看不懂这条在说什么", seen[1])     # 被点名时必须回
        self.assertNotIn("这些词你认识", seen[1])


if __name__ == "__main__":
    unittest.main()
