"""机器人进程控制与状态（启动 / 停止 / 重启 / 为扫码重启 NapCat）。

- 状态线程每 5 秒检查一次：NapCat 端口、NoneBot 进程/端口、QQ 登录健康；网页只读缓存，不阻塞。
- 所有操作都在后台线程执行，同一时间只允许一个（busy）；generation 让过期的检查结果作废。
- 机器人进程按命令行里的 v2 bot.py 绝对路径识别，不会误杀旧项目或其它 Python。
"""
from __future__ import annotations

import collections
import datetime as _dt
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from furina.core import paths

from . import envfile, health, napcat, winproc

BOT_SCRIPT = paths.ROOT / "bot.py"
CONSOLE_LOG = paths.LOG_DIR / "console.log"


def bot_port() -> int:
    try:
        return int(envfile.get("PORT", "8080"))
    except ValueError:
        return 8080


def python_exe() -> str:
    from furina.services.gscore import BOT_PYTHON
    if BOT_PYTHON.is_file():
        return str(BOT_PYTHON)
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def _trim(path: Path, limit: int = 5 * 1024 * 1024) -> None:
    """console.log 超过 5MB 时只保留末尾一段（改名方式，文件被占用时保持不动）。"""
    try:
        size = path.stat().st_size
        if size <= limit:
            return
        with open(path, "rb") as f:
            f.seek(size - limit)
            tail = f.read()
        backup = path.with_suffix(".1.log")
        backup.write_bytes(tail[tail.find(b"\n") + 1:])
        path.unlink()
    except OSError:
        pass


def find_bot_processes() -> list[tuple[int, float]]:
    """[(pid, 启动时间戳)]：命令行包含 v2 bot.py 绝对路径的 python 进程。"""
    if winproc.IS_WIN:
        script = (
            "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and "
            f"$_.CommandLine -like {winproc.ps_quote('*' + str(BOT_SCRIPT) + '*')} }} | "
            "ForEach-Object { '{0}|{1:yyyyMMddHHmmss}' -f $_.ProcessId, $_.CreationDate }")
        out = []
        for line in winproc.powershell(script, timeout=15).splitlines():
            pid, _, ts = line.strip().partition("|")
            if pid.isdigit():
                try:
                    t = _dt.datetime.strptime(ts, "%Y%m%d%H%M%S").timestamp()
                except ValueError:
                    t = 0.0
                out.append((int(pid), t))
        return out
    out = []
    for d in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
            if str(BOT_SCRIPT) in cmd and "python" in cmd:
                out.append((int(d.name), d.stat().st_mtime))
        except OSError:
            continue
    return out


