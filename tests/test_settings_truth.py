"""设置里写多少就是多少：随机接话的命中线、表情包比例、各种「会提高概率」的情况都能在面板里看到和改；
以及「模型花费」页读 DeepSeek 用量的解析。"""
from tests import _env  # noqa: F401  (must be first)

import random
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from furina.core import db
from furina.plugins.chat import attention, chat, cleanup, config, handlers, presence
from furina.services import llm_spend

ROOT = Path(__file__).resolve().parents[1]
GID = 424242
BASE = "AI_CHAT_REPLY_SPLIT_PROBABILITY=0\nAI_CHAT_POKE_PROBABILITY=0\n"


class RandomReplyChanceTest(unittest.TestCase):
    def setUp(self):
        attention.reset()

    def chance(self, text="今天好热", uid="555", hist=None, from_bot=False):
        with mock.patch.object(handlers.life_events, "pending", return_value=False), \
                mock.patch.object(presence, "welcome_back_probability", return_value=0.0):
            return handlers.random_reply_chance(GID, uid, "小王", text, hist or [], from_bot)

    def test_plain_is_exactly_the_setting(self):
        _env.write_env(BASE + "AI_CHAT_PROBABILITY=0.02\nAI_CHAT_CHATTY=1\n")
        self.assertEqual(self.chance(), (0.02, "平时"))

    def test_zero_turns_every_lift_off(self):
        _env.write_env(BASE + "AI_CHAT_PROBABILITY=0\nAI_CHAT_CHATTY=1\nAI_CHAT_LINGER=1\nAI_CHAT_LIFE_EVENTS=1\n")
        attention.on_reply(GID, now=time.time(), rng=random.Random(2))
        with mock.patch.object(handlers.life_events, "pending", return_value=True), \
                mock.patch.object(presence, "welcome_back_probability", return_value=0.8):
            p, _ = handlers.random_reply_chance(GID, "555", "小王", "有没有人知道？", [], False)
        self.assertEqual(p, 0.0)

    def test_question_lift_uses_setting(self):
        _env.write_env(BASE + "AI_CHAT_PROBABILITY=0.02\nAI_CHAT_CHATTY=1\nAI_CHAT_CHATTY_QUESTION_PROBABILITY=0.1\n")
        self.assertEqual(self.chance("有没有人知道这个怎么弄"), (0.1, "有人提问"))

    def test_linger_uses_setting(self):
        _env.write_env(BASE + "AI_CHAT_PROBABILITY=0.02\nAI_CHAT_LINGER=1\nAI_CHAT_LINGER_PROBABILITY=0.12\n")
        now = time.time()
        attention.on_reply(GID, now=now - 5, rng=random.Random(2))
        self.assertAlmostEqual(attention.linger_probability(GID, now), 0.12)
        self.assertEqual(self.chance(), (0.12, "刚聊完还留着"))
        _env.write_env(BASE + "AI_CHAT_PROBABILITY=0.02\nAI_CHAT_LINGER=1\nAI_CHAT_LINGER_PROBABILITY=0\n")
        self.assertEqual(self.chance(), (0.02, "平时"))  # 填 0 = 这种情况不加

    def test_thread_uses_setting(self):
        _env.write_env(BASE + "AI_CHAT_PROBABILITY=0.02\nAI_CHAT_THREAD_PROBABILITY=0.2\n")
        now = time.time()
        hist = [{"role": "user", "content": "小王：在吗", "ts": now - 20},
                {"role": "assistant", "content": "在", "ts": now - 10},
                {"role": "user", "content": "小王：那个呢", "ts": now}]
        self.assertEqual(self.chance(hist=hist), (0.2, "刚和她聊着"))

    def test_owner_wake_uses_setting(self):
        _env.write_env(BASE + "AI_CHAT_PROBABILITY=0.02\nAI_CHAT_GROUP_OWNER_QQ=999\nAI_CHAT_OWNER_WAKE_PROBABILITY=0.05\n")
        self.assertEqual(self.chance(uid="999"), (0.05, "群主说话"))

    def test_welcome_back_uses_setting(self):
        _env.write_env(BASE + "AI_CHAT_WELCOME_BACK_PROBABILITY=0.2\nAI_CHAT_WELCOME_BACK_MISSED_PROBABILITY=0.3\n")
        with mock.patch.object(presence, "_fresh_return", return_value={"missed": True, "gap": 99999, "miss_text": ""}):
            self.assertAlmostEqual(presence.welcome_back_probability(GID, "1"), 0.3)
        with mock.patch.object(presence, "_fresh_return", return_value={"missed": False, "gap": 99999, "miss_text": ""}), \
                mock.patch.object(presence, "frequent", return_value={"1": 20}):
            self.assertAlmostEqual(presence.welcome_back_probability(GID, "1"), 0.2)

    def test_no_hidden_floor_left(self):
        keys = {v[0] for v in config._SPEC.values()}
        self.assertNotIn("AI_CHAT_CHATTY_PROBABILITY", keys)
        src = (ROOT / "furina/plugins/chat/handlers.py").read_text(encoding="utf-8")
        self.assertNotIn("0.28", src)
        self.assertNotIn("CHATTY_PROBABILITY", src)


