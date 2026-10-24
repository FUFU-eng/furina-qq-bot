"""Desktop host for the unmodified upstream console, not a replacement game UI."""
from __future__ import annotations
import os
import threading
from PySide6.QtCore import Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QMessageBox, QSizePolicy)
from furina.services import gscore

# 上游控制台的样式大量使用 CSS backdrop-filter（毛玻璃）。Windows 上 Qt WebEngine 的 GPU
# 合成遇到 backdrop-filter 时，鼠标悬停会整块闪烁（混合显卡更明显）。
# 旧做法是关掉 Chromium 的 GPU 合成——闪烁没了，但整页改由 CPU 软件绘制，滚动 / 悬停 / 切菜单
# 都明显卡顿。现在保留 GPU 加速，改为只在本窗口里给控制台加一段显示用样式，把毛玻璃换成
# 不透明底色（上游文件不做任何修改，浏览器打开时仍是原样）。
# 万一某台电脑仍有闪烁：设环境变量 FURINA_WEBENGINE_SAFE=1 回到旧的软件绘制模式。
if os.environ.get("FURINA_WEBENGINE_SAFE") == "1":
    _flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--disable-gpu-compositing" not in _flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (_flags + " --disable-gpu-compositing").strip()

# 只作用于桌面面板内嵌的控制台：去掉毛玻璃（闪烁和卡顿的根源），过渡动画缩短。
PERF_CSS = """
*,*::before,*::after{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}
*,*::before,*::after{transition-duration:.12s!important;animation-duration:.12s!important}
[class*="backdrop-blur"],[class*="glass"]{background-color:rgba(16,20,30,.94)!important}
"""
PERF_JS = """
(function(){
  var css = %s;
  function add(){
    try {
      if (document.getElementById('furina-perf-css')) return true;
      var root = document.head || document.documentElement;
      if (!root) return false;
      var s = document.createElement('style'); s.id = 'furina-perf-css'; s.textContent = css;
      root.appendChild(s); return true;
    } catch (e) { return false; }
  }
  // 文档刚创建时可能连 <html> 都还没有：先等 DOM 就绪再补一次，SPA 切页不会丢。
  add(); document.addEventListener('DOMContentLoaded', add); window.addEventListener('load', add);
})();
"""

CONSOLE_BG = "#0b1018"


def fitted_zoom(width, height, auth_form=False):
    # Upstream login and registration share the /login route; reserve enough
    # CSS viewport for its five-field registration form without changing it.
    return max(0.60 if auth_form else 0.70, min(1.0, width / 1000, height / (840 if auth_form else 660)))


