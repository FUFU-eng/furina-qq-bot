"""全局UP名单、新图基线、每群去重：只测公开服务行为，不触碰真实B站/QQ群。"""
from tests import _env  # noqa: F401

import asyncio
import copy
import importlib
import unittest
from unittest.mock import AsyncMock

from furina.core import db
from furina.services.bilibili import pictures as pics

UID = "123456"
GROUPS = {"7001", "7002"}
IMAGE = "https://i0.hdslb.com/bfs/new_dyn/example.jpg"


def draw(mid="101", at=900, *, opus=True, uid=UID):
    major = {"type": "MAJOR_TYPE_OPUS", "opus": {"pics": [{"url": IMAGE}], "summary": {"text": "画师署名格式随意 https://t.bilibili.com/101"}}} if opus else {"type": "MAJOR_TYPE_DRAW", "draw": {"items": [{"src": IMAGE}]}}
    return {"id_str": mid, "type": "DYNAMIC_TYPE_DRAW", "modules": {"module_author": {"mid": uid, "name": "测试UP", "pub_ts": at}, "module_dynamic": {"major": major}}}


class FakeClient:
    def __init__(self, items=None):
        self.items = items if items is not None else [draw()]
        self.cards = 0
        self.reads = 0

    async def user_card(self, mid):
        self.cards += 1
        return {"mid": str(mid), "name": "测试UP"}

    async def user_dynamics(self, mid, offset=""):
        self.reads += 1
        return {"items": copy.deepcopy(self.items), "offset": "", "has_more": False}


class PictureCase(unittest.TestCase):
    def setUp(self):
        p = _env.TMP / ".env"
        self.old_env = p.read_text(encoding="utf-8") if p.exists() else ""
        _env.write_env("BILI_ENABLED=true\nBILI_PICS_ENABLED=true\nBILI_PICS_INTERVAL_MINUTES=20\nBILI_DRY_RUN=false\n")
        self.old_halt = db.get("bili", "halt")
        db.delete("bili", "halt")
        db.delete(pics.NS, "state")
        self.client = FakeClient()
        pics.sync_groups(GROUPS, now=800)

    def tearDown(self):
        db.delete(pics.NS, "state")
        if self.old_halt is None:
            db.delete("bili", "halt")
        else:
            db.put("bili", "halt", self.old_halt)
        _env.write_env(self.old_env)


