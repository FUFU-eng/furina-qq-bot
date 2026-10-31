"""只读的 NapCat 状态检查（沿用旧 connection_health.py）。HTTP 失败 = 状态未知，绝不当成在线。

不登录、不退出、不重启、不发消息、不改凭据。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

from furina.core import qq_transport


@dataclass(frozen=True)
class Health:
    state: str  # online / offline / logged_out / unknown / mismatch
    detail: str
    user_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def http_port() -> int:
    """Compatibility helper: the selected OneBot HTTP API port, not the bot port."""
    try:
        from urllib.parse import urlsplit
        return int(urlsplit(qq_transport.onebot_url()).port or (443 if urlsplit(qq_transport.onebot_url()).scheme == "https" else 80))
    except (ValueError, TypeError):
        return 3000


def _refused(exc) -> bool:
    """3000 端口拒绝连接：NapCat 只有 QQ 登录成功后才开 OneBot HTTP 服务，基本就是还没登录。"""
    return isinstance(exc, ConnectionRefusedError) or isinstance(getattr(exc, "reason", None), ConnectionRefusedError)


def call(action: str, timeout: float = 3, **params):
    """调用本机 OneBot HTTP 接口，返回 data。回环地址不走系统代理。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    query = urllib.parse.urlencode({k: str(v) for k, v in params.items()})
    url = qq_transport.onebot_url().rstrip("/") + "/" + action.lstrip("/") + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers=qq_transport.onebot_headers())
    with opener.open(req, timeout=timeout) as resp:
        payload = json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "ok" or payload.get("retcode") != 0:
        raise ValueError("OneBot 返回失败或无效数据")
    return payload.get("data")


def get_data(action: str, timeout: float = 3):
    data = call(action, timeout)
    if not isinstance(data, dict):
        raise ValueError("OneBot 返回失败或无效数据")
    return data


def query_health(expected_qq: str, get=None) -> Health:
    get = get or get_data
    transport = "SnowLuma" if qq_transport.uses_snowluma() else "NapCat"
    try:
        status = get("get_status")
        online = status.get("online")
        if online is False:
            return Health("offline", f"QQ 返回离线状态；登录失效需在 {transport} 中人工确认")
        if online is not True:
            return Health("unknown", f"{transport} 未提供有效在线状态")
        info = get("get_login_info")
        qq = str(info.get("user_id") or info.get("uin") or "")
        if not qq.isdigit() or int(qq) == 0:
            return Health("unknown", f"{transport} 未提供有效登录账号")
        if expected_qq and qq != str(expected_qq):
            return Health("mismatch", f"实际登录 QQ {qq} 与所选 QQ {expected_qq} 不一致", qq)
        return Health("online", f"QQ {qq} 在线（不代表群消息收发已实测）", qq)
    except Exception as exc:
        if _refused(exc):
            return Health("logged_out", f"OneBot 服务（{http_port()} 端口）未开启：QQ 还没登录，或 {transport} 没在运行")
        return Health("unknown", f"无法确认 QQ 状态：{type(exc).__name__}；请检查 {transport} 登录及 HTTP 服务")
