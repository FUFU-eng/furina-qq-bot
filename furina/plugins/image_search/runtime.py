"""共享运行时状态：线程池、浏览器锁、串行搜图控制、反风控状态。"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from nonebot import logger


class SearchAbortedError(RuntimeError):
    """搜图被中止（超时/强制重置）时抛出，让搜图线程尽快退出"""
    pass


# 线程池，用来运行同步的浏览器代码（单线程，确保上一个搜完才下一个）
executor = ThreadPoolExecutor(max_workers=1)


class DriverLock:
    """带持有者信息的浏览器锁。

    记录持有线程、角色（search/preload）和持有时的搜图 epoch，
    供 browser._get_driver 判断锁是被"卡死的搜图线程"占用（可强制重置），
    还是被"预加载线程"占用（等待即可，不做破坏性重置）。
    release() 校验持有者，防止误放他人持有的锁。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._owner = None
        self._owner_role = None
        self._owner_epoch = None

    def acquire(self, timeout=None, role="", epoch=None):
        ok = self._lock.acquire(timeout=timeout)
        if ok:
            self._owner = threading.get_ident()
            self._owner_role = role
            self._owner_epoch = epoch
        return ok

    def release(self):
        if self._owner is None or self._owner != threading.get_ident():
            logger.warning("[浏览器锁] 尝试释放非本线程持有的锁，已忽略（防止误放他人锁）")
            return False
        self._owner = None
        self._owner_role = None
        self._owner_epoch = None
        self._lock.release()
        return True

    @property
    def locked(self):
        return self._lock.locked()

    @property
    def owner_role(self):
        return self._owner_role

    @property
    def owner_epoch(self):
        return self._owner_epoch


# 全局浏览器实例（复用）
_driver = None
_driver_lock = DriverLock()

# ========== 搜图中止控制 ==========
# 搜图中止标记：超时/卡死时 +1，搜图线程通过对比自己记录的版本号来快速退出
_search_epoch = 0
# ==================================

# ========== 反谷歌风控状态 ==========
_user_last_search = {}   # user_id -> 最近一次开始搜图的时间戳
_last_search_end = 0.0   # 上一次搜图结束的时间戳（用于连续搜图随机延迟）
# ==================================


def _interruptible_sleep(seconds: float, epoch: int):
    """可被中止的 sleep：超时强制重置时能立刻退出，而不是傻等"""
    end = time.time() + seconds
    while time.time() < end:
        if _search_epoch != epoch:
            raise SearchAbortedError("搜图已中止")
        time.sleep(0.2)


def _abort_search():
    """通知所有正在运行的搜图线程：马上停止"""
    global _search_epoch
    _search_epoch += 1


def reset_search_runtime():
    """搜图超时/卡死时重建线程池与浏览器锁，放弃可能永远不归还的旧线程。

    返回被弃用的浏览器实例，由调用方在后台线程里退出，避免阻塞新搜图。
    旧的 DriverLock/线程池不再被全局引用，旧线程即使还卡在 Selenium 调用里，
    也无法再干扰后续搜图。
    """
    global executor, _driver, _driver_lock, _search_epoch
    _search_epoch += 1
    old_driver = _driver
    _driver = None
    _driver_lock = DriverLock()
    old_executor = executor
    executor = ThreadPoolExecutor(max_workers=1)
    old_executor.shutdown(wait=False)
    return old_driver


def aborted(epoch: int) -> bool:
    """判断当前搜图是否已被中止（epoch 已变化）"""
    return _search_epoch != epoch
