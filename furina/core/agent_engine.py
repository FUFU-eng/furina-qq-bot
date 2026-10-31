"""DSH 内核 worker 的生命周期与本机 IPC（只依赖 core，面板也能 import）。

- worker 在独立运行时环境（AI_CHAT_DSH_RUNTIME_DIR\\venv，默认 G:\\FurinaDshRuntime）里跑官方 deepseek-harness-sdk，
  机器人用到时才在后台无窗口拉起，空闲一段时间自己退出释放内存；
- 只监听 127.0.0.1，请求都带 data/agent/ipc.key 里的能力密钥；
- 不会按进程名杀任何东西：端口被别的程序占着就报错，不去动它；
- QQ 登录、NapCat 都不归这里管。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import db, env, paths

SDK_VERSION = "0.2.1rc2"
NS = "agent_dsh"
_lock = threading.RLock()
_last_start = {"ts": 0.0}


class AgentUnavailable(RuntimeError):
    pass


# ---------------- 路径与配置 ----------------
def runtime_dir() -> Path:
    return Path(env.get("AI_CHAT_DSH_RUNTIME_DIR", r"G:\FurinaDshRuntime"))


def runtime_python() -> Path:
    v = runtime_dir() / "venv"
    return v / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def sdk_installed() -> bool:
    v = runtime_dir() / "venv"
    cands = [v / "Lib/site-packages/deepseek_harness/__init__.py"] + list(v.glob("lib/python3*/site-packages/deepseek_harness/__init__.py"))
    return runtime_python().is_file() and any(p.is_file() for p in cands)


def data_dir() -> Path:
    return paths.DATA_DIR / "agent"


def port() -> int:
    return max(1024, min(65533, env.get_int("AI_CHAT_DSH_PORT", 53212)))


def worker_url() -> str:
    return f"http://127.0.0.1:{port()}"


def callback_url() -> str:
    return f"http://127.0.0.1:{env.get_int('PORT', 8080)}/internal/furina-agent/tool"


def bridge_key() -> str:
    p = data_dir() / "ipc.key"
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        if p.exists():
            value = p.read_text(encoding="ascii").strip()
            if len(value) >= 40:
                return value
        value = secrets.token_hex(32)
        p.write_text(value, encoding="ascii")
        return value


def authenticate(given: str, origin: str = "", remote_ip: str = "127.0.0.1") -> bool:
    return (not origin and remote_ip in ("127.0.0.1", "::1", "localhost", "testclient")
            and hmac.compare_digest(str(given or ""), bridge_key()))


def deepseek_model() -> str:
    return env.get("AI_CHAT_DSH_MODEL", "") or env.get("AI_CHAT_LLM_MODEL", "deepseek-flash") or "deepseek-flash"


def model() -> str:
    """当前线路实际用的模型名（外接线路在用时是外接模型）。"""
    if route() == "custom":
        return custom_settings()["model"]
    return deepseek_model()


# ---------------- 外接模型（别家的模型代替 DeepSeek 跑二代仿真 / Agent） ----------------
# 走 DSH 自带的 pi-ai 多服务商适配器；连不上 / 出错就暂停外接线路一段时间，先用 DeepSeek，到点再试。
CUSTOM_PROVIDER = "furina-custom"
PROTOCOL_NAMES = {"Anthropic 原生（Messages）": "anthropic-messages",
                  "OpenAI 兼容（Chat Completions）": "openai-completions",
                  "OpenAI Responses": "openai-responses"}
_reach: dict = {"ts": 0.0, "url": "", "ok": False}


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def _normalize_custom_url(raw: str, proto: str) -> str:
    import urllib.parse
    u = (raw or "").strip().rstrip("/")
    if not u:
        return ""
    for suffix in ("/chat/completions", "/v1/messages", "/messages", "/responses"):
        if u.lower().endswith(suffix):
            u = u[:-len(suffix)].rstrip("/")
            break
    if proto == "anthropic-messages" and u.lower().endswith("/v1"):
        u = u[:-3].rstrip("/")
    elif proto in ("openai-completions", "openai-responses"):
        parsed = urllib.parse.urlsplit(u)
        if parsed.scheme and parsed.netloc and not parsed.path.strip("/"):
            u = u + "/v1"
    return u


def custom_settings() -> dict:
    proto = env.get("AI_CHAT_DSH_CUSTOM_PROTOCOL", "").strip()
    proto = PROTOCOL_NAMES.get(proto, proto)
    if proto not in PROTOCOL_NAMES.values():
        proto = "anthropic-messages"
    return {
        "enabled": env.get_bool("AI_CHAT_DSH_CUSTOM_ENABLED", False),
        "base_url": _normalize_custom_url(env.get("AI_CHAT_DSH_CUSTOM_BASE_URL", ""), proto),
        "api_key": env.get("AI_CHAT_DSH_CUSTOM_API_KEY", "").strip(),
        "model": env.get("AI_CHAT_DSH_CUSTOM_MODEL", "").strip(),
        "protocol": proto,
        "context_k": _clamp(env.get_int("AI_CHAT_DSH_CUSTOM_CONTEXT_K", 200), 16, 2000),
        "cooldown_min": _clamp(env.get_int("AI_CHAT_DSH_CUSTOM_COOLDOWN_MIN", 5), 1, 1440),
        "price_hit": max(0.0, env.get_float("AI_CHAT_DSH_CUSTOM_PRICE_HIT", 0.0)),
        "price_miss": max(0.0, env.get_float("AI_CHAT_DSH_CUSTOM_PRICE_MISS", 0.0)),
        "price_out": max(0.0, env.get_float("AI_CHAT_DSH_CUSTOM_PRICE_OUT", 0.0)),
    }


def custom_configured(cs: dict | None = None) -> bool:
    cs = cs or custom_settings()
    return bool(cs["enabled"] and cs["base_url"].startswith(("http://", "https://")) and cs["model"])


def _custom_cfg_sig(cs: dict | None = None) -> str:
    cs = cs or custom_settings()
    raw = f"{cs.get('enabled')}|{cs.get('protocol')}|{cs.get('base_url')}|{cs.get('model')}|{cs.get('api_key')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def custom_down_until() -> float:
    """外接线路出错后暂停到什么时候（时间戳；0 = 没暂停）。若用户改了外接配置，自动解除旧冷却。"""
    try:
        until = float(db.get(NS, "custom_down_until", 0) or 0)
        if until > 0:
            saved_sig = str(db.get(NS, "custom_down_sig", "") or "")
            if saved_sig and saved_sig != _custom_cfg_sig():
                db.put(NS, "custom_down_until", 0)
                db.put(NS, "custom_down_reason", "")
                return 0.0
        return until
    except (TypeError, ValueError):
        return 0.0


def mark_custom_down(reason: str, count: bool = True) -> float:
    """外接线路出问题：先用 DeepSeek，冷却到点再试。返回恢复时间。count=False：只是没开（连不上），不算出错次数。"""
    cs = custom_settings()
    until = time.time() + cs["cooldown_min"] * 60
    db.put(NS, "custom_down_until", until)
    db.put(NS, "custom_down_sig", _custom_cfg_sig(cs))
    db.put(NS, "custom_down_reason", safe_error(reason, (cs["api_key"],))[:200])
    if count:
        db.update(NS, "custom_fail:" + time.strftime("%Y-%m-%d"), lambda v: int(v or 0) + 1, default=0)
    _reach.update(ts=0.0)
    return until


def clear_custom_down() -> None:
    db.put(NS, "custom_down_until", 0)
    db.put(NS, "custom_down_reason", "")
    _reach.update(ts=0.0)


def route() -> str:
    """这一刻该走哪条线路：custom（外接）/ deepseek。不联网，只看设置和冷却。"""
    if not custom_configured():
        return "deepseek"
    return "deepseek" if time.time() < custom_down_until() else "custom"


def custom_reachable(url: str, timeout: float = 0.8) -> bool:
    """外接接口的端口能不能连上（本机的 AI 客户端没开时这里立刻就知道，不用等模型请求超时）。缓存 10 秒。"""
    import urllib.parse

    now = time.time()
    if _reach["url"] == url and now - _reach["ts"] < 10:
        return bool(_reach["ok"])
    ok = False
    try:
        u = urllib.parse.urlsplit(url)
        host = u.hostname or ""
        port_ = u.port or (443 if u.scheme == "https" else 80)
        if host:
            with socket.create_connection((host, port_), timeout=timeout):
                ok = True
    except (OSError, ValueError):
        ok = False
    _reach.update(ts=now, url=url, ok=ok)
    return ok


def pick_route() -> str:
    """每轮开始前用：外接线路在用但连不上，马上暂停它、这轮就用 DeepSeek。"""
    r = route()
    if r == "custom":
        cs = custom_settings()
        if not custom_reachable(cs["base_url"]):
            mark_custom_down(f"连不上 {cs['base_url']}（外接的 AI 客户端可能没开）", count=False)
            return "deepseek"
    return r


_NOT_PROVIDER = ("busy", "bad identity", "Agent 没有打开", "桥接插件拒绝", "DSH 正在启动", "DSH 端口被其它程序占用",
                 "DSH 运行时还没装好", "缺少 tools/dsh_worker.py", "TransportClosedError", "DSH 回合超时",
                 "DSH worker 没有按时启动", "<urlopen error", "Remote end closed", "Connection reset")


def provider_failure(res: dict) -> bool:
    """这轮失败是不是模型接口的问题（而不是忙 / 没装好这类和外接模型无关的原因）。"""
    if res.get("ok"):
        return False
    err = str(res.get("error") or "")
    return bool(err) and not any(err.startswith(x) for x in _NOT_PROVIDER)


def messages_base_url() -> str:
    """DSH 走 DeepSeek 的 Messages（Anthropic 兼容）接口；留空 = 官方默认地址。"""
    return env.get("AI_CHAT_DSH_BASE_URL", "").strip()


def _code_hash() -> str:
    """worker / 桥接脚本改了（比如加了新工具）也要换新 worker。"""
    from . import paths

    files = [paths.ROOT.joinpath(*rel) for rel in (("tools", "dsh_worker.py"), ("tools", "dsh_bridge", "furina-bridge.mjs"))]
    stamp = []
    for f in files:
        try:
            st = f.stat()
            stamp.append((st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append(None)
    if _code_cache.get("stamp") != stamp:
        h = hashlib.sha256()
        for f in files:
            try:
                h.update(f.read_bytes())
            except OSError:
                pass
        _code_cache.update(stamp=stamp, hash=h.hexdigest()[:12])
    return _code_cache["hash"]


_code_cache: dict = {}


def fingerprint() -> str:
    """改了要重启 worker 才生效的设置（不含密钥原文）。"""
    from . import brain

    r = route()
    cs = custom_settings()
    custom = ([CUSTOM_PROVIDER, cs["base_url"], cs["protocol"], str(cs["context_k"]),
               hashlib.sha256(cs["api_key"].encode()).hexdigest()[:12]] if r == "custom" else [])
    parts = [model(), messages_base_url(), *custom, "agent" if brain.agent_enabled() else "-",
             env.get("AI_CHAT_AGENT_CWD", ""), str(env.get_int("AI_CHAT_DSH_MAX_TOKENS", 8192)),
             str(brain.dsh_settings()["idle_exit_min"]), callback_url(),
             hashlib.sha256(env.get("AI_CHAT_LLM_API_KEY", "").encode()).hexdigest()[:12], _code_hash()]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def safe_error(exc, extra_secrets=()) -> str:
    import re

    value = str(exc)
    for s in extra_secrets:
        if s:
            value = value.replace(str(s), "[redacted]")
    value = re.sub(r"(?i)sk-[a-z0-9_-]{8,}", "[redacted]", value)
    value = re.sub(r"(?i)(authorization|api[_-]?key|secret|token)[=: ]+[^\s,;]+", r"\1=[redacted]", value)
    return value[:400]


# ---------------- IPC ----------------
def rpc(path: str, payload=None, timeout: float = 4.0) -> dict:
    if path not in ("/health", "/run", "/shutdown", "/warmup"):
        raise ValueError("Unsupported local agent endpoint")
    req = urllib.request.Request(
        worker_url() + path,
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Furina-Key": bridge_key(), "User-Agent": "Furina-Agent/2"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AgentUnavailable(safe_error(e)) from None


def health(timeout: float = 1.0) -> dict | None:
    try:
        h = rpc("/health", timeout=timeout)
        return h if h.get("worker") == "furina-dsh" else None
    except AgentUnavailable:
        return None


def _port_busy() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port())) == 0


def start() -> dict:
    """只启动专用 worker。已有同指纹的就复用；端口被别的程序占着不杀它。"""
    from . import brain

    with _lock:
        h = health()
        if h:
            if h.get("fingerprint") == fingerprint():
                return h
            if int(h.get("busy") or 0) > 0:
                return h  # 正在忙，等这轮结束再换
            stop()
            for _ in range(20):
                if not _port_busy():
                    break
                time.sleep(0.25)
        if _port_busy():
            raise AgentUnavailable("DSH 端口被其它程序占用（没有去终止它），可以在设置里换个端口")
        if not sdk_installed():
            raise AgentUnavailable("DSH 运行时还没装好（模式与内核 → 安装 / 修复运行时）")
        worker = paths.ROOT / "tools" / "dsh_worker.py"
        if not worker.is_file():
            raise AgentUnavailable("缺少 tools/dsh_worker.py")
        if time.time() - _last_start["ts"] < 8:
            raise AgentUnavailable("DSH 正在启动")
        secret = bridge_key()
        # 子进程只继承必要的系统变量，不把网页登录令牌、代理等带进去
        keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA",
                "APPDATA", "HOME", "LANG", "LC_ALL", "PROGRAMDATA", "PROGRAMFILES", "SYSTEMDRIVE", "HOMEDRIVE", "HOMEPATH"}
        child_env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        ds = brain.dsh_settings()
        child_env.update({
            "FURINA_DSH_BRIDGE_KEY": secret, "FURINA_DSH_DATA": str(data_dir()), "FURINA_DSH_PROJECT": str(paths.ROOT),
            "FURINA_DSH_PORT": str(port()), "DEEPSEEK_API_KEY": env.get("AI_CHAT_LLM_API_KEY", ""),
            "FURINA_DSH_BASE_URL": messages_base_url(), "FURINA_DSH_MODEL": model(),
            "FURINA_DSH_MAX_TOKENS": str(max(1024, env.get_int("AI_CHAT_DSH_MAX_TOKENS", 8192))),
            "FURINA_DSH_CALLBACK_URL": callback_url(), "FURINA_DSH_FINGERPRINT": fingerprint(),
            "FURINA_DSH_AGENT": "1" if brain.agent_enabled() else "0",
            "FURINA_DSH_AGENT_CWD": env.get("AI_CHAT_AGENT_CWD", ""),
            "FURINA_DSH_IDLE_EXIT_MIN": str(ds["idle_exit_min"]),
            "FURINA_DSH_PROVIDER": "",
            "NO_PROXY": "127.0.0.1,localhost", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        })
        if route() == "custom":
            cs = custom_settings()
            child_env.update({
                "FURINA_DSH_PROVIDER": "custom", "FURINA_DSH_CUSTOM_BASE_URL": cs["base_url"],
                "FURINA_DSH_CUSTOM_PROTOCOL": cs["protocol"], "FURINA_DSH_CUSTOM_CONTEXT": str(cs["context_k"] * 1000),
                "FURINA_CUSTOM_KEY": cs["api_key"] or "none",
            })
        data_dir().mkdir(parents=True, exist_ok=True)
        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = (paths.LOG_DIR / "dsh-worker.log").open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen([str(runtime_python()), str(worker)], cwd=str(paths.ROOT), env=child_env,
                                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            log.close()
        _last_start["ts"] = time.time()
        try:
            db.put(NS, "last_start", {"pid": proc.pid, "ts": time.time()})
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "starting": True, "pid": proc.pid}


def ensure(wait: float = 20.0) -> dict:
    """确保 worker 在跑且配置是最新的；最多等 wait 秒。失败抛 AgentUnavailable。"""
    h = health()
    if h and h.get("fingerprint") == fingerprint():
        return h
    start()
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.4)
        h = health()
        if h and h.get("fingerprint") == fingerprint():
            return h
    raise AgentUnavailable("DSH worker 没有按时启动（看 logs/dsh-worker.log）")


def stop() -> dict:
    """用能力密钥请求优雅退出，不按进程名批量杀 Python。"""
    try:
        return rpc("/shutdown", {}, timeout=5)
    except AgentUnavailable:
        return {"ok": True, "already_stopped": True}


def run(payload: dict, timeout: float) -> dict:
    """同步跑一轮（机器人里用 asyncio.to_thread 调）。

    外接线路在用时：思考强度固定「不思考」（外接模型自己决定想多深；DSH 对没声明思考档位的模型会直接拒绝），
    这轮因为模型接口失败就暂停外接线路（res["custom_down"]），下一轮自动用 DeepSeek。
    """
    r = pick_route()
    sid = str(payload.get("session_id") or "")
    if sid:
        base = sid[:-2] if sid.endswith("-x") else sid
        payload = {**payload, "session_id": base + ("-x" if r == "custom" else "")}
    if r == "custom":
        payload = {**payload, "effort": "off", "effort_hi": "off"}
    try:
        ensure()
        res = rpc("/run", payload, timeout=timeout + 45)
    except AgentUnavailable as e:
        if r != "custom":
            raise
        res = {"ok": False, "error": str(e)}
    res["route"] = r
    if r == "custom":
        if provider_failure(res):
            mark_custom_down(str(res.get("error") or ""))
            res["custom_down"] = True
        elif res.get("ok"):
            db.put(NS, "custom_down_reason", "")
    return res


def warmup() -> dict:
    ensure()
    return rpc("/warmup", {}, timeout=120)


# ---------------- 会话标识（只存不透明的哈希，不把群号写进 DSH 文件名） ----------------
def _skey(bot_id, scope, channel: str) -> str:
    return hashlib.sha256(f"{bot_id}:{scope}:{channel}".encode()).hexdigest()[:24]


def session_id(bot_id, scope, *, channel: str = "group") -> str:
    """外接线路和 DeepSeek 各用各的会话（两家的思考记录格式不通用，混在一个会话里容易出错）。"""
    key = _skey(bot_id, scope, channel)
    gen = int(db.get(NS, "generation:" + key, 0) or 0) + int(db.get(NS, "generation:all", 0) or 0) * 4096
    prefix = "furagent" if channel == "agent" else "furina"
    return f"{prefix}-{key}-{gen:x}" + ("-x" if route() == "custom" else "")


def reset_all_sessions() -> None:
    """面板「清空二代会话」：所有群 / Agent 下一轮都从新会话开始（旧会话文件留在磁盘上，不删）。"""
    db.update(NS, "generation:all", lambda v: int(v or 0) + 1, default=0)


def reset_session(bot_id, scope, *, channel: str = "group") -> str:
    key = _skey(bot_id, scope, channel)
    db.update(NS, "generation:" + key, lambda v: int(v or 0) + 1, default=0)
    return session_id(bot_id, scope, channel=channel)


# ---------------- 状态 / 安装（面板用） ----------------
def status() -> dict:
    from . import brain

    h = health(0.8) or {}
    inst = install_state()
    return {"installed": sdk_installed(), "running": bool(h), "ready": bool(h.get("ready")),
            "busy": int(h.get("busy") or 0), "agent": bool(h.get("agent")), "error": h.get("error") or "",
            "stale": bool(h) and h.get("fingerprint") != fingerprint(), "pid": h.get("pid"),
            "uptime": h.get("uptime") or 0, "install": inst, "runtime_dir": str(runtime_dir()),
            "port": port(), "agent_enabled": brain.agent_enabled(), "sdk": SDK_VERSION,
            "provider": h.get("provider") or "", **custom_status()}


def custom_status() -> dict:
    cs = custom_settings()
    until = custom_down_until()
    return {"route": route(), "model": model(), "custom_configured": custom_configured(cs), "custom_model": cs["model"],
            "custom_down_until": until if until > time.time() else 0,
            "custom_down_reason": str(db.get(NS, "custom_down_reason", "") or "") if until > time.time() else "",
            "custom_fail_today": int(db.get(NS, "custom_fail:" + time.strftime("%Y-%m-%d"), 0) or 0)}


def install_log() -> Path:
    return paths.LOG_DIR / "dsh-install.log"


def install_state() -> str:
    p = install_log()
    if not p.is_file():
        return "未安装" if not sdk_installed() else "已安装"
    try:
        tail = p.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return "?"
    if "INSTALL_OK" in tail:
        return "已安装"
    if "INSTALL_FAILED" in tail:
        return "安装失败"
    if time.time() - p.stat().st_mtime > 3600:
        return "已安装" if sdk_installed() else "安装中断"
    return "安装中"


def install() -> str:
    """后台（无窗口）把官方 SDK 装进独立运行时环境；不碰机器人自己的 Python 环境。返回人话。"""
    if install_state() == "安装中":
        return "已经在安装了，稍等"
    rd = runtime_dir()
    py = runtime_python()
    index = env.get("AI_CHAT_DSH_PIP_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = install_log()
    log.write_text(f"start {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    steps = []
    if not py.is_file():
        steps.append(f'"{_base_python()}" -m venv "{rd / "venv"}"')
    steps.append(f'"{py}" -m pip install --no-input --disable-pip-version-check -i {index} '
                 f'--cache-dir "{rd / "pip-cache"}" deepseek-harness-sdk=={SDK_VERSION}')
    chain = " && ".join(f"{s} >> \"{log}\" 2>&1" for s in steps)
    cmd = f'{chain} && echo INSTALL_OK >> "{log}" || echo INSTALL_FAILED >> "{log}"'
    clean = {k: v for k, v in os.environ.items() if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
    if os.name == "nt":
        subprocess.Popen(["cmd.exe", "/c", cmd], env=clean, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        subprocess.Popen(["/bin/sh", "-c", cmd], env=clean, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    return "开始在后台安装（约 80MB），完成后这里会显示「已安装」"


def _base_python() -> str:
    import sys

    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        exe = exe.with_name("python.exe")
    return str(exe)
