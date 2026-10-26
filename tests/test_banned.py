"""主人点名禁止的句式（「这X我服」）一次都不能发出去。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest
from unittest import mock

from furina.plugins.chat import banned, kernel
from tests.test_humanlike import BASE_ENV, GID


class BannedTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE_ENV)

    def test_builtin_hits(self):
        for s in ("化学撕了生物倒是记得牢，这优先级我服", "这波我服了", "这操作我是服的", "这优先级，我服",
                  "这审美我真服了"):
            self.assertEqual(banned.hit(s), "这…我服", s)
        for s in ("这边我服务器卡了", "我服了你", "这衣服好看", "这个我服从安排", "服了"):
            self.assertEqual(banned.hit(s), "", s)

    def test_strip_keeps_the_rest(self):
        self.assertEqual(banned.strip("化学撕了生物倒是记得牢，这优先级我服"), "化学撕了生物倒是记得牢")
        self.assertEqual(banned.strip("这波我服了"), "")
        self.assertEqual(banned.strip("好耶"), "好耶")

    def test_custom_patterns(self):
        _env.write_env(BASE_ENV + "AI_CHAT_BANNED_PATTERNS=不愧是…,笑死…了\n")
        self.assertEqual(banned.hit("不愧是你啊"), "不愧是…")
        self.assertEqual(banned.hit("笑死我了"), "笑死…了")
        self.assertIn("「笑死…了」", banned.prompt_line())

    def test_sim2_say_retries_then_strips(self):
        ctx = kernel.RunCtx("group", "30000001", GID)
        r = asyncio.run(kernel._say(ctx, ["化学撕了生物倒是记得牢，这优先级我服"]))
        self.assertFalse(r["ok"])
        self.assertIn("禁止", r["reason"])
        sent = []

        class Bot:
            async def send_group_msg(self, group_id, message):
                sent.append(str(message))
                return {"message_id": 1}

        with mock.patch.object(kernel, "bot_for", return_value=Bot()), \
             mock.patch.object(kernel.humanize, "guarded", side_effect=lambda fn, gid: fn):
            asyncio.run(kernel._say(ctx, ["生物倒是记得牢，这优先级我服"]))
        self.assertTrue(sent)
        self.assertFalse(any("我服" in s for s in sent))

    def test_settings_field(self):
        from pathlib import Path
        from panel.services import huoren_settings as hs
        self.assertIn('"AI_CHAT_BANNED_PATTERNS"', Path(hs.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
