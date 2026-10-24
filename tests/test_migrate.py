from tests import _env  # noqa: F401  (must be first)

import json
import tempfile
import unittest
from pathlib import Path

from furina.core import db, paths
from tools.migrate import Migrator


def _w(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class MigrateTest(unittest.TestCase):
    def test_dry_run_then_apply(self):
        old = Path(tempfile.mkdtemp())
        bot = old / "bot"
        (bot).mkdir()
        (bot / ".env").write_text("HOST=0.0.0.0\nJM_USERNAME=x\nAI_CHAT_LLM_MODEL=deepseek-flash\n", encoding="utf-8")
        _w(bot / "data/_shared/feature_permissions.json",
           {"groups": {"1": {"enabled": True, "features": {"chat": True, "jm": True}}}})
        _w(bot / "data/_shared/daily_limits.json",
           {"date": "2026-09-28", "chat": {"1:2": 4}, "poke": {}, "stats": {"1": {"chat": 9}},
            "history": [{"date": "2026-09-27", "stats": {"1": {"auto": 3}}}]})
        _w(bot / "plugins/learned.json", {"900": {"aliases": [], "style_notes": [], "facts": [{"fact": "甲的设定：乙"}]}})
        _w(bot / "plugins/favor.json", {"users": {"2": {"favor": 55}}})
        _w(bot / "plugins/emotion.json", {"mood": 70, "day": "2026-09-28", "events": [], "journal": []})
        _w(bot / "plugins/digest.json", {"2026-09-28": {"1": {"digest": "d", "facts": []}}})
        _w(bot / "roles.json", {"roles": [{"id": "r1", "prompt": "p"}]})

        env_path = paths.ENV_PATH
        if env_path.exists():
            env_path.rename(env_path.with_suffix(".keep"))
        Migrator(old, apply=False).run()
        self.assertIsNone(db.get("learned", "900"))
        self.assertFalse(env_path.exists())

        Migrator(old, apply=True).run()
        self.assertEqual(db.get("perm", "1")["features"], {"chat": True})
        self.assertEqual(db.usage_get("2026-09-28", "chat", "1", "2"), 4)
        self.assertEqual(db.usage_get("2026-09-27", "stat:auto", "1", ""), 3)
        self.assertEqual(db.get("favor", "2")["favor"], 55)
        self.assertEqual(db.get("emotion", "state")["mood"], 70)
        self.assertEqual(db.get("persona", "roles")["roles"][0]["id"], "r1")
        text = env_path.read_text(encoding="utf-8")
        self.assertNotIn("JM_USERNAME", text)
        self.assertIn("AI_CHAT_GROUP_OWNER_QQ=", text)
        env_path.unlink()
        keep = env_path.with_suffix(".keep")
        if keep.exists():
            keep.rename(env_path)


if __name__ == "__main__":
    unittest.main()
