"""QQ 掉线自动恢复：先快速登录（免扫码），不行再出二维码；自动恢复有延迟和冷却。"""
from tests import _env  # noqa: F401  (must be first)

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from furina.services import napcat_recover as nr


class NapcatRecoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        (Path(self.tmp) / "cache").mkdir()
        _env.write_env(f"NAPCAT_DIR={self.tmp}\n")
        try:
            nr.STATE_PATH.unlink()
        except OSError:
            pass
        self.calls = []
        p = [mock.patch.object(nr, "stop_napcat", side_effect=lambda: self.calls.append("stop")),
             mock.patch.object(nr, "start_napcat", side_effect=lambda qq: self.calls.append(("start", qq)) or True),
             mock.patch.object(nr, "_wait", side_effect=self._fake_wait)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.online_after_quick = True

    def _fake_wait(self, pred, timeout, step=3):
        if self.calls and self.calls[-1] == ("start", "123"):
            return self.online_after_quick
        qr = Path(self.tmp) / "cache" / "qrcode.png"
        qr.write_bytes(b"x")
        os.utime(qr, (time.time() + 5, time.time() + 5))
        return pred()

    def test_quick_login_first(self):
        events = []
        self.assertEqual(nr.relogin("123", on_event=lambda lv, m: events.append(lv)), "quick_ok")
        self.assertEqual(self.calls, ["stop", ("start", "123")])
        self.assertIn("success", events)
        self.assertEqual(nr.load_state()["last_result"], "quick_ok")

    def test_falls_back_to_qrcode(self):
        self.online_after_quick = False
        self.assertEqual(nr.relogin("123"), "qr")
        self.assertEqual(self.calls, ["stop", ("start", "123"), "stop", ("start", None)])

    def test_auto_has_cooldown_and_switch(self):
        with mock.patch.object(nr.threading, "Thread") as th:
            self.assertTrue(nr.schedule_auto("123", "下线通知", "登录已失效"))
            th.return_value.start.assert_called_once()
            self.assertFalse(nr.schedule_auto("123"))  # 冷却中：不再自动重启
        _env.write_env(f"NAPCAT_DIR={self.tmp}\nNAPCAT_AUTO_RECOVER=false\n")
        nr.save_state(last_auto=0)
        self.assertFalse(nr.schedule_auto("123"))

    def test_auto_skips_when_already_back(self):
        with mock.patch.object(nr, "status", return_value="online"), mock.patch.object(nr.time, "sleep"), \
                mock.patch.object(nr, "relogin") as rl, \
                mock.patch.object(nr.threading, "Thread", side_effect=lambda target, **kw: mock.Mock(start=target)):
            nr.schedule_auto("123")
        rl.assert_not_called()

    def test_auto_marks_owner_notice_on_quick_ok(self):
        with mock.patch.object(nr, "status", return_value="down"), mock.patch.object(nr.time, "sleep"), \
                mock.patch.object(nr.threading, "Thread", side_effect=lambda target, **kw: mock.Mock(start=target)):
            nr.schedule_auto("123")
        self.assertEqual(nr.load_state()["last_result"], "quick_ok")
        self.assertFalse(nr.recovering())
        self.assertTrue(nr.pop_owner_notice())

    def test_owner_notice_once(self):
        nr.save_state(notify_owner=time.time())
        self.assertTrue(nr.pop_owner_notice())
        self.assertFalse(nr.pop_owner_notice())

    def _sync_thread(self):
        return mock.patch.object(nr.threading, "Thread", side_effect=lambda target, **kw: mock.Mock(start=target))

    def test_resume_after_bot_restart(self):
        # 掉线后安排了自动恢复，但机器人进程被重启（线程没了）：重新连上时接着做
        with mock.patch.object(nr.threading, "Thread"):
            self.assertTrue(nr.schedule_auto("123", "下线通知", "登录已失效"))
        self.assertEqual(nr.load_state()["pending_qq"], "123")
        self.assertFalse(nr.resume_pending("999"))  # 别的号连上：不管
        with mock.patch.object(nr, "status", return_value="offline"), mock.patch.object(nr.time, "sleep"), \
                mock.patch.object(nr, "relogin") as rl, self._sync_thread():
            self.assertTrue(nr.resume_pending("123"))
        rl.assert_called_once()
        self.assertFalse(nr.resume_pending("123"))  # 只接一次

    def test_manual_relogin_clears_pending(self):
        nr.save_state(pending_qq="123", pending_at=time.time())
        nr.relogin("123")
        self.assertFalse(nr.resume_pending("123"))

    def test_auto_does_not_kill_fresh_qrcode(self):
        qr = Path(self.tmp) / "cache" / "qrcode.png"
        qr.write_bytes(b"x")
        with mock.patch.object(nr, "status", return_value="down"), mock.patch.object(nr.time, "sleep"), \
                mock.patch.object(nr, "relogin") as rl, self._sync_thread():
            nr.schedule_auto("123", "ws_lost", "断开")
        rl.assert_not_called()
        os.utime(qr, (time.time() - 3600, time.time() - 3600))
        nr.save_state(last_auto=0)
        with mock.patch.object(nr, "status", return_value="down"), mock.patch.object(nr.time, "sleep"), \
                mock.patch.object(nr, "relogin") as rl, self._sync_thread():
            nr.schedule_auto("123", "ws_lost", "断开")
        rl.assert_called_once()

    def test_panel_defaults_match(self):
        try:
            from panel.classic import settings  # noqa: F401  （面板里也是先导入 settings，再由它导入 settings_env）
            from panel.classic import settings_env as se
        except ImportError as e:  # 没装 PySide6 的环境
            self.skipTest(str(e))
        fields = {f[0]: f for _t, _d, fs in se.LOGIN for f in fs}
        _env.write_env("")
        self.assertEqual(fields["NAPCAT_AUTO_RECOVER"][3], nr.enabled())
        self.assertEqual(fields["NAPCAT_RECOVER_DELAY"][3], nr.delay_seconds())
        self.assertEqual(fields["NAPCAT_RECOVER_COOLDOWN_MIN"][3], nr.cooldown_minutes())


if __name__ == "__main__":
    unittest.main()
