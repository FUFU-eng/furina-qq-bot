from tests import _env  # noqa: F401  (must be first)

import unittest
from unittest import mock

from furina.core import db, env, roles
from furina.plugins.chat import interaction, learning, memory_intent, role_persona
from furina.plugins.chat.persona import DAILY_RULES, generic_daily_rules

_QQ = "5550001"
_FURINA_WORDS = ("芙芙", "本导演", "水神", "枫丹", "白淞镇", "谢贝蕾妲", "大明星")


class RolesTest(unittest.TestCase):
    def setUp(self):
        self._roles = db.get("persona", "roles")
        self._profiles = db.get("persona", "profiles")
        self._lore = db.get("persona", "shared_lore")
        db.put("persona", "roles", {"roles": [
            {"id": "furina", "name": "芙宁娜-白芙", "prompt": "你是芙宁娜。"},
            {"id": "hutao", "name": "胡桃", "prompt": "你是胡桃。", "aliases": "胡桃，胡堂主、堂主",
             "self_names": "本堂主"},
            {"id": "raiden", "name": "雷电将军-测试", "prompt": "你是雷电将军。"},
        ]})
        db.put("persona", "shared_lore", {"items": [
            {"id": role_persona.FURINA_LORE_ID, "name": "芙宁娜背景", "keywords": "你是谁", "content": "芙宁娜的身世",
             "role_ids": []},
            {"id": "lore_hutao", "name": "胡桃背景", "keywords": "你是谁,往生堂", "content": "胡桃的身世",
             "role_ids": ["hutao"]},
        ]})
        self._patch = mock.patch.object(env, "bot_qq", return_value=_QQ)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        for key, val in (("roles", self._roles), ("profiles", self._profiles), ("shared_lore", self._lore)):
            if val is None:
                db.delete("persona", key)
            else:
                db.put("persona", key, val)

    def _use(self, role_id):
        db.put("persona", "profiles", [{"qq": _QQ, "nickname": "测试", "role_id": role_id}])

    def test_generic_rules_have_no_furina_words(self):
        text = generic_daily_rules("胡桃")
        for w in _FURINA_WORDS:
            self.assertNotIn(w, text)
        self.assertIn("[SEND_IMAGE:N]", text)
        self.assertIn("胡桃我们俩", text)

    def test_furina_role_keeps_original_rules(self):
        self._use("furina")
        self.assertTrue(roles.is_furina())
        self.assertEqual(roles.display_name(), "芙宁娜")
        self.assertTrue(role_persona.build_persona(_QQ).endswith(DAILY_RULES))
        self.assertIn("芙宁娜的身世", role_persona.build_shared_lore(_QQ, "你是谁"))

    def test_hutao_role(self):
        self._use("hutao")
        self.assertFalse(roles.is_furina())
        self.assertEqual(roles.aliases(), ["胡桃", "胡堂主", "堂主"])
        self.assertEqual(roles.display_name(), "胡桃")
        prompt = role_persona.build_persona(_QQ)
        self.assertTrue(prompt.startswith("你是胡桃。"))
        for w in _FURINA_WORDS:
            self.assertNotIn(w, prompt)
        lore = role_persona.build_shared_lore(_QQ, "你是谁呀")
        self.assertIn("胡桃的身世", lore)
        self.assertNotIn("芙宁娜的身世", lore)
        self.assertEqual(roles.flavor("芙宁娜的赠礼", "{name}的赠礼"), "胡桃的赠礼")
        self.assertEqual(learning.normalize_target("本堂主的设定"), "胡桃")
        self.assertIn("胡堂主", memory_intent._system_prompt())
        self.assertEqual(interaction.sentiment("胡桃真可爱"), 1)

    def test_role_without_aliases_uses_name(self):
        self._use("raiden")
        self.assertEqual(roles.display_name(), "雷电将军")
        self.assertNotIn("芙宁娜的身世", role_persona.build_shared_lore(_QQ, "你是谁"))

    def test_unbound_is_builtin_furina(self):
        db.put("persona", "profiles", [])
        self.assertTrue(roles.is_furina())
        self.assertEqual(roles.aliases(), list(roles.FURINA_ALIASES))
        self.assertEqual(roles.flavor("芙宁娜的赠礼", "{name}的赠礼"), "芙宁娜的赠礼")


if __name__ == "__main__":
    unittest.main()
