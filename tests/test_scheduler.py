"""统一定时任务：周期运行、异常不中断、停止。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest

from furina.core import scheduler


class SchedulerTest(unittest.TestCase):
    def test_every_survives_errors_and_stops(self):
        calls = []

        async def job():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("boom")

        async def main():
            j = scheduler.every("测试任务", 0.01, job)
            j.seconds = lambda: 0.01
            await asyncio.sleep(0.1)
            st = {s["name"]: s for s in scheduler.status()}["测试任务"]
            await scheduler.stop("测试任务")
            return st

        st = asyncio.run(main())
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(st["fails"], 1)
        self.assertIsNone(scheduler.get("测试任务"))

    def test_daily_runs_once_per_day(self):
        calls = []

        async def job():
            calls.append(1)

        async def main():
            j = scheduler.daily("每日测试", job, at=lambda: (0, 0))
            j.seconds = lambda: 0.01
            await asyncio.sleep(0.1)
            await scheduler.stop("每日测试")

        asyncio.run(main())
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
