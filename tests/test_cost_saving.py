"""2026-10-30 省钱改动：花钱感知、状态只给增量、说完就结束、接话闸门、主人指令、按群 / 逐轮记账。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from furina.core import brain, budget, db, rounds
from furina.plugins.chat import kernel
from tests.test_humanlike import BASE_ENV, GID

ROOT = Path(__file__).resolve().parents[1]


def _spend(yuan: float, gid: str = "") -> None:
    day = date.today().isoformat()
    db.usage_add(day, "llmtok:cost_u", "dsh.sim2", "", int(yuan * 1e6))
    if gid:
        db.usage_add(day, "llmgrp:cost_u", gid, "", int(yuan * 1e6))
    budget._cache["ts"] = 0


class BudgetTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE_ENV)
        db.usage_prune("9999-12-31")
        budget._cache["ts"] = 0

    def test_curve_shape(self):
        self.assertAlmostEqual(budget.willingness(0), 1.0, places=3)
        w1 = budget.willingness(1.0)  # 半衰点：保底 0.1 + 0.9 / 2
        self.assertAlmostEqual(w1, 0.55, places=2)
        self.assertLess(budget.willingness(3.0), 0.12)
        pts = budget.curve()
        self.assertEqual(len(pts), 13)
        self.assertTrue(all(a["w"] >= b["w"] for a, b in zip(pts, pts[1:])))

    def test_at_always_allowed_even_over_cap(self):
        _env.write_env(BASE_ENV + "AI_CHAT_DAILY_CAP_YUAN=0.5\n")
        _spend(1.0)
        self.assertEqual(budget.allow("at", GID), (True, ""))
        ok, why = budget.allow("random", GID, rng=lambda: 0.0)
        self.assertFalse(ok)
        self.assertIn("上限", why)
        self.assertEqual(budget.effort_cap("high", GID), "off")

    def test_group_cap(self):
        _env.write_env(BASE_ENV + "AI_CHAT_DAILY_CAP_YUAN=0\nAI_CHAT_GROUP_DAILY_CAP_YUAN=0.2\n")
        _spend(0.3, str(GID))
        self.assertEqual(budget.over_cap(GID), "group")
        self.assertEqual(budget.over_cap(123), "")

    def test_follow_less_willing_than_at(self):
        _spend(1.0)
        ok, _ = budget.allow("follow", GID, rng=lambda: 0.5)  # 0.55^2.5 ≈ 0.22 < 0.5
        self.assertFalse(ok)
        ok, _ = budget.allow("follow", GID, rng=lambda: 0.1)
        self.assertTrue(ok)

    def test_settings_documented_in_panel(self):
        from panel.services.brain_settings import BRAIN

        f = {x[0]: x for _t, _d, fs in BRAIN for x in fs}
        s = budget.settings()
        self.assertEqual(f["AI_CHAT_BUDGET_HALF_YUAN"][3], s["half"])
        self.assertEqual(f["AI_CHAT_DAILY_CAP_YUAN"][3], s["daily_cap"])
        self.assertEqual(f["AI_CHAT_GROUP_DAILY_CAP_YUAN"][3], s["group_cap"])
        ds = brain.dsh_settings()
        self.assertEqual(f["AI_CHAT_DSH_END_AFTER_SAY"][3], ds["end_after_say"])
        self.assertEqual(f["AI_CHAT_SIM2_DYNAMIC_MAX"][3], ds["dynamic_max"])
        self.assertEqual(f["AI_CHAT_WAKE_FOLLOW_GATE"][3], brain.follow_gate())
        self.assertEqual(f["AI_CHAT_WAKE_PEAK_FACTOR"][3], brain.peak_wake_factor())


class DynamicDeltaTest(unittest.TestCase):
    def test_only_new_paragraphs_and_capped(self):
        st = kernel.GState(sid="1", gid=GID)
        dyn = "【你的长期记忆】小明养猫\n\n你最近几次说过的话：\n- 哈哈\n\n【心情】开心"
        first = kernel._dynamic_delta(st, dyn, 1500)
        self.assertIn("小明养猫", first)
        self.assertNotIn("你最近几次说过的话", first)
        second = kernel._dynamic_delta(st, dyn.replace("开心", "有点烦"), 1500)
        self.assertEqual(second, "【心情】有点烦")
        st.dyn_seen.clear()
        self.assertLessEqual(len(kernel._dynamic_delta(st, "a" * 5000, 300)), 300)
        self.assertEqual(kernel._dynamic_delta(st, "x", 0), "")


class FollowGateTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE_ENV)

    def test_model_says_no(self):
        st = kernel.GState(sid="1", gid=GID)

        async def fake(*_a, **_k):
            return "否"
        with mock.patch.object(kernel.core_llm, "chat", fake):
            ok, why = asyncio.run(kernel._follow_gate(st, [{"uid": "2", "name": "a", "text": "今天吃啥"}]))
        self.assertFalse(ok)
        self.assertGreater(st.gate_until, time.time())

    def test_model_says_yes_and_owner_skips_model(self):
        st = kernel.GState(sid="1", gid=GID)

        async def fake(*_a, **_k):
            return "是"
        with mock.patch.object(kernel.core_llm, "chat", fake):
            self.assertTrue(asyncio.run(kernel._follow_gate(st, [{"uid": "2", "name": "a", "text": "你说得对"}]))[0])

        async def boom(*_a, **_k):
            raise AssertionError("不该调模型")
        with mock.patch.object(kernel.core_llm, "chat", boom):
            self.assertTrue(asyncio.run(kernel._follow_gate(st, [{"uid": "9", "name": "o", "text": "嗯", "owner": True}]))[0])

    def test_fallback_when_model_down(self):
        st = kernel.GState(sid="1", gid=GID)
        st.last_said = time.time()

        async def boom(*_a, **_k):
            raise RuntimeError("down")
        with mock.patch.object(kernel.core_llm, "chat", boom):
            self.assertTrue(asyncio.run(kernel._follow_gate(st, [{"uid": "2", "name": "a", "text": "你呢？"}]))[0])
            self.assertFalse(asyncio.run(kernel._follow_gate(st, [{"uid": "2", "name": "a", "text": "吃饭去了"}]))[0])


class RoundsTest(unittest.TestCase):
    def test_add_and_board(self):
        db.usage_prune("9999-12-31")
        db.delete(rounds.NS, date.today().isoformat())
        tot = rounds.add(GID, reason="at", rows=[{"hit": 1000, "miss": 200, "out": 10, "cost": 0.0003},
                                                 {"hit": 1200, "miss": 0, "out": 5, "cost": 0.0001}],
                         effort="low", steps=2, tools=["qq_say"], said=1)
        self.assertEqual(tot["calls"], 2)
        rec = rounds.recent(group_id=GID)
        self.assertEqual(rec[0]["why"], "at")
        self.assertEqual(rec[0]["max_in"], 1200)
        g = [r for r in rounds.by_group(1) if r["group"] == str(GID)][0]
        self.assertEqual(g["rounds"], 1)
        self.assertAlmostEqual(g["cost"], 0.0004, places=4)
        self.assertEqual(sum(h["rounds"] for h in rounds.hourly()), 1)
        from furina.services import llm_spend

        b = llm_spend.board()
        self.assertIn("budget", b)
        self.assertTrue(b["today"])


class BridgeEndAfterSayTest(unittest.TestCase):
    def test_bridge_concludes_turn(self):
        js = (ROOT / "tools" / "dsh_bridge" / "furina-bridge.mjs").read_text(encoding="utf-8")
        self.assertIn("exec.concludeTurn()", js)
        self.assertIn("end_after_say", js)
        worker = (ROOT / "tools" / "dsh_worker.py").read_text(encoding="utf-8")
        self.assertIn('"end_after_say"', worker)


if __name__ == "__main__":
    unittest.main()
