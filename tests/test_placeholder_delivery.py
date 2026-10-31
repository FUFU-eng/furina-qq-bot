"""QQ 原始表情占位串不能被当作聊天正文，也不能清理后留下空艾特。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from furina.plugins.chat import cleanup, humanize, proactive, seek

PLACEHOLDER = "[e590:A68463|200000001]"
ENV = (
    "AI_CHAT_NO_PERIOD=true\nAI_CHAT_HUMANIZE=0\nAI_CHAT_DEAI=false\n"
    "AI_CHAT_MAX_REPLY_CHARS=0\nAI_CHAT_SEND_LIMIT_ENABLED=false\n"
    "AI_CHAT_MAX_SEND_PER_MINUTE=0\nAI_CHAT_MAX_SEND_PER_HOUR=0\n"
)


class PlaceholderDeliveryTest(unittest.TestCase):
    def setUp(self):
        env_file = _env.TMP / ".env"
        self._old_env = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        _env.write_env(ENV)
        humanize.reset()

    def tearDown(self):
        _env.write_env(self._old_env)
        humanize.reset()

    def test_proactive_rejects_exact_long_placeholder_from_report(self):
        self.assertEqual(proactive._clean(PLACEHOLDER), "")

    def test_context_cleanup_keeps_real_words_but_not_placeholder(self):
        self.assertEqual(cleanup._strip_context_markers("想吃甜点 " + PLACEHOLDER), "想吃甜点")

    def test_proactive_keeps_other_lines_after_removing_placeholder(self):
        source = PLACEHOLDER + "\n想吃甜点\n你看着办"
        self.assertEqual(proactive._clean(source, 2), "想吃甜点\n你看着办")

    def test_proactive_handles_placeholder_split_across_model_lines(self):
        source = "[e590:A68463|\n200000001]\n想吃甜点"
        self.assertEqual(proactive._clean(source, 2), "想吃甜点")

    def test_cleanup_is_independent_of_period_style_switch(self):
        _env.write_env(ENV.replace("AI_CHAT_NO_PERIOD=true", "AI_CHAT_NO_PERIOD=false"))
        self.assertEqual(humanize.prepare_message("收到。" + PLACEHOLDER), "收到。")
        self.assertEqual(humanize.prepare_message("正常句号。"), "正常句号。")

    def test_does_not_hardcode_one_emoji_number_or_hash(self):
        for value in (PLACEHOLDER, "[e1:a0b1c2|123456789]", "[E1200:0123ABCDEF|1234567890123]", "[ e23 : F0A9 | 123456 ]"):
            with self.subTest(value=value):
                self.assertEqual(humanize.prepare_message("看到了" + value), "看到了")

    def test_adjacent_text_segments_are_cleaned_together_without_mutation(self):
        original = Message([
            MessageSegment.at(20002), MessageSegment.text(" 想吃甜点[e590:"),
            MessageSegment.text("A68463|200000001]"),
        ])
        prepared = humanize.prepare_message(original)
        self.assertEqual(prepared.extract_plain_text(), " 想吃甜点")
        self.assertEqual(prepared[0], original[0])
        self.assertIn(PLACEHOLDER, original.extract_plain_text())

    def test_placeholder_only_and_mention_only_residue_are_not_sent(self):
        sent = []
        async def send(message):
            sent.append(message)
            return {"message_id": 1}
        async def run():
            wrapped = humanize.guarded(send, 700001)
            for message in (
                PLACEHOLDER,
                MessageSegment.text(PLACEHOLDER),
                Message([MessageSegment.at(20002), MessageSegment.text(" " + PLACEHOLDER)]),
                Message([MessageSegment.reply(123), MessageSegment.at(20002), MessageSegment.text(PLACEHOLDER)]),
            ):
                self.assertIsNone(await wrapped(message))
        asyncio.run(run())
        self.assertEqual(sent, [])
        self.assertEqual(humanize.breaker_status()["sent_last_hour"], {})

    def test_real_media_and_forwarded_original_are_preserved(self):
        image = MessageSegment.image("https://example.com/original.png")
        face = MessageSegment.face(14)
        custom_face = MessageSegment("mface", {"emoji_id": "test-face", "key": "test-key", "emoji_package_id": "1"})
        forwarded = MessageSegment("node", {"content": [{"type": "text", "data": {"text": "群友原文 " + PLACEHOLDER}}]})
        original = Message([MessageSegment.at(20002), MessageSegment.text(PLACEHOLDER), image, face, custom_face, forwarded])
        prepared = humanize.prepare_message(original)
        self.assertEqual(prepared.extract_plain_text(), "")
        self.assertEqual([s.type for s in prepared], ["at", "image", "face", "mface", "node"])
        self.assertEqual(prepared[-1].data, forwarded.data)
        self.assertEqual(original[1].data["text"], PLACEHOLDER)

    def test_ordinary_brackets_numbers_links_and_deliberate_at_are_unchanged(self):
        _env.write_env(ENV.replace("AI_CHAT_NO_PERIOD=true", "AI_CHAT_NO_PERIOD=false"))
        text = "[UID:123|456] [e590:not-an-emoji|name] UID 123456789 https://example.com/p.png"
        self.assertEqual(humanize.prepare_message(text), text)
        at_only = Message([MessageSegment.at(20002)])
        self.assertEqual(humanize.prepare_message(at_only), at_only)

    def test_seek_compose_returns_empty_for_reported_placeholder(self):
        with patch.object(proactive, "base_system", return_value="测试人设"), \
             patch.object(seek.llm, "chat", new=AsyncMock(return_value=PLACEHOLDER)):
            text = asyncio.run(seek._compose("700001", "30000001", "测试主动找人", 1.0, 2))
        self.assertEqual(text, "")

    def test_seek_send_cannot_leak_placeholder_or_send_bare_at(self):
        bot = SimpleNamespace(send_group_msg=AsyncMock(return_value={"message_id": 1}))
        with patch.object(humanize, "pause", new=AsyncMock()):
            sent = asyncio.run(seek._send(bot, "700001", "20002", PLACEHOLDER))
        self.assertFalse(sent)
        bot.send_group_msg.assert_not_awaited()

    def test_seek_send_preserves_at_and_real_text(self):
        bot = SimpleNamespace(send_group_msg=AsyncMock(return_value={"message_id": 1}))
        with patch.object(humanize, "pause", new=AsyncMock()):
            sent = asyncio.run(seek._send(bot, "700001", "20002", "想吃甜点" + PLACEHOLDER))
        self.assertTrue(sent)
        message = bot.send_group_msg.await_args.kwargs["message"]
        self.assertEqual(message[0].type, "at")
        self.assertEqual(message.extract_plain_text(), " 想吃甜点")


if __name__ == "__main__":
    unittest.main()
