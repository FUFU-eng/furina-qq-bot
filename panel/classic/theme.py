"""经典面板统一配色与通用样式片段。

以前颜色散落在几十处 setStyleSheet 字符串里（同一种“正文灰”有 5 种写法）；
新代码和改动请只从这里取颜色/样式，保证各页面观感一致。
"""

# ---- 基础色（原 window.py 常量，名字不变）----
COLOR_BG = "#0a0f2d"
COLOR_PANEL = "#0d1526"
COLOR_TEXT = "#f4f7ff"
COLOR_MUTED = "#8a94a6"
COLOR_GOLD = "#ffd166"
COLOR_BLUE = "#6ad4ff"
COLOR_GREEN = "#3ecf8e"
COLOR_RED = "#e5484d"
COLOR_BTN = "#1e2540"
COLOR_BTN_TEXT = "#e2e8f0"

# ---- 语义色（与全局样式表 _qss 保持一致）----
SURFACE = "#172337"          # 卡片/分组底色
SURFACE_DEEP = "#0d1726"     # 输入框、日志区
SURFACE_HOVER = "#1b2a41"    # 列表项/行悬停
SURFACE_SELECTED = "#223957"
BORDER = "rgba(255,255,255,0.08)"
BORDER_STRONG = "rgba(255,255,255,0.16)"
BORDER_ACCENT = "rgba(106,212,255,0.45)"
TEXT_PRIMARY = "#f1f5f9"
TEXT_BODY = "#dbe6f5"
TEXT_SECONDARY = "#9aabc4"
TEXT_HINT = "#6f819c"
STATUS_OK = "#10b981"
STATUS_WARN = "#f59e0b"
STATUS_BAD = "#ef4444"

RADIUS_CARD = 14
RADIUS_CONTROL = 9

# ---- 通用样式片段 ----
LINE_EDIT_QSS = (
    f"QLineEdit {{ background: {SURFACE_DEEP}; color: {COLOR_TEXT}; border: 1px solid rgba(255,255,255,0.12);"
    f" border-radius: {RADIUS_CONTROL}px; padding: 8px 10px; font-size: 13px; }}"
    f"QLineEdit:focus {{ border-color: rgba(106,212,255,0.60); }}"
)
MINI_BUTTON_QSS = (
    f"QPushButton {{ background: {SURFACE_HOVER}; color: {TEXT_BODY}; border: 1px solid {BORDER};"
    f" border-radius: 8px; padding: 4px 10px; font-size: 12px; }}"
    f"QPushButton:hover {{ background: #263957; border-color: rgba(106,212,255,0.42); color: #ffffff; }}"
    f"QPushButton:pressed {{ background: #31486b; }}"
)


def text_color_qss(color: str, size: int | None = None, bold: bool = False) -> str:
    """单个标签的文字样式（颜色 + 可选字号/加粗）。"""
    parts = [f"color: {color};"]
    if size:
        parts.append(f"font-size: {size}px;")
    if bold:
        parts.append("font-weight: bold;")
    return " ".join(parts)
