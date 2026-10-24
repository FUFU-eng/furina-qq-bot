"""活人感 P3：语音消息 / B站生活 / 生活状态注入 / 新功能四处同步。全部离线，不连 B 站、不开语音引擎。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import random
import time
import unittest
from pathlib import Path
from unittest import mock

from furina.core import db, permissions
from furina.plugins.chat import huoren, memory, tts
from furina.services import voice_tts
from furina.services.bilibili import client as bili_client
from furina.services.bilibili import store as bili_store

GID = 100000004
_BASE = "BOT_QQ=30000002\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"


def _voice_env(extra: str = "") -> None:
    ref = _env.TMP / "ref.wav"
    ref.write_bytes(b"RIFF0000WAVEfmt ")
    _env.write_env(_BASE + f"FURINA_TTS_ENABLED=true\nFURINA_TTS_REF_WAV={ref}\nFURINA_TTS_REF_TEXT=早安。\n"
                   "AI_CHAT_VOICE_PROBABILITY=1\nAI_CHAT_VOICE_HOURLY_MAX=3\n" + extra)


class VoiceTest(unittest.TestCase):
    def setUp(self):
        tts._sent.clear()
        permissions.set_feature_enabled(str(GID), "voice", True)

    def tearDown(self):
        _env.write_env(_BASE)

    def test_marker_and_ask(self):
        self.assertEqual(tts.extract_marker("[VOICE] 晚安啦"), ("晚安啦", True))
        self.assertEqual(tts.extract_marker("【语音】哼"), ("哼", True))
        self.assertEqual(tts.extract_marker("普通的话"), ("普通的话", False))
        for t in ("发条语音听听", "想听你的声音", "用语音说一句", "你开口说句话"):
            self.assertTrue(tts.asks_for_voice(t), t)
        self.assertFalse(tts.asks_for_voice("今天吃什么"))

    def test_speakable(self):
        self.assertEqual(voice_tts.speakable("哈哈~ 好呀(笑) https://x.cn [图片]"), "哈哈，好呀")
        self.assertNotIn("😀", voice_tts.speakable("真的吗😀"))

    def test_decide_rules(self):
        _voice_env()
        rng = random.Random(1)
        self.assertEqual(tts.decide(GID, "诶嘿，被你发现了", "发条语音", False, engine_hot=False, rng=rng), "explicit")
        self.assertEqual(tts.decide(GID, "晚安啦", "睡了", True, engine_hot=True, rng=rng), "marked")
        self.assertEqual(tts.decide(GID, "好耶好耶", "我考过了", False, engine_hot=True, rng=rng), "random")
        # 引擎没开时不为随机的一句冷启动
        self.assertEqual(tts.decide(GID, "好耶好耶", "我考过了", False, engine_hot=False, rng=rng), "")
        # 太长、带链接、全是英文都不念
        self.assertEqual(tts.decide(GID, "很长" * 40, "x", False, engine_hot=True, rng=rng), "")
        self.assertEqual(tts.decide(GID, "看这个 https://b23.tv/x", "x", False, engine_hot=True, rng=rng), "")
        self.assertEqual(tts.decide(GID, "ok ok ok ok", "x", False, engine_hot=True, rng=rng), "")
        # 群关了语音
        permissions.set_feature_enabled(str(GID), "voice", False)
        self.assertEqual(tts.decide(GID, "诶嘿", "发条语音", False, engine_hot=True, rng=rng), "")

    def test_hourly_cap(self):
        _voice_env()
        now = time.time()
        tts._sent[GID].extend([now - 10, now - 20, now - 30])
        self.assertEqual(tts.decide(GID, "诶嘿嘿", "发条语音", False, engine_hot=True, now=now), "")
        tts._sent[GID].clear()
        tts._sent[GID].extend([now - 4000] * 3)
        self.assertEqual(tts.decide(GID, "诶嘿嘿", "发条语音", False, engine_hot=True, now=now), "explicit")

    def test_voice_unlimited_by_default(self):
        _voice_env()
        _env.write_env(_env.TMP.joinpath(".env").read_text(encoding="utf-8").replace("AI_CHAT_VOICE_HOURLY_MAX=3\n", ""))
        now = time.time()
        tts._sent[GID].extend([now - i for i in range(50)])
        self.assertEqual(tts.decide(GID, "诶嘿嘿", "发条语音", False, engine_hot=True, now=now), "explicit")

    def test_disabled_without_config(self):
        _env.write_env(_BASE)
        self.assertFalse(voice_tts.enabled())
        self.assertFalse(tts.available(GID))
        self.assertIsNone(asyncio.run(voice_tts.synthesize("你好呀")))

    def test_maybe_send_success_and_fallback(self):
        _voice_env()
        sent = []

        async def sender(msg):
            sent.append(msg)
            return {"message_id": 1}

        wav = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 2000
        with mock.patch.object(voice_tts, "is_online", mock.AsyncMock(return_value=True)), \
                mock.patch.object(voice_tts, "synthesize", mock.AsyncMock(return_value=wav)):
            ok = asyncio.run(tts.maybe_send(sender, GID, "诶嘿，被你发现了", "发条语音", False))
        self.assertTrue(ok)
        self.assertEqual(sent[0][0].type, "record")
        with mock.patch.object(voice_tts, "is_online", mock.AsyncMock(return_value=True)), \
                mock.patch.object(voice_tts, "synthesize", mock.AsyncMock(return_value=None)):
            self.assertFalse(asyncio.run(tts.maybe_send(sender, GID, "诶嘿", "发条语音", False)))


class BiliClientTest(unittest.TestCase):
    def test_wbi_sign_known_vector(self):
        # 公开文档里的示例：img/sub key + 固定 wts 得到固定 w_rid
        img, sub = "7cd084941338484aae1ad9425b84077c", "4932caff0ff746eab6f01bf08b70ac45"
        signed = bili_client.wbi_sign({"foo": "114", "bar": "514", "zab": 1919810}, img, sub, wts=1702204169)
        self.assertEqual(signed["w_rid"], "8f6f2b5b3d485fe1886cec6a0be8c5d4")
        self.assertEqual(bili_client.mixin_key(img, sub), "ea1db124af3c7062474693fa704f4ff8")

    def test_helpers(self):
        self.assertEqual(bili_client.parse_duration("4:12"), 252)
        self.assertEqual(bili_client.parse_duration("1:02:03"), 3723)
        self.assertEqual(bili_client.parse_duration(90), 90)
        self.assertEqual(bili_client.strip_html('<em class="keyword">芙宁娜</em>&amp;猫'), "芙宁娜&猫")

    def test_check_raises_risk(self):
        import httpx

        req = httpx.Request("GET", "https://api.bilibili.com/x")
        with self.assertRaises(bili_client.RiskControl):
            bili_client.check(httpx.Response(412, request=req, text=""))
        with self.assertRaises(bili_client.RiskControl):
            bili_client.check(httpx.Response(200, request=req, json={"code": -352, "message": "风控校验失败"}))
        with self.assertRaises(bili_client.NotLoggedIn):
            bili_client.check(httpx.Response(200, request=req, json={"code": -101}))
        self.assertEqual(bili_client.check(httpx.Response(200, request=req, json={"code": 0, "data": {"a": 1}})), {"a": 1})


class BiliSurfTest(unittest.TestCase):
    def setUp(self):
        for k in ("auth", "day", "halt", "watched", "posts", "meta"):
            db.delete(bili_store.NS, k)
        _env.write_env(_BASE + "BILI_HOURS=0-24\n")

    def tearDown(self):
        _env.write_env(_BASE)

    def _surf(self):
        from furina.plugins.bilibili import surf

        return surf

    def test_should_round_gates(self):
        surf = self._surf()
        always = random.Random(0)
        self.assertFalse(surf.should_round(rng=always, chance=1.0)[0])  # 没登录
        bili_store.save_auth({"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"}, uname="小号")
        self.assertTrue(surf.should_round(rng=always, chance=1.0)[0])
        bili_store.halt("测试风控", 1)
        ok, why = surf.should_round(rng=always, chance=1.0)
        self.assertFalse(ok)
        self.assertIn("风控", why)
        bili_store.resume()
        bili_store.bump("rounds")
        self.assertFalse(surf.should_round(rng=always, chance=1.0)[0])  # 离上一轮太近

    def test_parse_reactions_and_clean(self):
        surf = self._surf()
        raw = ('好的：{"videos":[{"bvid":"BV1xx","interest":12,"thought":"笑死这猫","comment":"感谢UP主的分享",'
               '"share":"你们快看"},{"bvid":"bad","interest":3}]}')
        out = surf.parse_reactions(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["interest"], 10)
        self.assertEqual(out[0]["comment"], "")  # 客套话被过滤
        self.assertEqual(out[0]["share"], "你们快看")
        self.assertEqual(surf.parse_reactions("不是 JSON"), [])

    def test_window(self):
        surf = self._surf()
        noon = time.mktime(time.strptime("2026-10-22 12:00", "%Y-%m-%d %H:%M"))
        self.assertTrue(surf.in_window("10-24", noon))
        self.assertFalse(surf.in_window("20-23", noon))
        self.assertTrue(surf.in_window("22-2", noon + 11 * 3600))

    def test_daily_limits_and_memory(self):
        bili_store.bump("comments")
        bili_store.bump("comments")
        self.assertEqual(bili_store.used("comments"), 2)
        bili_store.add_watched([{"bvid": "BV1", "title": "猫猫做蛋糕", "up": "某UP", "thought": "猫比我会做", "interest": 8},
                                {"bvid": "BV2", "title": "无聊广告", "up": "x", "thought": "划走", "interest": 2}])
        bili_store.add_post("comment", "这猫成精了", bvid="BV1", title="猫猫做蛋糕")
        self.assertEqual([w["bvid"] for w in bili_store.recent_watched()], ["BV1"])
        self.assertIn("BV1", bili_store.commented_bvids())
        note = huoren.bili_note()
        self.assertIn("猫猫做蛋糕", note)
        self.assertIn("这猫成精了", note)
        self.assertNotIn("无聊广告", note)

    def test_run_round_halts_on_risk(self):
        surf = self._surf()
        bili_store.save_auth({"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"})
        notes = []

        async def notify(t):
            notes.append(t)

        with mock.patch.object(surf.Client, "ensure_buvid", mock.AsyncMock()), \
                mock.patch.object(surf.Client, "feed", mock.AsyncMock(side_effect=bili_client.RiskControl(-352, "风控"))), \
                mock.patch.object(surf.random, "random", return_value=0.1):
            result = asyncio.run(surf.run_round(notify, None, manual=True))
        self.assertIn("风控", result)
        self.assertTrue(bili_store.halted()[0])
        self.assertTrue(notes)


class HuorenTest(unittest.TestCase):
    def test_life_note_stable_within_slot(self):
        t = time.mktime(time.strptime("2026-10-22 15:10", "%Y-%m-%d %H:%M"))
        self.assertEqual(huoren.life_note(t), huoren.life_note(t + 1800))
        self.assertIn("15 点", huoren.life_note(t))

    def test_style_note_from_real_messages(self):
        gid = 99887766
        memory.reset(gid)
        for i, (name, text) in enumerate([("阿一", "笑死"), ("阿二", "真的假的"), ("阿三", "我先睡了"),
                                          ("阿四", "[图片]"), ("阿五", "今天食堂的饭好难吃啊啊啊")]):
            memory.add_message(gid, "user", f"{name}：{text}", meta={"uid": str(1000 + i), "mid": str(i + 1)})
        note = huoren.style_note(gid, rng=random.Random(0))
        self.assertIn("笑死", note)
        self.assertNotIn("[图片]", note)
        memory.reset(gid)


class FeatureSyncTest(unittest.TestCase):
    def test_voice_and_bili_share_synced(self):
        from panel.services import data as pdata

        self.assertEqual(permissions.FEATURES["voice"], "语音消息")
        self.assertEqual(permissions.FEATURES["bili_share"], "B站刷到分享")
        self.assertIn("voice", permissions.DEFAULT_ON_FEATURES)
        self.assertIn("bili_share", permissions.DEFAULT_ON_FEATURES)  # 分享有趣视频默认开
        kinds = [k for k, _, _ in pdata.KINDS]
        self.assertIn("voice", kinds)
        self.assertIn("bili_share", kinds)
        root = Path(__file__).resolve().parent.parent
        app = (root / "panel/web/app.js").read_text(encoding="utf-8")
        for word in ("语音消息", "B站刷到分享", "B站登录"):
            self.assertIn(word, app)
        # 活人感 / 语音 / B站 设置统一定义在 huoren_settings，经典面板和网页面板都从这里取
        settings = (root / "panel/classic/settings_env.py").read_text(encoding="utf-8")
        self.assertIn("huoren_settings.classic_pages()", settings)
        from panel.services import huoren_settings as hs

        keys = {f[0] for _t, _d, fields in hs.GROUPS.values() for f in fields}
        for key in ("AI_CHAT_VOICE_PROBABILITY", "FURINA_TTS_REF_WAV", "BILI_COMMENT_DAILY_MAX", "BILI_MENTION_NAME",
                    "AI_CHAT_HUOREN_V3", "AI_CHAT_SLANG", "AI_CHAT_DEAI", "BILI_DANMAKU_DAILY_MAX", "BILI_SHARE_DAILY_MAX"):
            self.assertIn(key, keys)
        for word in ('key: "bili"', "envTab(\"huoren\"", "/api/bili"):
            self.assertIn(word, app)

    def test_websearch_synced(self):
        """联网搜索：四处同步（权限 / 网页面板 / 面板统计 / 测试）+ 经典面板设置页。"""
        from panel.services import data as pdata
        from panel.services import huoren_settings as hs

        self.assertEqual(permissions.FEATURES["websearch"], "联网搜索")
        self.assertIn("websearch", permissions.DEFAULT_ON_FEATURES)
        self.assertIn("websearch", [k for k, _, _ in pdata.KINDS])
        root = Path(__file__).resolve().parent.parent
        app = (root / "panel/web/app.js").read_text(encoding="utf-8")
        self.assertIn('envTab("websearch", "联网搜索")', app)
        self.assertIn('["联网搜索", ', app)
        keys = {f[0] for _t, _d, fields in hs.GROUPS.values() for f in fields}
        for key in ("AI_CHAT_WEB_SEARCH", "AI_CHAT_WEB_SEARCH_DAILY_MAX", "AI_CHAT_TALK_SHAPE", "AI_CHAT_SELF_ACTS",
                    "FURINA_TTS_EMOTIONS", "FURINA_TTS_REF_TSUN_WAV", "FURINA_TTS_PAUSE_FOR_GAMES", "FURINA_TTS_MIN_FREE_RAM_GB"):
            self.assertIn(key, keys)
        pages = {p[0]: p for p in hs.classic_pages()}
        self.assertEqual(set(pages), {"huoren", "voice", "bili", "websearch"})
        for name, page in pages.items():  # 每组字段都分进了卡片，一个不漏
            in_cards = [f[0] for _t, _d, fields in page[4] for f in fields]
            self.assertEqual(sorted(in_cards), sorted(f[0] for f in hs.GROUPS[name][2]), name)
        classic = (root / "panel/classic/settings.py").read_text(encoding="utf-8")
        for key in ("huoren", "voice", "websearch", "bili"):
            self.assertIn(f'_ENV["{key}"]', classic)
        window = (root / "panel/classic/window.py").read_text(encoding="utf-8")
        self.assertIn('("bili", "B站生活")', window)
        info = (root / "panel/classic/pages/info.py").read_text(encoding="utf-8")
        for word in ("联网搜索", "活人感", "B站生活", "语音消息"):
            self.assertIn(word, info)


if __name__ == "__main__":
    unittest.main()
