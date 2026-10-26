"""设置中心（2026-09 重做）：左侧导航 + 右侧分页，所有设置在同一个窗口内完成。

替代原来的「设置」卡片页及其弹出的多个子窗口（QQ 号管理、模型调用、Pixiv、提醒、壁纸库），
统一配色、间距和控件样式：
- 账号与角色：当前账号、账号列表、自动获取昵称、绑定角色、选用 / 删除；
- AI 模型：接口地址、密钥（可显示/隐藏）、连接测试；
- Pixiv 插画：服务开关、5 个批次时间、发送与缓存、标签映射（实时校验）、运行记录；
- 定时提醒：服务开关、自然语言辅助、额度与冷却、运行状态、群友用法；
- 外观：壁纸库（缩略图 + 预览）、轮播间隔、恢复默认背景。
需要保存的页面在底部统一提供「还原 / 保存」，只有改动过才可点；关闭窗口时若有未保存的修改会先询问。
"""
import json
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import (
    Property, QEasingCurve, QObject, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt,
    QTime, QTimer, QUrl, Signal,
)
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTimeEdit, QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------- 视觉规范
GOLD = "#ffd166"
CYAN = "#6ad4ff"
GREEN = "#3ecf8e"
RED = "#ff6b6b"
AMBER = "#f5b041"
MUTED = "#8b9bb6"
FONT_STACK = '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif'
MONO_STACK = '"Cascadia Mono", Consolas, "Noto Sans Mono CJK SC", monospace'

_STYLE_TEMPLATE = """
QDialog#settingsCenter {{ background: #0f172a; }}
QWidget {{ font-family: @FONT@; font-size: 13px; color: #e8eef8; }}
QWidget#sidebar {{ background: #0b1222; border-right: 1px solid rgba(255,255,255,0.06); }}
QWidget#content, QWidget#pageBody, QStackedWidget#pageStack, QStackedWidget#subStack {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLabel#brandTitle {{ font-size: 19px; font-weight: 700; color: #f8fafc; }}
QLabel#brandSub {{ font-size: 12px; color: #6f809c; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ border-radius: 10px; margin: 2px 10px; }}
QListWidget#nav::item:hover {{ background: rgba(255,255,255,0.04); }}
QListWidget#nav::item:selected {{ background: rgba(255,209,102,0.10); }}
QLabel#navTitle {{ font-size: 13px; font-weight: 700; color: #c6d1e2; }}
QLabel#navTitle[active="true"] {{ color: @GOLD@; }}
QLabel#navSub {{ font-size: 11px; color: #66768f; }}
QLineEdit#navSearch {{ background: #111b2e; border: 1px solid rgba(255,255,255,0.08); border-radius: 9px; padding: 6px 10px; color: #e8eef8; }}
QLineEdit#navSearch:focus {{ border-color: rgba(255,209,102,0.55); }}
QListWidget#searchResults {{ background: transparent; border: none; outline: none; }}
QListWidget#searchResults::item {{ border-radius: 10px; margin: 2px 10px; color: #66768f; }}
QListWidget#searchResults::item:hover {{ background: rgba(255,255,255,0.04); }}
QListWidget#searchResults::item:selected {{ background: rgba(255,209,102,0.10); }}
QLabel#searchHit {{ font-size: 13px; font-weight: 700; color: #d3dcea; }}
QLabel#pageTitle {{ font-size: 20px; font-weight: 700; color: #f8fafc; }}
QLabel#pageDesc {{ font-size: 12px; color: @MUTED@; }}
QFrame#card {{ background: #131e33; border: 1px solid rgba(255,255,255,0.07); border-radius: 12px; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 700; color: #f1f5f9; }}
QLabel#cardDesc {{ font-size: 12px; color: @MUTED@; }}
QLabel#fieldLabel {{ font-size: 13px; color: #d3dcea; }}
QLabel#help {{ font-size: 12px; color: #7d8ca6; }}
QLabel#bullet {{ font-size: 12px; color: #a9b6ca; }}
QLabel#bigName {{ font-size: 17px; font-weight: 700; color: #f8fafc; }}
QLabel#pill {{ background: #0b1526; border: 1px solid rgba(255,255,255,0.08); border-radius: 11px;
              padding: 4px 12px; min-height: 18px; color: #c9d4e6; font-size: 12px; }}
QLabel#chip {{ border-radius: 9px; padding: 2px 10px; min-height: 18px; font-size: 12px; font-weight: 700; }}
QLabel#chip[tone="on"] {{ background: rgba(62,207,142,0.14); color: @GREEN@; }}
QLabel#chip[tone="off"] {{ background: rgba(148,163,184,0.12); color: #94a3b8; }}
QLabel#chip[tone="warn"] {{ background: rgba(245,176,65,0.15); color: @AMBER@; }}
QLabel#chip[tone="info"] {{ background: rgba(106,212,255,0.13); color: @CYAN@; }}
QLabel#status {{ font-size: 12px; color: @MUTED@; }}
QLabel#status[tone="ok"] {{ color: @GREEN@; }}
QLabel#status[tone="err"] {{ color: @RED@; }}
QLabel#status[tone="warn"] {{ color: @AMBER@; }}
QLabel#status[tone="info"] {{ color: @CYAN@; }}
QLabel#preview {{ background: #0b1526; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; color: #6f809c; }}
QLineEdit, QSpinBox, QTimeEdit, QComboBox, QPlainTextEdit {{
    background: #0b1526; color: #eef3fb; border: 1px solid rgba(255,255,255,0.10); border-radius: 8px;
    padding: 6px 10px; selection-background-color: rgba(106,212,255,0.35); }}
QLineEdit:hover, QSpinBox:hover, QTimeEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: rgba(255,255,255,0.18); }}
QLineEdit:focus, QSpinBox:focus, QTimeEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: rgba(106,212,255,0.75); }}
QLineEdit:read-only {{ color: #9aa9c0; background: #0e192c; }}
QLineEdit, QSpinBox, QTimeEdit, QComboBox {{ min-height: 22px; }}
QPlainTextEdit#code {{ font-family: @MONO@; font-size: 12px; }}
QSpinBox, QTimeEdit {{ padding-right: 24px; }}
QSpinBox::up-button, QTimeEdit::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 22px;
    border: none; border-top-right-radius: 8px; background: transparent; }}
QSpinBox::down-button, QTimeEdit::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 22px;
    border: none; border-bottom-right-radius: 8px; background: transparent; }}
QSpinBox::up-button:hover, QTimeEdit::up-button:hover, QSpinBox::down-button:hover, QTimeEdit::down-button:hover {{
    background: rgba(255,255,255,0.06); }}
QSpinBox::up-arrow, QTimeEdit::up-arrow {{ image: url(@ASSETS@/up.png); width: 9px; height: 5px; }}
QSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: url(@ASSETS@/down.png); width: 9px; height: 5px; }}
QSpinBox::up-arrow:hover, QTimeEdit::up-arrow:hover {{ image: url(@ASSETS@/up_hover.png); }}
QSpinBox::down-arrow:hover, QTimeEdit::down-arrow:hover {{ image: url(@ASSETS@/down_hover.png); }}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off, QTimeEdit::up-arrow:disabled, QTimeEdit::up-arrow:off {{ image: url(@ASSETS@/up_off.png); }}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off, QTimeEdit::down-arrow:disabled, QTimeEdit::down-arrow:off {{ image: url(@ASSETS@/down_off.png); }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 26px; background: transparent; }}
QComboBox::down-arrow {{ image: url(@ASSETS@/down.png); width: 10px; height: 6px; margin-right: 8px; }}
QComboBox::down-arrow:on {{ image: url(@ASSETS@/up.png); }}
QComboBox QAbstractItemView {{ background: #0f1b30; color: #e8eef8; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 8px; padding: 4px; outline: none; selection-background-color: rgba(106,212,255,0.18); }}
QPushButton {{ background: #1a2740; color: #dbe6f5; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px;
    padding: 7px 16px; }}
QPushButton:hover {{ background: #22344f; border-color: rgba(106,212,255,0.35); color: #ffffff; }}
QPushButton:pressed {{ background: #1c2c45; }}
QPushButton:disabled {{ color: #56657d; background: #141e31; border-color: rgba(255,255,255,0.04); }}
QPushButton#primary {{ background: @GOLD@; color: #1d1706; border: none; font-weight: 700; padding: 7px 20px; }}
QPushButton#primary:hover {{ background: #ffe08a; }}
QPushButton#primary:pressed {{ background: #f2c14f; }}
QPushButton#primary:disabled {{ background: #34352c; color: #7f7a60; }}
QPushButton#danger {{ color: #ff9d9d; }}
QPushButton#danger:hover {{ background: rgba(255,107,107,0.14); border-color: rgba(255,107,107,0.50); color: #ffc2c2; }}
QPushButton#link {{ background: transparent; border: none; color: @CYAN@; padding: 4px 6px; }}
QPushButton#link:hover {{ color: #9ce3ff; text-decoration: underline; }}
QFrame#segBar {{ background: #0b1526; border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; }}
QPushButton#seg {{ background: transparent; border: none; color: #93a4bf; padding: 7px 18px; border-radius: 8px; }}
QPushButton#seg:hover {{ color: #e8eef8; }}
QPushButton#seg:checked {{ background: #22344f; color: @GOLD@; font-weight: 700; }}
QWidget#footer {{ background: #0c1527; border-top: 1px solid rgba(255,255,255,0.06); }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.12); border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.22); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
QListWidget#plain {{ background: #0b1526; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 4px; outline: none; }}
QListWidget#plain::item {{ padding: 9px 10px; border-radius: 8px; color: #dbe6f5; margin: 1px 0; }}
QListWidget#plain::item:hover {{ background: rgba(255,255,255,0.04); }}
QListWidget#plain::item:selected {{ background: rgba(106,212,255,0.16); color: #ffffff; }}
QListWidget#gallery {{ background: #0b1526; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 8px; outline: none; }}
QListWidget#gallery::item {{ color: #b8c4d8; border-radius: 8px; padding: 4px; }}
QListWidget#gallery::item:hover {{ background: rgba(255,255,255,0.05); }}
QListWidget#gallery::item:selected {{ background: rgba(255,209,102,0.16); color: #ffffff; border: 1px solid rgba(255,209,102,0.55); }}
QToolTip {{ background: #1a2740; color: #e8eef8; border: 1px solid rgba(255,255,255,0.12); padding: 6px 8px; }}
QMessageBox {{ background: #0f172a; }}
QMessageBox QLabel {{ color: #e8eef8; }}
"""

