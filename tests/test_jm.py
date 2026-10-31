"""JM 下载插件：车号解析、缓存、下载分支、群文件发送与送达核查、权限与限额。

全部离线（假 jmcomic / 假 bot），不联网、不发消息。
"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import os
import tempfile
import types
import unittest
from unittest import mock

from tests.test_chat_flow import BOT, make_event

from furina.core import permissions, task_queue
from furina.plugins.jm import _file_uri, config, service


class ParseAlbumIdTest(unittest.TestCase):
    def test_common_forms(self):
        for text in ("/jm 350234", "jm 350234", "JM350234", "/jm350234", "/JM 350234", "  /jm   350234  "):
            with self.subTest(text=text):
                self.assertEqual(config.parse_album_id(text), "350234")

    def test_rejects_non_album(self):
        for text in ("", "jm", "/jm", "/jm 你好", "/jm abc", "jmd 123", "jmabc 123",
                     "350234", "看看这个 /jm 350234", "/jm 350abc"):
            with self.subTest(text=text):
                self.assertEqual(config.parse_album_id(text), "")

    def test_head_detection(self):
        self.assertTrue(config.has_jm_head("/jm 1"))
        self.assertTrue(config.has_jm_head("jm1"))
        self.assertFalse(config.has_jm_head("jmd 1"))
        self.assertFalse(config.has_jm_head("我要 /jm 1"))


class FileUriTest(unittest.TestCase):
    def test_windows_path_uri(self):
        uri = _file_uri(r"D:\furina-qq-bot\data\jm\350234\pdf\350234.pdf")
        self.assertTrue(uri.startswith("file:///D:/furina-qq-bot/data/jm/"))
        self.assertNotIn("\\", uri)

    def test_chinese_path_is_quoted(self):
        uri = _file_uri(r"E:\下载\350234.pdf")
        self.assertIn("%", uri)


class ResponseOkTest(unittest.TestCase):
    """NapCat 上传群文件失败时返回 status=failed + retcode=200，不能当成成功。"""

    def test_ok_response(self):
        from furina.plugins.jm import _response_ok

        self.assertTrue(_response_ok({"status": "ok", "retcode": 0, "data": {"file_id": "x"}}))

    def test_napcat_timeout_response_is_failure(self):
        from furina.plugins.jm import _response_ok

        self.assertFalse(_response_ok({
            "status": "failed", "retcode": 200,
            "message": "Timeout: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg",
        }))

    def test_none_and_non_dict(self):
        from furina.plugins.jm import _response_ok

        self.assertFalse(_response_ok(None))
        self.assertTrue(_response_ok("whatever"))

    def test_unwrapped_snowluma_data_is_success(self):
        """NoneBot 只返回 data：SnowLuma 上传成功是 {"file_id": ...}，失败会直接抛 ActionFailed。"""
        from furina.plugins.jm import _response_ok

        self.assertTrue(_response_ok({"file_id": "/abc-123"}))
        self.assertTrue(_response_ok({}))


class HistoryMatcherTest(unittest.TestCase):
    """核查群文件是否送达：要同时兼容适配器解包后的结构和 NapCat 原始信封。"""

    FILE_MSG = {
        "time": 1,
        "sender": {"user_id": int(BOT)},
        "message": [{"type": "file", "data": {
            "file": "350234.pdf", "file_id": "/dc7d0f02-x", "file_size": "33483183"}}],
    }

    def test_envelope_shape(self):
        from furina.plugins.jm import _history_has_file, _history_messages

        hist = {"status": "ok", "retcode": 0, "data": {"messages": [self.FILE_MSG]}}
        self.assertEqual(len(_history_messages(hist)), 1)
        self.assertTrue(_history_has_file(hist, "350234.pdf"))
        self.assertFalse(_history_has_file(hist, "999999.pdf"))

    def test_unwrapped_shape(self):
        from furina.plugins.jm import _history_has_file

        self.assertTrue(_history_has_file({"messages": [self.FILE_MSG]}, "350234.pdf"))

    def test_empty_and_garbage(self):
        from furina.plugins.jm import _history_has_file, _history_messages

        self.assertEqual(_history_messages(None), [])
        self.assertEqual(_history_messages({"data": {}}), [])
        self.assertFalse(_history_has_file(None, "350234.pdf"))
        self.assertFalse(_history_has_file({"data": {"messages": [{"message": []}]}}, "350234.pdf"))

    def test_text_message_is_not_a_file(self):
        from furina.plugins.jm import _history_has_file

        hist = {"messages": [{"message": [{"type": "text", "data": {"text": "350234.pdf"}}]}]}
        self.assertFalse(_history_has_file(hist, "350234.pdf"))


class CacheTest(unittest.TestCase):
    """缓存结构、命中判定与 LRU 清理（缓存目录由 .env 的 JM_CACHE_DIR 指定）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _env.write_env(f"BOT_QQ={BOT}\nJM_CACHE_DIR={self.tmp.name}\nJM_CACHE_MAX_MB=0\n")

    def tearDown(self):
        _env.write_env(f"BOT_QQ={BOT}\n")
        self.tmp.cleanup()

    def _make_album(self, album_id, size_bytes):
        p = service.pdf_path(album_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size_bytes)
        return p

    def test_pdf_ready_requires_real_pdf(self):
        self.assertFalse(service.pdf_ready("350234"))
        self._make_album("350234", 10)  # 太小，视为半截文件
        self.assertFalse(service.pdf_ready("350234"))
        self._make_album("350234", 3000)
        self.assertTrue(service.pdf_ready("350234"))

    def test_count_images_ignores_pdf_dir(self):
        other = service.album_dir("555") / "img" / "第1話" / "001.jpg"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_bytes(b"x" * 100)
        self.assertEqual(service.count_images("555"), 1)

    def test_cached_albums_and_summary(self):
        self._make_album("111", 3000)
        rows = service.cached_albums()
        self.assertEqual([r["album_id"] for r in rows], ["111"])
        self.assertTrue(rows[0]["has_pdf"])
        self.assertIn("1 本", service.cache_summary())

    def test_clear_album_and_all(self):
        self._make_album("333", 3000)
        self._make_album("444", 3000)
        self.assertTrue(service.clear_album("333"))
        self.assertFalse(service.clear_album("333"))
        self.assertEqual(service.clear_all(), 1)