class Controller:
    def __init__(self):
        self.busy = False
        self.busy_label = ""
        self.generation = 0
        self.events: collections.deque = collections.deque(maxlen=200)
        self._seq = 0
        self._lock = threading.Lock()
        self.state = {
            "running": False, "napcat": False, "nonebot": False, "port_conflict": False,
            "health": health.Health("unknown", "等待状态检查").to_dict(), "started_at": None,
            "checked_at": 0.0,
        }
        self._offline_count = 0
        self._last_offline_toast = -600.0
        self._bot_proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    # ---------- 事件（网页轮询后显示为日志 / 浮层提示） ----------
    def emit(self, level: str, text: str, toast: bool = False) -> None:
        with self._lock:
            self._seq += 1
            self.events.append({"id": self._seq, "t": time.time(), "level": level, "text": text, "toast": toast})

    def events_since(self, since: int) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["id"] > since]

    # ---------- 状态 ----------
    def start_monitor(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="panel-status")
            self._thread.start()

    def poke(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while True:
            try:
                if not self.busy:
                    self.check_once()
            except Exception as exc:  # pragma: no cover
                self.emit("error", f"[状态检查] 异常：{exc}")
            self._wake.wait(5)
            self._wake.clear()

    def check_once(self) -> dict:
        gen = self.generation
        qq = envfile.get("BOT_QQ", "")
        nap = winproc.port_open(napcat.webui_port())
        procs = find_bot_processes()
        port = winproc.port_open(bot_port())
        nonebot = bool(procs)  # 只认 v2 的 bot.py；端口被别的程序（如旧版机器人）占着不算
        h = health.query_health(qq)
        napcat.groups.refresh()
        if gen != self.generation or self.busy:
            return self.state
        prev = self.state["health"]["state"]
        running = nap and nonebot
        started = min((t for _, t in procs if t), default=None) if procs else self.state.get("started_at")
        self.state.update(running=running, napcat=nap, nonebot=nonebot, port_conflict=port and not procs,
                          health=h.to_dict(), started_at=started if running else None, checked_at=time.time())
        if prev != h.state:
            self.emit("success" if h.state == "online" else "warn", "[连接状态] " + h.detail)
        if h.state in ("offline", "mismatch", "logged_out") and running:
            self._offline_count += 1
        else:
            self._offline_count = 0
        now = time.monotonic()
        if self._offline_count >= 4 and now - self._last_offline_toast >= 600:
            self._last_offline_toast = now
            hint = {"offline": "QQ 已离线：点左下角状态查看处理办法",
                    "logged_out": "QQ 未登录：点左下角状态查看扫码指引"}.get(h.state, h.detail)
            self.emit("warn", hint, toast=True)
        return self.state

    # ---------- 操作 ----------
    def _run(self, label: str, fn) -> bool:
        with self._lock:
            if self.busy:
                return False
            self.busy, self.busy_label = True, label
            self.generation += 1

        def work():
            try:
                fn()
            except Exception as exc:
                self.emit("error", f"[错误] {label}失败：{exc}", toast=True)
            finally:
                self.busy, self.busy_label = False, ""
                self.poke()

        threading.Thread(target=work, daemon=True, name=f"panel-{label}").start()
        return True

    def start(self) -> bool:
        return self._run("启动", self._start_work)

    def stop(self) -> bool:
        return self._run("停止", self._stop_work)

    def restart(self, with_napcat: bool = False) -> bool:
        def work():
            self.emit("info", "[系统] 正在重启机器人...")
            if with_napcat:
                self._stop_work()
            else:
                self._kill_bot()
                self.emit("info", "[系统] 仅重启 NoneBot，保留 NapCat 登录会话")
            time.sleep(2)
            self._start_work()
        return self._run("重启", work)

    def restart_napcat_for_login(self) -> bool:
        """登录助手里的「重启 NapCat」：只重启机器人用的 NapCat/QQ；先快速登录（免扫码），不行再出新二维码。"""
        qq = envfile.get("BOT_QQ", "")

        def work():
            from furina.services import napcat_recover

            self.emit("info", "[登录] 正在重启 NapCat（只重启机器人用的 QQ，你自己开的 QQ 不受影响）...")
            result = napcat_recover.relogin(qq, reason="登录助手", on_event=lambda lv, msg: self.emit(lv, "[登录] " + msg))
            gen = self.generation
            if result in ("quick_ok", "qr", "qr_missing"):
                threading.Thread(target=self._monitor_login, args=(qq, gen, 30 if result == "quick_ok" else 600),
                                 daemon=True).start()
        return self._run("重启 NapCat", work)

    def _wait_qrcode(self, since: float, timeout: float) -> bool:
        p = napcat.qrcode_path()
        deadline = time.monotonic() + timeout
        while p and time.monotonic() < deadline:
            try:
                if os.path.getmtime(p) >= since:
                    return True
            except OSError:
                pass
            time.sleep(1)
        return False

    def _kill_bot(self) -> None:
        if self._bot_proc is not None and self._bot_proc.poll() is None:
            winproc.kill_tree([self._bot_proc.pid])
        self._bot_proc = None
        winproc.kill_tree([pid for pid, _ in find_bot_processes()])
        for _ in range(20):
            if not find_bot_processes():
                break
            time.sleep(0.3)

    def _start_work(self) -> None:
        self.emit("info", "[系统] 正在启动机器人（保留已有 QQ 登录会话）...")
        qq = envfile.get("BOT_QQ", "")
        if not qq.isdigit():
            raise ValueError("还没有设置机器人 QQ（设置 → 账号与角色）")
        if napcat.is_running():
            h = health.query_health(qq)
            if h.state == "mismatch":
                raise RuntimeError(h.detail + "；请在「账号与角色」里明确切换账号")
            self.emit("info", "[系统] 复用已有 NapCat；未登录时请在登录助手里扫码，不自动重登")
        else:
            napcat.start(qq)
            time.sleep(8)
        gen = self.generation
        procs = find_bot_processes()
        if procs:
            self.emit("info", "[系统] 检测到机器人服务已运行，不重复启动")
            threading.Thread(target=self._monitor_login, args=(qq, gen, 120), daemon=True).start()
            return
        if winproc.port_open(bot_port()):
            raise RuntimeError(f"{bot_port()} 端口被其它程序占用（可能是旧版机器人还在运行），请先停止它")
        paths.ensure_dirs()
        _trim(CONSOLE_LOG)
        env = dict(os.environ, BOT_QQ=qq, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        with open(CONSOLE_LOG, "ab") as out:
            self._bot_proc = subprocess.Popen([python_exe(), str(BOT_SCRIPT)], cwd=str(paths.ROOT), env=env,
                                              creationflags=winproc.NO_WINDOW, stdout=out, stderr=subprocess.STDOUT)
        ok = False
        for _ in range(40):
            if winproc.port_open(bot_port()):
                ok = True
                break
            if self._bot_proc.poll() is not None:
                break
            time.sleep(1)
        if ok:
            self.emit("success", f"[系统] 机器人已启动并监听 {bot_port()}，日志见「详情日志」", toast=True)
        elif self._bot_proc.poll() is not None:
            raise RuntimeError("机器人启动后立即退出，请看「详情日志」里的 console.log 报错")
        else:
            self.emit("warn", f"[系统] 机器人进程已拉起，但 {bot_port()} 端口尚未就绪，请稍候查看日志")
        threading.Thread(target=self._monitor_login, args=(qq, gen, 120), daemon=True).start()

    def _stop_work(self) -> None:
        self.emit("info", "[系统] 正在停止机器人...")
        self._kill_bot()
        napcat.stop()
        for _ in range(20):
            if not find_bot_processes() and not winproc.port_open(napcat.webui_port()):
                break
            time.sleep(0.5)
        self.state.update(running=False, started_at=None)
        self.emit("success", "[系统] 机器人已停止", toast=True)

    def _monitor_login(self, qq: str, gen: int, timeout: float) -> None:
        """只有“在线 + 账号一致”才算登录成功；缓存的 UIN 不算。"""
        deadline = time.monotonic() + timeout
        h = health.Health("unknown", "")
        while time.monotonic() < deadline:
            if gen != self.generation:
                return
            h = health.query_health(qq)
            if h.state == "online":
                self.emit("success", f"[系统] NapCat 已确认 QQ {qq} 在线")
                self.poke()
                return
            if h.state == "mismatch":
                self.emit("error", "[登录检查] " + h.detail, toast=True)
                return
            time.sleep(3)
        if gen == self.generation:
            self.emit("warn", f"[登录检查] 未能确认 QQ {qq} 在线；点左下角状态打开「登录助手」，用该账号扫码即可", toast=True)

    def test_connection(self) -> dict:
        qq = envfile.get("BOT_QQ", "")
        h = health.query_health(qq)
        ready = bool(find_bot_processes()) and winproc.port_open(bot_port())
        detail = h.detail + ("" if ready else "\nNoneBot 服务未运行或尚未就绪。")
        return {"ok": h.state == "online" and ready, "headline": "状态检查" if h.state == "online" and ready else "连接未就绪",
                "detail": detail}


controller = Controller()