_ASSET_VERSION = "v1"


def _ensure_assets():
    """生成下拉 / 微调框用的小箭头图片（Qt 样式表只接受图片文件，不支持 CSS 三角形）。"""
    folder = Path(tempfile.gettempdir()) / f"qqbot-settings-ui-{_ASSET_VERSION}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name, up, color in (("down", False, "#8b9bb6"), ("up", True, "#8b9bb6"),
                                ("down_hover", False, "#eef3fb"), ("up_hover", True, "#eef3fb"),
                                ("down_off", False, "#3c4a61"), ("up_off", True, "#3c4a61")):
            for scale, suffix in ((1, ""), (2, "@2x")):
                path = folder / f"{name}{suffix}.png"
                if path.exists():
                    continue
                w, h = 10 * scale, 6 * scale
                pix = QPixmap(w, h)
                pix.fill(Qt.transparent)
                p = QPainter(pix)
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(QColor(color), 1.6 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                m = 1.2 * scale
                if up:
                    p.drawPolyline([QPointF(m, h - m), QPointF(w / 2, m), QPointF(w - m, h - m)])
                else:
                    p.drawPolyline([QPointF(m, m), QPointF(w / 2, h - m), QPointF(w - m, m)])
                p.end()
                pix.save(str(path))
    except Exception:
        pass
    return folder.as_posix()


def build_style():
    return _STYLE_TEMPLATE.replace("{{", "{").replace("}}", "}").replace("@ASSETS@", _ensure_assets()) \
        .replace("@FONT@", FONT_STACK).replace("@MONO@", MONO_STACK).replace("@GOLD@", GOLD) \
        .replace("@CYAN@", CYAN).replace("@GREEN@", GREEN).replace("@RED@", RED).replace("@AMBER@", AMBER) \
        .replace("@MUTED@", MUTED)


class FlowLayout(QLayout):
    """自动换行的横向布局（用于示例标签）。"""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, test_only):
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


def set_prop(widget, name, value):
    """修改动态属性后重新套用样式（[tone="..."] 这类选择器才会刷新）。"""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _dark_title_bar(widget):
    """Windows 10 20H1+ / 11：把原生标题栏切成深色，和深色窗口一致；其它系统或调用失败时什么也不做。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.c_void_p(int(widget.winId()))
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE；19 是 Windows 10 20H1 之前的旧编号
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(on), ctypes.sizeof(on))
        # Windows 11 才支持：标题栏底色取侧栏色 #0b1222（COLORREF 为 0x00BBGGRR），Windows 10 返回错误码，忽略
        caption = ctypes.c_uint(0x0022120B)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
    except Exception:
        pass


def _message(parent, icon, title, text, buttons, labels):
    box = QMessageBox(icon, title, text, buttons, parent)
    for std, label in labels.items():
        btn = box.button(std)
        if btn is not None:
            btn.setText(label)
    _dark_title_bar(box)
    box.exec()
    clicked = box.clickedButton()
    return box.standardButton(clicked) if clicked is not None else QMessageBox.NoButton


def _ask(parent, title, text):
    """确认框（中文按钮、深色标题栏）；点「确定」返回 True。"""
    return _message(parent, QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.No,
                    {QMessageBox.Yes: "确定", QMessageBox.No: "取消"}) == QMessageBox.Yes


def _warn(parent, title, text):
    _message(parent, QMessageBox.Warning, title, text, QMessageBox.Ok, {QMessageBox.Ok: "知道了"})


def _label(text="", obj=None, wrap=False):
    lbl = QLabel(text)
    if obj:
        lbl.setObjectName(obj)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def _button(text, obj=None, slot=None, tip=None):
    btn = QPushButton(text)
    if obj:
        btn.setObjectName(obj)
    btn.setCursor(Qt.PointingHandCursor)
    if slot:
        btn.clicked.connect(slot)
    if tip:
        btn.setToolTip(tip)
    return btn


def _chip(text, tone="off"):
    chip = _label(text, "chip")
    set_prop(chip, "tone", tone)
    return chip


def _spin(low, high, suffix="", width=150):
    box = QSpinBox()
    box.setRange(low, high)
    if suffix:
        box.setSuffix(" " + suffix)
    box.setFixedWidth(width)
    box.setAccelerated(True)
    return box


def _polish_combo(combo):
    """下拉弹层的外层容器默认是 Fusion 浅色边框，刷成深色。"""
    container = combo.view().parentWidget()
    if container is not None:
        container.setStyleSheet("QFrame { background: #0f1b30; border: 1px solid rgba(255,255,255,0.10); border-radius: 8px; }")


def _nav_icon(kind, color):
    """导航用的线性小图标（直接绘制，不依赖字体里的 emoji）。"""
    size = 36
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color).darker(420))
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 9, 9)
    pen = QPen(QColor(color), 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    c = size / 2
    if kind == "account":
        p.drawEllipse(QPointF(c, c - 3.5), 4.2, 4.2)
        path = QPainterPath()
        path.moveTo(c - 8, c + 9)
        path.cubicTo(c - 7, c + 2.5, c + 7, c + 2.5, c + 8, c + 9)
        p.drawPath(path)
    elif kind == "model":
        star = QPainterPath()
        star.moveTo(c, c - 9)
        star.quadTo(c + 1.5, c - 1.5, c + 9, c)
        star.quadTo(c + 1.5, c + 1.5, c, c + 9)
        star.quadTo(c - 1.5, c + 1.5, c - 9, c)
        star.quadTo(c - 1.5, c - 1.5, c, c - 9)
        p.drawPath(star)
    elif kind == "pixiv":
        p.drawRoundedRect(QRectF(c - 9, c - 7, 18, 14), 3, 3)
        mountain = QPainterPath()
        mountain.moveTo(c - 7, c + 5)
        mountain.lineTo(c - 2, c - 1)
        mountain.lineTo(c + 1.5, c + 2.5)
        mountain.lineTo(c + 4, c)
        mountain.lineTo(c + 7, c + 5)
        p.drawPath(mountain)
        p.drawPoint(QPointF(c + 4, c - 3.5))
    elif kind == "reminder":
        p.drawEllipse(QPointF(c, c + 1), 8, 8)
        p.drawLine(QPointF(c, c + 1), QPointF(c, c - 3.5))
        p.drawLine(QPointF(c, c + 1), QPointF(c + 3.5, c + 3))
        p.drawLine(QPointF(c - 7, c - 7), QPointF(c - 4.5, c - 9))
        p.drawLine(QPointF(c + 7, c - 7), QPointF(c + 4.5, c - 9))
    elif kind == "appearance":
        p.drawRoundedRect(QRectF(c - 9, c - 7, 18, 13), 2.5, 2.5)
        p.drawLine(QPointF(c - 4, c + 9), QPointF(c + 4, c + 9))
        p.drawLine(QPointF(c, c + 6), QPointF(c, c + 9))
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(c - 3, c - 1.5), 1.6, 1.6)
    elif kind == "chat":
        bubble = QPainterPath()
        bubble.addRoundedRect(QRectF(c - 9, c - 8, 18, 13), 4, 4)
        p.drawPath(bubble)
        p.drawLine(QPointF(c - 4, c + 5), QPointF(c - 6, c + 9))
        p.drawLine(QPointF(c - 6, c + 9), QPointF(c, c + 5))
        for dx in (-4, 0, 4):
            p.drawPoint(QPointF(c + dx, c - 1.5))
    elif kind == "context":
        p.drawRoundedRect(QRectF(c - 8, c - 9, 16, 18), 3, 3)
        for dy in (-4, 0, 4):
            p.drawLine(QPointF(c - 4.5, c + dy), QPointF(c + 4.5 - (3 if dy == 4 else 0), c + dy))
    elif kind == "interact":
        heart = QPainterPath()
        heart.moveTo(c, c + 8)
        heart.cubicTo(c - 12, c, c - 7, c - 11, c, c - 4)
        heart.cubicTo(c + 7, c - 11, c + 12, c, c, c + 8)
        p.drawPath(heart)
    elif kind == "digest":
        p.drawRoundedRect(QRectF(c - 9, c - 8, 18, 17), 3, 3)
        p.drawLine(QPointF(c - 9, c - 3), QPointF(c + 9, c - 3))
        p.drawLine(QPointF(c - 4, c - 11), QPointF(c - 4, c - 6))
        p.drawLine(QPointF(c + 4, c - 11), QPointF(c + 4, c - 6))
        p.drawLine(QPointF(c - 4, c + 2.5), QPointF(c + 4, c + 2.5))
    elif kind == "search":
        p.drawEllipse(QPointF(c - 2, c - 2), 6.5, 6.5)
        p.drawLine(QPointF(c + 3, c + 3), QPointF(c + 8.5, c + 8.5))
    elif kind == "jm":
        p.drawLine(QPointF(c, c - 9), QPointF(c, c + 3))
        p.drawLine(QPointF(c - 5, c - 2), QPointF(c, c + 3))
        p.drawLine(QPointF(c + 5, c - 2), QPointF(c, c + 3))
        p.drawLine(QPointF(c - 9, c + 4), QPointF(c - 9, c + 8))
        p.drawLine(QPointF(c - 9, c + 8), QPointF(c + 9, c + 8))
        p.drawLine(QPointF(c + 9, c + 8), QPointF(c + 9, c + 4))
    elif kind == "push":
        p.drawLine(QPointF(c - 8, c - 2), QPointF(c - 8, c + 3))
        cone = QPainterPath()
        cone.moveTo(c - 8, c - 2)
        cone.lineTo(c + 6, c - 8)
        cone.lineTo(c + 6, c + 9)
        cone.lineTo(c - 8, c + 3)
        p.drawPath(cone)
        p.drawLine(QPointF(c - 4, c + 4.5), QPointF(c - 2.5, c + 9))
    elif kind == "miyoushe":
        p.drawRoundedRect(QRectF(c - 10, c - 6, 20, 13), 6, 6)
        p.drawLine(QPointF(c - 6, c + 0.5), QPointF(c - 2, c + 0.5))
        p.drawLine(QPointF(c - 4, c - 1.5), QPointF(c - 4, c + 2.5))
        p.drawPoint(QPointF(c + 4, c - 1))
        p.drawPoint(QPointF(c + 6, c + 2))
    elif kind == "brain":  # 模式与内核：芯片 + 引脚
        p.drawRoundedRect(QRectF(c - 6.5, c - 6.5, 13, 13), 3, 3)
        p.drawEllipse(QPointF(c, c), 2.4, 2.4)
        for d in (-3.5, 3.5):
            p.drawLine(QPointF(c + d, c - 6.5), QPointF(c + d, c - 10))
            p.drawLine(QPointF(c + d, c + 6.5), QPointF(c + d, c + 10))
            p.drawLine(QPointF(c - 6.5, c + d), QPointF(c - 10, c + d))
            p.drawLine(QPointF(c + 6.5, c + d), QPointF(c + 10, c + d))
    elif kind == "huoren":  # 活人感：笑脸 + 小火花
        p.drawEllipse(QPointF(c - 1.5, c + 1.5), 7.5, 7.5)
        p.drawPoint(QPointF(c - 4, c))
        p.drawPoint(QPointF(c + 1, c))
        smile = QPainterPath()
        smile.moveTo(c - 5, c + 3.5)
        smile.quadTo(c - 1.5, c + 7, c + 2, c + 3.5)
        p.drawPath(smile)
        p.drawLine(QPointF(c + 8, c - 10), QPointF(c + 8, c - 5))
        p.drawLine(QPointF(c + 5.5, c - 7.5), QPointF(c + 10.5, c - 7.5))
    elif kind == "voice":  # 语音：话筒
        p.drawRoundedRect(QRectF(c - 3.5, c - 10, 7, 12), 3.5, 3.5)
        arc = QPainterPath()
        arc.moveTo(c - 7.5, c - 2)
        arc.cubicTo(c - 7.5, c + 7, c + 7.5, c + 7, c + 7.5, c - 2)
        p.drawPath(arc)
        p.drawLine(QPointF(c, c + 5), QPointF(c, c + 9.5))
        p.drawLine(QPointF(c - 4, c + 9.5), QPointF(c + 4, c + 9.5))
    elif kind == "websearch":  # 联网搜索：地球
        p.drawEllipse(QPointF(c, c), 9, 9)
        p.drawEllipse(QRectF(c - 4, c - 9, 8, 18))
        p.drawLine(QPointF(c - 9, c), QPointF(c + 9, c))
    elif kind == "bili":  # B站：小电视
        p.drawRoundedRect(QRectF(c - 9.5, c - 5, 19, 14), 3.5, 3.5)
        p.drawLine(QPointF(c - 5, c - 10), QPointF(c - 2, c - 5.5))
        p.drawLine(QPointF(c + 5, c - 10), QPointF(c + 2, c - 5.5))
        p.drawLine(QPointF(c - 4.5, c), QPointF(c - 4.5, c + 3))
        p.drawLine(QPointF(c + 4.5, c), QPointF(c + 4.5, c + 3))
    else:  # other：三根滑杆
        for dy, kx in ((-6, 3), (0, -3), (6, 1)):
            p.drawLine(QPointF(c - 9, c + dy), QPointF(c + 9, c + dy))
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(c + kx, c + dy), 2.2, 2.2)
            p.setBrush(Qt.NoBrush)
    p.end()
    return pix


class ToggleSwitch(QCheckBox):
    """滑动开关。继承 QCheckBox，isChecked()/toggled 等接口不变。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get_knob(self):
        return self._pos

    def _set_knob(self, value):
        self._pos = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def sync(self):
        """程序设置状态后调用：直接跳到终点，不播动画。"""
        self._anim.stop()
        self._pos = 1.0 if self.isChecked() else 0.0
        self.update()

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off, on = QColor("#2b3953"), QColor(GOLD)
        t = self._pos
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        d = rect.height() - 6
        x = rect.left() + 3 + (rect.width() - 6 - d) * t
        p.setBrush(QColor("#ffffff") if t > 0.5 else QColor("#cdd6e4"))
        p.drawEllipse(QRectF(x, rect.top() + 3, d, d))
        p.end()