class DownloadFlowTest(unittest.TestCase):
    """用假的 jmcomic 模块验证 download_pdf 的成功 / 失败 / 缓存分支。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _env.write_env(f"BOT_QQ={BOT}\nJM_CACHE_DIR={self.tmp.name}\nJM_CACHE_MAX_MB=0\nJM_MAX_IMAGES=0\n")
        self.calls = []

    def tearDown(self):
        _env.write_env(f"BOT_QQ={BOT}\n")
        self.tmp.cleanup()

    def _fake_jm(self, behavior):
        fake = types.SimpleNamespace()
        calls = self.calls

        class _Feature:
            @staticmethod
            def export_pdf(**kwargs):
                return ("export_pdf", kwargs)

        def download_album(album_id, option, extra=None):
            calls.append(album_id)
            behavior(album_id, option, extra)
            return types.SimpleNamespace(detail=types.SimpleNamespace(name="测试本子"))

        fake.Feature = _Feature
        fake.download_album = download_album
        fake.JmOption = types.SimpleNamespace(default=lambda: mock.MagicMock())
        fake.JmModuleConfig = types.SimpleNamespace(EXECUTOR_LOG=None)
        return fake

    def test_success_writes_pdf(self):
        out = service.pdf_path("999")

        def behavior(album_id, option, extra):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 4096)

        with mock.patch.object(service, "_jm", return_value=self._fake_jm(behavior)):
            path, err, title = asyncio.run(service.download_pdf("999"))
        self.assertEqual(err, "")
        self.assertEqual(title, "测试本子")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(self.calls, ["999"])

    def test_download_exception_reports_error(self):
        def behavior(album_id, option, extra):
            raise RuntimeError("boom")

        with mock.patch.object(service, "_jm", return_value=self._fake_jm(behavior)):
            path, err, _ = asyncio.run(service.download_pdf("998"))
        self.assertEqual((path, err), ("", "error"))

    def test_network_exception_reports_network(self):
        def behavior(album_id, option, extra):
            raise RuntimeError("Failed to connect to proxy 127.0.0.1:7890")

        with mock.patch.object(service, "_jm", return_value=self._fake_jm(behavior)):
            path, err, _ = asyncio.run(service.download_pdf("993"))
        self.assertEqual((path, err), ("", "network"))

    def test_missing_album_reports_not_found(self):
        def behavior(album_id, option, extra):
            raise RuntimeError("404 Not Found: album 999999 does not exist")

        with mock.patch.object(service, "_jm", return_value=self._fake_jm(behavior)):
            path, err, _ = asyncio.run(service.download_pdf("999999"))
        self.assertEqual((path, err), ("", "not_found"))

    def test_no_pdf_reports_error(self):
        with mock.patch.object(service, "_jm", return_value=self._fake_jm(lambda *a: None)):
            path, err, _ = asyncio.run(service.download_pdf("997"))
        self.assertEqual((path, err), ("", "error"))

    def test_cached_pdf_skips_download(self):
        out = service.pdf_path("996")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x" * 4096)
        with mock.patch.object(service, "_jm", return_value=self._fake_jm(lambda *a: None)):
            path, err, _ = asyncio.run(service.download_pdf("996"))
        self.assertEqual(err, "")
        self.assertEqual(self.calls, [])
        self.assertTrue(path.endswith("996.pdf"))

    def test_too_many_pages_is_rejected(self):
        class _Photo:
            def __len__(self):
                return 60

        class _Detail:
            def __iter__(self):
                return iter([_Photo() for _ in range(10)])

        option = mock.MagicMock()
        option.new_jm_client.return_value.get_album_detail.return_value = _Detail()
        _env.write_env(f"BOT_QQ={BOT}\nJM_CACHE_DIR={self.tmp.name}\nJM_MAX_IMAGES=100\n")
        with mock.patch.object(service, "_jm", return_value=self._fake_jm(lambda *a: None)), \
                mock.patch.object(service, "_new_option", return_value=option):
            path, err, _ = asyncio.run(service.download_pdf("995"))
        self.assertEqual((path, err), ("", "too_large"))
        self.assertEqual(self.calls, [])

    def test_big_album_is_not_rejected_by_cache_limit(self):
        """缓存上限只用来清理旧缓存，不该拿来拒绝下载。"""
        class _Photo:
            def __len__(self):
                return 60

        class _Detail:
            def __iter__(self):
                return iter([_Photo() for _ in range(10)])

        option = mock.MagicMock()
        option.new_jm_client.return_value.get_album_detail.return_value = _Detail()
        out = service.pdf_path("993")

        def behavior(album_id, opt, extra):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 4096)

        _env.write_env(
            f"BOT_QQ={BOT}\nJM_CACHE_DIR={self.tmp.name}\nJM_MAX_IMAGES=0\nJM_CACHE_MAX_MB=100\n"
        )
        with mock.patch.object(service, "_jm", return_value=self._fake_jm(behavior)), \
                mock.patch.object(service, "_new_option", return_value=option):
            path, err, _ = asyncio.run(service.download_pdf("993"))
        self.assertEqual(err, "")
        self.assertTrue(path.endswith("993.pdf"))
        self.assertEqual(self.calls, ["993"])


class SendOnceTest(unittest.TestCase):
    """上传群文件只能发一次：NapCat 的「超时」往往是文件已经发出去了，重发会变成两份。"""

    def _bot(self, effects):
        bot = mock.MagicMock()
        bot.call_api = mock.AsyncMock(side_effect=effects)
        return bot

    def test_timeout_is_not_retried(self):
        from nonebot.adapters.onebot.v11.exception import ActionFailed

        from furina.plugins.jm import _send_group_file

        err = ActionFailed(status="failed", retcode=1200, data=None,
                           message="Timeout: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg")
        bot = self._bot([err])
        with self.assertRaises(ActionFailed):
            asyncio.run(_send_group_file(bot, 1, r"E:\a\350234.pdf", "350234.pdf"))
        self.assertEqual(bot.call_api.await_count, 1)  # 没有第二次

    def test_success_sends_once(self):
        from furina.plugins.jm import _send_group_file

        bot = self._bot([{"status": "ok", "retcode": 0}])
        resp = asyncio.run(_send_group_file(bot, 1, r"E:\a\350234.pdf", "350234.pdf"))
        self.assertTrue(resp["status"] == "ok")
        self.assertEqual(bot.call_api.await_count, 1)
        self.assertTrue(bot.call_api.await_args.kwargs["file"].startswith("file:///"))

    def test_uri_rejected_falls_back_to_absolute_path(self):
        from nonebot.adapters.onebot.v11.exception import ActionFailed

        from furina.plugins.jm import _send_group_file

        err = ActionFailed(status="failed", retcode=1200, data=None,
                           message="识别URL失败, uri= E:\\a\\350234.pdf")
        bot = self._bot([err, {"status": "ok", "retcode": 0}])
        resp = asyncio.run(_send_group_file(bot, 1, r"E:\a\350234.pdf", "350234.pdf"))
        self.assertTrue(resp["status"] == "ok")
        self.assertEqual(bot.call_api.await_count, 2)  # 只有「路径没被接受」才重发


class HandlerFlowTest(unittest.TestCase):
    """端到端（假 bot / 假下载）：/jm 的处理流程、提示语与权限门控。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _env.write_env(
            f"BOT_QQ={BOT}\nJM_ENABLED=true\nJM_CACHE_DIR={self.tmp.name}\n"
            "JM_CACHE_MAX_MB=0\nJM_DAILY_USER_LIMIT=0\nJM_DAILY_GROUP_LIMIT=0\n"
        )
        permissions.set_feature_enabled("100000004", "jm", True)

    def tearDown(self):
        _env.write_env(f"BOT_QQ={BOT}\n")
        self.tmp.cleanup()

    def _make_album(self, album_id="350234"):
        out = service.pdf_path(album_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x" * 3000)
        return out

    def _run(self, text, expect_finish=True):
        return asyncio.run(self._run_async(text, expect_finish))

    async def _run_async(self, text, expect_finish=True):
        import nonebot

        from furina.plugins.jm import handle_jm

        event = make_event(text, mid=7)

        bot = mock.MagicMock()
        bot.self_id = BOT
        bot.call_api = mock.AsyncMock(return_value={"status": "ok", "retcode": 0, "data": {"file_id": "f"}})

        # 后台任务（上传/核查）收进队列，处理结束后统一 await，断言不依赖调度时机
        pending = []
        real_create_task = asyncio.create_task

        def capture_task(coro, *a, **kw):
            pending.append(real_create_task(coro))
            return pending[-1]

        sent = []
        with mock.patch("furina.plugins.jm.bot_for", return_value=bot), \
                mock.patch("asyncio.create_task", side_effect=capture_task), \
                mock.patch("furina.plugins.jm.jm_matcher.send",
                           side_effect=lambda msg, **kw: sent.append(str(msg)) or None):
            if expect_finish:
                with self.assertRaises(nonebot.exception.FinishedException):
                    await handle_jm(event)
            else:
                await handle_jm(event)
            for task in list(pending):
                await task
        return sent, bot

    def test_cached_album_sends_file_then_at(self):
        self._make_album()
        with mock.patch("furina.plugins.jm.service.download_pdf") as dl:
            sent, bot = self._run("/jm 350234", expect_finish=False)
            dl.assert_not_called()
        self.assertTrue(bot.call_api.called)
        api, data = bot.call_api.call_args.args[0], bot.call_api.call_args.kwargs
        self.assertEqual(api, "upload_group_file")
        self.assertEqual(data["name"], "350234.pdf")
        self.assertTrue(data["file"].startswith("file:///"))
        # 命中缓存：先回固定回应，文件发出去后艾特
        self.assertEqual(len(sent), 2, sent)
        self.assertEqual(sent[0], "收到神秘号码，正在进入天堂下载")
        self.assertIn("20001", sent[1])

    def test_usage_hint_when_album_missing(self):
        sent, bot = self._run("/jm")
        self.assertFalse(bot.call_api.called)
        self.assertTrue(any("用法" in s for s in sent))

    def test_download_then_upload_without_chatter(self):
        async def fake_download(album_id, on_progress=None):
            out = service.pdf_path(album_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 3000)
            return str(out), "", "测试本子"

        with mock.patch("furina.plugins.jm.service.download_pdf", side_effect=fake_download):
            sent, bot = self._run("/jm 350234")
        self.assertTrue(bot.call_api.called)
        joined = " | ".join(sent)
        # 先回固定回应；PDF 发出去之后才艾特发号码的人（艾特排在文件后面）
        self.assertTrue(sent and sent[0] == "收到神秘号码，正在进入天堂下载", sent)
        at_index = next((i for i, s in enumerate(sent) if "20001" in s and "at" in s), None)
        self.assertIsNotNone(at_index, sent)
        self.assertEqual(at_index, len(sent) - 1, sent)   # 艾特是最后一条
        self.assertTrue(bot.call_api.called)              # 文件在艾特之前就已经发出去了
        for noise in ("下好了", "正在上传", "缓存"):
            self.assertNotIn(noise, joined)

    def test_not_found_reports_error(self):
        async def fake_download(album_id, on_progress=None):
            return "", "not_found", ""

        with mock.patch("furina.plugins.jm.service.download_pdf", side_effect=fake_download):
            sent, bot = self._run("/jm 999999", expect_finish=False)
        self.assertFalse(bot.call_api.called)
        self.assertTrue(any("找不到" in s for s in sent))

    def test_upload_success_deletes_cache(self):
        """发出去之后必须把本子缓存删掉，不在磁盘上累积。"""
        async def fake_download(album_id, on_progress=None):
            out = service.pdf_path(album_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 3000)
            return str(out), "", "测试本子"

        with mock.patch("furina.plugins.jm.service.download_pdf", side_effect=fake_download), \
                mock.patch("furina.plugins.jm._send_group_file",
                           new=mock.AsyncMock(return_value={"status": "ok", "retcode": 0, "data": {}})):
            self._run("/jm 350234")
        self.assertFalse(service.album_dir("350234").exists())
        self.assertTrue(service.cache_summary().endswith("占用 0MB") or "空" in service.cache_summary())

    def test_cached_album_also_deleted_after_send(self):
        """命中缓存发出去之后同样删掉（缓存只是残留，不留着）。"""
        self._make_album("350234")
        with mock.patch("furina.plugins.jm._send_group_file",
                        new=mock.AsyncMock(return_value={"status": "ok", "retcode": 0, "data": {}})):
            self._run("/jm 350234", expect_finish=False)
        self.assertFalse(service.album_dir("350234").exists())

    def test_upload_failure_keeps_cache(self):
        """上传报错时先留着（可能要核查/重发），不能提前删。"""
        async def fake_download(album_id, on_progress=None):
            out = service.pdf_path(album_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 3000)
            return str(out), "", ""

        with mock.patch("furina.plugins.jm.service.download_pdf", side_effect=fake_download), \
                mock.patch("furina.plugins.jm._send_group_file",
                           new=mock.AsyncMock(return_value={"status": "failed", "retcode": 200})), \
                mock.patch("furina.plugins.jm._verify_file_upload", new=mock.AsyncMock()):
            self._run("/jm 350234")
        self.assertTrue(service.pdf_path("350234").exists())

    def test_queued_request_reports_position(self):
        async def fake_download(album_id, on_progress=None):
            await asyncio.sleep(0.05)
            out = service.pdf_path(album_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 3000)
            return str(out), "", ""

        ticket = task_queue.enqueue_jm()
        try:
            with mock.patch("furina.plugins.jm.service.download_pdf", side_effect=fake_download):
                sent, _bot = self._run("/jm 350234")
            self.assertTrue(any("排在第" in s for s in sent), sent)
        finally:
            ticket.cancel()

    def test_group_permission_off_is_silent(self):
        from furina.plugins.jm import handle_jm

        permissions.set_feature_enabled("100000004", "jm", False)
        event = make_event("/jm 350234", mid=8)
        with mock.patch("furina.plugins.jm.jm_matcher.send") as send:
            asyncio.run(handle_jm(event))
        self.assertFalse(send.called)

    def test_master_switch_off_is_silent(self):
        from furina.plugins.jm import handle_jm

        _env.write_env(f"BOT_QQ={BOT}\nJM_ENABLED=false\nJM_CACHE_DIR={self.tmp.name}\n")
        event = make_event("/jm 350234", mid=9)
        with mock.patch("furina.plugins.jm.jm_matcher.send") as send:
            asyncio.run(handle_jm(event))
        self.assertFalse(send.called)

    def test_daily_user_limit(self):
        async def fake_download(album_id, on_progress=None):
            out = service.pdf_path(album_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 3000)
            return str(out), "", ""

        _env.write_env(
            f"BOT_QQ={BOT}\nJM_CACHE_DIR={self.tmp.name}\nJM_CACHE_MAX_MB=0\n"
            "JM_DAILY_USER_LIMIT=1\nJM_DAILY_GROUP_LIMIT=0\n"
        )
        with mock.patch("furina.plugins.jm.service.download_pdf", side_effect=fake_download):
            self._run("/jm 111111")           # 第一次成功
            sent, bot = self._run("/jm 222222")  # 超限，finish 会抛 FinishedException
        self.assertTrue(any("今天你已经点过" in s for s in sent), sent)
        self.assertFalse(bot.call_api.called)


class PanelSyncTest(unittest.TestCase):
    """面板与核心的功能清单/设置项必须同步，漏改会直接测失败。"""

    # 注意：tests/_env.py 把 FURINA_ROOT 指向了临时目录，这里要的是真实仓库根目录
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _text(self, rel: str) -> str:
        import pathlib

        return pathlib.Path(self.ROOT, rel).read_text(encoding="utf-8")

    def test_core_feature_registered(self):
        self.assertIn("jm", permissions.FEATURES)
        self.assertTrue(permissions.allowed("999", "jm"))
        self.assertIn("enqueue_jm", self._text("furina/core/task_queue.py"))

    def test_panel_lists_feature(self):
        app_js = self._text("panel/web/app.js")
        self.assertIn("JM 下载", app_js)          # 功能清单条目
        self.assertIn("pages.settings", app_js)
        self.assertIn('label: "JM 下载"', app_js)  # 设置页里的 JM 标签页

    def test_panel_api_and_service(self):
        self.assertIn("/api/jm", self._text("panel/server.py"))
        data_py = self._text("panel/services/data.py")
        self.assertIn("JM_CACHE_DIR", data_py)
        self.assertIn('("jm", "JM 下载"', data_py)  # 统计页里的功能分布也要有它

    def test_env_keys_documented(self):
        env_text = self._text(".env.example")
        for key in ("JM_ENABLED", "JM_CACHE_DIR", "JM_PROXY", "JM_TIMEOUT", "JM_DAILY_USER_LIMIT"):
            self.assertIn(key, env_text)
        self.assertIn("jmcomic", self._text("requirements.txt"))


if __name__ == "__main__":
    unittest.main()
