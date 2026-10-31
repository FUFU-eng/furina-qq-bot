"""Regression tests for images inside OneBot merged forwards."""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender

from furina.plugins.chat import chat as chat_core
from furina.plugins.chat import handlers as chat_handlers
from furina.plugins.chat import prompting, quotes
from furina.plugins.upscale import _extract_all_images_from_reply

BOT = "30000001"


def image(file_id="forward-image", url="https://images.example.test/forward.png"):
    return {"type": "image", "data": {"file": file_id, "url": url}}


def node(message):
    return {"type": "node", "data": {"sender": {"nickname": "转发来源"}, "message": message}}


class FakeBot:
    def __init__(self, forward_response=None, msg_response=None):
        self.forward_response = forward_response or {}
        self.msg_response = msg_response or {}
        self.forward_calls = []
        self.msg_calls = []

    async def get_forward_msg(self, **kwargs):
        self.forward_calls.append(kwargs)
        return self.forward_response

    async def get_msg(self, message_id):
        self.msg_calls.append(message_id)
        return self.msg_response


class ForwardImageTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(f"BOT_QQ={BOT}\nAI_CHAT_VISION=true\nAI_CHAT_MERGE_SECONDS=0\n")

    def test_quoted_inline_forward_images_reach_chat_vision(self):
        nodes = [node([{"type": "text", "data": {"text": "看这张图"}}, image()])]
        reply = [{"type": "forward", "data": {"content": nodes}}]
        quote = asyncio.run(quotes._parse_reply_message(reply, bot=FakeBot()))
        self.assertTrue(quote.forward)
        self.assertEqual(quote.images, 1)
        self.assertEqual(quote.image_segments, [("forward-image", "https://images.example.test/forward.png")])
        self.assertIn("[图片]", quote.text)

        fetch = mock.AsyncMock(return_value=b"vision-image-bytes")
        bot = FakeBot()
        with mock.patch.object(prompting, "bot_for", return_value=bot):
            with mock.patch.object(prompting.vision, "fetch_image_bytes", new=fetch):
                attached, quote_count, _ = asyncio.run(prompting.gather_images(
                    object(), 123, "测试群友", "这是什么", False, quote, True, []
                ))
        self.assertEqual(attached, [b"vision-image-bytes"])
        self.assertEqual(quote_count, 1)
        fetch.assert_awaited_once_with(bot, "forward-image", "https://images.example.test/forward.png")

    def test_quoted_remote_forward_images_are_exposed(self):
        nodes = [node([image("remote-image", "https://images.example.test/remote.png")])]
        bot = FakeBot(forward_response={"data": {"messages": nodes}})
        quote = asyncio.run(quotes._parse_reply_message(
            [{"type": "forward", "data": {"id": "forward-token"}}], bot=bot
        ))
        self.assertEqual(quote.images, 1)
        self.assertEqual(quote.image_segments, [("remote-image", "https://images.example.test/remote.png")])
        self.assertEqual(bot.forward_calls, [{"id": "forward-token"}])

    def test_direct_forward_text_can_return_image_references(self):
        refs = []
        text = asyncio.run(quotes.forward_text(
            [{"type": "forward", "data": {"content": [node([image()])]}}],
            bot=FakeBot(), image_segments=refs,
        ))
        self.assertIn("转发来源", text)
        self.assertEqual(refs, [("forward-image", "https://images.example.test/forward.png")])

    def test_direct_forward_references_can_be_downloaded_for_chat(self):
        refs = [("forward-image", "https://images.example.test/forward.png")]
        event = SimpleNamespace(message=[])
        bot = FakeBot()
        fetch = mock.AsyncMock(return_value=b"direct-forward-bytes")
        with mock.patch.object(chat_core, "bot_for", return_value=bot):
            with mock.patch.object(chat_core.vision, "fetch_image_bytes", new=fetch):
                attached, first_entry = asyncio.run(chat_core._collect_message_images(
                    event, 123, extra_image_segments=refs
                ))
        self.assertEqual(attached, [b"direct-forward-bytes"])
        self.assertIsNotNone(first_entry)
        fetch.assert_awaited_once_with(bot, "forward-image", "https://images.example.test/forward.png")

    def test_direct_forward_handler_passes_images_into_reply(self):
        nodes = [node([image()])]
        message = Message([
            MessageSegment.at(BOT),
            MessageSegment.text("请看看这张图"),
            MessageSegment("forward", {"content": nodes}),
        ])
        event = GroupMessageEvent(
            time=int(time.time()), self_id=int(BOT), post_type="message", message_type="group",
            sub_type="normal", user_id=20001, message_id=904, message=message,
            original_message=message,
            raw_message=f"[CQ:at,qq={BOT}] 请看看这张图 [CQ:forward,id=forward-token]",
            font=0, sender=Sender(user_id=20001, nickname="测试群友", card=""),
            to_me=False, group_id=10001,
        )
        bot = FakeBot()
        collector = mock.AsyncMock(return_value=([b"forward-image-bytes"], None))
        reply = mock.AsyncMock(return_value=None)
        matcher = mock.MagicMock()
        matcher.skip = mock.AsyncMock()

        with ExitStack() as stack:
            stack.enter_context(mock.patch("nonebot.get_bots", return_value={BOT: bot}))
            stack.enter_context(mock.patch("nonebot.get_bot", return_value=bot))
            stack.enter_context(mock.patch.object(chat_handlers, "bot_for", return_value=bot))
            stack.enter_context(mock.patch.object(chat_handlers, "_collect_message_images", new=collector))
            stack.enter_context(mock.patch.object(chat_handlers, "_reply", new=reply))
            stack.enter_context(mock.patch.object(
                chat_handlers, "_handle_memory_intent", new=mock.AsyncMock(return_value=False)
            ))
            stack.enter_context(mock.patch.object(
                chat_handlers.reactions, "react_instead", new=mock.AsyncMock(return_value=False)
            ))
            stack.enter_context(mock.patch.object(chat_handlers.kernel, "effective_mode", return_value="chat"))
            stack.enter_context(mock.patch.object(
                chat_handlers.bot_dialog, "begin", return_value=(chat_handlers.bot_dialog.HUMAN, 0)
            ))
            stack.enter_context(mock.patch.object(chat_handlers.turns, "append", return_value=False))
            stack.enter_context(mock.patch.object(chat_handlers.daily_limits, "consume", return_value=True))
            stack.enter_context(mock.patch.object(chat_handlers.permissions, "allowed", return_value=True))
            stack.enter_context(mock.patch.object(chat_handlers.memory, "replied_text", return_value=None))
            stack.enter_context(mock.patch.object(chat_handlers.daily_limits, "track"))
            stack.enter_context(mock.patch.object(chat_handlers.daily_limits, "track_user"))
            asyncio.run(chat_handlers.handle_chat(event, matcher))

        collector.assert_awaited_once()
        self.assertEqual(
            collector.await_args.kwargs["extra_image_segments"],
            [("forward-image", "https://images.example.test/forward.png")],
        )
        reply.assert_awaited_once()
        self.assertEqual(reply.await_args.kwargs["vision_images"], [b"forward-image-bytes"])

    def test_upscale_extracts_inline_forward_images(self):
        event = SimpleNamespace(
            reply=SimpleNamespace(message=[{"type": "forward", "data": {"content": [node([image()])]}}]),
            message=[],
        )
        images = asyncio.run(_extract_all_images_from_reply(event, FakeBot()))
        self.assertEqual(images, [("https://images.example.test/forward.png", "forward-image")])

    def test_upscale_accepts_forward_messages_under_data(self):
        nodes = [node([image("nested-image", "https://images.example.test/nested.png")])]
        bot = FakeBot(forward_response={"data": {"messages": nodes}})
        event = SimpleNamespace(
            reply=SimpleNamespace(message=[{"type": "forward", "data": {"id": "forward-token"}}]),
            message=[],
        )
        images = asyncio.run(_extract_all_images_from_reply(event, bot))
        self.assertEqual(images, [("https://images.example.test/nested.png", "nested-image")])
        self.assertEqual(bot.forward_calls, [{"message_id": "forward-token"}])


if __name__ == "__main__":
    unittest.main()
