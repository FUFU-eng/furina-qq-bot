"""NapCat 相关：进程启停（只动机器人用的那一套）、群列表缓存、WebUI 地址、登录二维码、本机其它 QQ 检测。

路径来自 .env 的 NAPCAT_DIR（迁移时从旧版默认值补上），代码里不写死。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from furina.core import qq_transport as transport_settings
from . import envfile, health, qq_transport as transport, winproc

QR_MAX_AGE = 150  # NapCat 二维码约 2 分钟过期，超过这个时间没更新就是作废的码


def napcat_dir() -> Path | None:
    raw = envfile.get("NAPCAT_DIR", "")
    return Path(raw) if raw else None


def webui_port() -> int:
    if transport_settings.uses_snowluma():
        return transport_settings.webui_port()
    try:
        return int(envfile.get("NAPCAT_WEBUI_PORT", "6099"))
    except ValueError:
        return 6099


def qrcode_path() -> Path | None:
    if transport_settings.uses_snowluma():
        return None  # SnowLuma QQ login/Load is managed in its authenticated local WebUI.
    d = napcat_dir()
    return d / "cache" / "qrcode.png" if d else None


def qr_age(now: float | None = None) -> float | None:
    p = qrcode_path()
    try:
        return max(0.0, (now or time.time()) - os.path.getmtime(p)) if p else None
    except OSError:
        return None


def log_dir() -> Path | None:
    if transport_settings.uses_snowluma():
        return transport.log_dir()
    d = napcat_dir()
    return d / "logs" if d else None


def webui_url() -> str:
    """Open the selected local QQ transport WebUI; SnowLuma remains loopback-only."""
    if transport_settings.uses_snowluma():
        return transport.open_url()
    url = envfile.get("NAPCAT_WEBUI_URL", "")
    if url:
        return url
    d = napcat_dir()
    port, token = webui_port(), ""
    if d:
        try:
            cfg = json.loads((d / "config" / "webui.json").read_text(encoding="utf-8"))
            port = int(cfg.get("port") or port)
            token = str(cfg.get("token") or "")
        except Exception:
            pass
    return f"http://127.0.0.1:{port}/webui" + (f"?token={token}" if token else "")


# ---------------- 进程 ----------------
def _boot_exe() -> str:
    d = napcat_dir()
    return str(d / "NapCatWinBootMain.exe") if d else ""


def is_running() -> bool:
    if transport_settings.uses_snowluma():
        return transport.running()
    if winproc.port_open(webui_port()):
        return True
    exe = _boot_exe()
    if not exe:
        return False
    out = winproc.powershell(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'NapCatWinBootMain.exe' -and "
        f"$_.ExecutablePath -eq {winproc.ps_quote(exe)} }} | Select-Object -ExpandProperty ProcessId")
    return any(line.strip().isdigit() for line in out.splitlines())


def start(qq: str | None) -> subprocess.Popen | None:
    """Start the configured local QQ transport. SnowLuma never auto-selects or logs in a QQ account."""
    if transport_settings.uses_snowluma():
        return transport.start()
    d = napcat_dir()
    if not d or not winproc.IS_WIN:
        raise RuntimeError("没有配置 NAPCAT_DIR，无法启动 NapCat")
    cmd = f"launcher-user.bat {qq}" if qq else "launcher-user.bat"
    return subprocess.Popen(["cmd.exe", "/k", cmd], cwd=str(d), creationflags=winproc.NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop() -> None:
    """Stop only the configured local transport. SnowLuma unloads injected QQ hooks before its own process exits."""
    if transport_settings.uses_snowluma():
        transport.stop()
        return
    exe = _boot_exe()
    if not exe:
        return
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'NapCatWinBootMain.exe' -and "
        f"$_.ExecutablePath -eq {winproc.ps_quote(exe)} }} | ForEach-Object {{ $_.ProcessId; "
        "$p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.ParentProcessId); "
        "if ($p -and $p.Name -eq 'cmd.exe' -and $p.CommandLine -match 'launcher[-\\w]*\\.bat') { $p.ProcessId } }")
    pids = [int(x.strip()) for x in winproc.powershell(script).splitlines() if x.strip().isdigit()]
    winproc.kill_tree(pids)
    for _ in range(20):
        if not winproc.port_open(webui_port()):
            return
        time.sleep(0.3)
    raise RuntimeError("NapCat 仍在监听；已停止自动操作，请检查目标进程，不强杀其他 QQ")


def ensure_onebot_config(qq: str) -> bool:
    """Ensure the active local OneBot adapter config without changing an existing transport."""
    if transport_settings.uses_snowluma():
        before = transport_settings.runtime_dir() / "config" / "onebot.json"
        prior = before.read_bytes() if before.exists() else b""
        transport_settings.write_snowluma_onebot_config()
        return not prior or before.read_bytes() != prior
    d = napcat_dir()
    if not d:
        return False
    try:
        bot_port = int(envfile.get("PORT", "8080"))
    except ValueError:
        bot_port = 8080
    path = d / "config" / f"onebot11_{qq}.json"
    if path.exists():
        try:
            net = json.loads(path.read_text(encoding="utf-8")).get("network", {})
            has_ws = any(str(c.get("url", "")).endswith("/onebot/v11/ws") for c in net.get("websocketClients", []))
            has_http = any(s.get("port") == health.http_port() for s in net.get("httpServers", []))
            if has_ws and has_http:
                return False
        except Exception:
            pass
    template = {
        "network": {
            "httpServers": [{"name": "ControlPanel", "enable": True, "port": health.http_port(), "host": "127.0.0.1",
                             "enableCors": True, "enableWebsocket": False, "messagePostFormat": "array",
                             "token": "", "debug": False}],
            "httpSseServers": [], "httpClients": [], "websocketServers": [],
            "websocketClients": [{"name": "NoneBot2", "url": f"ws://127.0.0.1:{bot_port}/onebot/v11/ws",
                                  "reconnectInterval": 3000, "enable": True, "messagePostFormat": "array",
                                  "reportSelfMessage": False, "token": "", "debug": True,
                                  "heartInterval": 30000, "verifyCertificate": True}],
            "plugins": [],
        },
        "musicSignUrl": "", "enableLocalFile2Url": False, "parseMultMsg": False, "imageDownloadProxy": "",
        "timeout": {"baseTimeout": 60000, "uploadSpeedKBps": 256, "downloadSpeedKBps": 16384, "maxTimeout": 1800000},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


# ---------------- 群列表缓存 ----------------
class GroupCache:
    """NapCat 实时群列表（15 秒刷新一次，后台线程取，绝不阻塞请求）。"""

    def __init__(self):
        self._groups: dict[str, str] = {}
        self._ts = 0.0
        self._provider = transport_settings.provider()
        self._inflight = False
        self._lock = threading.Lock()

    def get(self) -> dict[str, str]:
        self.refresh()
        with self._lock:
            return dict(self._groups)

    def clear(self) -> None:
        with self._lock:
            self._groups, self._ts = {}, 0.0

    def refresh(self, force: bool = False) -> None:
        provider = transport_settings.provider()
        with self._lock:
            if provider != self._provider:
                self._groups = {}
                self._ts = 0.0
                self._provider = provider
            if self._inflight or (not force and time.time() - self._ts < 15):
                return
            self._inflight = True
        threading.Thread(target=self._work, args=(provider,), daemon=True).start()

    def refresh_sync(self) -> dict[str, str]:
        self._work(transport_settings.provider())
        return self.get()

    def _work(self, provider: str | None = None) -> None:
        provider = provider or transport_settings.provider()
        result: dict[str, str] = {}
        try:
            data = health.call("get_group_list", timeout=2)
            for g in data or []:
                gid = str(g.get("group_id", ""))
                if gid:
                    result[gid] = g.get("group_name") or f"群 {gid}"
        except Exception:
            result = {}
        with self._lock:
            if result:
                self._groups = result
            self._ts = time.time()
            self._inflight = False


groups = GroupCache()


def stranger_nickname(qq: str) -> str:
    data = health.call("get_stranger_info", timeout=4, user_id=qq)
    return str((data or {}).get("nickname") or "")


# ---------------- 本机其它 QQ（登录助手用，只读进程列表与目录修改时间） ----------------
_DETECT_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$all = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'QQ.exe' -or $_.Name -eq 'NapCatWinBootMain.exe' })
$boot = @($all | Where-Object { $_.Name -eq 'NapCatWinBootMain.exe' } | ForEach-Object { $_.ProcessId })
$qq = @($all | Where-Object { $_.Name -eq 'QQ.exe' })
foreach ($p in @($qq | Where-Object { $_.CommandLine -notmatch '--type=' -and ($boot -notcontains $_.ParentProcessId) })) {
  $dir = $null
  foreach ($c in $qq) { if ($c.ParentProcessId -eq $p.ProcessId -and $c.CommandLine -match '--user-data-dir=\x22?([^\x22]+)') { $dir = $Matches[1]; break } }
  if (-not $dir) { $dir = Join-Path $env:APPDATA 'QQ' }
  'P|{0}|{1:HH:mm}' -f $p.ProcessId, $p.CreationDate
  foreach ($d in @(Get-ChildItem -LiteralPath (Join-Path $dir 'Partitions') -Directory)) {
    if ($d.Name -match '^qqnt_(\d{5,12})$') {
      $uin = $Matches[1]; $t = $d.LastWriteTime
      foreach ($s in @(Get-ChildItem -LiteralPath $d.FullName -Directory)) { if ($s.LastWriteTime -gt $t) { $t = $s.LastWriteTime } }
      if ($t -gt $p.CreationDate) { 'A|{0}|{1}' -f $p.ProcessId, $uin }
    }
  }
}
"""


