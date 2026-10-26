"""词条/设定：她要知道自己刚记过什么；「把设定去掉」「删除xxx」这种说法也能删对。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest
from unittest import mock

from furina.plugins.chat import chat as chat_mod
from furina.plugins.chat import humanize, kernel, learning, memory
from tests.test_humanlike import BASE_ENV, GID, make_event


class DeleteByValueTest(unittest.TestCase):
    def test_content_only_delete_finds_owner(self):
        learning.add_setting(501, "沃超爱芙啦", "ntr苦主，铸币")
        learning.add_setting(501, "张三", "喜欢李四的猫")
        # 模型把「ntr苦主，铸币」拆成了 target=ntr苦主 value=铸币
        self.assertEqual(learning.delete_settings(501, "ntr苦主", "铸币")[0], 0)
        n, gone = learning.delete_by_value(501, "ntr苦主，铸币")
        self.assertEqual(n, 1)
        self.assertIn("ntr苦主，铸币", gone[0])
        self.assertEqual(learning.query_settings(501, "张三")[0], ["张三的设定：喜欢李四的猫"])

    def test_short_word_needs_exact_match(self):
        learning.add_setting(502, "李四", "张三的老婆")
        self.assertEqual(learning.delete_by_value(502, "张三"), (0, []))
        self.assertEqual(len(learning.query_settings(502, "李四")[0]), 1)


class HandledTurnTest(unittest.TestCase):
    def setUp(self):
        humanize.reset()
        _env.write_env(BASE_ENV)
        memory._histories[GID].clear()

    def _run(self, text, intent, mid):
        ev = make_event(f"[CQ:at,qq=30000001] {text}", mid=mid)
        with mock.patch.object(chat_mod.memory_intent, "detect_memory_action",
                               new=mock.AsyncMock(return_value=intent)) as det, \
             mock.patch.object(chat_mod._chat, "send", new=mock.AsyncMock()):
            res = asyncio.run(chat_mod._handle_memory_intent(ev, GID, ev.user_id, "测试群友", text, True))
        return ev, res, det

    def test_receipt_goes_into_history_and_context(self):
        ev, res, _ = self._run("添加小明的设定：爱吃鱼", {"action": "add", "target": "小明", "value": "爱吃鱼"}, 7001)
        self.assertIn("记住了", res)
        last = memory.get_history(GID)[-1]
        self.assertEqual(last["role"], "assistant")
        self.assertIn("小明的设定：爱吃鱼", last["content"])
        # 下一句「把设定去掉」时，意图识别能看到她刚才的回执
        _, res2, det = self._run("把设定去掉", {"action": "delete", "target": "小明", "value": ""}, 7002)
        self.assertIn("机器人：", det.await_args.kwargs["context"])
        self.assertIn("小明的设定：爱吃鱼", det.await_args.kwargs["context"])
        self.assertIn("删掉啦", res2)
        self.assertEqual(learning.query_settings(GID, "小明")[0], [])

    def test_content_only_delete_via_intent(self):
        learning.add_setting(GID, "阿花", "ntr苦主，铸币")
        _, res, _ = self._run("删除ntr苦主，铸币", {"action": "delete", "target": "ntr苦主", "value": "铸币"}, 7003)
        self.assertIn("删掉啦", res)
        self.assertIn("阿花", res)
        self.assertEqual(learning.query_settings(GID, "阿花")[0], [])

    def test_note_handled_enters_sim2_unread_without_at(self):
        ev = make_event("[CQ:at,qq=30000001] 添加小红的设定：猫娘", mid=7004)
        with mock.patch.object(kernel, "effective_mode", return_value="sim2"):
            kernel.note_handled(ev, GID, ev.user_id, "测试群友", "添加小红的设定：猫娘", "记住了，小红的设定：猫娘")
        st = kernel._state(str(ev.self_id), GID)
        user_e, note_e = st.unread[-2], st.unread[-1]
        self.assertFalse(user_e["at"])
        self.assertIn("添加小红的设定", user_e["text"])
        self.assertIn("记住了，小红的设定：猫娘", kernel._fmt_entry(note_e))
        self.assertIn("你自己刚做的事", kernel._fmt_entry(note_e))

    def test_note_handled_ignored_outside_sim2(self):
        ev = make_event("[CQ:at,qq=30000001] x", mid=7005)
        st = kernel._state(str(ev.self_id), GID)
        before = len(st.unread)
        with mock.patch.object(kernel, "effective_mode", return_value="sim1"):
            kernel.note_handled(ev, GID, ev.user_id, "测试群友", "x", "y")
        self.assertEqual(len(st.unread), before)


if __name__ == "__main__":
    unittest.main()
