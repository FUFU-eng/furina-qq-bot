from tests import _env  # noqa: F401  (must be first)

import unittest

from furina.core import db, env, permissions, usage


class DbTest(unittest.TestCase):
    def test_docs_roundtrip_and_update(self):
        db.put("t", "a", {"x": 1, "中文": "好"})
        self.assertEqual(db.get("t", "a"), {"x": 1, "中文": "好"})
        db.update("t", "a", lambda v: {**v, "x": v["x"] + 1})
        self.assertEqual(db.get("t", "a")["x"], 2)
        self.assertIn("a", db.items("t"))
        db.delete("t", "a")
        self.assertIsNone(db.get("t", "a"))

    def test_usage_limit(self):
        for _ in range(3):
            self.assertTrue(db.usage_add("2026-01-01", "k", "g", "u", 1, limit=3))
        self.assertFalse(db.usage_add("2026-01-01", "k", "g", "u", 1, limit=3))
        self.assertEqual(db.usage_get("2026-01-01", "k", "g", "u"), 3)


class PermissionTest(unittest.TestCase):
    def test_defaults_match_v1(self):
        g = permissions.get_group("1001")
        self.assertTrue(g["enabled"])
        self.assertTrue(permissions.allowed("1001", "chat"))
        self.assertFalse(permissions.allowed("1001", "pixiv_rank"))
        self.assertFalse(permissions.allowed("1001", "reminder"))
        # JM 下载沿用旧版语义：默认开启，可在面板按群关掉
        self.assertIn("jm", permissions.FEATURES)
        self.assertTrue(permissions.allowed("1001", "jm"))
        permissions.set_feature_enabled("1001", "jm", False)
        self.assertFalse(permissions.allowed("1001", "jm"))

    def test_toggle(self):
        permissions.set_feature_enabled("1002", "poke", False)
        self.assertFalse(permissions.allowed("1002", "poke"))
        permissions.set_group_enabled("1002", False)
        self.assertFalse(permissions.allowed("1002", "chat"))


class UsageTest(unittest.TestCase):
    def test_consume_and_stats(self):
        self.assertTrue(usage.consume("chat", 5, 6, 2))
        self.assertTrue(usage.consume("chat", 5, 6, 2))
        self.assertFalse(usage.consume("chat", 5, 6, 2))
        self.assertTrue(usage.consume("chat", 5, 7, 0))  # 0 = 无限
        usage.track("t_kind", 5)
        usage.track("t_kind", 5, 2)
        self.assertEqual(usage.get_stats()["5"]["t_kind"], 3)
        self.assertGreaterEqual(usage.get_stats_range(7)["total"], 3)
        self.assertEqual(usage.get_stats_range(7)["groups"]["5"]["t_kind"], 3)

    def test_overrides(self):
        o = usage.parse_limit_overrides("111:5, 222:333:0, bad, 9:x")
        self.assertEqual(o, {"111": 5, "222:333": 0})
        self.assertEqual(usage.effective_limit(o, 30, "999", "111"), 5)
        self.assertEqual(usage.effective_limit(o, 30, "222", "333"), 0)
        self.assertEqual(usage.effective_limit(o, 30, "1", "2"), 30)


class EnvTest(unittest.TestCase):
    def test_hot_reload(self):
        _env.write_env("AI_CHAT_PROBABILITY=0.25\nAI_CHAT_VISION=false\n")
        from furina.plugins.chat import config

        self.assertAlmostEqual(config.REPLY_PROBABILITY, 0.25)
        self.assertFalse(config.CHAT_VISION)
        v = env.version()
        _env.write_env("AI_CHAT_PROBABILITY=0.5\nAI_CHAT_GROUP_OWNER_QQ=42\n")
        self.assertGreater(env.version(), v)
        self.assertAlmostEqual(config.REPLY_PROBABILITY, 0.5)
        self.assertTrue(config.CHAT_VISION)  # 回到默认
        self.assertEqual(config.UNLIMITED_CHAT_QQ, {"42"})
        self.assertEqual(config.AI_CHAT_MAX_REPLY_CHARS, 0)  # 默认不限总字数（靠一句一条控制每条长短）


if __name__ == "__main__":
    unittest.main()
