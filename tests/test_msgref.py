"""引用原文：群友引用一条她这次看不到的旧消息时，把原文带给她（重启后也查得到）。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest
from unittest import mock

from furina.plugins.chat import kernel, msgref


class MsgRefTest(unittest.TestCase):
    def test_add_lookup_and_survives_restart(self):
        msgref.add(7001, "111", "小明", "秽土转生")
        msgref.save()
        self.assertEqual(msgref.lookup(7001, "111"), "小明：秽土转生")
        msgref._data.clear()
        msgref._loaded.clear()  # 模拟机器人重启：内存清空，从数据库读回
        self.assertEqual(msgref.lookup(7001, "111"), "小明：秽土转生")
        self.assertEqual(msgref.lookup(7001, "999"), "")

    def test_keep_limit(self):
        with mock.patch.object(msgref, "KEEP", 5):
            for i in range(9):
                msgref.add(7002, str(i), "a", f"第{i}条", ts=1000 + i)
        self.assertEqual(msgref.lookup(7002, "0"), "")
        self.assertTrue(msgref.lookup(7002, "8"))

    def test_resolve_falls_back_to_napcat(self):
        bot = mock.Mock()

        async def get_msg(message_id):
            return {"sender": {"nickname": "芙宁娜"}, "message": [{"type": "text", "data": {"text": "不"}}], "time": 1}
        bot.get_msg = get_msg
        self.assertEqual(asyncio.run(msgref.resolve(bot, 7003, "222")), "芙宁娜：不")

        async def fail(message_id):
            raise RuntimeError("获取不到带记录的引用消息")
        bot.get_msg = fail
        self.assertEqual(asyncio.run(msgref.resolve(bot, 7003, "333")), "")

    def test_fmt_shows_quote(self):
        e = {"mid": "5", "ts": 0, "uid": "123456", "name": "小瓜", "text": "知道是说谁吗", "at": True, "reply": "111",
             "quote": "小明：秽土转生"}
        self.assertIn("引用「小明：秽土转生」", kernel._fmt_entry(e))
        e["quote"] = "?"
        self.assertIn("原文看不到", kernel._fmt_entry(e))


if __name__ == "__main__":
    unittest.main()
