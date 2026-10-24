# ruff: noqa: F401  （本模块就是给其他页面 import * 用的，导入看似未使用其实都在用）
"""经典面板公共部分：依赖导入、文件/图片小工具、路径与颜色常量（由 window.py 拆出）。

各页面模块用 ``from .common import *`` 取到与原 window.py 完全相同的全局名字。
"""
import ctypes  # _apply_dwm_round 用它设置 Win11 圆角；旧版漏了导入，NameError 被 except 吞掉，圆角从未生效
import json
import os
import re
import shutil
import tempfile
from html import escape
import subprocess
import sys
import threading
import time
import datetime
import webbrowser

from ..services.health import Health, query_health
from . import v2bridge as _v2
from .v2bridge import V2DOC

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QUrl, Qt, QRect, QRectF, QSize, QTimer, QVariantAnimation, Signal
from PySide6.QtWidgets import QGraphicsOpacityEffect

from PySide6.QtGui import (
    QBitmap, QColor, QFont, QIcon, QImage, QImageReader, QLinearGradient, QMovie, QPainter, QPalette, QPen,
    QRegion, QPainterPath, QPainterPathStroker, QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from pypinyin import lazy_pinyin

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCharts import (
    QBarSeries, QBarSet, QBarCategoryAxis, QChart, QChartView, QValueAxis,
)

from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QSizeGrip, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QSizePolicy,
)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _atomic_write_text(path, text, newline=None):
    """先写同目录临时文件再原子替换。

    机器人进程会随时热读取 .env / feature_permissions.json / shared_lore.json；
    直接 open(path, "w") 覆盖写时，机器人可能正好读到空文件或半截内容（旧版机器人读到坏的
    权限文件会把所有群的权限重置）。目标文件恰被占用时短暂重试，最后才退回直接覆盖写。
    """
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05)
        with open(path, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _atomic_write_json(path, data):
    if str(path).startswith(V2DOC):
        _v2.write_doc(path, data)
        return
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _trim_log(path, limit=5 * 1024 * 1024):
    """启动前收拢累积日志：超过 limit 时只把末尾 limit 字节留作 *.1.log，原文件移走后重新开始。

    restart_stdout/stderr.log 以追加方式接收机器人控制台输出，此前从不轮转（曾涨到 50MB+）；
    完整历史仍在自带轮转的 bot.log 里。用改名而不是原地截断：文件仍被旧进程占用时改名失败、
    原文件保持不动（原地截断会让旧写入者在原偏移处继续写，留下几十 MB 的空洞）。
    """
    root, ext = os.path.splitext(path)
    backup = f"{root}.1{ext}"
    tmp = backup + ".tmp"
    try:
        size = os.path.getsize(path)
        if size <= limit:
            return
        with open(path, "rb") as src:
            src.seek(size - limit)
            tail = src.read()
        cut = tail.find(b"\n")
        if 0 <= cut < 4096:
            tail = tail[cut + 1:]
        with open(tmp, "wb") as f:
            f.write(tail)
        os.replace(path, backup)
        os.replace(tmp, backup)
    except OSError:
        pass
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v")
WALLPAPER_THUMB_PX = (336, 208)  # 壁纸缩略图按 2 倍分辨率生成，高分屏下也清晰
WALLPAPER_PREVIEW_SIZE = (298, 184)  # 设置页预览框（300×186）的可用区域，逻辑像素


def _read_image_fit(path, width, height, expand=False):
    """按显示需要的尺寸解码图片，返回 QImage；可在后台线程调用。

    壁纸动辄 10240×4320、几十 MB，整张解码一次要近 1 秒，还要占上百 MB 内存。这里让解码器直接输出
    接近目标的尺寸（JPEG 在解码阶段就能按 1/2、1/4、1/8 缩小）；原图不比目标大时照常整张读取。
    expand=False：完整放进 width×height（缩略图、预览）；expand=True：至少铺满它（背景裁切铺满用）。
    """
    reader = QImageReader(path)
    size = reader.size()
    if size.isValid() and width > 0 and height > 0:
        mode = Qt.KeepAspectRatioByExpanding if expand else Qt.KeepAspectRatio
        target = size.scaled(int(width), int(height), mode)
        if target.width() < size.width() and target.height() < size.height():
            reader.setScaledSize(target)
    return reader.read()


def _screen_cover_px():
    """所有屏幕里最大的物理像素宽、高；背景解码到能盖住它就足够清晰。须在主线程调用。"""
    w = h = 0
    for screen in QApplication.screens():
        dpr = screen.devicePixelRatio() or 1.0
        geo = screen.geometry()
        w, h = max(w, int(geo.width() * dpr)), max(h, int(geo.height() * dpr))
    return (w or 2560, h or 1600)


def _file_stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


BOT_DIR = _v2.ROOT_DIR  # v2 根目录：.env、bot.py 都在这里
NAPCAT_DIR = _v2.NAPCAT_DIR
PYTHON_EXE = _v2.PYTHON_EXE
BOT_STDOUT_LOG = _v2.CONSOLE_LOG  # 机器人控制台输出（stdout+stderr 合并）
BOT_STDERR_LOG = _v2.CONSOLE_LOG
SPLASH_DIR = _v2.asset("backgrounds", "splash")
BACKDROP_DIR = _v2.asset("backgrounds", "backdrop")
PERSONA_PATH = _v2.PERSONA_PATH
BOT_ICON = _v2.asset("avatar_round.png")
CONN_ICON = _v2.asset("conn_test_icon.png")
LOGO_SVG = _v2.asset("logo_furina.svg")
LOGO_SVG_DARK = _v2.asset("logo_furina_dark.svg")
CHECKMARK_SVG = _v2.asset("checkmark_white.svg").replace("\\", "/")
GROUP_OWNER_QQ = ""  # 不硬编码，一律读 .env 的 AI_CHAT_GROUP_OWNER_QQ
BOT_LOG_PATH = _v2.BOT_LOG
NAPCAT_LOG_DIR = os.path.join(NAPCAT_DIR, "logs")
NAPCAT_QRCODE_PATH = os.path.join(NAPCAT_DIR, "cache", "qrcode.png")  # NapCat 每次生成登录二维码都会覆盖这张图
CUSTOM_BG_DIR = _v2.CUSTOM_BG_DIR
CUSTOM_BG_CONFIG = V2DOC + "custom_background.json"
WALLPAPER_LIBRARY_PATH = V2DOC + "wallpaper_library.json"

BG_FILES = [os.path.join(SPLASH_DIR, f"bg{i}.jpg") for i in range(1, 10)]
BACKDROP_FILES = [os.path.join(BACKDROP_DIR, f"bg{i}.jpg") for i in range(1, 10)]


DEFAULT_WINDOW_SIZE = (1320, 880)
MIN_WINDOW_SIZE = (980, 640)  # 可拖到的最小尺寸（首页 600 宽工作区 + 侧栏 + 启动按钮仍放得下）
# 侧栏分组（按钮键名见 window._build_left_panel 的 specs）与图标颜色
RAIL_GROUPS = (
    ("概览", ("home", "features", "commands")),
    ("管理", ("permissions", "role", "games")),
    ("运行", ("logs", "stats", "usage", "spend")),
    ("生活", ("bili", "pics")),
)
RAIL_ICON_OFF = "#8193b2"
RAIL_ICON_ON = "#6ad4ff"

from .theme import *  # noqa: E402,F401,F403  统一配色（COLOR_* 等）

__all__ = [n for n in dir() if not n.startswith('__')]
