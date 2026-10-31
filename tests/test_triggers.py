"""搜图 / 强化 / Pixiv 点图触发规则与旧版一致（纯规则判定，不启动浏览器和 GPU）。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest

from tests.test_chat_flow import BOT, make_event

from furina.plugins.image_search import _check_trigger as search_trigger, _REPLY_RE as search_reply_re
from furina.plugins.upscale import _check_trigger as upscale_trigger, _REPLY_RE as upscale_reply_re
from furina.plugins.pixiv import _rule as pixiv_rule
from furina.plugins.chat.handlers import _REPLY_RE as chat_reply_re


class TriggerTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(f"BOT_QQ={BOT}\n")

    def test_search_requires_reply_at_and_word(self):
        ok = make_event(f"[CQ:reply,id=5][CQ:at,qq={BOT}] 搜图")
        self.assertTrue(asyncio.run(search_trigger(ok)))
        self.assertFalse(asyncio.run(search_trigger(make_event(f"[CQ:at,qq={BOT}] 搜图"))))
        self.assertFalse(asyncio.run(search_trigger(make_event("[CQ:reply,id=5] 搜图"))))
        self.assertTrue(asyncio.run(search_trigger(make_event("[CQ:reply,id=5]@芙芙 帮我找一下图"))))

    def test_search_accepts_negative_reply_id(self):
        ev = make_event(f"[CQ:reply,id=-17][CQ:at,qq={BOT}] 搜图")
        self.assertTrue(asyncio.run(search_trigger(ev)))
        # SnowLuma 等现代 OneBot 消息段可省略 CQ: 前缀。
        self.assertIsNotNone(search_reply_re.search("[reply:id=-17]"))

    def test_upscale(self):
        self.assertTrue(upscale_trigger(make_event(f"[CQ:reply,id=5][CQ:at,qq={BOT}] 强化")))
        self.assertTrue(upscale_trigger(make_event(f"[CQ:image,file=a.jpg,url=http://x/a.jpg][CQ:at,qq={BOT}] 强化一下")))
        self.assertFalse(upscale_trigger(make_event(f"[CQ:at,qq={BOT}] 强化")))

    def test_upscale_accepts_negative_reply_id(self):
        self.assertTrue(upscale_trigger(make_event(f"[CQ:reply,id=-17][CQ:at,qq={BOT}] 强化")))
        # Directly test the modern raw-message form: Message(text) escapes its brackets in this fixture.
        self.assertIsNotNone(upscale_reply_re.search("[reply:id=-17]"))

    def test_chat_reply_detection_accepts_negative_reply_id(self):
        self.assertIsNotNone(chat_reply_re.search("[CQ:reply,id=-17]"))
        self.assertIsNotNone(chat_reply_re.search("[reply:id=-17]"))

    def test_pixiv_named_request(self):
        ev = make_event(f"[CQ:at,qq={BOT}] 来两张芙宁娜的图")
        self.assertTrue(pixiv_rule(ev))
        self.assertFalse(pixiv_rule(make_event(f"[CQ:at,qq={BOT}] 来张图")))  # 无名称 → Eagle 发图
        self.assertFalse(pixiv_rule(make_event(f"[CQ:reply,id=3][CQ:at,qq={BOT}] 来两张芙宁娜的图")))


if __name__ == "__main__":
    unittest.main()
