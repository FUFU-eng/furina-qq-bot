"""「群友活跃」页（PanelWindow 的 mixin）：每个群友用了机器人多少。

数据来自机器人按人记的用量（furina/core/usage.py 的 who:*，永久保留）：艾特聊天、JM 本子、强化（次 / 张）、搜图、戳一戳。
每项可以设权重（比如强化最费电就调高），算出每人的占比；填上总额就按占比折算到每个人。
读库在后台线程，不卡界面；改权重 / 总额即时重算，不用重新读。
"""
import threading

from PySide6.QtCore import QObject
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QHeaderView, QListView, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem,
)

from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403

_MUTED = "#8193b2"
_RANGES = (("近 30 天", 30), ("近 7 天", 7), ("今天", 1), ("全部", 0))
# 表格列：(标题, 计数键 / 特殊)
_HEAD = ("#", "群友", "QQ", "艾特聊天", "JM 本子", "强化（张 / 次）", "搜图", "戳一戳", "加权分", "占比", "折算")
_KEYS = ("chat", "jm", "upscale_img", "search", "poke")


def _arrows() -> str:
    """下拉 / 微调框的小箭头图片目录（和设置窗口共用一套）。"""
    try:
        from ..settings import _ensure_assets
        return _ensure_assets()
    except Exception:  # noqa: BLE001
        return ""


def _spin_css(a: str) -> str:
    arrows = (f"QDoubleSpinBox::up-arrow{{image:url({a}/up.png);width:9px;height:5px;}}"
              f"QDoubleSpinBox::down-arrow{{image:url({a}/down.png);width:9px;height:5px;}}"
              f"QDoubleSpinBox::up-arrow:hover{{image:url({a}/up_hover.png);}}"
              f"QDoubleSpinBox::down-arrow:hover{{image:url({a}/down_hover.png);}}") if a else ""
    return ("QDoubleSpinBox{background:#0b1526;color:#eef3fb;border:1px solid rgba(255,255,255,0.10);border-radius:10px;"
            "padding:5px 24px 5px 8px;min-height:22px;selection-background-color:rgba(106,212,255,0.35);}"
            "QDoubleSpinBox:hover{border-color:rgba(255,255,255,0.20);}"
            "QDoubleSpinBox:focus{border-color:rgba(106,212,255,0.75);}"
            "QDoubleSpinBox::up-button{subcontrol-origin:border;subcontrol-position:top right;width:22px;border:none;"
            "border-top-right-radius:10px;background:transparent;}"
            "QDoubleSpinBox::down-button{subcontrol-origin:border;subcontrol-position:bottom right;width:22px;border:none;"
            "border-bottom-right-radius:10px;background:transparent;}"
            "QDoubleSpinBox::up-button:hover,QDoubleSpinBox::down-button:hover{background:rgba(255,255,255,0.07);}" + arrows)


def _combo_css(a: str) -> str:
    arrow = (f"QComboBox::down-arrow{{image:url({a}/down.png);width:10px;height:6px;margin-right:12px;}}"
             f"QComboBox::down-arrow:on{{image:url({a}/up.png);}}") if a else ""
    return ("QComboBox{background:#0b1526;color:#eef3fb;border:1px solid rgba(255,255,255,0.10);border-radius:10px;"
            "padding:6px 32px 6px 14px;min-height:22px;font-size:13px;}"
            "QComboBox:hover{border-color:rgba(106,212,255,0.45);background:#0f1c30;}"
            "QComboBox:on{border-color:rgba(106,212,255,0.75);}"
            "QComboBox::drop-down{border:none;width:30px;background:transparent;}" + arrow +
            # 弹出的列表：圆角卡片 + 每项圆角高亮
            "QComboBox QAbstractItemView{background:#101c31;color:#dbe6f5;border:1px solid rgba(106,212,255,0.28);"
            "border-radius:12px;padding:6px;outline:none;}"
            "QComboBox QAbstractItemView::item{min-height:32px;padding:0 12px;border-radius:8px;margin:1px 0;}"
            "QComboBox QAbstractItemView::item:hover{background:rgba(106,212,255,0.12);color:#ffffff;}"
            "QComboBox QAbstractItemView::item:selected{background:rgba(106,212,255,0.22);color:#ffffff;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:6px 2px;}"
            "QScrollBar::handle:vertical{background:rgba(255,255,255,0.16);border-radius:3px;min-height:24px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")


