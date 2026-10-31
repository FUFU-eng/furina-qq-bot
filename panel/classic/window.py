"""芙宁娜控制面板 - PySide6 原生版（米哈游启动器式高还原布局）

Qt 桌面界面；游戏管理页嵌入上游官方 QWebEngineView 控制台。
布局参考米哈游启动器：左侧 16:9 深色信息栏 + 角色卡 + 标签页 + 功能列表，
右侧整幅视觉，右下角金色主按钮，圆点位于按钮左侧。
"""
# 拆分说明：原 5700 行的单文件已按功能拆开——
#   common.py   依赖导入、小工具、路径常量     theme.py   统一配色与通用样式
#   widgets.py  自绘小控件                     pages/*.py 各页面/功能的方法（mixin）
# 这里只保留窗口骨架：初始化、整体布局、侧栏与切页、全局样式表、窗口拖拽。
from .common import *  # noqa: F401,F403
from .widgets import *  # noqa: F401,F403
from .pages.wallpaper import WallpaperMixin
from .pages.home import HomePageMixin
from .pages.permissions import PermissionsPageMixin
from .pages.info import InfoPagesMixin
from .pages.roles import RolePageMixin
from .pages.stats import StatsPageMixin
from .pages.control import BotControlMixin
from .pages.logs import LogsMixin
from .pages.tools import ToolsMixin
from .pages.bili import BiliPageMixin
from .pages.spend import SpendPageMixin
from .pages.usage_rank import UsageRankPageMixin
from .pages.pics import PicsPageMixin
from PySide6.QtWidgets import QScrollArea


class _EdgeHandle(QWidget):
    """无边框窗口的拖动缩放条：优先交给系统缩放（Win 下手感和普通窗口一样），不支持时手动改几何。"""

    GRIP = 6      # 边条厚度
    CORNER = 14   # 角块边长
    E = Qt.Edge
    ALL = (E.LeftEdge, E.RightEdge, E.TopEdge, E.BottomEdge,
           E.LeftEdge | E.TopEdge, E.RightEdge | E.TopEdge, E.LeftEdge | E.BottomEdge, E.RightEdge | E.BottomEdge)

    def __init__(self, win, edges):
        super().__init__(win)
        self._win, self._edges, self._start = win, edges, None
        e = Qt.Edge
        if edges in (e.LeftEdge, e.RightEdge):
            cursor = Qt.SizeHorCursor
        elif edges in (e.TopEdge, e.BottomEdge):
            cursor = Qt.SizeVerCursor
        elif edges in (e.LeftEdge | e.TopEdge, e.RightEdge | e.BottomEdge):
            cursor = Qt.SizeFDiagCursor
        else:
            cursor = Qt.SizeBDiagCursor
        self.setCursor(cursor)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def place(self, w, h):
        g, c, e = self.GRIP, self.CORNER, Qt.Edge
        geo = {
            e.LeftEdge: (0, c, g, h - 2 * c), e.RightEdge: (w - g, c, g, h - 2 * c),
            e.TopEdge: (c, 0, w - 2 * c, g), e.BottomEdge: (c, h - g, w - 2 * c, g),
            e.LeftEdge | e.TopEdge: (0, 0, c, c), e.RightEdge | e.TopEdge: (w - c, 0, c, c),
            e.LeftEdge | e.BottomEdge: (0, h - c, c, c), e.RightEdge | e.BottomEdge: (w - c, h - c, c, c),
        }[self._edges]
        self.setGeometry(*geo)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self._win.isMaximized():
            return super().mousePressEvent(event)
        handle = self._win.windowHandle()
        if handle is not None and handle.startSystemResize(self._edges):
            event.accept(); return
        self._start = (event.globalPosition().toPoint(), QRect(self._win.geometry()))
        event.accept()

    def mouseMoveEvent(self, event):
        if self._start is None or not (event.buttons() & Qt.LeftButton):
            return super().mouseMoveEvent(event)
        origin, geo = self._start
        d = event.globalPosition().toPoint() - origin
        r = QRect(geo)
        mw, mh = self._win.minimumWidth(), self._win.minimumHeight()
        if self._edges & Qt.Edge.LeftEdge:
            r.setLeft(min(geo.right() - mw + 1, geo.left() + d.x()))
        if self._edges & Qt.Edge.RightEdge:
            r.setRight(max(geo.left() + mw - 1, geo.right() + d.x()))
        if self._edges & Qt.Edge.TopEdge:
            r.setTop(min(geo.bottom() - mh + 1, geo.top() + d.y()))
        if self._edges & Qt.Edge.BottomEdge:
            r.setBottom(max(geo.top() + mh - 1, geo.bottom() + d.y()))
        self._win.setGeometry(r)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._start = None
        super().mouseReleaseEvent(event)


