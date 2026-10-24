"""电脑空闲判断：显卡占用低 + 剩余内存够，才在后台跑强化（不跟游戏 / AI 抢）。"""
from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys

from ...core import env


def free_ram_mb() -> int:
    if sys.platform != "win32":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable"):
                        return int(line.split()[1]) // 1024
        except OSError:
            return 0
        return 0

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return 0
    return int(st.ullAvailPhys // (1024 * 1024))


def _gpu_util_sync() -> int | None:
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8, creationflags=flags).stdout
        vals = [int(x.strip()) for x in out.splitlines() if x.strip().isdigit()]
        return max(vals) if vals else None
    except Exception:
        return None


async def gpu_util() -> int | None:
    return await asyncio.to_thread(_gpu_util_sync)


def limits() -> tuple[int, int]:
    return env.get_int("PIC_INBOX_IDLE_GPU", 30), env.get_int("PIC_INBOX_IDLE_RAM_MB", 3000)


async def is_idle() -> tuple[bool, str]:
    """连续两次采样显卡占用都低于门槛、剩余内存高于门槛才算空闲。"""
    max_gpu, min_ram = limits()
    ram = free_ram_mb()
    if ram and ram < min_ram:
        return False, f"内存只剩 {ram}MB"
    for i in range(2):
        u = await gpu_util()
        if u is None:
            return False, "读不到显卡占用"
        if u >= max_gpu:
            return False, f"显卡占用 {u}%"
        if i == 0:
            await asyncio.sleep(4)
    return True, ""
