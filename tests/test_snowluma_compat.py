"""NapCat → SnowLuma 迁移后的协议差异：负数消息 ID、语音转码、图片地址、内层合并转发、账号确认把关。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import base64
import io
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from nonebot.exception import MockApiException
from PIL import Image

from furina.core import forwards, images, qq_transport
from furina.services import voice_asr


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class NegativeMessageIdTest(unittest.TestCase):
    """SnowLuma 的 message_id 是有符号 int32，负数和正数一样是合法编号。"""

    def test_is_mid_accepts_negative(self):
        from furina.plugins.chat import kernel

        self.assertTrue(kernel._is_mid("-141545859"))
        self.assertTrue(kernel._is_mid("627378465"))
        self.assertFalse(kernel._is_mid("0"))
        self.assertFalse(kernel._is_mid(""))
        self.assertFalse(kernel._is_mid("-"))
        self.assertFalse(kernel._is_mid("m123"))

    def test_leak_filter_catches_negative_ids(self):
        from furina.plugins.chat import kernel

        self.assertEqual(kernel._clean_final("我回的是 m-141545859 那条"), "")
        self.assertEqual(kernel._clean_final("我回的是 m141545859 那条"), "")
        self.assertEqual(kernel._clean_final("好呀"), "好呀")


class VoiceRecordTest(unittest.TestCase):
    """SnowLuma 的 get_record(out_format=wav)：file 是下载地址，转好的 wav 在 base64 里。"""

    def _hear(self, record_result):
        seen = {}

        async def fake_transcribe(path, timeout=90.0):
            seen["path"] = path
            seen["exists"] = os.path.isfile(path)
            with open(path, "rb") as f:
                seen["data"] = f.read()
            return "你好呀"

        class Bot:
            async def call_api(self, api, **kw):
                seen["api"] = (api, kw)
                return record_result

        with mock.patch.object(voice_asr, "available", return_value=(True, "")), \
                mock.patch.object(voice_asr, "transcribe", new=fake_transcribe):
            text = asyncio.run(voice_asr.hear_segment(Bot(), {"file": "abc.amr", "url": "https://x.test/a"}))
        return text, seen

    def test_snowluma_base64_wav_is_transcribed_and_cleaned_up(self):
        wav = b"RIFF" + b"\0" * 100
        text, seen = self._hear({"file": "https://multimedia.nt.qq.com.cn/download?x", "url": "https://x",
                                 "out_format": "wav", "base64": base64.b64encode(wav).decode()})
        self.assertEqual(text, "你好呀")
        self.assertEqual(seen["api"], ("get_record", {"file": "abc.amr", "out_format": "wav"}))
        self.assertTrue(seen["exists"])
        self.assertEqual(seen["data"], wav)
        self.assertFalse(os.path.exists(seen["path"]))  # 临时 wav 用完删掉

    def test_url_without_base64_is_not_treated_as_local_file(self):
        text, seen = self._hear({"file": "https://multimedia.nt.qq.com.cn/download?x", "url": "https://x"})
        self.assertEqual(text, "")
        self.assertNotIn("path", seen)

    def test_napcat_local_file_still_works(self):
        p = _env.TMP / "napcat_record.wav"
        p.write_bytes(b"RIFF" + b"\0" * 10)
        text, seen = self._hear({"file": str(p)})
        self.assertEqual(text, "你好呀")
        self.assertEqual(seen["path"], str(p))
        self.assertTrue(p.exists())  # NapCat 自己的缓存文件不能删

    def test_too_long_base64_is_skipped(self):
        big = base64.b64encode(b"\0" * (voice_asr.MAX_WAV_BYTES + 1)).decode()
        text, seen = self._hear({"file": "https://x", "base64": big})
        self.assertEqual(text, "")
        self.assertNotIn("path", seen)


class ImageSourceTest(unittest.TestCase):
    def test_media_info_source(self):
        p = _env.TMP / "cached.png"
        p.write_bytes(_png())
        self.assertEqual(images.media_info_source({"file": str(p), "url": "https://a.test/x"}), (str(p), "https://a.test/x"))
        self.assertEqual(images.media_info_source({"file": "https://fresh.test/1", "url": ""}), ("", "https://fresh.test/1"))
        self.assertEqual(images.media_info_source({"file": "ABCDEF.png"}), ("", ""))
        self.assertEqual(images.media_info_source(None), ("", ""))

    def test_download_candidates_prefer_fresh_and_dedupe(self):
        self.assertEqual(images.download_candidates("https://f", "https://old"), ["https://f", "https://old"])
        self.assertEqual(images.download_candidates("https://f", "https://f"), ["https://f"])
        self.assertEqual(images.download_candidates("", "ABC.png"), [])

    def test_fetch_uses_refreshed_url_from_get_image(self):
        png = _png()
        calls = []

        class Resp:
            def __init__(self, status, content):
                self.status_code, self.content = status, content

        class Client:
            async def get(self, url, timeout=None):
                calls.append(url)
                return Resp(200, png) if url == "https://fresh.test/img" else Resp(403, b"")

        class Bot:
            async def get_image(self, file):
                return {"file": "https://fresh.test/img", "url": "https://fresh.test/img", "file_size": "1", "file_name": file}

        with mock.patch.object(images, "_get_client", new=mock.AsyncMock(return_value=Client())):
            data = asyncio.run(images.fetch_image_bytes(Bot(), "ABC.png", "https://expired.test/img"))
        self.assertEqual(data, png)
        self.assertEqual(calls, ["https://fresh.test/img"])

    def test_fetch_falls_back_to_event_url_when_get_image_fails(self):
        png = _png()

        class Resp:
            status_code, content = 200, png

        client = SimpleNamespace(get=mock.AsyncMock(return_value=Resp()))

        class Bot:
            async def get_image(self, file):
                raise RuntimeError("image not found in cache")

        with mock.patch.object(images, "_get_client", new=mock.AsyncMock(return_value=client)):
            data = asyncio.run(images.fetch_image_bytes(Bot(), "x.png", "https://event.test/img"))
        self.assertEqual(data, png)
        client.get.assert_awaited_once()


def _node(message):
    return {"type": "node", "data": {"sender": {"nickname": "群友"}, "message": message}}


class NestedForwardTest(unittest.TestCase):
    """SnowLuma 的内层合并转发只给 id；补成 NapCat 那样的内联 content。"""

    def test_inner_forward_is_inlined(self):
        inner = [_node([{"type": "text", "data": {"text": "里面那层"}}])]

        class Bot:
            calls = []

            async def get_forward_msg(self, id):  # noqa: A002
                self.calls.append(id)
                return {"messages": inner}

        bot = Bot()
        nodes = [_node([{"type": "forward", "data": {"id": "inner-res"}}])]
        asyncio.run(forwards.inline_nested(bot, nodes))
        self.assertEqual(bot.calls, ["inner-res"])
        self.assertEqual(nodes[0]["data"]["message"][0]["data"]["content"], inner)

    def test_inline_content_and_budget_are_respected(self):
        class Bot:
            calls = []

            async def get_forward_msg(self, id):  # noqa: A002
                self.calls.append(id)
                return {"messages": [_node([{"type": "forward", "data": {"id": id + "x"}}])]}

        bot = Bot()
        nodes = [_node([{"type": "forward", "data": {"id": "a"}}]),
                 _node([{"type": "forward", "data": {"content": [_node([])]}}])]
        asyncio.run(forwards.inline_nested(bot, nodes, max_depth=5, budget=2))
        self.assertEqual(bot.calls, ["a", "ax"])  # 已内联的不再拉；预算用完就停

    def test_quotes_expand_text_of_inner_forward(self):
        from furina.plugins.chat import quotes

        inner = [_node([{"type": "text", "data": {"text": "套娃里的话"}}])]

        class Bot:
            async def get_forward_msg(self, id):  # noqa: A002
                if id == "outer":
                    return {"messages": [_node([{"type": "forward", "data": {"id": "inner"}}])]}
                return {"messages": inner}

        out = asyncio.run(quotes.forward_text([{"type": "forward", "data": {"id": "outer"}}], bot=Bot()))
        self.assertIn("套娃里的话", out)


class TransportGuardTest(unittest.TestCase):
    def setUp(self):
        p = _env.TMP / ".env"
        self.old = p.read_text(encoding="utf-8") if p.exists() else ""

    def tearDown(self):
        _env.write_env(self.old)

    def test_invalid_transport_fails_closed_without_raising(self):
        from furina.plugins.qq_transport_guard import _block_unconfirmed_outbound, _blocked

        _env.write_env("QQ_TRANSPORT=snowlumaa\nBOT_QQ=111111\nSNOWLUMA_ACCOUNT_CONFIRMED=true\n")
        self.assertFalse(qq_transport.account_confirmed("111111"))
        self.assertTrue(_blocked(SimpleNamespace(self_id="111111")))
        with self.assertRaises(MockApiException):
            asyncio.run(_block_unconfirmed_outbound(SimpleNamespace(self_id="111111"), "send_group_msg", {}))

    def test_napcat_is_always_allowed(self):
        _env.write_env("QQ_TRANSPORT=napcat\n")
        self.assertTrue(qq_transport.account_confirmed(""))

    def test_history_seed_waits_for_confirmation(self):
        from furina.plugins.chat import history

        _env.write_env("QQ_TRANSPORT=snowluma\nBOT_QQ=111111\nSNOWLUMA_ACCOUNT_CONFIRMED=false\n")
        bot = SimpleNamespace(self_id="111111")
        with mock.patch("nonebot.get_bots", return_value={}):
            self.assertFalse(asyncio.run(history._wait_account_confirmed(bot, step=0.01, limit=1)))
        _env.write_env("QQ_TRANSPORT=snowluma\nBOT_QQ=111111\nSNOWLUMA_ACCOUNT_CONFIRMED=true\n")
        self.assertTrue(asyncio.run(history._wait_account_confirmed(bot, step=0.01, limit=1)))


if __name__ == "__main__":
    unittest.main()
