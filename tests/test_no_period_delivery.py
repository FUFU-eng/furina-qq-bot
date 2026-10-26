"""句号风格必须在发送前兜底，而不是依赖模型每次遵守提示词。"""
from tests import _env  # noqa: F401

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from furina.plugins.chat import actions, chat, cleanup, humanize, memory, proactive

ENV = "AI_CHAT_NO_PERIOD=true\nAI_CHAT_HUMANIZE=0\nAI_CHAT_DEAI=false\nAI_CHAT_MAX_REPLY_CHARS=0\nAI_CHAT_REPLY_SPLIT_PROBABILITY=0\nAI_CHAT_MAX_SEND_PER_MINUTE=0\nAI_CHAT_MAX_SEND_PER_HOUR=0\n"


class NoPeriodDeliveryTest(unittest.TestCase):
    def setUp(self):
        env_file = _env.TMP / ".env"  # 仅测试临时目录，不读真实配置
        self._previous_env = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        _env.write_env(ENV)
        humanize.reset()

    def tearDown(self):
        _env.write_env(self._previous_env)
        humanize.reset()

    def test_all_sentence_dot_shapes_and_terminal_symbols(self):
        cases = {
            "好吧。": "好吧",
            "行。我知道了。": "行，我知道了",
            "Hello. I'm here.": "Hello，I'm here",
            "知道了．我看看｡": "知道了，我看看",
            "OK🙂.": "OK🙂",
            "知道了。🙂": "知道了🙂",
            "她说「知道了。」": "她说「知道了」",
            '她说"知道了。"': '她说"知道了"',
            "好。<<STICKER:3>>": "好<<STICKER:3>>",
            "第一行。\n第二行.": "第一行\n第二行",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(cleanup.drop_periods(source), expected)

    def test_preserves_ellipses_and_machine_literals(self):
        text = "版本 v1.2.3。小数 0.02。看 https://a.example.com/a.b?q=3.5。"
        self.assertEqual(cleanup.drop_periods(text), "版本 v1.2.3，小数 0.02，看 https://a.example.com/a.b?q=3.5")
        self.assertEqual(cleanup.drop_periods("文件 config.json。邮箱 a.b@example.com。"), "文件 config.json，邮箱 a.b@example.com")
        self.assertEqual(cleanup.drop_periods("……。。。等一下..."), "……。。。等一下...")
        text = '代码 `obj.value = 1.5`。\n```python\nprint("你好。")\nx = 1.5\n```'
        self.assertEqual(cleanup.drop_periods(text), '代码 `obj.value = 1.5`\n```python\nprint("你好。")\nx = 1.5\n```')

    def test_plain_segment_and_message_are_cleaned_without_mutation(self):
        seen = []
        async def send(m):
            seen.append(m)
            return {"message_id": 1}
        image = MessageSegment.image("https://a.example.com/a.b.png")
        record = MessageSegment.record("file:///voice/v1.2.wav")
        node = MessageSegment("node", {"content": [{"type": "text", "data": {"text": "转发原文。"}}]})
        original = Message([MessageSegment.at(20002), MessageSegment.text(" 好的。"), image, record, node])
        async def run():
            f = humanize.guarded(send, 7)
            await f("是的。没错。")
            await f(MessageSegment.text("收到🙂."))
            await f(original)
        asyncio.run(run())
        self.assertEqual(seen[0], "是的，没错")
        self.assertEqual(seen[1].data["text"], "收到🙂")
        self.assertEqual(seen[2].extract_plain_text(), " 好的")
        self.assertEqual(original.extract_plain_text(), " 好的。")
        self.assertEqual(seen[2][0].data, original[0].data)
        self.assertEqual(seen[2][2].data, image.data)
        self.assertEqual(seen[2][3].data, record.data)
        self.assertEqual(seen[2][4].data, node.data)  # 不改真实转发里的用户原文

    def test_adjacent_text_runs_keep_sentence_boundary(self):
        m = Message([MessageSegment.text("第一句。"), MessageSegment.text("第二句。")])
        self.assertEqual(humanize.prepare_message(m).extract_plain_text(), "第一句，第二句")

    def test_disabled_switch_and_period_only_reply(self):
        _env.write_env(ENV.replace("AI_CHAT_NO_PERIOD=true", "AI_CHAT_NO_PERIOD=false"))
        self.assertEqual(humanize.prepare_message("好。"), "好。")
        _env.write_env(ENV)
        sent = []
        async def send(m): sent.append(m)
        asyncio.run(humanize.guarded(send, 8)(MessageSegment.text("。")))
        self.assertEqual(sent, [])

    def test_proactive_mid_sentence_is_cleaned(self):
        self.assertEqual(proactive._clean("我刚刚。想起一件事。", 1), "我刚刚，想起一件事")
        self.assertEqual(proactive._clean("……。。。", 1), "……。。。")

    def test_polish_preserves_expressive_ellipsis(self):
        memory.reset(777001)
        result = asyncio.run(chat._polish([], "……。。。", 777001))
        self.assertEqual(result, "……。。。")

    def test_send_text_cleans_each_final_part(self):
        sent = []
        async def send(m): sent.append(m)
        with patch.object(chat, "burst_parts", return_value=["好。", "收到🙂."]), patch.object(humanize, "pause", new=AsyncMock()):
            asyncio.run(chat._send_text(send, SimpleNamespace(user_id=20002, message_id=17), "好。收到🙂.", None, False, 7))
        self.assertEqual([m.extract_plain_text() for m in sent], ["好", "收到🙂"])

    def test_ai_at_content_cannot_bypass_style(self):
        class Bot:
            def __init__(self): self.sent = []
            async def get_group_member_list(self, **kw):
                return [{"user_id": 20002, "nickname": "测试群友", "card": "测试群友"}]
            async def send_group_msg(self, **kw):
                self.sent.append(kw["message"])
        bot = Bot()
        with patch.object(actions, "bot_for", return_value=bot):
            asyncio.run(actions.execute(actions.ReplyActions(at_target="测试群友", at_content="等一下。我来看看。"), SimpleNamespace(raw_message=""), 7, AsyncMock()))
        self.assertEqual(bot.sent[0].extract_plain_text(), " 等一下，我来看看")
