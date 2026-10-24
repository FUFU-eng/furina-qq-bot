from tests import _env  # noqa: F401  (must be first)

import asyncio
import random
import time
import unittest
from unittest import mock

from furina.core import permissions
from furina.plugins.chat import memory, selfacts, talkshape, transcript, tts
from furina.plugins.chat import websearch as chat_ws
from furina.services import voice_tts
from furina.services import websearch as ws

GID = 100000004
_BASE = "BOT_QQ=30000002\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"

BING = ('<li class="b_algo"><h2><a href="https://a.cn/1" h="x">原神 <strong>6.2</strong> 卡池</a></h2>'
        '<div class="b_caption"><p>上半是芙宁娜复刻&nbsp;下半…</p></div></li>'
        '<li class="b_algo"><h2><a href="https://b.cn/2">第二条</a></h2><p>摘要二</p></li>')
BAIDU = ('<div class="result c-container xpath-log" tpl="se"><h3 class="t"><a href="http://www.baidu.com/link?url=1">'
         '天气<em>预报</em></a></h3><span>今天晴，25 度</span></div>'
         '<div class="result-op c-container"><h3><a href="http://www.baidu.com/link?url=2">第二</a></h3>内容</div>')


class WebSearchParseTest(unittest.TestCase):
    def test_parse_bing(self):
        r = ws.parse_bing(BING, 5)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["title"], "原神 6.2 卡池")
        self.assertEqual(r[0]["url"], "https://a.cn/1")
        self.assertIn("芙宁娜复刻", r[0]["snippet"])
        self.assertEqual(r[1]["snippet"], "摘要二")
        self.assertEqual(len(ws.parse_bing(BING, 1)), 1)

    def test_parse_baidu(self):
        r = ws.parse_baidu(BAIDU, 5)
        self.assertEqual([x["title"] for x in r], ["天气预报", "第二"])
        self.assertIn("今天晴", r[0]["snippet"])

    def test_normalize_and_relevance(self):
        self.assertEqual(ws.normalize("绝区零 当前版本 2026年10月"), "绝区零 最新版本")
        self.assertEqual(ws.normalize("原神  6.2 卡池"), "原神 6.2 卡池")
        self.assertEqual(ws._bing_query("绝区零 最新版本"), '"绝区零" 最新版本')
        junk = {"title": "绝（汉语文字）_百度百科", "snippet": "绝字现代的写法是绞丝旁一个色字"}
        good = {"title": "《绝区零》官网-3.3全新版本「重返天空的旅程」现已上线！", "snippet": "1 天前"}
        jupyter = {"title": "Jupyter Notebook 使用教程", "snippet": "安装与入门"}
        self.assertLess(ws.relevance("绝区零 最新版本", junk), ws.MIN_RELEVANCE)
        self.assertLess(ws.relevance("绝区零 最新版本", jupyter), ws.MIN_RELEVANCE)
        self.assertGreaterEqual(ws.relevance("绝区零 最新版本", good), ws.MIN_RELEVANCE)
        merged = ws.merge("绝区零 最新版本", [[junk, good, jupyter], [dict(good), {"title": "绝区零版本更新信息整理", "snippet": ""}]], 5)
        self.assertEqual([m["title"][:6] for m in merged], ["《绝区零》官", "绝区零版本更"])

    def test_parse_ddg(self):
        page = ('<div class="result__body"><h2><a class="result__a" href="https://zzz.mihoyo.com/">《绝区零》官网</a></h2>'
                '<a class="result__snippet" href="x">3.3 全新版本</a></div>')
        self.assertEqual(ws.parse_ddg(page), [{"title": "《绝区零》官网", "url": "https://zzz.mihoyo.com/", "snippet": "3.3 全新版本"}])

    def test_baidu_comment_json_stripped(self):
        page = ('<div class="result c-container"><h3><a href="http://b/1">绝区零下载</a></h3>'
                '<!--s-data:{"summaryData":1}--><span>正文</span></div>')
        self.assertEqual(ws.parse_baidu(page)[0]["snippet"], "正文")

    def test_format(self):
        text = ws.format_results("原神 卡池", ws.parse_bing(BING))
        self.assertIn("原神 6.2 卡池", text)
        self.assertIn("原神 卡池", text)
        self.assertIn("不要提", text)
        self.assertIn("别提搜索", ws.format_results("x", []))


