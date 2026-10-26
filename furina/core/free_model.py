"""免费模型线路（our-free-model 独立服务，OpenAI 兼容接口，跑在本机）。

省钱用：打开后普通文字请求先走免费模型，失败（限流 / 地区限制 / 超时 / 空回复）自动换回付费 DeepSeek，
免费线路随后冷却一段时间，冷却期间直接走付费、不多等。带图的请求不走免费线路。

- 服务装在 ``integrations/our-free-model``（可用 AI_CHAT_FREE_DIR 改），数据在旁边的 ``our-free-model-data``；
- 服务没开时，第一次用到会在后台（无窗口）把它拉起来；
- 访问密钥留空 = 读服务数据目录 settings.json 里的 forwardKey（不用写进 .env）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import env, paths

DEFAULT_URL = "http://127.0.0.1:18900/v1"
DEFAULT_MODELS = "deepseek-v4.1-flash-free"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_state: dict = {"cooldown": {}, "spawn_at": 0.0, "version": -1, "ok": 0, "fail": 0, "last_error": ""}
_lock = threading.Lock()


def enabled() -> bool:
    return env.get_bool("AI_CHAT_FREE_ENABLED", False)


def base_url() -> str:
    return (env.get("AI_CHAT_FREE_BASE_URL") or DEFAULT_URL).rstrip("/")


def endpoint() -> str:
    base = base_url()
    if base.endswith("/chat/completions"):
        return base
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


def health_url() -> str:
    base = base_url()
    for tail in ("/chat/completions", "/v1"):
        if base.endswith(tail):
            base = base[: -len(tail)]
    return base + "/health"


def models() -> list[str]:
    raw = env.get("AI_CHAT_FREE_MODELS") or DEFAULT_MODELS
    return [m.strip() for m in raw.replace("，", ",").split(",") if m.strip()]


DEFAULT_BG_MODELS = "nvidia/nemotron-3-ultra-550b-a55b:free,poolside/laguna-s-2.1:free"
DEFAULT_BG_FEATURES = "chat.people,chat.emotion"


def bg_enabled() -> bool:
    return env.get_bool("AI_CHAT_FREE_BG_ENABLED", True)


def bg_models() -> list[str]:
    raw = env.get("AI_CHAT_FREE_BG_MODELS") or DEFAULT_BG_MODELS
    return [m.strip() for m in raw.replace("，", ",").split(",") if m.strip()]


def bg_features() -> list[str]:
    raw = env.get("AI_CHAT_FREE_BG_FEATURES") or DEFAULT_BG_FEATURES
    return [m.strip() for m in raw.replace("，", ",").split(",") if m.strip()]


def is_background(tag: str) -> bool:
    """群友看不到输出的后台任务（记忆整理、心情判断）：慢一点没关系，可以用其他免费模型。"""
    return any(tag == f or tag.startswith(f + ":") or tag.startswith(f + ".") for f in bg_features())


def timeout() -> float:
    return env.get_float("AI_CHAT_FREE_TIMEOUT", 15)


def cooldown_minutes() -> float:
    return env.get_float("AI_CHAT_FREE_COOLDOWN_MIN", 30)


def service_dir() -> Path:
    raw = env.get("AI_CHAT_FREE_DIR")
    p = Path(raw) if raw else paths.ROOT / "integrations" / "our-free-model"
    return p if p.is_absolute() else paths.ROOT / p


def data_dir() -> Path:
    d = service_dir()
    return d.parent / (d.name + "-data")


def api_key() -> str:
    key = env.get("AI_CHAT_FREE_API_KEY")
    if key:
        return key
    try:
        return str(json.loads((data_dir() / "settings.json").read_text(encoding="utf-8")).get("forwardKey") or "")
    except (OSError, ValueError):
        return ""


def thinking_payload(effort: str | None) -> dict:
    """免费 DeepSeek 默认会思考（慢），不思考时要明确关掉。"""
    if effort in ("low", "high", "max"):
        return {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
    return {"thinking": {"type": "disabled"}, "reasoning_effort": "disabled"}


# ---------------- 冷却 ----------------

def _reset_on_config_change() -> None:
    v = env.version()
    if v != _state["version"]:
        _state["version"] = v
        _state["cooldown"].clear()


def available_models(tag: str = "") -> list[str]:
    """能试的免费模型（按顺序）：主免费模型；后台任务再加上后台免费模型。冷却中的跳过。"""
    _reset_on_config_change()
    now = time.time()
    pool = models()
    if tag and bg_enabled() and is_background(tag):
        pool = pool + [m for m in bg_models() if m not in pool]
    return [m for m in pool if now >= _state["cooldown"].get(m, 0)]


def wants_json(messages: list) -> bool:
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str) and "JSON" in c.upper():
            return True
    return False


def json_ok(text: str) -> bool:
    """要求 JSON 的请求，免费模型回的里面得能解析出一个 JSON 对象，不然算失败、换下一个。"""
    s = str(text or "")
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return False
    try:
        return isinstance(json.loads(s[a:b + 1]), dict)
    except ValueError:
        return False


def mark_ok(model: str) -> None:
    _state["ok"] += 1
    _state["cooldown"].pop(model, None)


def mark_failed(model: str, reason: str, *, limited: bool = False) -> float:
    """记一次失败并冷却，返回冷却秒数。限流 / 地区限制按设置冷却（默认 30 分钟），其它错误冷却 5 分钟。"""
    secs = cooldown_minutes() * 60 if limited else min(300.0, cooldown_minutes() * 60)
    _state["cooldown"][model] = time.time() + secs
    _state["fail"] += 1
    _state["last_error"] = f"{model}: {reason}"[:200]
    return secs


def status() -> dict:
    now = time.time()
    return {"enabled": enabled(), "ok": _state["ok"], "fail": _state["fail"], "last_error": _state["last_error"],
            "cooling": {m: int(t - now) for m, t in _state["cooldown"].items() if t > now}}


# ---------------- 服务进程 ----------------

def _node() -> str:
    found = shutil.which("node")
    if found:
        return found
    for p in (r"C:\Program Files\nodejs\node.exe", r"C:\Program Files (x86)\nodejs\node.exe"):
        if Path(p).exists():
            return p
    return ""


def _port() -> int:
    try:
        from urllib.parse import urlparse
        return urlparse(base_url()).port or 18900
    except ValueError:
        return 18900


def ensure_service() -> str:
    """服务没在跑就后台拉起（5 分钟内最多拉一次）。返回说明文字；只在本机地址时才拉。"""
    if not env.get_bool("AI_CHAT_FREE_AUTOSTART", True):
        return "未开启自动启动"
    host = base_url().split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if host not in ("127.0.0.1", "localhost"):
        return "不是本机地址，不自动启动"
    cli = service_dir() / "packages" / "standalone" / "cli.mjs"
    node = _node()
    if not cli.exists() or not node:
        return "免费模型服务没装好（缺 node 或程序目录）"
    with _lock:
        if time.time() - _state["spawn_at"] < 300:
            return "刚拉起过，稍等"
        _state["spawn_at"] = time.time()
    log = paths.ROOT / "logs" / "free-model.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        out = open(log, "ab")
        subprocess.Popen([node, str(cli), "--port", str(_port()), "--data-dir", str(data_dir())],
                         cwd=str(service_dir()), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                         creationflags=NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        out.close()
    except OSError as e:
        return f"启动失败: {e}"
    return "已在后台启动免费模型服务"