def parse_detection(text: str) -> list[dict]:
    clients, order = {}, []
    for line in (text or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        kind, pid, value = parts
        if kind == "P" and pid.isdigit() and pid not in clients:
            clients[pid] = {"pid": int(pid), "started": value, "accounts": []}
            order.append(pid)
        elif kind == "A" and pid in clients and value.isdigit() and value not in clients[pid]["accounts"]:
            clients[pid]["accounts"].append(value)
    return [clients[p] for p in order]


def detect_other_qq() -> list[dict]:
    return parse_detection(winproc.powershell(_DETECT_SCRIPT))


def other_qq_warning(clients: list[dict], bot_qq: str) -> str:
    if not clients:
        return ""
    started = "、".join(c["started"] for c in clients)
    accounts: list[str] = []
    for c in clients:
        accounts += [a for a in c["accounts"] if a != bot_qq and a not in accounts]
    if accounts:
        shown = "、".join(accounts)
        return (f"本机还开着一个 QQ（{started} 打开），登录的可能是：{shown}。"
                f"不要用这些号扫码——它们已经登录在这台电脑上，NapCat 会提示「当前账号({accounts[0]})已登录,无法重复登录」。"
                "这是 QQ 的限制，不是机器人故障。")
    return (f"本机还开着一个 QQ（{started} 打开）。如果手机上用来扫码的号也登录在这个 QQ 里，"
            "NapCat 会提示「当前账号(…)已登录,无法重复登录」；机器人账号也不要同时登录在这个 QQ 里。")