def _round_combo(css: str, max_width: int = 0) -> QComboBox:
    """圆角下拉框：弹出层去掉系统方框和阴影、背景透明，圆角才露得出来；太长的群名截断显示。"""
    combo = QComboBox()
    view = QListView()
    view.setTextElideMode(Qt.ElideRight)
    view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    combo.setView(view)
    combo.setStyleSheet(css)
    combo.setCursor(Qt.PointingHandCursor)
    combo.setMaxVisibleItems(12)
    popup = view.window()
    popup.setWindowFlags(popup.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    popup.setAttribute(Qt.WA_TranslucentBackground, True)
    popup.setObjectName("roundComboPopup")  # 只让外层容器透明，列表本身保留深色圆角底
    popup.setStyleSheet("#roundComboPopup{background:transparent;border:none;}")
    if max_width:
        combo.setMaximumWidth(max_width)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)
    return combo


class _UsageBus(QObject):
    done = Signal(object)


class _NumItem(QTableWidgetItem):
    """按数字排序的单元格（显示文字可以带单位）。"""

    def __init__(self, text, value):
        super().__init__(text)
        self._v = float(value or 0)
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        if isinstance(other, _NumItem):
            return self._v < other._v
        return super().__lt__(other)


class _ShareDelegate(QStyledItemDelegate):
    """占比列：底下画一条进度条。"""

    def paint(self, painter, option, index):
        try:
            v = float(index.data(Qt.UserRole) or 0)
        except (TypeError, ValueError):
            v = 0.0
        painter.save()
        r = option.rect.adjusted(6, 7, -6, -7)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 18))
        painter.drawRoundedRect(QRectF(r), 4, 4)
        if v > 0:
            w = max(3.0, r.width() * min(1.0, v))
            painter.setBrush(QColor(106, 212, 255, 120))
            painter.drawRoundedRect(QRectF(r.x(), r.y(), w, r.height()), 4, 4)
        painter.setPen(QColor("#f4f7ff"))
        painter.drawText(option.rect.adjusted(6, 0, -10, 0), Qt.AlignRight | Qt.AlignVCenter, f"{v * 100:.1f}%")
        painter.restore()


