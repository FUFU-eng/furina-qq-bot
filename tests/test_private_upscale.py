"""主人私聊发图自动强化：触发规则（纯规则判定，不启动 GPU）。"""
from tests import _env  # noqa: F401  (must be first)

import time
import unittest

from nonebot.adapters.onebot.v11 import Message, MessageSegment, PrivateMessageEvent

from furina.core import env
from furina.plugins.upscale.private import is_owner_private_image, private_images

OWNER = "10001"
BOT = "20002"


def pm(user: str, message: Message) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=int(time.time()), self_id=int(BOT), post_type="message", sub_type="friend",
        user_id=int(user), message_type="private", message_id=1, message=message,
        original_message=message, raw_message=str(message), font=0,
        sender={"user_id": int(user), "nickname": "x"},
    )


def img(sub_type: int = 0, summary: str = "") -> MessageSegment:
    return MessageSegment("image", {"file": "a.jpg", "url": "http://x/a.jpg", "sub_type": sub_type, "summary": summary})


class PrivateUpscaleTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(f"BOT_QQ={BOT}\nAI_CHAT_GROUP_OWNER_QQ={OWNER}\n")

    def test_owner_image_triggers(self):
        self.assertTrue(is_owner_private_image(pm(OWNER, Message([img()]))))
        self.assertTrue(is_owner_private_image(pm(OWNER, Message([MessageSegment.text("看看"), img()]))))
        self.assertEqual(len(private_images(pm(OWNER, Message([img(), img()])))), 2)

    def test_others_and_stickers_ignored(self):
        self.assertFalse(is_owner_private_image(pm("30003", Message([img()]))))
        self.assertFalse(is_owner_private_image(pm(OWNER, Message([img(sub_type=1)]))))
        self.assertFalse(is_owner_private_image(pm(OWNER, Message([img(summary="[动画表情]")]))))
        self.assertFalse(is_owner_private_image(pm(OWNER, Message([MessageSegment.text("在吗")]))))

    def test_bot_qq_falls_back_to_env_when_offline(self):
        self.assertEqual(env.bot_qq(), BOT)


if __name__ == "__main__":
    unittest.main()
