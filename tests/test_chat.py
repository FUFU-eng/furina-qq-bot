from tests import _env  # noqa: F401  (must be first)

import unittest
from unittest import mock

from furina.core import db
from furina.plugins.chat import cleanup, emotion, favor, learning, memory_intent, role_persona, stickers
from furina.plugins.chat.persona import DAILY_RULES, PERSONA


class LearningTest(unittest.TestCase):
    def test_add_query_delete(self):
        ok, fact = learning.add_setting(1, "阿白", "喜欢喝奶茶")
        self.assertTrue(ok)
        self.assertEqual(fact, "阿白的设定：喜欢喝奶茶")
        self.assertFalse(learning.add_setting(1, "阿白", "喜欢喝奶茶")[0])
        facts, _ = learning.query_settings(1, "阿白")
        self.assertEqual(facts, [fact])
        self.assertIn("阿白的设定", learning.get_group_summary(1))
        # 持久化到数据库
        self.assertIn("1", db.items("learned"))
        n, _ = learning.delete_settings(1, "阿白")
        self.assertEqual(n, 1)

    def test_alias_and_normalize(self):
        got = learning.observe_message(2, "10", "小明", "以后叫我阿明", True)
        self.assertTrue(any("阿明" in x for x in got))
        self.assertEqual(learning.resolve_target(2, "阿明"), "小明")
        self.assertEqual(learning.normalize_target("芙芙的设定"), "芙宁娜")

    def test_reload_after_external_write(self):
        learning.add_setting(3, "甲", "乙")
        data = db.get("learned", "3")
        data["facts"].append({"fact": "丙的设定：丁", "ts": ""})
        db.put("learned", "3", data)  # 模拟面板改动
        self.assertEqual(learning.query_settings(3, "丙")[0], ["丙的设定：丁"])


class FavorEmotionTest(unittest.TestCase):
    def test_favor_gain_and_cooldown(self):
        base = favor.get_favor("777")
        d1 = favor.observe_message(1, "777", "路人", "你真可爱", True, message_id=1)
        self.assertGreater(d1, 0)
        self.assertEqual(favor.observe_message(1, "777", "路人", "再说一句别的", True, message_id=2), 0)
        self.assertGreaterEqual(favor.get_favor("777"), base)
        self.assertIn("777", db.items("favor"))
        self.assertIn("真实感觉", favor.favor_note("777"))

    def test_favor_partitioned_per_bot(self):
        from furina.core import env
        primary = env.get("BOT_QQ", "") or "10001"
        with mock.patch.object(env, "get", side_effect=lambda k, d="": primary if k == "BOT_QQ" else d):
            favor.observe_message(1, "4242", "甲", "你真可爱", True, message_id=11, bot_qq=primary)
            other = favor.get_favor("4242", bot_qq="99990001")
            self.assertEqual(other, favor.DEFAULT_FAVOR)
            self.assertIn("4242", db.items("favor"))
            self.assertNotIn("4242", db.items("favor@99990001"))
            favor.on_poke("4242", bot_qq="99990001")
            self.assertIn("4242", db.items("favor@99990001"))

    def test_emotion_praise(self):
        with mock.patch.object(emotion, "_judge_on", return_value=False):  # 老的关键词模式
            before = emotion.get_mood()
            emotion.observe_message(1, "888", "A", "谢谢你", True)
            self.assertEqual(emotion.get_mood(), min(100, before + 2))
            self.assertIsInstance(db.get("emotion", "state"), dict)
            self.assertIn("你今天的心情", emotion.get_emotion_note())


class PersonaTest(unittest.TestCase):
    def test_default_persona_and_lore_seed(self):
        p = role_persona.build_persona("123")
        self.assertTrue(p.startswith(PERSONA))
        self.assertTrue(p.endswith(DAILY_RULES))
        lore = role_persona.build_shared_lore("123", "你是谁呀")
        self.assertIsInstance(lore, str)
        self.assertIsNotNone(db.get("persona", "shared_lore"))


class CleanupTest(unittest.TestCase):
    def test_sticker_marker(self):
        text, idx = stickers.extract_sticker_marker("好耶<<STICKER:3>>")
        self.assertEqual((text, idx), ("好耶", 2))

    def test_trim_and_parentheses(self):
        self.assertLessEqual(len(cleanup._trim_reply("啊" * 200, 80)), 81)
        self.assertNotIn("得意地叉起腰", cleanup._strip_action_parentheses("哼（得意地叉起腰来）本水神才不怕"))

    def test_intent_json(self):
        self.assertEqual(memory_intent._extract_json('```json\n{"action":"add"}\n```'), {"action": "add"})
        self.assertIsNone(memory_intent._extract_json("没有 json"))




class GroupChatContinuationTest(unittest.TestCase):
    def test_detect_two_others_talking_strict_alternating(self):
        import time
        from furina.plugins.chat import context

        now = time.time()
        hist_alt = [
            {"role": "user", "content": "张三：你今天打深渊了吗", "ts": now - 30},
            {"role": "user", "content": "李四：打了，满星过了", "ts": now - 20},
            {"role": "user", "content": "张三：用的哪两队呀", "ts": now - 10},
            {"role": "user", "content": "李四：芙宁娜那维莱特一队", "ts": now - 2},
        ]
        talking, pair = context.detect_two_others_talking(hist_alt)
        self.assertTrue(talking)
        self.assertEqual(pair, ["张三", "李四"])

        hist_three = hist_alt[:3] + [{"role": "user", "content": "王五：我也过了", "ts": now - 1}]
        talking3, _ = context.detect_two_others_talking(hist_three)
        self.assertFalse(talking3)

    def test_active_bot_thread_and_dialogue_thread(self):
        import time
        from furina.plugins.chat import context

        now = time.time()
        hist = [
            {"role": "user", "content": "小明：芙芙今天吃什么好呢", "ts": now - 25},
            {"role": "assistant", "content": "去吃小蛋糕或者火锅呗", "ts": now - 20},
            {"role": "user", "content": "小明：火锅太辣了换一个", "ts": now - 3},
        ]
        self.assertTrue(context.is_speaker_in_active_bot_thread(hist, "小明"))
        thread_txt = context.dialogue_thread(hist, "小明", exclude="小明：火锅太辣了换一个")
        self.assertIn("小明：芙芙今天吃什么好呢", thread_txt)
        self.assertIn("去吃小蛋糕或者火锅呗", thread_txt)

    def test_build_target_anchor_includes_pronoun_and_quote_clues(self):
        from furina.plugins.chat import context
        from furina.plugins.chat.quotes import QuoteInfo

        q_bot = QuoteInfo(images=0, text="本导演当然会签到啦", sender_name="芙宁娜", sender_id="12345")
        anchor = context.build_target_anchor(
            "沃超爱芙啦",
            "那怎么绑定账号呀",
            quote=q_bot,
            bot_qq="12345",
            recent_replies=["哼，本导演才不困呢"],
        )
        self.assertIn("唯一要回复的是「沃超爱芙啦」", anchor)
        self.assertIn("引用你之前说的", anchor)
        self.assertIn("防复读约束", anchor)


if __name__ == "__main__":
    unittest.main()
