"""UP订阅的群命令与真正QQ发送适配层（假Bot，不碰真实QQ群）。"""
from tests import _env  # noqa: F401

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from furina.core import db, permissions
from furina.plugins import bili_pics as plug
from furina.services.bilibili import pictures as pics
from tests.test_bili_pics import FakeClient, draw, UID, IMAGE

SID, GID, USER = "30000001", 7001, 22222
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8//8/AwMDEwMDAwMDAwAkBgMB/DXemwAAAABJRU5ErkJggg==")


def event(text, *, at=True, user=USER):
    message = Message([MessageSegment.at(SID), MessageSegment.text(" " + text)]) if at else Message(text)
    return GroupMessageEvent(time=1000, self_id=int(SID), post_type="message", message_type="group", sub_type="normal", message_id=11,
        user_id=user, group_id=GID, message=message, raw_message=str(message), font=0,
        sender={"user_id": user, "nickname": "测试群友", "role": "member"})


class Bot:
    self_id = SID
    def __init__(self, online=True):
        self.sent = []
        self.online = online
    async def send_group_msg(self, **kwargs):
        self.sent.append(kwargs)
        return {"message_id": len(self.sent)}
    async def get_status(self): return {"online": self.online}
    async def get_group_list(self): return [{"group_id": GID}]


class PictureRuntimeTest(unittest.TestCase):
    def setUp(self):
        p = _env.TMP / ".env"
        self.old_env = p.read_text(encoding="utf-8") if p.exists() else ""
        _env.write_env("BILI_ENABLED=true\nBILI_PICS_ENABLED=true\nBILI_DRY_RUN=false\n")
        self.old_perm = db.get("perm", str(GID))
        self.old_halt = db.get("bili", "halt")
        db.delete("bili", "halt")
        db.delete(pics.NS, "state")
        permissions.set_feature_enabled(GID, "bili_pics", True)
        self.client = FakeClient()
        self.bot = Bot()
        self.track = Mock()
        plug._busy = False

    def tearDown(self):
        db.delete(pics.NS, "state")
        for ns, key, old in (("perm", str(GID), self.old_perm), ("bili", "halt", self.old_halt)):
            if old is None: db.delete(ns, key)
            else: db.put(ns, key, old)
        _env.write_env(self.old_env)
        plug._busy = False

    def test_only_explicit_at_commands_are_captured(self):
        self.assertTrue(plug._rule(event("订阅UP 123456")))
        self.assertFalse(plug._rule(event("订阅UP 123456", at=False)))
        self.assertFalse(plug._rule(event("这图挺好看")))
        self.assertFalse(plug._rule(event("订阅UP 123456", user=int(SID))))
        self.assertIsNone(plug.parse_command("这个UP以前的图有几百张"))
        self.assertEqual(plug.parse_command("订阅名单 第2页"), ("list", 2))

    def test_ordinary_member_can_add_to_global_roster_and_other_group_can_cancel(self):
        original = pics.subscribe
        async def subscribe(target):
            return await original(target, client=self.client, now=1000)
        with patch.object(pics, "subscribe", side_effect=subscribe):
            asyncio.run(plug.handle_command(self.bot, event("订阅UP 123456")))
        self.assertEqual(pics.snapshot()["subscriptions"][0]["uid"], UID)
        self.assertIn("所有群共用", self.bot.sent[-1]["message"].extract_plain_text())
        another = event("取消订阅 123456")
        another.group_id = 7002
        asyncio.run(plug.handle_command(self.bot, another))
        self.assertEqual(pics.snapshot()["subscriptions"], [])
        self.assertIn("所有群", self.bot.sent[-1]["message"].extract_plain_text())

    def test_turning_off_receive_does_not_create_a_different_roster(self):
        permissions.set_feature_enabled(GID, "bili_pics", False)
        self.assertTrue(plug._rule(event("订阅名单")))
        original = pics.subscribe
        async def subscribe(target): return await original(target, client=self.client, now=1000)
        with patch.object(pics, "subscribe", side_effect=subscribe):
            asyncio.run(plug.handle_command(self.bot, event("订阅UP 123456")))
        self.assertEqual(len(pics.snapshot()["subscriptions"]), 1)
        self.assertIn("本群接收关着", self.bot.sent[-1]["message"].extract_plain_text())

    def queued(self):
        pics.sync_groups({str(GID)}, now=800)
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.client.items = [draw("102", 1500)]
        asyncio.run(pics.scan_due(client=self.client, now=2300))

    def tick(self, *, load=None):
        with patch.object(plug, "_pick_bot", return_value=self.bot), \
             patch.object(pics, "scan_due", new=AsyncMock(return_value={"checked": 0})), \
             patch.object(pics, "load_image", new=load or AsyncMock(return_value=PNG)), \
             patch.object(pics, "prune_cache"), patch.object(plug.usage, "track", new=self.track), \
             patch.object(plug.time, "time", return_value=2301):
            asyncio.run(plug.tick())

    def test_actual_message_is_only_up_name_and_images_and_restart_does_not_repeat(self):
        self.queued()
        self.tick()
        self.assertEqual(len(self.bot.sent), 1)
        message = self.bot.sent[0]["message"]
        self.assertEqual(message.extract_plain_text(), "测试UP")
        self.assertEqual([part.type for part in message], ["text", "image"])
        self.assertNotIn("bilibili.com", message.extract_plain_text())
        self.tick()
        self.assertEqual(len(self.bot.sent), 1)

    def test_no_receipt_is_not_counted_as_success(self):
        self.queued()
        self.bot.send_group_msg = AsyncMock(return_value=None)
        self.tick()
        self.track.assert_not_called()
        self.assertEqual(pics.snapshot(now=2301)["uncertain"], 1)

    def test_qq_offline_keeps_pending_and_never_claims_delivered(self):
        self.queued()
        self.bot.online = False
        self.tick()
        self.assertEqual(self.bot.sent, [])
        self.assertEqual(pics.snapshot(now=2301)["pending"], 1)

    def test_cancelling_while_downloading_prevents_actual_group_send(self):
        self.queued()
        async def download(_):
            pics.unsubscribe(UID)
            return PNG
        self.tick(load=download)
        self.assertEqual(self.bot.sent, [])
        self.assertEqual(pics.snapshot()["subscriptions"], [])


if __name__ == "__main__":
    unittest.main()
