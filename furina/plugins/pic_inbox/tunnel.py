"""手机在外面也能筛图：拉起专用的 Cloudflare 隧道（pic 子域名 → 127.0.0.1 筛图接口）。

只认自己的配置文件（data/pic_tunnel.yml），已经在跑就不重复拉；别的隧道（LocalBridge 那条）一概不碰。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
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


async def ensure() -> str:
    """返回状态：off / no_config / running / started / failed。"""
    if not env.get_bool("PIC_INBOX_TUNNEL", True):
        return "off"
    cfg = config_path()
    if not cfg.exists() or not os.path.exists(_exe()):
        return "no_config"
    if await asyncio.to_thread(_running_sync, cfg):
        return "running"
    try:
        await asyncio.to_thread(_spawn_sync, cfg)
        logger.info("[筛图] 已拉起筛图隧道")
        return "started"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[筛图] 拉起隧道失败: {e}")
        return "failed"