class WebSearchChatTest(unittest.TestCase):
    def tearDown(self):
        _env.write_env(_BASE)
        permissions.set_feature_enabled(str(GID), "websearch", True)

    def test_extract(self):
        self.assertEqual(chat_ws.extract("<<SEARCH:原神 6.2 卡池>>"), ("", "原神 6.2 卡池"))
        self.assertEqual(chat_ws.extract("等下 <<搜索：今天天气>>"), ("等下", "今天天气"))
        self.assertEqual(chat_ws.extract("[SEARCH:黑神话]"), ("", "黑神话"))
        self.assertEqual(chat_ws.extract("普通回复"), ("普通回复", None))

    def test_explicit_query(self):
        self.assertEqual(chat_ws.explicit_query("帮我搜一下原神新版本"), "原神新版本")
        self.assertEqual(chat_ws.explicit_query("你查查明天上海天气吧"), "明天上海天气")
        self.assertIsNone(chat_ws.explicit_query("搜一下这张图"))
        self.assertIsNone(chat_ws.explicit_query("今天吃什么"))
        self.assertIsNone(chat_ws.explicit_query("搜图"))

    def test_enabled_switches(self):
        _env.write_env(_BASE)
        self.assertTrue(chat_ws.enabled(GID, True))
        self.assertFalse(chat_ws.enabled(GID, False))  # 随机接话默认不搜
        permissions.set_feature_enabled(str(GID), "websearch", False)
        self.assertFalse(chat_ws.enabled(GID, True))
        permissions.set_feature_enabled(str(GID), "websearch", True)
        _env.write_env(_BASE + "AI_CHAT_WEB_SEARCH=false\n")
        self.assertFalse(chat_ws.enabled(GID, True))

    def test_lookup_formats_and_caches(self):
        _env.write_env(_BASE)
        fake = mock.AsyncMock(return_value=ws.parse_bing(BING))
        with mock.patch.object(chat_ws.ws, "search", fake):
            note = asyncio.run(chat_ws.lookup("原神 卡池", GID))
        self.assertIn("原神 6.2 卡池", note or "")

    def test_leaks(self):
        self.assertTrue(chat_ws.leaks("我搜出来全是 Jupyter 的教程", "你知道绝区零现在什么版本吗"))
        self.assertTrue(chat_ws.leaks("根据搜索结果，现在是 3.3", "绝区零几版本了"))
        self.assertFalse(chat_ws.leaks("3.3 了吧，前两天刚出", "绝区零几版本了"))
        self.assertFalse(chat_ws.leaks("我搜了下，是 3.3", "帮我搜一下绝区零版本"))  # 对方叫她搜的可以说

    def test_add_to_system(self):
        msgs = [{"role": "system", "content": "人设"}, {"role": "user", "content": "hi"}]
        chat_ws.add_to_system(msgs, "【搜索结果】x")
        self.assertIn("【搜索结果】x", msgs[0]["content"])
        self.assertEqual(len(msgs), 2)


class SelfActsTest(unittest.TestCase):
    def tearDown(self):
        _env.write_env(_BASE)

    def test_record_and_note(self):
        g = GID + 7
        now = time.time()
        selfacts.record(g, "react", "给阿成的「考过了」贴了个「赞」", target_uid="123", mid="9", now=now - 60)
        selfacts.record(g, "poke", "回完话顺手戳了戳小李", target_uid="456", now=now - 30)
        text = selfacts.note(g, "123", now=now)
        self.assertIn("贴了个「赞」", text)
        self.assertIn("戳了戳小李", text)
        self.assertIn("大方承认", text)
        self.assertEqual(selfacts.note(g, "123", now=now + 13 * 3600), "")  # 太久以前的不提
        _env.write_env(_BASE + "AI_CHAT_SELF_ACTS=false\n")
        self.assertEqual(selfacts.note(g, "123", now=now), "")

    def test_annotate_shows_in_transcript(self):
        g = GID + 8
        memory.reset(g)
        memory.add_message(g, "user", "阿成：我考过了！", meta={"uid": "123", "mid": "77", "name": "阿成"})
        self.assertTrue(memory.annotate(g, "77", "reacted", "赞"))
        self.assertFalse(memory.annotate(g, "78", "reacted", "赞"))
        entry = memory.get_history(g)[-1]
        self.assertEqual(entry["reacted"], "赞")
        self.assertIn("你给这条贴了「赞」表情", transcript.render(memory.get_history(g))[0])
        memory.reset(g)


