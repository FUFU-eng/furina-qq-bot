"""Windows 进程/端口小工具（PowerShell + taskkill）；非 Windows 下退化为无操作，便于测试。"""
from __future__ import annotations

import base64
import os
import socket
import subprocess

IS_WIN = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def powershell(script: str, timeout: float = 20) -> str:
    """脚本经 -EncodedCommand 传入，不受引号/编码影响。失败返回空串。"""
    if not IS_WIN:
        return ""
    wrapped = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script
    encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                           capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def kill_tree(pids) -> None:
    for pid in pids:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                               timeout=15, creationflags=NO_WINDOW)
            else:
                os.kill(int(pid), 9)
        except Exception:
            pass


def ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"
