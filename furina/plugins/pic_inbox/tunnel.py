"""手机在外面也能筛图：拉起专用的 Cloudflare 隧道（pic 子域名 → 127.0.0.1 筛图接口）。

只认自己的配置文件（data/pic_tunnel.yml），已经在跑就不重复拉；别的隧道（LocalBridge 那条）一概不碰。
进程在但外网连不上（Clash 重启后 QUIC 卡死过，2026-10-29）：从外网地址 ping 一下，连续失败就只重启这条隧道。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from nonebot import logger

from ...core import env, paths

DEFAULT_EXE = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"


def config_path() -> Path:
    p = env.get("PIC_INBOX_TUNNEL_CONFIG", "")
    return Path(p) if p else Path(paths.ROOT) / "data" / "pic_tunnel.yml"


def _exe() -> str:
    return env.get("CLOUDFLARED_EXE", DEFAULT_EXE)


def _running_sync(cfg: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
              "ForEach-Object { $_.CommandLine }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=20, creationflags=0x08000000).stdout or ""
    except Exception:
        return False
    key = cfg.name.lower()
    return any(key in line.lower() for line in out.splitlines())


def _spawn_sync(cfg: Path) -> None:
    log = Path(paths.ROOT) / "logs" / "pic_tunnel.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    with open(log, "ab") as f:
        subprocess.Popen([_exe(), "tunnel", "--no-autoupdate", "--config", str(cfg), "run"],
                         stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=flags if sys.platform == "win32" else 0, close_fds=True)


def _kill_sync(cfg: Path) -> int:
    """只杀命令行里带自己配置文件名的 cloudflared（LocalBridge 那条不带，碰不到）。"""
    if sys.platform != "win32":
        return 0
    key = cfg.name.replace("'", "")
    ps = ("$n=0; Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{key}*' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; $n")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=30, creationflags=0x08000000).stdout or "0"
        return int((out.strip().splitlines() or ["0"])[-1])
    except Exception:
        return 0


def ping_url() -> str:
    base = env.get("PIC_INBOX_PUBLIC_URL", "").strip().rstrip("/")
    return f"{base}/api/ping" if base.startswith("http") else ""


async def _reachable() -> bool | None:
    """外网地址能不能走通：True / False；没配地址或本机网络整体不通（连百度都不行）时返回 None，不算隧道的错。"""
    url = ping_url()
    if not url:
        return None
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
            r = await c.get(url)
        # 530 / 1033 / 502：Cloudflare 找不到这条隧道的连接
        return r.status_code < 500
    except Exception:  # noqa: BLE001
        pass
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.get("https://www.cloudflare.com/cdn-cgi/trace")
        return False
    except Exception:  # noqa: BLE001
        return None


_health = {"fails": 0, "last_restart": 0.0}


async def ensure() -> str:
    """返回状态：off / no_config / running / started / restarted / unreachable / failed。"""
    if not env.get_bool("PIC_INBOX_TUNNEL", True):
        return "off"
    cfg = config_path()
    if not cfg.exists() or not os.path.exists(_exe()):
        return "no_config"
    if await asyncio.to_thread(_running_sync, cfg):
        if not env.get_bool("PIC_INBOX_TUNNEL_PING", True):
            return "running"
        ok = await _reachable()
        if ok is not False:
            _health["fails"] = 0
            return "running"
        _health["fails"] += 1
        # 连续两次（约 10 分钟）连不上、且 15 分钟内没重启过，才重启这一条
        if _health["fails"] < 2 or time.time() - _health["last_restart"] < 900:
            return "unreachable"
        n = await asyncio.to_thread(_kill_sync, cfg)
        logger.warning(f"[筛图] 隧道进程在但外网连不上，重启隧道（结束了 {n} 个进程）")
        _health.update(fails=0, last_restart=time.time())
        await asyncio.sleep(3)
        try:
            await asyncio.to_thread(_spawn_sync, cfg)
            return "restarted"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[筛图] 重启隧道失败: {e}")
            return "failed"
    try:
        await asyncio.to_thread(_spawn_sync, cfg)
        logger.info("[筛图] 已拉起筛图隧道")
        return "started"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[筛图] 拉起隧道失败: {e}")
        return "failed"
