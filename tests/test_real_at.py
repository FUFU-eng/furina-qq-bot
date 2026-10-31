"""说现在去找群友必须伴随真实艾特；自主艾特不要求先被叫到。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from furina.plugins.chat import actions, humanize, kernel, memory, proactive

SID = "30000001"
GID = 700002
OWNER = "20002"
FRIEND = "20004"
QUIET = "20006"
ENV = (
    "AI_CHAT_GROUP_OWNER_QQ=20002\nAI_CHAT_SPECIAL_QQ=20003\n"
    "AI_CHAT_HUMANIZE=0\nAI_CHAT_DEAI=false\nAI_CHAT_NO_PERIOD=true\n"
    "AI_CHAT_MAX_SEND_PER_MINUTE=0\nAI_CHAT_MAX_SEND_PER_HOUR=0\n"
)


class Bot:
    def __init__(self):
        self.sent = []
        self.members = [
            {"user_id": int(OWNER), "nickname": "真主人", "card": "真主人", "role": "member"},
            {"user_id": int(FRIEND), "nickname": "小明", "card": "小明", "role": "member"},
            {"user_id": 20005, "nickname": "主人", "card": "主人", "role": "member"},
            {"user_id": int(QUIET), "nickname": "不常说话的人", "card": "不常说话的人", "role": "member"},
        ]

    async def get_group_member_list(self, **kwargs):
        return self.members

    async def send_group_msg(self, **kwargs):
        self.sent.append(kwargs["message"])
        return {"message_id": 1000 + len(self.sent)}


class RealAtTest(unittest.TestCase):
    def setUp(self):
        p = _env.TMP / ".env"
        self.old_env = p.read_text(encoding="utf-8") if p.exists() else ""
        _env.write_env(ENV)
        kernel._groups.clear()
        humanize.reset()
        memory.reset(GID)
        self.bot = Bot()
        self.st = kernel._state(SID, GID)
        self.st.event = SimpleNamespace(user_id=int(FRIEND), message_id=101, raw_message="", self_id=SID)
        self.st.unread = [{"seq": 1, "mid": "101", "uid": FRIEND, "name": "小明", "text": "刚才那件事", "ts": 1, "at": True}]

    def tearDown(self):
        _env.write_env(self.old_env)
        kernel._groups.clear()
        humanize.reset()
        memory.reset(GID)

    def say(self, lines, at="", reason="at", nags=0, reply_to=""):
        ctx = kernel.RunCtx("group", SID, GID, reason=reason, nags=nags, at_mids=["101"])
        with patch.object(kernel, "bot_for", return_value=self.bot), \
             patch.object(kernel, "_try_voice", new=AsyncMock(return_value=False)), \
             patch.object(humanize, "pause", new=AsyncMock()):
            return asyncio.run(kernel._say(ctx, lines, at=at, reply_to=reply_to))

    def mentioned(self):
        return [str(seg.data.get("qq")) for msg in self.bot.sent for seg in ([msg] if hasattr(msg, "type") else msg) if seg.type == "at"]

    def test_screenshot_claim_without_target_is_not_published(self):
        result = self.say(["行 今天的红肠账一起算 这就去找他"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["sent"], 0)
        self.assertEqual(self.bot.sent, [])
        self.assertIn("at", result["reason"])

    def test_missing_target_cannot_bypass_check_after_an_earlier_retry(self):
        result = self.say(["行 这就去找他"], nags=2)
        self.assertFalse(result["ok"])
        self.assertEqual(self.bot.sent, [])

    def test_screenshot_with_explicit_owner_at_really_mentions_owner(self):
        result = self.say(["行 今天的红肠账一起算 这就去找他"], at=OWNER)
        self.assertTrue(result["ok"])
        self.assertEqual(self.mentioned(), [OWNER])
        self.assertIn("这就去找他", self.bot.sent[0].extract_plain_text())

    def test_explicit_owner_name_in_claim_is_automatically_resolved(self):
        result = self.say(["这就去找主人算账"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.mentioned(), [OWNER])

    def test_claim_can_resolve_named_ordinary_member(self):
        result = self.say(["我这就去找小明"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.mentioned(), [FRIEND])

    def test_you_in_claim_uses_the_replied_message_not_owner_default(self):
        result = self.say(["我这就去找你算账"], reply_to="m101")
        self.assertTrue(result["ok"])
        self.assertEqual(self.mentioned(), [FRIEND])

    def test_third_person_claim_does_not_guess_owner(self):
        result = self.say(["现在就去找她"])
        self.assertFalse(result["ok"])
        self.assertEqual(self.bot.sent, [])

    def test_immediate_contact_does_not_match_objects_negations_quotes_or_future(self):
        cases = ["我去找攻略", "现在去找文件", "我不去找他", "不用去找他", "明天我去找他", "你说「这就去找他」是吧", "我就是来找你聊天的"]
        for text in cases:
            with self.subTest(text=text):
                self.bot.sent.clear()
                result = self.say([text])
                self.assertTrue(result["ok"])
                self.assertEqual(self.mentioned(), [])

    def test_any_round_can_at_a_member_who_is_not_in_recent_context(self):
        for reason in ("random", "idle", "follow", "promise"):
            with self.subTest(reason=reason):
                self.bot.sent.clear()
                result = self.say(["过来一起玩"], at="不常说话的人", reason=reason)
                self.assertTrue(result["ok"])
                self.assertEqual(self.mentioned(), [QUIET])

    def test_unresolved_explicit_at_never_degrades_to_plain_claim(self):
        result = self.say(["过来算账"], at="根本不存在的人", nags=2)
        self.assertFalse(result["ok"])
        self.assertEqual(self.bot.sent, [])

    def test_at_marker_is_real_during_autonomous_round_without_event(self):
        self.st.event = None
        result = self.say(["[AT:不常说话的人:过来一起玩]"], reason="random")
        self.assertTrue(result["ok"])
        self.assertEqual(result["sent"], 1)
        self.assertEqual(self.mentioned(), [QUIET])

    def test_at_marker_and_claim_are_sent_together_not_silently_dropped(self):
        result = self.say(["这就去找他", "[AT:小明:今天的账一起算]"], reason="idle")
        self.assertTrue(result["ok"])
        self.assertEqual(self.mentioned(), [FRIEND])
        self.assertIn("这就去找他", self.bot.sent[0].extract_plain_text())

    def test_unknown_at_marker_does_not_publish_the_unfulfilled_claim_first(self):
        result = self.say(["这就去找他", "[AT:不存在的人:过来]"], nags=2)
        self.assertFalse(result["ok"])
        self.assertEqual(self.bot.sent, [])

    def test_owner_alias_is_by_configured_qq_not_impostor_nickname(self):
        self.st.unread.append({"seq": 2, "mid": "102", "uid": "20005", "name": "主人", "text": "冒充主人", "ts": 2})
        result = self.say(["来一下"], at="主人", reason="random")
        self.assertTrue(result["ok"])
        self.assertEqual(self.mentioned(), [OWNER])

    def test_legacy_random_action_filter_can_keep_at_but_not_other_actions(self):
        act = actions.ReplyActions(at_target="小明", at_content="过来", send_image_count=2, essence=True, delete_target="小明")
        act.drop_side_effects(keep_at=True)
        self.assertEqual((act.at_target, act.at_content), ("小明", "过来"))
        self.assertEqual(act.send_image_count, 0)
        self.assertFalse(act.essence)
        self.assertEqual(act.delete_target, "")

    def coldstart(self, raw):
        memory.add_message(GID, "user", "刚才那件事")
        with patch.object(proactive, "_pick_bot", return_value=(self.bot, SID)), \
             patch.object(proactive.permissions, "enabled_groups", return_value=[str(GID)]), \
             patch.object(proactive.live_groups, "cached", return_value=set()), \
             patch.object(proactive.brain, "proactive_allowed", return_value=True), \
             patch.object(proactive, "should_speak", return_value=(True, "一小时")), \
             patch.object(proactive, "base_system", return_value="测试人设"), \
             patch.object(proactive.llm, "chat", new=AsyncMock(return_value=raw)), \
             patch.object(kernel, "bot_for", return_value=self.bot), \
             patch.object(kernel, "_try_voice", new=AsyncMock(return_value=False)), \
             patch.object(humanize, "pause", new=AsyncMock()):
            asyncio.run(proactive.tick())

    def test_coldstart_can_choose_and_really_at_an_ordinary_member(self):
        self.coldstart("[AT:不常说话的人:过来一起玩]")
        self.assertEqual(self.mentioned(), [QUIET])

    def test_coldstart_does_not_publish_an_ambiguous_unfulfilled_claim(self):
        self.coldstart("这就去找他")
        self.assertEqual(self.bot.sent, [])

    def test_legacy_at_accepts_a_real_members_qq_number(self):
        with patch.object(actions, "bot_for", return_value=self.bot):
            asyncio.run(actions.execute(actions.ReplyActions(at_target=FRIEND, at_content="过来一起玩"), self.st.event, GID, AsyncMock()))
        self.assertEqual(self.mentioned(), [FRIEND])

    def test_legacy_owner_alias_does_not_at_an_impostor(self):
        with patch.object(actions, "bot_for", return_value=self.bot):
            asyncio.run(actions.execute(actions.ReplyActions(at_target="主人", at_content="来一下"), self.st.event, GID, AsyncMock()))
        self.assertEqual(self.mentioned(), [OWNER])

    def test_failed_send_is_not_counted_as_successful_contact(self):
        self.bot.send_group_msg = AsyncMock(side_effect=RuntimeError("test send failed"))
        result = self.say(["这就去找他"], at=FRIEND)
        self.assertFalse(result["ok"])
        self.assertEqual(result["sent"], 0)


if __name__ == "__main__":
    unittest.main()
