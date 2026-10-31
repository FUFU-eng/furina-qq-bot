"""Provider-neutral management for the local QQ transport.

The live bot continues to use the OneBot v11 adapter. This module only selects
which local gateway provides the OneBot HTTP/WS endpoints. It never logs in or
chooses a QQ account on the operator's behalf.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

from furina.core import qq_transport as settings
from furina.core import winproc
from . import envfile, health


SNOWLUMA_SHA256 = "09a3fbbf42ea805c4d61b5e67bec2a46ef341fa803f211d78c40796cb7f535dc"


def mode() -> str:
    return settings.provider()


def snowluma_root() -> Path:
    return settings.runtime_dir().parent


def node_pids() -> list[int]:
    if not winproc.IS_WIN:
        return []
    node = settings.node_exe()
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^node\\.exe$' -and "
        f"$_.ExecutablePath -eq {winproc.ps_quote(str(node))} -and "
        f"$_.CommandLine -like {winproc.ps_quote('*' + str(node.parent / 'index.mjs') + '*')} }} | "
        "Select-Object -ExpandProperty ProcessId")
    return [int(row.strip()) for row in winproc.powershell(script, timeout=10).splitlines() if row.strip().isdigit()]


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def ensure_snowluma_config() -> tuple[Path, Path]:
    node, entry = settings.validate_snowluma_install()
    config_dir = entry.parent / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = config_dir / "runtime.json"
    runtime = {}
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        if not isinstance(runtime, dict):
            raise ValueError("SnowLuma runtime.json is not a JSON object")
    # Never auto-inject every QQ process. The operator selects the intended
    # account and clicks Load in SnowLuma's authenticated local WebUI.
    runtime.update(settings.snowluma_runtime_config())
    _atomic_json(runtime_path, runtime)

    # NoneBot authenticates its reverse-WebSocket server with
    # ONEBOT_V11_ACCESS_TOKEN. Reuse that effective value when already set;
    # otherwise promote the installer-generated SnowLuma token into .env so
    # both sides use the same secret before the bot process is launched.
    ws_token = (os.environ.get("ONEBOT_V11_ACCESS_TOKEN", "").strip()
                or envfile.get("ONEBOT_V11_ACCESS_TOKEN", ""))
    if not ws_token:
        ws_token = envfile.get("SNOWLUMA_WS_TOKEN", "")
        if len(ws_token) < 32:
            raise RuntimeError("SnowLuma WebSocket access token is missing or too short")
        envfile.set_values({"ONEBOT_V11_ACCESS_TOKEN": ws_token})
    settings.write_snowluma_onebot_config(ws_token=ws_token)
    return node, entry


def running() -> bool:
    try:
        return bool(node_pids())
    except Exception:
        return False


def start() -> subprocess.Popen | None:
    if mode() != settings.SNOWLUMA:
        raise RuntimeError("QQ_TRANSPORT is not set to SnowLuma")
    if not winproc.IS_WIN:
        raise RuntimeError("This configured SnowLuma package targets Windows x64")
    node, entry = ensure_snowluma_config()
    pids = node_pids()
    if pids:
        return None
    port = settings.webui_port()
    if winproc.port_open(port):
        raise RuntimeError(f"SnowLuma WebUI port {port} is used by an unrelated process")
    log_dir = entry.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / "console.log"
    # Start from a small OS-runtime allowlist rather than inheriting the bot's
    # entire environment (which may contain model keys, cookies, or debug secrets).
    os_keys = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA",
               "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
               "OS", "PROCESSOR_ARCHITECTURE"}
    child_env = {key: value for key, value in os.environ.items() if key.upper() in os_keys}
    # Only the local WebUI bootstrap credential and explicit safety switches
    # cross the process boundary. API/WS tokens stay in the config file, and
    # consent flags are deliberately never inherited or auto-accepted.
    for key in ("SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD", "SNOWLUMA_HOOK_AUTOLOAD", "SNOWLUMA_TELEMETRY"):
        value = envfile.get(key, "")
        if value:
            child_env[key] = value
    child_env.pop("SNOWLUMA_ACCEPT_EULA", None)
    child_env.pop("SNOWLUMA_ACCEPT_PRIVACY", None)
    child_env["SNOWLUMA_HOOK_AUTOLOAD"] = "0"
    child_env["SNOWLUMA_TELEMETRY"] = "0"
    with log.open("ab") as out:
        return subprocess.Popen([str(node), str(entry)], cwd=str(entry.parent), env=child_env,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                creationflags=winproc.NO_WINDOW,
                                close_fds=True)


def _webui_session() -> tuple[httpx.Client, str]:
    url = settings.webui_url()
    password = envfile.get("SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD", "")
    if not password:
        raise RuntimeError("SnowLuma WebUI凭据未配置，未强行结束账号接入端")
    client = httpx.Client(trust_env=False, timeout=5.0, follow_redirects=False)
    try:
        response = client.post(url + "/api/login", json={"password": password})
        data = response.json()
        token = str(data.get("token") or "") if response.is_success else ""
        if not token:
            raise RuntimeError("无法用本机SnowLuma管理凭据验证接入端；未强行结束，避免账号仍挂载钩子")
        return client, token
    except Exception:
        client.close()
        raise


def stop() -> None:
    if mode() == settings.NAPCAT:
        return
    pids = node_pids()
    if not pids:
        return
    client, token = _webui_session()
    try:
        headers = {"X-SnowLuma-Token": token}
        response = client.get(settings.webui_url() + "/api/processes", headers=headers)
        response.raise_for_status()
        processes = response.json().get("list") or []
        for process in processes:
            pid = process.get("pid")
            if isinstance(pid, int) and (process.get("injected") or process.get("connected")):
                result = client.post(settings.webui_url() + f"/api/processes/{pid}/unload", headers=headers)
                result.raise_for_status()
                if result.json().get("success") is not True:
                    raise RuntimeError("SnowLuma没有确认卸载QQ挂钩；保留接入端进程")
    finally:
        client.close()
    # Only terminate the exact, project-local Node processes, and only after
    # every injected QQ process explicitly confirmed unload. Never taskkill /T.
    for pid in pids:
        result = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=12,
                                creationflags=winproc.NO_WINDOW)
        if result.returncode and pid in node_pids():
            raise RuntimeError("SnowLuma进程未退出；没有触碰QQ客户端")
    for _ in range(20):
        if not node_pids():
            return
        time.sleep(0.25)
    raise RuntimeError("SnowLuma仍在运行；没有强杀其他进程")


def confirm_account(uin: str) -> dict:
    """Explicitly bind the OneBot account the operator has just loaded in SnowLuma."""
    if mode() != settings.SNOWLUMA:
        raise ValueError("当前接入方式不是SnowLuma")
    if not uin.isdigit() or not 5 <= len(uin) <= 20:
        raise ValueError("SnowLuma返回的QQ账号格式不正确")
    live = health.query_health("")
    if live.state != "online" or live.user_id != uin:
        raise ValueError("QQ当前未在线，或当前连接的账号与刚才选择的不一致")
    info=health.get_data("get_login_info")
    if str(info.get("user_id") or info.get("uin") or "") != uin:
        raise ValueError("确认期间SnowLuma连接账号发生变化；请重新核对后再确认")
    from . import data
    current = data.profiles()
    existing = next((p for p in current if str(p.get("qq")) == uin), None)
    if existing is None:
        nickname = info.get("nickname") or "芙宁娜"
        data.save_profile(uin, str(nickname), "furina")
    # Preserve an existing account profile/binding; only select the account the
    # operator explicitly confirmed as the active transport for this bot.
    envfile.set_values({"BOT_QQ": uin, "SNOWLUMA_ACCOUNT_CONFIRMED": "true"})
    return {"confirmed": True, "uin": uin, "added_furina_profile": existing is None,
            "preserved_existing_profile": existing is not None}


def open_url() -> str:
    return settings.webui_url()


def log_dir() -> Path:
    return settings.runtime_dir() / "logs"