class TalkShapeTest(unittest.TestCase):
    def tearDown(self):
        _env.write_env(_BASE)

    def test_note_variants(self):
        _env.write_env(_BASE)
        self.assertIn("认真", talkshape.note("今天好累，想哭"))
        self.assertIn("嘴硬", talkshape.note("你就是在吹牛"))
        self.assertIn("被夸", talkshape.note("芙芙好厉害"))
        rng = random.Random(3)
        seen = {talkshape.note("今天吃啥", rng=rng) for _ in range(40)}
        self.assertGreater(len(seen), 3)
        _env.write_env(_BASE + "AI_CHAT_TALK_SHAPE=false\n")
        self.assertEqual(talkshape.note("今天好累"), "")

    def test_repeat_note(self):
        g = GID + 9
        memory.reset(g)
        memory.add_message(g, "user", "甲：草", meta={"uid": "1", "mid": "1", "name": "甲"})
        memory.add_message(g, "user", "乙：草", meta={"uid": "2", "mid": "2", "name": "乙"})
        self.assertIn("复读「草」", talkshape.repeat_note(g))
        memory.reset(g)
        memory.add_message(g, "user", "甲：草", meta={"uid": "1", "mid": "3", "name": "甲"})
        self.assertEqual(talkshape.repeat_note(g), "")
        memory.reset(g)


