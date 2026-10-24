"""省钱：锚定窗口让群聊记录前缀稳定（吃缓存）、人设与本轮提示分开、按功能记 token 和花费、冷清时跳过判断。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from datetime import date
from unittest import mock

from furina.core import llm as core_llm
from furina.plugins.chat import chat, config, prompting, transcript


def _rows(n, start=0):
    now = time.time()
    return [{"role": "user", "content": f"阿{i % 3}：第{i}句", "name": f"阿{i % 3}", "uid": str(100 + i % 3),
             "mid": str(5000 + i), "ts": now - (n - i) * 30} for i in range(start, start + n)]


class AnchoredWindowTest(unittest.TestCase):
    def setUp(self):
        transcript._anchors.clear()

    def test_append_only_until_limit_then_reanchor(self):
        hist = _rows(10)
        t1, _ = transcript.render(hist, limit=30, anchor="g1")
        hist2 = hist + _rows(5, start=10)
        t2, _ = transcript.render(hist2, limit=30, anchor="g1")
        self.assertTrue(t2.startswith(t1), "新消息只往后追加，前面原样不动")
        # 攒过上限 → 重新起锚到最近 60%
        hist3 = _rows(40)
        transcript._anchors.clear()
        transcript.render(hist3[:30], limit=30, anchor="g1")
        t4, idx = transcript.render(hist3, limit=30, anchor="g1")
        self.assertEqual(len(idx), 18)
        self.assertIn("第39句", t4)
        hist5 = hist3 + _rows(3, start=40)
        t5, _ = transcript.render(hist5, limit=30, anchor="g1")
        self.assertTrue(t5.startswith(t4))

    def test_without_anchor_slides_like_before(self):
        hist = _rows(40)
        _t, idx = transcript.render(hist, limit=30)
        self.assertEqual(len(idx), 30)

    def test_lost_anchor_reanchors(self):
        transcript.render(_rows(20), limit=30, anchor="g2")
        _t, idx = transcript.render(_rows(25, start=100), limit=30, anchor="g2")
        self.assertEqual(len(idx), 18)


class SplitPromptTest(unittest.TestCase):
    def test_split_and_structured_order(self):
        sp = "人设内容" + prompting.DYN_MARK + "\n\n本轮提示"
        self.assertEqual(prompting.split_dynamic(sp), ("人设内容", "本轮提示"))
        self.assertEqual(prompting.split_dynamic("无分界"), ("无分界", ""))
        msgs = asyncio.run(prompting._build_structured(sp, [], "阿成：你好", [], 1, "#1 10:00 阿成：在吗", ""))
        system = msgs[0]["content"]
        self.assertNotIn("\x00", system)
        self.assertLess(system.index("人设内容"), system.index("#1 10:00"))
        self.assertLess(system.index("#1 10:00"), system.index("本轮提示"))
        self.assertIn("阿成：你好", msgs[1]["content"])
        self.assertNotIn("#1 10:00", msgs[1]["content"])

    def test_classic_strips_mark(self):
        sp = "人设" + prompting.DYN_MARK + "\n\n提示"
        msgs = asyncio.run(prompting._build_classic(sp, [], "x", [], 1))
        self.assertNotIn("\x00", msgs[0]["content"])


class UsageTest(unittest.TestCase):
    def test_caller_tag_and_report(self):
        g = {"__name__": "furina.plugins.chat.fakefeature", "core_llm": core_llm}
        exec("def judge_thing(u):\n    core_llm._record_tokens('m', u)\n", g)
        day = date.today().isoformat()
        before = {r["tag"]: r for r in core_llm.usage_report(day)}.get("chat.fakefeature:judge_thing", {"calls": 0, "miss": 0})
        with mock.patch.object(core_llm, "is_peak", return_value=False):
            g["judge_thing"]({"prompt_tokens": 9000, "prompt_cache_hit_tokens": 8000,
                              "prompt_cache_miss_tokens": 1000, "completion_tokens": 100})
        row = {r["tag"]: r for r in core_llm.usage_report(day)}["chat.fakefeature:judge_thing"]
        self.assertEqual(row["calls"] - before["calls"], 1)
        self.assertEqual(row["miss"] - before["miss"], 1000)
        self.assertGreater(row["cost"], 0)

    def test_peak(self):
        wed_10 = time.mktime((2026, 10, 21, 10, 0, 0, 0, 0, -1))
        wed_13 = time.mktime((2026, 10, 21, 13, 0, 0, 0, 0, -1))
        sat_10 = time.mktime((2026, 10, 24, 10, 0, 0, 0, 0, -1))
        self.assertTrue(core_llm.is_peak(wed_10))
        self.assertFalse(core_llm.is_peak(wed_13))
        self.assertFalse(core_llm.is_peak(sat_10))
        self.assertAlmostEqual(core_llm.prices(wed_10)[1], 2 * core_llm.prices(wed_13)[1])


class QuietSkipTest(unittest.TestCase):
    def test_quiet_forced(self):
        now = time.time()
        old = [{"role": "user", "content": "a：x", "mid": "1", "ts": now - 3600},
               {"role": "user", "content": "b：@你", "mid": "2", "ts": now}]
        self.assertTrue(chat._quiet_forced(True, old, "2"))
        self.assertFalse(chat._quiet_forced(False, old, "2"))
        busy = old + [{"role": "user", "content": "c：y", "mid": "3", "ts": now - 60}]
        self.assertFalse(chat._quiet_forced(True, busy, "2"))
        with mock.patch.object(config, "PLANNER_QUIET_SKIP_MIN", 0, create=True):
            self.assertFalse(chat._quiet_forced(True, old, "2"))


if __name__ == "__main__":
    unittest.main()
