"""画师订阅的面板公开接口、权限与设置同步。"""
from tests import _env  # noqa: F401

from pathlib import Path
import unittest
from unittest.mock import patch
from furina.services.bilibili import pictures
from tests.test_bili_pics import FakeClient
from fastapi.testclient import TestClient
from furina.core import db, permissions
from panel import server
from panel.services import data


class PicturePanelTest(unittest.TestCase):
    def setUp(self):
        p = _env.TMP / ".env"
        self.old_env = p.read_text(encoding="utf-8") if p.exists() else ""
        _env.write_env("BILI_ENABLED=true\nBILI_PICS_ENABLED=true\nBILI_PICS_INTERVAL_MINUTES=20\n")
        db.delete("bili_pics", "state")
        self.old_halt = db.get("bili", "halt")
        db.delete("bili", "halt")
        self.client = TestClient(server.create_app())
        self.headers = {"x-panel-token": server.TOKEN}

    def tearDown(self):
        self.client.close()
        db.delete("bili_pics", "state")
        if self.old_halt is None: db.delete("bili", "halt")
        else: db.put("bili", "halt", self.old_halt)
        _env.write_env(self.old_env)

    def test_public_settings_are_global_and_hot_saved(self):
        r = self.client.get("/api/bili-pics", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["shared"])
        self.assertEqual(r.json()["subscriptions"], [])
        r = self.client.post("/api/bili-pics/settings", headers=self.headers, json={"enabled": False, "interval_minutes": 30})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])
        self.assertEqual(r.json()["interval_minutes"], 30)
        fields = self.client.get("/api/envgroup/bili", headers=self.headers).json()["fields"]
        values = {field["key"]: field["value"] for field in fields}
        self.assertFalse(values["BILI_PICS_ENABLED"])
        self.assertEqual(values["BILI_PICS_INTERVAL_MINUTES"], 30)

    def test_api_requires_panel_token(self):
        self.assertEqual(self.client.get("/api/bili-pics").status_code, 401)

    def test_settings_validation_and_uncertain_retry_require_real_confirmation(self):
        for body in ({"enabled": "false"}, {"interval_minutes": 4}, {"interval_minutes": True}, {"unexpected": 1}):
            r = self.client.post("/api/bili-pics/settings", headers=self.headers, json=body)
            self.assertEqual(r.status_code, 400)
        row = self.client.get("/api/bili-pics", headers=self.headers).json()
        self.assertTrue(row["enabled"])
        self.assertEqual(row["interval_minutes"], 20)
        self.assertEqual(self.client.post("/api/bili-pics/retry", headers=self.headers, json={"confirmed": "true"}).status_code, 400)
        self.assertEqual(self.client.post("/api/bili-pics/retry", headers=self.headers, json={"confirmed": True}).status_code, 200)

    def test_api_add_and_delete_use_the_same_global_roster(self):
        original = pictures.subscribe
        async def subscribe(target):
            return await original(target, client=FakeClient(), now=1000)
        with patch.object(pictures, "subscribe", side_effect=subscribe):
            r = self.client.post("/api/bili-pics/subscriptions", headers=self.headers, json={"target": "123456"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["created"])
        row = self.client.get("/api/bili-pics", headers=self.headers).json()
        self.assertEqual([up["uid"] for up in row["subscriptions"]], ["123456"])
        self.assertTrue(self.client.delete("/api/bili-pics/subscriptions/123456", headers=self.headers).json()["removed"])
        self.assertEqual(self.client.get("/api/bili-pics", headers=self.headers).json()["subscriptions"], [])

    def test_permission_web_api_and_classic_have_same_feature(self):
        self.assertEqual(permissions.FEATURES.get("bili_pics"), "B站画师图片")
        self.assertIn("bili_pics", permissions.DEFAULT_ON_FEATURES)
        self.assertIn("bili_pics", {key for key, _, _ in data.KINDS})
        root = Path(__file__).resolve().parents[1]
        web = (root / "panel/web/app.js").read_text(encoding="utf-8")
        classic = (root / "panel/classic/pages/bili.py").read_text(encoding="utf-8")
        self.assertIn("B站画师图片", web)
        self.assertIn("bili_pics", web)
        self.assertIn("_bili_pics", classic)

    def test_history_endpoint_is_not_provided(self):
        self.assertEqual(self.client.get("/api/bili-pics/history", headers=self.headers).status_code, 404)


if __name__ == "__main__":
    unittest.main()