class _Bridge(QObject):
    """把后台线程的结果安全地送回界面线程。"""
    done = Signal(object, object)


# ---------------------------------------------------------------- 页面基类
class SettingsPage(QWidget):
    key = ""
    title = ""
    desc = ""
    savable = True
    footer_hint = ""

    def __init__(self, center):
        super().__init__()
        self.center = center
        self.panel = center.panel
        self._dirty = False
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(32, 26, 32, 14)
        hl.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(5)
        titles.addWidget(_label(self.title, "pageTitle"))
        titles.addWidget(_label(self.desc, "pageDesc", wrap=True))
        hl.addLayout(titles, 1)
        self.chip = _chip("", "off")
        self.chip.hide()
        hl.addWidget(self.chip, 0, Qt.AlignTop)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("pageBody")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(32, 4, 26, 24)
        self.body.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        footer = QWidget()
        footer.setObjectName("footer")
        footer.setAttribute(Qt.WA_StyledBackground, True)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(32, 12, 32, 12)
        fl.setSpacing(10)
        self.status = _label(self.footer_hint, "status")
        fl.addWidget(self.status, 1)
        if self.savable:
            self.revert_btn = _button("还原", slot=self.do_revert, tip="放弃未保存的修改，恢复为已保存的设置")
            self.save_btn = _button("保存", "primary", self.do_save)
            self.save_btn.setShortcut("Ctrl+S")
            fl.addWidget(self.revert_btn)
            fl.addWidget(self.save_btn)
            self.revert_btn.setEnabled(False)
            self.save_btn.setEnabled(False)
        outer.addWidget(footer)

        self.build()
        self.body.addStretch(1)
        self.reload()

    # 子类实现 ----------------------------------------------------------
    def build(self):
        pass

    def load(self):
        pass

    def save(self):
        pass

    # 通用 --------------------------------------------------------------
    def card(self, title=None, desc=None, into=None):
        frame = QFrame()
        frame.setObjectName("card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(12)
        if title or desc:
            head = QVBoxLayout()
            head.setSpacing(3)
            if title:
                head.addWidget(_label(title, "cardTitle"))
            if desc:
                head.addWidget(_label(desc, "cardDesc", wrap=True))
            lay.addLayout(head)
        (into if into is not None else self.body).addWidget(frame)
        return lay

    def toggle_row(self, layout, title, help_text):
        row = QHBoxLayout()
        row.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(_label(title, "fieldLabel"))
        if help_text:
            texts.addWidget(_label(help_text, "help", wrap=True))
        row.addLayout(texts, 1)
        sw = ToggleSwitch()
        row.addWidget(sw, 0, Qt.AlignVCenter)
        layout.addLayout(row)
        return sw

    def metric_grid(self, layout, items):
        """三列等宽的「标签 / 输入 / 说明」网格，比整行表单更紧凑。"""
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        for col, (label, widget, help_text) in enumerate(items):
            grid.addWidget(_label(label, "fieldLabel"), 0, col)
            grid.addWidget(widget, 1, col, Qt.AlignLeft)
            grid.addWidget(_label(help_text, "help", wrap=True), 2, col, Qt.AlignTop)
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

    def form_row(self, grid, row, label, widget, help_text=None):
        lbl = _label(label, "fieldLabel")
        lbl.setMinimumWidth(92)
        grid.addWidget(lbl, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(widget, row, 1)
        if help_text:
            grid.addWidget(_label(help_text, "help", wrap=True), row + 1, 1)

    def watch(self, *widgets):
        for w in widgets:
            if isinstance(w, QCheckBox):
                w.toggled.connect(self.mark_dirty)
            elif isinstance(w, QSpinBox):
                w.valueChanged.connect(self.mark_dirty)
            elif isinstance(w, QTimeEdit):
                w.timeChanged.connect(self.mark_dirty)
            elif isinstance(w, QLineEdit):
                w.textChanged.connect(self.mark_dirty)
            elif isinstance(w, QPlainTextEdit):
                w.textChanged.connect(self.mark_dirty)
            elif isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self.mark_dirty)

    def is_dirty(self):
        return self._dirty

    def mark_dirty(self, *_):
        if self._loading or not self.savable:
            return
        self.set_dirty(True)

    def set_dirty(self, dirty):
        self._dirty = dirty
        if self.savable:
            self.save_btn.setEnabled(dirty)
            self.revert_btn.setEnabled(dirty)
        if dirty:
            self.set_status("有未保存的修改", "warn")
        self.center.refresh_nav_marks()

    def set_status(self, text, tone=""):
        self.status.setText(text)
        set_prop(self.status, "tone", tone)

    def set_chip(self, text, tone):
        self.chip.setText(text)
        set_prop(self.chip, "tone", tone)
        self.chip.setVisible(bool(text))

    def reload(self):
        self._loading = True
        try:
            self.load()
        except Exception as exc:
            self.set_status(f"读取配置失败：{exc}（未覆盖原配置）", "err")
        finally:
            self._loading = False
        for sw in self.findChildren(ToggleSwitch):
            sw.sync()
        if self.savable:
            self.set_dirty(False)
            if self.status.property("tone") != "err":
                self.set_status(self.footer_hint, "")

    def do_save(self):
        try:
            message = self.save() or "已保存，实时生效"
        except Exception as exc:
            self.set_status(f"未保存：{exc}", "err")
            _warn(self, "无法保存", str(exc))
            return False
        self.set_dirty(False)
        self.set_status("✓ " + message, "ok")
        return True

    def do_revert(self):
        self.reload()
        self.set_status("已还原为上次保存的设置", "info")

    def log(self, level, text):
        fn = getattr(self.panel, "add_log", None)
        if callable(fn):
            try:
                fn(level, text)
            except Exception:
                pass


# ---------------------------------------------------------------- 账号与角色
class AccountPage(SettingsPage):
    key = "account"
    title = "账号与角色"
    desc = "机器人使用的 QQ 号、昵称和绑定的人格角色。选用其它账号时，会自动用该号重启 NapCat 和机器人。"
    savable = False
    footer_hint = "账号操作点按即生效；角色绑定需点「保存角色」。"

    def build(self):
        lay = self.card()
        row = QHBoxLayout()
        row.setSpacing(14)
        self.avatar = QLabel()
        self.avatar.setFixedSize(52, 52)
        row.addWidget(self.avatar)
        info = QVBoxLayout()
        info.setSpacing(3)
        self.cur_name = _label("", "bigName")
        self.cur_meta = _label("", "help")
        info.addWidget(self.cur_name)
        info.addWidget(self.cur_meta)
        row.addLayout(info, 1)
        self.cur_chip = _chip("当前使用", "on")
        row.addWidget(self.cur_chip, 0, Qt.AlignVCenter)
        lay.addLayout(row)

        lay = self.card("账号列表", "选中账号后可以修改绑定角色、选用或删除；输入新 QQ 号会自动从 NapCat 获取昵称。")
        cols = QHBoxLayout()
        cols.setSpacing(18)
        self.plist = QListWidget()
        self.plist.setObjectName("plain")
        self.plist.setMinimumWidth(250)
        self.plist.setMinimumHeight(230)
        self.plist.currentItemChanged.connect(lambda *_: self._on_select())
        cols.addWidget(self.plist, 5)

        form_box = QVBoxLayout()
        form_box.setSpacing(10)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.qq_edit = QLineEdit()
        self.qq_edit.setPlaceholderText("输入 QQ 号")
        self.fetch_btn = _button("获取昵称", slot=self._on_fetch)
        qq_row = QHBoxLayout()
        qq_row.setSpacing(8)
        qq_row.addWidget(self.qq_edit, 1)
        qq_row.addWidget(self.fetch_btn)
        grid.addWidget(_label("QQ 号", "fieldLabel"), 0, 0)
        grid.addLayout(qq_row, 0, 1)
        self.nick_edit = QLineEdit()
        self.nick_edit.setPlaceholderText("自动获取，也可手动修改")
        grid.addWidget(_label("昵称", "fieldLabel"), 1, 0)
        grid.addWidget(self.nick_edit, 1, 1)
        self.role_combo = QComboBox()
        _polish_combo(self.role_combo)
        grid.addWidget(_label("绑定角色", "fieldLabel"), 2, 0)
        grid.addWidget(self.role_combo, 2, 1)
        grid.setColumnStretch(1, 1)
        form_box.addLayout(grid)
        self.acc_status = _label("", "status", wrap=True)
        self.acc_status.setMinimumHeight(18)
        form_box.addWidget(self.acc_status)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.add_btn = _button("添加为新账号", slot=self._on_add)
        self.role_btn = _button("保存角色", slot=self._on_save_role, tip="只修改选中账号绑定的角色，不切换账号")
        r1.addWidget(self.add_btn)
        r1.addWidget(self.role_btn)
        r1.addStretch(1)
        form_box.addLayout(r1)
        form_box.addStretch(1)
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self.del_btn = _button("删除", "danger", self._on_delete)
        self.use_btn = _button("选用此号并重启", "primary", self._on_use)
        r2.addWidget(self.del_btn)
        r2.addStretch(1)
        r2.addWidget(self.use_btn)
        form_box.addLayout(r2)
        cols.addLayout(form_box, 6)
        lay.addLayout(cols)

        self._fetch_state = {"qq": None}
        self._nam = None
        self._fetch_timer = QTimer(self)
        self._fetch_timer.setSingleShot(True)
        self._fetch_timer.setInterval(500)
        self._fetch_timer.timeout.connect(lambda: self._do_fetch(self.qq_edit.text().strip()))
        self.qq_edit.textChanged.connect(self._schedule_fetch)
        self.qq_edit.returnPressed.connect(lambda: self._do_fetch(self.qq_edit.text().strip()))

    # 数据 ---------------------------------------------------------------
    def _profiles(self):
        fn = getattr(self.panel, "_load_qq_profiles", None)
        try:
            return list(fn()) if callable(fn) else []
        except Exception:
            return []

    def _roles(self):
        fn = getattr(self.panel, "_load_roles", None)
        try:
            return list(fn()) if callable(fn) else []
        except Exception:
            return []

    def _current_qq(self):
        fn = getattr(self.panel, "_read_env_value", None)
        try:
            return (fn("BOT_QQ") if callable(fn) else "").strip()
        except Exception:
            return ""

    def _role_name(self, role_id):
        if not role_id:
            return "默认 · 芙宁娜人格"
        for r in self._roles():
            if str(r.get("id")) == str(role_id):
                return r.get("name") or role_id
        return f"未知角色（{role_id}）"

    def _paint_avatar(self, text):
        pix = QPixmap(52, 52)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#22344f"))
        p.drawEllipse(0, 0, 52, 52)
        p.setPen(QColor(GOLD))
        f = QFont(self.font())
        f.setPixelSize(22)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, 52, 52), Qt.AlignCenter, (text or "?")[:1])
        p.end()
        self.avatar.setPixmap(pix)

    def load(self):
        current = self._current_qq()
        profiles = self._profiles()
        cur = next((p for p in profiles if str(p.get("qq")) == current), None)
        nick = (cur or {}).get("nickname") or "未命名账号"
        self._paint_avatar(nick if cur else "?")
        if current:
            self.cur_name.setText(nick if cur else "当前账号不在列表中")
            self.cur_meta.setText(f"QQ {current}　·　角色：{self._role_name((cur or {}).get('role_id', ''))}")
            self.cur_chip.show()
        else:
            self.cur_name.setText("尚未设置机器人 QQ")
            self.cur_meta.setText("在下方添加账号并点「选用此号」")
            self.cur_chip.hide()
        self.role_combo.blockSignals(True)
        self.role_combo.clear()
        self.role_combo.addItem("默认 · 芙宁娜人格", "")
        for r in self._roles():
            self.role_combo.addItem(r.get("name") or r.get("id", ""), r.get("id", ""))
        self.role_combo.blockSignals(False)
        selected = self.plist.currentItem().data(Qt.UserRole) if self.plist.currentItem() else current
        self.plist.blockSignals(True)
        self.plist.clear()
        for p in profiles:
            qq = str(p.get("qq", ""))
            mark = "　· 当前" if qq == current else ""
            item = QListWidgetItem(f"{p.get('nickname') or '未命名'}　({qq}){mark}")
            item.setData(Qt.UserRole, qq)
            item.setToolTip(f"角色：{self._role_name(p.get('role_id', ''))}")
            self.plist.addItem(item)
            if qq == selected:
                self.plist.setCurrentItem(item)
        self.plist.blockSignals(False)
        self._on_select()
        self.set_chip("", "off")

    def _on_select(self):
        item = self.plist.currentItem()
        has = item is not None
        for b in (self.role_btn, self.del_btn, self.use_btn):
            b.setEnabled(has)
        if not has:
            return
        qq = item.data(Qt.UserRole)
        prof = next((p for p in self._profiles() if str(p.get("qq")) == qq), {})
        self.qq_edit.blockSignals(True)
        self.qq_edit.setText(qq)
        self.qq_edit.blockSignals(False)
        self.nick_edit.setText(prof.get("nickname", ""))
        idx = self.role_combo.findData(prof.get("role_id", "") or "")
        self.role_combo.setCurrentIndex(max(0, idx))
        self._acc_status("")

    def _acc_status(self, text, tone=""):
        self.acc_status.setText(text)
        set_prop(self.acc_status, "tone", tone)

    # 昵称获取（NapCat HTTP 接口，与原 QQ 管理窗口一致）--------------------
    def _schedule_fetch(self, *_):
        text = self.qq_edit.text().strip()
        self._fetch_timer.stop()
        if text.isdigit() and len(text) >= 5:
            self._fetch_timer.start()

    def _on_fetch(self):
        qq = self.qq_edit.text().strip()
        if not qq.isdigit():
            self._acc_status("请输入纯数字的 QQ 号", "warn")
            return
        self._do_fetch(qq)

    def _do_fetch(self, qq):
        if not qq.isdigit():
            return
        try:
            from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
        except Exception:
            self._acc_status("当前环境不支持联网获取昵称，请手动填写", "warn")
            return
        if self._nam is None:
            self._nam = QNetworkAccessManager(self)
        self._fetch_state["qq"] = qq
        self._acc_status("正在从 NapCat 获取昵称…", "info")
        reply = self._nam.get(QNetworkRequest(QUrl(f"http://127.0.0.1:3000/get_stranger_info?user_id={qq}")))

        def finished():
            try:
                if self._fetch_state["qq"] != qq:
                    return
                if reply.error() != QNetworkReply.NoError:
                    self._acc_status(f"获取失败：无法连接 NapCat（{reply.errorString()}），可手动填写昵称", "err")
                    return
                data = json.loads(bytes(reply.readAll()).decode("utf-8", errors="ignore") or "{}")
                nick = (data.get("data") or {}).get("nickname", "")
                if data.get("status") == "failed" or not nick:
                    self._acc_status("没有取到昵称：QQ 号无效或 NapCat 未登录，可手动填写", "err")
                    return
                self.nick_edit.setText(nick)
                self._acc_status(f"✓ 已获取昵称：{nick}", "ok")
            except Exception as exc:
                if self._fetch_state["qq"] == qq:
                    self._acc_status(f"获取失败：{exc}", "err")
            finally:
                reply.deleteLater()

        reply.finished.connect(finished)

    # 操作 ---------------------------------------------------------------
    def _save_profile(self, qq, nick, role_id):
        fn = getattr(self.panel, "_save_qq_profile", None)
        if not callable(fn):
            raise RuntimeError("当前环境不支持保存账号")
        fn(qq, nick, role_id)

    def _on_add(self):
        qq = self.qq_edit.text().strip()
        nick = self.nick_edit.text().strip()
        if not qq.isdigit():
            self._acc_status("QQ 号格式不正确（需纯数字）", "warn")
            return
        if not nick:
            self._acc_status("请先获取或填写昵称", "warn")
            return
        try:
            self._save_profile(qq, nick, self.role_combo.currentData() or "")
        except Exception as exc:
            self._acc_status(f"添加失败：{exc}", "err")
            return
        self.log("success", f"[QQ管理] 已添加 {nick} ({qq})")
        self.load()
        self._select_qq(qq)
        self._acc_status(f"✓ 已添加 {nick}", "ok")

    def _select_qq(self, qq):
        for i in range(self.plist.count()):
            if self.plist.item(i).data(Qt.UserRole) == qq:
                self.plist.setCurrentRow(i)
                break

    def _on_save_role(self):
        item = self.plist.currentItem()
        if not item:
            return
        qq = item.data(Qt.UserRole)
        nick = self.nick_edit.text().strip() or next(
            (p.get("nickname", "") for p in self._profiles() if str(p.get("qq")) == qq), "")
        try:
            self._save_profile(qq, nick, self.role_combo.currentData() or "")
        except Exception as exc:
            self._acc_status(f"保存失败：{exc}", "err")
            return
        self.log("success", f"[QQ管理] {qq} 已绑定角色：{self.role_combo.currentText()}")
        self.load()
        self._select_qq(qq)
        self._acc_status("✓ 角色绑定已保存；该账号在线时，机器人下一次回复就会用新角色", "ok")

    def _on_delete(self):
        item = self.plist.currentItem()
        if not item:
            return
        qq = item.data(Qt.UserRole)
        if qq == self._current_qq():
            self._acc_status("不能删除正在使用的账号，请先选用其它账号", "warn")
            return
        if not _ask(self, "删除账号", f"从列表中删除 {item.text().split('　')[0]}（{qq}）？\n只删除面板里的记录，不影响 QQ 本身。"):
            return
        fn = getattr(self.panel, "_delete_qq_profile", None)
        if callable(fn) and fn(qq):
            self.log("info", f"[QQ管理] 已删除 {qq}")
            self.load()
            self._acc_status(f"已删除 {qq}", "info")

    def _on_use(self):
        panel = self.panel
        if getattr(panel, "busy", False):
            self._acc_status("机器人正在启动或停止，请完成后再切换账号", "warn")
            return
        item = self.plist.currentItem()
        if not item:
            return
        qq = item.data(Qt.UserRole)
        role_id = self.role_combo.currentData() or ""
        nick = self.nick_edit.text().strip() or next(
            (p.get("nickname", "") for p in self._profiles() if str(p.get("qq")) == qq), "")
        if not _ask(
                self, "切换账号",
                f"用 {nick or qq}（{qq}）重新登录？\n会先停止当前的 NapCat 和机器人，再用新账号启动，期间机器人暂时离线。",
        ):
            return
        try:
            self._save_profile(qq, nick, role_id)
            panel._write_env_values({"BOT_QQ": qq})
            ensure = getattr(panel, "_ensure_napcat_config", None)
            if callable(ensure):
                ensure(qq)
        except Exception as exc:
            self._acc_status(f"切换失败：{exc}", "err")
            return
        # 清除群列表缓存，下一轮刷新会获取新号的群
        panel._napcat_groups_cache = {}
        panel._napcat_groups_ts = 0.0
        panel._group_refresh_inflight = False
        panel._last_group_sig = ""
        self.log("success", f"[QQ管理] 已选用 {qq}，正在用该号重启 NapCat + 机器人上线...")
        restart = getattr(panel, "restart_bot", None)
        if callable(restart):
            restart(restart_napcat=True)
        refresh = getattr(panel, "refresh_home_cards", None)
        if callable(refresh):
            refresh()
        self.load()
        self._acc_status("✓ 已切换，机器人正在用新账号重启（进度见主界面日志）", "ok")