class UsageRankPageMixin:
    def _build_usage_rank_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        head = QHBoxLayout()
        self.usage_status = self._label("每个群友用了多少：艾特聊天、JM 本子、强化、搜图、戳一戳", "pageSubLabel", 11)
        self.usage_status.setWordWrap(True)
        head.addWidget(self.usage_status, 1)
        arrows = _arrows()
        combo_css = _combo_css(arrows)
        self.usage_range = _round_combo(combo_css)
        self.usage_range.setMinimumWidth(116)
        for name, days in _RANGES:
            self.usage_range.addItem(name, days)
        self.usage_range.currentIndexChanged.connect(lambda *_: self.refresh_usage_rank(force=True))
        head.addWidget(self.usage_range)
        self.usage_group = _round_combo(combo_css, max_width=280)
        self.usage_group.setMinimumWidth(200)
        self.usage_group.addItem("全部群", "")
        self.usage_group.currentIndexChanged.connect(lambda *_: self.refresh_usage_rank(force=True))
        head.addWidget(self.usage_group)
        self.usage_refresh_btn = self._tool_button("刷新", lambda: self.refresh_usage_rank(force=True))
        head.addWidget(self.usage_refresh_btn)
        self.usage_copy_btn = self._tool_button("复制表格", self._copy_usage_table)
        head.addWidget(self.usage_copy_btn)
        layout.addLayout(head)

        # 权重 + 总额
        wbox = QFrame()
        wbox.setObjectName("contentSurface")
        wl = QHBoxLayout(wbox)
        wl.setContentsMargins(14, 8, 14, 8)
        wl.setSpacing(8)
        wl.addWidget(self._label("权重（一次算几分）", "sectionLabel", 11))
        self.usage_weight_spins = {}
        labels = {"chat": "艾特聊天", "jm": "JM", "upscale_img": "强化每张", "search": "搜图", "poke": "戳一戳"}
        try:
            from ...services import data as _data
            saved = _data.usage_weights()
            saved_total = _data.usage_total()
        except Exception:  # noqa: BLE001
            saved, saved_total = {}, 0.0
        for k in _KEYS:
            lab = QLabel(labels[k])
            lab.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:12px;")
            wl.addWidget(lab)
            sp = QDoubleSpinBox()
            sp.setRange(0, 20)
            sp.setDecimals(1)
            sp.setSingleStep(0.5)
            sp.setFixedWidth(96)
            sp.setStyleSheet(_spin_css(arrows))
            sp.setValue(float(saved.get(k, 1.0 if k != "poke" else 0.0)))
            sp.valueChanged.connect(lambda *_: self._render_usage_rank())
            wl.addWidget(sp)
            self.usage_weight_spins[k] = sp
        wl.addSpacing(10)
        lab = QLabel("总额 ¥")
        lab.setStyleSheet("background:transparent; color:#ffd166; font-size:12px;")
        wl.addWidget(lab)
        self.usage_total_spin = QDoubleSpinBox()
        self.usage_total_spin.setRange(0, 1_000_000)
        self.usage_total_spin.setDecimals(2)
        self.usage_total_spin.setSingleStep(10)
        self.usage_total_spin.setFixedWidth(112)
        self.usage_total_spin.setStyleSheet(_spin_css(arrows))
        self.usage_total_spin.setValue(float(saved_total or 0))
        self.usage_total_spin.setToolTip("填了就按占比折算到每个人；0 = 不折算")
        self.usage_total_spin.valueChanged.connect(lambda *_: self._render_usage_rank())
        wl.addWidget(self.usage_total_spin)
        wl.addStretch(1)
        save = QPushButton("保存")
        save.setObjectName("primaryBtn")
        save.setCursor(Qt.PointingHandCursor)

        def _save():
            try:
                from ...services import data as _data
                _data.save_usage_weights({k: sp.value() for k, sp in self.usage_weight_spins.items()})
                _data.save_usage_total(self.usage_total_spin.value())
                save.setText("已保存 ✓")
            except Exception as e:  # noqa: BLE001
                save.setText("保存失败")
                self.usage_status.setText(f"保存失败：{e}")
            QTimer.singleShot(1500, lambda: save.setText("保存"))

        save.clicked.connect(_save)
        wl.addWidget(save)
        layout.addWidget(wbox)

        box = QFrame()
        box.setObjectName("contentSurface")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 10, 10, 8)
        bl.setSpacing(6)
        self.usage_table = QTableWidget(0, len(_HEAD))
        self.usage_table.setHorizontalHeaderLabels(list(_HEAD))
        self.usage_table.verticalHeader().setVisible(False)
        self.usage_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.usage_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.usage_table.setAlternatingRowColors(False)
        self.usage_table.setShowGrid(False)
        self.usage_table.setSortingEnabled(True)
        self.usage_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.usage_table.verticalHeader().setDefaultSectionSize(32)
        hh = self.usage_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(9, QHeaderView.Fixed)
        hh.setSortIndicator(0, Qt.AscendingOrder)  # 默认按排名从高到低（第 1 名在最上面）
        self.usage_table.setColumnWidth(9, 150)
        self.usage_table.setItemDelegateForColumn(9, _ShareDelegate(self.usage_table))
        self.usage_table.setStyleSheet(
            "QTableWidget{background:transparent;color:#dbe4f3;border:none;font-size:13px;"
            "selection-background-color:rgba(106,212,255,0.16);selection-color:#f4f7ff;}"
            "QTableWidget::item{padding:2px 6px;border-bottom:1px solid rgba(255,255,255,0.05);}"
            "QHeaderView::section{background:transparent;color:#a9b7cc;border:none;"
            "border-bottom:1px solid rgba(255,255,255,0.12);padding:6px;font-size:12px;}")
        bl.addWidget(self.usage_table, 1)
        self.usage_foot = QLabel("")
        self.usage_foot.setWordWrap(True)
        self.usage_foot.setTextFormat(Qt.RichText)
        self.usage_foot.setStyleSheet(f"background:transparent; color:{_MUTED}; font-size:11px;")
        bl.addWidget(self.usage_foot)
        layout.addWidget(box, 1)

        self._usage_bus = _UsageBus(page)
        self._usage_bus.done.connect(self._on_usage_rank)
        self._usage_busy = False
        self._usage_data = {}
        return page

    # ---------------- 数据 ----------------
    def refresh_usage_rank(self, force=False):
        if self._usage_busy:
            return
        if not force and self._usage_data:
            return
        self._usage_busy = True
        self.usage_refresh_btn.setText("读取中…")
        days = self.usage_range.currentData()
        gid = self.usage_group.currentData() or ""

        def work():
            try:
                from ...services import data as _data
                res = _data.member_usage(int(days or 0), gid)
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)}
            self._usage_bus.done.emit(res)

        threading.Thread(target=work, daemon=True).start()

    def _on_usage_rank(self, res):
        self._usage_busy = False
        self.usage_refresh_btn.setText("刷新")
        res = res or {}
        if res.get("error"):
            self.usage_status.setText(f"读取失败：{res['error']}")
            return
        self._usage_data = res
        # 群下拉：保持当前选择
        cur = self.usage_group.currentData() or ""
        groups = res.get("groups") or []
        if self.usage_group.count() - 1 != len(groups):
            self.usage_group.blockSignals(True)
            self.usage_group.clear()
            self.usage_group.addItem("全部群", "")
            for g in groups:
                self.usage_group.addItem(g.get("name") or g.get("gid"), g.get("gid"))
                self.usage_group.setItemData(self.usage_group.count() - 1, g.get("name") or g.get("gid"), Qt.ToolTipRole)
            i = self.usage_group.findData(cur)
            self.usage_group.setCurrentIndex(max(0, i))
            self.usage_group.blockSignals(False)
        self._render_usage_rank()

    def _usage_rows(self):
        """按当前权重 / 总额重算：[(行, 分, 占比, 折算)]。"""
        rows = list((self._usage_data or {}).get("rows") or [])
        w = {k: sp.value() for k, sp in self.usage_weight_spins.items()}
        scored = []
        for r in rows:
            c = r.get("counts") or {}
            scored.append((r, sum(float(c.get(k, 0)) * w.get(k, 0.0) for k in _KEYS)))
        total_score = sum(s for _r, s in scored)
        money = self.usage_total_spin.value()
        out = []
        for r, s in scored:
            share = s / total_score if total_score > 0 else 0.0
            out.append((r, s, share, round(money * share, 2) if money > 0 else None))
        out.sort(key=lambda x: (-x[1], -sum((x[0].get("counts") or {}).values())))
        return out

    def _render_usage_rank(self):
        if not hasattr(self, "usage_table"):
            return
        data = self._usage_rows()
        t = self.usage_table
        hh = t.horizontalHeader()
        sort_col, sort_order = hh.sortIndicatorSection(), hh.sortIndicatorOrder()
        t.setSortingEnabled(False)
        t.setRowCount(len(data))
        for i, (r, score, share, pay) in enumerate(data):
            c = r.get("counts") or {}
            cells = [
                _NumItem(str(i + 1), i + 1),
                QTableWidgetItem(r.get("nick") or r.get("uid")),
                QTableWidgetItem(str(r.get("uid"))),
                _NumItem(str(c.get("chat", 0)), c.get("chat", 0)),
                _NumItem(str(c.get("jm", 0)), c.get("jm", 0)),
                _NumItem(f"{c.get('upscale_img', 0)} / {c.get('upscale', 0)}", c.get("upscale_img", 0)),
                _NumItem(str(c.get("search", 0)), c.get("search", 0)),
                _NumItem(str(c.get("poke", 0)), c.get("poke", 0)),
                _NumItem(f"{score:g}", score),
                _NumItem("", share),
                _NumItem("--" if pay is None else f"¥{pay:.2f}", pay or 0),
            ]
            cells[9].setData(Qt.UserRole, share)
            cells[2].setForeground(QColor(_MUTED))
            if pay is not None:
                cells[10].setForeground(QColor("#ffd166"))
            for j, it in enumerate(cells):
                t.setItem(i, j, it)
        t.setSortingEnabled(True)
        t.sortItems(sort_col if 0 <= sort_col < len(_HEAD) else 0, sort_order)
        res = self._usage_data or {}
        tot = res.get("totals") or {}
        label = self.usage_range.currentText()
        if data:
            self.usage_status.setText(
                f"{label}：{len(data)} 人用过 · 艾特聊天 {tot.get('chat', 0)} · JM {tot.get('jm', 0)} · "
                f"强化 {tot.get('upscale_img', 0)} 张 / {tot.get('upscale', 0)} 次 · 搜图 {tot.get('search', 0)} · 戳一戳 {tot.get('poke', 0)}")
        else:
            self.usage_status.setText(f"{label}还没有按人的使用记录")
        first = res.get("first") or {}
        names = {"chat": "艾特聊天", "jm": "JM", "upscale": "强化", "search": "搜图", "poke": "戳一戳"}
        since = " · ".join(f"{names[k]} {first[k][5:]} 起" for k in names if first.get(k))
        self.usage_foot.setText(
            ("记录起始：" + since + "。" if since else "") +
            "早期的搜图 / 强化是从日志里补的（按触发算）；以前群主和特别关照的人不限次数，旧版没给他们记艾特聊天和戳一戳。"
            "机器人号自己不算。点表头可以排序。")

    def _copy_usage_table(self):
        data = self._usage_rows()
        if not data:
            return
        w = {k: sp.value() for k, sp in self.usage_weight_spins.items()}
        money = self.usage_total_spin.value()
        lines = [f"群友活跃（{self.usage_range.currentText()}"
                 + (f"，{self.usage_group.currentText()}" if self.usage_group.currentData() else "") + "）",
                 f"权重：艾特聊天×{w['chat']:g} JM×{w['jm']:g} 强化每张×{w['upscale_img']:g} 搜图×{w['search']:g} 戳一戳×{w['poke']:g}"
                 + (f"；总额 ¥{money:.2f}" if money > 0 else "")]
        for i, (r, _s, share, pay) in enumerate(data, 1):
            if share <= 0:
                continue
            c = r.get("counts") or {}
            lines.append(f"{i}. {r.get('nick')}（{r.get('uid')}）聊天{c.get('chat', 0)} JM{c.get('jm', 0)} "
                         f"强化{c.get('upscale_img', 0)}张 搜图{c.get('search', 0)} → {share * 100:.1f}%"
                         + (f" ≈ ¥{pay:.2f}" if pay is not None else ""))
        QApplication.clipboard().setText("\n".join(lines))
        self.usage_copy_btn.setText("已复制 ✓")
        QTimer.singleShot(1500, lambda: self.usage_copy_btn.setText("复制表格"))
