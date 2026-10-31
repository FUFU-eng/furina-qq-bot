"""她答应过的事：识别「等下发你」这类话，到点给提示，只提醒一次。"""
from tests import _env  # noqa: F401  (must be first)

import time
import unittest
from datetime import datetime

from furina.core import db
from furina.plugins.chat import commit


class CommitTest(unittest.TestCase):
    def setUp(self):
        db.delete(commit.NS, "88")

    def test_detect(self):
        now = datetime(2026, 10, 30, 14, 0).timestamp()
        self.assertAlmostEqual(commit.detect("等下发你", now) - now, 2400, delta=1)
        self.assertAlmostEqual(commit.detect("10分钟后告诉你", now) - now, 600, delta=1)
        self.assertEqual(datetime.fromtimestamp(commit.detect("明天帮你看看", now)).hour, 10)
        self.assertEqual(datetime.fromtimestamp(commit.detect("今晚给你画", now)).hour, 20)
        self.assertEqual(commit.detect("我才不会帮你", now), 0)
        self.assertEqual(commit.detect("你等下要发我吗", now), 0)
        self.assertEqual(commit.detect("今天好热", now), 0)

    def test_due_once(self):
        now = time.time()
        self.assertIsNotNone(commit.add(88, "晚点告诉你结果", "小明", "1001", now=now - 3 * 3600))
        self.assertIsNone(commit.add(88, "哈哈哈", now=now))
        self.assertEqual(commit.groups_due(now), ["88"])
        note = commit.take_note(88, now)
        self.assertIn("晚点告诉你结果", note)
        self.assertIn("道个歉", note)
        self.assertEqual(commit.take_note(88, now), "")


if __name__ == "__main__":
    unittest.main()


class PromiseWakeTest(unittest.TestCase):
    def test_promise_run_without_new_messages(self):
        import asyncio
        from unittest import mock

        from furina.plugins.chat import kernel

        _env.write_env("AI_CHAT_WAKE_RANDOM=0\n")
        st = kernel._state("40000001", 99)
        db.delete(commit.NS, "99")
        commit.add(99, "等下发你", "小明", "1001", now=time.time() - 3600)
        self.assertEqual(commit.groups_due(), ["99"])
        seen = {}

        def fake_run(payload, timeout):
            seen["prompt"] = payload["prompt"]
            return {"ok": True, "usage": []}
        with mock.patch.object(kernel.engine, "run", fake_run), \
             mock.patch.object(kernel.humanize, "skip_if_limited", return_value=False), \
             mock.patch.object(kernel, "_sync_route", return_value="deepseek"), \
             mock.patch.object(kernel, "_persona", return_value="p"):
            asyncio.run(kernel._run_group(st, "promise"))
        self.assertIn("没有新消息", seen["prompt"])
        self.assertIn("等下发你", seen["prompt"])
        self.assertEqual(commit.groups_due(), [])


