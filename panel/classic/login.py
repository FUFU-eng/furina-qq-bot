"""QQ 登录助手（2026-09-25）。

机器人 QQ 未登录 / 掉线时，从主页「WebUI」按钮、侧栏底部的状态文字或「测试」失败弹窗打开：
- 写明该用哪个号扫码（.env 的 BOT_QQ + QQ 号管理里的昵称）；
- 解释 NapCat WebUI 的「当前账号(xxx)已登录,无法重复登录」：扫码用的号已经登录在这台电脑的另一个
  QQ 里（QQ 不允许同一台电脑同一个号登录两次），或者 NapCat 掉线后残留了旧会话；
- 检测本机另外打开的 QQ 客户端，以及它可能登录着的账号（进程列表 + QQ 分区目录的修改时间，只读）；
- 直接显示 NapCat 缓存的登录二维码（NapCat\\cache\\qrcode.png），作废的旧码不显示；
- 「打开 WebUI」「重启 NapCat」（只重启机器人用的 NapCat/QQ 进程树，需用户确认）。
不读取任何密码 / token，也不会替用户登录：扫码确认必须在手机上完成。
"""
import base64
import math
import os
import socket
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from furina.core import env, qq_transport
from . import v2bridge as _v2
from .settings import _ask, _button, _chip, _dark_title_bar, _label, build_style, set_prop

DEFAULT_QRCODE_PATH = r"E:\NapCat\cache\qrcode.png"
QR_MAX_AGE = 150  # NapCat 的二维码约 2 分钟过期，过期时会自动换一张新图；超过这个时间没更新 = 这张码已作废
QR_BOX = 216
PROBE_EVERY = 2  # 秒：检查 NapCat 是否在运行
DETECT_EVERY = 15  # 秒：重新检测本机其它 QQ

# 找出「不是 NapCat 拉起的」QQ 主进程；再看它的数据目录里哪些 qqnt_<QQ号> 分区在它启动后有写入。
# 只读进程列表和目录修改时间，不读任何文件内容。脚本里不用双引号，经 -EncodedCommand 传给 PowerShell。
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

_EXTRA_STYLE = """
QDialog#loginHelper { background: #0f172a; }
QLabel#statusText { font-size: 15px; font-weight: 700; color: #f1f5f9; }
QLabel#stepNum { background: rgba(255,209,102,0.14); color: #ffd166; border-radius: 11px; font-weight: 700;
                 min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }
QLabel#stepTitle { font-size: 13px; font-weight: 700; color: #e8eef8; }
QFrame#warnCard { background: rgba(245,176,65,0.08); border: 1px solid rgba(245,176,65,0.35); border-radius: 12px; }
QLabel#warnText { color: #f5d49a; font-size: 12px; }
QLabel#qrImage { background: #ffffff; border-radius: 10px; }
QLabel#qrEmpty { background: #0b1526; border: 1px dashed rgba(255,255,255,0.16); border-radius: 10px;
                 color: #8b9bb6; font-size: 13px; padding: 12px; }
QLabel#qrEmpty[tone="ok"] { color: #3ecf8e; border-color: rgba(62,207,142,0.45); }
QLabel#qrCaption { color: #a9b6ca; font-size: 12px; }
"""


def _run_powershell(script, timeout=20):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def parse_detection(text):
    """'P|pid|HH:mm' 一行一个 QQ 主进程，'A|pid|QQ号' 是它可能登录着的账号。"""
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
    return [clients[pid] for pid in order]


def detect_other_qq():
    return parse_detection(_run_powershell(_DETECT_SCRIPT))


