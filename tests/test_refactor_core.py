"""重构后的核心件：只读缓存失效、按事件绑定机器人号、Pixiv 点图去重记录入库。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from furina.core import botref, db, env, paths, permissions


class CachedGetTest(unittest.TestCase):
    def test_local_and_external_writes_invalidate(self):
        db.put("perm", "777001", {"enabled": True})
        self.assertTrue(permissions.get_group("777001")["enabled"])
        db.put("perm", "777001", {"enabled": False})  # 本进程写
        self.assertFalse(permissions.get_group("777001")["enabled"])
        # 另一个连接（相当于面板进程）写入
        other = sqlite3.connect(str(paths.DB_PATH), timeout=10, isolation_level=None)
        other.execute("UPDATE docs SET value=? WHERE ns='perm' AND key='777001'", (json.dumps({"enabled": True}),))
        other.close()
        self.assertTrue(permissions.get_group("777001")["enabled"])

    def test_missing_key_default(self):
        self.assertEqual(db.get_cached("perm", "no-such", {"x": 1}), {"x": 1})


class BindBotTest(unittest.TestCase):
    def test_bot_qq_follows_bound_event(self):
        bots = {"111": object(), "222": object()}

        async def handle(sid):
            botref.bind(self_id=sid)
            await asyncio.sleep(0)
            return env.bot_qq()

        async def main():
            return await asyncio.gather(handle("111"), handle("222"))

        with mock.patch("nonebot.get_bots", return_value=bots):
            self.assertEqual(asyncio.run(main()), ["111", "222"])
            self.assertIs(botref.bot_for(self_id="222"), bots["222"])


class PixivRecentTest(unittest.TestCase):
    def test_legacy_json_imported_and_trimmed(self):
        from furina.services.pixiv.storage import Store

        root = Path(tempfile.mkdtemp())
        (root / "search_recent.json").write_text(json.dumps({"5": ["1", "2", "3"]}), encoding="utf-8")
        s = Store(root)
        self.assertEqual(s.recent_search_pids(5), ["1", "2", "3"])
        s.remember_search_pids(5, ["2", "9"])
        self.assertEqual(s.recent_search_pids(5), ["1", "3", "2", "9"])
        s.remember_search_pids(6, [str(i) for i in range(130)])
        self.assertEqual(len(s.recent_search_pids(6, 500)), 120)
        self.assertFalse((root / "search_recent.json").exists())


if __name__ == "__main__":
    unittest.main()
