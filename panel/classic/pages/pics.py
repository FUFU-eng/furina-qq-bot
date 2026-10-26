"""「筛图」页（PanelWindow 的 mixin）：群里收来的图一张张过，留下的自动进 Eagle。

操作尽量轻：→ / D / 空格 = 留下，← / A / Delete = 丢掉，Z / Backspace = 撤销；
也可以直接用鼠标把图往左/右拖。图片在后台线程预读，切换不卡。
数据和手机端共用 furina/services/pic_inbox.py。
"""
import html
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QItemSelectionModel, QObject, QPointF
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QMessageBox, QStyle, QStyledItemDelegate

from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403

_E = html.escape
_MUTED = "#8193b2"
_KEEP = "#3ecf8e"
_DROP = "#f87171"
_SRC = {"group": "群聊", "forward": "合并转发", "search": "搜图原图", "pixiv": "Pixiv 点图", "pixiv_rank": "Pixiv 榜单"}
_THUMB = 420          # 总览缩略图读取尺寸（缩放上限）
_ZOOM_MIN, _ZOOM_MAX = 90, 420
_ENH = {"todo": "等电脑空闲时强化", "run": "强化中…", "done": "已强化", "fail": "强化失败，用原图"}


def _kb(n):
    n = int(n or 0)
    return f"{n / 1048576:.1f}MB" if n >= 1048576 else f"{max(1, n // 1024)}KB"


class _PicBus(QObject):
    items = Signal(object)
    image = Signal(str, object)
    thumb = Signal(str, object)
    stats = Signal(object)


