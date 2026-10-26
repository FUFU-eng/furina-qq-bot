"""她答应少说话就真潜水；qq_say 收到字符串形式的 JSON 数组不原样发出去。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from pathlib import Path
from unittest import mock

from furina.plugins.chat import kernel
from tests.test_humanlike import BASE_ENV, GID

ROOT = Path(__file__).resolve().parents[1]


class JsonLinesTest(unittest.TestCase):
    def test_json_array_string_is_unpacked(self):
        self.assertEqual(kernel._split_lines('["抱玩偶那个…有点可爱"]'), ["抱玩偶那个…有点可爱"])
        self.assertEqual(kernel._split_lines(['["a", "b"]']), ["a", "b"])
        self.assertEqual(kernel._split_lines("[\"半截的"), ["[\"半截的"])
        self.assertEqual(kernel._split_lines("[图片] 好看"), ["[图片] 好看"])
        self.assertEqual(kernel._split_lines(["正常一句", "第二句"]), ["正常一句", "第二句"])


class QuietTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE_ENV)
        kernel._groups.clear()

    def _ctx(self, *lines):
        ctx = kernel.RunCtx("group", "30000001", GID)
        ctx.lines = list(lines)
        ctx.said = len(lines)
        return ctx

    def test_promise_without_nap_becomes_nap(self):
        st = kernel._state("30000001", GID)
        kernel._keep_quiet_promise(st, self._ctx("行，那我潜水了，别想我"), "at", ["qq_say"])
        self.assertAlmostEqual(st.nap_until - time.time(), 3600, delta=5)

    def test_minutes_setting(self):
        _env.write_env(BASE_ENV + "AI_CHAT_QUIET_MINUTES=20\n")
        st = kernel._state("30000001", GID)
        kernel._keep_quiet_promise(st, self._ctx("……行，闭就闭"), "at", ["qq_say"])
        self.assertAlmostEqual(st.nap_until - time.time(), 1200, delta=5)

    def test_no_false_positive(self):
        st = kernel._state("30000001", GID)
        kernel._keep_quiet_promise(st, self._ctx("这猫在水里潜得挺深"), "at", ["qq_say"])
        kernel._keep_quiet_promise(st, self._ctx("行，我潜水"), "random", ["qq_say"])  # 不是被叫到
        kernel._keep_quiet_promise(st, self._ctx("行，我潜水"), "at", ["qq_say", "nap"])  # 自己用了 nap
        self.assertEqual(st.nap_until, 0.0)

    def test_nap_blocks_follow_and_limits_to_one_line(self):
        st = kernel._state("30000001", GID)
        st.nap_until = time.time() + 600
        st.last_said = time.time()
        e = {"seq": 1, "mid": "1", "uid": "1", "name": "a", "text": "哈哈", "ts": time.time(), "at": False,
             "reply": "", "image": False, "bot": False}
        self.assertEqual(asyncio.run(kernel._wake_reason(None, st, e)), "")
        prompt = kernel._group_prompt(st, "at", [e])
        self.assertIn("你在潜水", prompt)

    def test_nap_zero_ends(self):
        st = kernel._state("30000001", GID)
        st.nap_until = time.time() + 600
        ctx = kernel.RunCtx("group", "30000001", GID)
        kernel._runs["d" * 32] = ctx
        try:
            r = asyncio.run(kernel.handle_tool_callback({"run_id": "d" * 32, "name": "nap", "arguments": {"minutes": 0}}))
        finally:
            kernel._runs.pop("d" * 32, None)
        self.assertTrue(r["ok"])
        self.assertEqual(st.nap_until, 0.0)

    def test_rules_mention_it(self):
        self.assertIn("少说话", kernel.SIM2_RULES)
        bridge = (ROOT / "tools/dsh_bridge/furina-bridge.mjs").read_text(encoding="utf-8")
        self.assertIn("minutes=0", bridge)


if __name__ == "__main__":
    unittest.main()


class QuoteTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE_ENV)
        kernel._groups.clear()

    def _st(self, n):
        st = kernel._state("30000001", GID)
        for i in range(1, n + 1):
            st.unread.append({"seq": i, "mid": str(100 + i), "uid": "1", "name": "a", "text": "x", "ts": time.time(),
                              "at": False, "reply": "", "image": False, "bot": False})
        return st

    def test_default_rarely_quotes(self):
        from furina.plugins.chat import config
        self.assertEqual(config.QUOTE_PROBABILITY, 0.15)
        st = self._st(3)
        with mock.patch.object(kernel.random, "random", return_value=0.5):
            self.assertFalse(kernel._keep_quote(st, "103"))
        with mock.patch.object(kernel.random, "random", return_value=0.05):
            self.assertTrue(kernel._keep_quote(st, "103"))

    def test_far_message_still_quoted(self):
        st = self._st(8)
        with mock.patch.object(kernel.random, "random", return_value=0.99):
            self.assertTrue(kernel._keep_quote(st, "101"))
            self.assertFalse(kernel._keep_quote(st, "107"))

    def test_setting_zero(self):
        _env.write_env(BASE_ENV + "AI_CHAT_QUOTE_PROBABILITY=0\n")
        st = self._st(2)
        with mock.patch.object(kernel.random, "random", return_value=0.0):
            self.assertFalse(kernel._keep_quote(st, "102"))


class LimitNoteTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE_ENV + "AI_CHAT_DAILY_CHAT_LIMIT=2\n")
        kernel._groups.clear()

    def _at(self, mid, uid=20777):
        from tests.test_humanlike import make_event
        ev = make_event("[CQ:at,qq=30000001] 在吗", user_id=uid, mid=mid)
        with mock.patch.object(kernel, "available", return_value=True), \
             mock.patch.object(kernel, "_wake") as wk, \
             mock.patch.object(kernel, "_wake_reason", new=mock.AsyncMock(return_value="at")):
            asyncio.run(kernel.observe(ev, GID, str(uid), "小瓜", "在吗", True, False, False))
        return wk

    def test_says_once_then_silent(self):
        from furina.core import usage
        uid = 20000 + int(time.time()) % 9000
        self.assertEqual(self._at(8001, uid).call_args.args[1], "at")
        self.assertEqual(self._at(8002, uid).call_args.args[1], "at")
        wk = self._at(8003, uid)  # 第 3 次：撞上限，说一句
        self.assertEqual(wk.call_args.args[1], "limited")
        st = wk.call_args.args[0]
        prompt = kernel._group_prompt(st, "limited", [st.unread[-1]])
        self.assertIn("今天被找烦了", prompt)
        self.assertIn("小瓜", prompt)
        self.assertEqual(st.limit_notes, [])
        self._at(8004, uid).assert_not_called()  # 之后今天真的不理
        self.assertEqual(usage.used("chat_limit_note", GID, uid), 1)

    def test_switch_off(self):
        _env.write_env(BASE_ENV + "AI_CHAT_DAILY_CHAT_LIMIT=1\nAI_CHAT_LIMIT_NOTE=false\n")
        uid = 30000 + int(time.time()) % 9000
        self._at(8101, uid)
        self._at(8102, uid).assert_not_called()
