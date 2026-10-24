"""小鲸鱼全套（ai-companion-kit / qq-bridge / QQ-agent）：每个人一本账、等说完、察觉、日记、自己的时间、相册、去 AI 味。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import json
import random
import time
import unittest
from unittest import mock

from furina.core import db
from furina.plugins.chat import boundary, kouchan  # noqa: E402
from furina.plugins.chat import (attention, catchup, cleanup, deai, diary, gallery, humanize, memory, ownlife,
                                 people, proactive, quotes, tts, turns)
from furina.services import voice_asr

GID = 100000004
BASE = "BOT_QQ=30000009\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"


class PeopleTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE)

    def test_observe_is_cross_group_and_tracks_hours(self):
        uid = "880001"
        people.observe(GID, uid, "阿成", "今天好热", now=1_000_000)
        people.observe(100000003, uid, "阿成", "在吗", now=1_000_100)
        rec = people.get(uid)
        self.assertEqual(set(rec["groups"]), {str(GID), "100000003"})
        self.assertEqual(sum(rec["hours"]), 2)

    def test_away_then_chase_once(self):
        uid = "880002"
        t0 = 2_000_000.0
        people.observe(GID, uid, "小明", "等我洗个澡再聊", now=t0)
        self.assertIsNone(people.due_pending(uid, now=t0 + 60))
        item = people.due_pending(uid, now=t0 + 95 * 60)
        self.assertIsNotNone(item)
        people.mark_chased(uid, item, now=t0 + 95 * 60)
        self.assertIsNone(people.due_pending(uid, now=t0 + 96 * 60))

    def test_back_cancels_chase(self):
        uid = "880003"
        t0 = 3_000_000.0
        people.observe(GID, uid, "小红", "先去吃饭了", now=t0)
        people.observe(GID, uid, "小红", "吃完了", now=t0 + 20 * 60)
        self.assertIsNone(people.due_pending(uid, now=t0 + 95 * 60))

    def test_merge_dedup_and_done(self):
        rec = people._blank("1", "x", 0)
        people.merge(rec, {"traits": ["熬夜打游戏到两点"], "facts": ["养了只橘猫叫煤球"],
                           "pending": [{"text": "周五面试", "after_hours": 48}]}, 10)
        people.merge(rec, {"traits": ["熬夜打游戏到两点"], "done": ["周五面试"]}, 20)
        self.assertEqual(people.texts(rec, "traits"), ["熬夜打游戏到两点"])
        self.assertEqual(int(rec["traits"][0].get("rc") or 0), 1)  # 又提一遍 = 记得更牢
        self.assertEqual(rec["pending"], [])

    def test_note_mentions_days_and_skips_bots(self):
        uid = "880004"
        people.observe(GID, uid, "阿花", "嗨", now=time.time() - 3 * 86400)
        note = people.note(uid, "阿花", GID)
        self.assertIn("认识第", note)
        people.observe(GID, "30000009", "我自己", "嗨")
        self.assertIsNone(people.get("30000009"))

    def test_first_interaction_does_not_extract_immediately(self):
        uid = "880005"
        with mock.patch.object(people, "_spawn") as spawn:
            people.note_interaction(GID, uid, "阿草", "你好", "嗯")
        spawn.assert_not_called()


class TurnsTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE)

    def test_looks_unfinished(self):
        for t in ("我跟你说", "然后", "那个，", "主要是"):
            self.assertTrue(turns.looks_unfinished(t), t)
        for t in ("今天好热", "好的！", "然后呢？", ""):
            self.assertFalse(turns.looks_unfinished(t), t)

    def test_quiet_window(self):
        turn = turns.Turn(GID, "1")
        self.assertEqual(turns.quiet_window("在吗", turn), (1.5, 3.0))
        self.assertEqual(turns.quiet_window("我跟你说", turn)[0], 12.0)
        self.assertEqual(turns.quiet_window("我跟你说", turn)[1], 90.0)
        self.assertEqual(turns.quiet_window("今天好热", turn), (6.0, 20.0))

    def test_unfinished_note(self):
        note = turns.unfinished_note("我跟你说", turns.Turn(GID, "1"), random.Random(1))
        self.assertIn("没说完", note)


class HumanizeTest(unittest.TestCase):
    def test_typing_bounds(self):
        _env.write_env(BASE)
        for text in ("", "嗯", "一" * 200):
            s = humanize.typing_seconds(text)
            self.assertGreaterEqual(s, 1.2 * 0.8 - 1e-9)
            self.assertLessEqual(s, 6.0 * 1.2 * 1.6 + 1e-9)

    def test_long_gap_probability(self):
        _env.write_env(BASE + "AI_CHAT_LONG_GAP_PROBABILITY=1\n")
        self.assertGreaterEqual(humanize._long_gap(), 4.0)
        _env.write_env(BASE + "AI_CHAT_LONG_GAP_PROBABILITY=0\n")
        self.assertEqual(humanize._long_gap(), 0.0)


class AttentionTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE)
        attention.reset()
        memory.reset(GID)

    def test_mirror_and_qmark(self):
        self.assertIn("喵", attention.mirror_note("喵"))
        self.assertEqual(attention.mirror_note("今天好热"), "")
        self.assertIn("？", attention.qmark_note(GID, "1", "？"))

    def test_no_reaction_after_voice(self):
        now = time.time()
        attention.record_outgoing(GID, "77", "voice", "你听听", now=now - 300)
        for i in range(2):
            memory.add_message(GID, "user", f"小王：别的事{i}", ts=now - 100 + i, meta={"uid": "77", "mid": 900 + i})
        note = attention.no_reaction_note(GID, "77", "别的事", now=now, rng=random.Random(1))
        self.assertIn("语音", note)
        self.assertEqual(attention.no_reaction_note(GID, "77", "别的事", now=now), "")  # 只提一次

    def test_stale_note(self):
        now = time.time()
        self.assertEqual(attention.stale_note(now - 60, now), "")
        self.assertIn("时间感", attention.stale_note(now - 30 * 60, now))
        self.assertIn("才看到", attention.stale_note(now - 200 * 60, now))

    def test_linger_and_wrapup(self):
        _env.write_env(BASE + "AI_CHAT_LINGER=1\n")
        now = 5_000_000.0
        attention.on_reply(GID, now=now, rng=random.Random(2))
        self.assertEqual(attention.linger_probability(GID, now + 10), 0.3)
        self.assertEqual(attention.linger_probability(GID, now + 3600), 0.0)

    def test_group_note(self):
        _env.write_env(BASE + f"AI_CHAT_GROUP_NOTES=123=别的群|{GID}=原神同好群，爱玩梗\n")
        self.assertIn("原神同好群", attention.group_note(GID))
        self.assertEqual(attention.group_note(999), "")


class DiaryOwnLifeTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE)

    def test_parse(self):
        self.assertIsNone(diary.parse("不写"))
        d = diary.parse('好的 {"title": "布丁", "mood": "开心", "tags": ["吃"], "text": "**今天**偷吃了两个布丁"}')
        self.assertEqual(d["text"], "今天偷吃了两个布丁")

    def test_add_and_context(self):
        e = diary.add({"title": "下雨", "mood": "懒", "text": "窗外一直在下雨，什么都不想干"})
        self.assertTrue(e)
        self.assertIn("别装作不记得", diary.context_note())

    def test_write_respects_daily_cap(self):
        _env.write_env(BASE + "AI_CHAT_DIARY_DAILY_MAX=0\n")
        with mock.patch.object(diary.llm, "chat", mock.AsyncMock(return_value="{}")) as chat:
            self.assertIsNone(asyncio.run(diary.write()))
        chat.assert_not_called()

    def test_ownlife_due_and_parse(self):
        db.delete(ownlife.NS, "state")
        noon = time.mktime(time.strptime("2026-10-22 12:00", "%Y-%m-%d %H:%M"))
        self.assertEqual(ownlife.due(noon), "normal")
        ownlife._mark("normal", noon, random.Random(0))
        self.assertEqual(ownlife.due(noon + 600), "")
        night = time.mktime(time.strptime("2026-10-22 05:00", "%Y-%m-%d %H:%M"))
        self.assertEqual(ownlife.due(night), "")
        self.assertEqual(ownlife.parse('{"do": "bili", "doing": "刷视频"}', {"idle": "", "diary": ""}), ("idle", "刷视频"))


class MiscTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE)

    def test_gallery_extract(self):
        text, why = gallery.extract("哈哈哈这个好好笑<<KEEP:表情太欠了>>")
        self.assertEqual((text, why), ("哈哈哈这个好好笑", "表情太欠了"))
        self.assertEqual(gallery.extract("普通回复"), ("普通回复", ""))

    def test_deai_blacklist(self):
        for t in ("这笔账我记下了", "我一直都在", "听起来你很累"):
            self.assertTrue(deai.hard_problem(t), t)
        for t in ("哈哈哈笑死", "我在吃饭", "记下了"):
            self.assertFalse(deai.hard_problem(t), t)

    def test_cjk_space_becomes_comma(self):
        self.assertEqual(cleanup.burst_parts("哈哈 笑死我了"), ["哈哈，笑死我了"])
        self.assertEqual(cleanup.burst_parts("我玩 LOL 呢"), ["我玩 LOL 呢"])

    def test_card_and_voice_text(self):
        card = json.dumps({"prompt": "[QQ小程序]哔哩哔哩", "meta": {"detail_1": {"title": "哔哩哔哩", "desc": "猫猫翻跟头"}}},
                          ensure_ascii=False)
        msg = [{"type": "json", "data": {"data": card}}, {"type": "record", "data": {"file": "a.amr"}}]
        with mock.patch.object(quotes, "_iter_segments", return_value=[(m["type"], m["data"]) for m in msg]):
            text = quotes._extract_text(None)
        self.assertIn("[卡片] 哔哩哔哩（猫猫翻跟头）", text)
        self.assertIn("[语音]", text)

    def test_asr_output_parse_and_off(self):
        self.assertEqual(voice_asr.parse_output('loading...\n{"text": "你好呀"}\n'), "你好呀")
        _env.write_env(BASE + "AI_CHAT_HEAR_VOICE=0\n")
        self.assertFalse(voice_asr.available()[0])

    def test_catchup_needs_fresh_wake(self):
        now = time.time()
        catchup._pending[str(GID)] = [{"uid": "5", "name": "a", "text": "在吗", "ts": now - 600, "mid": 1}]
        db.delete("presence", "wake")
        self.assertIsNone(catchup.pick(GID, now))
        db.put("presence", "wake", {"from": now - 3600, "at": now - 60, "told": {}})
        self.assertEqual(catchup.pick(GID, now)["text"], "在吗")
        catchup._pending.clear()
        db.delete("presence", "wake")


class LeftoverMarkerTest(unittest.TestCase):
    """10:53 惦记发出了「@某人 [」「[」：标记被剥掉后剩下的残渣不能发出去。"""

    def setUp(self):
        _env.write_env(BASE)

    def test_junk_lines_dropped(self):
        self.assertEqual(proactive._clean("[\n[", 2), "")
        self.assertEqual(proactive._clean("[AT:300000001:你这几天跑哪去了]", 2), "你这几天跑哪去了")
        self.assertEqual(proactive._clean("[AT:300000001:\n上次那事咋样了", 2), "上次那事咋样了")
        self.assertEqual(cleanup.burst_parts("[\n哈哈哈\n】"), ["哈哈哈"])
        self.assertEqual(cleanup.burst_parts("？"), ["？"])
        self.assertEqual(cleanup.burst_parts("。。。"), ["。。。"])


class VoiceStateTest(unittest.TestCase):
    def test_no_voice_note_when_cannot_speak(self):
        _env.write_env(BASE)
        with mock.patch.object(tts, "available", return_value=True), \
                mock.patch.object(tts.voice_tts, "is_online", mock.AsyncMock(return_value=False)), \
                mock.patch.object(tts.voice_tts, "can_start", return_value=(False, "可用内存只剩 1.2G")):
            self.assertEqual(asyncio.run(tts.refresh_state(GID)), "no")
            note = tts.voice_note(GID)
        self.assertIn("发不了语音", note)
        self.assertNotIn("内存只剩", note)
        with mock.patch.object(tts, "available", return_value=True), \
                mock.patch.object(tts.voice_tts, "is_online", mock.AsyncMock(return_value=True)):
            self.assertEqual(asyncio.run(tts.refresh_state(GID)), "hot")
            self.assertIn("别预告", tts.voice_note(GID))


class GameCommandVsChatTest(unittest.TestCase):
    """「@芙芙 绝区零开播了，你也去看看直播」被当成查角色指令，回了「芙芙没听懂…」。"""

    def test_reserved_only_for_real_commands(self):
        from furina.services import gscore
        gscore._namespace_cache = (time.monotonic() + 3600, ("绝区零", "zzz", "原神", "ys"), ())
        try:
            for t in ("zzz每日", "绝区零星见雅攻略", "ys芙宁娜", "原神深渊", "指令帮助", "绝区零"):
                self.assertTrue(gscore.reserved_for_core(t), t)
            for t in ("绝区零开播了，你也去看看直播", "原神好玩吗", "原神启动", "绝区零新版本什么时候出", "今天好热"):
                self.assertFalse(gscore.reserved_for_core(t), t)
        finally:
            gscore._namespace_cache = (0.0, (), ())


class AtNameTest(unittest.TestCase):
    """「叫@小燕，不是我」：艾特段原来被整个丢掉，她只看到「叫，不是我」，就猜成了别人。"""

    def test_at_rendered_with_name(self):
        _env.write_env(BASE)
        people.observe(GID, "300000002", "小燕", "十二点记得提醒我打脚")
        msg = [{"type": "text", "data": {"text": "叫"}}, {"type": "at", "data": {"qq": "300000002"}},
               {"type": "text", "data": {"text": "，不是我"}}]
        self.assertEqual(quotes._extract_text(msg), "叫@小燕，不是我")
        self.assertEqual(quotes.at_label("30000009"), "")  # 艾特她自己不写进正文
        self.assertEqual(quotes.at_label("123456789000"), "@某人")


class ForwardQuoteTest(unittest.TestCase):
    """回复她推送的米游社合并转发说「妈妈来了」，她以为是群主来了（「回来啦，正好」）。"""

    def test_forward_expanded_and_framed_as_post(self):
        _env.write_env(BASE)
        from furina.plugins.chat import prompting

        class FakeBot:
            async def get_forward_msg(self, id):
                # 适配器返回的就是 data 本身，没有外层 data
                return {"messages": [{"sender": {"nickname": "米游社推送"},
                                      "message": [{"type": "text", "data": {"text": "新角色「某某」角色演示"}},
                                                  {"type": "video", "data": {}}]}]}

        info = asyncio.run(quotes._parse_reply_message(
            [{"type": "forward", "data": {"id": "7561234567890"}}], bot=FakeBot()))
        self.assertTrue(info.forward)
        self.assertIn("新角色「某某」", info.text)
        info.sender_id, info.sender_name = "30000009", "芙宁娜"
        ev = mock.Mock(message=[], group_id=GID, user_id=200000001)
        out = prompting.build_user_content(ev, "群主", "妈妈来了", False, info, True, [], 0, "")
        self.assertIn("那不是你说的话", out)
        self.assertIn("新角色「某某」", out)
        self.assertNotIn("引用了你之前说的", out)

    def test_inline_forward_and_unreadable_quote(self):
        _env.write_env(BASE)
        from furina.plugins.chat import prompting
        seg = {"type": "forward", "data": {"id": "x", "content": [
            {"sender": {"nickname": "A"}, "message": [{"type": "text", "data": {"text": "好看"}}]}]}}
        info = asyncio.run(quotes._parse_reply_message([seg], bot=object()))
        self.assertTrue(info.forward)
        self.assertIn("A: 好看", info.text)
        empty = quotes.QuoteInfo(images=0, text="")
        ev = mock.Mock(message=[], group_id=GID, user_id=1)
        out = prompting.build_user_content(ev, "群主", "妈妈来了", False, empty, True, [], 0, "")
        self.assertIn("打不开的消息", out)


class KouchanTest(unittest.TestCase):
    """「眼睛画得是挺绝的，就是你这连发几张的架势有点吓人」：先夸后挑刺的对仗句、评论家腔、说烂的口头禅。"""

    def test_hard_templates(self):
        for t in ("这眼睛画得是挺绝的，就是你这连发几张的架势有点吓人", "哎呀，这图看着挺好看的，就是有点晃眼",
                  "这图确实好看啊，光影和花衬得人特别温柔"):
            self.assertTrue(kouchan.hard_template(t), t)
        for t in ("好看", "眼睛好好看", "存了", "没事，下次一定", "挺好的就是贵"):
            self.assertEqual(kouchan.hard_template(t), "", t)
        self.assertEqual(kouchan.trim_template("这图看着挺好看的，就是有点晃眼"), "这图看着挺好看的")

    def test_overused_across_groups_not_topic(self):
        recent = []
        for i, t in enumerate(["行行行，发就发", "别戳啦，痒", "好好好，晚安", "别戳啦，痒痒的", "行吧行吧，听你的",
                               "别戳啦，痒", "凯撒那个梗", "凯撒笑死", "凯撒又来"]):
            # 凯撒三条挤在 10 分钟内 = 那段聊天的话题，不算口癖
            ts = 50_000 + i * 100 if t.startswith("凯撒") else 1000 + i * 3600
            recent.append((ts, 1 + i % 2, t))
        o = kouchan.overused(recent)
        self.assertIn("别戳啦", o["phrases"])
        self.assertFalse(any("凯撒" in p for p in o["phrases"]))
        self.assertTrue(any("行行行" in h for h in o["habits"]))
        with mock.patch.object(kouchan, "recent_replies", return_value=recent):
            self.assertIn("别戳啦", kouchan.note())
            self.assertTrue(kouchan.reply_hits("别戳啦，手不累啊"))
            self.assertEqual(kouchan.reply_hits("干嘛"), [])


class BoundaryTest(unittest.TestCase):
    """除群主和特别关照的人外最多抱抱：亲亲警告两次第三次冷落（重的一次算两次），求情悔改可原谅、一次比一次难，原谅后再犯直接冷落。"""

    def setUp(self):
        _env.write_env(BASE + "AI_CHAT_GROUP_OWNER_QQ=200000001\nAI_CHAT_SPECIAL_QQ=11112222\n")
        for u in ("200000003", "200000001", "11112222"):
            boundary.reset(u)

    def test_offense_detection(self):
        for t in ("芙芙堂主我来喽（亲了好几口）", "芙芙让我亲一下", "*扑倒*", "来涩涩", "mua", "芙芙我想舔你的脚"):
            self.assertTrue(boundary.offense(t), t)
        for t in ("（抱抱）", "(摸摸头)", "贴贴", "我上床睡觉了", "亲爱的芙芙", "我亲戚来了", "舔屏了", "摸鱼中",
                  "今天去相亲", "（抱住芙芙转圈圈）", "你是舔狗"):
            self.assertEqual(boundary.offense(t), "", t)

    def test_kiss_warns_twice_then_cold(self):
        uid, t0 = "200000003", 1_000_000.0
        w1 = boundary.check(uid, "堂主", "（亲了好几口）", now=t0)
        self.assertIn("不许亲", w1)
        self.assertIn("不理你", w1)
        self.assertFalse(boundary.ignoring(uid, now=t0 + 1))
        w2 = boundary.check(uid, "堂主", "mua", now=t0 + 600)
        self.assertIn("最后一次警告", w2)
        self.assertFalse(boundary.ignoring(uid, now=t0 + 601))
        self.assertIn("说到做到", boundary.check(uid, "堂主", "芙芙让我亲一下", now=t0 + 1200))
        self.assertTrue(boundary.ignoring(uid, now=t0 + 1201))
        self.assertFalse(boundary.ignoring(uid, now=t0 + 1200 + 6 * 3600 + 1))
        for t in ("*扑倒*", "来涩涩", "（强吻）", "（亲了一口又舔了一下）", "让我舔"):
            self.assertEqual(boundary.classify(t)[0], "heavy", t)
        for t in ("（蹭蹭）", "么么哒", "亲你的嘴"):
            self.assertEqual(boundary.classify(t)[0], "mild", t)

    def test_warn_times_setting(self):
        _env.write_env(BASE + "AI_CHAT_BOUNDARY_WARNS=0\n")
        uid = "200000003"
        self.assertIn("说到做到", boundary.check(uid, "堂主", "mua", now=1000.0))
        self.assertTrue(boundary.ignoring(uid, now=1001.0))

    def _make_cold(self, uid, t):
        boundary.check(uid, "堂主", "（舔了一下）", now=t)
        boundary.check(uid, "堂主", "（摸了摸腿）", now=t + 100)
        self.assertTrue(boundary.ignoring(uid, now=t + 101))

    def test_first_apology_forgives_then_reoffend_is_cold(self):
        uid, t0 = "200000003", 1_000_000.0
        self._make_cold(uid, t0)
        self.assertEqual(boundary.plead(uid, "堂主", "对不起嘛亲一个", now=t0 + 200), "")   # 认错里又冒犯：不算
        self.assertEqual(boundary.plead(uid, "堂主", "芙芙今天吃什么", now=t0 + 200), "")   # 不是认错
        self.assertTrue(boundary.ignoring(uid, now=t0 + 201))
        self.assertEqual(boundary.plead(uid, "堂主", "芙芙对不起，我错了", now=t0 + 300), "forgiven")
        self.assertFalse(boundary.ignoring(uid, now=t0 + 301))
        note = boundary.pop_forgive_note(uid, now=t0 + 310)
        self.assertIn("原谅", note)
        self.assertEqual(boundary.pop_forgive_note(uid, now=t0 + 311), "")        # 只用一次
        # 原谅以后再犯：哪怕只是亲亲，也直接冷落 6 小时
        self.assertIn("原谅过还再犯", boundary.check(uid, "堂主", "mua", now=t0 + 7200))
        self.assertTrue(boundary.ignoring(uid, now=t0 + 7201))
        self.assertFalse(boundary.ignoring(uid, now=t0 + 7200 + 6 * 3600 + 1))

    def test_forgiveness_gets_harder(self):
        uid, t = "200000003", 1_000_000.0
        self._make_cold(uid, t)
        self.assertEqual(boundary.plead(uid, "堂主", "我错了", now=t + 200), "forgiven")   # 第 1 次：一句就行
        t += 1000
        boundary.check(uid, "堂主", "（亲亲）", now=t)
        self.assertTrue(boundary.ignoring(uid, now=t + 1))
        # 第 2 次：要 3 点且保证不再犯；同一句复读、刷屏不加分
        self.assertEqual(boundary.plead(uid, "堂主", "对不起", now=t + 100), "noted")
        self.assertEqual(boundary.plead(uid, "堂主", "对不起", now=t + 200), "noted")
        self.assertEqual(boundary.plead(uid, "堂主", "我错了芙芙", now=t + 300), "softening")  # 心软回一句，但没原谅
        self.assertIn("看你表现", boundary.pop_forgive_note(uid, now=t + 301))
        self.assertTrue(boundary.ignoring(uid, now=t + 302))
        self.assertEqual(boundary.plead(uid, "堂主", "我保证以后不会再这样了", now=t + 400), "forgiven")
        self.assertFalse(boundary.ignoring(uid, now=t + 401))
        self.assertIn("不太情愿", boundary.pop_forgive_note(uid, now=t + 402))
        # 第 3 次：6 点、还得磨 10 分钟
        t += 2000
        boundary.check(uid, "堂主", "（亲亲）", now=t)
        self.assertEqual(boundary.forgive_level(uid, now=t), 2)
        self.assertEqual(boundary.plead(uid, "堂主", "求求你原谅我，我保证再也不敢了", now=t + 60), "softening")
        self.assertEqual(boundary.plead(uid, "堂主", "对不起芙芙大人我真的会改", now=t + 120), "noted")  # 点数够了但时间不够
        self.assertTrue(boundary.ignoring(uid, now=t + 121))
        self.assertEqual(boundary.plead(uid, "堂主", "拜托理理我嘛，以后只抱抱", now=t + 700), "forgiven")
        self.assertIn("第 3 次", boundary.pop_forgive_note(uid, now=t + 701))

    def test_apology_when_only_warned_and_switch_off(self):
        uid = "200000003"
        self.assertEqual(boundary.plead(uid, "堂主", "对不起", now=1.0), "")  # 没犯过，无须原谅
        boundary.check(uid, "堂主", "mua", now=10.0)
        self.assertEqual(boundary.plead(uid, "堂主", "对不起嘛", now=20.0), "accepted")
        self.assertIn("警告还算数", boundary.pop_forgive_note(uid, now=21.0))
        self.assertEqual(boundary.plead(uid, "堂主", "真的对不起", now=30.0), "")  # 同一次警告只接受一次
        _env.write_env(BASE + "AI_CHAT_FORGIVE=0\n")
        boundary.reset(uid)
        self._make_cold(uid, 1000.0)
        self.assertEqual(boundary.plead(uid, "堂主", "对不起", now=1300.0), "")
        self.assertTrue(boundary.ignoring(uid, now=1301.0))

    def test_warn_then_cold_shoulder(self):
        uid, t0 = "200000003", 1_000_000.0
        warn = boundary.check(uid, "堂主", "（舔了好几口）", now=t0)
        self.assertIn("最后一次警告", warn)    # 重的一次算两次
        self.assertFalse(boundary.ignoring(uid, now=t0 + 1))
        boundary.check(uid, "堂主", "（舔了好几口）", now=t0 + 30)  # 同一条重新生成，不算第二次
        self.assertFalse(boundary.ignoring(uid, now=t0 + 31))
        cold = boundary.check(uid, "堂主", "*扑倒*", now=t0 + 3600)
        self.assertIn("说到做到", cold)
        self.assertTrue(boundary.ignoring(uid, now=t0 + 3601))
        self.assertFalse(boundary.ignoring(uid, now=t0 + 3600 + 6 * 3600 + 1))
        self.assertEqual(boundary.check(uid, "堂主", "（抱抱）", now=t0 + 9 * 3600), "")

    def test_owner_and_special_exempt(self):
        for uid in ("200000001", "11112222"):
            self.assertEqual(boundary.check(uid, "x", "（亲了好几口）", now=1.0), "")
            self.assertEqual(boundary.check(uid, "x", "（亲了好几口）", now=500.0), "")
            self.assertFalse(boundary.ignoring(uid, now=501.0))


if __name__ == "__main__":
    unittest.main()
