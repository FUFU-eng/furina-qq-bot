"""``@机器人 pid1234567`` 按 ID 取图：解析、触发、R18 拦截、权限、发送流程（全离线）。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import pathlib
import tempfile
import unittest
from unittest import mock

from tests.test_chat_flow import BOT, make_event

from furina.core import permissions
from furina.plugins import pixiv as pixiv_mod
from furina.plugins.pixiv import pid_lookup

R18_TEXT = "这张图有露点，小孩子不能看哦~"


class FakeClient:
    """只提供 json()：detail 查询走它。"""

    def __init__(self, body):
        self.body = body
        self.calls = []

    async def json(self, path, params=None):
        self.calls.append((path, params))
        return {"body": self.body}


class FakeStore:
    def __init__(self):
        self.rows = []
        self.cleaned = 0

    def claim(self, key, status="started", detail=""):
        self.rows.append(("claim", key, status, detail))

    def finish(self, key, status, detail):
        self.rows.append(("finish", key, status, detail))

    def cleanup(self, *a, **kw):
        self.cleaned += 1

    def statuses(self):
        return [r[2] for r in self.rows if r[0] == "finish"]


def event_with(text_after_at: str, group_id: int = 100000004, uid: int = 20001):
    """构造一条「@机器人 + 文本」的群消息。"""
    return make_event(f"[CQ:at,qq={BOT}] {text_after_at}", user_id=uid, group_id=group_id)


class ParseTest(unittest.TestCase):
    def test_accepts_common_forms(self):
        for text in ("pid1234567", "PID1234567", "pid 1234567", "pid:1234567", "pid：1234567",
                     "pid#1234567", "看一下 pid9876543 这个"):
            with self.subTest(text=text):
                self.assertEqual(len(pid_lookup._pids_in(text)), 1, text)

    def test_batch_separators(self):
        """一条消息多个 pid：换行 / 逗号 / 空格 / 顿号都能分开，且保持顺序、去重。"""
        text = "pid112267675\npid145761009, pid148047075 pid145804393、pid135522409\npid112267675"
        self.assertEqual(
            pid_lookup._pids_in(text),
            ["112267675", "145761009", "148047075", "145804393", "135522409"],
        )

    def test_batch_keeps_first_occurrence_order(self):
        self.assertEqual(pid_lookup._pids_in("pid2222222 pid1111111 pid2222222"), ["2222222", "1111111"])

    def test_rejects_non_pid(self):
        for text in ("", "1234567", "pixiv1234567", "pid", "pidabc", "pid12"):
            with self.subTest(text=text):
                self.assertEqual(pid_lookup._pids_in(text), [], text)

    def test_rule_needs_at_bot_and_pid(self):
        self.assertTrue(pid_lookup._rule(event_with("pid1234567")))
        self.assertTrue(pid_lookup._rule(event_with("pid1111111\npid2222222")))
        self.assertFalse(pid_lookup._rule(make_event("pid1234567")))                    # 没艾特
        self.assertFalse(pid_lookup._rule(event_with("没有号码")))                       # 没 pid
        self.assertFalse(pid_lookup._rule(event_with("pid1234567", uid=int(BOT))))      # 机器人自己


class BatchTest(unittest.TestCase):
    """批量：全部下载攒着，最后合并成一条转发发出去。"""

    def setUp(self):
        permissions.set_feature_enabled("100000004", "pixiv_id", True)
        self._cfg = mock.patch.object(pid_lookup, "_config", return_value={
            "enabled": True, "search_cooldown_seconds": 0, "interval_seconds": 0,
            "cache_mb": 64, "aliases": {},
        })
        self._cfg.start()
        self.addCleanup(self._cfg.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, pids, meta=None, pages=None):
        """meta: pid -> 作品体（None 表示查到但没有 id）；pages: pid -> 页数。"""
        meta = meta or {}
        pages = pages or {}
        client = FakeClient(None)
        store = FakeStore()
        sent = []
        bot = mock.MagicMock()
        bot.self_id = BOT

        async def fake_send_group_msg(**kw):
            sent.append(kw.get("message"))
            return {"message_id": 1}

        bot.send_group_msg = fake_send_group_msg
        send_mock = mock.AsyncMock()
        files = self.tmp.name

        async def fake_json(path, params=None):
            pid = path.rsplit("/", 1)[-1]
            return {"body": meta.get(pid)}

        client.json = fake_json

        async def fake_send_work(_client, _store, work, _send, _cfg, **kw):
            pid = work["id"]
            count = pages.get(pid, 1)
            out = []
            for i in range(count):
                p = pathlib.Path(files, f"{pid}_p{i}.jpg")
                p.write_bytes(b"x" * 2048)
                out.append(p)
            return out if kw.get("keep_paths") else count

        send_work_mock = mock.AsyncMock(side_effect=fake_send_work)

        with mock.patch.object(pixiv_mod, "_client", client), \
                mock.patch.object(pixiv_mod, "_store", store), \
                mock.patch.object(pid_lookup, "send_work", new=send_work_mock), \
                mock.patch.object(pid_lookup, "_sender", return_value=send_mock):
            asyncio.run(pid_lookup._lookup_batch(bot, 100000004, 20001, list(pids)))
        return sent, send_mock, send_work_mock, store

    def test_batch_sends_one_merged_forward(self):
        pids = ["1111111", "2222222", "3333333"]
        meta = {p: {"id": p, "xRestrict": 0, "illustType": 0, "userName": f"作者{p[-1]}", "pageCount": 1}
                for p in pids}
        sent, send_mock, sw, store = self._run(pids, meta=meta)

        self.assertEqual(sw.await_count, 3, "三部都该下载")
        send_mock.assert_awaited_once()                      # 只发一条（中途不逐部发）
        args, kwargs = send_mock.await_args
        self.assertTrue(kwargs.get("forward"), kwargs)        # 合并转发
        self.assertEqual(len(args[1]), 3)                     # 3 张图
        self.assertEqual(len(kwargs.get("node_captions")), 3)
        self.assertIn("1111111", kwargs["node_captions"][0])  # 每部第一张带署名
        self.assertEqual(sent, [])                            # 没有逐条提示
        self.assertEqual(store.statuses(), ["completed"])

    def test_batch_multi_page_credits_and_blanks(self):
        pids = ["1111111", "2222222"]
        meta = {p: {"id": p, "xRestrict": 0, "illustType": 0, "userName": "某作者", "pageCount": 3}
                for p in pids}
        _sent, send_mock, _sw, _store = self._run(pids, meta=meta, pages={p: 3 for p in pids})
        args, kwargs = send_mock.await_args
        self.assertEqual(len(args[1]), 6)                     # 2 部 × 3 页
        caps = kwargs["node_captions"]
        self.assertIn("1111111", caps[0])
        self.assertEqual(caps[1], "")                         # 同一部的后续页留空
        self.assertEqual(caps[2], "")
        self.assertIn("2222222", caps[3])

    def test_batch_skips_r18_and_reports_once(self):
        meta = {
            "1111111": {"id": "1111111", "xRestrict": 0, "illustType": 0, "userName": "A", "pageCount": 1},
            "2222222": {"id": "2222222", "xRestrict": 1, "illustType": 0, "userName": "B", "pageCount": 1},
        }
        sent, send_mock, sw, _store = self._run(["1111111", "2222222"], meta=meta)
        self.assertEqual(sw.await_count, 1, "R18 那部不该下载")
        send_mock.assert_awaited_once()
        self.assertEqual(len(send_mock.await_args.args[1]), 1)
        self.assertEqual(len(sent), 1)                        # 只汇总说一句
        self.assertIn(pid_lookup.R18_TEXT, str(sent[0]))

    def test_batch_all_failed_reports(self):
        meta = {"1111111": None, "2222222": None}
        sent, send_mock, _sw, store = self._run(["1111111", "2222222"], meta=meta)
        send_mock.assert_not_awaited()                        # 一张都没有，不发转发
        self.assertTrue(sent and "没取到" in str(sent[-1]), sent)
        self.assertEqual(store.statuses().count("failed"), 3)  # 两次查询失败 + 整批失败


class DispatchTest(unittest.TestCase):
    """单个 pid 走单发；多个 pid 走合并转发。"""

    def setUp(self):
        self._cfg = mock.patch.object(pid_lookup, "_config", return_value={
            "enabled": True, "search_cooldown_seconds": 0, "interval_seconds": 0,
            "cache_mb": 64, "aliases": {},
        })
        self._cfg.start()
        self.addCleanup(self._cfg.stop)

    def test_dispatch(self):
        calls = []

        async def one(bot, gid, uid, pid):
            calls.append(("one", pid))

        async def batch(bot, gid, uid, pids):
            calls.append(("batch", list(pids)))

        with mock.patch.object(pid_lookup, "_queued_one", side_effect=one), \
                mock.patch.object(pid_lookup, "_lookup_batch", side_effect=batch):
            asyncio.run(pid_lookup._queued(None, 1, 2, ["1111111"]))
            asyncio.run(pid_lookup._queued(None, 1, 2, ["1111111", "2222222"]))
        self.assertEqual(calls, [("one", "1111111"), ("batch", ["1111111", "2222222"])])


class ChunkPlanTest(unittest.TestCase):
    """51MB 一条转发会让 NapCat 超时收不到：必须按体积/张数拆成小转发。"""

    def _files(self, sizes):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = []
        for i, size in enumerate(sizes):
            p = pathlib.Path(tmp.name, f"f{i}.jpg")
            p.write_bytes(b"x" * size)
            out.append((p, f"credit{i}"))
        return out

    def test_small_batch_is_one_chunk(self):
        items = self._files([1 * 1024 * 1024] * 3)
        chunks = pid_lookup._plan_chunks(items)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 3)

    def test_big_batch_splits_by_bytes(self):
        # 11 张 × 5MB = 55MB，18MB 上限 → 至少 4 条
        items = self._files([5 * 1024 * 1024] * 11)
        chunks = pid_lookup._plan_chunks(items)
        self.assertGreaterEqual(len(chunks), 4)
        for chunk in chunks:
            total = sum(p.stat().st_size for p, _ in chunk)
            self.assertLessEqual(total, 18 * 1024 * 1024)
            self.assertLessEqual(len(chunk), 8)
        self.assertEqual(sum(len(c) for c in chunks), 11)

    def test_splits_by_image_count_too(self):
        items = self._files([1] * 20)
        chunks = pid_lookup._plan_chunks(items)
        self.assertEqual([len(c) for c in chunks], [8, 8, 4])

    def test_single_huge_image_still_sent(self):
        items = self._files([50 * 1024 * 1024])
        chunks = pid_lookup._plan_chunks(items)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 1)


class ChunkSendTest(unittest.TestCase):
    """拆包逐条发送 + 转发失败回退成普通图片消息。"""

    def setUp(self):
        self._cfg = mock.patch.object(pid_lookup, "_config", return_value={
            "enabled": True, "search_cooldown_seconds": 0, "interval_seconds": 0,
            "cache_mb": 1024, "aliases": {},
        })
        self._cfg.start()
        self.addCleanup(self._cfg.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, sizes, forward_fails=False, fail_first_n=0, plain_fails=False):
        pids = [f"11111{i}" for i in range(len(sizes))]
        meta = {p: {"id": p, "xRestrict": 0, "illustType": 0, "userName": "A", "pageCount": 1}
                for p in pids}
        client = FakeClient(None)

        async def fake_json(path, params=None):
            return {"body": meta[path.rsplit("/", 1)[-1]]}

        client.json = fake_json
        store = FakeStore()
        sent, send_log = [], []
        bot = mock.MagicMock()
        bot.self_id = BOT

        async def fake_send_group_msg(**kw):
            sent.append(str(kw.get("message")))
            return {"message_id": 1}

        bot.send_group_msg = fake_send_group_msg
        forwards = {"n": 0}

        async def fake_send(text, paths, **kw):
            is_forward = bool(kw.get("forward"))
            if is_forward:
                forwards["n"] += 1
            send_log.append({"images": len(paths), "forward": is_forward,
                             "caption": text, "credits": [c for c in (kw.get("node_captions") or []) if c]})
            if is_forward and (forward_fails or forwards["n"] <= fail_first_n):
                raise RuntimeError("ActionFailed: send_forward_msg 超时")
            if not is_forward and plain_fails:
                raise RuntimeError("ActionFailed: 普通消息也失败")

        async def fake_send_work(_c, _s, work, _send, _cfg, **kw):
            size = sizes[pids.index(work["id"])]
            p = pathlib.Path(self.tmp.name, f"{work['id']}.jpg")
            p.write_bytes(b"x" * size)
            return [p]

        with mock.patch.object(pixiv_mod, "_client", client), \
                mock.patch.object(pixiv_mod, "_store", store), \
                mock.patch.object(pid_lookup, "send_work", new=mock.AsyncMock(side_effect=fake_send_work)), \
                mock.patch.object(pid_lookup, "_sender", return_value=fake_send):
            asyncio.run(pid_lookup._lookup_batch(bot, 100000004, 20001, pids))
        return sent, send_log, store, bot

    def test_small_batch_is_one_forward(self):
        sent, log, store, _bot = self._run([1 * 1024 * 1024] * 3)
        self.assertEqual(len(log), 1)
        self.assertTrue(log[0]["forward"])
        self.assertEqual(log[0]["images"], 3)
        self.assertEqual(store.statuses(), ["completed"])
        self.assertEqual(sent, [])

    def test_big_batch_becomes_several_forwards(self):
        # 11 张 × 5MB = 55MB → 多条小转发，每条都不超 18MB
        _sent, log, store, _bot = self._run([5 * 1024 * 1024] * 11)
        self.assertGreaterEqual(len(log), 4)
        for row in log:
            self.assertTrue(row["forward"])
        self.assertEqual(store.statuses(), ["completed"])
        self.assertIn("第 1/", log[0]["caption"])

    def test_forward_failure_falls_back_to_plain_images(self):
        """合并转发发不出去时，必须回退成普通图片消息，否则用户什么都收不到。"""
        sent, log, store, _bot = self._run([1 * 1024 * 1024] * 3, forward_fails=True)
        forwards = [r for r in log if r["forward"]]
        plains = [r for r in log if not r["forward"]]
        self.assertEqual(len(forwards), 1)              # 只试了一次转发
        self.assertEqual(len(plains), 3)                # 三张都回退发了普通消息
        self.assertTrue(all(r["images"] == 1 for r in plains))
        self.assertTrue(any("PID" in p["caption"] or "作者" in p["caption"] for p in plains))
        self.assertEqual(store.statuses(), ["completed"])   # 回退成功就算发出去了
        self.assertEqual(sent, [])

    def test_one_failed_chunk_falls_back_and_later_chunks_still_forward(self):
        sent, log, store, _bot = self._run([5 * 1024 * 1024] * 11, fail_first_n=1)
        forwards = [r for r in log if r["forward"]]
        plains = [r for r in log if not r["forward"]]
        self.assertGreaterEqual(len(forwards), 4)       # 后面的转发照发
        self.assertEqual(len(plains), 3)                # 第一条的 3 张回退
        self.assertEqual(store.statuses(), ["completed"])
        self.assertEqual(sent, [])

    def test_all_fallback_also_failed_is_partial(self):
        """连回退的普通消息都发不出去时，状态记 partial 并提示用户。"""
        sent, log, store, _bot = self._run([5 * 1024 * 1024] * 11, forward_fails=True,
                                           plain_fails=True)
        self.assertEqual(store.statuses(), ["partial"])
        self.assertTrue(any("没能发出来" in s for s in sent), sent)


class LookupTest(unittest.TestCase):
    def setUp(self):
        permissions.set_feature_enabled("100000004", "pixiv_id", True)
        self._cfg = mock.patch.object(pid_lookup, "_config", return_value={
            "enabled": True, "search_cooldown_seconds": 0, "interval_seconds": 0,
            "cache_mb": 64, "aliases": {},
        })
        self._cfg.start()
        self.addCleanup(self._cfg.stop)

    def _run(self, text, client=None, store=None, send_work=None):
        client = client or FakeClient(None)
        store = store or FakeStore()
        sent = []
        bot = mock.MagicMock()
        bot.self_id = BOT

        async def fake_send_group_msg(**kw):
            sent.append(kw.get("message"))
            return {"message_id": 1}

        bot.send_group_msg = fake_send_group_msg
        sw = send_work or mock.AsyncMock(return_value=1)

        with mock.patch.object(pixiv_mod, "_client", client), \
                mock.patch.object(pixiv_mod, "_store", store), \
                mock.patch.object(pid_lookup, "send_work", new=sw), \
                mock.patch.object(pid_lookup, "_sender", return_value=mock.AsyncMock()):
            event = event_with(text)
            asyncio.run(pid_lookup._lookup(bot, 100000004, 20001, pid_lookup._pids_in(text)[0]))
        return sent, store, sw

    def test_r18_is_refused_with_fixed_text(self):
        body = {"id": "1207478", "xRestrict": 1, "userName": "作者", "pageCount": 3, "illustType": 0}
        sent, store, sw = self._run("pid1207478", client=FakeClient(body))
        self.assertEqual(len(sent), 1)
        self.assertIn(R18_TEXT, str(sent[0]))
        self.assertIn("20001", str(sent[0]))          # 艾特了请求者
        sw.assert_not_called()                        # 一张都没发
        self.assertEqual(store.statuses(), ["r18"])

    def test_masked_work_is_refused(self):
        body = {"id": "1207478", "xRestrict": 0, "isMasked": True, "pageCount": 1, "illustType": 0}
        sent, _store, sw = self._run("pid1207478", client=FakeClient(body))
        self.assertIn(R18_TEXT, str(sent[0]))
        sw.assert_not_called()

    def test_normal_work_is_sent(self):
        body = {"id": "1207478", "xRestrict": 0, "userName": "某作者", "pageCount": 3, "illustType": 0}
        sent, store, sw = self._run("pid1207478", client=FakeClient(body))
        self.assertEqual(sent, [])                    # 正常作品不发提示，直接出图
        sw.assert_awaited_once()
        work = sw.await_args.args[2]
        self.assertEqual(work["id"], "1207478")
        self.assertEqual(work["pageCount"], 3)
        self.assertEqual(store.statuses(), ["completed"])

    def test_missing_work_reports(self):
        sent, store, sw = self._run("pid99999999", client=FakeClient(None))
        self.assertIn("找不到", str(sent[0]))
        sw.assert_not_called()
        self.assertEqual(store.statuses(), ["failed"])

    def test_ugoira_is_rejected(self):
        body = {"id": "1207478", "xRestrict": 0, "illustType": 2, "pageCount": 1}
        sent, store, sw = self._run("pid1207478", client=FakeClient(body))
        self.assertIn("不是插画", str(sent[0]))
        sw.assert_not_called()
        self.assertEqual(store.statuses(), ["failed"])


class HandleTest(unittest.TestCase):
    """触发入口：权限、固定回应、号码解析、后台任务。"""

    def setUp(self):
        self._cfg = mock.patch.object(pid_lookup, "_config", return_value={
            "enabled": True, "search_cooldown_seconds": 3600, "interval_seconds": 0,
            "cache_mb": 64, "aliases": {},
        })
        self._cfg.start()
        self.addCleanup(self._cfg.stop)

    def _bot(self, sent):
        bot = mock.MagicMock()
        bot.self_id = BOT

        async def send_group_msg(**kw):
            sent.append(str(kw.get("message")))
            return {"message_id": 1}

        bot.send_group_msg = send_group_msg
        return bot

    def test_ack_is_sent_first_and_no_cooldown(self):
        """先回「等着，本芙这就给你端上来」；冷却是 3600 秒也不该拦（已去掉时间限制）。"""
        sent = []
        bot = self._bot(sent)
        spawned = []

        def capture(coro, *a, **kw):
            spawned.append(coro)
            coro.close()
            return mock.MagicMock()

        with mock.patch.object(pid_lookup, "_allowed", return_value=True), \
                mock.patch("asyncio.create_task", side_effect=capture):
            for _ in range(2):  # 连着取两次都不该被"刚刚才取过图"挡住
                asyncio.run(pid_lookup.handle_pid(bot, event_with("pid1207478")))
        self.assertEqual(len(sent), 2, sent)
        for line in sent:
            self.assertIn(pid_lookup.ACK_TEXT, line)   # 每条都是那句固定回应（带艾特）
        self.assertEqual(len(spawned), 2)

    def test_batch_is_queued_once_with_all_pids(self):
        """一条消息多个 pid：只回一句回应，后台任务带着全部号码按顺序处理。"""
        sent = []
        bot = self._bot(sent)
        spawned = []

        def capture(coro, *a, **kw):
            spawned.append(coro)
            coro.close()
            return mock.MagicMock()

        text = "pid112267675\npid145761009, pid148047075 pid145804393"
        with mock.patch.object(pid_lookup, "_allowed", return_value=True), \
                mock.patch("asyncio.create_task", side_effect=capture):
            asyncio.run(pid_lookup.handle_pid(bot, event_with(text)))
        self.assertEqual(len(sent), 1, sent)                     # 整批只回一句
        self.assertIn(pid_lookup.ACK_TEXT, sent[0])
        self.assertEqual(len(spawned), 1)                        # 只有一个后台任务

    def test_batch_over_limit_is_trimmed(self):
        sent = []
        bot = self._bot(sent)
        pids = " ".join(f"pid{1000000 + i}" for i in range(pid_lookup.MAX_BATCH + 5))

        def capture(coro, *a, **kw):
            coro.close()
            return mock.MagicMock()

        with mock.patch.object(pid_lookup, "_allowed", return_value=True), \
                mock.patch("asyncio.create_task", side_effect=capture):
            asyncio.run(pid_lookup.handle_pid(bot, event_with(pids)))
        self.assertEqual(len(sent), 1, sent)
        self.assertIn(str(pid_lookup.MAX_BATCH), sent[0])

    def test_permission_off_keeps_original_notice(self):
        permissions.set_feature_enabled("100000004", "pixiv_id", False)
        sent = []
        bot = self._bot(sent)
        with mock.patch("asyncio.create_task", side_effect=lambda coro, *a, **kw: (coro.close(), mock.MagicMock())[1]):
            asyncio.run(pid_lookup.handle_pid(bot, event_with("pid1207478")))
        self.assertTrue(sent and "开启" in sent[0])


class PermissionTest(unittest.TestCase):
    def test_feature_off_is_refused(self):
        permissions.set_feature_enabled("100000004", "pixiv_id", False)
        bot = mock.MagicMock()
        bot.self_id = BOT
        sent = []

        async def fake_send_group_msg(**kw):
            sent.append(kw.get("message"))
            return {"message_id": 1}

        bot.send_group_msg = fake_send_group_msg
        with mock.patch.object(pixiv_mod, "_client", FakeClient(None)), \
                mock.patch.object(pixiv_mod, "_store", FakeStore()):
            asyncio.run(pid_lookup.handle_pid(bot, event_with("pid1207478")))
        self.assertTrue(sent and "开启" in str(sent[0]))

    def test_default_is_off(self):
        """新功能默认关：没有记录的群不该能用。"""
        self.assertFalse(permissions.allowed("999999999", "pixiv_id"))
        self.assertIn("pixiv_id", permissions.FEATURES)


class PanelSyncTest(unittest.TestCase):
    ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))

    def test_panel_lists_feature(self):
        import pathlib

        app_js = pathlib.Path(self.ROOT, "panel/web/app.js").read_text(encoding="utf-8")
        self.assertIn("Pixiv ID 取图", app_js)




class ReceiptAndFallbackTest(unittest.TestCase):
    def test_confirm_history_rejects_local_echo_with_duplicate_real_seq(self):
        from furina.services.pixiv import qq_receipt

        bot = mock.MagicMock()
        bot.self_id = BOT
        md5 = "03f604a3a44aecf7f473e410159f57c4"
        messages = [
            {
                "message_id": 151438404,
                "real_seq": "824044",
                "time": 1000,
                "user_id": BOT,
                "raw_message": "等着，本芙这就给你端上来",
                "message": [{"type": "text", "data": {"text": "等着，本芙这就给你端上来"}}],
            },
            {
                "message_id": 243453554,
                "real_seq": "824044",
                "time": 1002,
                "user_id": BOT,
                "raw_message": f"作者：Escobar · PID：128752034[CQ:image,file={md5}.jpg]",
                "message": [
                    {"type": "text", "data": {"text": "作者：Escobar · PID：128752034"}},
                    {"type": "image", "data": {"file": f"{md5}.jpg"}},
                ],
            },
        ]
        bot.call_api = mock.AsyncMock(return_value={"messages": messages})
        with mock.patch.object(qq_receipt, "CONFIRM_DELAYS", (0.0,)):
            res = asyncio.run(
                qq_receipt.confirm_history(
                    bot, 100000001, "作者：Escobar · PID：128752034", [md5], 999
                )
            )
        self.assertIsNone(res)

        messages[1]["real_seq"] = "824045"
        with mock.patch.object(qq_receipt, "CONFIRM_DELAYS", (0.0,)):
            res2 = asyncio.run(
                qq_receipt.confirm_history(
                    bot, 100000001, "作者：Escobar · PID：128752034", [md5], 999
                )
            )
        self.assertIsNotNone(res2)
        self.assertEqual(res2["message_id"], 243453554)

    def test_lookup_send_uncertain_replies_with_mirror_link(self):
        permissions.set_feature_enabled("100000004", "pixiv_id", True)
        body = {"id": "128752034", "xRestrict": 0, "userName": "Escobar", "pageCount": 1, "illustType": 0}
        sent = []
        bot = mock.MagicMock()
        bot.self_id = BOT

        async def fake_send_group_msg(**kw):
            sent.append(str(kw.get("message")))
            return {"message_id": 1}

        bot.send_group_msg = fake_send_group_msg
        sw = mock.AsyncMock(side_effect=pid_lookup.SendUncertain("QQ 发送未得到成功确认"))
        store = FakeStore()
        with mock.patch.object(pid_lookup, "_config", return_value={
            "enabled": True, "search_cooldown_seconds": 0, "interval_seconds": 0,
            "cache_mb": 64, "aliases": {},
        }), mock.patch.object(pixiv_mod, "_client", FakeClient(body)), \
                mock.patch.object(pixiv_mod, "_store", store), \
                mock.patch.object(pid_lookup, "send_work", new=sw), \
                mock.patch.object(pid_lookup, "_sender", return_value=mock.AsyncMock()):
            asyncio.run(pid_lookup._lookup(bot, 100000004, 20001, "128752034"))
        self.assertEqual(len(sent), 1)
        self.assertIn("https://pixiv.re/128752034.jpg", sent[0])
        self.assertEqual(store.statuses(), ["uncertain"])


if __name__ == "__main__":
    unittest.main()
