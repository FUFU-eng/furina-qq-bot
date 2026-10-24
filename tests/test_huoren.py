"""活人感 P2：说话方式 / 自称兜底 / 表情回应 / 只发表情包 / 群友近况 / 冷场开话题 / 群主艾特。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import random
import time
import unittest
from pathlib import Path
from unittest import mock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from furina.core import db, permissions
from furina.plugins.chat import chat as chat_mod
from furina.plugins.chat import commands, handlers, life_events, memory, proactive, prompting, reactions, voice
from furina.plugins.chat import llm as chat_llm

BOT = "30000002"
GID = 100000004
_TITLES = ["本导演", "本大明星", "芙宁娜大人"]
_ENV = (f"BOT_QQ={BOT}\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"
        "AI_CHAT_REPLY_SPLIT_PROBABILITY=0\nAI_CHAT_POKE_PROBABILITY=0\nAI_CHAT_STICKER_PROBABILITY=0\n"
        "AI_CHAT_MERGE_SECONDS=0\nAI_CHAT_HUMANIZE=0\nAI_CHAT_PLANNER=0\n")


def make_event(text: str, user_id: int = 20002, mid: int = 11) -> GroupMessageEvent:
    msg = Message(text)
    return GroupMessageEvent(
        time=int(time.time()), self_id=int(BOT), post_type="message", message_type="group",
        sub_type="normal", user_id=user_id, message_id=mid, message=msg, original_message=msg,
        raw_message=str(msg), font=0, sender=Sender(user_id=user_id, nickname="小测", card=""),
        to_me=False, group_id=GID,
    )


class FakeBot:
    self_id = BOT

    def __init__(self):
        self.sent, self.apis = [], []

    async def send_group_msg(self, **kw):
        self.sent.append(kw)
        return {"message_id": 1}

    async def call_api(self, api, **kw):
        self.apis.append((api, kw))
        if api == "get_group_member_info":
            return {"card": "", "nickname": "小测"}
        return []

    async def get_image(self, **kw):
        return {}


class VoiceTest(unittest.TestCase):
    def test_guard_replaces_overused_titles(self):
        with mock.patch.object(voice, "title_words", return_value=_TITLES):
            recent = ["本导演才不困", "行吧", "本大明星说了算"]
            self.assertEqual(voice.guard_self_titles("本导演今天很忙的", recent), "我今天很忙的")
            # 最近没怎么用：保留一个，第二个换成我
            self.assertEqual(voice.guard_self_titles("本导演觉得本大明星最棒", ["好", "嗯"]), "本导演觉得我最棒")
            self.assertEqual(voice.guard_self_titles("我觉得挺好", recent), "我觉得挺好")

    def test_habit_note_names_repeats(self):
        with mock.patch.object(voice, "title_words", return_value=_TITLES):
            note = voice.habit_note(["行啊，那你去呀", "行啊，本导演鼓掌呀", "哟，又来了呀", "（发了张表情包）"])
        self.assertIn("行啊", note)
        self.assertIn("呀", note)
        self.assertIn("自称", note)
        self.assertEqual(voice.habit_note(["今天天气不错", "确实"]), "")

    def test_turn_note_and_sticker_candidate(self):
        rng = random.Random(3)
        for _ in range(20):
            self.assertTrue(voice.turn_note("哈哈哈", True, rng).startswith("【这次的说话方式】"))
        self.assertTrue(voice.sticker_only_candidate("哈哈哈哈"))
        self.assertTrue(voice.sticker_only_candidate("6"))
        self.assertTrue(voice.sticker_only_candidate("", has_image=True))
        self.assertFalse(voice.sticker_only_candidate("在吗"))
        self.assertFalse(voice.sticker_only_candidate("你觉得这个角色强不强啊"))


class MatchMemberTest(unittest.TestCase):
    MEMBERS = [
        {"user_id": 300000003, "card": "我超爱芙啦", "nickname": "x", "role": "member"},
        {"user_id": 200000001, "card": "沃超爱芙啦", "nickname": "y", "role": "owner"},
        {"user_id": 200000005, "card": "昔涟", "nickname": "z", "role": "member"},
        {"user_id": 555, "card": "小明", "nickname": "a", "role": "member"},
        {"user_id": 556, "card": "小明", "nickname": "b", "role": "admin"},
    ]

    def test_owner_by_word_or_label(self):
        self.assertEqual(commands._match_member(self.MEMBERS, "群主", owner_qq="200000001"), "200000001")
        self.assertEqual(commands._match_member(self.MEMBERS, "铸币群主", owner_qq="200000001"), "200000001")
        self.assertEqual(commands._match_member(self.MEMBERS, "我超爱芙啦（群主）", owner_qq="200000001"), "200000001")
        self.assertEqual(commands._match_member(self.MEMBERS, "我超爱芙啦", owner_qq="200000001"), "200000001")
        # 没配群主 QQ 时看群角色
        self.assertEqual(commands._match_member(self.MEMBERS, "群主", owner_qq=""), "200000001")

    def test_same_name_prefers_admin_and_normal_match(self):
        self.assertEqual(commands._match_member(self.MEMBERS, "小明", owner_qq=""), "556")
        self.assertEqual(commands._match_member(self.MEMBERS, "昔涟", owner_qq="200000001"), "200000005")
        self.assertEqual(commands._match_member(self.MEMBERS, "不存在的人", owner_qq=""), "")


class LifeEventsTest(unittest.TestCase):
    NOW = time.mktime((2026, 10, 22, 10, 0, 0, 0, 0, -1))  # 周四 10:00

    def setUp(self):
        _env.write_env(_ENV)

    def test_detect(self):
        ev = life_events.detect("明天考四级好紧张", self.NOW)
        self.assertEqual(ev["event"], "考试")
        self.assertGreater(ev["due"], self.NOW + 86400)
        self.assertEqual(life_events.detect("周五要去面试", self.NOW)["event"], "面试")
        self.assertEqual(life_events.detect("我感冒了好难受", self.NOW)["event"], "身体不舒服")
        self.assertLess(life_events.detect("我今天下午有面试", self.NOW)["due"], self.NOW + 4 * 3600)
        for text in ("明天考试吗", "如果明天考试怎么办", "明天不用考试了", "他明天生日", "今天天气不错", "你明天面试？"):
            self.assertIsNone(life_events.detect(text, self.NOW), text)

    def test_record_pending_once(self):
        gid, uid = 7701, 9901
        db.delete("life_events", f"{gid}:{uid}")
        self.assertIsNotNone(life_events.record(gid, uid, "小测", "明天考四级", now=self.NOW))
        self.assertIsNone(life_events.pending(gid, uid, now=self.NOW + 3600))
        later = self.NOW + 2 * 86400
        ev = life_events.pending(gid, uid, now=later)
        self.assertEqual(ev["event"], "考试")
        self.assertIn("明天考四级", life_events.followup_note("小测", ev, later))
        life_events.mark_asked(gid, uid, ev, now=later)
        self.assertIsNone(life_events.pending(gid, uid, now=later + 86400))
        self.assertIsNone(life_events.pending(gid, uid, now=self.NOW + 30 * 86400))


class ReactionTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(_ENV + "AI_CHAT_REACTION_INSTEAD_PROBABILITY=1\n")
        reactions._last_react.clear()

    def test_pick_and_ack(self):
        rng = random.Random(1)
        self.assertIn(reactions.pick_emoji("哈哈哈哈笑死", rng=rng), (182, 178, 128514))
        self.assertEqual(reactions.pick_emoji("晚安啦", rng=rng), 75)
        self.assertIsNone(reactions.pick_emoji("今天去上班", rng=rng))
        self.assertTrue(reactions.is_ack("谢谢你"))
        self.assertTrue(reactions.is_ack("晚安~"))
        self.assertFalse(reactions.is_ack("谢谢你帮我找图，再找一张"))

    def test_at_ack_reacts_instead_of_reply(self):
        bot = FakeBot()
        sent = []

        async def fake_send(message, **kw):
            sent.append(str(message))

        async def fake_llm(messages, timeout=None, max_tokens=None, **kw):
            if max_tokens == 160:
                return '{"action":"none"}'
            return "不客气"

        matcher = mock.MagicMock()
        with mock.patch("nonebot.get_bots", return_value={BOT: bot}), \
                mock.patch("nonebot.get_bot", return_value=bot), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm), \
                mock.patch.object(chat_mod._chat, "send", side_effect=fake_send):
            asyncio.run(handlers.handle_chat(make_event(f"[CQ:at,qq={BOT}] 谢谢", mid=31), matcher))
        calls = [kw for api, kw in bot.apis if api == "set_msg_emoji_like"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["message_id"], 31)
        self.assertEqual(sent, [])
        self.assertTrue(memory.recent_assistant_messages(GID, 1)[-1].startswith("（"))


class StickerOnlyTest(unittest.TestCase):
    def test_short_message_gets_lone_sticker(self):
        _env.write_env(_ENV + "AI_CHAT_STICKER_ONLY_PROBABILITY=1\nAI_CHAT_REACTION_INSTEAD_PROBABILITY=0\n")
        bot = FakeBot()
        sent = []

        async def fake_send(message, **kw):
            sent.append(message)

        prompts = []

        async def fake_llm(messages, timeout=None, max_tokens=None, **kw):
            prompts.append(messages)
            if max_tokens == 160:
                return '{"action":"none"}'
            return "<<STICKER:2>>"

        urls = ["http://s/1.gif", "http://s/2.gif", "http://s/3.gif"]
        matcher = mock.MagicMock()
        with mock.patch("nonebot.get_bots", return_value={BOT: bot}), \
                mock.patch("nonebot.get_bot", return_value=bot), \
                mock.patch.object(prompting, "fetch_stickers", mock.AsyncMock(return_value=urls)), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm), \
                mock.patch.object(chat_mod._chat, "send", side_effect=fake_send):
            asyncio.run(handlers.handle_chat(make_event(f"[CQ:at,qq={BOT}] 哈哈哈哈", mid=41), matcher))
        self.assertEqual(len(sent), 1)
        segs = list(sent[0])
        self.assertTrue(any(s.type == "image" and s.data.get("file") == "http://s/2.gif" for s in segs))
        self.assertFalse(any(s.type in ("text", "at") for s in segs))
        self.assertIn("只发表情包", prompts[-1][0]["content"])


class ProactiveTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(_ENV + "AI_CHAT_PROACTIVE_HOURS=0-24\nAI_CHAT_PROACTIVE_MIN_MESSAGES=3\n")

    def _hist(self, now, idle_min, last_role="user"):
        base = now - idle_min * 60
        h = [{"role": "user", "content": f"甲：第{i}句", "ts": base - 600 + i * 60} for i in range(5)]
        h.append({"role": last_role, "content": "乙：最后一句" if last_role == "user" else "我说的", "ts": base})
        return h

    def test_should_speak_rules(self):
        now = time.time()
        gid = "8801"
        db.delete("proactive", gid)
        always = mock.Mock(random=mock.Mock(return_value=0.0))
        self.assertTrue(proactive.should_speak(gid, self._hist(now, 120), now, always)[0])
        self.assertFalse(proactive.should_speak(gid, self._hist(now, 30), now, always)[0])
        self.assertFalse(proactive.should_speak(gid, self._hist(now, 120, "assistant"), now, always)[0])
        self.assertFalse(proactive.should_speak(gid, self._hist(now, 60 * 10), now, always)[0])
        db.put("proactive", gid, {"day": time.strftime("%Y-%m-%d"), "count": proactive.config.PROACTIVE_DAILY_MAX,
                                  "last_ts": now - 4 * 3600})
        self.assertFalse(proactive.should_speak(gid, self._hist(now, 120), now, always)[0])
        db.put("proactive", gid, {"day": time.strftime("%Y-%m-%d"), "count": 1, "last_ts": now - 30 * 60})  # 离上次太近
        self.assertFalse(proactive.should_speak(gid, self._hist(now, 120), now, always)[0])
        db.delete("proactive", gid)

    def test_clean_strips_markers(self):
        self.assertEqual(proactive._clean("[AT:小明:你好]（伸了个懒腰）有人在打游戏吗~"), "有人在打游戏吗")

    def test_tick_sends_and_records(self):
        gid = 8802
        db.delete("proactive", str(gid))
        permissions.set_feature_enabled(str(gid), "proactive", True)
        now = time.time()
        memory._histories.pop(gid, None)
        memory.seed(gid, self._hist(now, 120))
        bot = FakeBot()

        async def fake_llm(messages, **kw):
            return "刚吃完一大碗螺蛳粉，好满足"

        with mock.patch("nonebot.get_bots", return_value={BOT: bot}), \
                mock.patch.object(proactive, "FIRE_CHANCE", 1.0), \
                mock.patch.object(proactive.dice, "peak_factor", return_value=1.0), \
                mock.patch.object(proactive.live_groups, "cached", return_value=set()), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm):
            asyncio.run(proactive.tick(now))
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0]["group_id"], gid)
        self.assertEqual(memory.get_history(gid)[-1]["role"], "assistant")
        self.assertEqual(db.get("proactive", str(gid))["count"], 1)
        permissions.set_feature_enabled(str(gid), "proactive", False)


class FeatureSyncTest(unittest.TestCase):
    def test_proactive_feature_synced(self):
        from panel.services import data as pdata

        self.assertIn("proactive", permissions.FEATURES)
        self.assertNotIn("proactive", permissions.DEFAULT_ON_FEATURES)
        self.assertIn("proactive", [k for k, _, _ in pdata.KINDS])
        root = Path(__file__).resolve().parent.parent
        self.assertIn("冷场开话题", (root / "panel/web/app.js").read_text(encoding="utf-8"))
        settings = (root / "panel/classic/settings_env.py").read_text(encoding="utf-8")
        for key in ("AI_CHAT_PROACTIVE_IDLE_MINUTES", "AI_CHAT_STICKER_ONLY_PROBABILITY", "AI_CHAT_REACTION",
                    "AI_CHAT_LIFE_EVENTS", "AI_CHAT_STYLE_VARIETY"):
            self.assertIn(key, settings)


if __name__ == "__main__":
    unittest.main()