class StickerRatioTest(unittest.TestCase):
    def run_many(self, env, marker: bool, n=400):
        _env.write_env(BASE + env)
        cleanup._sticker_stats.pop(GID, None)
        urls = [f"u{i}" for i in range(5)]
        sent = 0
        rng = random.Random(7)
        with mock.patch.object(chat.random, "random", side_effect=rng.random), \
                mock.patch.object(chat.random, "randrange", side_effect=rng.randrange):
            for _ in range(n):
                text = "好耶<<STICKER:2>>" if marker else "好耶"
                _r, url = chat._pick_sticker(text, urls, GID)
                sent += url is not None
        return sent / n

    def test_model_picks_capped(self):
        self.assertLessEqual(self.run_many("AI_CHAT_STICKER_PROBABILITY=0.3\n", True), 0.31)

    def test_fallback_counted(self):
        r = self.run_many("AI_CHAT_STICKER_PROBABILITY=0.3\n", False)
        self.assertLessEqual(r, 0.31)
        self.assertGreater(r, 0.15)

    def test_zero_means_none(self):
        self.assertEqual(self.run_many("AI_CHAT_STICKER_PROBABILITY=0\n", True, 50), 0.0)
        self.assertEqual(self.run_many("AI_CHAT_STICKER_PROBABILITY=0\n", False, 50), 0.0)


class PanelTruthTest(unittest.TestCase):
    def panel_keys(self):
        from panel.services import huoren_settings as hs
        keys = {f[0] for _t, _d, fields in hs.GROUPS.values() for f in fields}
        se = (ROOT / "panel/classic/settings_env.py").read_text(encoding="utf-8")
        return keys, se

    def test_every_lift_is_in_panel(self):
        keys, se = self.panel_keys()
        for key in ("AI_CHAT_THREAD_PROBABILITY", "AI_CHAT_LINGER_PROBABILITY", "AI_CHAT_WELCOME_BACK_PROBABILITY",
                    "AI_CHAT_WELCOME_BACK_MISSED_PROBABILITY", "AI_CHAT_CHATTY_QUESTION_PROBABILITY",
                    "AI_CHAT_OWNER_WAKE_PROBABILITY"):
            self.assertIn(key, keys)
        self.assertIn('"AI_CHAT_LIFE_FOLLOWUP_PROBABILITY"', se)
        self.assertNotIn("AI_CHAT_CHATTY_PROBABILITY", keys)

    def test_every_probability_key_is_in_panel(self):
        keys, se = self.panel_keys()
        missing = [v[0] for v in config._SPEC.values()
                   if v[0].endswith("_PROBABILITY") and v[0] not in keys and f'"{v[0]}"' not in se]
        self.assertEqual(missing, [], "机器人读了但面板里看不到的概率设置")

    def test_panel_defaults_match_bot(self):
        from panel.services import huoren_settings as hs
        spec = {v[0]: v[2] for v in config._SPEC.values()}
        for _t, _d, fields in hs.GROUPS.values():
            for key, _label, kind, default, *_rest in fields:
                if key in spec and kind in ("pct", "int", "dec", "bool"):
                    self.assertEqual(float(spec[key]), float(default), key)


