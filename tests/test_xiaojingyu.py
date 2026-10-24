"""小鲸鱼式：一句一条 / 话多一点 / 惦记常聊的人 / 活着的痕迹（睡了多久、没发出去的话、时间感）。"""
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
from furina.plugins.chat import attention, cleanup, config, handlers, humanize, memory, planner, presence, proactive
from furina.plugins.chat import llm as chat_llm

BOT = "30000003"
GID = 100000004
_ENV = (f"BOT_QQ={BOT}\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"
        "AI_CHAT_REPLY_SPLIT_PROBABILITY=0\nAI_CHAT_POKE_PROBABILITY=0\nAI_CHAT_STICKER_PROBABILITY=0\n"
        "AI_CHAT_MERGE_SECONDS=0\nAI_CHAT_HUMANIZE=0\nAI_CHAT_PLANNER=0\nAI_CHAT_PROACTIVE_HOURS=0-24\n"
        "AI_CHAT_STYLE_VARIETY=0\nAI_CHAT_VOICE=0\nAI_CHAT_WEB_SEARCH=0\n")


def make_event(text: str, user_id: int = 20005, mid: int = 11, name: str = "小鲸") -> GroupMessageEvent:
    msg = Message(text)
    return GroupMessageEvent(
        time=int(time.time()), self_id=int(BOT), post_type="message", message_type="group",
        sub_type="normal", user_id=user_id, message_id=mid, message=msg, original_message=msg,
        raw_message=str(msg), font=0, sender=Sender(user_id=user_id, nickname=name, card=""),
        to_me=False, group_id=GID,
    )


class FakeBot:
    self_id = BOT

    def __init__(self):
        self.sent = []

    async def send_group_msg(self, **kw):
        self.sent.append(kw)
        return {"message_id": 1}

    async def call_api(self, api, **kw):
        return {"card": "", "nickname": "小鲸"} if api == "get_group_member_info" else []


def _reset_presence():
    presence._people.clear()
    presence._dirty.clear()
    presence._returning.clear()
    presence._next_check.clear()
    presence._freq_cache.update(ts=0.0, map={})


class BurstPartsTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(_ENV)

    def test_one_line_one_message(self):
        self.assertEqual(cleanup.burst_parts("哈哈哈哈\n你认真的吗\n我昨天也是"), ["哈哈哈哈", "你认真的吗", "我昨天也是"])
        self.assertEqual(cleanup.burst_parts("啊？"), ["啊？"])
        self.assertEqual(cleanup.burst_parts("绝区零现在是三点三版本了，前两天刚更新的你没玩吗"),
                         ["绝区零现在是三点三版本了，前两天刚更新的你没玩吗"])  # 不算长：不拆

    def test_long_line_splits_at_punctuation_without_cutting(self):
        text = ("说起来我今天去看了一场歌剧，主演唱到一半突然忘词了，全场都安静了好几秒，"
                "然后他居然即兴编了一段，观众还以为是故意的！笑死我了，太有才了吧")
        parts = cleanup.burst_parts(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 40 for p in parts), parts)
        self.assertEqual("".join(parts).replace("，", ""), text.replace("，", ""))  # 一个字都不丢
        self.assertEqual(cleanup.burst_parts("这一整句话没有任何标点所以就算很长也不应该被硬生生地从中间切开来发出去"),
                         ["这一整句话没有任何标点所以就算很长也不应该被硬生生地从中间切开来发出去"])

    def test_burst_max_and_no_total_cap(self):
        self.assertEqual(cleanup.burst_parts("一\n二\n三\n四", burst_max=2), ["一", "二，三，四"])
        self.assertEqual(len(cleanup.burst_parts("\n".join(f"第{i}条" for i in range(9)))), 9)  # 默认不限条数
        self.assertEqual(config.AI_CHAT_MAX_REPLY_CHARS, 0)
        self.assertEqual(cleanup._trim_reply("啊" * 300, 0), "啊" * 300)

    def test_newlines_survive_cleanup(self):
        self.assertEqual(cleanup._strip_context_markers("哈哈哈  \n  你认真的吗"), "哈哈哈\n你认真的吗")
        self.assertEqual(cleanup.drop_periods("好吧。\n那我走了。"), "好吧\n那我走了")


