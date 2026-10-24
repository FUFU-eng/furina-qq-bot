"""故意冷落的人：有人问「是不是拉黑了」时她要认，不能装不知道说「没顾上」。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from unittest import mock

from furina.plugins.chat import boundary, memory, planner
from furina.plugins.chat import llm as chat_llm

GID = 100000004
BASE = ("BOT_QQ=30000009\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"
        "AI_CHAT_GROUP_OWNER_QQ=200000001\nAI_CHAT_PLANNER=1\n")


class ColdNoteTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE)
        boundary.reset("200000003")
        memory.reset(GID)

    def _strike_twice(self, now):
        boundary.check("200000003", "桃桃（往生堂堂主）", "（舔了好几口）", now=now - 3600)
        boundary.check("200000003", "桃桃（往生堂堂主）", "*扑倒*", now=now - 60)

    def test_note_when_cold_person_is_around(self):
        now = time.time()
        self._strike_twice(now)
        memory.add_message(GID, "user", "桃桃（往生堂堂主）：某种意义上是被芙芙拉黑了啊", ts=now - 30,
                           meta={"uid": "200000003", "mid": 71})
        note = boundary.cold_note(memory.get_history(GID), now=now)
        self.assertIn("桃桃", note)
        self.assertIn("故意", note)
        self.assertIn("没顾上", note)       # 明确禁止「没顾上」这种借口
        self.assertIn("6 小时", note)

    def test_no_note_when_not_cold_or_not_in_group(self):
        now = time.time()
        memory.add_message(GID, "user", "桃桃（往生堂堂主）：在吗", ts=now - 30, meta={"uid": "200000003", "mid": 72})
        self.assertEqual(boundary.cold_note(memory.get_history(GID), now=now), "")   # 没被冷落
        self._strike_twice(now)
        self.assertEqual(boundary.cold_note([], now=now), "")                          # 不在这个群出现
        self.assertEqual(boundary.cold_note(memory.get_history(GID), now=now + 7 * 3600), "")  # 冷落已结束

    def test_planner_gets_facts(self):
        seen = []

        async def fake_llm(messages, **kw):
            seen.append(messages[-1]["content"])
            return '{"reply": true}'

        with mock.patch.object(chat_llm, "chat", side_effect=fake_llm):
            asyncio.run(planner.plan("", "魔小墨：芙芙姐你是把堂主拉黑了吗？", True, facts="【你正在故意冷落的人】堂主"))
        self.assertIn("【你正在故意冷落的人】堂主", seen[0])


if __name__ == "__main__":
    unittest.main()
