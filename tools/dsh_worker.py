"""芙宁娜的 DSH 内核 worker：在独立的运行时环境（AI_CHAT_DSH_RUNTIME_DIR\\venv）里运行官方 DeepSeek Harness SDK。

由机器人按需在后台拉起（无窗口），只监听 127.0.0.1，所有请求都要带本机能力密钥（data/agent/ipc.key）。
- GET  /health   ：状态（版本、配置指纹、是否已启动运行时）
- POST /run      ：跑一轮（二代仿真的一次「醒来」，或 Agent 模式的一次私聊任务）
- POST /shutdown ：优雅退出

只依赖标准库 + deepseek_harness；不 import 机器人代码（机器人的 nonebot 环境和这里互不影响）。
QQ 侧所有真实动作都由 furina-bridge.mjs 回调机器人执行，发送限速、权限仍由机器人把关。
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "furina-dsh/2"
KEY = os.environ.get("FURINA_DSH_BRIDGE_KEY", "")
PORT = int(os.environ.get("FURINA_DSH_PORT", "53212"))
NATIVE_PORT = PORT + 1
DATA = Path(os.environ.get("FURINA_DSH_DATA", str(Path(__file__).resolve().parents[1] / "data" / "agent")))
PROJECT = Path(os.environ.get("FURINA_DSH_PROJECT", str(Path(__file__).resolve().parents[1])))
FINGERPRINT = os.environ.get("FURINA_DSH_FINGERPRINT", "")
AGENT = os.environ.get("FURINA_DSH_AGENT", "0") == "1"
IDLE_EXIT = float(os.environ.get("FURINA_DSH_IDLE_EXIT_MIN", "30") or 0) * 60
BRIDGE = PROJECT / "tools" / "dsh_bridge" / "furina-bridge.mjs"

_lock = threading.RLock()
_state: dict = {"harness": None, "resumed": set(), "busy": set(), "last": time.time(), "error": "", "started": 0.0}
_server: ThreadingHTTPServer | None = None

# 群聊会话只要 QQ 工具：会话日志上传、插件清单、会话标题（额外花钱）、MCP 一律不加载；
# 没打开 Agent 时连 shell / 文件 / 子进程插件都不加载（不只是对模型隐藏）。
_ALWAYS_OFF = ["session-log-deepseek", "plugin-package-inventory-deepseek", "deepseek-llm-api-extensions",
               "session-title", "mcp-resources"]
_SHELL = ["tool-fs", "tool-working-directory", "persistent-bash", "persistent-pwsh", "terminal-bash",
          "terminal-pwsh", "pty", "subprocess"]


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _patch_file() -> Path:
    off = list(_ALWAYS_OFF) + ([] if AGENT else list(_SHELL))
    llm_cfg = {"thinking": "enabled", "reasoningEffort": "off", "streamIdleTimeoutMs": 90000}
    base = os.environ.get("FURINA_DSH_BASE_URL", "").strip()
    if base:
        llm_cfg["baseURL"] = base
    patch = [{"id": n, "disabled": True} for n in off]
    patch += [{"id": "llm-deepseek", "config": llm_cfg},
              {"id": "tools", "config": {"mode": "native"}},
              {"insert": [{"id": "furina-bridge", "name": BRIDGE.resolve().as_posix()}]}]
    p = DATA / "furina.patch.yml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _harness():
    with _lock:
        h = _state["harness"]
        if h is not None:
            return h
        from deepseek_harness import DeepSeekHarness

        home = DATA / "dsh_home"
        work = Path(os.environ.get("FURINA_DSH_AGENT_CWD", "") or (DATA / "workspace"))
        home.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        env = {
            "FURINA_DSH_BRIDGE_KEY": KEY, "FURINA_DSH_NATIVE_PORT": str(NATIVE_PORT),
            "FURINA_DSH_CALLBACK_URL": os.environ.get("FURINA_DSH_CALLBACK_URL", ""),
            "FURINA_DSH_AGENT": "1" if AGENT else "0",
            "DSH_SYSTEM_PROMPT": "你在QQ里。",
            "DSH_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
        }
        max_tokens = int(os.environ.get("FURINA_DSH_MAX_TOKENS", "8192") or 8192)
        h = DeepSeekHarness(
            provider="deepseek-official", model=os.environ.get("FURINA_DSH_MODEL", "deepseek-flash"),
            reasoning_effort=None, max_tokens=max_tokens, cwd=str(work), dsh_home=str(home),
            profile="sdk-minimal", patches=(str(_patch_file()),), env=env,
            api_key=os.environ.get("DEEPSEEK_API_KEY") or None,
            initialize_timeout_seconds=90,
        )
        t = time.time()
        h.start()
        _state.update(harness=h, started=time.time(), error="")
        log(f"运行时已启动（{time.time() - t:.1f}s，Agent={'开' if AGENT else '关'}）")
        return h


def _drop_harness(reason: str) -> None:
    with _lock:
        h = _state["harness"]
        _state.update(harness=None, resumed=set())
    if h is not None:
        log(f"重置运行时：{reason}")
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass


def _bridge(path: str, payload: dict, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{NATIVE_PORT}{path}", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Furina-Key": KEY})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        try:
            return json.loads(e.read().decode("utf-8") or "{}") | {"ok": False, "status": e.code}
        except Exception:  # noqa: BLE001
            return {"ok": False, "status": e.code}


def usage_rows(events) -> list[dict]:
    """官方 DSH 的 inputTokens 与 cacheReadTokens 不重叠（不能照 OpenAI 的总输入再减一次）。"""
    rows, seen = [], set()
    for event in events or []:
        if event.get("type") != "assistant/message":
            continue
        data = event.get("data") or {}
        usage = data.get("usage") or (data.get("message") or {}).get("usage") or {}
        msg = data.get("message") or {}
        ident = msg.get("id") or event.get("seq")
        if ident in seen or not usage:
            continue
        seen.add(ident)
        hit = int(usage.get("cacheReadTokens") or 0)
        miss = int(usage.get("inputTokens") or 0) + int(usage.get("cacheWriteTokens") or 0)
        out = int(usage.get("outputTokens") or 0)
        think = int(usage.get("reasoningTokens") or 0)
        rows.append({"prompt_tokens": hit + miss, "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": miss,
                     "completion_tokens": out, "reasoning_tokens": think})
    return rows


def _final_text(events) -> str:
    for event in reversed(events):
        if event.get("type") != "assistant/message":
            continue
        data = event.get("data") or {}
        msg = data.get("message") if isinstance(data.get("message"), dict) else data
        parts = [str(b.get("text") or "") for b in (msg.get("content") or []) if isinstance(b, dict) and b.get("type") == "text"]
        return "".join(parts)
    return ""


def _finish_reason(events) -> str:
    for event in reversed(events):
        if event.get("type") == "turn/end":
            reason = (event.get("data") or {}).get("reason") or {}
            return str(reason.get("kind") or "")
    return ""


def _run_turn(h, sid: str, prompt, timeout: float) -> list:
    """和 SDK 的 Session.run 一样，但带超时（桥接插件到点会取消这一轮，这里再兜一层）。"""
    client = h.client
    blocks = [{"type": "text", "text": prompt}] if isinstance(prompt, str) else prompt
    events: list = []
    with client.subscribe_session_notifications(sid) as sub:
        message_id = client.session_prompt(sid, blocks, notification_subscription=sub)
        received = False
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError("DSH 回合超时")
            try:
                n = sub._notifications.get(timeout=min(left, 5))
            except queue.Empty:
                continue
            if isinstance(n, BaseException):
                raise n
            if not received:
                if n.method != "session.event" or n.payload.get("sessionId") != sid:
                    continue
                ev = n.payload.get("event") or {}
                ins = (ev.get("data") or {}).get("inserted") if isinstance(ev, dict) else None
                if not (ev.get("type") == "agent/inbox/spliced" and isinstance(ins, list)
                        and any(isinstance(m, dict) and m.get("id") == message_id for m in ins)):
                    continue
                received = True
            if n.method == "session.event" and n.payload.get("sessionId") == sid:
                ev = n.payload.get("event")
                if isinstance(ev, dict):
                    events.append(ev)
            if n.method == "session.status" and n.payload.get("sessionId") == sid and n.payload.get("status") == "idle":
                return events


def do_run(p: dict) -> dict:
    sid, rid = str(p.get("session_id") or ""), str(p.get("run_id") or "")
    if not (sid.startswith("furina-") or sid.startswith("furagent-")) or len(rid) != 32:
        return {"ok": False, "error": "bad identity"}
    if sid.startswith("furagent-") and not AGENT:
        return {"ok": False, "error": "Agent 没有打开"}
    with _lock:
        if sid in _state["busy"]:
            return {"ok": False, "error": "busy"}
        _state["busy"].add(sid)
    t0 = time.time()
    timeout = max(15.0, min(900.0, float(p.get("timeout") or 120)))
    try:
        try:
            h = _harness()
        except Exception as e:  # noqa: BLE001
            _state["error"] = f"运行时启动失败：{e}"[:300]
            _drop_harness("启动失败")
            return {"ok": False, "error": _state["error"]}
        ctx = _bridge("/context", {"session_id": sid, "run_id": rid, "persona": p.get("persona") or "",
                                   "max_steps": int(p.get("max_steps") or 8), "effort": p.get("effort") or "off",
                                   "effort_hi": p.get("effort_hi") or p.get("effort") or "off",
                                   "timeout_ms": int(timeout * 1000)})
        if not ctx.get("ok"):
            return {"ok": False, "error": f"桥接插件拒绝：{ctx.get('reason') or ctx.get('status')}"}
        if sid not in _state["resumed"]:
            try:
                h.resume_session(sid)
            except Exception as e:  # noqa: BLE001  新会话没有存档是正常的
                msg = str(e)
                if "occupied" in msg or "busy" in msg:
                    log(f"恢复会话失败：{msg[:120]}")
            _state["resumed"].add(sid)
        events, err = [], ""
        try:
            events = _run_turn(h, sid, p.get("prompt") or "", timeout + 20)
        except TimeoutError as e:
            err = str(e)
            _drop_harness("回合超时")
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            log("回合失败：" + traceback.format_exc()[-800:])
            if "closed" in err.lower() or "transport" in err.lower():
                _drop_harness("运行时断开")
        res = _bridge("/result", {"session_id": sid, "run_id": rid}) if _state["harness"] is not None else {}
        out = {"ok": not err, "error": err, "final_response": _final_text(events), "finish_reason": _finish_reason(events),
               "usage": usage_rows(events), "silent": bool(res.get("silent")), "said": int(res.get("said") or 0),
               "tools": res.get("tools") or [], "escalated": bool(res.get("escalated")), "steps": int(res.get("steps") or 0),
               "cancelled": res.get("cancelled") or "", "capabilities": res.get("capabilities") or [],
               "seconds": round(time.time() - t0, 1)}
        log(f"{sid[:18]} 完成 {out['seconds']}s 结束={out['finish_reason'] or err[:40]} 工具={','.join(out['tools'])[:120]}")
        return out
    finally:
        with _lock:
            _state["busy"].discard(sid)
            _state["last"] = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = "FurinaDSH"

    def log_message(self, *_a):
        pass

    def _send(self, code: int, value: dict) -> None:
        b = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _auth(self) -> bool:
        if self.headers.get("Origin") or self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        return bool(KEY) and hmac.compare_digest(self.headers.get("X-Furina-Key", ""), KEY)

    def do_GET(self):
        if not self._auth():
            return self._send(403, {"ok": False})
        if self.path == "/health":
            _state["last"] = max(_state["last"], time.time() - 1) if _state["busy"] else _state["last"]
            return self._send(200, {"ok": True, "worker": "furina-dsh", "version": VERSION, "fingerprint": FINGERPRINT,
                                    "ready": _state["harness"] is not None, "busy": len(_state["busy"]),
                                    "agent": AGENT, "error": _state["error"], "pid": os.getpid(),
                                    "uptime": round(time.time() - _state["started"]) if _state["started"] else 0})
        return self._send(404, {"ok": False})

    def do_POST(self):
        if not self._auth():
            return self._send(403, {"ok": False})
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            p = json.loads(self.rfile.read(min(n, 8 * 1024 * 1024)).decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return self._send(400, {"ok": False})
        if self.path == "/run":
            return self._send(200, do_run(p))
        if self.path == "/warmup":
            try:
                _harness()
                return self._send(200, {"ok": True})
            except Exception as e:  # noqa: BLE001
                _state["error"] = f"运行时启动失败：{e}"[:300]
                _drop_harness("启动失败")
                return self._send(200, {"ok": False, "error": _state["error"]})
        if self.path == "/shutdown":
            self._send(200, {"ok": True})
            threading.Thread(target=_shutdown, args=("收到停止请求",), daemon=True).start()
            return None
        return self._send(404, {"ok": False})


def _shutdown(reason: str) -> None:
    log(f"退出：{reason}")
    _drop_harness(reason)
    if _server is not None:
        _server.shutdown()


def _idle_watch() -> None:
    while True:
        time.sleep(30)
        if IDLE_EXIT > 0 and not _state["busy"] and time.time() - _state["last"] > IDLE_EXIT:
            _shutdown(f"空闲超过 {IDLE_EXIT / 60:.0f} 分钟，释放内存（下次用到会自动再启动）")
            return


def main() -> int:
    global _server
    if len(KEY) < 32:
        print("缺少能力密钥", file=sys.stderr)
        return 2
    DATA.mkdir(parents=True, exist_ok=True)
    _server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    _server.daemon_threads = True
    (DATA / "worker.json").write_text(json.dumps({"pid": os.getpid(), "port": PORT, "version": VERSION,
                                                   "fingerprint": FINGERPRINT, "ts": time.time()}), encoding="utf-8")
    threading.Thread(target=_idle_watch, daemon=True).start()
    log(f"worker 已就绪 127.0.0.1:{PORT}（{VERSION}，Agent={'开' if AGENT else '关'}）")
    try:
        _server.serve_forever(poll_interval=0.5)
    finally:
        _drop_harness("进程结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
