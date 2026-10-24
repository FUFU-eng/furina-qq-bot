"""她知道群主在养她（像吃白饭的大肥鱼）；不出现技术名词和账目。也认识最近巨火的大肥鱼。"""
from tests import _env  # noqa: F401  (must be first)

import random
import time
import unittest

from furina.plugins.chat import huoren, memory, prompting, upkeep
from furina.services import slang

GID = 515151
JARGON = ("token", "Token", "DeepSeek", "deepseek", "余额", "模型", "额度", "API")


class UpkeepTest(unittest.TestCase):
    def setUp(self):
        _env.write_env("AI_CHAT_UPKEEP=1\n")
        memory.reset(GID)

    def test_topic_detection(self):
        for s in ("但是改完框架一天要花到三块钱了", "负担不起", "养不起你了", "一天 3 块", "token 烧得好快"):
            self.assertTrue(upkeep.TOPIC_RE.search(s), s)
        for s in ("我们一块去吃饭", "今天好热", "元素反应好难"):
            self.assertFalse(upkeep.TOPIC_RE.search(s), s)

    def test_note_uses_recent_lines_and_no_jargon(self):
        memory.add_message(GID, "user", "沃超爱芙啦：但是改完框架一天要花到三块钱了", ts=time.time() - 5, meta={"uid": "1", "mid": 1})
        n = upkeep.note(GID, "负担不起")
        self.assertIn("养你", n)
        self.assertIn("吃白饭", n)
        for w in JARGON:
            self.assertNotIn(w, n)
            self.assertNotIn(w, upkeep.static_line())
        self.assertEqual(upkeep.note(GID + 1, "今天好热"), "")

    def test_switch_off(self):
        _env.write_env("AI_CHAT_UPKEEP=0\n")
        self.assertEqual(upkeep.static_line(), "")
        self.assertEqual(upkeep.note(GID, "一天三块钱"), "")

    def test_static_line_in_cached_part(self):
        self.assertIn("大肥鱼", upkeep.static_line())
        src = open(prompting.__file__, encoding="utf-8").read()
        self.assertIn("upkeep.static_line() + DYN_MARK", src)


class DafeiyuTest(unittest.TestCase):
    def test_slang_knows_dafeiyu(self):
        terms = [t for t, _ in slang.find_in("你就是吃白饭的大肥鱼吧")]
        self.assertIn("吃白饭", terms)
        self.assertTrue(any("大肥鱼" in t for t in terms))

    def test_hot_note_only_when_mentioned(self):
        self.assertEqual(len(slang.hot_notes("B站最近闹鱼灾了")), 1)
        self.assertIn("溟月", slang.hot_notes("鲸鱼娘好可爱")[0])
        self.assertEqual(slang.hot_notes("今天好热"), [])

    def test_hot_note_reaches_prompt(self):
        _env.write_env("AI_CHAT_SLANG=1\nAI_CHAT_SLANG_PROBABILITY=0\nAI_CHAT_TRENDING=0\n")
        memory.reset(GID)
        note = huoren.slang_note(GID, "你知道蓝色大肥鱼吗", rng=random.Random(1))
        self.assertIn("最近网上巨火的", note)
        self.assertEqual(huoren.slang_note(GID, "今天好热", rng=random.Random(1)).count("最近网上巨火"), 0)


if __name__ == "__main__":
    unittest.main()