# ---------------------------------------------------------------- AI 模型
class ModelPage(SettingsPage):
    key = "model"
    title = "AI 模型"
    desc = "聊天、看图和搜图比对统一使用下面这个 DeepSeek 接口。保存后写入 .env，机器人自动热加载，无需重启。"
    footer_hint = "保存后实时生效"
    MODEL = "deepseek-flash"

    def build(self):
        lay = self.card("接口配置", "填写 OpenAI 兼容接口的地址与密钥。地址可以只填到 /v1，程序会自动补全。")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://…/v1")
        self.form_row(grid, 0, "Base URL", self.url_edit)
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-…")
        self.eye_btn = _button("显示", slot=self._toggle_key, tip="显示 / 隐藏密钥")
        self.eye_btn.setFixedWidth(64)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.eye_btn)
        holder = QWidget()
        holder.setLayout(key_row)
        key_row.setContentsMargins(0, 0, 0, 0)
        self.form_row(grid, 2, "API Key", holder)
        self.model_edit = QLineEdit(self.MODEL)
        self.model_edit.setReadOnly(True)
        self.form_row(grid, 4, "模型", self.model_edit, "固定为 deepseek-flash：文字聊天、看图、搜图比对共用，已验证可用。")
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.watch(self.url_edit, self.key_edit)

        lay = self.card("连接测试", "用当前填写的地址和密钥发送一条最小请求，只做检测，不会保存。")
        row = QHBoxLayout()
        row.setSpacing(12)
        self.test_btn = _button("测试连接", slot=self._test)
        row.addWidget(self.test_btn)
        self.test_status = _label("尚未测试", "status", wrap=True)
        row.addWidget(self.test_status, 1)
        lay.addLayout(row)
        self._bridge = _Bridge(self)
        self._bridge.done.connect(self._on_test_done)

    def _env(self, key):
        fn = getattr(self.panel, "_read_env_value", None)
        try:
            return fn(key) if callable(fn) else ""
        except Exception:
            return ""

    def load(self):
        self.url_edit.setText(self._env("AI_CHAT_LLM_BASE_URL"))
        self.key_edit.setText(self._env("AI_CHAT_LLM_API_KEY"))
        configured = bool(self.url_edit.text().strip() and self.key_edit.text().strip())
        self.set_chip("已配置" if configured else "未配置", "on" if configured else "warn")

    def save(self):
        url = self.url_edit.text().strip()
        key = self.key_edit.text().strip()
        if not url or not key:
            raise ValueError("Base URL 和 API Key 都需要填写")
        writer = getattr(self.panel, "_write_env_values", None)
        if not callable(writer):
            raise RuntimeError("当前环境不支持写入 .env")
        writer({"AI_CHAT_LLM_BASE_URL": url, "AI_CHAT_LLM_API_KEY": key,
                "AI_CHAT_LLM_MODEL": self.MODEL, "AI_CHAT_LLM_MODELS": self.MODEL})
        if self._env("AI_CHAT_LLM_BASE_URL") != url or self._env("AI_CHAT_LLM_API_KEY") != key:
            raise RuntimeError("写入 .env 失败，详情见主界面日志")
        self.log("success", "[设置] 模型设置已保存至 .env，机器人将自动热重载最新配置")
        self.set_chip("已配置", "on")
        return "已保存到 .env，机器人会自动热加载"

    def _toggle_key(self):
        hidden = self.key_edit.echoMode() == QLineEdit.Password
        self.key_edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        self.eye_btn.setText("隐藏" if hidden else "显示")

    def _set_test(self, text, tone):
        self.test_status.setText(text)
        set_prop(self.test_status, "tone", tone)

    def _test(self):
        url = self.url_edit.text().strip().rstrip("/")
        key = self.key_edit.text().strip()
        if not url:
            self._set_test("请先填写 Base URL", "warn")
            return
        if not key:
            self._set_test("请先填写 API Key", "warn")
            return
        if not url.endswith("/chat/completions"):
            url += "/chat/completions" if url.endswith("/v1") else "/v1/chat/completions"
        self.test_btn.setEnabled(False)
        self._set_test("正在测试连接…", "info")

        def worker():
            import httpx
            t0 = time.time()
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(url, headers={"Authorization": f"Bearer {key}"}, json={
                        "model": self.MODEL, "messages": [{"role": "user", "content": "1"}],
                        "max_tokens": 16, "thinking": {"type": "disabled"}, "stream": False,
                    })
                ms = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    result = (f"✓ 连接成功，耗时 {ms} ms", "ok")
                else:
                    snippet = resp.text[:120].replace("\n", " ")
                    result = (f"✗ 连接失败（HTTP {resp.status_code}）：{snippet}", "err")
            except Exception as exc:
                result = (f"✗ 请求异常：{str(exc)[:120]}", "err")
            try:
                self._bridge.done.emit(*result)
            except RuntimeError:
                pass  # 窗口已关闭

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, text, tone):
        self.test_btn.setEnabled(True)
        self._set_test(text, tone)


