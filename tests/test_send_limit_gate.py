"""发送已到上限时，要发消息的流程不再调用模型（保留每分钟 / 每小时限速本身，两者都按群分开算）。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from unittest import mock

from furina.plugins.chat import chat as chat_mod
from furina.plugins.chat import humanize, presence, proactive, seek
from furina.plugins.chat import llm as chat_llm
from tests.test_humanlike import BASE_ENV, make_event


class SendLimitGateTest(unittest.TestCase):
    def setUp(self):
        humanize.reset()

    def tearDown(self):
        humanize.reset()

    def _fill_hour(self, n, group_id=1, age=120):
        # 默认放在 2 分钟前：只占小时额度，不碰每分钟限速
        humanize._group_sent[int(group_id)].extend([time.time() - age] * n)

    def test_at_limit_reports_reason_without_side_effects(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_HOUR=3\n")
        self.assertEqual(humanize.at_limit(1), "")
        self._fill_hour(3)
        self.assertIn("本群 1 小时内已发 3 条", humanize.at_limit(1))
        self.assertEqual(len(humanize._group_sent[1]), 3)

    def test_hour_limit_is_per_group(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_HOUR=3\n")
        self._fill_hour(3, group_id=1)
        self.assertTrue(humanize.at_limit(1))
        self.assertEqual(humanize.at_limit(2), "")
        humanize._sent_ok(2)
        self.assertEqual(len(humanize._group_sent[1]), 3)
        self.assertEqual(humanize.breaker_status()["sent_last_hour"], {1: 3, 2: 1})

    def test_hour_window_rolls_off(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_HOUR=2\n")
        self._fill_hour(2, group_id=1, age=3700)
        self.assertEqual(humanize.at_limit(1), "")
        self.assertEqual(len(humanize._group_sent[1]), 0)

    def test_old_sends_do_not_count_toward_minute_limit(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_MINUTE=2\n")
        self._fill_hour(5, group_id=7, age=90)
        self.assertEqual(humanize.at_limit(7), "")

    def test_minute_limit_still_applies(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_MINUTE=2\n")
        humanize._group_sent[5].extend([time.time()] * 2)
        self.assertTrue(humanize.at_limit(5))
        self.assertEqual(humanize.at_limit(6), "")

    def test_master_switch_off_ignores_counts(self):
        _env.write_env(BASE_ENV + "AI_CHAT_SEND_LIMIT_ENABLED=false\nAI_CHAT_MAX_SEND_PER_HOUR=2\nAI_CHAT_MAX_SEND_PER_MINUTE=1\n")
        self._fill_hour(5, group_id=1, age=10)
        self.assertEqual(humanize.at_limit(1), "")
        self.assertEqual(humanize.allow_send(1), (True, ""))
        _env.write_env(BASE_ENV + "AI_CHAT_SEND_LIMIT_ENABLED=true\nAI_CHAT_MAX_SEND_PER_HOUR=2\n")
        self.assertIn("本群 1 小时内已发 5 条", humanize.at_limit(1))

    def test_master_switch_off_keeps_breaker(self):
        _env.write_env(BASE_ENV + "AI_CHAT_SEND_LIMIT_ENABLED=false\n")
        for _ in range(humanize.BREAKER_FAILS):
            humanize._sent_fail(RuntimeError("x"))
        self.assertIn("熔断", humanize.at_limit(1))

    def test_reply_skips_llm_when_limited(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_HOUR=2\n")
        ev = make_event("[CQ:at,qq=30000001] 在吗", mid=4242)
        self._fill_hour(2, group_id=ev.group_id)
        with mock.patch.object(chat_llm, "chat", new=mock.AsyncMock(return_value="在")) as m, \
             mock.patch.object(chat_mod.prompting, "gather_images", new=mock.AsyncMock()) as g:
            res = asyncio.run(chat_mod._reply(ev, ev.group_id, "测试群友", None, "在吗", False, None, forced=True))
        self.assertIsNone(res)
        m.assert_not_awaited()
        g.assert_not_awaited()

    def test_memory_intent_skips_llm_when_limited(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_HOUR=1\n")
        ev = make_event("[CQ:at,qq=30000001] 记住我叫小明", mid=4243)
        self._fill_hour(1, group_id=ev.group_id)
        with mock.patch.object(chat_mod.memory_intent, "detect_memory_action", new=mock.AsyncMock()) as m:
            hint = next(iter(chat_mod._MEMORY_HINTS))
            res = asyncio.run(chat_mod._handle_memory_intent(ev, ev.group_id, ev.user_id, "测试群友", f"{hint} 小明", True))
        self.assertFalse(res)
        m.assert_not_awaited()

    def test_proactive_paths_skip_llm_when_limited(self):
        _env.write_env(BASE_ENV + "AI_CHAT_MAX_SEND_PER_HOUR=1\n")
        self._fill_hour(1, group_id=123)
        with mock.patch.object(chat_llm, "chat", new=mock.AsyncMock(return_value="嗨")) as m:
            self.assertEqual(asyncio.run(proactive.compose("123", "30000001", [], "两小时")), "")
            self.assertEqual(asyncio.run(seek._compose("123", "30000001", "p", 0.9, 2)), "")
            self.assertEqual(asyncio.run(presence.compose_miss("123", "30000001", "20001", 86400 * 2)), "")
        m.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
