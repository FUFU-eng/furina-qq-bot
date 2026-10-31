"""QQ protocol/transport selection, independent from the chat/business logic.

NapCat remains the backwards-compatible default. SnowLuma is used only when
explicitly selected in QQ_TRANSPORT; no account login or auto-injection occurs
in this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import urllib.parse

from . import env, paths

NAPCAT = "napcat"
SNOWLUMA = "snowluma"
SNOWLUMA_RELEASE_SHA256 = "09a3fbbf42ea805c4d61b5e67bec2a46ef341fa803f211d78c40796cb7f535dc"


def provider() -> str:
    value = env.get("QQ_TRANSPORT", NAPCAT).strip().lower() or NAPCAT
    if value not in (NAPCAT, SNOWLUMA):
        raise ValueError("QQ_TRANSPORT must be napcat or snowluma")
    return value


def uses_snowluma() -> bool:
    return provider() == SNOWLUMA


def account_confirmed(self_id: str = "") -> bool:
    """收发放行判断：NapCat 恒放行；SnowLuma 要面板里明确确认过、且连上来的就是 BOT_QQ 这个号。

    QQ_TRANSPORT 写错时一律不放行（失败即关闭），而不是让每条事件都抛 ValueError。
    """
    try:
        if not uses_snowluma():
            return True
    except ValueError:
        return False
    if not env.get_bool("SNOWLUMA_ACCOUNT_CONFIRMED", False):
        return False
    expected = env.get("BOT_QQ", "")
    return bool(expected and self_id and str(self_id) == expected)


def _loopback_base(raw: str, label: str) -> str:
    parsed = urllib.parse.urlsplit(raw.strip())
    if (parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError(f"{label} must use a loopback URL without credentials/path")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def onebot_url() -> str:
    if uses_snowluma():
        raw=env.get("SNOWLUMA_HTTP_URL", "http://127.0.0.1:13000")
        base=_loopback_base(raw, "SNOWLUMA_HTTP_URL")
        if urllib.parse.urlsplit(base).scheme != "http":
            raise ValueError("SNOWLUMA_HTTP_URL must use local plain HTTP; TLS is not configured in OneBot")
        return base
    port = env.get_int("NAPCAT_HTTP_PORT", 3000)
    if not 1 <= port <= 65535:
        raise ValueError("NAPCAT_HTTP_PORT is invalid")
    return f"http://127.0.0.1:{port}"


def onebot_headers() -> dict[str, str]:
    headers = {"User-Agent": "FurinaPanel"}
    token = env.get("SNOWLUMA_HTTP_TOKEN", "") if uses_snowluma() else ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def webui_port() -> int:
    value = env.get_int("SNOWLUMA_WEBUI_PORT", 5099) if uses_snowluma() else env.get_int("NAPCAT_WEBUI_PORT", 6099)
    return value if 1 <= value <= 65535 else (5099 if uses_snowluma() else 6099)


def webui_url() -> str:
    if uses_snowluma():
        host = env.get("SNOWLUMA_WEBUI_HOST", "127.0.0.1")
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("SnowLuma WebUI must remain loopback-only")
        return f"http://{host}:{webui_port()}"
    return ""


def runtime_dir() -> Path:
    value = env.get("SNOWLUMA_DIR", str(paths.ROOT / "integrations" / "snowluma-v2.2.1"))
    result = Path(value).resolve()
    root = (paths.ROOT / "integrations").resolve()
    if root not in result.parents:
        raise ValueError("SNOWLUMA_DIR must stay inside project integrations")
    return result


def snowluma_runtime_config() -> dict:
    return {"hookAutoLoad": False, "webuiPort": webui_port(), "webuiHost": "127.0.0.1"}


def node_exe() -> Path:
    value = env.get("SNOWLUMA_NODE", str(runtime_dir() / "node.exe"))
    node = Path(value).resolve()
    if runtime_dir() not in node.parents or node.name.lower() != "node.exe":
        raise ValueError("SNOWLUMA_NODE must point to the bundled project-local node.exe")
    return node


def history_sync_override() -> bool | None:
    """SNOWLUMA_HISTORY_SYNC：登录时让 SnowLuma 从 QQ 回填离线期间的聊天记录。

    get_group_msg_history 在 SnowLuma 里只读它自己的本地消息库；不开回填时，SnowLuma 没运行那段时间的
    消息查不到（开机灌记忆、「总结」会偏少）。没写这个键 → None：保留 onebot.json 里现有的设置（默认关）。
    """
    raw = env.get("SNOWLUMA_HISTORY_SYNC", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def onebot_config(*, ws_token: str | None = None) -> dict:
    """OneBot bridge configuration: SnowLuma actively connects to NoneBot reverse WS."""
    if not uses_snowluma():
        raise RuntimeError("OneBot SnowLuma config requested while another QQ transport is active")
    api_token = env.get("SNOWLUMA_HTTP_TOKEN", "")
    client_token = ws_token if ws_token is not None else env.get("ONEBOT_V11_ACCESS_TOKEN", "")
    if len(api_token) < 32 or len(client_token) < 32:
        raise ValueError("SnowLuma HTTP and OneBot WebSocket tokens must be at least 32 characters")
    port = env.get_int("PORT", 8080)
    if not 1 <= port <= 65535:
        raise ValueError("OneBot reverse-WebSocket port is invalid")
    http_port = int(urllib.parse.urlsplit(onebot_url()).port or 80)
    return {
        "networks": {
            "httpServers": [{"name": "furina-http", "enabled": True, "host": "127.0.0.1", "port": http_port,
                             "path": "/", "enableWebSocket": False, "accessToken": api_token,
                             "messageFormat": "array", "reportSelfMessage": False}],
            "httpClients": [], "wsServers": [],
            "wsClients": [{"name": "furina-nonebot", "enabled": True,
                            "url": f"ws://127.0.0.1:{port}/onebot/v11/ws", "role": "Universal",
                            "accessToken": client_token, "reconnectIntervalMs": 3000,
                            "messageFormat": "array", "reportSelfMessage": False}],
        },
        "statusCommand": {"enabled": False, "swallow": False, "cooldownSeconds": 5, "trigger": "#sl"},
        "historySync": {"enabled": bool(history_sync_override())}, "notifications": {"channelIds": []},
    }


def validate_snowluma_install() -> tuple[Path, Path]:
    runtime = runtime_dir()
    node = node_exe()
    entry = runtime / "index.mjs"
    marker = runtime / "furina-install.json"
    if not node.is_file() or not entry.is_file() or not marker.is_file():
        raise FileNotFoundError("Verified project-local SnowLuma release is incomplete")
    receipt = json.loads(marker.read_text(encoding="utf-8"))
    if receipt.get("version") != "2.2.1" or receipt.get("sha256") != SNOWLUMA_RELEASE_SHA256:
        raise RuntimeError("SnowLuma install marker is invalid")
    return node, entry


def write_snowluma_onebot_config(*, ws_token: str | None = None) -> Path:
    runtime = runtime_dir()
    config_dir = runtime / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "onebot.json"
    wanted = onebot_config(ws_token=ws_token)
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        networks = old.get("networks") if isinstance(old, dict) else None
        if not isinstance(networks, dict):
            raise ValueError("Existing SnowLuma OneBot config is not a valid object")
        servers = networks.get("httpServers") or []
        if servers and not any(x.get("name") == "furina-http" for x in servers):
            raise FileExistsError("SnowLuma already has an unrelated HTTP server; preserving the existing config")
        clients = networks.get("wsClients") or []
        other_clients = [x for x in clients if x.get("name") != "furina-nonebot"]
        other_servers = [x for x in servers if x.get("name") != "furina-http"]
        wanted["networks"]["httpServers"] = other_servers + wanted["networks"]["httpServers"]
        wanted["networks"]["wsClients"] = other_clients + wanted["networks"]["wsClients"]
        # Retain unknown, previously configured networks unless they conflict with the reserved local API.
        wanted["networks"]["httpClients"] = networks.get("httpClients") or []
        wanted["networks"]["wsServers"] = networks.get("wsServers") or []
        for key in ("statusCommand", "historySync", "notifications"):
            if key in old:
                wanted[key] = old[key]
        if history_sync_override() is not None:  # .env 里明确写了才覆盖（SnowLuma 下次登录生效）
            wanted["historySync"] = {"enabled": bool(history_sync_override())}
    fd, tmp_name = tempfile.mkstemp(prefix=".furina-onebot-", suffix=".tmp", dir=str(config_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(wanted, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path
