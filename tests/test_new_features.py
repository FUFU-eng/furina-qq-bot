"""2026-10-30 新功能：查梗 / 做文件 —— 四处同步 + 基本行为；点歌、QQ空间、删除我的数据已按主人要求删掉。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest
from pathlib import Path
from unittest import mock

from furina.core import permissions
from furina.services import maker, memewiki
from panel.services import data as pdata

ROOT = Path(__file__).resolve().parents[1]
NEW = {"memewiki": True, "maker": True}
GONE = (("music", "点歌", "music_share"), ("qzone", "QQ空间", "qzone_"), ("wipe", "删除我的数据", "wipe"))


class SyncTest(unittest.TestCase):
    def test_four_places(self):
        js = (ROOT / "panel" / "web" / "app.js").read_text(encoding="utf-8")
        bridge = (ROOT / "tools" / "dsh_bridge" / "furina-bridge.mjs").read_text(encoding="utf-8")
        kinds = [k for k, _, _ in pdata.KINDS]
        for key, on in NEW.items():
            self.assertIn(key, permissions.FEATURES)
            self.assertEqual(key in permissions.DEFAULT_ON_FEATURES, on, key)
            self.assertIn(key, kinds)
            self.assertIn(permissions.FEATURES[key], js)
        for tool in ("meme_lookup", "make_file", "remind_later"):
            self.assertIn(f"['{tool}'", bridge)

    def test_removed_stay_removed(self):
        js = (ROOT / "panel" / "web" / "app.js").read_text(encoding="utf-8")
        bridge = (ROOT / "tools" / "dsh_bridge" / "furina-bridge.mjs").read_text(encoding="utf-8")
        kinds = [k for k, _, _ in pdata.KINDS]
        for key, label, tool in GONE:
            self.assertNotIn(key, permissions.FEATURES)
            self.assertNotIn(key, kinds)
            self.assertNotIn(label, js)
            self.assertNotIn(tool, bridge)
        for rel in ("furina/services/music163.py", "furina/services/qzone.py", "furina/plugins/qzone",
                    "furina/plugins/chat/wipe.py"):
            self.assertFalse((ROOT / rel).exists(), rel)


class MemeTest(unittest.TestCase):
    def test_clean_and_local_first(self):
        self.assertEqual(memewiki.clean_term("哈基米是什么梗"), "哈基米")

        async def no_net(*_a, **_k):
            raise AssertionError("本子里有就不该上网")
        with mock.patch.object(memewiki, "_local", return_value="猫"), mock.patch.object(memewiki, "moegirl", no_net):
            r = asyncio.run(memewiki.lookup("哈基米是什么梗"))
        self.assertEqual((r["ok"], r["source"]), (True, "梗本子"))

    def test_web_fallback_cached(self):
        async def none(*_a, **_k):
            return ""

        async def web(q, gid):
            return "某个梗的解释。后面的话"
        with mock.patch.object(memewiki, "_local", return_value=""), mock.patch.object(memewiki, "moegirl", none):
            r = asyncio.run(memewiki.lookup("测试梗甲", 1, web=web))
            self.assertEqual(r["source"], "联网")
            r2 = asyncio.run(memewiki.lookup("测试梗甲", 1, web=None))
        self.assertEqual(r2["source"], "缓存")


class MakerTest(unittest.TestCase):
    def test_name_and_cap(self):
        self.assertEqual(maker.safe_name('a/b:c*?', "md"), "abc.md")
        self.assertEqual(maker.safe_name("", "zzz"), "文件.md")
        _env.write_env("AI_CHAT_MAKER_DAILY=1\n")

        async def fake(*_a, **_k):
            return "# 标题\n\n这是一份认真写好的文件正文，内容足够长。"
        with mock.patch.object(maker.llm, "chat", fake):
            r = asyncio.run(maker.make(555, "清单", "帮我列一个出游清单"))
            self.assertTrue(r["ok"])
            self.assertTrue(Path(r["path"]).is_file())
            r2 = asyncio.run(maker.make(555, "清单", "再来一份"))
        self.assertFalse(r2["ok"])
        _env.write_env("")  # 默认 0 = 不限
        self.assertEqual(maker.daily_cap(), 0)
        with mock.patch.object(maker.llm, "chat", fake):
            self.assertTrue(asyncio.run(maker.make(555, "清单", "第三份也能做"))["ok"])


if __name__ == "__main__":
    unittest.main()