class SendBurstTest(unittest.TestCase):
    def setUp(self):
        memory.reset(GID)
        humanize.reset()
        _reset_presence()

    def test_reply_lines_become_separate_messages_with_at_on_first(self):
        _env.write_env(_ENV)
        sent = []

        async def fake_send(message, **kw):
            sent.append(message)
            return {"message_id": 5}

        async def fake_llm(messages, timeout=None, max_tokens=None, **kw):
            if max_tokens == 160:
                return '{"action":"none"}'
            return "哈？\n你认真的吗\n我昨天也是这样"

        bot = FakeBot()
        with mock.patch("nonebot.get_bots", return_value={BOT: bot}), \
                mock.patch("nonebot.get_bot", return_value=bot), \
                mock.patch.object(humanize, "burst_gap", return_value=0.0), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm), \
                mock.patch.object(chat_mod._chat, "send", side_effect=fake_send):
            asyncio.run(handlers.handle_chat(make_event(f"[CQ:at,qq={BOT}] 我今天把钥匙锁家里了", mid=71), mock.MagicMock()))
        self.assertEqual(len(sent), 3)
        self.assertTrue(any(s.type == "at" for s in sent[0]))
        self.assertFalse(any(s.type == "at" for s in sent[1]))
        self.assertEqual(sent[2].extract_plain_text(), "我昨天也是这样")

    def test_burst_gap_always_pauses(self):
        _env.write_env(_ENV)
        self.assertEqual(humanize.gap_before("一句话"), 0.0)  # 老的停顿仍只在打开模拟打字时
        gaps = [humanize.burst_gap("一句十个字左右的话") for _ in range(30)]
        self.assertTrue(all(g > 0.3 for g in gaps))
        self.assertTrue(all(g < 12 for g in gaps))


class ChattyTest(unittest.TestCase):
    def test_planner_is_more_willing_when_chatty(self):
        seen = []

        async def fake_llm(messages, **kw):
            seen.append(messages[-1]["content"])
            return '{"reply": true}'

        with mock.patch.object(chat_llm, "chat", side_effect=fake_llm):
            _env.write_env(_ENV.replace("AI_CHAT_PLANNER=0", "AI_CHAT_PLANNER=1") + "AI_CHAT_CHATTY=1\n")
            asyncio.run(planner.plan("", "甲：有人玩过绝区零吗", False))
            _env.write_env(_ENV.replace("AI_CHAT_PLANNER=0", "AI_CHAT_PLANNER=1") + "AI_CHAT_CHATTY=0\n")
            asyncio.run(planner.plan("", "甲：有人玩过绝区零吗", False))
        self.assertIn("能插上嘴", seen[0])
        self.assertNotIn("能插上嘴", seen[1])

    def test_chatty_no_floor_question_lift_and_zero_means_off(self):
        calls = []

        async def fake_reply(*a, **kw):
            calls.append(kw.get("forced"))

        def run(env, text, rnd):
            _env.write_env(env)
            memory.reset(GID)
            attention.reset()  # 别让前面测试里「刚聊完多留一会儿」影响概率
            with mock.patch.object(handlers, "_reply", side_effect=fake_reply), \
                    mock.patch.object(handlers.random, "random", return_value=rnd), \
                    mock.patch.object(handlers.reactions, "maybe_react", mock.AsyncMock(return_value=False)):
                asyncio.run(handlers.handle_chat(make_event(text, mid=int(rnd * 1000) + len(calls) + 100), mock.MagicMock()))

        base = _ENV + "AI_CHAT_PROBABILITY=0.02\n"
        # 设置里写 2% 就是 2%：「话多一点」不再偷偷把平时的概率抬到 6%
        run(base + "AI_CHAT_CHATTY=1\n", "今天好热", 0.05)            # 0.05 > 0.02：不接
        run(base + "AI_CHAT_CHATTY=1\n", "今天好热", 0.01)            # 0.01 < 0.02：接
        run(base + "AI_CHAT_CHATTY=1\n", "有没有人知道这个怎么弄", 0.12)  # 有人提问：提到 15%
        run(base + "AI_CHAT_CHATTY=0\n", "有没有人知道这个怎么弄", 0.12)  # 关掉话多一点：提问也只有 2%
        run(_ENV + "AI_CHAT_PROBABILITY=0\nAI_CHAT_CHATTY=1\n", "有没有人知道这个怎么弄", 0.0)  # 概率 0 = 关掉随机接话
        self.assertEqual(calls, [False, False])


class PresenceTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(_ENV)
        _reset_presence()
        humanize.reset()
        for k in ("hb", "wake", f"unsent:{GID}", f"people:{GID}", f"miss_day:{GID}"):
            db.delete(presence.NS, k)

    def test_wake_after_downtime_is_told_twice(self):
        now = time.time()
        db.put(presence.NS, "hb", now - 5 * 3600)
        self.assertIsNotNone(presence.check_wake(now))
        note = presence.wake_note(GID, now + 60)
        self.assertIn("睡了", note)
        self.assertIn("5 个小时", note)
        self.assertTrue(presence.wake_note(GID, now + 120))
        self.assertEqual(presence.wake_note(GID, now + 180), "")
        self.assertEqual(presence.wake_note(GID + 1, now + 4 * 3600), "")  # 3 小时后不再提
        db.put(presence.NS, "hb", now)
        self.assertIsNone(presence.check_wake(now + 60))  # 短暂重启不算睡觉

    def test_unsent_lines_are_told_then_forgotten(self):
        async def boom(m):
            raise RuntimeError("timeout")

        send = humanize.guarded(boom, GID)
        asyncio.run(send(Message("刚才那个梗我笑了好久")))
        note = presence.unsent_note(GID)
        self.assertIn("刚才那个梗我笑了好久", note)
        self.assertIn("不是你的错", note)
        self.assertTrue(presence.unsent_note(GID))
        self.assertEqual(presence.unsent_note(GID), "")
        presence.record_unsent(GID, "旧的一句", now=time.time() - 30 * 3600)
        self.assertEqual(presence.unsent_note(GID), "")  # 超过 24 小时就忘了
        humanize.reset()

    def test_time_sense_when_someone_returns(self):
        now = time.time()
        presence.seen(GID, 777, "阿七", "我去出差了", now=now - 2 * 86400 - 600)
        self.assertEqual(presence.absence_note(GID, 777, "阿七", now=now), "")
        presence.seen(GID, 777, "阿七", "我回来啦", now=now)
        note = presence.absence_note(GID, 777, "阿七", now=now + 5)
        self.assertIn("2 天", note)
        self.assertEqual(presence.absence_note(GID, 777, "阿七", now=now + 6), "")  # 只提一次

    def _make_frequent(self, uid: str, now: float, days: int = 4, per_day: int = 4):
        for i in range(days):
            day = time.strftime("%Y-%m-%d", time.localtime(now - (i + 2) * 86400))
            db.usage_add(day, "chat", str(GID), uid, per_day)

    def test_miss_tick_pings_frequent_person_then_backs_off(self):
        now = time.time()
        uid = "880088"
        self._make_frequent(uid, now)
        presence.seen(GID, uid, "阿八", "我下周要考科目二", now=now - 30 * 3600)
        self.assertIn(uid, [c[0] for c in presence.candidates(GID, now)])
        permissions.set_feature_enabled(str(GID), presence.FEATURE, True)
        bot = FakeBot()
        prompts = []

        async def fake_llm(messages, **kw):
            prompts.append(messages[-1]["content"])
            return "科目二过了没\n别装死"

        presence._next_check[str(GID)] = 0
        always = mock.Mock(random=mock.Mock(return_value=0.0), uniform=mock.Mock(return_value=60),
                           choices=lambda seq, weights=None: [seq[0]])
        with mock.patch.object(proactive, "_pick_bot", return_value=(bot, BOT)), \
                mock.patch.object(presence.live_groups, "cached", return_value=set()), \
                mock.patch.object(humanize, "burst_gap", return_value=0.0), \
                mock.patch.object(chat_llm, "chat", side_effect=fake_llm):
            asyncio.run(presence.miss_tick(now, always))
        self.assertEqual(len(bot.sent), 2)
        first = bot.sent[0]["message"]
        self.assertEqual(first[0].type, "at")
        self.assertEqual(str(first[0].data["qq"]), uid)
        self.assertIn("科目二", prompts[0])
        self.assertIn("阿八", prompts[0])
        self.assertEqual(presence._load(str(GID))[uid]["miss_unanswered"], 1)
        self.assertEqual(presence.candidates(GID, now + 3600), [])  # 同一个人隔几天才再找
        # TA 回来了：她知道自己惦记过 TA，大概率接话；未回复计数清零
        presence.seen(GID, uid, "阿八", "我回来了", now=now + 7200)
        self.assertEqual(presence.welcome_back_probability(GID, uid, now + 7201), 0.8)
        self.assertIn("艾特过", presence.absence_note(GID, uid, "阿八", now + 7202))
        self.assertEqual(presence._load(str(GID))[uid]["miss_unanswered"], 0)
        permissions.set_feature_enabled(str(GID), presence.FEATURE, False)

    def test_stops_after_three_unanswered(self):
        now = time.time()
        uid = "990099"
        self._make_frequent(uid, now)
        presence.seen(GID, uid, "阿九", "嗯", now=now - 40 * 3600)
        rec = presence._load(str(GID))[uid]
        rec.update(miss_unanswered=3, miss_ts=now - 10 * 86400)
        self.assertNotIn(uid, [c[0] for c in presence.candidates(GID, now)])
        rec.update(miss_unanswered=2)
        self.assertIn(uid, [c[0] for c in presence.candidates(GID, now)])


