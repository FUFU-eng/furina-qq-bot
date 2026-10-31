"""二代仿真省钱：群会话太长就换新会话，下一轮带上最近的聊天。"""
from tests import _env  # noqa: F401  (must be first)

import unittest
from unittest import mock

from furina.core import agent_engine as engine
from furina.core import brain
from furina.plugins.chat import kernel
from tests.test_humanlike import BASE_ENV, GID


def _res(*prompts):
    return {"ok": True, "usage": [{"prompt_tokens": p, "completion_tokens": 10} for p in prompts]}


class RotateTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE_ENV)
        kernel._groups.clear()

    def tearDown(self):
        kernel._groups.clear()

    def test_defaults(self):
        ds = brain.dsh_settings()
        self.assertEqual((ds["context_max_k"], ds["recap_lines"]), (30, 30))
        from panel.services import brain_settings
        fields = {f[0]: f for _t, _d, fs in brain_settings.BRAIN for f in fs}
        self.assertEqual(fields["AI_CHAT_DSH_CONTEXT_MAX_K"][3], ds["context_max_k"])
        self.assertEqual(fields["AI_CHAT_DSH_RECAP_LINES"][3], ds["recap_lines"])

    def test_context_tokens_is_largest_request(self):
        self.assertEqual(kernel.context_tokens(_res(1000, 75000, 74000)), 75000)
        self.assertEqual(kernel.context_tokens({"ok": False}), 0)

    def test_rotates_only_when_big_and_after_min_runs(self):
        st = kernel._state("30000001", GID)
        st.bootstrapped = True
        ds = brain.dsh_settings()
        old = engine.session_id(st.sid, st.gid)
        self.assertFalse(kernel._maybe_rotate(st, _res(90000), ds))   # 第 1 轮：刚开的会话不急着换
        self.assertFalse(kernel._maybe_rotate(st, _res(20000), ds))   # 还小
        self.assertTrue(kernel._maybe_rotate(st, _res(61000), ds))    # 第 3 轮且超了
        self.assertNotEqual(engine.session_id(st.sid, st.gid), old)
        self.assertFalse(st.bootstrapped)                             # 下一轮会带上最近的聊天
        self.assertEqual(st.runs_in_session, 0)

    def test_zero_means_unlimited(self):
        _env.write_env(BASE_ENV + "AI_CHAT_DSH_CONTEXT_MAX_K=0\n")
        st = kernel._state("30000001", GID)
        ds = brain.dsh_settings()
        for _ in range(5):
            self.assertFalse(kernel._maybe_rotate(st, _res(500000), ds))

    def test_recap_uses_setting(self):
        _env.write_env(BASE_ENV + "AI_CHAT_DSH_RECAP_LINES=12\n")
        st = kernel._state("30000001", GID)
        st.bootstrapped = False
        new = [{"seq": 1, "mid": "9", "uid": "1", "name": "a", "text": "在吗", "ts": 0, "at": False, "reply": "",
                "image": False, "bot": False}]
        with mock.patch.object(kernel.transcript, "render", return_value=("（旧聊天）", None)) as r:
            prompt = kernel._group_prompt(st, "random", new)
        self.assertEqual(r.call_args.kwargs["limit"], 12)
        self.assertIn("（旧聊天）", prompt)
        self.assertTrue(st.bootstrapped)


if __name__ == "__main__":
    unittest.main()
