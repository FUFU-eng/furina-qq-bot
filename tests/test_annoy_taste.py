"""烦躁值（会被找烦、会消气、烦透了会不理人）+ B站口味（看过的东西会改变她）+ 面板能看到。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import random
import time
import unittest
from unittest import mock

from furina.core import db
from furina.plugins.chat import kernel
from furina.services import annoy
from furina.services.bilibili import store, taste
from tests.test_humanlike import BASE_ENV, GID, make_event

ON = BASE_ENV + "AI_CHAT_ANNOY=true\nBILI_TASTE=true\n"


class AnnoyTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(ON)
        db.delete(annoy.NS, annoy.KEY)
        kernel._groups.clear()

    def test_rapid_repeats_build_up_and_decay(self):
        t0 = 1_000_000.0
        lv = [annoy.note_at(GID, 501, "小明", "在吗在吗", now=t0 + i * 20)["level"] for i in range(10)]
        self.assertTrue(all(b > a for a, b in zip(lv, lv[1:])))  # 越找越烦
        self.assertGreaterEqual(lv[-1], 75)
        self.assertEqual(annoy.band(lv[-1]), "烦透了" if lv[-1] >= 85 else "很烦")
        later = annoy.level(501, now=t0 + 200 + 3600)  # 一小时没人吵：淡一半
        self.assertAlmostEqual(later, annoy.level(501, now=t0 + 200) / 2, delta=1)

    def test_one_polite_at_is_nothing(self):
        r = annoy.note_at(GID, 502, "小红", "你好呀，今天有什么好看的", now=2_000_000)
        self.assertLess(r["level"], 5)
        self.assertEqual(annoy.note([502], now=2_000_000), "")

    def test_soothing_and_insult(self):
        t0 = 3_000_000.0
        for i in range(6):
            annoy.note_at(GID, 503, "阿强", f"第{i}个问题", now=t0 + i * 30)
        before = annoy.level(503, now=t0 + 200)
        after = annoy.note_at(GID, 503, "阿强", "对不起嘛别生气，抱抱", now=t0 + 200)["level"]
        self.assertLess(after, before)
        bad = annoy.note_at(GID, 504, "阿坏", "你个废物", now=t0)
        self.assertGreater(bad["delta"], 8)

    def test_owner_never_ignored_and_counts_half(self):
        t0 = time.time()
        for i in range(20):
            annoy.note_at(GID, 505, "路人", "在吗", now=t0 - 400 + i * 20)
        self.assertTrue(any(annoy.should_ignore(505, rng=random.Random(s)) for s in range(30)))
        self.assertFalse(any(annoy.should_ignore(505, owner=True, rng=random.Random(s)) for s in range(30)))
        a = annoy.note_at(GID, 506, "主人", "在吗", owner=True, now=t0)["delta"]
        b = annoy.note_at(GID, 507, "别人", "在吗", now=t0)["delta"]
        self.assertAlmostEqual(a, b / 2, delta=0.01)

    def test_switch_off(self):
        _env.write_env(BASE_ENV + "AI_CHAT_ANNOY=false\n")
        self.assertEqual(annoy.note_at(GID, 508, "x", "在吗")["level"], 0)
        self.assertEqual(annoy.random_factor(), 1.0)

    def test_note_and_prompt(self):
        t0 = time.time()
        for i in range(9):
            annoy.note_at(GID, 509, "小瓜", "在吗", now=t0 - 200 + i * 20)
        n = annoy.note([509])
        self.assertIn("小瓜", n)
        self.assertIn("不是非要发火", n)
        self.assertNotIn("烦躁值", n.split("别说出")[0])
        self.assertTrue(annoy.short_reply(509))
        self.assertLess(annoy.random_factor(), 1.0)

    def _at(self, mid, uid, text="在吗"):
        ev = make_event(f"[CQ:at,qq=30000001] {text}", user_id=uid, mid=mid)
        with mock.patch.object(kernel, "available", return_value=True), \
             mock.patch.object(kernel, "_wake") as wk, \
             mock.patch.object(kernel, "_wake_reason", new=mock.AsyncMock(return_value="at")):
            asyncio.run(kernel.observe(ev, GID, str(uid), "烦人精", text, True, False, False))
        return wk

    def test_kernel_ignores_when_fed_up(self):
        uid = 40000 + int(time.time()) % 9000
        self._at(9001, uid).assert_called_once()
        self.assertGreater(annoy.level(uid), 0)
        with mock.patch.object(annoy, "should_ignore", return_value=True):
            self._at(9002, uid).assert_not_called()

    def test_settings_listed(self):
        from panel.services import huoren_settings as hs
        keys = {f[0] for f in hs.FIELDS} if hasattr(hs, "FIELDS") else set()
        from pathlib import Path
        src = Path(hs.__file__).read_text(encoding="utf-8")
        for k in ("AI_CHAT_ANNOY", "AI_CHAT_ANNOY_IGNORE", "AI_CHAT_ANNOY_HALF_MINUTES", "AI_CHAT_ANNOY_SENSITIVITY",
                  "BILI_TASTE", "BILI_TASTE_PROBABILITY", "BILI_TASTE_HALF_DAYS"):
            self.assertIn(f'"{k}"', src)
            if keys:
                self.assertIn(k, keys)


class TasteTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(ON)
        db.delete(store.NS, taste.KEY)

    def _v(self, bvid, up, interest, tname="游戏", tags="原神,芙宁娜", thought=""):
        return {"bvid": bvid, "title": f"视频{bvid}", "up": up, "tname": tname, "tags": tags,
                "interest": interest, "thought": thought, "source": ""}

    def test_likes_grow_and_dislikes_sink(self):
        t0 = 5_000_000.0
        taste.learn([self._v("a", "芙芙剪辑", 9, thought="好可爱")], now=t0)
        taste.learn([self._v("b", "芙芙剪辑", 8)], now=t0 + 3600)
        taste.learn([self._v("c", "无聊UP", 1, tname="知识", tags="财经")], now=t0 + 7200)
        likes = [t["term"] for t in taste.likes(now=t0 + 7300)]
        dislikes = [t["term"] for t in taste.dislikes(now=t0 + 7300)]
        self.assertIn("芙芙剪辑", likes)
        self.assertIn("无聊UP", dislikes)
        line = taste.taste_line(now=t0 + 7300)
        self.assertIn("最近在迷", line)
        self.assertIn("不太爱看", line)
        # 十天后淡一半：口味会漂移
        s0 = {t["term"]: t["score"] for t in taste.ranked(now=t0 + 7300)}["芙芙剪辑"]
        s1 = {t["term"]: t["score"] for t in taste.ranked(now=t0 + 7300 + 10 * 86400)}["芙芙剪辑"]
        self.assertAlmostEqual(s1, s0 / 2, delta=0.05)

    def test_gets_bored(self):
        t0 = 6_000_000.0
        for i in range(8):
            taste.learn([self._v(f"x{i}", "天天刷的UP", 7, tags="同一个梗")], now=t0 + i * 600)
        self.assertIn("同一个梗", [t["term"] for t in taste.bored(now=t0 + 5000)])
        self.assertIn("腻", taste.taste_line(now=t0 + 5000))

    def test_recall_old_opinion(self):
        t0 = time.time() - 2 * 86400
        taste.learn([self._v("r1", "猫猫UP", 9, tname="动物圈", tags="猫", thought="笑死这猫")], now=t0)
        got = taste.recall("你们看过猫猫UP的新视频没", now=time.time())
        self.assertTrue(got)
        self.assertIn("2 天前", got[0])
        self.assertIn("笑死这猫", got[0])
        self.assertEqual(taste.recall("今天天气不错"), [])

    def test_pick_keyword_from_likes(self):
        taste.learn([self._v("k", "芙芙剪辑", 10)])
        self.assertIn(taste.pick_keyword(rng=random.Random(1)), {"芙芙剪辑", "游戏", "原神", "芙宁娜"})

    def test_huoren_note_carries_taste_and_recall(self):
        from furina.plugins.chat import huoren
        store.add_watched([{"bvid": "h1", "title": "测试视频", "up": "猫猫UP", "interest": 9, "thought": "可爱"}])
        taste.learn([self._v("h1", "猫猫UP", 9, tname="动物圈", tags="猫", thought="可爱")])
        note = huoren.bili_note(context_text="猫猫UP 又更新了")
        self.assertIn("口味", note)
        self.assertIn("你刷到过", note)

    def test_switch_off(self):
        _env.write_env(BASE_ENV + "BILI_TASTE=false\n")
        self.assertEqual(taste.learn([self._v("o", "u", 9)]), [])
        self.assertEqual(taste.taste_line(), "")


class PanelTest(unittest.TestCase):
    def test_overview_has_inner_and_taste(self):
        _env.write_env(ON)
        from panel.services import bili_panel
        annoy.note_at(GID, 601, "面板测试", "在吗")
        d = bili_panel.overview()
        self.assertIn("annoy", d["inner"])
        self.assertIn("mood_label", d["inner"])
        self.assertIn("likes", d["taste"])

    def test_html_helpers(self):
        try:
            from panel.classic.pages import bili as page
        except ImportError as e:  # 沙箱没有 Qt 运行库
            self.skipTest(str(e))
        html = page._inner_html({"mood": 70, "mood_label": "开心", "annoy": {
            "enabled": True, "global": 50, "global_band": "有点烦",
            "users": [{"name": "小瓜", "level": 66, "band": "很烦", "why": "10 分钟里找了你 6 次"}]}})
        self.assertIn("小瓜", html)
        self.assertIn("很烦", html)
        self.assertIn("还没形成口味", page._taste_html({"enabled": True}))


if __name__ == "__main__":
    unittest.main()