class LlmSpendParseTest(unittest.TestCase):
    TZ = 8 * 3600

    def test_by_key_cost_and_amount(self):
        day = int(time.mktime((2026, 10, 22, 0, 0, 0, 0, 0, -1)))
        cost = {"code": 0, "data": {"biz_code": 0, "biz_data": {"data": [
            {"currency": "USD", "series": []},
            {"currency": "CNY", "series": [
                {"api_key": {"name": "bot"}, "model": "deepseek-flash", "buckets": [
                    {"time": day, "cost": "1.25"}, {"time": day + 86400, "cost": "0.75"}]},
                {"api_key": "x", "model": "deepseek-pro", "buckets": [{"time": day, "cost": 0.5}]}]}]}}}
        tz = -time.timezone if not time.localtime().tm_isdst else -time.altzone
        days, models, cur = llm_spend.parse_by_key_cost(cost, tz)
        self.assertEqual(cur, "CNY")
        self.assertAlmostEqual(days["2026-10-22"], 1.75)
        self.assertAlmostEqual(days["2026-10-23"], 0.75)
        self.assertAlmostEqual(models["deepseek-flash"], 2.0)
        amount = {"code": 0, "data": {"biz_code": 0, "biz_data": {"series": [
            {"model": "deepseek-flash", "buckets": [{"time": day, "usage": {
                "REQUEST": "10", "PROMPT_CACHE_HIT_TOKEN": "900", "PROMPT_CACHE_MISS_TOKEN": 100, "RESPONSE_TOKEN": "50"}}]}]}}}
        a = llm_spend.parse_by_key_amount(amount, tz)
        self.assertEqual(a["2026-10-22"], {"requests": 10, "hit": 900, "miss": 100, "out": 50})

    def test_monthly_fallback_and_summary(self):
        cost = [{"currency": "CNY", "total": [{"model": "m", "usage": [{"type": "RESPONSE_TOKEN", "amount": "1.5"}]}],
                 "days": [{"date": "2026-10-20", "data": [{"model": "m", "usage": [
                     {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "0.5"}, {"type": "RESPONSE_TOKEN", "amount": "1.0"}]}]}]}]
        amount = [{"days": [{"date": "2026-10-20", "data": [{"model": "m", "usage": [
            {"type": "REQUEST", "amount": "3"}, {"type": "RESPONSE_TOKEN", "amount": "40"}]}]}]}]
        days, amounts, models = llm_spend.parse_monthly(cost, amount)
        self.assertAlmostEqual(days["2026-10-20"], 1.5)
        self.assertEqual(amounts["2026-10-20"]["requests"], 3)
        self.assertAlmostEqual(models["m"], 1.5)
        s = llm_spend.parse_summary({"code": 0, "data": {"biz_code": 0, "biz_data": {
            "normal_wallets": [{"currency": "CNY", "balance": "22.24"}], "bonus_wallets": [{"currency": "CNY", "balance": 0}],
            "total_costs": [{"currency": "CNY", "amount": "271.32"}]}}})
        self.assertAlmostEqual(s["balance"], 22.24)
        self.assertAlmostEqual(s["costs"]["total_costs"], 271.32)

    def test_expired_token_detected(self):
        class R:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(llm_spend.urllib.request, "urlopen",
                               return_value=R(b'{"code":40003,"msg":"Authorization Failed","data":null}')):
            with self.assertRaises(llm_spend.TokenExpired):
                llm_spend.fetch_web("tok")

    def test_token_forms(self):
        for raw in ('abc123', '"abc123"', 'Bearer abc123', '{"value":"abc123","__version":"0"}'):
            with mock.patch.object(llm_spend.env, "get", return_value=raw):
                self.assertEqual(llm_spend.web_token(), "abc123")

    def test_api_base(self):
        for raw, want in (("https://api.deepseek.com/v1", "https://api.deepseek.com"),
                          ("https://api.deepseek.com/chat/completions", "https://api.deepseek.com"),
                          ("", "https://api.deepseek.com")):
            with mock.patch.object(llm_spend.env, "get", return_value=raw):
                self.assertEqual(llm_spend.api_base(), want)

    def test_local_features_merge(self):
        d = date.today().isoformat()
        for tag, cost in (("chat.chat:_reply", 3000), ("chat.turns:x", 1000), ("chat.planner:plan", 500), ("?", 99)):
            db.usage_add(d, "llmtok:cost_u", tag, "", cost)
            db.usage_add(d, "llmtok:calls", tag, "", 1)
        feats = llm_spend.local_features(d)
        self.assertEqual(feats[0]["name"], "回复")
        self.assertEqual(feats[0]["calls"], 2)
        self.assertAlmostEqual(feats[0]["cost"], 0.004)
        self.assertNotIn("?", [f["name"] for f in feats])
        self.assertGreaterEqual(llm_spend.local_days()[d]["cost"], 0.0045)


if __name__ == "__main__":
    unittest.main()