# ---------------------------------------------------------------- Pixiv
ALIAS_EXAMPLES = {
    "初音未来": ["初音ミク", "初音未来", "HatsuneMiku"],
    "星野爱": ["星野アイ", "星野爱", "HoshinoAi"],
    "博丽灵梦": ["博麗霊夢", "博丽灵梦", "HakureiReimu"],
}
_JOB_LABELS = {
    "started": "已领取", "running": "发送中", "waiting": "等待榜单公布", "completed": "完成",
    "partial": "部分成功", "skipped": "已跳过", "stopped": "已停止", "interrupted": "已中断",
}


class PixivPage(SettingsPage):
    key = "pixiv"
    title = "Pixiv 插画"
    desc = "每日榜单分批推送与群友点图。默认关闭；开启后还需要在「群权限」里逐群授权「Pixiv 榜单推送 / Pixiv 点图」。"
    footer_hint = "保存后实时生效，无需重启机器人"

    def build(self):
        from furina.services.pixiv import settings as pixiv_settings
        self.store = pixiv_settings

        seg_bar = QFrame()
        seg_bar.setObjectName("segBar")
        seg_layout = QHBoxLayout(seg_bar)
        seg_layout.setContentsMargins(4, 4, 4, 4)
        seg_layout.setSpacing(4)
        self.sub = QStackedWidget()
        self.sub.setObjectName("subStack")
        group = QButtonGroup(self)
        self.seg_group = group
        for i, name in enumerate(("推送设置", "标签映射", "运行记录")):
            b = _button(name, "seg")
            b.setCheckable(True)
            group.addButton(b, i)
            seg_layout.addWidget(b)
        group.button(0).setChecked(True)
        group.idClicked.connect(self._switch_sub)
        seg_row = QHBoxLayout()
        seg_row.addWidget(seg_bar)
        seg_row.addStretch(1)
        self.body.addLayout(seg_row)
        self.body.addWidget(self.sub)

        # 推送设置
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(14)
        lay = self.card(into=pl)
        self.enabled = self.toggle_row(lay, "启用 Pixiv 服务", "关闭时不推送榜单、也不响应点图；已发出的内容不受影响。")
        lay = self.card("每日批次 · 北京时间", "插画榜前 150 名分 5 批发送，每批 30 部；错过的批次不补发。时间需从早到晚、互不重复。", into=pl)
        times_row = QHBoxLayout()
        times_row.setSpacing(12)
        self.times = []
        for i in range(5):
            col = QVBoxLayout()
            col.setSpacing(5)
            col.addWidget(_label(f"第 {i + 1} 批", "help"))
            edit = QTimeEdit()
            edit.setDisplayFormat("HH:mm")
            edit.setFixedWidth(96)
            col.addWidget(edit)
            times_row.addLayout(col)
            self.times.append(edit)
        times_row.addStretch(1)
        lay.addLayout(times_row)
        lay = self.card("发送与缓存", into=pl)
        self.interval = _spin(2, 60, "秒")
        self.cache_mb = _spin(64, 1024, "MiB")
        self.cooldown = _spin(10, 3600, "秒")
        self.metric_grid(lay, [
            ("作品发送间隔", self.interval, "相邻两部作品 / 消息之间等待的时间"),
            ("原图缓存上限", self.cache_mb, "本插件的临时原图，发送后自动清理"),
            ("点图冷却", self.cooldown, "同一群的同一个人两次点图的最短间隔"),
        ])
        lay = self.card("规则说明", into=pl)
        for text in (
                "周日推送上周的周榜；每月 15 日推送上月的月榜（15 日恰逢周日时顺延到 16 日）。",
                "点图：在群里 @机器人「来两张神里绫华的图」，最多 10 部，没说数量时随机 1–3 部。",
                "多页作品以合并转发发送；只使用公开网页数据，不读取任何账号密码或浏览器登录信息。"):
            lay.addWidget(_label("•  " + text, "bullet", wrap=True))
        pl.addStretch(1)
        self.sub.addWidget(page)

        # 标签映射
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(14)
        lay = self.card("角色名 → 检索标签",
                        "内置原神等常见角色；这里补充或覆盖。每个名称最多 4 个检索词，最多 500 条。未收录的名称按原词搜索。",
                        into=pl)
        self.aliases = QPlainTextEdit()
        self.aliases.setObjectName("code")
        self.aliases.setPlaceholderText('{\n  "角色中文名": ["日文标签", "英文标签"]\n}')
        self.aliases.setMinimumHeight(300)
        lay.addWidget(self.aliases)
        row = QHBoxLayout()
        self.alias_state = _label("", "status")
        row.addWidget(self.alias_state, 1)
        row.addWidget(_button("插入示例", slot=self._fill_examples, tip="加入 3 个示例条目（已存在的不会重复）"))
        lay.addLayout(row)
        self.aliases.textChanged.connect(self._validate_aliases)
        pl.addStretch(1)
        self.sub.addWidget(page)

        # 运行记录
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(14)
        lay = self.card("最近任务", "记录键：日期 : 批次（0–4）: 群号。发送结果不确定的任务不会自动重发。", into=pl)
        head = QHBoxLayout()
        self.cache_chip = _chip("", "info")
        head.addWidget(self.cache_chip, 0, Qt.AlignVCenter)
        head.addStretch(1)
        head.addWidget(_button("刷新", slot=self._refresh_jobs))
        lay.addLayout(head)
        self.jobs = QPlainTextEdit()
        self.jobs.setObjectName("code")
        self.jobs.setReadOnly(True)
        self.jobs.setMinimumHeight(320)
        lay.addWidget(self.jobs)
        pl.addStretch(1)
        self.sub.addWidget(page)

        self.watch(self.enabled, self.interval, self.cache_mb, self.cooldown, self.aliases, *self.times)
        self.enabled.toggled.connect(self._update_chip)
        self._switch_sub(0)

    def _switch_sub(self, index):
        button = self.seg_group.button(index)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        # 只让当前子页参与尺寸计算，避免短页面底下出现大片空白和多余的滚动条
        for i in range(self.sub.count()):
            policy = QSizePolicy.Preferred if i == index else QSizePolicy.Ignored
            self.sub.widget(i).setSizePolicy(QSizePolicy.Preferred, policy)
        self.sub.setCurrentIndex(index)
        self.sub.updateGeometry()
        if index == 2:
            self._refresh_jobs()

    def _update_chip(self, *_):
        on = self.enabled.isChecked()
        self.set_chip("已开启" if on else "未开启", "on" if on else "off")

    def load(self):
        cfg = self.store.load()
        self.enabled.setChecked(cfg["enabled"])
        for edit, value in zip(self.times, cfg["times"]):
            edit.setTime(QTime.fromString(value, "HH:mm"))
        self.interval.setValue(cfg["interval_seconds"])
        self.cache_mb.setValue(cfg["cache_mb"])
        self.cooldown.setValue(cfg["search_cooldown_seconds"])
        self.aliases.setPlainText(json.dumps(cfg["aliases"], ensure_ascii=False, indent=2))
        self._update_chip()
        self._validate_aliases()
        self._refresh_jobs()

    def _parse_aliases(self):
        text = self.aliases.toPlainText().strip() or "{}"
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("最外层必须是 { } 对象")
        return data

    def _validate_aliases(self):
        try:
            data = self._parse_aliases()
            self.store.validate({"aliases": data})
        except json.JSONDecodeError as exc:
            self.alias_state.setText(f"✗ JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列（{exc.msg}）")
            set_prop(self.alias_state, "tone", "err")
            return False
        except Exception as exc:
            self.alias_state.setText(f"✗ {exc}")
            set_prop(self.alias_state, "tone", "err")
            return False
        self.alias_state.setText(f"✓ 格式正确，共 {len(data)} 条自定义映射")
        set_prop(self.alias_state, "tone", "ok")
        return True

    def _fill_examples(self):
        try:
            current = self._parse_aliases()
        except Exception as exc:
            _warn(self, "内容不是有效 JSON", f"{exc}\n请先修正后再插入示例。")
            return
        added = [k for k in ALIAS_EXAMPLES if k not in current]
        if not added:
            self.alias_state.setText("示例条目都已存在，无需重复插入")
            set_prop(self.alias_state, "tone", "info")
            return
        for k in added:
            current[k] = ALIAS_EXAMPLES[k]
        self.aliases.setPlainText(json.dumps(current, ensure_ascii=False, indent=2))

    def save(self):
        if not self._validate_aliases():
            raise ValueError("标签映射格式有误，请先修正（见「标签映射」页）")
        self.store.save({
            "enabled": self.enabled.isChecked(),
            "times": [e.time().toString("HH:mm") for e in self.times],
            "interval_seconds": self.interval.value(),
            "cache_mb": self.cache_mb.value(),
            "search_cooldown_seconds": self.cooldown.value(),
            "aliases": self._parse_aliases(),
        })
        self.log("success", "[Pixiv] 设置已保存，实时生效，无需重启。")
        return "已保存，实时生效"

    def _refresh_jobs(self):
        root = self.store.DATA / "pixiv"
        lines = []
        db_path = root / "jobs.sqlite3"
        if db_path.exists():
            try:
                con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
                try:
                    rows = con.execute("SELECT key,status,detail FROM jobs ORDER BY updated DESC LIMIT 40").fetchall()
                finally:
                    con.close()
                for k, s, d in rows:
                    lines.append(f"{k}\n    {_JOB_LABELS.get(s, s)}：{d}")
            except Exception:
                lines.append("运行记录暂时不可读（数据库忙），请稍后刷新。")
        used = 0
        cache = root / "temporary"
        if cache.exists():
            for p in cache.rglob("*"):
                try:
                    if p.is_file():
                        used += p.stat().st_size
                except OSError:
                    pass
        self.cache_chip.setText(f"临时原图占用 {used / 1024 / 1024:.1f} MiB")
        self.jobs.setPlainText("\n\n".join(lines) or "暂无推送记录。")


