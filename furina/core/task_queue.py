"""搜图/强化/JM 下载各自的跨群任务队列：三条队列互不干扰。"""

import asyncio


class TaskQueue:
    """一条独立 FIFO 队列，同一时刻只允许一个任务占用处理名额。"""

    def __init__(self, name: str):
        self.name = name
        self._task_lock = asyncio.Lock()
        self._waiters = 0

    def queued_count(self) -> int:
        """当前排队中的请求数（不含正在处理的那一个）。"""
        return max(0, self._waiters - 1)

    def enqueue(self) -> "QueueTicket":
        """登记一个任务并返回排队凭证；position=当前请求序号（1=直接开始）。"""
        self._waiters += 1
        return QueueTicket(self, self._waiters)


class QueueTicket:
    """一次任务的排队凭证。

    调用 enqueue() 登记后立刻能拿到请求序号并发送排队提示；
    async with ticket 时才真正等待并占用唯一处理名额。
    """

    __slots__ = ("queue", "position", "_entered", "_released")

    def __init__(self, queue: TaskQueue, position: int):
        self.queue = queue
        self.position = position
        self._entered = False
        self._released = False

    def _release_waiter(self):
        if not self._released:
            self.queue._waiters -= 1
            self._released = True

    async def __aenter__(self):
        if self._released or self._entered:
            raise RuntimeError("Queue ticket has already been used or cancelled")
        try:
            await self.queue._task_lock.acquire()
        except BaseException:
            self._release_waiter()
            raise
        if self._released:
            self.queue._task_lock.release()
            raise RuntimeError("Queue ticket cancelled while waiting")
        self._entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._entered = False
        self._release_waiter()
        self.queue._task_lock.release()
        return False

    def cancel(self):
        """还没进入等待（async with 之前）就退出时，释放排队名额。"""
        if not self._entered:
            self._release_waiter()


search_queue = TaskQueue("search")
upscale_queue = TaskQueue("upscale")
jm_queue = TaskQueue("jm")


def enqueue_search() -> QueueTicket:
    """登记一个搜图任务，返回搜图队列的排队凭证。"""
    return search_queue.enqueue()


def queued_search_count() -> int:
    """搜图队列当前排队人数。"""
    return search_queue.queued_count()


def enqueue_upscale() -> QueueTicket:
    """登记一个强化任务，返回强化队列的排队凭证。"""
    return upscale_queue.enqueue()


def queued_upscale_count() -> int:
    """强化队列当前排队人数。"""
    return upscale_queue.queued_count()


def enqueue_jm() -> QueueTicket:
    """登记一个 JM 下载任务，返回 JM 队列的排队凭证。"""
    return jm_queue.enqueue()


def queued_jm_count() -> int:
    """JM 下载队列当前排队人数。"""
    return jm_queue.queued_count()
