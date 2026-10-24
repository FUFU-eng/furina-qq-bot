from tests import _env  # noqa: F401  (must be first)
from tests._classic_src import classic_panel_source

import unittest

from fastapi.testclient import TestClient

from furina.core import db, docfile
from panel import server
from panel.services import envfile, napcat, wallpapers
from panel.services import data as pdata


class PanelServiceTest(unittest.TestCase):
    def setUp(self):
        _env.write_env("# 注释保留\nBOT_QQ=111\nAI_CHAT_DAILY_CHAT_LIMIT=30\nAI_CHAT_GROUP_OWNER_QQ=9\n")
        napcat.groups.clear()

    def test_envfile_preserves_other_lines(self):
        envfile.set_values({"AI_CHAT_DAILY_CHAT_LIMIT": "12", "NEW_KEY": "x"})
        text = (_env.TMP / ".env").read_text(encoding="utf-8")
        self.assertIn("# 注释保留", text)
        self.assertIn("AI_CHAT_DAILY_CHAT_LIMIT=12", text)
        self.assertEqual(envfile.get("NEW_KEY"), "x")

    def test_user_limit_roundtrip(self):
        self.assertEqual(pdata.save_user_limit("7001", "42", "custom", "group", 5), "5 次/天（仅本群）")
        self.assertEqual(pdata.user_limit("7001", "42")["effective"], 5)
        pdata.save_user_limit("7001", "42", "unlimited", "all", 0)
        info = pdata.user_limit("7002", "42")
        self.assertEqual((info["mode"], info["scope"]), ("unlimited", "all"))
        pdata.save_user_limit("7001", "42", "follow", "group", 0)
        self.assertEqual(pdata.user_limit("7001", "42")["mode"], "follow")

    def test_permissions_and_miyoushe_baseline(self):
        db.put("perm", "7101", {"enabled": True, "features": {"miyoushe": False}})
        r = pdata.save_permissions("7101", {"miyoushe": True, "chat": True})
        self.assertIsNotNone(r["miyoushe_baseline"])
        self.assertIn("7101", docfile.load("miyoushe_baselines.json"))
        self.assertIn("7101", docfile.load("miyoushe_known_groups.json"))
        self.assertTrue(pdata.get_permissions("7101")["features"]["chat"])
        self.assertFalse(pdata.get_permissions("7101")["features"]["search"])

    def test_detection_parse_and_warning(self):
        clients = napcat.parse_detection("P|10|09:30\nA|10|222\nA|10|111\nP|11|10:00\n")
        self.assertEqual(clients[0]["accounts"], ["222", "111"])
        w = napcat.other_qq_warning(clients, "111")
        self.assertIn("222", w)
        self.assertNotIn("111，", w)
        self.assertEqual(napcat.other_qq_warning([], "111"), "")

    def test_lore_select_all_means_everyone(self):
        db.put("persona", "roles", {"roles": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})
        db.put("persona", "shared_lore", {"items": []})
        item = pdata.save_lore_item({"name": "x", "role_ids": ["a", "b"]})
        self.assertEqual(item["role_ids"], [])

    def test_pixiv_push_validation(self):
        with self.assertRaises(ValueError):
            pdata.pixiv_push_now([7], None)
        req = pdata.pixiv_push_now([2, 0], ["123"], "10:30")
        self.assertEqual(req["batches"], [0, 2])
        self.assertTrue(pdata.pixiv_cancel_push())


class PanelApiTest(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(server.create_app())
        self.h = {"X-Panel-Token": server.TOKEN}

    def test_token_required(self):
        self.assertEqual(self.c.get("/api/overview").status_code, 401)
        self.assertEqual(self.c.get("/api/overview", headers=self.h).json()["ok"], True)

    def test_index_injects_token(self):
        self.assertIn(server.TOKEN, self.c.get("/").text)

    def test_error_becomes_400(self):
        r = self.c.post("/api/limit/unified", headers=self.h, json={"value": "abc"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()["ok"])

    def test_wallpaper_upload_rejects_bad_type(self):
        r = self.c.post("/api/wallpapers/upload", headers={**self.h, "X-Filename": "a.exe"}, content=b"x")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(wallpapers.public()["items"], [])



    def test_miyoushe_sign_feature_synced(self):
        from pathlib import Path
        from furina.core import permissions

        self.assertIn("miyoushe_sign", permissions.FEATURES)
        self.assertEqual(permissions.FEATURES["miyoushe_sign"], "米游社签到")
        self.assertIn("miyoushe_sign", [k for k, _, _ in pdata.KINDS])
        root = Path(__file__).resolve().parent.parent
        app_js = (root / "panel/web/app.js").read_text(encoding="utf-8")
        self.assertIn("miyoushe_sign", app_js)
        self.assertIn("miyoushe_sign:", app_js)
        self.assertIn("米游社签到", app_js)
        classic_win = classic_panel_source()
        self.assertIn("米游社签到", classic_win)
        r = self.c.get("/api/mihoyo", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))

    def test_commands_reference_page_synced(self):
        from pathlib import Path
        from furina.plugins.jm import config as jm_cfg

        root = Path(__file__).resolve().parent.parent
        classic_win = classic_panel_source()
        app_js = (root / "panel/web/app.js").read_text(encoding="utf-8")
        index_html = (root / "panel/web/index.html").read_text(encoding="utf-8")

        self.assertIn('("commands", "指令大全"', classic_win)
        self.assertIn("def _build_commands_page(self):", classic_win)
        self.assertIn("self._build_commands_page()", classic_win)
        self.assertIn('key: "commands", label: "指令大全"', app_js)
        self.assertIn("COMMAND_GROUPS", app_js)
        self.assertIn('data-page="commands"', index_html)
        for kw in ("ys帮助", "bt帮助", "zzz帮助", "bbb帮助", "@芙芙 绑定账号", "@芙芙 指令帮助", "@芙芙 更新展柜", "@芙芙 ys每日", "@芙芙 bt深渊", "@芙芙 zzz星见雅攻略", "@芙芙 bbb查询", "/jm 350234", "pid113456789", "确认提醒", "设精华"):
            self.assertIn(kw, classic_win)
            self.assertIn(kw, app_js)

        self.assertTrue(jm_cfg.has_jm_head("@芙宁娜-黑芙 /jm 350234"))
        self.assertEqual(jm_cfg.parse_album_id("@芙宁娜-黑芙 /jm 350234"), "350234")


if __name__ == "__main__":
    unittest.main()