class ReminderTest(unittest.TestCase):
    """谁都可以让她到点提醒（remind_later），不只主人。"""

    def setUp(self):
        db.delete(commit.NS, "77")
        self.now = datetime(2026, 10, 30, 14, 0).timestamp()  # 周五 14:00

    def at(self, text):
        ts = commit.parse_when(text, self.now)
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""

    def test_parse_when(self):
        self.assertEqual(self.at("20:30"), "10-30 20:30")
        self.assertEqual(self.at("8点半"), "10-30 20:30")       # 8点已过，没说上午就当晚上
        self.assertEqual(self.at("明天早上9点"), "10-31 09:00")
        self.assertEqual(self.at("明晚八点"), "10-31 20:00")
        self.assertEqual(self.at("下午三点一刻"), "10-30 15:15")
        self.assertEqual(self.at("30分钟后"), "10-30 14:30")
        self.assertEqual(self.at("半小时后"), "10-30 14:30")
        self.assertEqual(self.at("两小时后"), "10-30 16:00")
        self.assertEqual(self.at("下周一10点"), "11-02 10:00")
        self.assertEqual(self.at("周日"), "11-01 10:00")
        self.assertEqual(self.at("下周日"), "11-08 10:00")
        self.assertEqual(self.at("周五"), "10-30 14:30")      # 今天，没说几点且10点已过 → 半小时后
        self.assertEqual(self.at("11月3日 10点"), "11-03 10:00")
        self.assertEqual(self.at("今晚"), "10-30 20:00")
        self.assertEqual(self.at("早上6点"), "10-31 06:00")    # 今天的已经过了 → 明天
        self.assertEqual(self.at("随便什么时候"), "")
        self.assertEqual(self.at("100天后"), "")               # 超过 30 天不记

    def test_add_and_take(self):
        r = commit.add_reminder(77, "交作业", "30分钟后", "小明", "1001", "小红", now=self.now)
        self.assertTrue(r["ok"])
        self.assertFalse(commit.add_reminder(77, "x", "随便", "小明", "1001", now=self.now)["ok"])
        self.assertFalse(commit.add_reminder(77, "", "20:00", "小明", "1001", now=self.now)["ok"])
        self.assertEqual(commit.take_note(77, self.now + 60), "")
        note = commit.take_note(77, self.now + 31 * 60)
        self.assertIn("小红让你提醒小明", note)
        self.assertIn("交作业", note)
        self.assertIn("at=1001", note)
        self.assertEqual(commit.take_note(77, self.now + 32 * 60), "")

    def test_per_person_cap(self):
        for i in range(commit.MAX_PER_PERSON):
            self.assertTrue(commit.add_reminder(77, f"事{i}", "明天9点", "小明", "1001", now=self.now)["ok"])
        self.assertFalse(commit.add_reminder(77, "再一件", "明天9点", "小明", "1001", now=self.now)["ok"])
        self.assertTrue(commit.add_reminder(77, "别人的", "明天9点", "小李", "1002", now=self.now)["ok"])

    def test_promise_and_reminder_live_together(self):
        commit.add(77, "等下发你", "小明", "1001", now=self.now)
        commit.add_reminder(77, "开会", "10分钟后", "小李", "1002", now=self.now)
        note = commit.take_note(77, self.now + 3 * 3600)
        self.assertIn("到点要提醒的人", note)
        self.assertIn("你答应过的事", note)

    def test_tool_records_requester(self):
        import asyncio

        from furina.plugins.chat import kernel

        st = kernel._state("40000002", 77)
        st.unread = [{"seq": 1, "mid": "11", "uid": "2001", "name": "阿强", "text": "芙芙 半小时后提醒我收衣服",
                      "ts": time.time(), "at": False, "reply": "", "image": False, "bot": False}]
        ctx = kernel.RunCtx(kind="group", sid="40000002", gid=77)
        res = asyncio.run(kernel._tool(ctx, "remind_later", {"when": "半小时后", "what": "收衣服"}))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["who"], "阿强")
        items = db.get(commit.NS, "77")
        self.assertEqual(items[-1]["uid"], "2001")


class TickAfterRestartTest(unittest.TestCase):
    def test_wakes_group_without_state(self):
        import asyncio
        from unittest import mock

        from furina.plugins.chat import kernel

        db.delete(commit.NS, "66")
        commit.add_reminder(66, "喝水", "10分钟后", "小明", "1001", now=time.time() - 3600)
        kernel._groups.pop(("40000003", 66), None)
        woke = []
        with mock.patch.object(kernel, "get_bots", return_value={"40000003": object()}), \
             mock.patch.object(kernel, "effective_mode", return_value="sim2"), \
             mock.patch.object(kernel, "available", return_value=True), \
             mock.patch.object(kernel, "away_info", return_value=None), \
             mock.patch.object(kernel, "_wake", lambda st, why: woke.append((st.gid, why))):
            asyncio.run(kernel.promise_tick())
        self.assertIn((66, "promise"), woke)
        db.delete(commit.NS, "66")
