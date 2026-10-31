"""经典面板统一配色与通用样式片段（2026-10 重做）。

设计约定（新代码和改动请只从这里取颜色/样式）：
- 功能页一律实色底，不再把壁纸垫在内容下面；壁纸只留在主页。
- 三层表面：APP_BG（页面底）< SURFACE（卡片）< SURFACE_RAISED（按钮/悬停）；输入框用更深的 SURFACE_DEEP。
- 只有一个强调色 ACCENT（青蓝）；状态色只用 STATUS_OK / STATUS_WARN / STATUS_BAD。
- 字号：页标题 20、卡片标题 14、正文 13、说明 12；圆角：卡片 12、控件 8。
"""

# ---- 基础色（原 window.py 常量，名字不变）----
COLOR_BG = "#0b1220"
COLOR_PANEL = "#0d1524"
COLOR_TEXT = "#e8eef7"
COLOR_MUTED = "#94a3b8"
COLOR_GOLD = "#ffd166"
COLOR_BLUE = "#6ad4ff"
COLOR_GREEN = "#34d399"
COLOR_RED = "#f87171"
COLOR_BTN = "#1a2539"
COLOR_BTN_TEXT = "#dbe4f0"

# ---- 语义色 ----
APP_BG = "#0d1524"            # 功能页底色
RAIL_BG = "#0a101c"           # 左侧导航
SURFACE = "#141e30"           # 卡片/分组底色
SURFACE_DEEP = "#0a1120"      # 输入框、日志区
SURFACE_RAISED = "#1a2539"    # 按钮
SURFACE_HOVER = "#1e2b42"     # 列表项/行悬停
SURFACE_SELECTED = "#1f3550"
BORDER = "rgba(255,255,255,0.07)"
BORDER_STRONG = "rgba(255,255,255,0.13)"
BORDER_ACCENT = "rgba(106,212,255,0.55)"
ACCENT = "#6ad4ff"
ACCENT_SOFT = "rgba(106,212,255,0.14)"
TEXT_PRIMARY = "#f1f5f9"
TEXT_BODY = "#d5deea"
TEXT_SECONDARY = "#94a3b8"
TEXT_HINT = "#748299"
STATUS_OK = "#34d399"
STATUS_WARN = "#fbbf24"
STATUS_BAD = "#f87171"

RADIUS_CARD = 12
RADIUS_CONTROL = 8

# ---- 通用样式片段 ----
LINE_EDIT_QSS = (
    f"QLineEdit {{ background: {SURFACE_DEEP}; color: {COLOR_TEXT}; border: 1px solid {BORDER_STRONG};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 7px 10px; font-size: 13px; }}"
    f"QLineEdit:focus {{ border-color: {BORDER_ACCENT}; }}"
)
MINI_BUTTON_QSS = (
    f"QPushButton {{ background: {SURFACE_RAISED}; color: {TEXT_BODY}; border: 1px solid {BORDER};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 4px 10px; font-size: 12px; }}"
    f"QPushButton:hover {{ background: {SURFACE_HOVER}; border-color: {BORDER_STRONG}; color: #ffffff; }}"
    f"QPushButton:pressed {{ background: {SURFACE_SELECTED}; }}"
)


def text_color_qss(color: str, size: int | None = None, bold: bool = False) -> str:
    """单个标签的文字样式（颜色 + 可选字号/加粗）。"""
    parts = [f"color: {color};"]
    if size:
        parts.append(f"font-size: {size}px;")
    if bold:
        parts.append("font-weight: bold;")
    return " ".join(parts)
