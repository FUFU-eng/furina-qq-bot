"""外接模型：别家的模型代替 DeepSeek 跑二代仿真 / Agent，出问题自动先用 DeepSeek。"""
from tests import _env  # noqa: F401  (must be first)

import importlib.util
import json
import os
import time
import unittest
from unittest import mock

from furina.core import agent_engine as engine
from furina.core import db
from furina.core import llm as core_llm
from furina.plugins.chat import kernel
from tests.test_humanlike import BASE_ENV, GID

CUSTOM = ("AI_CHAT_DSH_CUSTOM_ENABLED=true\nAI_CHAT_DSH_CUSTOM_BASE_URL=http://127.0.0.1:18765/\n"
          "AI_CHAT_DSH_CUSTOM_API_KEY=sk-local-test\nAI_CHAT_DSH_CUSTOM_MODEL=big-model\n")


def _worker():
    spec = importlib.util.spec_from_file_location("dsh_worker_t", str(_env.ROOT / "tools" / "dsh_worker.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CustomRouteTest(unittest.TestCase):
    def setUp(self):
        _env.write_env(BASE_ENV + CUSTOM)
        engine.clear_custom_down()
        engine._reach.update(ts=0.0, url="", ok=False)
        kernel._groups.clear()

    def tearDown(self):
        _env.write_env("")
        engine.clear_custom_down()
        kernel._groups.clear()

    def test_settings_and_panel_defaults_match(self):
        _env.write_env("")
        cs = engine.custom_settings()
        self.assertFalse(cs["enabled"])
        self.assertEqual(engine.route(), "deepseek")
        from panel.services import brain_settings
        fields = {f[0]: f for _t, _d, fs in brain_settings.BRAIN for f in fs}
        self.assertEqual(fields["AI_CHAT_DSH_CUSTOM_ENABLED"][3], cs["enabled"])
        self.assertEqual(fields["AI_CHAT_DSH_CUSTOM_CONTEXT_K"][3], cs["context_k"])
        self.assertEqual(fields["AI_CHAT_DSH_CUSTOM_COOLDOWN_MIN"][3], cs["cooldown_min"])
        self.assertEqual(fields["AI_CHAT_DSH_CUSTOM_PRICE_OUT"][3], cs["price_out"])
        self.assertEqual(brain_settings.CUSTOM_PROTOCOL_CHOICES, list(engine.PROTOCOL_NAMES))
        self.assertEqual(engine.PROTOCOL_NAMES[fields["AI_CHAT_DSH_CUSTOM_PROTOCOL"][3]], cs["protocol"])
        self.assertEqual(fields["AI_CHAT_DSH_CUSTOM_API_KEY"][2], "secret")

    def test_route_and_model(self):
        cs = engine.custom_settings()
        self.assertEqual((cs["base_url"], cs["protocol"]), ("http://127.0.0.1:18765", "anthropic-messages"))
        self.assertEqual(engine.route(), "custom")
        self.assertEqual(engine.model(), "big-model")
        _env.write_env(BASE_ENV + CUSTOM + "AI_CHAT_DSH_CUSTOM_PROTOCOL=OpenAI 兼容（Chat Completions）\n")
        self.assertEqual(engine.custom_settings()["protocol"], "openai-completions")
        _env.write_env(BASE_ENV + CUSTOM.replace("big-model", ""))   # 没填模型名 = 不启用
        self.assertEqual(engine.route(), "deepseek")

    def test_cooldown_falls_back_and_recovers(self):
        engine.mark_custom_down("boom sk-local-test")
        self.assertEqual(engine.route(), "deepseek")
        self.assertNotEqual(engine.model(), "big-model")
        self.assertNotIn("sk-local-test", str(db.get(engine.NS, "custom_down_reason", "")))  # 不把密钥记下来
        db.put(engine.NS, "custom_down_until", time.time() - 1)
        self.assertEqual(engine.route(), "custom")

    def test_unreachable_is_detected_before_the_round(self):
        with mock.patch.object(engine, "custom_reachable", return_value=False):
            self.assertEqual(engine.pick_route(), "deepseek")
        self.assertGreater(engine.custom_down_until(), time.time())
        st = engine.custom_status()
        self.assertTrue(st["custom_down_until"])
        self.assertIn("连不上", st["custom_down_reason"])

    def test_fingerprint_and_session_follow_route(self):
        fp_custom = engine.fingerprint()
        sid_custom = engine.session_id("30000001", GID)
        self.assertTrue(sid_custom.endswith("-x"))
        engine.mark_custom_down("x")
        self.assertNotEqual(engine.fingerprint(), fp_custom)       # 换线路 = 换 worker
        self.assertEqual(engine.session_id("30000001", GID), sid_custom[:-2])

    def test_run_forces_effort_off_and_marks_down_on_failure(self):
        seen = {}

        def fake_rpc(path, payload=None, timeout=4.0):
            seen.update(payload)
            return {"ok": False, "error": "模型接口出错：500 boom"}

        with mock.patch.object(engine, "custom_reachable", return_value=True), \
                mock.patch.object(engine, "ensure", return_value={}), mock.patch.object(engine, "rpc", fake_rpc):
            res = engine.run({"session_id": engine.session_id("30000001", GID), "effort": "high", "effort_hi": "max"}, 30)
        self.assertEqual((seen["effort"], seen["effort_hi"]), ("off", "off"))
        self.assertTrue(seen["session_id"].endswith("-x"))
        self.assertEqual(res["route"], "custom")
        self.assertTrue(res.get("custom_down"))
        self.assertEqual(engine.route(), "deepseek")

    def test_busy_is_not_blamed_on_custom(self):
        with mock.patch.object(engine, "custom_reachable", return_value=True), \
                mock.patch.object(engine, "ensure", return_value={}), \
                mock.patch.object(engine, "rpc", return_value={"ok": False, "error": "busy"}):
            res = engine.run({"session_id": "furina-a-0-x", "effort": "off"}, 30)
        self.assertFalse(res.get("custom_down"))
        self.assertEqual(engine.route(), "custom")

    def test_session_id_fixed_when_route_changes_mid_way(self):
        seen = {}
        with mock.patch.object(engine, "custom_reachable", return_value=False), \
                mock.patch.object(engine, "ensure", return_value={}), \
                mock.patch.object(engine, "rpc", side_effect=lambda p, payload=None, timeout=4.0: seen.update(payload) or {"ok": True}):
            res = engine.run({"session_id": "furina-abc-0-x", "effort": "high"}, 30)
        self.assertEqual(seen["session_id"], "furina-abc-0")
        self.assertEqual(seen["effort"], "high")                    # DeepSeek 线路照常按设置思考
        self.assertEqual(res["route"], "deepseek")

    def test_kernel_new_session_on_route_change(self):
        st = kernel._state("30000001", GID)
        with mock.patch.object(engine, "custom_reachable", return_value=True):
            self.assertEqual(kernel._sync_route(st), "custom")
        st.bootstrapped, st.runs_in_session = True, 4
        engine.mark_custom_down("x")
        old_gen = engine.session_id(st.sid, st.gid)
        self.assertEqual(kernel._sync_route(st), "deepseek")
        self.assertFalse(st.bootstrapped)                           # 下一轮带上最近的聊天
        self.assertEqual(st.runs_in_session, 0)
        self.assertNotEqual(engine.session_id(st.sid, st.gid), old_gen)

    def test_usage_priced_with_custom_prices(self):
        _env.write_env(BASE_ENV + CUSTOM + "AI_CHAT_DSH_CUSTOM_PRICE_MISS=2\nAI_CHAT_DSH_CUSTOM_PRICE_OUT=10\n")
        calls = []
        with mock.patch.object(core_llm, "record_usage", side_effect=lambda *a, **k: calls.append((a, k))):
            kernel._record_usage({"route": "custom", "usage": [{"prompt_tokens": 100}]}, "dsh.sim2", "high")
            kernel._record_usage({"usage": [{"prompt_tokens": 100}]}, "dsh.sim2", "high")
        (a1, k1), (a2, k2) = calls
        self.assertEqual(a1[1], "dsh.sim2" + kernel.CUSTOM_TAG)
        self.assertEqual((k1["price"], k1["model"], k1["effort"]), ((0.0, 2.0, 10.0), "big-model", "off"))
        self.assertEqual((a2[1], k2["price"], k2["effort"]), ("dsh.sim2", None, "high"))

    def test_record_usage_price_override(self):
        day_cost = []
        with mock.patch.object(core_llm.db, "usage_add", side_effect=lambda d, kind, tag, u, n: day_cost.append((kind, n))):
            core_llm.record_usage({"prompt_tokens": 1_000_000, "prompt_cache_hit_tokens": 0, "completion_tokens": 0},
                                  "dsh.sim2·外接", model="m", price=(0.0, 3.0, 0.0))
        self.assertIn(("llmtok:cost_u", 3_000_000), day_cost)


class WorkerCustomTest(unittest.TestCase):
    def setUp(self):
        self._old = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old)

    def test_patch_inserts_pi_ai_provider(self):
        w = _worker()
        os.environ.update({"FURINA_DSH_PROVIDER": "custom", "FURINA_DSH_CUSTOM_BASE_URL": "http://127.0.0.1:18765",
                           "FURINA_DSH_MODEL": "big-model", "FURINA_DSH_CUSTOM_PROTOCOL": "anthropic-messages",
                           "FURINA_DSH_CUSTOM_CONTEXT": "200000"})
        with mock.patch.object(w, "DATA", _env.TMP / "agent_t"):
            patch = json.loads(w._patch_file().read_text(encoding="utf-8"))
        ins = [r["insert"][0] for r in patch if "insert" in r and r["insert"][0]["id"] == "llm-custom"]
        self.assertEqual(len(ins), 1)
        self.assertEqual(ins[0]["name"], "@deepseek-ai/dsh-llm-pi-ai")
        prov = ins[0]["config"]["providers"][w.CUSTOM_PROVIDER]
        self.assertEqual((prov["api"], prov["baseURL"], prov["apiKeyEnv"]), ("anthropic-messages", "http://127.0.0.1:18765", "FURINA_CUSTOM_KEY"))
        self.assertEqual(prov["models"][0]["id"], "big-model")
        self.assertNotIn("sk-", json.dumps(patch))                  # 密钥只走环境变量，不写进文件

    def test_no_custom_by_default(self):
        w = _worker()
        os.environ.pop("FURINA_DSH_PROVIDER", None)
        self.assertIsNone(w._custom())
        with mock.patch.object(w, "DATA", _env.TMP / "agent_t2"):
            patch = json.loads(w._patch_file().read_text(encoding="utf-8"))
        self.assertFalse(any("insert" in r and r["insert"][0]["id"] == "llm-custom" for r in patch))

    def test_turn_error_is_reported(self):
        w = _worker()
        ev = [{"type": "assistant/attempt"}, {"type": "turn/end", "data": {"reason": {"kind": "error", "error": {"message": "500 boom"}}}}]
        self.assertEqual(w._turn_error(ev), "500 boom")
        self.assertEqual(w._turn_error([{"type": "turn/end", "data": {"reason": {"kind": "completed"}}}]), "")


if __name__ == "__main__":
    unittest.main()
