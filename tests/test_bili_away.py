"""叫她去刷 B 站：真去刷，一阵子别人艾特不回；到点（或主人叫）回来，再按性格回艾特过她的人。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from pathlib import Path
from unittest import mock

from furina.plugins.chat import handlers, kernel
from furina.services.bilibili import store as bili_store
from tests.test_humanlike import BASE_ENV, GID, make_event

ROOT = Path(__file__).resolve().parents[1]
OWNER = 20999
ENV = BASE_ENV + f"AI_CHAT_GROUP_OWNER_QQ={OWNER}\n"


class AwayTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(ENV)
        kernel._away.clear()
        kernel._groups.clear()

    def tearDown(self):
        kernel._away.clear()
        kernel._groups.clear()

    def _go(self, sid="30000001", gid=GID, logged=True):
        async def run():
            with mock.patch.object(bili_store, "logged_in", return_value=logged), \
                 mock.patch.object(bili_store, "halted", return_value=(False, "")), \
                 mock.patch.object(bili_store, "put_request") as pr:
                r = kernel.go_away(sid, gid, "主人叫我去")
            return r, pr
        return asyncio.run(run())

    def _observe(self, text, uid, mid):
        ev = make_event(f"[CQ:at,qq=30000001] {text}", user_id=uid, mid=mid)
        with mock.patch.object(kernel, "available", return_value=True), \
             mock.patch.object(kernel, "_wake") as wk, \
             mock.patch.object(kernel, "_wake_reason", new=mock.AsyncMock(return_value="at")) as wr:
            ok = asyncio.run(kernel.observe(ev, GID, str(uid), "群友", text, True, False, False))
        return ok, wk, wr

    def test_go_away_requests_surf_and_sets_timer(self):
        r, pr = self._go()
        self.assertTrue(r["ok"])
        self.assertEqual(r["minutes"], 10)
        pr.assert_called_once_with("surf_told")
        aw = kernel.away_info("30000001")
        self.assertAlmostEqual(aw["until"] - time.time(), 600, delta=5)
        r2, pr2 = self._go(logged=False)
        self.assertTrue(r2["ok"])  # 已经在刷了
        pr2.assert_not_called()

    def test_minutes_and_switch_from_settings(self):
        _env.write_env(ENV + "BILI_AWAY_MINUTES=3\n")
        r, _ = self._go()
        self.assertEqual(r["minutes"], 3)
        kernel._away.clear()
        _env.write_env(ENV + "BILI_AWAY_ENABLED=false\n")
        r, _ = self._go()
        self.assertFalse(r["ok"])
        self.assertIsNone(kernel.away_info("30000001"))

    def test_mentions_while_away_are_held(self):
        self._go()
        ok, wk, wr = self._observe("在吗", 20001, 9101)
        self.assertTrue(ok)
        wk.assert_not_called()
        wr.assert_not_awaited()
        self.assertIn(GID, kernel.away_info("30000001")["groups"])
        st = kernel._state("30000001", GID)
        self.assertTrue(st.unread[-1]["at"])  # 回来时还能看到是谁艾特的

    def test_owner_mention_brings_her_back(self):
        self._go()
        self._observe("在吗", 20001, 9102)
        with mock.patch.object(kernel, "available", return_value=True), \
             mock.patch.object(kernel, "_wake") as wk:
            ev = make_event("[CQ:at,qq=30000001] 回来", user_id=OWNER, mid=9103)
            asyncio.run(kernel.observe(ev, GID, str(OWNER), "主人", "回来", True, False, False))
        self.assertIsNone(kernel.away_info("30000001"))
        self.assertEqual(wk.call_args.args[1], "back")
        self.assertIn("主人艾特你", kernel._state("30000001", GID).back_note)

    def test_time_up_wakes_with_personality_note(self):
        self._go()
        self._observe("在吗", 20001, 9104)
        with mock.patch.object(kernel, "_wake") as wk:
            kernel.end_away("30000001", "time")
        wk.assert_called_once()
        st, reason = wk.call_args.args
        self.assertEqual(reason, "back")
        self.assertIn("群友", st.back_note)
        self.assertIn("烦不烦", st.back_note)
        self.assertIsNone(kernel.away_info("30000001"))
        prompt = kernel._group_prompt(st, "back", [x for x in st.unread if x["seq"] > st.seen_seq])
        self.assertIn("你刚回来", prompt)
        self.assertNotIn("有人在找你", prompt)

    def test_no_messages_no_wake(self):
        self._go()
        with mock.patch.object(kernel, "_wake") as wk:
            kernel.end_away("30000001", "time")
        wk.assert_not_called()

    def test_run_group_skips_while_away(self):
        self._go()
        st = kernel._state("30000001", GID)
        st.unread.append({"seq": 1, "mid": "1", "uid": "1", "name": "a", "text": "x", "ts": time.time(),
                          "at": True, "reply": "", "image": False, "bot": False})
        st.seq = 1
        with mock.patch.object(kernel.humanize, "skip_if_limited") as sk:
            asyncio.run(kernel._run_group(st, "at"))
        sk.assert_not_called()
        self.assertEqual(st.seen_seq, 0)

    def test_memory_shortcut_waits_too(self):
        self._go()
        ev = make_event("[CQ:at,qq=30000001] 添加小明的设定：猫", mid=9105)
        with mock.patch.object(handlers, "_handle_memory_intent", new=mock.AsyncMock(return_value="记住了")) as h, \
             mock.patch.object(handlers.emotion, "observe_message"), mock.patch.object(handlers.favor, "observe_message"):
            r = asyncio.run(handlers._sim2_pre_at(ev, GID, "20001", "群友", str(ev.message), "添加小明的设定：猫", True))
        self.assertFalse(r)
        h.assert_not_awaited()

    def test_wiring(self):
        self.assertIn("surf_told", bili_store.REQUEST_ACTIONS)
        self.assertIn("surf_life", bili_store.REQUEST_ACTIONS)  # 她自己想刷那条以前会被拒
        bridge = (ROOT / "tools/dsh_bridge/furina-bridge.mjs").read_text(encoding="utf-8")
        self.assertIn("['go_bili'", bridge)
        self.assertIn("go_bili", kernel.SIM2_RULES + (ROOT / "furina/plugins/chat/kernel.py").read_text(encoding="utf-8"))
        plugin = (ROOT / "furina/plugins/bilibili/__init__.py").read_text(encoding="utf-8")
        self.assertIn('action == "surf_told"', plugin)
        from panel.services import huoren_settings as hs
        keys = {f[0] for _t, _d, fields in hs.GROUPS.values() for f in fields}
        self.assertTrue({"BILI_AWAY_ENABLED", "BILI_AWAY_MINUTES"} <= keys)


if __name__ == "__main__":
    unittest.main()