# ---------------------------------------------------------------- 定时提醒
class ReminderPage(SettingsPage):
    key = "reminder"
    title = "定时提醒"
    desc = "群友 @机器人 就能创建只提醒自己的定时任务。默认关闭；开启后还需要在「群权限」里逐群开启「定时提醒」。"
    footer_hint = "保存后实时生效，无需重启机器人"
    EXAMPLES = ("20分钟后提醒我收菜", "明天晚上8点叫我打周本", "每天上午9点提醒我喝水",
                "每周日下午3点提醒我整理照片", "每月15号上午9点提醒我交账单",
                "我的提醒", "取消提醒 12", "确认提醒")

    def build(self):
        from furina.services.reminder import settings as reminder_settings
        self.store = reminder_settings
        lay = self.card()
        self.enabled = self.toggle_row(lay, "启用提醒服务", "关闭后不再创建新提醒；已有任务保留，到期时按离线规则处理。")
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet("background: rgba(255,255,255,0.06);")
        lay.addWidget(div)
        self.natural = self.toggle_row(lay, "自然语言辅助", "说法不标准时借助聊天模型理解，创建前仍需本人回复「确认提醒」；会消耗少量模型额度。")
        lay = self.card("额度与冷却")
        self.max_active = _spin(1, 50, "条")
        self.cooldown = _spin(5, 300, "秒")
        self.grace = _spin(1, 30, "分钟")
        self.metric_grid(lay, [
            ("有效提醒上限", self.max_active, "每人在每个群最多同时保留的提醒数"),
            ("创建冷却", self.cooldown, "同一个人连续创建提醒的最短间隔；查询和取消不受限"),
            ("离线补发宽限", self.grace, "机器人离线错过的提醒，在此时间内补发，超时跳过"),
        ])
        lay = self.card("运行状态")
        row = QHBoxLayout()
        self.stats = _label("", "fieldLabel", wrap=True)
        row.addWidget(self.stats, 1)
        row.addWidget(_button("刷新", slot=self._refresh_stats))
        lay.addLayout(row)
        lay = self.card("群友这样说", "统一北京时间；到时只在创建提醒的群里艾特本人，别人不能查询或取消。")
        flow_host = QWidget()
        flow = FlowLayout(flow_host, spacing=8)
        for text in self.EXAMPLES:
            flow.addWidget(_label(text, "pill"))
        lay.addWidget(flow_host)
        self.watch(self.enabled, self.natural, self.max_active, self.cooldown, self.grace)
        self.enabled.toggled.connect(self._update_chip)

    def _update_chip(self, *_):
        on = self.enabled.isChecked()
        self.set_chip("已开启" if on else "未开启", "on" if on else "off")

    def load(self):
        cfg = self.store.load()
        self.enabled.setChecked(cfg["enabled"])
        self.natural.setChecked(cfg["natural_language"])
        self.max_active.setValue(cfg["max_active"])
        self.cooldown.setValue(cfg["cooldown_seconds"])
        self.grace.setValue(cfg["grace_minutes"])
        self._update_chip()
        self._refresh_stats()

    def save(self):
        self.store.save({
            "enabled": self.enabled.isChecked(), "natural_language": self.natural.isChecked(),
            "max_active": self.max_active.value(), "cooldown_seconds": self.cooldown.value(),
            "grace_minutes": self.grace.value(),
        })
        self.log("success", "[提醒] 设置已保存，实时生效，无需重启。")
        return "已保存，实时生效"

    def _refresh_stats(self):
        from furina.core import paths as _paths
        db = _paths.DB_PATH
        if not db.exists():
            self.stats.setText("还没有任务数据库，机器人首次加载提醒插件时会自动创建。")
            return
        try:
            con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                counts = dict(con.execute("SELECT state,COUNT(*) FROM reminders GROUP BY state").fetchall())
            finally:
                con.close()
            self.stats.setText(
                f"有效提醒 {counts.get('active', 0)} 条　·　发送中 {counts.get('sending', 0)} 条　·　"
                f"已完成 {counts.get('done', 0)} 条　·　已取消 {counts.get('cancelled', 0)} 条")
        except Exception:
            self.stats.setText("任务数据库暂时忙，请稍后刷新。")




