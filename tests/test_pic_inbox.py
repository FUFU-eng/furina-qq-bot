"""筛图收件箱：收图去重、表情包过滤、留下/丢掉/撤销、强化替换、导出到 Eagle 自动导入文件夹、合并转发取图。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import io
import time
import unittest
from pathlib import Path

import nonebot
from PIL import Image, ImageDraw

from furina.core import permissions
from furina.services import pic_inbox as svc

nonebot.load_plugin("furina.plugins.pic_inbox")
from furina.plugins.pic_inbox import collect  # noqa: E402


def _img(w, h, seed, fmt="JPEG"):
    im = Image.new("RGB", (w, h), (seed * 7 % 255, 90, 160))
    d = ImageDraw.Draw(im)
    for i in range(40):
        x, y = (i * 37 * seed) % w, (i * 53 + seed * 11) % h
        d.ellipse([x, y, x + 70, y + 50], fill=((i * 20) % 255, (seed * 30) % 255, (i * 9) % 255))
    b = io.BytesIO()
    im.save(b, fmt, quality=90)
    return b.getvalue()


class PicInboxTest(unittest.TestCase):
    def setUp(self):
        self.root = _env.TMP / f"inbox_{time.time_ns()}"
        self.eagle = self.root / "eagle" / "足迹"
        _env.write_env(f"PIC_INBOX_DIR={self.root}\nPIC_INBOX_EXPORT_DIR={self.eagle}\n")
        for ns in (svc.NS, svc.NS_SEEN, svc.NS_META):
            for k in list(svc.db.items(ns)):
                svc.db.delete(ns, k)

    def test_filters_and_dedup(self):
        a = _img(1200, 900, 3)
        iid, why = svc.add_bytes(a, "group", "1", "2", "甲")
        self.assertEqual(why, "ok")
        self.assertEqual(svc.add_bytes(a, "group")[1], "dup")
        # 重新压缩 + 轻微缩放的同一张图也算见过
        b = io.BytesIO()
        Image.open(io.BytesIO(a)).resize((1100, 825)).save(b, "JPEG", quality=70)
        self.assertEqual(svc.add_bytes(b.getvalue(), "group")[1], "seen")
        self.assertEqual(svc.add_bytes(_img(300, 300, 5), "group")[1], "sticker")
        g = io.BytesIO()
        Image.new("RGB", (800, 800)).save(g, "GIF")
        self.assertEqual(svc.add_bytes(g.getvalue(), "group")[1], "animated")
        self.assertEqual(svc.add_bytes(b"not an image", "group")[1], "not_image")
        # 搜图/Pixiv 来的图不按尺寸挡
        self.assertEqual(svc.add_bytes(_img(300, 300, 5), "search", strict=False)[1], "ok")
        self.assertEqual(svc.get(iid)["enh"], "todo")  # 小于 1MB 排队强化

    def test_enhance_threshold_setting(self):
        _env.write_env(f"PIC_INBOX_DIR={self.root}\nPIC_INBOX_EXPORT_DIR={self.eagle}\nPIC_INBOX_ENHANCE_UNDER_KB=0\n")
        iid, _ = svc.add_bytes(_img(1200, 900, 4), "group")
        self.assertEqual(svc.get(iid)["enh"], "no")

    def test_keep_waits_for_enhance_then_exports(self):
        iid, _ = svc.add_bytes(_img(1200, 900, 6), "group")
        svc.decide(iid, True)
        self.assertEqual(svc.get(iid)["status"], "keep")
        self.assertFalse(self.eagle.exists() and any(self.eagle.iterdir()))
        up = self.root / "up.png"
        with Image.open(svc.file_path(svc.get(iid))) as im:
            im.resize((2400, 1800)).save(up)
        self.assertEqual(svc.next_to_enhance()["id"], iid)
        self.assertTrue(svc.apply_enhanced(iid, up))
        it = svc.get(iid)
        self.assertEqual((it["w"], it["enh"], it["ow"]), (2400, "done", 1200))
        self.assertTrue(it["file"].endswith(".jpg"))
        self.assertEqual(svc.export_ready(), 1)
        files = list(self.eagle.iterdir())
        self.assertEqual(len(files), 1)
        with Image.open(files[0]) as im:
            self.assertEqual(im.size, (2400, 1800))
        self.assertFalse((self.eagle.parent / "_furina_stage" / files[0].name).exists())
        # 撤销：Eagle 还没取走就撤回
        self.assertTrue(svc.undo(iid)["ok"])
        self.assertEqual(svc.get(iid)["status"], "pending")
        self.assertEqual(list(self.eagle.iterdir()), [])

    def test_export_goes_into_eagle_library_subfolder(self):
        # Eagle 4 只监听「自动导入文件夹\\图库名」这一层
        sub = self.eagle / "足迹"
        sub.mkdir(parents=True)
        _env.write_env(f"PIC_INBOX_DIR={self.root}\nPIC_INBOX_EXPORT_DIR={self.eagle}\n"
                       f"PIC_INBOX_ENHANCE_UNDER_KB=0\nEAGLE_LIBRARY_DIR=D:\\Pictures\\Eagle.library\n")
        self.assertEqual(svc.export_dir(), sub)
        iid, _ = svc.add_bytes(_img(1200, 900, 11), "group")
        svc.decide(iid, True)
        self.assertEqual(len(list(sub.glob("furina_*"))), 1)
        self.assertEqual([p.name for p in self.eagle.glob("furina_*")], [])

    def test_drop_undo_and_purge(self):
        iid, _ = svc.add_bytes(_img(1200, 900, 7), "group")
        svc.decide(iid, False)
        self.assertEqual(svc.get(iid)["status"], "trash")
        self.assertTrue(svc.undo(iid)["ok"])
        self.assertTrue(svc.file_path(svc.get(iid)).exists())
        svc.decide(iid, False)
        self.assertEqual(svc.purge(), 0)  # 撤销窗口内不删
        self.assertEqual(svc.purge(time.time() + svc.UNDO_SECONDS + 5), 1)
        self.assertIsNone(svc.get(iid))
        self.assertEqual(list((self.root / "trash").iterdir()), [])
        # 丢掉的图再被人发一次，不会重复收
        self.assertEqual(svc.add_bytes(_img(1200, 900, 7), "group")[1], "seen")
        st = svc.stats()
        self.assertEqual(st["dropped_today"], 1)

    def test_preview_and_token(self):
        iid, _ = svc.add_bytes(_img(2400, 1800, 8, "PNG"), "pixiv", strict=False)
        p = svc.preview(iid, 800)
        with Image.open(p) as im:
            self.assertEqual(max(im.size), 800)
        self.assertEqual(svc.preview(iid, 0), svc.file_path(svc.get(iid)))
        t = svc.token()
        self.assertTrue(svc.check_token(t))
        self.assertFalse(svc.check_token("wrong"))
        self.assertFalse(svc.check_token(""))
        self.assertEqual(svc.set_token("my-pass"), "my-pass")
        self.assertTrue(svc.check_token("my-pass"))

    def test_feature_synced(self):
        from panel.services import data as pdata

        self.assertEqual(permissions.FEATURES["pic_inbox"], "收图筛选")
        self.assertNotIn("pic_inbox", permissions.DEFAULT_ON_FEATURES)
        self.assertIn("pic_inbox", [k for k, _, _ in pdata.KINDS])
        app = (Path(__file__).resolve().parents[1] / "panel" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("收图筛选", app)

    def test_settings_listed(self):
        root = Path(__file__).resolve().parents[1]
        keys = (root / "panel/classic/settings_env.py").read_text(encoding="utf-8")
        for k in ("PIC_INBOX_ENABLED", "PIC_INBOX_ENHANCE_UNDER_KB", "PIC_INBOX_EXPORT_DIR", "PIC_INBOX_IDLE_GPU",
                  "PIC_INBOX_IDLE_RAM_MB", "PIC_INBOX_FROM_SEARCH", "PIC_INBOX_FROM_PIXIV", "PIC_INBOX_TUNNEL",
                  "PIC_INBOX_FROM_RANK", "PIC_INBOX_REQUIRE_PASSWORD"):
            self.assertIn(f'("{k}"', keys)
        window = (root / "panel/classic/window.py").read_text(encoding="utf-8")
        self.assertIn('("pics", "图片筛选")', window)


class ApiAuthTest(unittest.TestCase):
    def _req(self, auth=""):
        from starlette.requests import Request

        headers = [(b"authorization", auth.encode())] if auth else []
        return Request({"type": "http", "method": "GET", "path": "/api/ping", "headers": headers,
                        "query_string": b"", "client": ("1.2.3.4", 1)})

    def test_no_password_by_default(self):
        from fastapi import HTTPException

        from furina.plugins.pic_inbox import api

        _env.write_env("")
        api._auth(self._req())  # 默认不要密码
        _env.write_env("PIC_INBOX_REQUIRE_PASSWORD=1\n")
        with self.assertRaises(HTTPException):
            api._auth(self._req())
        api._auth(self._req("Bearer " + svc.token()))
        _env.write_env("")

    def test_rank_push_hooked(self):
        src = (Path(__file__).resolve().parents[1] / "furina/plugins/pixiv/__init__.py").read_text(encoding="utf-8")
        self.assertIn("'pixiv_rank' if bare_images else 'pixiv'", src)
        self.assertNotIn("if paths and not bare_images", src)


class _FakeBot:
    def __init__(self, fwd):
        self.fwd = fwd

    async def get_forward_msg(self, id):  # noqa: A002
        return self.fwd.get(str(id), {})


class CollectTest(unittest.TestCase):
    def test_gather_skips_stickers_and_reads_forwards(self):
        inner_fwd = {"messages": [
            {"sender": {"nickname": "丙"}, "message": [{"type": "image", "data": {"file": "c.jpg", "url": "u3"}}]},
        ]}
        msg = [
            {"type": "image", "data": {"file": "a.jpg", "url": "u1", "sub_type": 0}},
            {"type": "image", "data": {"file": "s.gif", "url": "s", "sub_type": 1}},
            {"type": "image", "data": {"file": "m.jpg", "url": "m", "summary": "[动画表情]"}},
            {"type": "mface", "data": {"url": "x"}},
            {"type": "forward", "data": {"id": "1", "content": [
                {"type": "node", "data": {"nickname": "乙", "content": [
                    {"type": "image", "data": {"file": "b.jpg", "url": "u2"}},
                    {"type": "forward", "data": {"id": "99"}},
                ]}},
            ]}},
        ]
        bot = _FakeBot({"99": inner_fwd})
        out = asyncio.run(collect.gather_images(bot, msg, "甲"))
        self.assertEqual([(o["file"], o["name"], o["forward"]) for o in out],
                         [("a.jpg", "甲", False), ("b.jpg", "乙", True), ("c.jpg", "丙", True)])


if __name__ == "__main__":
    unittest.main()
