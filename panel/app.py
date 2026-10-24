"""桌面窗口：无边框 QWebEngineView 承载本机网页；最小化/关闭/拖动通过 QWebChannel 交给 Qt。

没有 QtWebEngine 时退回 Edge 的 --app 独立窗口模式。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time


def _single_instance():
    """同一时间只开一个面板（Windows 命名互斥量）；已开时返回 None。"""
    if os.name != "nt":
        return True
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\FurinaPanelV2")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def run_qt(url: str, server) -> int:
    from PySide6.QtCore import QFile, QIODevice, QObject, QPoint, Qt, QUrl, Slot
    from PySide6.QtGui import QColor, QIcon
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow

    from . import server as srv
    from .services import assets

    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-driver-bug-workarounds")
    if os.name == "nt":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Furina.ControlPanel.v2")
    app = QApplication(sys.argv)
    app.setApplicationName("芙宁娜控制面板")

    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.ReadOnly):
        srv.EXTRA_STATIC["qwebchannel.js"] = bytes(f.readAll())
        f.close()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
            self.setWindowTitle("芙宁娜控制面板")
            icon = assets.named("icon")
            if icon:
                self.setWindowIcon(QIcon(str(icon)))
            self.resize(1295, 727)
            self.setMinimumSize(1100, 640)
            self.view = QWebEngineView(self)
            page = QWebEnginePage(self.view)
            page.setBackgroundColor(QColor("#0f172a"))
            self.view.setPage(page)
            s = page.settings()
            s.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
            s.setAttribute(QWebEngineSettings.ShowScrollBars, False)
            self.view.setContextMenuPolicy(Qt.NoContextMenu)
            self.setCentralWidget(self.view)
            self.channel = QWebChannel(page)
            self.bridge = Bridge(self)
            self.channel.registerObject("host", self.bridge)
            page.setWebChannel(self.channel)
            self.view.load(QUrl(url))

    class Bridge(QObject):
        def __init__(self, win):
            super().__init__(win)
            self.win = win

        @Slot()
        def minimize(self):
            self.win.showMinimized()

        @Slot()
        def toggleMaximize(self):
            self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

        @Slot()
        def close(self):
            self.win.close()

        @Slot()
        def startMove(self):
            handle = self.win.windowHandle()
            if handle is not None:
                handle.startSystemMove()

        @Slot(str)
        def startResize(self, edge: str):
            edges = {"r": Qt.RightEdge, "b": Qt.BottomEdge, "rb": Qt.RightEdge | Qt.BottomEdge}
            handle = self.win.windowHandle()
            if handle is not None and edge in edges:
                handle.startSystemResize(edges[edge])

    win = Window()
    screen = app.primaryScreen().availableGeometry()
    win.move(screen.center() - QPoint(win.width() // 2, win.height() // 2))
    win.show()
    code = app.exec()
    server.stop()
    return code


def run_browser(url: str, server) -> int:
    """退路：用 Edge 应用窗口打开；Edge 关闭后面板服务也退出。"""
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(exe):
            prof = os.path.join(os.environ.get("LOCALAPPDATA", "."), "FurinaPanelV2")
            proc = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={prof}", "--window-size=1295,760"])
            proc.wait()
            server.stop()
            return 0
    import webbrowser

    webbrowser.open(url)
    print("面板已在浏览器打开：", url, "（关闭此窗口即退出）")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()
    return 0


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m panel")
    ap.add_argument("--serve", action="store_true", help="只启动网页服务（调试用）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--browser", action="store_true", help="不用 Qt 窗口，改用 Edge 应用窗口")
    args = ap.parse_args(argv)

    lock = True if args.serve else _single_instance()
    if lock is None:
        return 0
    from furina.core import paths

    paths.ensure_dirs()
    from . import server as srv
    from .services.control import controller

    controller.start_monitor()
    server = srv.ServerThread(args.host, args.port)
    server.start()
    if not server.wait_ready():
        print("面板服务启动失败")
        return 1
    if args.serve:
        print(f"PANEL_URL {server.url}  token={srv.TOKEN}", flush=True)
        server.join()
        return 0
    if not args.browser:
        try:
            import PySide6.QtWebEngineWidgets  # noqa: F401
        except Exception:
            args.browser = True
    return run_browser(server.url, server) if args.browser else run_qt(server.url, server)
