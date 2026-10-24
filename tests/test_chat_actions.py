"""AI 回复动作标记：解析/剥离、非艾特时丢弃副作用、回复搜图意图。"""
from tests import _env  # noqa: F401  (must be first)

import unittest
from types import SimpleNamespace

from furina.plugins.chat import actions


class ParseTest(unittest.TestCase):
    def test_markers_stripped_and_parsed(self):
        text = "好呀[SEND_IMAGE:30] [AT:小明:快来] [ESSENCE][ADD_SETTING:小明:爱吃甜点]"
        clean, act = actions.parse(text, 1)
        self.assertEqual(clean.strip(), "好呀")
        self.assertEqual(act.send_image_count, 10)  # 上限 10
        self.assertEqual((act.at_target, act.at_content), ("小明", "快来"))
        self.assertTrue(act.essence)
        self.assertEqual((act.add_target, act.add_value), ("小明", "爱吃甜点"))
        self.assertTrue(act.has_side_effects())

    def test_search_and_upscale_are_not_side_effects(self):
        clean, act = actions.parse("[SEARCH_IMAGE][UPSCALE]", 1)
        self.assertEqual(clean, "")
        self.assertTrue(act.search_image and act.upscale)
        self.assertFalse(act.has_side_effects())

    def test_drop_side_effects(self):
        _, act = actions.parse("嗯[DELETE_SETTING:小红][AT:a:b]", 1)
        act.drop_side_effects()
        self.assertFalse(act.has_side_effects())

    def test_wants_search(self):
        quote = SimpleNamespace(images=["x"], failed=False)
        self.assertTrue(actions.wants_search("我这就帮你找原图", quote))
        self.assertFalse(actions.wants_search("我这就帮你找原图", None))
        self.assertFalse(actions.wants_search("今天天气不错", quote))


if __name__ == "__main__":
    unittest.main()