class PanelWindow(WallpaperMixin, HomePageMixin, PermissionsPageMixin, InfoPagesMixin, RolePageMixin, StatsPageMixin, BotControlMixin, LogsMixin, ToolsMixin, BiliPageMixin, SpendPageMixin, PicsPageMixin, UsageRankPageMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("芙宁娜控制面板")
        self.setWindowIcon(QIcon(BOT_ICON))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        # Avoid the layered-window path for the native WebEngine host on Windows.
        # Wallpaper/translucent cards are still painted inside this window, not over the desktop.
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#0f172a"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        # 默认 1320×880（与 Brana AI 默认窗口一致），屏幕放不下时按可用区域收缩，并居中显示。
        available = self.screen().availableGeometry()
        self.setMinimumSize(min(MIN_WINDOW_SIZE[0], available.width()), min(MIN_WINDOW_SIZE[1], available.height()))
        width = min(DEFAULT_WINDOW_SIZE[0], available.width() - 24)
        height = min(DEFAULT_WINDOW_SIZE[1], available.height() - 24)
        self.resize(width, height)
        self.move(available.x() + (available.width() - width) // 2,
                  available.y() + (available.height() - height) // 2)
        self._restore_geometry()
        self.setStyleSheet(self._qss())

        self.bg_index = 0
        self.bot_running = False
        self.start_time = None
        self.busy = False
        self._qq_online = None  # None = 状态未知，不以端口或查询异常推定在线
        self._health = Health("unknown", "等待状态检查")
        self._status_check_inflight = False
        self._login_generation = 0
        self._offline_fail_count = 0  # 连续检测到离线的次数（用于防抖，满4次才确认掉线）
        self._last_offline_toast_time = 0.0  # 离线浮层提示冷却时间戳（避免频繁弹窗打扰）
        self.filter_group = ""
        self.filter_sort = "letter"
        self._revealed_qq = set()
        self.sidebar_collapsed = False
        self.wallpaper_library = []  # 壁纸库里的全部壁纸
        self.wallpaper_list = []  # 正在显示 / 轮播的壁纸（壁纸库的子集）
        self._wallpaper_art_cache = {}  # 路径 -> (文件戳, 缩略图 QIcon, 预览 QPixmap)，后台生成
        self._wallpaper_art_inflight = set()
        self._wallpaper_preview_wanted = ""
        self._bg_prefetch = None  # (路径, 文件戳, QImage)：后台提前解码好的下一张轮播壁纸
        self.wallpaper_index = 0
        self.wallpaper_interval = 30
        self.current_wallpaper_path = ""
        self.wallpaper_timer = QTimer(self)
        self.wallpaper_timer.timeout.connect(self._next_wallpaper)
        self.home_refresh_timer = QTimer(self)
        self.home_refresh_timer.timeout.connect(self._refresh_home_cards)
        self.home_refresh_timer.start(5000)
        self._last_group_sig = ""
        self.group_refresh_timer = QTimer(self)
        self.group_refresh_timer.timeout.connect(self._auto_refresh_groups)
        self.group_refresh_timer.start(15000)
        # 不再高频抓取窗口截图计算 logo 亮度；切换壁纸时更新一次即可，避免主线程卡顿。
        self.logo_adaptive_timer = None
        self._drag_pos = None
        self._icon_cache = {}
        self._page_mode_home = None
        self._active_tab = 0
        # 群列表和模型用量都走轻量缓存，避免切页时在主线程等待网络或扫描大日志。
        self._napcat_groups_cache = {}
        self._napcat_groups_ts = 0.0
        self._group_refresh_inflight = False
        self._model_usage_day = ""
        self._model_usage_file_pos = 0
        self._model_usage_counts = {}
        self._page_refresh_serial = 0

        self._build_ui()
        self.resize_grip = QSizeGrip(self)
        self.resize_grip.setToolTip("拖动这里调整窗口大小；双击标题栏可最大化")
        self.resize_grip.setFixedSize(16, 16)
        self.resize_grip.move(self.width() - 18, self.height() - 18)
        self.resize_grip.raise_()
        # 无边框窗口没有系统边框可拖：四条边和四个角各放一条透明的拖动条
        self._edge_handles = [_EdgeHandle(self, edges) for edges in _EdgeHandle.ALL]
        self._layout_edge_handles()
        self._init_backend()
        self.load_bg(0)
        self._load_custom_background()
        # 构建完成后显式应用首页模式：避免 windowBody 的默认深色背景盖住首页壁纸。
        self._apply_page_mode(0, force=True)

        QTimer.singleShot(300, self.check_status)
        QTimer.singleShot(1000, self.update_uptime)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.check_status)
        self.status_timer.start(5000)

    def nativeEvent(self, eventType, message):
        """处理Windows任务栏图标点击最小化/还原"""
        try:
            if sys.platform == 'win32':
                et = bytes(eventType) if not isinstance(eventType, bytes) else eventType
                if et == b'windows_generic_MSG':
                    import ctypes
                    from ctypes import wintypes
                    class MSG(ctypes.Structure):
                        _fields_ = [
                            ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                            ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                            ("time", wintypes.DWORD), ("pt", wintypes.POINT),
                        ]
                    msg = MSG.from_address(int(message))
                    # WM_SYSCOMMAND = 0x0112
                    if msg.message == 0x0112:
                        cmd = msg.wParam & 0xFFF0
                        if cmd == 0xF020:  # SC_MINIMIZE
                            self.showMinimized()
                            return (True, 0)
                        elif cmd == 0xF120:  # SC_RESTORE
                            self.showNormal()
                            self.activateWindow()
                            return (True, 0)
        except Exception as e:
            try:
                with open(_v2.log_path('native_debug.log'), 'a', encoding='utf-8') as f:
                    f.write(f'nativeEvent error: {e}\n')
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def _show_dialog(self, dlg):
        """显示对话框，先0透明度渲染再淡入，防止白闪。"""
        dlg.setWindowOpacity(0.0)
        QTimer.singleShot(30, lambda: dlg.setWindowOpacity(1.0))
        dlg.exec()

    def _build_ui(self):
        self.bg_layer = BackgroundLayer(self)
        self.bg_layer.lower()
        self.bg_layer.setGeometry(0, 0, self.width(), self.height())

        self.window_body = QWidget(self)
        self.window_body.setObjectName("windowBody")
        self.window_body.setAttribute(Qt.WA_StyledBackground, True)
        self.window_body.setGeometry(0, 0, self.width(), self.height())

        self.sidebar_top_fill = QWidget(self.window_body)
        self.sidebar_top_fill.setObjectName("sidebarTopFill")
        self.sidebar_top_fill.setAttribute(Qt.WA_StyledBackground, True)
        self.sidebar_top_fill.setGeometry(0, 0, 190, 74)
        self.sidebar_top_fill.lower()

        root = QVBoxLayout(self.window_body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title_bar = QWidget()
        self.title_bar = title_bar
        title_bar.setObjectName("titleBar")
        title_bar.setAttribute(Qt.WA_StyledBackground, True)
        title_bar.setFixedHeight(74)
        self.title_layout = QHBoxLayout(title_bar)
        self.title_layout.setContentsMargins(18, 18, 10, 0)
        self.title_layout.setSpacing(12)

        icon = QLabel()
        icon.setObjectName("brandIcon")
        icon.setFixedSize(36, 36)
        bot_pix = QPixmap(BOT_ICON)
        if bot_pix.isNull():
            bot_pix = QPixmap(36, 36)
            bot_pix.fill(QColor("#ffd166"))
        else:
            bot_pix = bot_pix.scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        icon.setPixmap(bot_pix)
        self.brand_icon = icon
        self.title_layout.addWidget(icon)
        self.brand_title = self._label("FURINA PANEL", "titleText", 13)
        self.title_layout.addWidget(self.brand_title)

        self.title_layout.addStretch(1)

        self.min_btn = QPushButton("–")
        self.min_btn.setToolTip("最小化")
        self.min_btn.setObjectName("winBtn")
        self.min_btn.setFixedSize(34, 30)
        self.min_btn.clicked.connect(self.showMinimized)
        self.title_layout.addWidget(self.min_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setToolTip("最大化 / 还原（也可双击标题栏）")
        self.max_btn.setObjectName("winBtn")
        self.max_btn.setFixedSize(34, 30)
        self.max_btn.clicked.connect(self._toggle_maximized)
        self.title_layout.addWidget(self.max_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setToolTip("关闭")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedSize(34, 30)
        self.close_btn.clicked.connect(self.close)
        self.title_layout.addWidget(self.close_btn)

        root.addWidget(title_bar)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        root.addLayout(content, 1)

        content.addWidget(self._build_left_panel())
        self._update_sidebar_top_fill()

        self.art = ArtPanel()
        self.art.bgClicked.connect(self.load_bg)
        content.addWidget(self.art, 1)

        self.start_btn = StartButton()
        self.start_btn.configure(
            self._make_icon("play_btn", "#000000"),
            self._make_icon("play_btn_hover", "#ffd700"),
            "#ffd700", "#212429", "#111111", "#ffd700",
        )
        self.start_btn.clicked.connect(self.toggle_bot)
        self.start_btn.setParent(self.art)
        self.art.start_button = self.start_btn
        self.art.resizeEvent = self._art_resized
        self._place_start_button()

    def _art_resized(self, event):
        self._place_start_button()
        ArtPanel.resizeEvent(self.art, event)

    def _place_start_button(self):
        w = self.art.width()
        h = self.art.height()
        sx = w - self.start_btn.width() - 56
        sy = h - self.start_btn.height() - 36
        self.start_btn.move(sx, sy)
        self.art.update()

    def _label(self, text, obj, size=10):
        lbl = QLabel(text)
        lbl.setObjectName(obj)
        lbl.setFont(QFont("Microsoft YaHei", size))
        return lbl

    def _shadow(self, widget, blur=30, alpha=120):
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur)
        effect.setOffset(0, 4)
        effect.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(effect)

    def _make_icon(self, kind, color):
        cache_key = (kind, color)
        cached = getattr(self, "_icon_cache", {}).get(cache_key)
        if cached is not None:
            return cached
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(64 / 26.0, 64 / 26.0)
        painter.setPen(QPen(QColor(color), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if kind == "features":
            for x, y in ((5, 5), (15, 5), (5, 15), (15, 15)):
                painter.drawRoundedRect(QRect(x, y, 6, 6), 1, 1)
        elif kind == "games":
            painter.drawRoundedRect(QRectF(3, 7, 20, 13), 4, 4)
            painter.drawLine(7, 13, 13, 13)
            painter.drawLine(10, 10, 10, 16)
            painter.drawEllipse(QRectF(17, 11, 1.5, 1.5))
            painter.drawEllipse(QRectF(19, 15, 1.5, 1.5))
        elif kind == "commands":
            painter.drawRoundedRect(QRectF(3.5, 4.5, 19, 17), 3.5, 3.5)
            painter.drawLine(7.5, 9.5, 10.5, 12.0)
            painter.drawLine(10.5, 12.0, 7.5, 14.5)
            painter.drawLine(12.2, 14.8, 16.5, 14.8)
            painter.drawLine(7.5, 18.0, 18.5, 18.0)
        elif kind == "logs":
            painter.drawRoundedRect(QRectF(3, 6, 20, 14), 3, 3)
            painter.drawLine(8, 11, 11, 14)
            painter.drawLine(11, 14, 8, 17)
            painter.drawLine(14, 17, 18, 17)
        elif kind == "prompt":
            painter.drawRoundedRect(QRectF(3, 5, 20, 15), 5, 5)
            painter.drawEllipse(QRect(9, 9, 4, 4))
            painter.drawEllipse(QRect(15, 9, 4, 4))
        elif kind == "role":
            painter.drawEllipse(QRect(9, 4, 8, 8))
            painter.drawRoundedRect(QRectF(5, 14, 16, 10), 4, 4)
        elif kind == "home":
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            house = QPainterPath()
            house.moveTo(13, 3)
            house.lineTo(24, 13)
            house.lineTo(21, 13)
            house.lineTo(21, 22)
            house.lineTo(5, 22)
            house.lineTo(5, 13)
            house.lineTo(2, 13)
            house.closeSubpath()
            painter.drawPath(house)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.drawRoundedRect(QRectF(11, 15, 4, 7), 1, 1)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        elif kind == "filter":
            painter.drawLine(5, 5, 21, 5)
            painter.drawLine(7, 10, 19, 10)
            painter.drawLine(9, 15, 17, 15)
            painter.drawLine(11, 20, 15, 20)
        elif kind == "play":
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            path = QPainterPath()
            path.moveTo(8, 6)
            path.lineTo(21, 13)
            path.lineTo(8, 20)
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "play_btn":
            painter.setBrush(QColor("#000000"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(2, 2, 22, 22))
            painter.setBrush(QColor("#ffd700"))
            tri = QPainterPath()
            tri.moveTo(11.5, 10.8)
            tri.lineTo(14.8, 13)
            tri.lineTo(11.5, 15.2)
            tri.closeSubpath()
            sk = QPainterPathStroker()
            sk.setWidth(3.7); sk.setCapStyle(Qt.RoundCap); sk.setJoinStyle(Qt.RoundJoin)
            painter.drawPath(tri.united(sk.createStroke(tri)))
        elif kind == "play_btn_hover":
            painter.setBrush(QColor("#ffd700"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(2, 2, 22, 22))
            painter.setBrush(QColor("#111111"))
            tri = QPainterPath()
            tri.moveTo(11.5, 10.8)
            tri.lineTo(14.8, 13)
            tri.lineTo(11.5, 15.2)
            tri.closeSubpath()
            sk = QPainterPathStroker()
            sk.setWidth(3.7); sk.setCapStyle(Qt.RoundCap); sk.setJoinStyle(Qt.RoundJoin)
            painter.drawPath(tri.united(sk.createStroke(tri)))
        elif kind == "stop_btn":
            painter.setBrush(QColor("#111111"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(2, 2, 22, 22))
            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(QRectF(9, 9, 8, 8), 2, 2)
        elif kind == "stop_btn_hover":
            painter.setBrush(QColor("#ffd700"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(2, 2, 22, 22))
            painter.setBrush(QColor("#111111"))
            painter.drawRoundedRect(QRectF(9, 9, 8, 8), 2, 2)
        elif kind == "permissions":
            painter.drawRoundedRect(QRectF(6, 4, 14, 18), 4, 4)
            painter.drawLine(11, 12, 14, 15)
            painter.drawLine(14, 15, 18, 9)
        elif kind == "stop":
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(7, 7, 12, 12), 2, 2)
        elif kind == "sidebar":
            painter.drawRoundedRect(QRectF(3, 4, 20, 18), 3, 3)
            painter.drawLine(10, 4, 10, 22)
            painter.setBrush(QColor(color))
            painter.drawRect(QRectF(5, 6, 4, 14))
        elif kind == "rest":
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(7, 6, 4, 14), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(15, 6, 4, 14), 1.5, 1.5)
        elif kind == "stats":
            painter.drawLine(4, 20, 22, 20)
            painter.drawLine(7, 19, 7, 9)
            painter.drawLine(13, 19, 13, 5)
            painter.drawLine(19, 19, 19, 13)
        elif kind == "bili":  # 小电视
            painter.drawRoundedRect(QRectF(3, 8, 20, 14), 3.5, 3.5)
            painter.drawLine(8, 3, 11, 8)
            painter.drawLine(18, 3, 15, 8)
            painter.drawLine(9, 13, 9, 16)
            painter.drawLine(17, 13, 17, 16)
        elif kind == "pics":  # 相框 + 山 + 太阳
            painter.drawRoundedRect(QRectF(3, 5, 20, 16), 3, 3)
            painter.drawLine(5, 19, 11, 12)
            painter.drawLine(11, 12, 15, 16)
            painter.drawLine(14, 15, 17, 12)
            painter.drawLine(17, 12, 21, 17)
            painter.drawEllipse(QRectF(15, 7, 4, 4))
        elif kind == "usage":  # 三个人 + 高低不一的柱
            painter.drawEllipse(QRectF(4, 5, 5, 5))
            painter.drawEllipse(QRectF(10.5, 3, 5, 5))
            painter.drawEllipse(QRectF(17, 7, 5, 5))
            painter.drawLine(6, 22, 6, 13)
            painter.drawLine(13, 22, 13, 11)
            painter.drawLine(19, 22, 19, 15)
            painter.drawLine(3, 22, 23, 22)
        elif kind == "spend":  # 硬币 + ¥
            painter.drawEllipse(QRectF(3, 3, 20, 20))
            painter.drawLine(9, 8, 13, 13)
            painter.drawLine(17, 8, 13, 13)
            painter.drawLine(13, 13, 13, 19)
            painter.drawLine(9, 14, 17, 14)
        painter.end()
        icon = QIcon(pix)
        getattr(self, "_icon_cache", {})[cache_key] = icon
        return icon

    def _build_left_panel(self):
        panel = QFrame()
        self.left_panel = panel
        panel.setObjectName("leftWrap")
        panel.setFixedWidth(600)
        root_layout = QHBoxLayout(panel)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 左侧导航栏（完全按示例样式：深靛蓝 + 圆角选中块 + 彩色图标）
        rail = QWidget()
        self.rail = rail
        rail.setObjectName("leftRail")
        rail.setAttribute(Qt.WA_StyledBackground, True)
        rail.setFixedWidth(190)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(12, 6, 12, 14)
        rail_layout.setSpacing(2)

        self.rail_group = QButtonGroup(self)
        self.rail_group.setExclusive(True)
        self.rail_kinds = []
        self.rail_names = []
        self.rail_buttons = []
        # 顺序 = 页面栈顺序（按钮序号即页面序号）；侧栏里按 RAIL_GROUPS 分组摆放
        specs = [
            ("home", "主页"),
            ("features", "功能清单"),
            ("commands", "指令大全"),
            ("permissions", "功能权限"),
            ("logs", "详情日志"),
            ("role", "角色管理"),
            ("stats", "回复次数"),
            ("games", "游戏管理"),
            ("bili", "B站生活"),
            ("spend", "模型花费"),
            ("pics", "图片筛选"),
            ("usage", "群友活跃"),
        ]
        for i, (kind, name) in enumerate(specs):
            btn = QPushButton(name)
            btn.setObjectName("navItem")
            btn.setFixedHeight(40)
            btn.setIconSize(QSize(20, 20))
            btn.setFont(QFont("Microsoft YaHei", 13))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda checked=False, idx=i: self.show_tab(idx))
            self.rail_group.addButton(btn, i)
            self.rail_kinds.append(kind)
            self.rail_names.append(name)
            self.rail_buttons.append(btn)
        self.rail_group_labels = []
        for title, kinds in RAIL_GROUPS:
            head = QLabel(title)
            head.setObjectName("navGroup")
            self.rail_group_labels.append(head)
            rail_layout.addWidget(head)
            for kind in kinds:
                rail_layout.addWidget(self.rail_buttons[self.rail_kinds.index(kind)])
        self.rail_group.buttonToggled.connect(lambda *_: self._refresh_rail_icons())
        self._refresh_rail_icons()
        rail_layout.addStretch(1)

        # 底部：运行状态 + 折叠按钮，收在一张小卡片里
        self.rail_card = QWidget()
        self.rail_card.setObjectName("railCard")
        self.rail_card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(self.rail_card)
        card_layout.setContentsMargins(6, 6, 6, 6)
        self.rail_bottom = QWidget()
        bottom_layout = QHBoxLayout(self.rail_bottom)
        bottom_layout.setContentsMargins(6, 0, 0, 0)
        bottom_layout.setSpacing(8)
        self.rail_status_dot = QLabel("●")
        self.rail_status_dot.setStyleSheet("color: #ef4444;")
        self.rail_status_dot.setFont(QFont("Microsoft YaHei", 10))
        self.rail_status_text = StatusLabel("机器人未运行")
        self.rail_status_text.setObjectName("railStatusText")
        self.rail_status_text.setFont(QFont("Microsoft YaHei", 12))
        self.rail_status_text.setCursor(Qt.PointingHandCursor)
        self.rail_status_text.clicked.connect(self.open_login_helper)
        bottom_layout.addWidget(self.rail_status_dot)
        bottom_layout.addWidget(self.rail_status_text, 1)

        self.collapse_btn = QPushButton()
        self.collapse_btn.setObjectName("collapseBtn")
        self.collapse_btn.setFixedSize(32, 32)
        self.collapse_btn.setIcon(self._make_icon("sidebar", "#94a3b8"))
        self.collapse_btn.setIconSize(QSize(18, 18))
        self.collapse_btn.setToolTip("折叠 / 展开侧边栏")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.clicked.connect(self.toggle_sidebar)
        bottom_layout.addWidget(self.collapse_btn, 0, Qt.AlignRight)
        card_layout.addWidget(self.rail_bottom)
        rail_layout.addWidget(self.rail_card)

        root_layout.addWidget(rail)

        # 右侧详情面板
        detail = QFrame()
        self.detail_panel = detail
        detail.setObjectName("detailPanel")
        detail.setAttribute(Qt.WA_StyledBackground, True)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(22, 26, 18, 20)
        detail_layout.setSpacing(0)

        # 首页专属头部：功能页隐藏整个容器，避免留下空白和首页装饰。
        self.home_header = QWidget()
        home_header_layout = QVBoxLayout(self.home_header)
        home_header_layout.setContentsMargins(0, 6, 0, 16)
        home_header_layout.setSpacing(10)

        self.furina_logo_label = QLabel()
        self.furina_logo_label.setObjectName("furinaLogo")
        svg_renderer = QSvgRenderer(LOGO_SVG)
        if svg_renderer.isValid():
            # 高DPI渲染：2倍分辨率
            target_w = 220
            target_h = int(target_w * 1006 / 2000)
            pm = QPixmap(target_w * 2, target_h * 2)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            svg_renderer.render(p)
            p.end()
            pm.setDevicePixelRatio(2)
            self.furina_logo_label.setPixmap(pm)
            self.furina_logo_label.setFixedHeight(target_h + 4)
        self.furina_logo_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._logo_effect = QGraphicsOpacityEffect(self.furina_logo_label)
        self._logo_effect.setOpacity(1.0)
        self.furina_logo_label.setGraphicsEffect(self._logo_effect)
        self._logo_anim = QPropertyAnimation(self._logo_effect, b"opacity", self)
        self._current_logo_path = None
        home_header_layout.addWidget(self.furina_logo_label)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.stat_uptime = QLabel("运行时长 --")
        self.stat_uptime.setObjectName("uptimeLabel")
        self.stat_uptime.setFont(QFont("Microsoft YaHei", 11))
        self.stat_uptime.setStyleSheet("color: #ffb347;")
        status_row.addWidget(self.stat_uptime)
        status_row.addStretch(1)
        home_header_layout.addLayout(status_row)
        detail_layout.addWidget(self.home_header)

        # 功能页统一的页标题（主页和游戏管理页不显示）；页面自己的说明和操作按钮紧跟在下面
        self.page_header = QWidget()
        ph = QHBoxLayout(self.page_header)
        ph.setContentsMargins(2, 6, 0, 12)
        ph.setSpacing(10)
        self.page_heading = QLabel("")
        self.page_heading.setObjectName("pageHeading")
        ph.addWidget(self.page_heading)
        ph.addStretch(1)
        self.page_header.hide()
        detail_layout.addWidget(self.page_header)

        self.stack = QStackedWidget()
        self.stack.setObjectName("stack")
        self.stack.addWidget(self._build_home_page())
        self.stack.addWidget(self._build_feature_page())
        self.stack.addWidget(self._build_commands_page())
        self.stack.addWidget(self._build_permissions_page())
        self.stack.addWidget(self._build_log_page())
        self.stack.addWidget(self._build_role_page())
        self.stack.addWidget(self._build_stats_page())
        from .games import GameManagementPage
        self.game_management_page = GameManagementPage(self)
        self.stack.addWidget(self.game_management_page)
        try:  # B站生活：读库出错也不影响其它页
            self.stack.addWidget(self._scroll_page(self._build_bili_page()))
        except Exception as exc:  # noqa: BLE001
            fallback = QWidget()
            fallback.setObjectName("stackPage")
            QVBoxLayout(fallback).addWidget(self._label(f"B站生活页加载失败：{exc}", "pageSubLabel", 11))
            self.stack.addWidget(fallback)
        try:  # 模型花费：读不到也不影响其它页
            self.stack.addWidget(self._scroll_page(self._build_spend_page()))
        except Exception as exc:  # noqa: BLE001
            fallback = QWidget()
            fallback.setObjectName("stackPage")
            QVBoxLayout(fallback).addWidget(self._label(f"模型花费页加载失败：{exc}", "pageSubLabel", 11))
            self.stack.addWidget(fallback)
        try:  # 筛图：读不到也不影响其它页
            self.stack.addWidget(self._build_pics_page())
        except Exception as exc:  # noqa: BLE001
            fallback = QWidget()
            fallback.setObjectName("stackPage")
            QVBoxLayout(fallback).addWidget(self._label(f"图片筛选页加载失败：{exc}", "pageSubLabel", 11))
            self.stack.addWidget(fallback)
        try:  # 群友活跃：读不到也不影响其它页
            self.stack.addWidget(self._build_usage_rank_page())
        except Exception as exc:  # noqa: BLE001
            fallback = QWidget()
            fallback.setObjectName("stackPage")
            QVBoxLayout(fallback).addWidget(self._label(f"群友活跃页加载失败：{exc}", "pageSubLabel", 11))
            self.stack.addWidget(fallback)
        detail_layout.addWidget(self.stack, 1)

        # 首页专属底部工具栏；功能页只保留当前功能内容。
        self.home_footer = QWidget()
        footer_layout = QVBoxLayout(self.home_footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.setSpacing(10)
        util_row = QHBoxLayout()
        util_row.setSpacing(8)
        util_row.addWidget(self._tool_button("WebUI", self.open_webui))
        util_row.addWidget(self._tool_button("路径", self.open_folder))
        util_row.addWidget(self._tool_button("设置", self.toggle_settings))
        util_row.addWidget(self._tool_button("帮助", self.show_help))
        footer_layout.addLayout(util_row)
        self.footer_label = self._label("FURINA PANEL · v2.0.0", "footerLabel", 9)
        footer_layout.addWidget(self.footer_label)
        detail_layout.addWidget(self.home_footer)

        root_layout.addWidget(detail)
        self.add_log("info", "[系统] 芙宁娜控制面板已启动")
        return panel

    def _scroll_page(self, page):
        """内容比窗口高的页面：整页滚动，卡片按内容自然撑开（不再在每张卡片里塞一个小滚动框）。"""
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page.setContentsMargins(0, 0, 10, 0)
        scroll.setWidget(page)
        return scroll

    def _update_page_header(self, index):
        header = getattr(self, "page_header", None)
        if header is None:
            return
        kind = self.rail_kinds[index] if 0 <= index < len(self.rail_kinds) else ""
        show = kind not in ("", "home", "games")
        if show:
            self.page_heading.setText(self.rail_names[index])
        header.setVisible(show)

    def _refresh_rail_icons(self):
        """侧栏图标统一灰蓝，只有当前页是亮蓝。"""
        for kind, btn in zip(self.rail_kinds, self.rail_buttons):
            btn.setIcon(self._make_icon(kind, RAIL_ICON_ON if btn.isChecked() else RAIL_ICON_OFF))

    def toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        self._apply_sidebar_state()

    def _update_sidebar_top_fill(self):
        if not hasattr(self, "sidebar_top_fill") or not hasattr(self, "rail"):
            return
        self.sidebar_top_fill.setGeometry(0, 0, self.rail.width(), self.title_bar.height())

    def _apply_sidebar_state(self):
        # 首页保留原始左右分栏；功能页则把详情面板扩展到除侧栏外的整个窗口。
        if self.sidebar_collapsed:
            self.rail.setFixedWidth(84)
            # 折叠时隐藏 FURINA PANEL 英文，头像在窄侧栏(84)内水平居中
            self.brand_title.setVisible(False)
            self.title_layout.setContentsMargins(24, 18, 10, 0)
            self.rail_status_dot.setVisible(False)
            self.rail_status_text.setVisible(False)
            for head in self.rail_group_labels:
                head.setVisible(False)
            self.rail_card.setProperty("collapsed", True)
            for btn in self.rail_buttons:
                btn.setText("")
                btn.setIconSize(QSize(22, 22))
                btn.setFixedHeight(40)
        else:
            self.rail.setFixedWidth(190)
            # 展开时恢复 FURINA PANEL 英文与原始左间距
            self.brand_title.setVisible(True)
            self.title_layout.setContentsMargins(18, 18, 10, 0)
            self.rail_status_dot.setVisible(True)
            self.rail_status_text.setVisible(True)
            for head in self.rail_group_labels:
                head.setVisible(True)
            self.rail_card.setProperty("collapsed", False)
            for name, btn in zip(self.rail_names, self.rail_buttons):
                btn.setText(name)
                btn.setIconSize(QSize(20, 20))
                btn.setFixedHeight(40)
        self.rail_card.style().unpolish(self.rail_card)
        self.rail_card.style().polish(self.rail_card)

        self._apply_page_mode(getattr(self, "_active_tab", 0))
        self._update_sidebar_top_fill()
        self.left_panel.updateGeometry()

    def _apply_page_mode(self, index, force=False):
        """切换主页 / 功能页的窗口级布局。功能页让壁纸铺满右侧内容区，左侧工作栏保持稳定深色。"""
        is_home = index == 0
        is_games = 0 <= index < len(self.rail_kinds) and self.rail_kinds[index] == "games"
        # Apply before the home/non-home cache: switching between two feature tabs also matters.
        if is_games:
            margins = (6, 6, 6, 6)
        elif is_home:
            margins = (22, 26, 18, 20)
        else:
            margins = (32, 4, 30, 22)
        self.detail_panel.layout().setContentsMargins(*margins)
        self.title_bar.setFixedHeight(74 if is_home else 52)
        self.title_layout.setContentsMargins(24 if self.sidebar_collapsed else 18, 18 if is_home else 8, 10, 0)
        self._update_page_header(index)
        self._update_sidebar_top_fill()
        self._active_tab = index
        mode = "home" if is_home else ("games" if is_games else "feature")
        if not force and self._page_mode_home == mode:
            return
        self._page_mode_home = mode
        # 壁纸只在主页显示；其它页面实色底，顺便暂停动态壁纸省 CPU/GPU
        self._set_wallpaper_paused(not is_home)
        if is_home:
            # 布局完全不动：工作区仍固定 600/454，卡片/按钮尺寸位置不变；
            # 仅让底层壁纸铺满侧边栏右侧，art 不再画自己那半张立绘（只留启动按钮）。
            self.art.blank_mode = True
            self.art.show()
            if hasattr(self, "bg_layer"):
                if self.bg_layer.is_active:
                    self.bg_layer.show()
                else:
                    self.bg_layer.set_fallback_background(BACKDROP_FILES[self.bg_index])
                self._layout_background()
            self.home_header.setVisible(True)
            self.home_footer.setVisible(True)
            self.left_panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            self.left_panel.setMinimumWidth(600 if not self.sidebar_collapsed else 454)
            self.left_panel.setMaximumWidth(600 if not self.sidebar_collapsed else 454)
            self.left_panel.setFixedWidth(600 if not self.sidebar_collapsed else 454)
            # 窗口底色透明，露出底层铺满的壁纸（样式见 _PAGE_MODE_SHEETS）
        elif is_games:
            # 内嵌官方控制台是原生 WebEngine 表面：下面不能再叠半透明层/动态壁纸，
            # 否则在混合显卡上悬停时会整块闪烁。这里整页改为不透明纯色。
            self.art.hide()
            if hasattr(self, "bg_layer"):
                self.bg_layer.hide()
            self.home_header.setVisible(False)
            self.home_footer.setVisible(False)
            self.left_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.left_panel.setMinimumWidth(0)
            self.left_panel.setMaximumWidth(16777215)
            # 整页不透明纯色（样式见 _PAGE_MODE_SHEETS）
        else:
            # 功能页：实色底，不再把壁纸垫在内容下面（半透明叠壁纸会让文字难读、各页颜色也不一致）
            self.art.hide()
            if hasattr(self, "bg_layer"):
                self.bg_layer.hide()
            self.home_header.setVisible(False)
            self.home_footer.setVisible(False)
            self.left_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.left_panel.setMinimumWidth(0)
            self.left_panel.setMaximumWidth(16777215)
        self._set_page_mode_style(mode)
        self.left_panel.updateGeometry()
        self.stack.updateGeometry()
        self.window_body.update()

    # 三种页面模式的底色一次性写进各自的样式表，用 pageMode 属性切换。
    # 以前每次切页都对 windowBody（整棵控件树的根）重新 setStyleSheet，Qt 会把几百个子控件全部
    # 重新解析样式，一次 60ms 以上、切页卡 0.3 秒；现在只改属性并重刷这 4 个控件本身。
    _PAGE_MODE_SHEETS = {
        "window_body": (
            'QWidget#windowBody[pageMode="home"] { background: transparent; border-radius: 16px; }\n'
            'QWidget#windowBody[pageMode="games"], QWidget#windowBody[pageMode="feature"] { background: #0d1524; border-radius: 16px; }'
        ),
        "detail_panel": (
            '#detailPanel[pageMode="home"] { background: transparent; }\n'
            '#detailPanel[pageMode="games"], #detailPanel[pageMode="feature"] { background: #0d1524; border: none; border-bottom-right-radius: 16px; }'
        ),
        "stack": (
            '#stack[pageMode="home"] { background: transparent; }\n'
            '#stack[pageMode="games"], #stack[pageMode="feature"] { background: transparent; border: none; }'
        ),
        "title_bar": (
            '#titleBar[pageMode="games"], #titleBar[pageMode="feature"] { background: #0d1524; border-top-right-radius: 16px; }'
        ),
    }

    def _set_page_mode_style(self, mode):
        for attr, sheet in self._PAGE_MODE_SHEETS.items():
            w = getattr(self, attr, None)
            if w is None:
                continue
            w.setProperty("pageMode", mode)
            if w.styleSheet() != sheet:
                w.setStyleSheet(sheet)  # 只在首次 / 被 reset 清掉后装一次
            else:
                st = w.style()
                st.unpolish(w)
                st.polish(w)
            w.update()

    def _games_page_active(self):
        page = getattr(self, "game_management_page", None)
        return page is not None and getattr(self, "stack", None) is not None and self.stack.currentWidget() is page

    def _set_wallpaper_paused(self, paused):
        """游戏管理页暂停动态壁纸（视频 / GIF），离开后恢复。"""
        layer = getattr(self, "bg_layer", None)
        if layer is None:
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            state = layer.player.playbackState()
            if paused and state == QMediaPlayer.PlaybackState.PlayingState:
                layer.player.pause()
            elif not paused and state == QMediaPlayer.PlaybackState.PausedState:
                layer.player.play()
            if layer.movie is not None:
                layer.movie.setPaused(bool(paused))
        except Exception:
            pass
        effect = getattr(self, "_logo_effect", None)
        if effect is not None:
            effect.setEnabled(not paused)

    def closeEvent(self, event):
        page = getattr(self, "game_management_page", None)
        if page is not None and not page.can_leave():
            event.ignore()
            return
        self._save_geometry()
        super().closeEvent(event)

    # ===== 记住窗口大小/位置（下次打开还是上次拖成的样子）=====
    def _restore_geometry(self):
        try:
            data = json.loads(open(_v2.log_path("panel_window.json"), encoding="utf-8").read())
            x, y, w, h = (int(data[k]) for k in ("x", "y", "w", "h"))
        except Exception:
            return
        w, h = max(w, self.minimumWidth()), max(h, self.minimumHeight())
        rect = QRect(x, y, w, h)
        screen = QApplication.screenAt(rect.center())
        if screen is None:  # 上次在已经拔掉的副屏上：只用大小，位置居中
            area = self.screen().availableGeometry()
            w, h = min(w, area.width()), min(h, area.height())
            rect = QRect(area.x() + (area.width() - w) // 2, area.y() + (area.height() - h) // 2, w, h)
        else:
            area = screen.availableGeometry()
            rect.setWidth(min(w, area.width())); rect.setHeight(min(h, area.height()))
        self.setGeometry(rect)
        self._restore_maximized = bool(data.get("max"))

    def _save_geometry(self):
        try:
            g = self.normalGeometry() if self.isMaximized() else self.geometry()
            with open(_v2.log_path("panel_window.json"), "w", encoding="utf-8") as f:
                json.dump({"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height(), "max": self.isMaximized()}, f)
        except Exception:
            pass

    def _layout_edge_handles(self):
        handles = getattr(self, "_edge_handles", None)
        if not handles:
            return
        show = not (self.isMaximized() or self.isFullScreen())
        for h in handles:
            h.place(self.width(), self.height())
            h.setVisible(show)
            if show:
                h.raise_()

    def show_tab(self, index):
        # 直接切换页面：不抓屏、不做圆形遮罩、不创建高分辨率过渡图。
        if index < 0 or index >= self.stack.count():
            return
        if self.stack.currentIndex() == index and self._active_tab == index:
            return
        if self.stack.currentWidget() is getattr(self, "game_management_page", None):
            if not self.game_management_page.can_leave():
                self.rail_buttons[self.stack.currentIndex()].setChecked(True)
                return
        self._apply_page_mode(index)
        self.stack.setCurrentIndex(index)
        # 先完成页面切换，再把较重的数据刷新放到下一轮事件循环，避免点击时阻塞重绘。
        self._page_refresh_serial += 1
        refresh_serial = self._page_refresh_serial
        kind = self.rail_kinds[index] if 0 <= index < len(self.rail_kinds) else ""
        if kind in ("permissions", "stats", "bili", "spend", "pics", "usage"):
            # 让堆叠页先完成首帧绘制，再刷新可能涉及文件/网络缓存的数据，
            # 避免点击后紧接着出现一次明显的主线程阻塞。
            QTimer.singleShot(80, lambda idx=index, serial=refresh_serial: self._refresh_page_data(idx, serial))
        self.rail_buttons[index].setChecked(True)  # 按钮组互斥 + buttonToggled 会同步图标颜色
        self.stack.update()

    def _refresh_page_data(self, index, serial):
        """在页面切换完成后刷新重数据页，避免把列表重建放在点击事件里。"""
        if serial != getattr(self, "_page_refresh_serial", 0):
            return
        if getattr(self, "_active_tab", -1) != index:
            return
        kind = self.rail_kinds[index] if 0 <= index < len(self.rail_kinds) else ""
        if kind == "permissions":
            self._refresh_permission_groups()
        elif kind == "stats":
            self.refresh_stats()
        elif kind == "bili" and hasattr(self, "refresh_bili"):
            self.refresh_bili()
        elif kind == "spend" and hasattr(self, "refresh_spend"):
            self.refresh_spend()
        elif kind == "pics" and hasattr(self, "refresh_pics"):
            self.refresh_pics()
        elif kind == "usage" and hasattr(self, "refresh_usage_rank"):
            self.refresh_usage_rank(force=True)

    def _tool_button(self, text, callback):
        btn = QPushButton(text)
        btn.setObjectName("toolBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(32)
        btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        btn.clicked.connect(callback)
        return btn

    def _polish_combo(self, combo):
        """把下拉弹层外层容器也刷成深色，消除 Fusion 默认浅色白边。"""
        try:
            container = combo.view().parentWidget()
            if container is not None:
                container.setStyleSheet(
                    "QFrame{background:#0d1726;border:1px solid rgba(106,212,255,0.25);border-radius:10px;}"
                )
        except Exception:
            pass

    def _line_edit_style(self):
        return (f"background-color: {SURFACE_DEEP}; color: {COLOR_TEXT}; border: 1px solid rgba(255,255,255,0.10);"
                f"border-radius: {RADIUS_CONTROL}px; padding: 9px 12px; font-size: 13px;")

    def _mini_btn_style(self):
        return (f"background: {SURFACE_HOVER}; color: {TEXT_BODY}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px;")

    def open_prompt_file(self):
        try:
            subprocess.Popen(["notepad.exe", PERSONA_PATH])
            self.add_log("info", "[系统] 已用记事本打开 AI 提示词文件")
        except Exception as e:
            self.add_log("error", f"[错误] 打开提示词失败: {e}")

    def copy_prompt(self):
        try:
            QApplication.clipboard().setText(self.prompt_text.toPlainText())
            self.add_log("success", "[系统] AI 提示词已复制到剪贴板")
        except Exception as e:
            self.add_log("error", f"[错误] 复制失败: {e}")

    def _qss(self):
        # 全局样式：颜色全部取自 theme.py（实色表面 + 单一强调色），选择器名字与各页面保持不变。
        return f"""
        QWidget {{ background: transparent; color: {COLOR_TEXT}; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; }}
        QToolTip {{ background: {SURFACE_RAISED}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_STRONG}; padding: 6px 8px; border-radius: 6px; }}
        #titleBar {{ background: transparent; border: none; }}
        #brandIcon {{ background: transparent; border-radius: 10px; }}
        #titleText {{ color: {TEXT_SECONDARY}; font-size: 13px; font-weight: bold; letter-spacing: 1px; }}
        #winBtn, #closeBtn {{ background: transparent; color: {TEXT_SECONDARY}; border: none; border-radius: 8px; font-size: 15px; }}
        #winBtn:hover {{ background: rgba(255,255,255,0.08); color: #ffffff; }}
        #winBtn:pressed {{ background: rgba(255,255,255,0.14); }}
        #closeBtn:hover {{ background: #e81123; color: white; }}
        #closeBtn:pressed {{ background: #bf0f1d; color: white; }}
        #windowBody {{ background: {APP_BG}; border-radius: 16px; }}
        #leftRail {{ background: {RAIL_BG}; border-right: 1px solid {BORDER}; border-bottom-left-radius: 16px; }}
        #navGroup {{ color: {TEXT_HINT}; font-size: 11px; font-weight: bold; padding: 14px 12px 4px 12px; }}
        #navItem {{ background: transparent; color: #a9b6c9; border: none; border-radius: 8px; padding: 8px 12px; text-align: left; font-size: 14px; }}
        #navItem:hover {{ background: rgba(255,255,255,0.05); color: {TEXT_PRIMARY}; }}
        #navItem:checked {{ background: {ACCENT_SOFT}; color: #ffffff; font-weight: bold; }}
        #railCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
        #railCard[collapsed="true"] {{ background: transparent; border: none; }}
        #railStatusText {{ color: {TEXT_BODY}; font-size: 12px; }}
        #collapseBtn {{ background: transparent; border: none; border-radius: 8px; }}
        #collapseBtn:hover {{ background: rgba(255,255,255,0.08); }}
        #sidebarTopFill {{ background: {RAIL_BG}; border-top-left-radius: 16px; }}
        #detailPanel {{ background: transparent; }}
        #pageHeading {{ color: {TEXT_PRIMARY}; font-size: 20px; font-weight: bold; }}
        #pageSubLabel {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
        #pageTitleLabel {{ color: {TEXT_PRIMARY}; font-size: 20px; font-weight: bold; }}
        #pageCountLabel {{ color: {TEXT_SECONDARY}; background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 10px; font-size: 12px; }}
        #sectionLabel, #fieldLabel {{ color: {TEXT_PRIMARY}; font-size: 13px; font-weight: bold; }}
        #statusText {{ color: {COLOR_TEXT}; }}
        #stack {{ background: transparent; }}
        #stackPage {{ background: transparent; }}
        QScrollArea#pageScroll {{ background: transparent; border: none; }}
        QScrollArea#pageScroll > QWidget > QWidget {{ background: transparent; }}
        #contentSurface, #statToolbar, #cmdToolbar {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; }}
        #infoTile {{ background: {SURFACE}; color: {TEXT_BODY}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; padding: 14px; min-height: 28px; }}
        #dashCard {{ background: rgba(13,21,36,0.78); border: 1px solid rgba(255,255,255,0.12); border-radius: {RADIUS_CARD}px; }}
        #dashCard:hover {{ background: rgba(26,37,57,0.90); border-color: rgba(255,255,255,0.22); }}
        #dashTitle {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
        #dashValue {{ color: #ffffff; font-size: 18px; font-weight: bold; }}
        #dashSub {{ color: {TEXT_SECONDARY}; font-size: 11px; }}
        #toolBtn {{ background: {SURFACE_RAISED}; color: {TEXT_BODY}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_CONTROL}px; padding: 6px 12px; font-size: 12px; }}
        #toolBtn:hover {{ background: {SURFACE_HOVER}; border-color: rgba(106,212,255,0.40); color: #ffffff; }}
        #toolBtn:pressed {{ background: {SURFACE_SELECTED}; }}
        #toolBtn:disabled {{ color: {TEXT_HINT}; background: {SURFACE}; border-color: {BORDER}; }}
        #primaryBtn {{ background: {ACCENT}; color: #04202c; border: none; border-radius: {RADIUS_CONTROL}px; padding: 6px 14px; font-size: 12px; font-weight: bold; }}
        #primaryBtn:hover {{ background: #8fe0ff; }}
        #featureList, #permList, #roleList, #statList {{ background: {SURFACE}; color: {TEXT_BODY}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; padding: 6px; outline: none; font-size: 13px; }}
        #featureList::item, #permList::item, #roleList::item {{ padding: 9px 12px; border: 1px solid transparent; border-radius: 8px; margin: 1px 0; }}
        #featureList::item:hover, #permList::item:hover, #roleList::item:hover {{ background: {SURFACE_HOVER}; }}
        #featureList::item:selected, #permList::item:selected, #roleList::item:selected {{ background: {SURFACE_SELECTED}; border-color: rgba(106,212,255,0.35); color: #ffffff; }}
        #permissionCheck {{ color: {TEXT_BODY}; background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 8px 10px; spacing: 10px; }}
        #permissionCheck:hover {{ background: {SURFACE_HOVER}; }}
        #permissionCheck:disabled {{ color: {TEXT_HINT}; }}
        #permissionCheck::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #5b6b84; background: {SURFACE_DEEP}; }}
        #permissionCheck::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({CHECKMARK_SVG}); }}
        #permissionCheck:pressed {{ background: {SURFACE_SELECTED}; }}
        #featureList::item {{ padding: 12px 14px; }}
        #statList::item {{ padding: 9px 12px; border-bottom: 1px solid {BORDER}; margin: 0; }}
        #statList::item:hover {{ background: {SURFACE_HOVER}; }}
        #statList::item:selected {{ background: {SURFACE_SELECTED}; color: #ffffff; }}
        #logText, #promptText, #rolePromptEdit {{ background: {SURFACE_DEEP}; color: {TEXT_BODY}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; padding: 12px; font-family: Consolas, "Microsoft YaHei"; font-size: 12px; selection-background-color: #31537b; }}
        #roleNameEdit, #searchInput {{ background: {SURFACE_DEEP}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_CONTROL}px; padding: 8px 12px; font-size: 13px; min-height: 18px; }}
        #roleNameEdit:focus, #searchInput:focus, #rolePromptEdit:focus, #logText:focus {{ border-color: {BORDER_ACCENT}; }}
        #filterBtn {{ background: {SURFACE_RAISED}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_CONTROL}px; }}
        #filterBtn:hover {{ background: {SURFACE_HOVER}; border-color: rgba(106,212,255,0.40); }}
        #filterBtn:pressed {{ background: {SURFACE_SELECTED}; }}
        QComboBox {{ background: {SURFACE_DEEP}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_CONTROL}px; padding: 6px 12px; font-size: 13px; min-height: 20px; }}
        QComboBox:hover {{ border-color: rgba(106,212,255,0.40); }}
        QComboBox:focus {{ border-color: {BORDER_ACCENT}; }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox QAbstractItemView {{ background: {SURFACE}; color: {TEXT_BODY}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 4px; outline: none; }}
        QComboBox QAbstractItemView::item {{ padding: 6px 12px; border-radius: 6px; min-height: 22px; }}
        QComboBox QAbstractItemView::item:hover {{ background: {SURFACE_HOVER}; }}
        QComboBox QAbstractItemView::item:selected {{ background: {SURFACE_SELECTED}; color: #ffffff; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 0; }}
        QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.14); min-height: 36px; border-radius: 4px; }}
        QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.26); }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 4px 2px 4px; }}
        QScrollBar::handle:horizontal {{ background: rgba(255,255,255,0.14); min-width: 36px; border-radius: 4px; }}
        QScrollBar::handle:horizontal:hover {{ background: rgba(255,255,255,0.26); }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
        #artPanel {{ background: transparent; }}
        #footerLabel {{ color: rgba(255,255,255,0.32); }}
        #featureGroup {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; }}
        #featGroupTitle {{ color: {TEXT_PRIMARY}; font-size: 14px; font-weight: bold; }}
        #featureRow {{ background: transparent; border-radius: 8px; }}
        #featureRow:hover {{ background: {SURFACE_HOVER}; }}
        #featName {{ color: {TEXT_PRIMARY}; font-size: 13px; font-weight: bold; }}
        #featDesc {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
        #featTag {{ color: {TEXT_SECONDARY}; background: rgba(148,163,184,0.10); border: none; border-radius: 6px; padding: 2px 8px; font-size: 11px; }}
        #featSep {{ background: {BORDER}; border: none; max-height: 1px; min-height: 1px; }}
        #cmdCatBtn {{ background: transparent; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_STRONG}; border-radius: 14px; padding: 4px 14px; font-size: 13px; }}
        #cmdCatBtn:hover {{ background: {SURFACE_HOVER}; color: {TEXT_PRIMARY}; }}
        #cmdCatBtn:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-color: rgba(106,212,255,0.50); font-weight: bold; }}
        #cmdTriggerTag {{ color: {TEXT_SECONDARY}; background: rgba(148,163,184,0.10); border: none; border-radius: 6px; padding: 1px 7px; font-size: 11px; }}
        #cmdBadgeBtn {{ color: {ACCENT}; background: {SURFACE_DEEP}; border: 1px solid rgba(106,212,255,0.22); border-radius: 6px; padding: 4px 10px; font-family: Consolas, "Microsoft YaHei"; font-size: 12px; }}
        #cmdBadgeBtn:hover {{ background: {ACCENT_SOFT}; color: #ffffff; border-color: rgba(106,212,255,0.55); }}
        #cmdBadgeBtn:pressed {{ background: rgba(106,212,255,0.28); }}
        #cmdCatCaption {{ color: {TEXT_HINT}; font-size: 12px; font-weight: bold; }}
        #gameManagement {{ background: {APP_BG}; }}
        #gameHeader {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; }}
        #gameTitle {{ color: {TEXT_PRIMARY}; font-size: 18px; font-weight: bold; }}
        #gameSubtitle {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
        #gameChip {{ color: {TEXT_SECONDARY}; background: rgba(148,163,184,0.12); border: 1px solid rgba(148,163,184,0.25); border-radius: 11px; padding: 4px 12px; font-size: 12px; }}
        #gameChip[state="ok"] {{ color: {STATUS_OK}; background: rgba(52,211,153,0.12); border-color: rgba(52,211,153,0.32); }}
        #gameChip[state="warn"] {{ color: {STATUS_WARN}; background: rgba(251,191,36,0.12); border-color: rgba(251,191,36,0.30); }}
        #gameChip[state="bad"] {{ color: {STATUS_BAD}; background: rgba(248,113,113,0.12); border-color: rgba(248,113,113,0.32); }}
        #gameBtn, #gamePrimaryBtn {{ background: {SURFACE_RAISED}; color: {TEXT_BODY}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_CONTROL}px; padding: 6px 12px; font-size: 12px; min-height: 18px; }}
        #gameBtn:hover {{ background: {SURFACE_HOVER}; color: #ffffff; border-color: rgba(106,212,255,0.40); }}
        #gameBtn:pressed {{ background: {SURFACE_SELECTED}; }}
        #gameBtn:disabled, #gamePrimaryBtn:disabled {{ color: {TEXT_HINT}; background: {SURFACE}; }}
        #gamePrimaryBtn {{ background: {ACCENT}; color: #04202c; border: none; font-weight: bold; }}
        #gamePrimaryBtn:hover {{ background: #8fe0ff; }}
        #gameZoomGroup {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
        #gameZoomBtn {{ background: transparent; color: {TEXT_BODY}; border: none; border-radius: 7px; padding: 4px 8px; font-size: 12px; }}
        #gameZoomBtn:hover {{ background: {SURFACE_HOVER}; color: #ffffff; }}
        #gameConsole {{ background: #0a0f18; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; }}
        #gamePlaceholder {{ color: {TEXT_SECONDARY}; font-size: 13px; }}
        """

    def _init_backend(self):
        from PySide6.QtCore import QObject
        class PanelSignals(QObject):
            log = Signal(str, str)
            running = Signal(bool)
            busy = Signal(bool)
            groups = Signal(dict)
            test_result = Signal(bool, str, str)  # (是否正常, 结论, 详情/原因)
            online_state = Signal(object)  # 机器人 QQ 是否真的在线（进程在跑≠QQ在线）
            wallpaper_art = Signal(str, object)  # 后台生成的壁纸缩略图 / 预览（路径, QImage）
            bg_prefetched = Signal(str, object)  # 后台预解码的下一张轮播壁纸（路径, QImage）
            home_cards = Signal(object)  # 后台算好的首页卡片数据
        self.signals = PanelSignals()
        self.signals.home_cards.connect(self._on_home_cards)
        self.signals.log.connect(self.add_log)
        self.signals.test_result.connect(self._on_test_done)
        self.signals.running.connect(self.set_running)
        self.signals.online_state.connect(self._on_online_state)
        self.signals.busy.connect(self.set_busy)
        self.signals.groups.connect(self._on_napcat_groups_updated)
        self.signals.wallpaper_art.connect(self._on_wallpaper_art)
        self.signals.bg_prefetched.connect(self._on_bg_prefetched)
        # 启动一次后台群列表刷新；页面首次显示时先用本地数据，不会卡住窗口。
        self._request_napcat_groups(force=True)

    # ===== 窗口拖拽 =====

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0f172a"))

    def showEvent(self, event):
        # 旧版这里有两个同名 showEvent：前一个调用并不存在的 _apply_rounded_mask，
        # 被后一个覆盖而从未执行，已删除，只保留 DWM 圆角。
        super().showEvent(event)
        self._apply_dwm_round()

    def _apply_dwm_round(self):
        try:
            hwnd = int(self.winId())
            attr = 33  # DWMWA_WINDOW_CORNER_PREFERENCE
            value = 2  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(ctypes.c_int(value)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

    def resizeEvent(self, event):
        if hasattr(self, "bg_layer"):
            self._layout_background()
        if hasattr(self, "window_body"):
            self.window_body.setGeometry(0, 0, self.width(), self.height())
        if hasattr(self, "resize_grip"):
            self.resize_grip.move(self.width() - 18, self.height() - 18)
            self.resize_grip.raise_()
        self._layout_edge_handles()
        super().resizeEvent(event)

    def _layout_background(self):
        if not hasattr(self, "bg_layer") or not self.bg_layer.is_active:
            return
        self.bg_layer.setGeometry(0, 0, self.width(), self.height())

    def _toggle_maximized(self):
        if self.isMaximized(): self.showNormal()
        else: self.showMaximized()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "max_btn"):
            self.max_btn.setText("❐" if self.isMaximized() else "□")
        if hasattr(self, "resize_grip"):
            self.resize_grip.setVisible(not self.isMaximized())
        self._layout_edge_handles()
        if hasattr(self, "game_management_page"):
            self.game_management_page.maximize_button.setText("还原窗口" if self.isMaximized() else "最大化")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() < self.title_bar.height():
            self._drag_pos = None
            self._toggle_maximized(); event.accept(); return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() < self.title_bar.height():
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept(); return
            if not self.isMaximized():
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


class _StallWatchdog:
    """卡顿记录器：主线程被阻塞超过 0.25 秒时，把它当时正在执行的代码位置写进 logs/panel-stall.log，
    方便定位到底是哪一步让面板卡住。只做记录，开销很小；设 FURINA_STALL_LOG=0 可关闭。"""

    THRESHOLD = 0.25

    def __init__(self, app):
        self.main_id = threading.main_thread().ident
        self.beat = time.monotonic()
        self.path = os.path.join(os.path.dirname(BOT_STDOUT_LOG), "panel-stall.log")
        self.timer = QTimer(app)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        threading.Thread(target=self._watch, daemon=True, name="panel-stall-watchdog").start()

    def _tick(self):
        self.beat = time.monotonic()

    def _write(self, text):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > 1024 * 1024:
                os.replace(self.path, self.path + ".1")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def _watch(self):
        import traceback
        reported = None
        while True:
            time.sleep(0.05)
            lag = time.monotonic() - self.beat
            if lag < self.THRESHOLD:
                if reported is not None:
                    self._write(f"    -> 共卡住约 {reported[1]:.2f}s\n")
                    reported = None
                continue
            if reported is None or reported[0] != self.beat:
                frame = sys._current_frames().get(self.main_id)
                stack = "".join(traceback.format_stack(frame, limit=14)) if frame else "(无堆栈)\n"
                self._write(f"[{datetime.datetime.now():%m-%d %H:%M:%S}] 主线程卡住 >{self.THRESHOLD}s，当时在执行：\n{stack}")
                reported = [self.beat, lag]
            else:
                reported[1] = lag


def main():
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Furina.ControlPanel.v2")
        except Exception:
            pass
    if QApplication.instance() is None:
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    icon = _v2.asset("furina_round.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    window = PanelWindow()
    if os.environ.get("FURINA_STALL_LOG", "1") != "0":
        app._stall_watchdog = _StallWatchdog(app)
    window.setAttribute(Qt.WA_ShowWithoutActivating, True)
    if getattr(window, "_restore_maximized", False):
        window.showMaximized()  # 上次是最大化关的，就最大化打开
    else:
        window.show()
    QTimer.singleShot(0, lambda: window.setAttribute(Qt.WA_ShowWithoutActivating, False))
    return app.exec()