class PictureSubscriptionsTest(PictureCase):
    def test_parse_uid_and_profile_url_without_guessing_a_nickname(self):
        self.assertEqual(pics.parse_uid(UID), UID)
        self.assertEqual(pics.parse_uid("https://space.bilibili.com/123456?spm_id_from=abc"), UID)
        for raw in ("0", "-1", "某画师", "https://evil.example/123456", "http://127.0.0.1/123456"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                pics.parse_uid(raw)

    def test_one_shared_roster_and_initial_subscription_sends_no_history(self):
        result = asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.assertTrue(result["created"])
        snap = pics.snapshot(now=1000)
        self.assertEqual([(r["uid"], r["name"]) for r in snap["subscriptions"]], [(UID, "测试UP")])
        self.assertEqual(snap["pending"], 0)
        sender = AsyncMock(return_value={"message_id": 1})
        asyncio.run(pics.dispatch(sender, GROUPS, now=1001))
        sender.assert_not_awaited()

    def test_duplicate_subscription_does_not_reset_tracking(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        result = asyncio.run(pics.subscribe("https://space.bilibili.com/123456", client=self.client, now=1100))
        self.assertFalse(result["created"])
        self.assertEqual(len(pics.snapshot()["subscriptions"]), 1)
        self.assertEqual(self.client.cards, 1)

    def test_new_picture_reaches_each_group_once_and_only_has_up_name(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.client.items = [draw("102", 1500), draw()]
        asyncio.run(pics.scan_due(client=self.client, now=2300))
        seen = []
        async def send(job, gid):
            seen.append((gid, job["caption"], job["images"]))
            return {"message_id": len(seen)}
        result = asyncio.run(pics.dispatch(send, GROUPS, now=2300))
        self.assertEqual(result["sent"], 2)
        self.assertEqual({g for g, _, _ in seen}, GROUPS)
        self.assertTrue(all(caption == "测试UP" for _, caption, _ in seen))
        self.assertTrue(all(urls == [IMAGE] for _, _, urls in seen))
        importlib.reload(pics)  # 模拟程序重启，不许丢失每群成功回执
        asyncio.run(pics.dispatch(send, GROUPS, now=2400))
        self.assertEqual(len(seen), 2)

    def test_video_cover_forwarded_post_and_unrelated_author_are_not_art_updates(self):
        video = draw(); video["type"] = "DYNAMIC_TYPE_AV"
        forward = draw(); forward["type"] = "DYNAMIC_TYPE_FORWARD"
        for row in (video, forward, draw(uid="999999")):
            self.assertIsNone(pics.extract_post(row, UID))
        self.assertEqual(pics.extract_post(draw(opus=False), UID)["images"], [IMAGE])

    def test_unsubscribe_is_global_and_drops_pending_images(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.client.items = [draw("102", 1500)]
        asyncio.run(pics.scan_due(client=self.client, now=2300))
        self.assertTrue(pics.unsubscribe(UID))
        self.assertEqual(pics.snapshot()["subscriptions"], [])
        sender = AsyncMock()
        asyncio.run(pics.dispatch(sender, GROUPS, now=2400))
        sender.assert_not_awaited()

    def test_many_images_are_split_without_loss_and_group_is_rate_limited(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        item = draw("102", 1500)
        item["modules"]["module_dynamic"]["major"]["opus"]["pics"] = [{"url": f"https://i0.hdslb.com/bfs/new_dyn/image-{n}.png"} for n in range(12)]
        self.client.items = [item]
        asyncio.run(pics.scan_due(client=self.client, now=2300))
        seen = []
        async def send(job, gid):
            seen.extend(job["images"])
            return {"message_id": len(seen)}
        asyncio.run(pics.dispatch(send, {"7001"}, now=2300))
        asyncio.run(pics.dispatch(send, {"7001"}, now=2301))
        self.assertEqual(len(seen), 9)
        asyncio.run(pics.dispatch(send, {"7001"}, now=2361))
        self.assertEqual(len(set(seen)), 12)

    def test_picture_switch_and_risk_halt_pause_queued_delivery(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.client.items = [draw("102", 1500)]
        asyncio.run(pics.scan_due(client=self.client, now=2300))
        sender = AsyncMock(return_value={"message_id": 1})
        _env.write_env("BILI_ENABLED=true\nBILI_PICS_ENABLED=false\n")
        asyncio.run(pics.dispatch(sender, GROUPS, now=2300))
        sender.assert_not_awaited()
        _env.write_env("BILI_ENABLED=true\nBILI_PICS_ENABLED=true\n")
        from furina.services.bilibili import store
        store.halt("测试风控", 24)
        asyncio.run(pics.dispatch(sender, GROUPS, now=2400))
        sender.assert_not_awaited()

    def test_subscription_is_independent_of_autobrowsing_and_social_dryrun(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.client.items = [draw("102", 1500)]
        _env.write_env("BILI_ENABLED=false\nBILI_DRY_RUN=true\nBILI_PICS_ENABLED=true\n")
        result = asyncio.run(pics.scan_due(client=self.client, now=2300))
        self.assertEqual(result["checked"], 1)
        sender = AsyncMock(return_value={"message_id": 1})
        asyncio.run(pics.dispatch(sender, GROUPS, now=2301))
        self.assertEqual(sender.await_count, 2)

    def test_timeout_is_uncertain_not_automatically_retried(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.client.items = [draw("102", 1500)]
        asyncio.run(pics.scan_due(client=self.client, now=2300))
        sender = AsyncMock(side_effect=TimeoutError("unknown remote result"))
        asyncio.run(pics.dispatch(sender, {"7001"}, now=2300))
        asyncio.run(pics.dispatch(sender, {"7001"}, now=3000))
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(pics.snapshot(now=3000)["uncertain"], 1)


if __name__ == "__main__":
    unittest.main()
