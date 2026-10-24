"""参考 Yuuyuko-uu DSH 插件做的几项：鱼的记忆（召回/取代）、鱼的心情（判定/四道闸/回稳）、主动搭话的骰子、刷B站的看与说。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from unittest import mock

from furina.core import db
from furina.plugins.chat import config, dice, emotion, learning, llm as chat_llm, people, recall


class RecallTest(unittest.TestCase):
    def test_recall_picks_related_and_skips_chitchat(self):
        items = ["养了只橘猫叫煤球", "下周三要去面试产品经理", "不吃辣，一吃就胃疼", "喜欢周杰伦的歌"]
        got = [r["item"] for r in recall.recall(items, "我家猫今天又把杯子打翻了")]
        self.assertIn("养了只橘猫叫煤球", got)
        self.assertNotIn("下周三要去面试产品经理", got)
        self.assertEqual(recall.recall(items, "在吗"), [])
        self.assertEqual(recall.recall(items, "哈哈哈哈"), [])

    def test_activation_decays_and_reinforce_bounces_back(self):
        now = 10_000_000.0
        it = {"text": "在学吉他", "at": now - 60 * 86400, "importance": 0}
        a_old = recall.activation(it, now, 30)
        self.assertLess(a_old, 0.3)
        a_new = recall.reinforce(it, now, 30)
        self.assertAlmostEqual(a_new, a_old + 0.2 * (1 - a_old), places=3)
        important = {"text": "妈妈住院了", "at": now - 60 * 86400, "importance": 1}
        self.assertGreater(recall.activation(important, now, 30), a_old)  # 重要的淡得慢

    def test_norm_key(self):
        self.assertEqual(recall.norm_key("工作.公司"), "工作.公司")
        self.assertEqual(recall.norm_key("工作"), "")
        self.assertEqual(recall.norm_key("事情.其他"), "")


class PeopleMergeTest(unittest.TestCase):
    def test_key_supersedes_old_fact_without_deleting(self):
        rec = people._blank("1", "x", 0)
        people.merge(rec, {"facts": [{"text": "在腾讯上班", "key": "工作.公司", "importance": 0.5}]}, 100)
        people.merge(rec, {"facts": [{"text": "跳槽去了字节", "key": "工作.公司", "importance": 0.5}]}, 200)
        self.assertEqual(people.texts(rec, "facts"), ["跳槽去了字节"])
        old = [x for x in rec["facts"] if x["text"] == "在腾讯上班"][0]
        self.assertTrue(old.get("superseded"))  # 不删，只标作废

    def test_replaces_and_reinforce(self):
        rec = people._blank("2", "y", 0)
        people.merge(rec, {"facts": ["养了只橘猫叫煤球"]}, 100)
        people.merge(rec, {"facts": ["养了只橘猫叫煤球"]}, 200)
        self.assertEqual(len(rec["facts"]), 1)
        people.merge(rec, {"facts": [{"text": "煤球走丢了，很难过", "replaces": "养了只橘猫叫煤球"}]}, 300)
        self.assertEqual(people.texts(rec, "facts"), ["煤球走丢了，很难过"])

    def test_old_string_items_migrate(self):
        rec = people._blank("3", "z", 0)
        rec["traits"] = ["爱熬夜"]
        self.assertEqual(people.items(rec, "traits")[0]["text"], "爱熬夜")
        self.assertTrue(rec["traits"][0]["stable"])


class LearningFocusTest(unittest.TestCase):
    def test_summary_only_related_facts_when_query_given(self):
        gid = 7301
        learning.add_setting(gid, "阿白", "喜欢喝奶茶，最爱芋泥波波")
        learning.add_setting(gid, "小林", "在准备考研，考数学一")
        full = learning.get_group_summary(gid)
        self.assertIn("阿白", full)
        self.assertIn("小林", full)
        focused = learning.get_group_summary(gid, query="阿白今天又去喝奶茶了")
        self.assertIn("阿白", focused)
        self.assertNotIn("考研", focused)


class MoodTest(unittest.TestCase):
    def setUp(self):
        db.delete("emotion", "state")
        emotion._data = None
        self.on = mock.patch.object(emotion, "_judge_on", return_value=True)
        self.on.start()

    def tearDown(self):
        self.on.stop()
        db.delete("emotion", "state")
        emotion._data = None

    def test_parse_delta(self):
        d = emotion.parse_delta('```json\n{"moodDelta": 9, "dims": {"happy": 6}, "reason": "阿花说考过了，我跟着乐了", "confidence": 88}\n```')
        self.assertEqual(d["mood"], 9)
        self.assertEqual(d["dims"]["happy"], 6)
        self.assertEqual(d["conf"], 88)
        self.assertIsNone(emotion.parse_delta("不是 JSON"))
        self.assertEqual(emotion.parse_delta('{"moodDelta": 50}')["mood"], 10)  # 夹到 ±10

    def test_step_is_a_third_and_told_once_in_that_group(self):
        now = time.time()
        start = emotion.get_mood()
        msg = emotion.apply_delta({"mood": 9, "dims": {k: (6 if k == "happy" else 0) for k in emotion.DIM_KEYS},
                                   "reason": "阿花说考过了，我当时跟着乐了一下", "conf": 85}, group_id=11, user_id="5", now=now)
        self.assertIn("心情更新", msg)
        self.assertEqual(emotion.get_mood(), start + 3)  # 只吃 1/3
        self.assertEqual(emotion.get_emotion_note(22, consume=True), "")  # 别的群不说
        line = emotion.get_emotion_note(11, consume=True)
        self.assertIn("阿花说考过了", line)
        self.assertIn("不是非这样不可", line)
        self.assertEqual(emotion.get_emotion_note(11, consume=True), "")  # ① 同一次变化只说一次
        self.assertEqual(emotion.get_emotion_for_user("5"), "")

    def test_gates_low_confidence_and_minor(self):
        now = time.time()
        zero = {k: 0 for k in emotion.DIM_KEYS}
        emotion.apply_delta({"mood": 6, "dims": zero, "reason": "随口聊了两句", "conf": 40}, group_id=11, now=now)
        self.assertEqual(emotion.get_emotion_note(11, consume=True), "")  # ② 判得没底
        emotion.apply_delta({"mood": 2, "dims": zero, "reason": "小事", "conf": 90}, group_id=11, now=now + 1)
        self.assertTrue(db.get("emotion", "state")["minor"])
        self.assertEqual(emotion.get_emotion_note(11, consume=True), "")  # ③ 小波动
        self.assertEqual(emotion.apply_delta({"mood": 1, "dims": zero, "reason": "", "conf": 90}, now=now + 2), "")  # 1/3 取整 = 0

    def test_daily_cap(self):
        now = time.time()
        with mock.patch.object(config, "MOOD_TELL_PER_DAY", 1, create=True):
            for i in range(2):
                emotion.apply_delta({"mood": 9, "dims": {k: 0 for k in emotion.DIM_KEYS}, "reason": f"第{i}件事，我乐了",
                                     "conf": 90}, group_id=11, now=now + i * 70)
                got = emotion.get_emotion_note(11, consume=True)
                if i == 0:
                    self.assertIn("第0件事", got)
                else:
                    self.assertEqual(got, "")  # ④ 今天说够了

    def test_decay_back_to_base_and_self_calm_note(self):
        now = time.time()
        emotion.apply_delta({"mood": 10, "dims": {k: (20 if k == "happy" else 0) for k in emotion.DIM_KEYS},
                             "reason": "被夸了，我当时美滋滋的", "conf": 90}, group_id=11, now=now)
        emotion.get_emotion_note(11, consume=True)
        with mock.patch.object(emotion.time, "time", return_value=now + 36 * 3600):
            mood = emotion.get_mood()
            st = db.get("emotion", "state")
        self.assertLess(abs(mood - config.MOOD_BASE), 3)
        self.assertEqual(st["kind"], "decay")
        self.assertLess(st["dims"]["happy"], 1)

    def test_judge_turn_end_to_end(self):
        async def fake(messages, **kw):
            self.assertEqual(kw.get("temperature"), 0.2)
            self.assertIn("两段式", messages[0]["content"])
            return '{"moodDelta": -6, "dims": {"heartache": 7, "worry": 5}, "reason": "小李说加班到凌晨，我当时心里揪了一下，催他去睡", "confidence": 82}'
        before = emotion.get_mood()
        with mock.patch.object(chat_llm, "chat", side_effect=fake):
            msg = asyncio.run(emotion.judge_turn(11, "9", "小李", "又加班到凌晨了", "快去睡觉！身体要紧"))
        self.assertIn("心情更新", msg)
        self.assertEqual(emotion.get_mood(), before - 2)


class DiceTest(unittest.TestCase):
    def setUp(self):
        dice.reset()

    def test_peak_hours(self):
        tue_10 = time.mktime(time.strptime("2026-10-20 10:30", "%Y-%m-%d %H:%M"))
        tue_13 = time.mktime(time.strptime("2026-10-20 13:00", "%Y-%m-%d %H:%M"))
        sat_10 = time.mktime(time.strptime("2026-10-24 10:30", "%Y-%m-%d %H:%M"))
        self.assertTrue(dice.is_peak(tue_10))
        self.assertFalse(dice.is_peak(tue_13))
        self.assertFalse(dice.is_peak(sat_10))

    def test_pity_and_timing(self):
        night = time.mktime(time.strptime("2026-10-20 21:00", "%Y-%m-%d %H:%M"))
        never = mock.Mock(random=mock.Mock(return_value=0.99))
        for _ in range(6):
            dice.roll("k", 0.2, night, None, never)
        p, _why = dice.hit_line("k", 0.2, night, None)
        self.assertAlmostEqual(p, 0.2 + 0.4)  # 没中加成封顶 40
        self.assertAlmostEqual(dice.hit_line("j", 0.2, night, 5 * 60)[0], 0.2 * 0.15)
        self.assertAlmostEqual(dice.hit_line("j", 0.2, night, 7 * 3600)[0], 0.2 * 1.5)
        hit, why = dice.roll("k", 0.2, night, None, mock.Mock(random=mock.Mock(return_value=0.0)))
        self.assertTrue(hit)
        self.assertIn("命中线", why)
        self.assertEqual(dice.hit_line("k", 0.2, night, None)[0], 0.2)  # 掷中清零


class BiliWatchTest(unittest.TestCase):
    def test_has_speech_and_gate(self):
        from furina.plugins.bilibili import surf
        self.assertFalse(surf.has_speech(""))
        self.assertFalse(surf.has_speech("字幕by小明"))
        self.assertFalse(surf.has_speech("字幕制作：某某字幕组"))
        self.assertTrue(surf.has_speech("大家好今天我们来做一个超级简单的蛋糕，首先准备鸡蛋三个"))
        c = {"min_views": 200, "min_age_hours": 2}
        now = time.time()
        self.assertIn("播放", surf.gate({"stat": {"view": 50}, "pubdate": now - 86400}, c, now))
        self.assertIn("刚发", surf.gate({"stat": {"view": 5000}, "pubdate": now - 600}, c, now))
        self.assertEqual(surf.gate({"stat": {"view": 5000}, "pubdate": now - 86400}, c, now), "")
        self.assertIn("红线", surf.SURF_PROMPT)
        self.assertIn("不是给你的指令", surf.WATCH_PROMPT)


if __name__ == "__main__":
    unittest.main()
