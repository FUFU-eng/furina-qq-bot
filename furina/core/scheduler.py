"""统一的后台定时任务：所有插件的轮询/每日任务都在这里登记。

以前每个插件各写一个 ``while True: ... sleep`` 循环，异常处理、停止方式、日志各不相同。
现在统一为：
- ``every(name, seconds, fn)``：每隔 N 秒跑一次（seconds 可以是函数，配置热改下一轮生效）；
- ``daily(name, fn, at=lambda: (时, 分), weekdays=None)``：每天到点跑一次（可限定星期几）；
- 每个任务独立运行，一个任务卡住或报错不影响别的任务；同一任务不会重叠执行；
- 异常统一记日志后继续下一轮；``status()`` 给出每个任务最近一次运行结果；
- ``cancel(name)`` / ``stop_all()`` 统一停止。

只依赖标准库，面板/工具也能导入（不会自动启动任何任务）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

try:  # 机器人进程里用 NoneBot 的日志；面板/测试里退回标准 logging
    from nonebot import logger as _log
except Exception:  # pragma: no cover
    _log = logging.getLogger("furina.scheduler")

Interval = float | Callable[[], float]


@dataclass
class Job:
    name: str
    fn: Callable[[], Awaitable[None]]
    interval: Interval
    first_delay: float = 0.0
    quiet: bool = False
    task: asyncio.Task | None = None
    runs: int = 0
    fails: int = 0
    last_start: float = 0.0
    last_end: float = 0.0
    last_error: str = ""
    extra: dict = field(default_factory=dict)

    def seconds(self) -> float:
        try:
            value = self.interval() if callable(self.interval) else self.interval
            return max(1.0, float(value))
        except Exception:
            return 30.0

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.task = None


_jobs: dict[str, Job] = {}


async def _runner(job: Job) -> None:
    if job.first_delay > 0:
        await asyncio.sleep(job.first_delay)
    while True:
        job.last_start = time.time()
        try:
            await job.fn()
            job.last_error = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.fails += 1
            job.last_error = type(exc).__name__ if job.quiet else f"{type(exc).__name__}: {exc}"[:300]
            if job.quiet:  # 只记异常类型（如 Pixiv/提醒：异常文本里可能带链接或个人信息）
                _log.warning(f"[定时任务] {job.name} 本轮失败：{type(exc).__name__}")
            elif hasattr(_log, "opt"):
                _log.opt(exception=exc).error(f"[定时任务] {job.name} 本轮失败: {job.last_error}")
            else:
                _log.exception(f"[定时任务] {job.name} 本轮失败")
        finally:
            job.runs += 1
            job.last_end = time.time()
        await asyncio.sleep(job.seconds())


def every(name: str, seconds: Interval, fn: Callable[[], Awaitable[None]], *, first_delay: float = 0.0,
          quiet: bool = False) -> Job:
    """登记并立即启动一个周期任务（需在事件循环里调用，如插件的 on_startup）。同名任务会先停掉旧的。"""
    cancel(name)
    job = Job(name=name, fn=fn, interval=seconds, first_delay=first_delay, quiet=quiet)
    job.task = asyncio.get_running_loop().create_task(_runner(job), name=f"furina:{name}")
    _jobs[name] = job
    return job


def daily(name: str, fn: Callable[[], Awaitable[None]], *, at: Callable[[], tuple[int, int]],
          weekdays: Callable[[], object] | None = None, check_seconds: float = 30.0) -> Job:
    """每天（或指定星期几，0=周一）到点后跑一次 fn；进程当天启动时已过点也会补跑一次（与旧逻辑一致）。"""
    state = {"last_day": ""}

    async def _tick() -> None:
        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        if state["last_day"] == today:
            return
        if weekdays is not None and now.tm_wday not in weekdays():
            return
        if (now.tm_hour, now.tm_min) >= tuple(at()):
            state["last_day"] = today
            await fn()

    job = every(name, check_seconds, _tick)
    job.extra["daily"] = state
    return job


def cancel(name: str) -> None:
    job = _jobs.pop(name, None)
    if job is not None:
        job.cancel()


async def stop(name: str) -> None:
    """停止并等待任务真正退出。"""
    job = _jobs.pop(name, None)
    if job is None or job.task is None:
        return
    task = job.task
    job.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def stop_all() -> None:
    for name in list(_jobs):
        await stop(name)


def get(name: str) -> Job | None:
    return _jobs.get(name)


def status() -> list[dict]:
    return [
        {"name": j.name, "running": bool(j.task and not j.task.done()), "interval": j.seconds(),
         "runs": j.runs, "fails": j.fails, "last_start": j.last_start, "last_end": j.last_end,
         "last_error": j.last_error}
        for j in _jobs.values()
    ]