class VoiceEmotionTest(unittest.TestCase):
    def tearDown(self):
        _env.write_env(_BASE)

    def test_marker_with_emotion(self):
        self.assertEqual(tts.extract("[VOICE:傲娇] 哼，才不是"), ("哼，才不是", True, "tsun"))
        self.assertEqual(tts.extract("【语音：神气】看我的"), ("看我的", True, "proud"))
        self.assertEqual(tts.extract("[VOICE] 晚安"), ("晚安", True, None))
        self.assertEqual(tts.extract("普通"), ("普通", False, None))

    def test_classify(self):
        self.assertEqual(tts.classify("哼，才不是因为你呢"), "tsun")
        self.assertEqual(tts.classify("本芙宁娜宣布，演出开始！"), "proud")
        self.assertEqual(tts.classify("别难过，我在呢"), "calm")
        self.assertEqual(tts.classify("诶，吃了吗"), "daily")

    def test_pick_ref_falls_back(self):
        a = _env.TMP / "calm.wav"
        b = _env.TMP / "tsun.wav"
        a.write_bytes(b"RIFF")
        b.write_bytes(b"RIFF")
        _env.write_env(_BASE + f"FURINA_TTS_ENABLED=true\nFURINA_TTS_REF_WAV={a}\nFURINA_TTS_REF_TEXT=平静\n"
                       f"FURINA_TTS_REF_TSUN_WAV={b}\nFURINA_TTS_REF_TSUN_TEXT=傲娇\n")
        self.assertEqual(voice_tts.pick_ref("tsun")[1:], ("傲娇", "tsun"))
        self.assertEqual(voice_tts.pick_ref("proud")[1:], ("平静", "calm"))  # 没配神气 → 平静
        self.assertEqual(voice_tts._steps(10), 8)
        self.assertEqual(voice_tts._steps(32), 32)

    def test_finish_and_polish(self):
        import io
        import struct
        import wave

        self.assertEqual(voice_tts.finish_text("哈哈，好呀，"), "哈哈，好呀。")
        self.assertEqual(voice_tts.finish_text("真的吗？"), "真的吗？")
        self.assertNotIn("，", voice_tts.CUT_PUNC)  # 不按逗号切，免得每段都吞尾
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"".join(struct.pack("<h", 8000 if (i // 20) % 2 else -8000) for i in range(24000)))
        out = voice_tts.polish_wav(buf.getvalue(), 0.35, 500)
        with wave.open(io.BytesIO(out)) as w:
            self.assertEqual(w.getnframes(), 24000 + 12000)  # 补了 0.5 秒静音
            frames = w.readframes(w.getnframes())
        tail = struct.unpack("<200h", frames[-400:])
        self.assertEqual(max(abs(v) for v in tail), 0)
        self.assertEqual(voice_tts.polish_wav(b"not a wav"), b"not a wav")

    def test_can_start_guards(self):
        _env.write_env(_BASE)
        s = voice_tts.settings()
        ok_res = {"ram": 6.0, "commit": 20.0, "vram": 5.0, "game": ""}
        self.assertEqual(voice_tts.can_start(s, ok_res), (True, ""))
        self.assertFalse(voice_tts.can_start(s, {**ok_res, "game": "YuanShen.exe"})[0])
        self.assertFalse(voice_tts.can_start(s, {**ok_res, "ram": 1.0})[0])
        self.assertFalse(voice_tts.can_start(s, {**ok_res, "commit": 5.0})[0])
        self.assertFalse(voice_tts.can_start(s, {**ok_res, "vram": 1.0})[0])
        self.assertTrue(voice_tts.can_start(s, {"ram": -1, "commit": -1, "vram": -1, "game": ""})[0])  # 取不到不拦


class CommandParseTest(unittest.TestCase):
    def test_talk_to_me_is_not_relay(self):
        from furina.plugins.chat import commands

        self.assertIsNone(commands._parse_command("跟我说说仆人吧，你怕她吗"))
        self.assertIsNone(commands._parse_command("和我聊聊原神"))
        self.assertIsNone(commands._parse_command("给我讲讲那维莱特吧"))
        cmd = commands._parse_command("跟小明说晚上开黑")
        self.assertEqual((cmd or {}).get("target"), "小明")


class NoPeriodTest(unittest.TestCase):
    def test_drop_periods(self):
        from furina.plugins.chat.cleanup import _split_reply_parts, drop_periods

        self.assertEqual(drop_periods("好吧。"), "好吧")
        self.assertEqual(drop_periods("行。我知道了。"), "行，我知道了")
        self.assertEqual(drop_periods("这搜索有点离谱。要不你直接说？"), "这搜索有点离谱，要不你直接说？")
        self.assertEqual(drop_periods("……。。。"), "……。。。")  # 连打的句号是在表达无语，保留
        self.assertEqual(drop_periods("哼。<<STICKER:3>>"), "哼<<STICKER:3>>")
        self.assertEqual(drop_periods("3.3 了吧，前两天刚出。"), "3.3 了吧，前两天刚出")
        self.assertEqual(drop_periods("第一行。\n第二行。"), "第一行\n第二行")
        with mock.patch("random.random", return_value=0.0):
            parts = _split_reply_parts(drop_periods("绝区零现在是三点三版本了。前两天刚更新的你没玩吗"))
        self.assertEqual(parts, ["绝区零现在是三点三版本了", "前两天刚更新的你没玩吗"])

    def test_voice_pauses_at_spaces(self):
        self.assertEqual(voice_tts.speakable("行 我知道了"), "行，我知道了")


class VoiceKeepAliveTest(unittest.TestCase):
    def _s(self, **kw):
        s = {"pause_games": True, "stop_ram": 1.0, "stop_commit": 1.0, "stop_vram": 0.8,
             "min_ram": 2.0, "min_commit": 10.0, "min_vram": 2.5, "keep_alive": True, "autostart": True}
        s.update(kw)
        return s

    def test_must_yield(self):
        s = self._s()
        self.assertEqual(voice_tts.must_yield(s, {"ram": 5, "commit": 3, "vram": 4, "game": ""}), "")
        self.assertIn("内存", voice_tts.must_yield(s, {"ram": 0.6, "commit": 3, "vram": 4, "game": ""}))
        self.assertIn("提交内存", voice_tts.must_yield(s, {"ram": 5, "commit": 0.5, "vram": 4, "game": ""}))
        self.assertIn("显存", voice_tts.must_yield(s, {"ram": 5, "commit": 3, "vram": 0.3, "game": ""}))
        self.assertIn("游戏", voice_tts.must_yield(s, {"ram": 9, "commit": 30, "vram": 8, "game": "YuanShen.exe"}))
        self.assertEqual(voice_tts.must_yield(s, {"ram": -1, "commit": -1, "vram": -1, "game": ""}), "")

    def test_keep_alive_needs_headroom_and_cooldown(self):
        s = self._s()
        plenty = {"ram": 6, "commit": 14, "vram": 6, "game": ""}
        with mock.patch.object(voice_tts, "enabled", return_value=True), \
                mock.patch.object(voice_tts, "resources", return_value=plenty):
            voice_tts._state["yielded_at"] = 0.0
            voice_tts._state["last_fail"] = 0.0
            self.assertTrue(voice_tts.want_keep_alive(s))
            voice_tts._state["yielded_at"] = time.time()  # 刚让过路：冷却中
            self.assertFalse(voice_tts.want_keep_alive(s))
            voice_tts._state["yielded_at"] = 0.0
            self.assertFalse(voice_tts.want_keep_alive(self._s(keep_alive=False)))
        tight = {"ram": 3, "commit": 10.2, "vram": 6, "game": ""}  # 够开机门槛，但开完会掉到让路线以下
        with mock.patch.object(voice_tts, "enabled", return_value=True), \
                mock.patch.object(voice_tts, "resources", return_value=tight):
            self.assertFalse(voice_tts.want_keep_alive(s))


class ReminderParseTest(unittest.TestCase):
    def test_voice_request_is_not_reminder(self):
        from furina.services.reminder.parser import candidate

        self.assertFalse(candidate("发语音跟我说让我明天早点起床吃早饭"))
        self.assertFalse(candidate("发语音跟我说让我明天早点起"))
        self.assertFalse(candidate("明天早点跟我说一声"))
        self.assertTrue(candidate("明天早上8点提醒我起床"))
        self.assertTrue(candidate("明天8点跟我说起床"))
        self.assertTrue(candidate("发条语音提醒我明天8点起床"))


if __name__ == "__main__":
    unittest.main()
