"""群友活跃：按人记艾特聊天 / JM / 强化 / 搜图 / 戳一戳，永久保留，面板按权重算占比。"""
from tests import _env  # noqa: F401  (must be first)

import unittest
from datetime import date, timedelta
from pathlib import Path

from furina.core import db, usage
from panel.services import data as pdata

BASE = "BOT_QQ=30000009\nPEER_BOTS=30000010\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"
ROOT = Path(__file__).resolve().parents[1]


class MemberUsageTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE)
        db.usage_prune("9999-99-99", keep_prefix="stat:")  # 清掉之前测试留下的按人记录
        db.put("panel_prefs", "usage_weights", {})
        db.put("panel_prefs", "usage_total", 0)

    def test_track_and_totals(self):
        usage.track_user("chat", 111, "9001", name="阿成")
        usage.track_user("chat", 111, "9001")
        usage.track_user("jm", 222, "9001")
        usage.track_user("search", 111, "9002", name="小雨")
        usage.track_user("upscale", 111, "9002")
        usage.track_user("upscale_img", 111, "9002", count=5)
        usage.track_user("chat", 111, "")          # 没有 QQ：不记
        tot = usage.who_totals(0)
        self.assertEqual(tot["9001"], {"chat": 2, "jm": 1})
        self.assertEqual(tot["9002"]["upscale_img"], 5)
        self.assertEqual(usage.who_totals(0, 222), {"9001": {"jm": 1}})
        self.assertEqual(db.get("usage_names", "9001"), {"name": "阿成"})

    def test_prune_keeps_who_rows(self):
        old = (date.today() - timedelta(days=200)).isoformat()
        db.usage_add(old, "who:chat", "111", "9003", 7)
        db.usage_add(old, "stat:chat", "111", "", 7)
        usage._last_prune["day"] = ""
        usage.track("chat", 111)                  # 触发每日清理
        self.assertEqual(usage.who_totals(0).get("9003"), {"chat": 7})   # 按人的永久保留
        self.assertEqual(usage.who_totals(30).get("9003"), None)         # 但不在近 30 天里
        self.assertEqual(db.usage_get(old, "stat:chat", "111"), 0)       # 功能统计照旧只留 40 天

    def test_member_usage_shares_and_pay(self):
        for _ in range(6):
            usage.track_user("chat", 111, "9001", name="阿成")
        usage.track_user("jm", 111, "9002", name="小雨")
        usage.track_user("upscale_img", 111, "9002", count=3)
        usage.track_user("poke", 111, "9002")      # 戳一戳默认权重 0
        usage.track_user("chat", 111, "30000010")  # 别的机器人不算
        usage.track_user("chat", 111, "30000009")  # 自己不算
        res = pdata.member_usage(30, "", total=100)
        uids = [r["uid"] for r in res["rows"]]
        self.assertEqual(uids, ["9001", "9002"])
        a, b = res["rows"]
        self.assertAlmostEqual(a["share"], 0.6)
        self.assertAlmostEqual(b["share"], 0.4)
        self.assertEqual(a["pay"], 60.0)
        self.assertEqual(res["totals"]["poke"], 1)
        pdata.save_usage_weights({"upscale_img": 3})
        res = pdata.member_usage(30, "")
        self.assertEqual(res["rows"][0]["uid"], "9002")   # 强化调成 3 倍后，小雨 1 + 9 = 10 分 > 阿成 6 分
        self.assertIsNone(res["rows"][0]["pay"])
        self.assertEqual(pdata.save_usage_total(88.5), 88.5)
        self.assertEqual(pdata.usage_total(), 88.5)

    def test_hooks_and_panel_wired(self):
        src = {p: (ROOT / p).read_text(encoding="utf-8") for p in (
            "furina/plugins/chat/handlers.py", "furina/plugins/jm/__init__.py", "furina/plugins/upscale/__init__.py",
            "furina/plugins/image_search/__init__.py", "panel/classic/window.py", "panel/classic/common.py",
            "panel/web/app.js", "panel/server.py")}
        self.assertIn('track_user("chat"', src["furina/plugins/chat/handlers.py"])
        self.assertIn('track_user("poke"', src["furina/plugins/chat/handlers.py"])
        self.assertEqual(src["furina/plugins/jm/__init__.py"].count('track_user("jm"'), 2)
        self.assertIn('track_user("upscale"', src["furina/plugins/upscale/__init__.py"])
        self.assertEqual(src["furina/plugins/upscale/__init__.py"].count('track_user("upscale_img"'), 2)
        self.assertIn('track_user("search"', src["furina/plugins/image_search/__init__.py"])
        self.assertIn('("usage", "群友活跃")', src["panel/classic/window.py"])
        self.assertIn('"usage"', src["panel/classic/common.py"])
        self.assertIn("/api/member_usage", src["panel/web/app.js"])
        self.assertIn("/api/member_usage", src["panel/server.py"])


if __name__ == "__main__":
    unittest.main()