class _ThumbDelegate(QStyledItemDelegate):
    """缩略图格子：图片居中等比缩放、圆角；选中时蓝框 + 右上角勾。"""

    def __init__(self, grid):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index):
        s = self.grid.size + 16
        return QSize(s, s)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cell = QRectF(option.rect).adjusted(4, 4, -4, -4)
        sel = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        bg = QPainterPath()
        bg.addRoundedRect(cell, 10, 10)
        p.fillPath(bg, QColor(106, 212, 255, 38) if sel else QColor(255, 255, 255, 10))
        icon = index.data(Qt.DecorationRole)
        if icon is not None:
            side = int(min(cell.width(), cell.height())) - 8
            pm = icon.pixmap(QSize(side, side))
            if not pm.isNull():
                w, h = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
                k = min(side / max(1.0, w), side / max(1.0, h))
                w, h = w * k, h * k
                tgt = QRectF(cell.center().x() - w / 2, cell.center().y() - h / 2, w, h)
                clip = QPainterPath()
                clip.addRoundedRect(tgt, 7, 7)
                p.setClipPath(clip)
                p.drawPixmap(tgt, pm, QRectF(pm.rect()))
                p.setClipping(False)
        if sel or hover:
            p.setPen(QPen(QColor("#6ad4ff") if sel else QColor(106, 212, 255, 110), 3 if sel else 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 10, 10)
        if sel:
            badge = QRectF(cell.right() - 28, cell.top() + 6, 22, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#6ad4ff"))
            p.drawEllipse(badge)
            p.setPen(QPen(QColor("#0b1020"), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(QPointF(badge.x() + 6, badge.y() + 11.5), QPointF(badge.x() + 9.5, badge.y() + 15))
            p.drawLine(QPointF(badge.x() + 9.5, badge.y() + 15), QPointF(badge.x() + 16, badge.y() + 7.5))
        p.restore()


class PicGrid(QListWidget):
    """总览：缩略图网格。Ctrl+滚轮缩放，框选/Ctrl/Shift 多选，拖到垃圾桶删除，双击看单张。"""

    zoomed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragOnly)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListWidget.Batched)
        self.setBatchSize(80)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setSpacing(6)
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setStyleSheet("QListWidget{background:rgba(10,14,26,150); border-radius:14px; padding:8px; outline:none;}")
        self.setItemDelegate(_ThumbDelegate(self))
        self.size = 180
        self.set_zoom(self.size)

    def set_zoom(self, px):
        px = max(_ZOOM_MIN, min(_ZOOM_MAX, int(px)))
        self.size = px
        self.setIconSize(QSize(px, px))
        self.setGridSize(QSize(px + 16, px + 16))
        self.doItemsLayout()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            step = 1 if e.angleDelta().y() > 0 else -1
            self.set_zoom(self.size + step * max(12, self.size // 8))
            self.zoomed.emit(self.size)
            e.accept()
            return
        super().wheelEvent(e)

    def selected_ids(self):
        return [it.data(Qt.UserRole) for it in self.selectedItems()]

    def startDrag(self, actions):
        items = self.selectedItems()
        if not items:
            return
        from PySide6.QtGui import QDrag

        drag = QDrag(self)
        drag.setMimeData(self.mimeData(items))
        # 拖动时手上的小图：第一张缩略图 + 张数角标
        side = 96
        pm = QPixmap(side + 14, side + 14)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        icon_pm = items[0].icon().pixmap(QSize(side, side))
        if len(items) > 1:
            p.setOpacity(0.55)
            p.drawPixmap(10, 10, icon_pm.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            p.setOpacity(1.0)
        p.drawPixmap(2, 2, icon_pm.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if len(items) > 1:
            p.setBrush(QColor(_DROP))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(side - 20, 0, 32, 32))
            p.setPen(QColor("#ffffff"))
            p.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
            p.drawText(QRectF(side - 20, 0, 32, 32), Qt.AlignCenter, str(len(items)))
        p.end()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(side // 2, side // 2))
        drag.exec(Qt.MoveAction)


class TrashBin(QWidget):
    """总览右侧的垃圾桶：把图拖进来就丢掉。拖到上方时桶盖掀开、变红。"""

    dropped = Signal()

    def __init__(self, grid, parent=None):
        super().__init__(parent)
        self.grid = grid
        self.setAcceptDrops(True)
        self.setFixedWidth(118)
        self.setMinimumHeight(160)
        self.setToolTip("把图拖进来就丢掉（5 分钟内可以撤销）")
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_t)
        self._pulse = 0.0
        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setDuration(380)
        self._pulse_anim.setStartValue(1.0)
        self._pulse_anim.setEndValue(0.0)
        self._pulse_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pulse_anim.valueChanged.connect(self._set_pulse)

    def _set_t(self, v):
        self._t = float(v)
        self.update()

    def _set_pulse(self, v):
        self._pulse = float(v)
        self.update()

    def _hover(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def dragEnterEvent(self, e):
        if e.source() is self.grid:
            e.acceptProposedAction()
            self._hover(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is self.grid:
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        if e.source() is self.grid:
            e.acceptProposedAction()
            self._hover(False)
            self._pulse_anim.stop()
            self._pulse_anim.start()
            self.dropped.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        bg = QColor(_DROP)
        bg.setAlphaF(0.08 + 0.22 * self._t + 0.3 * self._pulse)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.fillPath(path, bg)
        pen_c = QColor(_DROP) if self._t > 0.02 else QColor(_MUTED)
        if 0.02 < self._t < 1:
            pen_c = QColor(_MUTED)
            pen_c.setRedF(min(1.0, pen_c.redF() + (QColor(_DROP).redF() - pen_c.redF()) * self._t))
        dash = QPen(pen_c, 2, Qt.DashLine)
        p.setPen(dash)
        p.drawRoundedRect(r, 16, 16)
        # 桶
        scale = 1.0 + 0.12 * self._t
        cx, cy = r.center().x(), r.center().y() - 6
        p.translate(cx, cy)
        p.scale(scale, scale)
        p.setPen(QPen(pen_c, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        body = QPainterPath()
        body.moveTo(-20, -10)
        body.lineTo(-16, 26)
        body.lineTo(16, 26)
        body.lineTo(20, -10)
        p.drawPath(body)
        for x in (-8, 0, 8):
            p.drawLine(QPointF(x, -3), QPointF(x * 0.9, 19))
        # 盖子：悬停时绕左端掀开
        p.save()
        p.translate(-24, -15)
        p.rotate(-35 * self._t)
        p.drawLine(QPointF(0, 0), QPointF(48, 0))
        p.drawLine(QPointF(18, 0), QPointF(18, -6))
        p.drawLine(QPointF(18, -6), QPointF(30, -6))
        p.drawLine(QPointF(30, -6), QPointF(30, 0))
        p.restore()
        p.resetTransform()
        p.setPen(pen_c)
        p.setFont(QFont("Microsoft YaHei", 10))
        p.drawText(QRectF(r.x(), cy + 40, r.width(), 22), Qt.AlignHCenter, "松手丢掉" if self._t > 0.5 else "拖到这里丢掉")
        p.end()


class PicCanvas(QWidget):
    """显示当前图；可以用鼠标左右拖，松手超过阈值就判定。"""

    swiped = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setCursor(Qt.OpenHandCursor)
        self._img = None
        self._msg = "正在读取…"
        self._dx = 0.0
        self._press = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_dx)

    def set_image(self, img, msg=""):
        self._anim.stop()
        self._img = img
        self._msg = msg
        self._dx = 0.0
        self.update()

    def _set_dx(self, v):
        self._dx = float(v)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._img is not None:
            self._press = e.position().x() - self._dx
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._press is not None:
            self._dx = e.position().x() - self._press
            self.update()

    def mouseReleaseEvent(self, e):
        if self._press is None:
            return
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        limit = max(90.0, self.width() * 0.18)
        if abs(self._dx) >= limit:
            self.swiped.emit(self._dx > 0)
        else:
            self._anim.stop()
            self._anim.setStartValue(self._dx)
            self._anim.setEndValue(0.0)
            self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect().adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 14, 14)
        p.fillPath(path, QColor(10, 14, 26, 150))
        if self._img is None or self._img.isNull():
            p.setPen(QColor(_MUTED))
            p.setFont(QFont("Microsoft YaHei", 13))
            p.drawText(r, Qt.AlignCenter, self._msg)
            p.end()
            return
        iw, ih = self._img.width(), self._img.height()
        scale = min((r.width() - 16) / max(1, iw), (r.height() - 16) / max(1, ih))
        w, h = iw * scale, ih * scale
        x = r.x() + (r.width() - w) / 2 + self._dx
        y = r.y() + (r.height() - h) / 2
        p.save()
        p.setClipPath(path)
        p.translate(x + w / 2, y + h / 2)
        p.rotate(max(-8.0, min(8.0, self._dx / 40.0)))
        target = QRectF(-w / 2, -h / 2, w, h)
        p.drawImage(target, self._img)
        if abs(self._dx) > 20:
            keep = self._dx > 0
            a = min(1.0, abs(self._dx) / max(90.0, self.width() * 0.18))
            c = QColor(_KEEP if keep else _DROP)
            c.setAlphaF(0.85 * a)
            p.setPen(QPen(c, 4))
            p.setFont(QFont("Microsoft YaHei", 26, QFont.Bold))
            stamp = QRectF(-w / 2 + 18, -h / 2 + 18, 150, 58) if keep else QRectF(w / 2 - 168, -h / 2 + 18, 150, 58)
            p.drawRoundedRect(stamp, 10, 10)
            p.drawText(stamp, Qt.AlignCenter, "留下" if keep else "丢掉")
        p.restore()
        p.end()


class PicsPageMixin:
    def _build_pics_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        self.pics_page = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        head = QHBoxLayout()
        self.pics_status = self._label("…", "pageSubLabel", 11)
        self.pics_status.setTextFormat(Qt.RichText)
        self.pics_status.setWordWrap(True)
        head.addWidget(self.pics_status, 1)
        self.pics_mode_btn = self._tool_button("总览", self._pics_toggle_mode)
        head.addWidget(self.pics_mode_btn)
        head.addWidget(self._tool_button("刷新", lambda: self.refresh_pics(force=True)))
        head.addWidget(self._tool_button("打开原图", self._pics_open_current))
        head.addWidget(self._tool_button("手机连接", self._pics_phone_dialog))
        head.addWidget(self._tool_button("设置", lambda: self._open_settings("search")))
        layout.addLayout(head)

        page.setFocusPolicy(Qt.StrongFocus)
        self.pics_views = QStackedWidget(page)
        layout.addWidget(self.pics_views, 1)
        single = QWidget()
        single.setObjectName("stackPage")
        outer = layout
        layout = QVBoxLayout(single)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.pics_views.addWidget(single)
        self.pics_canvas = PicCanvas(page)
        self.pics_canvas.setFocusPolicy(Qt.StrongFocus)
        self.pics_canvas.swiped.connect(self._pics_decide)
        layout.addWidget(self.pics_canvas, 1)

        self.pics_info = QLabel("")
        self.pics_info.setTextFormat(Qt.RichText)
        self.pics_info.setAlignment(Qt.AlignCenter)
        self.pics_info.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:12px;")
        layout.addWidget(self.pics_info)

        bar = QHBoxLayout()
        bar.setSpacing(14)
        bar.addStretch(1)
        self.pics_drop_btn = self._pics_round_button("✕  丢掉", _DROP, lambda: self._pics_decide(False))
        self.pics_undo_btn = self._pics_round_button("↶  撤销", "#8193b2", self._pics_undo, small=True)
        self.pics_keep_btn = self._pics_round_button("✓  留下", _KEEP, lambda: self._pics_decide(True))
        for b in (self.pics_drop_btn, self.pics_undo_btn, self.pics_keep_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        layout.addLayout(bar)
        hint = QLabel("← / A 丢掉　　→ / D / 空格 留下　　Z 撤销　　也可以直接拖图")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:11px;")
        layout.addWidget(hint)
        self.pics_views.addWidget(self._build_pics_grid())
        layout = outer

        self._pics_bus = _PicBus(page)
        self._pics_bus.items.connect(self._pics_on_items)
        self._pics_bus.image.connect(self._pics_on_image)
        self._pics_bus.thumb.connect(self._pics_on_thumb)
        self._pics_bus.stats.connect(self._pics_on_stats)
        self._pics_queue = []         # 待筛的条目（当前 = [0]）
        self._pics_images = {}        # id -> QImage（预读）
        self._pics_loading = set()
        self._pics_undo_stack = []    # 每次操作一条：[(id, item), ...]（总览里一次可能丢好几张）
        self._pics_thumbs = {}        # id -> QIcon（总览缩略图）
        self._pics_thumb_loading = set()
        self._pics_grid_mode = False
        self._pics_names = {}
        self._pics_last = 0.0
        self._pics_busy = False
        self._pics_exec = ThreadPoolExecutor(max_workers=1)   # 留下/丢掉/撤销按顺序执行
        self._pics_reader = ThreadPoolExecutor(max_workers=2)  # 预读图片
        self._pics_thumb_reader = ThreadPoolExecutor(max_workers=3)  # 总览缩略图

        self._pics_timer = QTimer(page)  # 开着本页时每 30 秒补一次新收的图
        self._pics_timer.setInterval(30_000)
        self._pics_timer.timeout.connect(lambda: self._pics_active() and self.refresh_pics(force=True))
        self._pics_timer.start()

        self._pics_single_sc = []
        self._pics_grid_sc = []

        def sc(keys, fn, bucket=None):
            for k in keys:
                # 只在本页（及其子控件）有焦点时生效，不影响别的页面按空格/方向键
                s = QShortcut(QKeySequence(k), page)
                s.setContext(Qt.WidgetWithChildrenShortcut)
                s.activated.connect(lambda fn=fn: self._pics_active() and fn())
                if bucket is not None:
                    bucket.append(s)

        sc(("Right", "D", "Space"), lambda: self._pics_decide(True), self._pics_single_sc)
        sc(("Left", "A", "Delete", "Backspace"), lambda: self._pics_decide(False), self._pics_single_sc)
        sc(("Delete", "Backspace"), lambda: self._pics_grid_decide(False), self._pics_grid_sc)
        sc(("Return", "Enter"), lambda: self._pics_grid_decide(True), self._pics_grid_sc)
        sc(("Z", "Ctrl+Z"), self._pics_undo)
        for s_ in self._pics_grid_sc:
            s_.setEnabled(False)
        return page

    def _pics_round_button(self, text, color, cb, small=False):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(110 if small else 150, 44 if small else 52)
        b.setFont(QFont("Microsoft YaHei", 11 if small else 13, QFont.Bold))
        b.setStyleSheet(
            f"QPushButton{{background:rgba(15,22,40,190); color:{color}; border:2px solid {color}; border-radius:{22 if small else 26}px;}}"
            f"QPushButton:hover{{background:{color}; color:#0b1020;}}"
            f"QPushButton:disabled{{color:#4b5871; border-color:#4b5871;}}")
        b.clicked.connect(cb)
        return b

    def _pics_active(self):
        return getattr(self, "stack", None) is not None and self.stack.currentWidget() is getattr(self, "pics_page", None)

    # ---------------- 数据 ----------------
    def refresh_pics(self, force=False):
        if self._pics_active():
            self.pics_canvas.setFocus()
        if self._pics_busy or (not force and time.time() - self._pics_last < 8 and self._pics_queue):
            return
        self._pics_busy = True
        self._pics_last = time.time()

        def work():
            try:
                from furina.services import pic_inbox as svc
                rows = svc.pending(1000)
                names = dict(self._pics_names)
                for gid in {r.get("gid") for r in rows if r.get("gid")} - set(names):
                    try:
                        from ...services import data as pdata
                        names[gid] = pdata.group_name(gid) or gid
                    except Exception:  # noqa: BLE001
                        names[gid] = gid
                self._pics_bus.items.emit({"rows": rows, "names": names, "stats": svc.stats()})
            except Exception as e:  # noqa: BLE001
                self._pics_bus.items.emit({"error": str(e)})

        threading.Thread(target=work, daemon=True).start()

    def _pics_on_items(self, res):
        self._pics_busy = False
        if res.get("error"):
            self.pics_canvas.set_image(None, f"读不到筛图库：{res['error']}")
            return
        self._pics_names.update(res.get("names") or {})
        decided = {iid for entry in self._pics_undo_stack[-20:] for iid, _ in entry}
        cur = self._pics_queue[0]["id"] if self._pics_queue else None
        rows = [r for r in res.get("rows") or [] if r["id"] not in decided or r["id"] == cur]
        # 当前正看的那张保持在最前面，别因为刷新跳走
        if cur and any(r["id"] == cur for r in rows):
            rows.sort(key=lambda r: r["id"] != cur)
        self._pics_queue = rows
        keep_ids = {r["id"] for r in rows[:8]}
        self._pics_images = {k: v for k, v in self._pics_images.items() if k in keep_ids}
        alive = {r["id"] for r in rows}
        self._pics_thumbs = {k: v for k, v in self._pics_thumbs.items() if k in alive}
        self._pics_on_stats(res.get("stats") or {})
        self._pics_show()

    def _pics_on_stats(self, st):
        st = st or {}
        parts = [f"待筛 <b style='color:#f4f7ff'>{st.get('pending', 0)}</b>"]
        if st.get("enh_todo"):
            parts.append(f"等强化 {st['enh_todo']}")
        if st.get("keep_wait"):
            parts.append(f"强化完就进 Eagle {st['keep_wait']}")
        parts.append(f"今天留 <span style='color:{_KEEP}'>{st.get('kept_today', 0)}</span> · "
                     f"丢 <span style='color:{_DROP}'>{st.get('dropped_today', 0)}</span>")
        self.pics_status.setText("　·　".join(parts))

    def _pics_load(self, it):
        iid = it["id"]
        if iid in self._pics_images or iid in self._pics_loading:
            return
        self._pics_loading.add(iid)

        def work():
            img = None
            try:
                from furina.services import pic_inbox as svc
                p = svc.preview(iid, 2000)
                if p:
                    img = QImage(str(p))
            except Exception:  # noqa: BLE001
                img = None
            self._pics_bus.image.emit(iid, img)

        self._pics_reader.submit(work)

    def _pics_on_image(self, iid, img):
        self._pics_loading.discard(iid)
        if img is None or img.isNull():
            self._pics_queue = [r for r in self._pics_queue if r["id"] != iid]  # 文件没了：跳过
            self._pics_show()
            return
        self._pics_images[iid] = img
        if self._pics_queue and self._pics_queue[0]["id"] == iid:
            self._pics_show()

    def _pics_show(self):
        if self._pics_grid_mode:
            self._pics_sync_grid()
        has = bool(self._pics_queue)
        for b in (self.pics_drop_btn, self.pics_keep_btn):
            b.setEnabled(has)
        self.pics_undo_btn.setEnabled(bool(self._pics_undo_stack))
        if not has:
            self.pics_canvas.set_image(None, "都筛完啦，新图来了会自动出现在这里")
            self.pics_info.setText("")
            return
        if self._pics_grid_mode:
            return
        for it in self._pics_queue[:4]:  # 当前 + 后面三张预读
            self._pics_load(it)
        it = self._pics_queue[0]
        img = self._pics_images.get(it["id"])
        self.pics_canvas.set_image(img, "载入中…")
        bits = []
        gname = self._pics_names.get(it.get("gid") or "", "")
        src = _SRC.get(it.get("src"), it.get("src") or "")
        bits.append(_E(" · ".join(x for x in (gname, src) if x)))
        if it.get("uname"):
            bits.append(_E(it["uname"]))
        bits.append(time.strftime("%m-%d %H:%M", time.localtime(it.get("ts") or 0)))
        size = f"{it.get('w')}×{it.get('h')} · {_kb(it.get('size'))}"
        enh = it.get("enh")
        if enh in _ENH:
            color = _KEEP if enh == "done" else _MUTED
            size += f" · <span style='color:{color}'>{_ENH[enh]}</span>"
            if enh == "done":
                size += f"（原 {it.get('ow')}×{it.get('oh')} · {_kb(it.get('osize'))}）"
        bits.append(size)
        self.pics_info.setText("　".join(bits))

    # ---------------- 操作 ----------------
    def _pics_decide(self, keep):
        if not self._pics_queue:
            return
        it = self._pics_queue.pop(0)
        self._pics_undo_stack.append([(it["id"], it)])
        self._pics_undo_stack = self._pics_undo_stack[-50:]
        self._pics_images.pop(it["id"], None)
        self._pics_show()

        def work():
            from furina.services import pic_inbox as svc
            res = svc.decide(it["id"], bool(keep))
            self._pics_bus.stats.emit(svc.stats())
            if not res.get("ok") and res.get("reason") == "busy":  # 文件被占用没丢掉：放回队列
                self._pics_bus.items.emit({"rows": svc.pending(1000), "names": {}, "stats": svc.stats()})

        self._pics_exec.submit(work)
        if len(self._pics_queue) < 5:
            self.refresh_pics(force=True)

    def _pics_undo(self):
        if not self._pics_undo_stack:
            return
        entry = self._pics_undo_stack.pop()

        def work():
            from furina.services import pic_inbox as svc
            bad = False
            for iid, _ in entry:
                try:
                    bad |= not svc.undo(iid).get("ok")
                except OSError:
                    bad = True
            self._pics_bus.stats.emit(svc.stats())
            if bad:
                self._pics_bus.items.emit({"rows": svc.pending(1000), "names": {}, "stats": svc.stats()})

        back = [it for _, it in entry]
        if self._pics_grid_mode:  # 总览：放回原来的位置（按收图时间）
            self._pics_queue = sorted(self._pics_queue + back, key=lambda r: r.get("ts", 0))
        else:
            self._pics_queue[0:0] = back
        self._pics_show()
        if self._pics_grid_mode:
            ids = {it["id"] for it in back}
            for i in range(self.pics_grid.count()):
                if self.pics_grid.item(i).data(Qt.UserRole) in ids:
                    self.pics_grid.item(i).setSelected(True)
        self._pics_exec.submit(work)

    # ---------------- 总览 ----------------
    def _build_pics_grid(self):
        w = QWidget()
        w.setObjectName("stackPage")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.pics_grid_sel = QLabel("")
        self.pics_grid_sel.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:12px;")
        bar.addWidget(self.pics_grid_sel, 1)
        bar.addWidget(QLabel("缩放"))
        from PySide6.QtWidgets import QSlider

        self.pics_zoom = QSlider(Qt.Horizontal)
        self.pics_zoom.setRange(_ZOOM_MIN, _ZOOM_MAX)
        self.pics_zoom.setFixedWidth(160)
        bar.addWidget(self.pics_zoom)
        bar.addWidget(self._tool_button("全选", lambda: (self.pics_grid.selectAll(), self.pics_grid.setFocus())))
        self.pics_grid_keep = self._pics_round_button("✓ 留下选中", _KEEP, lambda: self._pics_grid_decide(True), small=True)
        self.pics_grid_drop = self._pics_round_button("✕ 丢掉选中", _DROP, lambda: self._pics_grid_decide(False), small=True)
        self.pics_grid_undo = self._pics_round_button("↶ 撤销", "#8193b2", self._pics_undo, small=True)
        for b in (self.pics_grid_keep, self.pics_grid_drop, self.pics_grid_undo):
            b.setFixedWidth(118)
            bar.addWidget(b)
        lay.addLayout(bar)
        body = QHBoxLayout()
        body.setSpacing(10)
        self.pics_grid = PicGrid(w)
        self.pics_trash = TrashBin(self.pics_grid, w)
        body.addWidget(self.pics_grid, 1)
        body.addWidget(self.pics_trash)
        lay.addLayout(body, 1)
        hint = QLabel("Ctrl + 滚轮 / 拖动滑块缩放　　框选、Ctrl / Shift 点选多张　　Delete 丢掉选中　　Enter 留下选中　　"
                      "把图拖到右边垃圾桶直接丢掉　　双击看大图　　Z 撤销")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:11px;")
        lay.addWidget(hint)
        self.pics_zoom.setValue(self.pics_grid.size)
        self.pics_zoom.valueChanged.connect(self.pics_grid.set_zoom)
        self.pics_grid.zoomed.connect(lambda v: (self.pics_zoom.blockSignals(True), self.pics_zoom.setValue(v),
                                                 self.pics_zoom.blockSignals(False)))
        self.pics_grid.itemSelectionChanged.connect(self._pics_grid_sel_changed)
        self.pics_grid.itemDoubleClicked.connect(self._pics_grid_open)
        self.pics_trash.dropped.connect(lambda: self._pics_grid_decide(False))
        self._pics_blank = None
        return w

    def _pics_toggle_mode(self):
        self._pics_grid_mode = not self._pics_grid_mode
        if self._pics_grid_mode:  # 总览按收图时间排（单张里双击跳过来的那张归位）
            self._pics_queue.sort(key=lambda r: r.get("ts", 0))
        self.pics_mode_btn.setText("单张" if self._pics_grid_mode else "总览")
        self.pics_views.setCurrentIndex(1 if self._pics_grid_mode else 0)
        for s_ in self._pics_single_sc:
            s_.setEnabled(not self._pics_grid_mode)
        for s_ in self._pics_grid_sc:
            s_.setEnabled(self._pics_grid_mode)
        self._pics_show()
        (self.pics_grid if self._pics_grid_mode else self.pics_canvas).setFocus()

    def _pics_blank_icon(self):
        if self._pics_blank is None:
            pm = QPixmap(_THUMB, _THUMB)
            pm.fill(QColor(30, 38, 60))
            self._pics_blank = QIcon(pm)
        return self._pics_blank

    def _pics_new_item(self, it):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, it["id"])
        item.setIcon(self._pics_thumbs.get(it["id"]) or self._pics_blank_icon())
        gname = self._pics_names.get(it.get("gid") or "", "")
        tip = " · ".join(x for x in (gname, _SRC.get(it.get("src"), ""), it.get("uname") or "") if x)
        item.setToolTip(f"{tip}\n{it.get('w')}×{it.get('h')} · {_kb(it.get('size'))}"
                        f"{' · ' + _ENH[it['enh']] if it.get('enh') in _ENH else ''}")
        if it["id"] not in self._pics_thumbs:
            self._pics_load_thumb(it["id"])
        return item

    def _pics_sync_grid(self):
        """让网格和队列一致：只增删有变化的格子（不整表重建，滚动位置和选择都保留）。"""
        g = self.pics_grid
        want = [r["id"] for r in self._pics_queue]
        want_set = set(want)
        g.setUpdatesEnabled(False)
        for i in range(g.count() - 1, -1, -1):
            if g.item(i).data(Qt.UserRole) not in want_set:
                g.takeItem(i)
        by_id = {r["id"]: r for r in self._pics_queue}
        for i, iid in enumerate(want):
            cur = g.item(i)
            if cur is None or cur.data(Qt.UserRole) != iid:
                g.insertItem(i, self._pics_new_item(by_id[iid]))
        while g.count() > len(want):
            g.takeItem(g.count() - 1)
        g.setUpdatesEnabled(True)
        self._pics_grid_sel_changed()

    def _pics_load_thumb(self, iid):
        if iid in self._pics_thumb_loading:
            return
        self._pics_thumb_loading.add(iid)

        def work():
            img = None
            try:
                from furina.services import pic_inbox as svc
                p = svc.preview(iid, _THUMB)
                if p:
                    img = QImage(str(p))
                    if not img.isNull() and max(img.width(), img.height()) > _THUMB:
                        img = img.scaled(_THUMB, _THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            except Exception:  # noqa: BLE001
                img = None
            self._pics_bus.thumb.emit(iid, img)

        self._pics_thumb_reader.submit(work)

    def _pics_on_thumb(self, iid, img):
        self._pics_thumb_loading.discard(iid)
        if img is None or img.isNull():
            return
        icon = QIcon(QPixmap.fromImage(img))
        self._pics_thumbs[iid] = icon
        g = self.pics_grid
        for i in range(g.count()):
            if g.item(i).data(Qt.UserRole) == iid:
                g.item(i).setIcon(icon)
                break

    def _pics_grid_sel_changed(self):
        n = len(self.pics_grid.selectedItems())
        total = self.pics_grid.count()
        self.pics_grid_sel.setText(f"共 {total} 张" + (f"，已选 {n} 张" if n else "，点选或框选图片"))
        self.pics_grid_keep.setEnabled(n > 0)
        self.pics_grid_drop.setEnabled(n > 0)
        self.pics_grid_undo.setEnabled(bool(self._pics_undo_stack))

    def _pics_grid_decide(self, keep):
        g = self.pics_grid
        ids = g.selected_ids()
        if not ids:
            return
        idset = set(ids)
        rows = sorted((g.row(it) for it in g.selectedItems()))
        next_row = rows[0] if rows else 0
        items = [r for r in self._pics_queue if r["id"] in idset]
        self._pics_queue = [r for r in self._pics_queue if r["id"] not in idset]
        self._pics_undo_stack.append([(r["id"], r) for r in items])
        self._pics_undo_stack = self._pics_undo_stack[-50:]
        g.setUpdatesEnabled(False)
        for r in sorted(rows, reverse=True):
            g.takeItem(r)
        g.setUpdatesEnabled(True)
        if g.count():  # 光标落到下一张，方便连续操作
            g.clearSelection()
            g.setCurrentRow(min(next_row, g.count() - 1), QItemSelectionModel.NoUpdate)
        self._pics_grid_sel_changed()
        self.pics_undo_btn.setEnabled(True)

        def work():
            from furina.services import pic_inbox as svc
            busy = False
            for iid in ids:
                res = svc.decide(iid, bool(keep))
                busy |= res.get("reason") == "busy"
            self._pics_bus.stats.emit(svc.stats())
            if busy:
                self._pics_bus.items.emit({"rows": svc.pending(1000), "names": {}, "stats": svc.stats()})

        self._pics_exec.submit(work)

    def _pics_grid_open(self, item):
        iid = item.data(Qt.UserRole)
        idx = next((i for i, r in enumerate(self._pics_queue) if r["id"] == iid), None)
        if idx is None:
            return
        self._pics_queue.insert(0, self._pics_queue.pop(idx))
        self._pics_toggle_mode()

    def _pics_open_current(self):
        if not self._pics_queue:
            return
        try:
            from furina.services import pic_inbox as svc
            it = svc.get(self._pics_queue[0]["id"])
            if it:
                os.startfile(str(svc.file_path(it)))  # noqa: S606  用系统看图软件打开原图
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "打开原图", str(e))

    def _pics_phone_dialog(self):
        from furina.services import pic_inbox as svc

        dlg = QDialog(self)
        dlg.setWindowTitle("手机连接")
        dlg.setMinimumWidth(460)
        dlg.setStyleSheet("QDialog{background:#111827;} QLabel{color:#dbe4f3;} "
                          "QLineEdit{background:#0b1222;color:#f4f7ff;border:1px solid #334155;border-radius:6px;padding:6px;}")
        lay = QVBoxLayout(dlg)
        need_pw = svc.require_password()
        lay.addWidget(QLabel("在手机「芙芙筛图」App 里填这个地址（在外面也能用）：" if not need_pw
                             else "在手机「芙芙筛图」App 里填这两项（在外面也能用）："))
        url = QLineEdit(svc.public_url())
        url.setReadOnly(True)
        pw = QLineEdit(svc.token())
        pw.setReadOnly(True)
        for title, edit in ((("地址", url), ("密码", pw)) if need_pw else (("地址", url),)):
            row = QHBoxLayout()
            row.addWidget(QLabel(title))
            row.addWidget(edit, 1)
            row.addWidget(self._tool_button("复制", lambda _=False, e=edit: QGuiApplication.clipboard().setText(e.text())))
            lay.addLayout(row)
        note = QLabel("换密码后，手机上要重新填一次。" if need_pw
                      else "现在不需要密码；想加密码可以在「设置 → 搜图与强化 → 手机连接要密码」打开。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{_MUTED};")
        lay.addWidget(note)
        download = svc.public_url().rstrip("/") + "/app"
        install = QLineEdit(download)
        install.setReadOnly(True)
        lay.addWidget(QLabel("安卓安装包（手机浏览器打开，允许安装即可）："))
        install_row = QHBoxLayout()
        install_row.addWidget(install, 1)
        install_row.addWidget(self._tool_button("复制安装地址", lambda: QGuiApplication.clipboard().setText(download)))
        lay.addLayout(install_row)
        btns = QHBoxLayout()
        btns.addStretch(1)

        def regen():
            pw.setText(svc.set_token())

        if need_pw:
            btns.addWidget(self._tool_button("换一个密码", regen))
        btns.addWidget(self._tool_button("关闭", dlg.accept))
        lay.addLayout(btns)
        dlg.exec()
