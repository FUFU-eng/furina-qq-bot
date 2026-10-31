"""故障/重启/取消/翻页与下载边界。观察名单、回执与发送结果，不查实现私有字段。"""
from tests import _env  # noqa: F401

import asyncio
import base64
import copy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from furina.services.bilibili import pictures as pics, store
from furina.services.bilibili.client import Client, RiskControl
from tests.test_bili_pics import PictureCase, FakeClient, draw, UID, GROUPS, IMAGE

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8//8/AwMDEwMDAwMDAwAkBgMB/DXemwAAAABJRU5ErkJggg==")


class PictureEdgesTest(PictureCase):
    def queued(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.client.items = [draw("102", 1500), draw()]
        asyncio.run(pics.scan_due(client=self.client, now=2300))

    def test_offline_does_not_lose_pending_delivery(self):
        self.queued()
        sender = AsyncMock(return_value={"message_id": 1})
        asyncio.run(pics.dispatch(sender, GROUPS, now=2301, online=False))
        sender.assert_not_awaited()
        self.assertEqual(pics.snapshot(now=2301)["pending"], 2)
        asyncio.run(pics.dispatch(sender, GROUPS, now=2400, online=True))
        self.assertEqual(sender.await_count, 2)

    def test_reenabled_group_does_not_receive_its_old_backlog(self):
        self.queued()
        pics.sync_groups({"7001"}, now=2400)
        pics.sync_groups(GROUPS, now=2500)
        sender = AsyncMock(return_value={"message_id": 1})
        asyncio.run(pics.dispatch(sender, {"7002"}, now=2501))
        sender.assert_not_awaited()
        self.client.items = [draw("103", 2600), draw("102", 1500)]
        pics.make_due()
        asyncio.run(pics.scan_due(client=self.client, now=2700))
        asyncio.run(pics.dispatch(sender, {"7002"}, now=2701))
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(sender.await_args.args[0]["post_id"], "103")

    def test_new_group_is_not_given_old_jobs(self):
        self.queued()
        pics.sync_groups(GROUPS | {"7003"}, now=2400)
        sender = AsyncMock()
        asyncio.run(pics.dispatch(sender, {"7003"}, now=2401))
        sender.assert_not_awaited()

    def test_resubscribe_does_not_resurrect_cancelled_jobs(self):
        self.queued()
        pics.unsubscribe(UID)
        asyncio.run(pics.subscribe(UID, client=self.client, now=2500))
        pics.make_due()
        asyncio.run(pics.scan_due(client=self.client, now=4000))
        sender = AsyncMock()
        asyncio.run(pics.dispatch(sender, GROUPS, now=4001))
        sender.assert_not_awaited()

    def test_unsubscribe_during_a_feed_request_is_not_undone_by_its_response(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        async def response(*args, **kwargs):
            pics.unsubscribe(UID)
            return {"items": [draw("102", 1500)], "has_more": False, "offset": ""}
        self.client.user_dynamics = response
        asyncio.run(pics.scan_due(client=self.client, now=2300))
        self.assertEqual(pics.snapshot()["subscriptions"], [])
        sender = AsyncMock()
        asyncio.run(pics.dispatch(sender, GROUPS, now=2301))
        sender.assert_not_awaited()

    def test_wrong_profile_or_ambiguous_name_does_not_change_the_wrong_up(self):
        bad = FakeClient()
        bad.user_card = AsyncMock(return_value={"mid": "999999", "name": "测试UP"})
        with self.assertRaises(ValueError):
            asyncio.run(pics.subscribe(UID, client=bad, now=1000))
        self.assertEqual(pics.snapshot()["subscriptions"], [])
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        asyncio.run(pics.subscribe("654321", client=self.client, now=1000))
        with self.assertRaises(ValueError): pics.unsubscribe("测试UP")
        self.assertEqual(len(pics.snapshot()["subscriptions"]), 2)

    def test_risk_control_halts_requests_instead_of_retrying(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        self.client.user_dynamics = AsyncMock(side_effect=RiskControl(-352, "test"))
        asyncio.run(pics.scan_due(client=self.client, now=2300))
        self.assertTrue(store.halted()[0])
        pics.make_due()
        asyncio.run(pics.scan_due(client=self.client, now=2400))
        self.assertEqual(self.client.user_dynamics.await_count, 1)

    def test_known_failure_retries_with_backoff_and_success_is_not_repeated(self):
        self.queued()
        sender = AsyncMock(side_effect=[pics.DeliveryRejected("not sent"), {"message_id": 2}])
        asyncio.run(pics.dispatch(sender, {"7001"}, now=2300))
        asyncio.run(pics.dispatch(sender, {"7001"}, now=2400))
        self.assertEqual(sender.await_count, 1)
        asyncio.run(pics.dispatch(sender, {"7001"}, now=2500))
        asyncio.run(pics.dispatch(sender, {"7001"}, now=2600))
        self.assertEqual(sender.await_count, 2)

    def test_no_receipt_is_unknown_and_explicit_retry_is_required(self):
        self.queued()
        sender = AsyncMock(return_value=None)
        asyncio.run(pics.dispatch(sender, {"7001"}, now=2300))
        asyncio.run(pics.dispatch(sender, {"7001"}, now=3000))
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(pics.snapshot(now=3000)["uncertain"], 1)
        self.assertEqual(pics.retry_unconfirmed(now=3000), 1)
        sender.return_value = {"message_id": 2}
        asyncio.run(pics.dispatch(sender, {"7001"}, now=3100))
        self.assertEqual(sender.await_count, 2)

    def test_hard_process_exit_during_send_does_not_blindly_repeat_after_restart(self):
        self.queued()
        code = "import asyncio,os; from furina.services.bilibili import pictures as p\nasync def send(job,gid): os._exit(0)\nasyncio.run(p.dispatch(send, {'7001'}, now=2300))\n"
        child_env = dict(os.environ, PYTHONPATH=str(_env.ROOT), PYTHONUTF8="1")
        p = subprocess.run([sys.executable, "-c", code], cwd=str(_env.TMP), env=child_env, capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(p.returncode, 0, p.stderr.decode(errors="replace"))
        sender = AsyncMock()
        asyncio.run(pics.dispatch(sender, {"7001"}, now=3000))
        sender.assert_not_awaited()
        self.assertEqual(pics.snapshot(now=3000)["uncertain"], 1)

    def test_confirmed_manual_retry_can_recover_an_expired_inflight_send(self):
        self.queued()
        code = "import asyncio,os; from furina.services.bilibili import pictures as p\nasync def send(job,gid): os._exit(0)\nasyncio.run(p.dispatch(send, {'7001'}, now=2300))\n"
        child_env = dict(os.environ, PYTHONPATH=str(_env.ROOT), PYTHONUTF8="1")
        child = subprocess.run([sys.executable, "-c", code], cwd=str(_env.TMP), env=child_env, capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(child.returncode, 0)
        self.assertEqual(pics.snapshot(now=3000)["uncertain"], 1)
        self.assertEqual(pics.retry_unconfirmed(now=3000), 1)
        sender = AsyncMock(return_value={"message_id": 9})
        asyncio.run(pics.dispatch(sender, {"7001"}, now=3100))
        self.assertEqual(sender.await_count, 1)

    def test_pinned_old_entry_does_not_hide_new_pictures_on_the_next_page(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        calls = []
        async def feed(mid, offset=""):
            calls.append(offset)
            return {"items": [draw(), draw("102", 1500)], "has_more": True, "offset": "next"} if not offset else {"items": [draw("103", 1400), draw("90", 800)], "has_more": False, "offset": ""}
        self.client.user_dynamics = feed
        with patch.object(pics.asyncio, "sleep", new=AsyncMock()):
            asyncio.run(pics.scan_due(client=self.client, now=2300))
        self.assertEqual(calls, ["", "next"])
        sender = AsyncMock(return_value={"message_id": 1})
        asyncio.run(pics.dispatch(sender, {"7001"}, now=2301))
        asyncio.run(pics.dispatch(sender, {"7001"}, now=2362))
        self.assertEqual({r.args[0]["post_id"] for r in sender.await_args_list}, {"102", "103"})

    def test_scan_page_budget_resumes_instead_of_fetching_old_history_forever(self):
        asyncio.run(pics.subscribe(UID, client=self.client, now=1000))
        calls = []
        async def feed(mid, offset=""):
            n = int(offset or 0)
            calls.append(n)
            return {"items": [draw(str(200 + n), 1800 - n)], "has_more": n < 3, "offset": str(n + 1)}
        self.client.user_dynamics = feed
        with patch.object(pics.asyncio, "sleep", new=AsyncMock()):
            asyncio.run(pics.scan_due(client=self.client, now=2300))
            self.assertEqual(calls, [0, 1, 2])
            asyncio.run(pics.scan_due(client=self.client, now=2361))
        self.assertEqual(calls, [0, 1, 2, 3])
        self.assertEqual(pics.snapshot(now=2362)["pending"], 8)

    def test_photo_urls_are_official_https_and_never_accept_private_urls(self):
        self.assertEqual(pics.image_url("http://i0.hdslb.com/bfs/test.jpg@200w.webp"), "https://i0.hdslb.com/bfs/test.jpg")
        for value in ("http://127.0.0.1/p.png", "https://i0.hdslb.com.evil.example/p.png", "file:///secret.png", "https://user:password@i0.hdslb.com/p.png", "https://i0.hdslb.com:22/a.png"):
            self.assertEqual(pics.image_url(value), "")

    def test_image_download_has_no_auth_cookies_and_does_not_follow_redirects(self):
        calls = []
        def respond(request):
            calls.append(request)
            self.assertEqual(request.headers.get("cookie", ""), "")
            if request.url.path.endswith("redirect.png"):
                return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
            return httpx.Response(200, content=PNG)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond), cookies={"SESSDATA": "test-cookie"}, follow_redirects=True) as client:
                data = await pics.load_image(IMAGE.replace("example.jpg", "download.png"), client=client)
                self.assertEqual(data, PNG)
                with self.assertRaises(pics.DeliveryRejected):
                    await pics.load_image(IMAGE.replace("example.jpg", "redirect.png"), client=client)
        asyncio.run(run())
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(r.url.scheme == "https" for r in calls))

    def test_client_uses_uid_specific_feed_and_preserves_pagination(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"code": 0, "data": {"items": [draw()], "offset": "123", "has_more": True}})
        actual = httpx.AsyncClient
        async def run():
            with patch("httpx.AsyncClient", side_effect=lambda **kwargs: actual(transport=httpx.MockTransport(respond), **kwargs)):
                async with Client(cookies={}) as client:
                    row = await client.user_dynamics(UID, offset="prev")
                    self.assertEqual(row["offset"], "123")
                    self.assertTrue(row["has_more"])
        asyncio.run(run())
        self.assertEqual(calls[0].url.path, "/x/polymer/web-dynamic/v1/feed/space")
        self.assertEqual(calls[0].url.params["host_mid"], UID)
        self.assertEqual(calls[0].url.params["offset"], "prev")

    def test_subscription_scanning_and_delivery_never_call_a_chat_model(self):
        with patch("furina.core.llm.chat", new=AsyncMock(side_effect=AssertionError("no model needed"))) as model:
            self.queued()
            asyncio.run(pics.dispatch(AsyncMock(return_value={"message_id": 1}), GROUPS, now=2301))
            model.assert_not_awaited()

    def test_malformed_post_shapes_never_turn_covers_into_photos(self):
        for row in (None, [], {"type": "DYNAMIC_TYPE_DRAW"}, {"id_str": "1", "type": "DYNAMIC_TYPE_DRAW", "modules": []}):
            self.assertIsNone(pics.extract_post(row, UID))


if __name__ == "__main__":
    unittest.main()
