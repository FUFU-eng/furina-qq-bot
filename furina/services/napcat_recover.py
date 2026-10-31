"""QQ 掉线后重新登录：先用快速登录（免扫码）重启机器人用的 NapCat，登不上再出二维码。

两处用它：
- 机器人收到 NapCat「下线通知」（登录已失效）后，等一会儿还没恢复就自动试一次（NAPCAT_AUTO_RECOVER，默认开，
  30 分钟内最多一次）；
- 面板登录助手的「重启 NapCat」按钮。

只动 NAPCAT_DIR 这一套 NapCat（NapCatWinBootMain 和它的 launcher），不碰用户自己开的 QQ。
NapCat 用 WMI 拉起，不挂在机器人进程下面，所以之后「重启机器人（保留 QQ 登录）」不会把它一起杀掉。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from ..core import env, paths, qq_transport, winproc

STATE_PATH = paths.DATA_DIR / "napcat_recover.json"
LOG_PATH = paths.LOG_DIR / "napcat-recover.log"
_lock = threading.Lock()
QR_GRACE_MIN = 10  # 刚出二维码后这么多分钟内，自动恢复不重启 NapCat


# ---------------- 设置 ----------------

def enabled() -> bool:
    # SnowLuma owns the client hook lifecycle. Never let the legacy recovery
    # worker start/kill a second login client under a migrated transport.
    return qq_transport.provider() == qq_transport.NAPCAT and env.get_bool("NAPCAT_AUTO_RECOVER", True)


def delay_seconds() -> int:
    return max(10, env.get_int("NAPCAT_RECOVER_DELAY", 120))


def cooldown_minutes() -> int:
    return max(5, env.get_int("NAPCAT_RECOVER_COOLDOWN_MIN", 30))


def napcat_dir() -> Path | None:
    raw = env.get("NAPCAT_DIR")
    return Path(raw) if raw else None


def http_port() -> int:
    if qq_transport.provider() == qq_transport.SNOWLUMA:
        from urllib.parse import urlsplit
        try:
            parsed=urlsplit(qq_transport.onebot_url())
            return int(parsed.port or (443 if parsed.scheme == "https" else 80))
        except (TypeError,ValueError):
            return 0
    return env.get_int("NAPCAT_HTTP_PORT", 3000)


# ---------------- 记录 ----------------

def log(msg: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def save_state(**kw) -> dict:
    st = load_state()
    st.update(kw)
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return st


# ---------------- 状态 ----------------

def status(qq: str = "") -> str:
    """online / offline（NapCat 在跑但 QQ 掉了）/ down（OneBot 没开：没登录或没运行）/ mismatch / unknown。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(action):
        if qq_transport.provider() == qq_transport.SNOWLUMA:
            url=qq_transport.onebot_url().rstrip("/")+"/"+action.lstrip("/")
            headers=qq_transport.onebot_headers()
        else:
            url=f"http://127.0.0.1:{http_port()}/{action}"
            headers={"User-Agent": "FurinaRecover"}
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=3) as resp:
            payload = json.loads(resp.read(1024 * 1024).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("retcode") != 0:
            raise ValueError("bad payload")
        return payload.get("data") or {}

    try:
        online = get("get_status").get("online")
        if online is False:
            return "offline"
        if online is not True:
            return "unknown"
        if qq:
            uid = str(get("get_login_info").get("user_id") or "")
            if uid and uid != str(qq):
                return "mismatch"
        return "online"
    except Exception as e:  # noqa: BLE001
        reason = getattr(e, "reason", e)
        if isinstance(reason, ConnectionRefusedError) or isinstance(e, ConnectionRefusedError):
            return "down"
        return "unknown"


# ---------------- 进程 ----------------

def _boot_exe() -> str:
    d = napcat_dir()
    return str(d / "NapCatWinBootMain.exe") if d else ""


def stop_napcat() -> None:
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
    time.sleep(3)


def start_napcat(qq: str | None) -> bool:
    """用 launcher-user.bat 启动（带 QQ 号 = 快速登录）。经 WMI 创建，无窗口，不是调用方的子进程。"""
    d = napcat_dir()
    if not d or not winproc.IS_WIN:
        return False
    cmd = f"cmd.exe /k launcher-user.bat {qq}" if qq else "cmd.exe /k launcher-user.bat"
    script = (
        "$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}; "
        f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine={winproc.ps_quote(cmd)}; "
        f"CurrentDirectory={winproc.ps_quote(str(d))}; ProcessStartupInformation=$si}}; $r.ReturnValue")
    return winproc.powershell(script, timeout=30).strip().endswith("0")


def qrcode_path() -> Path | None:
    d = napcat_dir()
    return d / "cache" / "qrcode.png" if d else None


def _wait(pred, timeout: float, step: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


def relogin(qq: str, *, reason: str = "", on_event=None, quick_wait: float = 60, qr_wait: float = 60,
            notify_owner: bool = False) -> str:
    """先快速登录，再二维码。返回 quick_ok / qr / qr_missing / busy / no_napcat。阻塞，放线程里跑。"""
    def say(level: str, msg: str):
        log(f"[{level}] {msg}")
        if on_event:
            try:
                on_event(level, msg)
            except Exception:  # noqa: BLE001
                pass

    if qq_transport.provider() != qq_transport.NAPCAT:
        say("error", "当前不是NapCat接入；拒绝重启或登录NapCat")
        return "unsupported_transport"
    if not napcat_dir():
        say("error", "没有配置 NAPCAT_DIR，无法重启 NapCat")
        return "no_napcat"
    if not _lock.acquire(blocking=False):
        return "busy"
    try:
        st = load_state()
        if time.time() < float(st.get("running_until") or 0):
            say("warn", "另一处正在重新登录，这次跳过")
            return "busy"
        save_state(running_until=time.time() + quick_wait + qr_wait + 60, pending_qq="", pending_at=0)
        say("info", f"正在用快速登录（免扫码）重启 NapCat，账号 {qq}" + (f"（{reason}）" if reason else ""))
        stop_napcat()
        start_napcat(qq)
        if _wait(lambda: status(qq) == "online", quick_wait):
            say("success", f"QQ {qq} 已免扫码重新登录")
            # 提醒标记和「结束」一起写：机器人重连事件可能和这里同时发生，见 connection_watch
            save_state(running_until=0, last_result="quick_ok", last_ok=time.time(),
                       **({"notify_owner": time.time()} if notify_owner else {}))
            return "quick_ok"
        say("warn", "快速登录没成功（登录凭据可能已彻底失效），改为生成二维码")
        started = time.time()
        stop_napcat()
        start_napcat(None)
        qr = qrcode_path()

        def fresh():
            try:
                return bool(qr) and os.path.getmtime(qr) >= started
            except OSError:
                return False

        got = _wait(fresh, qr_wait, step=1)
        result = "qr" if got else "qr_missing"
        say("warn", f"请用机器人账号 {qq} 扫码登录" if got else "NapCat 已重启，但还没生成二维码，可在登录助手里刷新")
        save_state(running_until=0, last_result=result)
        return result
    finally:
        _lock.release()


# ---------------- 掉线后自动恢复（机器人进程里调用） ----------------

def schedule_auto(qq: str, tag: str = "", message: str = "") -> bool:
    """收到下线通知后调用：后台等 delay 秒，仍掉线就自动 relogin 一次（受冷却限制）。返回是否已安排。"""
    if not enabled() or not napcat_dir():
        log(f"[info] 收到下线通知（{tag} {message}），自动恢复未开启")
        return False
    st = load_state()
    if time.time() - float(st.get("last_auto") or 0) < cooldown_minutes() * 60:
        log(f"[warn] 收到下线通知，但 {cooldown_minutes()} 分钟内已自动恢复过一次，这次不再自动重启，请扫码")
        return False
    save_state(last_auto=time.time(), last_kick=time.time(), last_kick_reason=f"{tag} {message}".strip(),
               pending_qq=str(qq), pending_at=time.time())
    _start_auto(qq, delay_seconds())
    return True


def qr_waiting(minutes: float = QR_GRACE_MIN) -> bool:
    """最近几分钟刚出过二维码（有人可能正在扫）：这时自动恢复不要重启 NapCat，免得把正在扫的二维码作废。"""
    qr = qrcode_path()
    try:
        return bool(qr) and time.time() - os.path.getmtime(qr) < minutes * 60
    except OSError:
        return False


def _start_auto(qq: str, wait: float) -> None:
    def run():
        time.sleep(max(0.0, wait))
        st = load_state()
        if str(st.get("pending_qq") or "") != str(qq):
            return  # 已经被别处处理过（另一次恢复 / 登录助手）
        save_state(pending_qq="", pending_at=0)
        s = status(qq)
        if s not in ("offline", "down"):  # down：被踢后 NapCat 自己退出了，也要拉起来
            log(f"[info] 等了 {int(wait)} 秒，当前状态 {s}，不需要自动恢复")
            return
        if s == "down" and qr_waiting():
            log("[info] NapCat 刚出过二维码、可能有人正在扫码，这次不自动重启")
            return
        relogin(qq, reason="掉线后自动恢复", notify_owner=True)

    threading.Thread(target=run, daemon=True, name="napcat-recover").start()


def resume_pending(qq: str) -> bool:
    """机器人重启后调用：掉线后安排的自动恢复还没来得及做（机器人进程被重启打断了），接着做。"""
    if not enabled() or not napcat_dir():
        return False
    st = load_state()
    if str(st.get("pending_qq") or "") != str(qq):
        return False
    at = float(st.get("pending_at") or 0)
    if time.time() - at > 3600:
        save_state(pending_qq="", pending_at=0)
        return False
    if recovering():
        return False
    left = at + delay_seconds() - time.time()
    log(f"[info] 机器人重启前 QQ {qq} 掉线后的自动恢复还没做，接着做")
    _start_auto(qq, max(10.0, left))
    return True


def recovering() -> bool:
    return time.time() < float(load_state().get("running_until") or 0)


def pop_owner_notice() -> bool:
    """自动恢复成功后，机器人重新连上时提醒一次主人。"""
    st = load_state()
    ts = float(st.get("notify_owner") or 0)
    if ts and time.time() - ts < 3600:
        save_state(notify_owner=0)
        return True
    return False
