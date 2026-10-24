"""活人感 v4：热梗库 / 群梗 / 热搜 / 芙宁娜口吻 / 去 AI 味；B站按意愿行动、实时状态、面板请求与设置。全部离线。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import random
import time
import unittest
from unittest import mock

from furina.core import db
from furina.plugins.chat import deai, huoren, memory
from furina.services import slang
from furina.services.bilibili import store as bili_store

GID = 100000004
_BASE = "BOT_QQ=30000002\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"


def _clear_slang():
    for k in list(db.items(slang.NS)):
        db.delete(slang.NS, k)


class SlangTest(unittest.TestCase):
    def setUp(self):
        _clear_slang()

    def test_find_in_text(self):
        found = dict(slang.find_in("哈哈蚌埠住了，这也太离谱了 xswl"))
        self.assertIn("蚌埠住了", found)
        self.assertIn("离谱", found)
        self.assertIn("xswl", found)
        # 单字梗只在独立出现时才算，避免「草莓」里的「草」误伤
        self.assertNotIn("草", dict(slang.find_in("我想吃草莓蛋糕")))
        self.assertIn("草", dict(slang.find_in("草 太好笑了")))

    def test_learned_dedup_and_remove(self):
        n = slang.add_learned([{"term": "哈基米", "meaning": "内置已有"}, {"term": "芙芙教", "meaning": "芙宁娜粉丝自称"},
                               {"term": "芙芙教", "meaning": "重复"}], source="B站")
        self.assertEqual(n, 1)
        self.assertEqual([x["term"] for x in slang.learned()], ["芙芙教"])
        self.assertIn("芙芙教", dict(slang.find_in("我入芙芙教了")))
        slang.remove_learned("芙芙教")
        self.assertEqual(slang.learned(), [])

    def test_group_memes_need_several_people(self):
        for uid in (1, 2):
            slang.observe(GID, uid, "芙芙又在摸鱼")
        self.assertEqual(slang.group_memes(GID), [])
        slang.observe(GID, 3, "芙芙又在摸鱼")
        self.assertEqual(slang.group_memes(GID), ["芙芙又在摸鱼"])
        for t in ("哈哈哈", "[CQ:image]", "12345", "这句话实在是太长了根本不像是一个梗吧"):
            for uid in (1, 2, 3):
                slang.observe(GID, uid, t)
        self.assertEqual(slang.group_memes(GID), ["芙芙又在摸鱼"])

    def test_trending_freshness(self):
        slang.set_trending(["原神新版本", "  ", "猫猫"])
        self.assertEqual(slang.fresh_trending(), ["原神新版本", "猫猫"])
        db.put(slang.NS, "trending", {"words": ["旧"], "ts": time.time() - 13 * 3600})
        self.assertEqual(slang.fresh_trending(), [])


class HuorenNoteTest(unittest.TestCase):
    def setUp(self):
        _clear_slang()
        memory.reset(GID)
        _env.write_env(_BASE)

    def test_note_explains_slang_and_has_lines(self):
        memory.add_message(GID, "user", "阿一：这也太典了，蚌埠住了", meta={"uid": "1", "mid": "1"})
        note = huoren.build_note(GID, context_text="xswl")
        self.assertIn("蚌埠住了", note)
        self.assertIn("xswl", note)
        self.assertIn("你平时说话的感觉", note)

    def test_switches(self):
        _env.write_env(_BASE + "AI_CHAT_SLANG=false\nAI_CHAT_STYLE_LINES=false\nAI_CHAT_GROUP_MEMES=false\nAI_CHAT_TRENDING=false\n")
        note = huoren.build_note(GID, context_text="蚌埠住了")
        self.assertNotIn("网络用语", note)
        self.assertNotIn("你平时说话的感觉", note)
        _env.write_env(_BASE + "AI_CHAT_HUOREN_V3=false\n")
        self.assertEqual(huoren.build_note(GID, context_text="蚌埠住了"), "")

    def test_group_memes_in_note(self):
        for uid in (1, 2, 3):
            slang.observe(GID, uid, "芙芙又在摸鱼")
        self.assertIn("芙芙又在摸鱼", huoren.meme_note(GID))

    def test_slang_note_trending_and_usable(self):
        slang.set_trending(["今天的大新闻"])
        note = huoren.slang_note(GID, "", rng=mock.Mock(random=lambda: 0.0, sample=lambda seq, k: list(seq)[:k]))
        self.assertIn("今天的大新闻", note)
        self.assertIn("合适才用", note)


class DeaiTest(unittest.TestCase):
    def test_soft_clean(self):
        self.assertEqual(deai.soft_clean("明天是周五哦。希望对你有帮助！"), "明天是周五哦")
        self.assertEqual(deai.soft_clean("好的，有问题随时告诉我～"), "好的")
        self.assertEqual(deai.soft_clean("总的来说，蛋糕最好吃"), "蛋糕最好吃")
        self.assertEqual(deai.soft_clean("希望对你有帮助"), "希望对你有帮助")  # 删光了就不删

    def test_hard_problem(self):
        self.assertTrue(deai.hard_problem("作为一个AI，我不能吃蛋糕"))
        self.assertTrue(deai.hard_problem("**重点**：别熬夜"))
        self.assertTrue(deai.hard_problem("我理解你的感受"))
        self.assertEqual(deai.hard_problem("1. 搜图\n2. 强化\n3. 点歌"), "")  # 功能介绍的数字列表不算
        self.assertEqual(deai.hard_problem("笑死，我也想要"), "")
        self.assertEqual(deai.strip_markdown("## 标题\n**加粗**"), "标题\n加粗")


class BiliWillTest(unittest.TestCase):
    def setUp(self):
        for k in ("auth", "day", "halt", "watched", "posts", "meta", "live", "activity", "request"):
            db.delete(bili_store.NS, k)
        _clear_slang()
        _env.write_env(_BASE + "BILI_HOURS=0-24\n")

    def tearDown(self):
        _env.write_env(_BASE)

    def _surf(self):
        from furina.plugins.bilibili import surf

        return surf

    def test_parse_round_full(self):
        surf = self._surf()
        raw = ('{"videos":[{"bvid":"BV1a","interest":9,"thought":"笑死","like":true,"comment":"三分钟那里绷不住了",'
               '"danmaku":{"text":"哈哈哈哈哈","second":"75"},"share":""}],'
               '"new_slang":[{"term":"新梗A","meaning":"某个意思"},{"term":"","meaning":"x"}],"dynamic":"今天刷到一只会做蛋糕的猫"}')
        out = surf.parse_round(raw)
        v = out["videos"][0]
        self.assertTrue(v["like"])
        self.assertEqual((v["danmaku"], v["second"]), ("哈哈哈哈哈", 75))
        self.assertEqual(out["new_slang"], [{"term": "新梗A", "meaning": "某个意思"}])
        self.assertEqual(out["dynamic"], "今天刷到一只会做蛋糕的猫")
        self.assertEqual(surf.parse_round("坏的"), {"videos": [], "new_slang": [], "dynamic": ""})

    def test_caps_default_unlimited(self):
        surf = self._surf()
        c = surf.cfg()
        for k in ("comment_max", "dynamic_max", "like_max", "danmaku_max"):
            self.assertEqual(c[k], 0, k)
        for _ in range(50):
            bili_store.bump("comments")
        self.assertTrue(surf.under_cap("comments", 0))
        self.assertFalse(surf.under_cap("comments", 3))

    def test_run_round_follows_her_will(self):
        surf = self._surf()
        bili_store.save_auth({"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"})
        feed = [{"bvid": f"BV{i}", "title": f"视频{i}", "up": "UP", "duration": 120, "pic": ""} for i in range(3)]
        view = mock.AsyncMock(side_effect=lambda bvid: {"aid": int(bvid[2:]) + 1, "cid": 10 + int(bvid[2:]), "title": f"视频{bvid[2:]}",
                                                         "duration": 120, "owner": {"name": "UP"}, "stat": {"view": 5000}})
        reply = ('{"videos":[{"bvid":"BV0","interest":9,"thought":"好看","like":true,"comment":"这段我倒回去看了三遍",'
                 '"danmaku":{"text":"前方高能","second":30},"share":"你们快看"},'
                 '{"bvid":"BV1","interest":2,"thought":"无聊","like":false,"comment":"","danmaku":{"text":"","second":0},"share":""}],'
                 '"new_slang":[{"term":"测试新梗","meaning":"测试用"}],"dynamic":""}')
        like, cmt, dm = mock.AsyncMock(), mock.AsyncMock(return_value="1"), mock.AsyncMock(return_value="2")
        shared = []

        async def share(text, bvid):
            shared.append((text, bvid))
            return 1

        C = surf.Client
        with mock.patch.object(C, "ensure_buvid", mock.AsyncMock()), mock.patch.object(C, "feed", mock.AsyncMock(return_value=feed)), \
                mock.patch.object(C, "view", view), mock.patch.object(C, "subtitle_text", mock.AsyncMock(return_value="大家好，今天教大家做一个不用烤箱的猫爪蛋糕，先把奶油打发")), \
                mock.patch.object(C, "danmaku", mock.AsyncMock(return_value=[])), mock.patch.object(C, "hot_comments", mock.AsyncMock(return_value=[])), \
                mock.patch.object(C, "like", like), mock.patch.object(C, "comment", cmt), mock.patch.object(C, "send_danmaku", dm), \
                mock.patch.object(surf, "_llm", mock.AsyncMock(return_value=reply)), \
                mock.patch.object(surf, "_watch", mock.AsyncMock(return_value="讲了什么：教做猫爪蛋糕")), \
                mock.patch.object(surf.asyncio, "sleep", mock.AsyncMock()), mock.patch.object(surf.random, "random", return_value=0.1):
            result = asyncio.run(surf.run_round(None, share, manual=True))
        self.assertIn("刷完一轮", result)
        like.assert_awaited_once_with(1)
        cmt.assert_awaited_once()
        dm.assert_awaited_once_with(1, 10, "前方高能", 30000)
        self.assertEqual(shared, [("你们快看", "BV0")])
        self.assertEqual(bili_store.used("likes"), 1)
        self.assertEqual(bili_store.used("danmaku"), 1)
        self.assertEqual({p["kind"] for p in bili_store.all_posts()}, {"comment", "danmaku"})
        self.assertIn("测试新梗", [x["term"] for x in slang.learned()])
        acts = {w["bvid"]: w.get("acts") for w in bili_store.all_watched()}
        self.assertEqual(set(acts["BV0"]), {"like", "danmaku", "comment", "share"})
        self.assertEqual(bili_store.get_live()["state"], "idle")
        self.assertTrue(any("前方高能" in a["text"] for a in bili_store.activity()))

    def test_no_speech_video_only_likes(self):
        surf = self._surf()
        bili_store.save_auth({"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"})
        feed = [{"bvid": "BV7", "title": "纯音乐", "up": "UP", "duration": 120, "pic": ""}]
        view = mock.AsyncMock(return_value={"aid": 8, "cid": 17, "title": "纯音乐", "duration": 120, "owner": {"name": "UP"},
                                            "stat": {"view": 9000}})
        reply = ('{"videos":[{"bvid":"BV7","interest":8,"thought":"好听","like":true,"comment":"这段旋律绝了",'
                 '"danmaku":{"text":"好听","second":30},"share":""}],"new_slang":[],"dynamic":""}')
        like, cmt, dm = mock.AsyncMock(), mock.AsyncMock(return_value="1"), mock.AsyncMock(return_value="2")
        C = surf.Client
        with mock.patch.object(C, "ensure_buvid", mock.AsyncMock()), mock.patch.object(C, "feed", mock.AsyncMock(return_value=feed)), \
                mock.patch.object(C, "view", view), mock.patch.object(C, "subtitle_text", mock.AsyncMock(return_value="字幕by小明")), \
                mock.patch.object(C, "danmaku", mock.AsyncMock(return_value=[])), mock.patch.object(C, "hot_comments", mock.AsyncMock(return_value=[])), \
                mock.patch.object(C, "like", like), mock.patch.object(C, "comment", cmt), mock.patch.object(C, "send_danmaku", dm), \
                mock.patch.object(surf, "_llm", mock.AsyncMock(return_value=reply)), \
                mock.patch.object(surf, "_watch", mock.AsyncMock(return_value="")), \
                mock.patch.object(surf.asyncio, "sleep", mock.AsyncMock()), mock.patch.object(surf.random, "random", return_value=0.1):
            result = asyncio.run(surf.run_round(None, None, manual=True))
        self.assertIn("刷完一轮", result)
        like.assert_awaited_once_with(8)
        cmt.assert_not_awaited()
        dm.assert_not_awaited()

    def test_switch_off_comment(self):
        surf = self._surf()
        _env.write_env(_BASE + "BILI_HOURS=0-24\nBILI_COMMENT_ENABLED=false\nBILI_DANMAKU_DAILY_MAX=1\n")
        bili_store.bump("danmaku")
        c = surf.cfg()
        self.assertFalse(c["comment_on"])
        self.assertFalse(surf.under_cap("danmaku", c["danmaku_max"]))

    def test_store_live_activity_request(self):
        bili_store.set_live("watching", {"bvid": "BV1", "title": "猫", "pic": "x", "junk": 1}, "看弹幕")
        lv = bili_store.get_live()
        self.assertEqual(lv["video"], {"bvid": "BV1", "title": "猫", "pic": "x"})
        db.put(bili_store.NS, "live", {**lv, "ts": time.time() - 3600})
        self.assertEqual(bili_store.get_live()["state"], "idle")  # 太久没更新就当没在刷
        for i in range(70):
            bili_store.log_activity(f"第{i}步")
        self.assertEqual(len(bili_store.activity(100)), bili_store.MAX_ACTIVITY)
        with self.assertRaises(ValueError):
            bili_store.put_request("hack")
        bili_store.put_request("surf", keyword="猫猫")
        req = bili_store.take_request()
        self.assertEqual((req["action"], req["keyword"]), ("surf", "猫猫"))
        self.assertIsNone(bili_store.take_request())


class PanelBiliTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(_BASE)

    def test_overview_and_requests(self):
        from panel.services import bili_panel

        d = bili_panel.overview()
        for k in ("status", "caps", "live", "activity", "watched", "posts", "learned", "trending", "group_memes"):
            self.assertIn(k, d)
        self.assertEqual(d["caps"]["comments"], 0)
        r = bili_panel.request("surf", "原神")
        self.assertEqual(r["request"]["keyword"], "原神")
        self.assertTrue(bili_panel.cancel_request())
        bili_panel.request("pause")
        self.assertTrue(bili_store.halted()[0])
        bili_panel.request("resume")
        self.assertFalse(bili_store.halted()[0])

    def test_envgroup_roundtrip(self):
        from panel.services import huoren_settings as hs

        g = hs.get_group("bili")
        f = {x["key"]: x for x in g["fields"]}
        self.assertEqual(f["BILI_COMMENT_DAILY_MAX"]["value"], 0)
        hs.save_group("huoren", {"AI_CHAT_SLANG_PROBABILITY": 0.5, "AI_CHAT_DEAI": False})
        g = {x["key"]: x["value"] for x in hs.get_group("huoren")["fields"]}
        self.assertEqual(g["AI_CHAT_SLANG_PROBABILITY"], 0.5)
        self.assertFalse(g["AI_CHAT_DEAI"])
        with self.assertRaises(ValueError):
            hs.save_group("voice", {"AI_CHAT_VOICE_MAX_CHARS": 5})
        with self.assertRaises(ValueError):
            hs.get_group("nope")
        for _t, _d, fields in hs.GROUPS.values():
            for key, label, kind, default, _h, opts in fields:
                if kind in ("int", "dec"):
                    self.assertTrue(opts.get("min", 0) <= float(default) <= opts.get("max", 1e9), key)


if __name__ == "__main__":
    unittest.main()