class GameManagementPage(QWidget):
    result = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("gameManagement")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.busy = False
        self.web = None
        self._loaded = False
        self._auto_zoom = True
        self._zoom = 1.0
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(8)

        # ---- 头部卡片：标题 + 状态 + 操作，固定高度，状态变化不会挤动下方控制台 ----
        header = QFrame()
        header.setObjectName("gameHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setFixedHeight(64)
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 8, 12, 8)
        row.setSpacing(8)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title_line = QHBoxLayout()
        title_line.setSpacing(10)
        title = QLabel("游戏管理")
        title.setObjectName("gameTitle")
        title.setToolTip("GenshinUID · StarRailUID · ZZZeroUID · BBBUID\n账号、订阅、签到时间及细分权限均由下方原版控制台管理。")
        self.status = QLabel("检查中")
        self.status.setObjectName("gameChip")
        self.status.setProperty("state", "warn")
        self.status.setTextFormat(Qt.PlainText)
        title_line.addWidget(title)
        title_line.addWidget(self.status, 0, Qt.AlignVCenter)
        title_line.addStretch(1)
        subtitle = QLabel("GsCore 官方控制台 · 原神 / 星铁 / 绝区零 / 崩坏3 · 群内仍受「功能权限」开关约束")
        subtitle.setObjectName("gameSubtitle")
        text_box.addLayout(title_line)
        text_box.addWidget(subtitle)
        row.addLayout(text_box, 1)

        self.start_button = self._button("启动核心", self.start, primary=True,
                                         tip="后台启动本机 GsCore；不会重启 QQ / NapCat")
        self.refresh_button = self._button("刷新", self.reload, tip="重新检查核心状态并刷新控制台")
        code = self._button("本机注册码", self.registration, tip="首次在控制台注册账号时使用")
        for widget in (self.start_button, self.refresh_button, code):
            row.addWidget(widget)

        zoom_box = QFrame()
        zoom_box.setObjectName("gameZoomGroup")
        zoom_box.setAttribute(Qt.WA_StyledBackground, True)
        zl = QHBoxLayout(zoom_box)
        zl.setContentsMargins(3, 2, 3, 2)
        zl.setSpacing(0)
        smaller = self._button("−", lambda: self.adjust_zoom(-0.1), zoom=True, tip="缩小控制台页面（不改变系统缩放）")
        smaller.setFixedWidth(30)
        self.zoom_button = self._button("适配 100%", self.fit_console, zoom=True,
                                        tip="恢复自动适配；登录/注册页优先完整显示")
        self.zoom_button.setMinimumWidth(78)
        larger = self._button("+", lambda: self.adjust_zoom(0.1), zoom=True, tip="放大控制台页面")
        larger.setFixedWidth(30)
        for widget in (smaller, self.zoom_button, larger):
            zl.addWidget(widget)
        row.addWidget(zoom_box)
        self.maximize_button = self._button("最大化", self.toggle_workspace, tip="最大化 / 还原面板窗口")
        browser = self._button("浏览器打开", self.open_browser, tip="由你主动打开同一个本机官方控制台；不会重新登录 QQ")
        row.addWidget(self.maximize_button)
        row.addWidget(browser)
        root.addWidget(header)

        # ---- 控制台卡片：不透明底色，内嵌原版网页 ----
        self.console = QFrame()
        self.console.setObjectName("gameConsole")
        self.console.setAttribute(Qt.WA_StyledBackground, True)
        self.host = QVBoxLayout(self.console)
        self.host.setContentsMargins(1, 1, 1, 1)
        self.host.setSpacing(0)
        root.addWidget(self.console, 1)
        self.placeholder = QLabel("官方控制台将在此加载。\n先绑定 UID，再私聊机器人发送「扫码登录」。\nys帮助 / bt帮助 / zzz帮助 / bbb帮助")
        self.placeholder.setObjectName("gamePlaceholder")
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.host.addWidget(self.placeholder)
        self.result.connect(self._result)

    def _button(self, text, callback, primary=False, zoom=False, tip=""):
        button = QPushButton(text)
        button.setObjectName("gameZoomBtn" if zoom else ("gamePrimaryBtn" if primary else "gameBtn"))
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda *_: callback())
        if tip:
            button.setToolTip(tip)
        return button

    def _set_status(self, text, state="warn", tip=""):
        self.status.setText(text)
        self.status.setToolTip(tip or text)
        if self.status.property("state") != state:
            self.status.setProperty("state", state)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def _work(self, start=False):
        if self.busy: return
        self.busy = True; self.start_button.setEnabled(False); self.refresh_button.setEnabled(False)
        self._set_status("启动中…" if start else "检查中…", "warn")
        def worker():
            try: self.result.emit(gscore.ensure_running() if start else gscore.snapshot(), "")
            except Exception as exc: self.result.emit(None, str(exc))
        threading.Thread(target=worker, daemon=True, name="gscore-panel-status").start()

    def reload(self, *_, **kwargs):
        # 手动点「刷新」时连同控制台页面一起刷新；切页触发的检查不重载页面。
        self._reload_page = self.web is not None and self._loaded
        self._work()
    def start(self, *_): self._work(True)

    def _result(self, state, error):
        self.busy = False; self.refresh_button.setEnabled(True)
        self.start_button.setEnabled(not state or not state.get("running"))
        self.start_button.setVisible(not state or not state.get("running"))
        if error:
            self._set_status("出错", "bad", error); return
        if state["port_conflict"]:
            self._set_status("端口冲突", "bad", "端口冲突：8765 被其他进程占用；未操作该进程。"); return
        if not state["ready"]:
            if state["running"]:
                self._set_status("初始化中", "warn", "核心正在初始化，请稍后点「刷新」。")
            else:
                self._set_status("未运行", "bad", "核心未运行。点击「启动核心」；不会重启 QQ / NapCat。")
            return
        self._set_status("核心在线", "ok", "核心在线 · 本机 127.0.0.1:8765 · 下方为上游官方控制台")
        if self.web is None:
            try:
                from PySide6.QtWebEngineWidgets import QWebEngineView
                from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
                self.web = QWebEngineView(self.console)
                self.web.setFocusPolicy(Qt.StrongFocus)
                self.web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                self.web.setMinimumSize(0, 0)
                self.web.setAttribute(Qt.WA_OpaquePaintEvent, True)
                self.web.setStyleSheet(f"background:{CONSOLE_BG};")
                self.profile = QWebEngineProfile("furina-gscore", self)
                storage = gscore.paths.DATA_DIR / "gscore-webview"
                storage.mkdir(parents=True, exist_ok=True)
                self.profile.setPersistentStoragePath(str(storage))
                if os.environ.get("FURINA_WEBENGINE_SAFE") != "1":
                    import json as _json
                    from PySide6.QtWebEngineCore import QWebEngineScript
                    script = QWebEngineScript()
                    script.setName("furina-perf")
                    script.setInjectionPoint(QWebEngineScript.DocumentCreation)
                    script.setWorldId(QWebEngineScript.ApplicationWorld)
                    script.setRunsOnSubFrames(True)
                    script.setSourceCode(PERF_JS % _json.dumps(PERF_CSS))
                    self.profile.scripts().insert(script)
                page = QWebEnginePage(self.profile, self.web)
                page.setBackgroundColor(QColor(CONSOLE_BG))
                self.web.setPage(page)
                self.web.loadFinished.connect(self._page_loaded)
                self.web.urlChanged.connect(lambda *_: QTimer.singleShot(0, self._apply_zoom))
                self.host.addWidget(self.web, 1); self.placeholder.hide()
            except ImportError:
                self.placeholder.setText("当前 PySide6 缺少 WebEngine。请点「浏览器打开」使用同一个官方控制台；没有自制页面回退。")
                return
        if not self._loaded or not self.web.url().isValid():
            self.web.setUrl(QUrl(gscore.CONSOLE_URL)); self._loaded = True
        elif getattr(self, "_reload_page", False):
            self.web.reload()
        self._reload_page = False
        QTimer.singleShot(0, self._apply_zoom)

    def _page_loaded(self, ok):
        if not ok:
            self._set_status("加载失败", "bad", "控制台页面加载失败，可用「浏览器打开」检查本机核心。")
        self._apply_zoom()

    def _apply_zoom(self):
        if self.web is None: return
        if self._auto_zoom:
            # Scale the browser viewport; upstream assets are never changed.
            route = self.web.url().fragment().split("?")[0].strip("/")
            self._zoom = fitted_zoom(self.web.width(), self.web.height(), route in ("login", "register"))
        # 缩放没变就不重设：反复 setZoomFactor 会让整页重新排版并闪一下。
        if abs(self.web.zoomFactor() - self._zoom) > 0.001:
            self.web.setZoomFactor(self._zoom)
        self.zoom_button.setText(("适配 " if self._auto_zoom else "") + f"{self._zoom:.0%}")

    def adjust_zoom(self, delta):
        self._auto_zoom = False; self._zoom = max(0.5, min(1.5, round(self._zoom + delta, 2)))
        self._apply_zoom()

    def fit_console(self):
        self._auto_zoom = True; self._apply_zoom()

    def toggle_workspace(self):
        window = self.window()
        if window.isMaximized(): window.showNormal()
        else: window.showMaximized()
        self.maximize_button.setText("还原窗口" if window.isMaximized() else "最大化")

    def open_browser(self):
        QDesktopServices.openUrl(QUrl(gscore.CONSOLE_URL))

    def registration(self):
        try:
            code = gscore.local_registration_code()
            if not code: raise ValueError("核心尚未生成注册码，请先启动核心。")
            QMessageBox.information(self, "本机首次注册", "在下方官方控制台选择注册，自行设置账号和密码。\n首个注册账号具有管理员权限。\n\n本机注册码：\n" + code + "\n\n请勿转发此码。程序不会复制到剪贴板或写入日志。")
        except Exception as exc:
            QMessageBox.warning(self, "注册码暂不可用", str(exc))

    def can_leave(self): return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 拖动改变窗口大小时每一帧都重设缩放会让整个控制台反复重新排版；停手 120ms 后再算一次。
        timer = getattr(self, "_zoom_timer", None)
        if timer is None:
            timer = self._zoom_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(120)
            timer.timeout.connect(self._apply_zoom)
        timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.maximize_button.setText("还原窗口" if self.window().isMaximized() else "最大化")
        # 已加载过就不再每次切回都重查（避免状态文字反复变化引起重排）；需要时点「刷新」。
        if self.web is None or not self._loaded:
            self.reload()