# ---------------------------------------------------------------- 米游社服务
class MiyoushePage(SettingsPage):
    key = "miyoushe"
    title = "GsCore 游戏服务"
    desc = "游戏功能由官方项目接管。此处不再维护独立的扫码、账号或自动签到实现。"
    savable = False
    footer_hint = "所有游戏设置使用 GsCore 官方控制台"

    def build(self):
        lay = self.card("官方游戏管理", "GenshinUID / StarRailUID / ZZZeroUID / BBBUID")
        lay.addWidget(_label("在主窗口左侧选择「游戏管理」，可直接使用官方控制台。\n首次注册请点击「本机注册码」；自行设置管理员账号和密码。\n旧账号数据保留，迁移或重新扫码后由上游管理。", "help", wrap=True))
        lay.addWidget(_button("返回主窗口游戏管理", slot=self._open_games))

    def load(self):
        self.set_chip("上游接管", "info")

    def _open_games(self):
        index = self.panel.rail_kinds.index("games")
        self.panel.show_tab(index)
        # Closing only this settings dialog is a user-initiated navigation action.
        self.window().close()


# ---------------------------------------------------------------- 外观
class AppearancePage(SettingsPage):
    key = "appearance"
    title = "外观"
    desc = "首页背景壁纸。单选一张即单张显示；按住 Ctrl / Shift 多选后点「应用选中」，按选中顺序轮播。"
    savable = False
    footer_hint = "壁纸操作点按即生效"
    REQUIRED = ("wallpaper_library", "wallpaper_list", "_refresh_wallpaper_list", "_add_wallpaper_to_library",
                "_apply_selected_wallpaper", "_remove_selected_wallpaper", "_clear_wallpaper_library")

    def build(self):
        panel = self.panel
        self.available = all(hasattr(panel, n) for n in self.REQUIRED)
        if not self.available:
            lay = self.card("壁纸库")
            lay.addWidget(_label("当前环境不支持壁纸管理。", "help"))
            return
        lay = self.card("壁纸库")
        cols = QHBoxLayout()
        cols.setSpacing(16)
        gallery = QListWidget()
        gallery.setObjectName("gallery")
        gallery.setViewMode(QListWidget.IconMode)
        gallery.setIconSize(QSize(150, 92))
        gallery.setGridSize(QSize(170, 128))
        gallery.setSpacing(6)
        gallery.setMovement(QListWidget.Static)
        gallery.setResizeMode(QListWidget.Adjust)
        gallery.setSelectionMode(QAbstractItemView.ExtendedSelection)
        gallery.setMinimumHeight(290)
        gallery.setWordWrap(True)
        cols.addWidget(gallery, 1)
        side = QVBoxLayout()
        side.setSpacing(8)
        preview = QLabel("选择壁纸查看预览")
        preview.setObjectName("preview")
        preview.setAlignment(Qt.AlignCenter)
        preview.setFixedSize(300, 186)
        side.addWidget(preview)
        self.lib_info = _label("", "help", wrap=True)
        self.lib_info.setFixedWidth(300)
        side.addWidget(self.lib_info)
        side.addStretch(1)
        cols.addLayout(side)
        lay.addLayout(cols)
        # 面板已有的壁纸库方法按属性名操作这些控件，直接挂到面板上即可复用
        panel.wallpaper_list_widget = gallery
        panel.wallpaper_preview_label = preview
        gallery.currentItemChanged.connect(
            lambda cur, _prev: panel._show_wallpaper_preview(gallery.row(cur)) if cur else None)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_button("添加壁纸…", slot=lambda: self._run(lambda: panel._add_wallpaper_to_library(self))))
        row.addWidget(_button("应用选中", "primary", lambda: self._run(panel._apply_selected_wallpaper)))
        row.addWidget(_button("删除选中", slot=lambda: self._run(panel._remove_selected_wallpaper)))
        row.addStretch(1)
        row.addWidget(_button("清空壁纸库", "danger", self._clear))
        lay.addLayout(row)

        lay = self.card("轮播间隔", "多张壁纸时按这个间隔依次切换。")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.interval = _spin(1, 86400, "秒", width=160)
        panel.wallpaper_interval_spin = self.interval
        row.addWidget(self.interval)
        row.addWidget(_button("保存间隔", slot=self._save_interval))
        self.interval_status = _label("", "status")
        row.addWidget(self.interval_status, 1)
        lay.addLayout(row)

        lay = self.card("恢复默认背景", "清空壁纸设置，恢复面板内置的轮播背景。壁纸原文件不会被删除。")
        row = QHBoxLayout()
        row.addWidget(_button("恢复默认背景", "danger", self._reset))
        row.addStretch(1)
        lay.addLayout(row)

    def _run(self, fn):
        try:
            fn()
        finally:
            self._update_info()

    def _update_info(self):
        panel = self.panel
        library = list(getattr(panel, "wallpaper_library", []) or [])
        playing = list(getattr(panel, "wallpaper_list", []) or [])
        if not library:
            self.lib_info.setText("壁纸库是空的，首页使用内置的轮播背景。")
            self.set_chip("默认背景", "off")
        elif len(playing) <= 1:
            name = Path(playing[0]).name if playing else "—"
            self.lib_info.setText(f"壁纸库共 {len(library)} 张\n当前单张显示：{name}")
            self.set_chip("单张壁纸", "info")
        else:
            self.lib_info.setText(f"壁纸库共 {len(library)} 张\n正在轮播其中 {len(playing)} 张，"
                                  f"每 {getattr(panel, 'wallpaper_interval', 30)} 秒切换")
            self.set_chip(f"轮播 {len(playing)} 张", "info")

    def load(self):
        if not self.available:
            return
        self.panel._refresh_wallpaper_list()
        self.interval.setValue(int(getattr(self.panel, "wallpaper_interval", 30) or 30))
        self._update_info()

    def _save_interval(self):
        panel = self.panel
        panel.wallpaper_interval = self.interval.value()
        try:
            panel._save_wallpaper_library()
            starter = getattr(panel, "_start_wallpaper_timer", None)
            if callable(starter):
                starter()
        except Exception as exc:
            self.interval_status.setText(f"保存失败：{exc}")
            set_prop(self.interval_status, "tone", "err")
            return
        self.log("success", f"[壁纸库] 循环间隔已改为 {panel.wallpaper_interval} 秒")
        self.interval_status.setText(f"✓ 已改为每 {panel.wallpaper_interval} 秒切换")
        set_prop(self.interval_status, "tone", "ok")
        self._update_info()

    def _clear(self):
        if _ask(self, "清空壁纸库", "清空壁纸库并恢复默认背景？\n只清空列表，不删除壁纸原文件。"):
            self._run(self.panel._clear_wallpaper_library)

    def _reset(self):
        if _ask(self, "恢复默认背景", "恢复面板内置的轮播背景？当前壁纸设置会被清空。"):
            self._run(self.panel.reset_background)
            self.panel._refresh_wallpaper_list()


# ---------------------------------------------------------------- 窗口
from .settings_env import schema_page_specs  # noqa: E402  （依赖上面的 SettingsPage 等，必须放在类定义之后）

_ENV = {spec[0]: spec for spec in schema_page_specs()}
PAGES = (
    ("account", "账号与角色", "QQ 号与人格角色", AccountPage),
    ("model", "AI 模型", "接口与连接测试", ModelPage),
    _ENV["brain"],
    _ENV["chat"],
    _ENV["huoren"],
    _ENV["voice"],
    _ENV["websearch"],
    _ENV["bili"],
    _ENV["context"],
    _ENV["interact"],
    _ENV["digest"],
    ("pixiv", "Pixiv 插画", "榜单推送与点图", PixivPage),
    ("reminder", "定时提醒", "群友自助提醒", ReminderPage),
    _ENV["search"],
    _ENV["jm"],
    _ENV["push"],
    _ENV["login"],
    ("miyoushe", "米游社服务", "扫码绑定与自动签到", MiyoushePage),
    ("appearance", "外观", "壁纸与背景", AppearancePage),
    _ENV["other"],
)


class _ErrorPage(QWidget):
    def __init__(self, title, exc):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 26)
        lay.addWidget(_label(title, "pageTitle"))
        msg = _label(f"这一页加载失败，其它设置不受影响。\n\n{type(exc).__name__}: {exc}", "status", wrap=True)
        set_prop(msg, "tone", "err")
        lay.addWidget(msg)
        lay.addStretch(1)

    def is_dirty(self):
        return False


class _SearchEdit(QLineEdit):
    """设置搜索框：有字时按 Esc 先清空，不直接关掉设置窗口。"""

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.text():
            self.clear()
            event.accept()
            return
        super().keyPressEvent(event)


