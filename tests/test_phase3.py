"""阶段 3：米游社 / 封神榜 / 成员缓存 / 定时提醒 / 转发的存储改造与迁移。"""
from tests import _env  # noqa: F401  (must be first)

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from furina.core import db, docfile, paths
from tools.migrate import Migrator


def _w(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class DocfileTest(unittest.TestCase):
    def test_roundtrip_uses_basename(self):
        self.assertFalse(docfile.exists("p3_x.json"))
        self.assertEqual(docfile.load("p3_x.json", {"d": 1}), {"d": 1})
        docfile.save("/any/dir/p3_x.json", {"a": [1, 2]}, indent=1)
        self.assertTrue(docfile.exists("p3_x.json"))
        self.assertEqual(docfile.load("elsewhere/p3_x.json"), {"a": [1, 2]})


class ReminderSettingsDbTest(unittest.TestCase):
    def test_db_mode(self):
        from furina.services.reminder import settings
        db.delete("reminder", "settings")
        cfg = settings.save(dict(settings.DEFAULTS, enabled=True, max_active=7))
        self.assertTrue(cfg["enabled"])
        self.assertEqual(settings.load()["max_active"], 7)
        self.assertEqual(db.get("reminder", "settings")["max_active"], 7)

    def test_store_defaults_to_main_db(self):
        from furina.services.reminder.store import Store
        self.assertEqual(Store().path, paths.DB_PATH)


class Phase3MigrateTest(unittest.TestCase):
    def test_migrate(self):
        old = Path(tempfile.mkdtemp())
        bot = old / "bot"
        bot.mkdir()
        (bot / ".env").write_text("HOST=0.0.0.0\nRELAY_ENABLED=true\n", encoding="utf-8")
        _w(bot / "miyoushe_seen.json", {"26": {"p1": {"groups": ["1"]}, "p2": {}}})
        _w(bot / "miyoushe_known_groups.json", ["1"])
        _w(bot / "miyoushe_baselines.json", {"1": 1700000000.0})
        _w(bot / "miyoushe_rate.json", {"rate": 9437184.0, "n": 3})
        _w(bot / "data/_shared/fengshen_data.json", {"1": {"stats": {"2": {"msg": 5}}}})
        _w(bot / "data/_shared/group_members.json", {"groups": {"1": {"2": "甲"}}, "group_names": {"1": "群"}})
        rdir = bot / "data/_shared/reminder"
        _w(rdir / "settings.json", {"enabled": True, "natural_language": True, "max_active": 10,
                                    "cooldown_seconds": 10, "grace_minutes": 30})
        from furina.services.reminder.store import Store
        s = Store(rdir / "reminders.sqlite3")
        with s.connect() as c:
            c.execute("INSERT INTO reminders (gid,uid,text,schedule,next_at,created,updated,source_key) "
                      "VALUES ('1','2','收菜','{}',1.0,1.0,1.0,'k-p3-1')")

        env_path = paths.ENV_PATH
        if env_path.exists():
            env_path.rename(env_path.with_suffix(".keep"))
        try:
            Migrator(old, apply=False).run()
            self.assertIsNone(db.get("files", "fengshen_data.json"))
            Migrator(old, apply=True).run()
            self.assertEqual(docfile.load("miyoushe_seen.json")["26"]["p1"]["groups"], ["1"])
            self.assertEqual(docfile.load("miyoushe_known_groups.json"), ["1"])
            self.assertEqual(docfile.load("miyoushe_rate.json")["n"], 3)
            self.assertEqual(docfile.load("fengshen_data.json")["1"]["stats"]["2"]["msg"], 5)
            self.assertEqual(docfile.load("group_members.json")["group_names"]["1"], "群")
            self.assertTrue(db.get("reminder", "settings")["enabled"])
            with sqlite3.connect(paths.DB_PATH) as c:
                rows = c.execute("SELECT text FROM reminders WHERE source_key='k-p3-1'").fetchall()
            self.assertEqual(rows, [("收菜",)])
            Migrator(old, apply=True).run()  # 重复导入不重复插入
            with sqlite3.connect(paths.DB_PATH) as c:
                n = c.execute("SELECT COUNT(*) FROM reminders WHERE source_key='k-p3-1'").fetchone()[0]
            self.assertEqual(n, 1)
            text = env_path.read_text(encoding="utf-8")
            for key in ("RELAY_SOURCE_GROUP=", "RELAY_OWNER_QQ=", "FENGSHEN_GROUPS=", "MIYOUSHE_GROUPS="):
                self.assertIn(key, text)
        finally:
            if env_path.exists():
                env_path.unlink()
            keep = env_path.with_suffix(".keep")
            if keep.exists():
                keep.rename(env_path)


if __name__ == "__main__":
    unittest.main()