class FeatureSyncTest(unittest.TestCase):
    def test_miss_feature_and_settings_synced(self):
        from panel.services import data as pdata
        from panel.services import huoren_settings as hs

        self.assertEqual(permissions.FEATURES["miss"], "惦记常聊的人")
        self.assertIn("miss", permissions.DEFAULT_ON_FEATURES)
        self.assertIn("miss", [k for k, _, _ in pdata.KINDS])
        root = Path(__file__).resolve().parent.parent
        app = (root / "panel/web/app.js").read_text(encoding="utf-8")
        for word in ('["惦记常聊的人", ', '["一句一条", ', '["活着的痕迹", '):
            self.assertIn(word, app)
        keys = {f[0] for _t, _d, fields in hs.GROUPS.values() for f in fields}
        for key in ("AI_CHAT_MSG_SOFT_MAX", "AI_CHAT_BURST_MAX", "AI_CHAT_MAX_REPLY_CHARS", "AI_CHAT_CHATTY",
                    "AI_CHAT_LINGER_PROBABILITY", "AI_CHAT_MISS", "AI_CHAT_MISS_AFTER_HOURS", "AI_CHAT_MISS_MAX_UNANSWERED",
                    "AI_CHAT_PRESENCE", "AI_CHAT_PRESENCE_SLEEP_MINUTES"):
            self.assertIn(key, keys)
            self.assertIn(key, {v[0] for v in config._SPEC.values()})  # 机器人真的读这个键
        settings = (root / "panel/classic/settings_env.py").read_text(encoding="utf-8")
        self.assertNotIn('"AI_CHAT_MAX_REPLY_CHARS"', settings)  # 只在活人感页出现一次，不重复
        self.assertIn("AI_CHAT_PROACTIVE_GAP_MINUTES", settings)


if __name__ == "__main__":
    unittest.main()