class SettingsCenter(QDialog):
    def __init__(self, panel, page=None):
        super().__init__(panel)
        self.panel = panel
        initial = page if isinstance(page, str) else "account"
        self.setObjectName("settingsCenter")
        self.setWindowTitle("设置")
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#0f172a"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setStyleSheet(build_style())
        self._match_panel_geometry(panel)
        self._nav_rows = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QWidget()
        side.setObjectName("sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(236)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 26, 0, 18)
        sl.setSpacing(0)
        brand = QVBoxLayout()
        brand.setContentsMargins(24, 0, 24, 18)
        brand.setSpacing(4)
        brand.addWidget(_label("设置", "brandTitle"))
        brand.addWidget(_label("机器人与控制面板配置", "brandSub"))
        sl.addLayout(brand)
        # 总搜索：输入就在左侧列出所有页里匹配的设置项，点一下跳过去并高亮那一项
        search_box = QVBoxLayout()
        search_box.setContentsMargins(16, 0, 16, 12)
        self.search = _SearchEdit()
        self.search.setObjectName("navSearch")
        self.search.setPlaceholderText("搜索设置项（Ctrl+F）")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("搜索设置")
        search_box.addWidget(self.search)
        sl.addLayout(search_box)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.nav.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        sl.addWidget(self.nav, 1)
        self.results = QListWidget()
        self.results.setObjectName("searchResults")
        self.results.setFocusPolicy(Qt.NoFocus)
        self.results.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.results.hide()
        sl.addWidget(self.results, 1)
        self._hits = []
        foot = QVBoxLayout()
        foot.setContentsMargins(20, 0, 20, 0)
        foot.setSpacing(10)
        done = _button("完成", slot=self.accept)
        foot.addWidget(done)
        sl.addLayout(foot)
        root.addWidget(side)

        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        root.addWidget(self.stack, 1)

        self.pages = []
        for key, title, sub, cls in PAGES:
            try:
                widget = cls(self)
            except Exception as exc:
                widget = _ErrorPage(title, exc)
            self.pages.append((key, widget))
            self.stack.addWidget(widget)
            self._add_nav(key, title, sub)
        self.nav.currentRowChanged.connect(self._on_nav)
        self.search.textChanged.connect(self._on_search)
        self.search.returnPressed.connect(self._open_first_result)
        self.results.itemClicked.connect(self._open_result)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self, activated=self._focus_search)
        self.select(initial)

    # ===== 总搜索 =====
    def _focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def _search_index(self):
        """现搜现建（「其他配置项」是读 .env 动态生成的）：每页的页名、设置项名称、所在卡片、说明文字。"""
        out = []
        for (key, page), spec in zip(self.pages, PAGES):
            title, sub = spec[1], spec[2]
            out.append({"key": key, "page": title, "label": None, "name": title, "card": sub, "extra": "", "help": ""})
            if not isinstance(page, QWidget):
                continue
            for frame in page.findChildren(QFrame, "card"):
                labels = frame.findChildren(QLabel)
                card = next((lb.text() for lb in labels if lb.objectName() == "cardTitle"), "")
                current = None
                for lb in labels:
                    kind = lb.objectName()
                    if kind == "fieldLabel" and lb.text().strip():
                        current = {"key": key, "page": title, "label": lb, "name": lb.text().strip(), "card": card,
                                   "extra": str(lb.property("searchExtra") or ""), "help": ""}
                        out.append(current)
                    elif kind == "help" and current is not None:
                        current["help"] += " " + lb.text()
        return out

    def _on_search(self, text):
        q = (text or "").strip().lower()
        self.results.clear()
        self._hits = []
        if not q:
            self.results.hide()
            self.nav.show()
            return
        self.nav.hide()
        self.results.show()
        scored = []
        for i, h in enumerate(self._search_index()):
            for score, field in enumerate(("name", "extra", "card", "help")):
                if q in (h[field] or "").lower():
                    scored.append((score + (0 if h["label"] is not None else 0.5), i, h))
                    break
        scored.sort(key=lambda x: (x[0], x[1]))
        for _score, _i, h in scored[:80]:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, len(self._hits))
            item.setData(Qt.AccessibleTextRole, h["name"])
            self._hits.append(h)
            w = QWidget()
            w.setAttribute(Qt.WA_TranslucentBackground, True)
            vl = QVBoxLayout(w)
            vl.setContentsMargins(14, 5, 12, 5)
            vl.setSpacing(1)
            vl.addWidget(_label(h["name"], "searchHit"))
            where = h["page"] if h["label"] is None else " › ".join(x for x in (h["page"], h["card"]) if x)
            vl.addWidget(_label(where if h["label"] is not None else f"页面 · {h['card']}", "navSub"))
            item.setSizeHint(QSize(200, 48))
            self.results.addItem(item)
            self.results.setItemWidget(item, w)
        if not self._hits:
            empty = QListWidgetItem(f"没有找到「{text.strip()}」")
            empty.setFlags(Qt.NoItemFlags)
            empty.setSizeHint(QSize(200, 40))
            self.results.addItem(empty)

    def _open_first_result(self):
        if self._hits:
            self._open_result(self.results.item(0))

    def _open_result(self, item):
        idx = item.data(Qt.UserRole) if item is not None else None
        if idx is None or not 0 <= idx < len(self._hits):
            return
        h = self._hits[idx]
        self.select(h["key"])
        page = self.page(h["key"])
        if h["label"] is not None and page is not None:
            QTimer.singleShot(0, lambda: self._reveal(page, h["label"]))

    def _reveal(self, page, label):
        try:
            scroll = page.findChild(QScrollArea)
            if scroll is not None and label.isVisibleTo(page):
                scroll.ensureWidgetVisible(label, 0, 120)
            old = label.styleSheet()
            label.setStyleSheet(f"color: {GOLD}; background: rgba(255,209,102,0.16); border-radius: 4px; padding: 0 4px;")
        except RuntimeError:  # 那一项已经被重新生成（比如「其他配置项」刚重载）
            return

        def restore():
            try:
                label.setStyleSheet(old)
            except RuntimeError:
                pass
        QTimer.singleShot(1800, restore)

    def _add_nav(self, key, title, sub):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, key)
        item.setData(Qt.AccessibleTextRole, title)  # 行内容是自绘控件，给读屏 / UI 自动化一个名字
        w = QWidget()
        w.setAttribute(Qt.WA_TranslucentBackground, True)
        hl = QHBoxLayout(w)
        hl.setContentsMargins(12, 5, 12, 5)
        hl.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(36, 36)
        hl.addWidget(icon)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        t = _label(title, "navTitle")
        s = _label(sub, "navSub")
        texts.addWidget(t)
        texts.addWidget(s)
        hl.addLayout(texts, 1)
        dot = _label("●", "navSub")
        dot.setStyleSheet(f"color: {AMBER}; font-size: 10px;")
        dot.setToolTip("有未保存的修改")
        dot.hide()
        hl.addWidget(dot)
        item.setSizeHint(QSize(200, 48))
        self.nav.addItem(item)
        self.nav.setItemWidget(item, w)
        self._nav_rows.append((key, icon, t, dot))

    def _match_panel_geometry(self, panel):
        """和主界面一样大、叠在主界面正上方；仍可自由拉伸。"""
        from .common import DEFAULT_WINDOW_SIZE, MIN_WINDOW_SIZE
        self.setMinimumSize(*MIN_WINDOW_SIZE)
        self.setSizeGripEnabled(True)
        self._target = None
        try:
            geo = QRect(panel.frameGeometry()) if panel.isVisible() else None
        except Exception:
            geo = None
        if geo is None or geo.width() < 200:
            self.resize(*DEFAULT_WINDOW_SIZE)
            return
        self._target = geo
        self.resize(max(geo.width(), MIN_WINDOW_SIZE[0]), max(geo.height() - 32, MIN_WINDOW_SIZE[1]))
        self.move(geo.topLeft())
        if panel.isMaximized():
            self.setWindowState(self.windowState() | Qt.WindowMaximized)
            self._target = None

    def _fit_to_target(self):
        """主界面无边框、设置窗口有系统标题栏：显示后按实际边框把整个窗口对齐到主界面的位置和大小。"""
        t = self._target
        self._target = None
        if t is None or self.isMaximized():
            return
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            t = t.intersected(screen) if screen.intersects(t) else QRect(screen.topLeft(), t.size()).intersected(screen)
        fg, g = self.frameGeometry(), self.geometry()
        dx, dy = g.x() - fg.x(), g.y() - fg.y()
        w = max(t.width() - (fg.width() - g.width()), self.minimumWidth())
        h = max(t.height() - (fg.height() - g.height()), self.minimumHeight())
        self.setGeometry(t.x() + dx, t.y() + dy, w, h)

    def _on_nav(self, row):
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        for i, (key, icon, title, _dot) in enumerate(self._nav_rows):
            active = i == row
            icon.setPixmap(_nav_icon(key, GOLD if active else "#8fa0bb"))
            set_prop(title, "active", "true" if active else "false")

    def refresh_nav_marks(self):
        for (key, _icon, _title, dot), (_k, page) in zip(self._nav_rows, getattr(self, "pages", [])):
            dot.setVisible(bool(getattr(page, "is_dirty", lambda: False)()))

    def select(self, key):
        for i, (k, _page) in enumerate(self.pages):
            if k == key:
                self.nav.setCurrentRow(i)
                self._on_nav(i)
                return
        self.nav.setCurrentRow(0)
        self._on_nav(0)

    def page(self, key):
        return next((p for k, p in self.pages if k == key), None)

    def _confirm_discard(self):
        dirty = [p for _k, p in self.pages if getattr(p, "is_dirty", lambda: False)()]
        if not dirty:
            return True
        names = "、".join(p.title for p in dirty)
        box = QMessageBox(self)
        box.setWindowTitle("有未保存的修改")
        box.setText(f"「{names}」有未保存的修改。")
        save = box.addButton("全部保存", QMessageBox.AcceptRole)
        discard = box.addButton("放弃修改", QMessageBox.DestructiveRole)
        box.addButton("继续编辑", QMessageBox.RejectRole)
        _dark_title_bar(box)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return all(p.do_save() for p in dirty)
        return clicked is discard

    def done(self, result):
        # 完成 / Esc / 关闭按钮最终都走这里：有未保存的修改先询问
        if not self._confirm_discard():
            return
        self._release_panel_widgets()
        super().done(result)

    def closeEvent(self, event):
        event.ignore()
        self.reject()

    def showEvent(self, event):
        _dark_title_bar(self)
        super().showEvent(event)
        if getattr(self, "_target", None) is not None:
            QTimer.singleShot(0, self._fit_to_target)

    def _release_panel_widgets(self):
        """面板上的壁纸库方法通过属性访问本窗口的控件；窗口关闭后解除引用，避免访问已销毁的对象。"""
        for name in ("wallpaper_list_widget", "wallpaper_preview_label", "wallpaper_interval_spin"):
            widget = getattr(self.panel, name, None)
            if isinstance(widget, QWidget) and self.isAncestorOf(widget):
                try:
                    delattr(self.panel, name)
                except AttributeError:
                    pass


def open_settings(panel, page=None):
    """控制面板入口：打开设置中心，可指定初始页（account/model/pixiv/reminder/appearance）。"""
    dialog = SettingsCenter(panel, page if isinstance(page, str) else None)
    show = getattr(panel, "_show_dialog", None)
    if callable(show):
        show(dialog)
    else:
        dialog.exec()
    dialog.deleteLater()