def port_open(port, host="127.0.0.1", timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def qr_age(path, now=None):
    """二维码图片距上次更新的秒数；文件不存在返回 None。"""
    try:
        return max(0.0, (now or time.time()) - os.path.getmtime(path))
    except OSError:
        return None


def other_qq_warning(clients, bot_qq):
    """把检测结果写成提示；没有别的 QQ 时返回空字符串。

    机器人账号本身不列出来：NapCat 用的是同一个数据目录，它登录 / 掉线前写入的分区会被误认成
    「登录在另一个 QQ 里」。机器人账号的情况由底部的通用提示覆盖。"""
    if not clients:
        return ""
    started = "、".join(c["started"] for c in clients)
    accounts = []
    for c in clients:
        accounts += [a for a in c["accounts"] if a != bot_qq and a not in accounts]
    if accounts:
        shown = "、".join(accounts)
        return (f"本机还开着一个 QQ（{started} 打开），登录的可能是：{shown}。"
                f"不要用这些号扫码——它们已经登录在这台电脑上，NapCat 会提示「当前账号({accounts[0]})已登录,无法重复登录」。"
                "这是 QQ 的限制，不是机器人故障。")
    return (f"本机还开着一个 QQ（{started} 打开）。如果手机上用来扫码的号也登录在这个 QQ 里，"
            "NapCat 会提示「当前账号(…)已登录,无法重复登录」；机器人账号也不要同时登录在这个 QQ 里。")


def _bot_account(panel):
    qq = ""
    try:
        qq = (panel._read_env_value("BOT_QQ") or "").strip()
    except Exception:
        pass
    nick = ""
    try:
        for profile in panel._load_qq_profiles() or []:
            if str(profile.get("qq", "")).strip() == qq:
                nick = str(profile.get("nickname") or "").strip()
                break
    except Exception:
        pass
    return qq, nick


def _set_role(button, role):
    """切换按钮样式（primary / danger / 普通）后重新套用样式表。"""
    button.setObjectName(role or "")
    button.style().unpolish(button)
    button.style().polish(button)


class _Bridge(QObject):
    detected = Signal(object)
    probed = Signal(object)
    confirmed = Signal(object)


class LoginHelper(QDialog):
    def __init__(self, panel, qrcode_path=DEFAULT_QRCODE_PATH, webui_port=6099):
        super().__init__(panel)
        self.panel = panel
        self.snow = qq_transport.uses_snowluma()
        self.qrcode_path = qrcode_path
        self.webui_port = webui_port
        self.qq, self.nick = _bot_account(panel)
        self.account_text = f"「{self.nick}（{self.qq}）」" if self.nick and self.qq else f"「{self.qq or '机器人账号'}」"
        self._others = None  # None = 还在检测
        self._napcat_up = None  # None = 还没探测
        self._probe_inflight = False
        self._detect_inflight = False
        self._last_probe = 0.0
        self._last_detect = 0.0
        self._restart_requested = False
        self._restart_at = 0.0
        self._was_online = self._health_state() == "online"
        self._login_succeeded = False
        self._qr_stamp = None
        self._qr_dpr = None
        self._view = None

        self.setObjectName("loginHelper")
        self.setWindowTitle("QQ 接入助手" if self.snow else "QQ 登录助手")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#0f172a"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setStyleSheet(build_style() + _EXTRA_STYLE)
        self.setMinimumWidth(800)
        self.resize(840, 600)

        self._bridge = _Bridge(self)
        self._bridge.detected.connect(self._on_detected)
        self._bridge.probed.connect(self._on_probed)
        self._bridge.confirmed.connect(self._on_confirmed)
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()
        self._fit()

    # 界面 ----------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(14)
        root.addWidget(_label("QQ 接入助手", "pageTitle"))
        root.addWidget(_label("SnowLuma 由你手动选择 QQ 客户端；账号确认前芙宁娜不会处理群消息。" if self.snow else "机器人 QQ 掉线或没登录时，按这里的步骤重新扫码。", "pageDesc", wrap=True))

        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(12)

        status = self._card()
        row = QHBoxLayout()
        row.setSpacing(10)
        self.chip = _chip("检查中", "off")
        row.addWidget(self.chip, 0, Qt.AlignTop)
        self.status_text = _label("正在检查 QQ 状态…", "statusText", wrap=True)
        row.addWidget(self.status_text, 1)
        status.layout().addLayout(row)
        self.status_note = _label("", "help", wrap=True)
        self.status_note.setVisible(False)
        status.layout().addWidget(self.status_note)
        account = QHBoxLayout()
        account.setSpacing(8)
        account.addWidget(_label("当前接入", "fieldLabel"))
        account.addWidget(_label("SnowLuma" if self.snow else "NapCat", "bigName"))
        account.addWidget(_label(("配置QQ " + self.qq if self.qq else "账号待确认") if self.snow else f"QQ {self.qq or '未设置'}", "pill"))
        account.addStretch(1)
        status.layout().addLayout(account)
        left.addWidget(status)

        steps = self._card("SnowLuma接入步骤" if self.snow else "扫码步骤")
        instructions = (
            ("1", "阅读并同意SnowLuma首次协议提示", "SnowLuma是第三方QQ接入工具，不代表官方认证通道。"),
            ("2", "在本地管理页选择你要使用的QQ进程并点击Load", "自动挂钩保持关闭；不要选择/尝试已被封的账号。"),
            ("3", "确认检测到的QQ号码确实是机器人号", "点下面的确认按钮后才会启用芙宁娜处理消息。"),
        ) if self.snow else (
            ("1", f"手机 QQ 先切换到 {self.account_text}", "手机上登录的是哪个号，扫码登录进来的就是哪个号。"),
            ("2", "扫右边的二维码，或打开 WebUI 的「扫码登录」", "WebUI 先弹出「登录失败」时：点「确定」，再点「重新获取二维码」。"),
            ("3", "在手机上确认登录", "左下角变成「QQ 在线」就好了，这个窗口也会显示登录成功。"),
        )
        for num, title, sub in instructions:
            steps.layout().addLayout(self._step(num, title, sub))
        left.addWidget(steps)

        self.warn_card = QFrame()
        self.warn_card.setObjectName("warnCard")
        warn_lay = QVBoxLayout(self.warn_card)
        warn_lay.setContentsMargins(14, 10, 14, 10)
        self.warn_text = _label("", "warnText", wrap=True)
        warn_lay.addWidget(self.warn_text)
        self.warn_card.setVisible(False)
        left.addWidget(self.warn_card)
        left.addStretch(1)
        body.addLayout(left, 1)

        qr_card = self._card()
        qr_card.setFixedWidth(QR_BOX + 44)
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(QR_BOX, QR_BOX)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setWordWrap(True)
        qr_card.layout().addWidget(self.qr_label, 0, Qt.AlignHCenter)
        self.qr_caption = _label("", "qrCaption", wrap=True)
        self.qr_caption.setAlignment(Qt.AlignHCenter)
        qr_card.layout().addWidget(self.qr_caption)
        qr_card.layout().addStretch(1)
        body.addWidget(qr_card, 0, Qt.AlignTop)
        root.addLayout(body, 1)

        guidance = (
            "SnowLuma是第三方QQ接入工具，不代表官方通道。首次使用请自行阅读并同意其协议；"
            "只手动加载你选定的QQ进程，确认显示账号无误后再启用芙宁娜。"
            if self.snow else
            "WebUI 提示「当前账号(xxx)已登录,无法重复登录」？xxx 不是机器人账号：扫码用错了号，这个号正登录在这台电脑的"
            "另一个 QQ 里；xxx 就是机器人账号：先确认自己的 QQ 里没有登录它，再点「重启 NapCat」重新扫码"
            "（NapCat 掉线后可能残留旧会话）。"
        )
        root.addWidget(_label(guidance, "help", wrap=True))

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.restart_btn = _button(
            "打开 SnowLuma 管理页" if self.snow else "重启 NapCat", "danger", self._on_restart,
            "只启动或打开 SnowLuma 本地管理页；不会自动登录、加载或结束任何 QQ 进程" if self.snow else
            "只重启机器人用的 NapCat（QQ）进程，不会关闭你自己开的 QQ，也不停 NoneBot；重启后重新扫码"
        )
        footer.addWidget(self.restart_btn)
        footer.addStretch(1)
        self.close_btn = _button("关闭", slot=self.close)
        footer.addWidget(self.close_btn)
        self.confirm_btn = _button("确认此QQ并启用", "primary", self._on_confirm_snow, "只在你核对账号无误后确认；此前出站和群聊处理都被阻止")
        self.confirm_btn.setVisible(False)
        footer.addWidget(self.confirm_btn)
        self.start_btn = _button("启动机器人", "primary", self._on_start)
        footer.addWidget(self.start_btn)
        self.webui_btn = _button("打开本地管理页" if self.snow else "打开 WebUI", slot=self._on_webui,
                                 tip="打开本机 SnowLuma WebUI；需要手动登录并选择 QQ" if self.snow else "在浏览器里打开 NapCat WebUI（扫码 / 密码登录）")
        footer.addWidget(self.webui_btn)
        root.addLayout(footer)

    def _card(self, title=None):
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        if title:
            lay.addWidget(_label(title, "cardTitle"))
        return card

    def _step(self, num, title, sub):
        row = QHBoxLayout()
        row.setSpacing(10)
        badge = _label(num, "stepNum")
        badge.setAlignment(Qt.AlignCenter)
        row.addWidget(badge, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(_label(title, "stepTitle", wrap=True))
        text.addWidget(_label(sub, "help", wrap=True))
        row.addLayout(text, 1)
        return row

    def showEvent(self, event):
        super().showEvent(event)
        _dark_title_bar(self)
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            QTimer.singleShot(0, self, self._fit)

    def _fit(self):
        """自动换行的文字要按实际宽度算高度（Qt 的最小尺寸不考虑换行，窗口偏矮时文字会叠在一起）。"""
        lay = self.layout()
        if lay is None or not lay.hasHeightForWidth():
            return
        need = lay.totalHeightForWidth(self.width())
        if need > 0:
            self.setMinimumHeight(need)
            if self.height() < need:
                self.resize(self.width(), need)

    # 状态 ----------------------------------------------------------------
    def _health(self):
        return getattr(self.panel, "_health", None)

    def _health_state(self):
        return getattr(self._health(), "state", "unknown")

    def _state(self):
        if self.snow:
            health = self._health()
            if not self._napcat_up:
                return "snowluma_stopped"
            if getattr(health, "state", "") == "online":
                actual = str(getattr(health, "user_id", "") or "")
                confirmed = env.get_bool("SNOWLUMA_ACCOUNT_CONFIRMED", False)
                if not confirmed or actual != env.get("BOT_QQ", ""):
                    return "snowluma_confirm"
                return "online"
            return "snowluma_waiting"
        if self._restart_requested and getattr(self.panel, "busy", False):
            return "restarting"
        state = self._health_state()
        if state == "online":
            return "online"
        if self._napcat_up is False:
            return "no_napcat"
        if state in ("offline", "mismatch"):
            # 重启后主界面最多 5 秒才刷新状态；新二维码已经生成就按「等待扫码」显示
            try:
                if self._restart_requested and os.path.getmtime(self.qrcode_path) >= self._restart_at:
                    return "waiting"
            except OSError:
                pass
            return state
        return "waiting"

    def _tick(self):
        now = time.monotonic()
        if not self._probe_inflight and now - self._last_probe >= PROBE_EVERY:
            self._probe_inflight = True
            self._last_probe = now
            threading.Thread(target=self._probe_worker, daemon=True).start()
        if not self._detect_inflight and now - self._last_detect >= DETECT_EVERY:
            self._detect_inflight = True
            self._last_detect = now
            threading.Thread(target=self._detect_worker, daemon=True).start()
        self._render()

    def _probe_worker(self):
        up = _v2.napcat_running()
        try:
            self._bridge.probed.emit(up)
        except RuntimeError:  # 窗口已关闭
            pass

    def _detect_worker(self):
        clients = [] if self.snow else detect_other_qq()
        try:
            self._bridge.detected.emit(clients)
        except RuntimeError:
            pass

    def _on_probed(self, up):
        self._probe_inflight = False
        self._napcat_up = bool(up)
        self._render()

    def _on_detected(self, clients):
        self._detect_inflight = False
        self._others = clients or []
        self._render()

    def _on_confirm_snow(self):
        actual=str(getattr(self._health(), "user_id", "") or "")
        if not actual.isdigit():
            self.status_note.setText("尚未读到SnowLuma当前QQ账号")
            self.status_note.setVisible(True)
            return
        if not _ask(self,"确认机器人QQ",f"SnowLuma当前加载的账号是 {actual}。确认这是你选定的机器人号吗？确认后才开放消息处理，并把BOT_QQ绑定到该账号。"):
            return
        self.confirm_btn.setEnabled(False)
        def run():
            try:
                from panel.services.qq_transport import confirm_account
                result=confirm_account(actual)
                self._bridge.confirmed.emit({"ok":True,**result})
            except Exception as exc:
                self._bridge.confirmed.emit({"ok":False,"error":str(exc)[:180]})
        threading.Thread(target=run,daemon=True).start()

    def _on_confirmed(self,result):
        self.confirm_btn.setEnabled(True)
        if not result.get("ok"):
            self.status_note.setText(result.get("error") or "账号确认失败")
            self.status_note.setVisible(True)
            return
        logger=getattr(self.panel,"add_log",None)
        if callable(logger):logger("success",f"[SnowLuma] 已确认QQ {result.get('uin')}，正在启动机器人")
        starter=getattr(self.panel,"start_bot",None)
        if callable(starter):starter()
        self._last_probe=0.0

    def _render(self):
        state = self._state()
        health = self._health()
        if state == "online":
            if not self._was_online:
                self._login_succeeded = True  # 打开窗口后才上线 = 这次扫码成功
            self._was_online = True
            self._restart_requested = False
        elif state != "restarting":
            self._was_online = False

        chip_text, tone, headline = {
            "snowluma_stopped": ("未运行", "off", "SnowLuma本地接入端尚未启动"),
            "snowluma_waiting": ("等待加载QQ", "warn", "打开SnowLuma本地管理页，手动选择目标QQ并点Load；确认前芙宁娜保持锁定"),
            "snowluma_confirm": ("待确认", "warn", f"检测到QQ {getattr(health, 'user_id', '') or '未知'}；核对为预定机器人号后再点确认启用"),
            "restarting": ("启动中", "info", "正在启动本地QQ接入端…"),
            "online": ("在线", "on", "✓ 机器人 QQ 在线，消息通道正常"),
            "no_napcat": ("未运行", "off", "NapCat 没有在运行：先点「启动机器人」"),
            "offline": ("已掉线", "warn", "QQ 登录已失效（NapCat 还在运行，但账号掉线了）"),
            "mismatch": ("账号不对", "warn", "NapCat 登录的不是机器人账号"),
            "waiting": ("等待扫码", "warn", "NapCat 正在等待登录：用机器人账号扫码即可"),
        }[state]
        note = ""
        if self.snow:
            actual = str(getattr(health, "user_id", "") or "")
            if state == "snowluma_confirm":
                note = f"检测到 {actual}，请确认这就是你亲自选定的机器人账号。确认后才会启动芙宁娜。"
            elif state == "snowluma_waiting":
                note = "先阅读/同意SnowLuma条款，在本机管理页手动选中QQ进程并点击Load。不要选被封号。"
        elif state == "online" and self._login_succeeded:
            headline = "✓ 登录成功，机器人已上线"
        if not self.snow and state == "offline":
            note = "掉线后在同一个 NapCat 里直接重新扫码，可能会被提示「已登录,无法重复登录」，建议先点「重启 NapCat」。"
        elif state == "mismatch":
            actual = getattr(health, "user_id", "") or "其它账号"
            note = f"现在登录的是 {actual}，应该是 {self.qq}。点「重启 NapCat」后用机器人账号重新扫码。"
        elif state == "waiting" and not getattr(self.panel, "bot_running", True) and self._napcat_up:
            note = "NoneBot 没在运行：登录成功后还要在主页点「启动机器人」。"
        warning = other_qq_warning(self._others, self.qq) if self._others and state != "online" else ""
        relayout = warning != self.warn_text.text() or bool(note) != self.status_note.isVisible()
        if warning != self.warn_text.text():
            self.warn_text.setText(warning)
        self.warn_card.setVisible(bool(warning))
        self.chip.setText(chip_text)
        if self.chip.property("tone") != tone:
            set_prop(self.chip, "tone", tone)
        if self.status_text.text() != headline:
            self.status_text.setText(headline)
        if self.status_note.text() != note:
            self.status_note.setText(note)
        self.status_note.setVisible(bool(note))
        self._render_qr(state)
        self._render_buttons(state)
        if relayout:
            QTimer.singleShot(0, self, self._fit)

    def _render_qr(self, state):
        if self.snow:
            view = "snowluma:" + state
            text = "在本机管理页加载QQ" if state in ("snowluma_waiting", "snowluma_confirm") else "本地管理页未运行"
            if self._view != view:
                self._view = view
                self._qr_stamp = None
                self.qr_label.clear()
                self.qr_label.setObjectName("qrEmpty")
                set_prop(self.qr_label, "tone", "off")
                self.qr_label.setText(text)
            self.qr_caption.setText(f"本机地址：{qq_transport.webui_url()}\n不会自动登录或注入其他QQ")
            return
        age = qr_age(self.qrcode_path)
        fresh = age is not None and age < QR_MAX_AGE
        if state == "waiting" and fresh:
            try:
                stamp = os.path.getmtime(self.qrcode_path)
            except OSError:
                stamp = None
            dpr = self.devicePixelRatioF() or 1.0
            if stamp != self._qr_stamp or dpr != self._qr_dpr or self._view != "qr":
                pix = self._load_qr()
                if pix is not None:  # NapCat 正在写文件时可能读到半张图，下一秒再试
                    self._qr_stamp = stamp
                    self._qr_dpr = dpr
                    self._view = "qr"
                    self.qr_label.setObjectName("qrImage")
                    self.qr_label.style().unpolish(self.qr_label)
                    self.qr_label.style().polish(self.qr_label)
                    self.qr_label.setPixmap(pix)
            if self._view == "qr":
                who = self.nick or self.qq or "机器人账号"
                self.qr_caption.setText(f"用「{who}」扫这个码\nQQ {self.qq} · 约 2 分钟自动换新")
                return
        text, caption, tone = {
            "restarting": ("正在生成二维码…", "NapCat 启动后会自动出现", "off"),
            "online": ("✓ 已登录", "机器人 QQ 在线", "ok"),
            "no_napcat": ("NapCat 未运行", "启动机器人后这里会显示二维码", "off"),
            "offline": ("先重启 NapCat", "重启后这里会出现新的二维码", "off"),
            "mismatch": ("先重启 NapCat", "重启后用机器人账号扫码", "off"),
            "waiting": ("二维码已失效", "刚扫过码但没登录成功，或码已作废：在 WebUI 点「重新获取二维码」，或点「重启 NapCat」", "off"),
        }[state]
        view = "empty:" + state
        if self._view != view:
            self._view = view
            self._qr_stamp = None
            self.qr_label.clear()
            self.qr_label.setObjectName("qrEmpty")
            set_prop(self.qr_label, "tone", tone)
            self.qr_label.setText(text)
        self.qr_caption.setText(caption)

    def _load_qr(self):
        pix = QPixmap(self.qrcode_path)
        if pix.isNull():
            return None
        dpr = self.devicePixelRatioF() or 1.0
        target = int((QR_BOX - 16) * dpr)
        factor = math.floor(target / max(pix.width(), 1))
        if factor >= 1:
            # 整数倍放大 + 最近邻：二维码的每个格子大小一致、边缘锐利，手机更好扫
            pix = pix.scaled(pix.width() * factor, pix.height() * factor, Qt.KeepAspectRatio, Qt.FastTransformation)
        else:
            pix = pix.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pix.setDevicePixelRatio(dpr)
        return pix

    def _render_buttons(self, state):
        busy = getattr(self.panel, "busy", False)
        self.start_btn.setVisible(state in ("no_napcat", "snowluma_stopped"))
        self.start_btn.setEnabled(not busy)
        self.confirm_btn.setVisible(self.snow and state == "snowluma_confirm")
        self.confirm_btn.setEnabled(not busy)
        self.restart_btn.setVisible(state in ("waiting", "offline", "mismatch", "restarting", "snowluma_waiting", "snowluma_confirm"))
        self.restart_btn.setEnabled(not busy)
        want_restart_primary = state in ("offline", "mismatch", "snowluma_waiting", "snowluma_confirm")
        label = ("打开 SnowLuma 管理页" if self.snow else "重启 NapCat 并扫码") if want_restart_primary else ("检查接入端" if self.snow else "重启 NapCat")
        if self.restart_btn.text() != label:
            self.restart_btn.setText(label)
        restart_role = "primary" if want_restart_primary else "danger"
        if self.restart_btn.objectName() != restart_role:
            _set_role(self.restart_btn, restart_role)
        self.webui_btn.setEnabled(state not in ("no_napcat", "snowluma_stopped", "restarting"))
        webui_role = "primary" if state in ("waiting", "snowluma_waiting", "snowluma_confirm") and self._view != "qr" else ""
        if self.webui_btn.objectName() != webui_role:
            _set_role(self.webui_btn, webui_role)

    # 动作 ----------------------------------------------------------------
    def _on_webui(self):
        opener = getattr(self.panel, "_open_webui_page", None)
        if callable(opener):
            opener()

    def _on_start(self):
        starter = getattr(self.panel, "start_bot", None)
        if callable(starter):
            starter()
        self._last_probe = 0.0

    def _on_restart(self):
        if self.snow:
            restart = getattr(self.panel, "restart_napcat_for_login", None)
            if callable(restart):
                restart()
            self._on_webui()
            self._last_probe = 0.0
            return
        text = ("只重启机器人用的 NapCat（QQ）进程：\n"
                "· 不会关闭你自己开的 QQ\n"
                "· 不会停止 NoneBot（机器人服务）\n\n"
                "会先试一次免扫码登录（约 1 分钟）；登上了就不用扫码。\n"
                f"没登上，这个窗口会出现新的二维码，请用 {self.account_text} 扫码。\n\n继续吗？")
        if not _ask(self, "重启 NapCat", text):
            return
        restart = getattr(self.panel, "restart_napcat_for_login", None)
        started = time.time()
        if callable(restart) and restart():
            self._restart_requested = True
            self._restart_at = started
            self._qr_stamp = None
            self._view = None
            self._last_probe = 0.0
        self._render()


def open_login_helper(panel, qrcode_path=DEFAULT_QRCODE_PATH, webui_port=6099):
    """控制面板入口：非模态、只开一个；再次调用时把已打开的窗口提到前面。"""
    existing = getattr(panel, "_login_helper", None)
    if existing is not None:
        try:
            if existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return existing
        except RuntimeError:  # 旧窗口已销毁
            pass
    dialog = LoginHelper(panel, qrcode_path, webui_port)
    panel._login_helper = dialog
    dialog.destroyed.connect(lambda *_: setattr(panel, "_login_helper", None))
    dialog.setWindowOpacity(0.0)
    dialog.show()
    QTimer.singleShot(30, dialog, lambda: dialog.setWindowOpacity(1.0))
    return dialog
