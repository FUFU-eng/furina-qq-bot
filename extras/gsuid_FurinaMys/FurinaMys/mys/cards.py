"""三张卡片：扫码二维码 / 已绑定账号 / 指令帮助。

用 GsCore 自带的 Takumi HTML 渲染（与 FurinaSign 签到战报同一套视觉：浅蓝水色 + 白色卡片）。
"""
from __future__ import annotations

import io
import base64
import datetime as dt
from html import escape
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from dataclasses import field, dataclass

from gsuid_core.utils.html_render import render_html_to_bytes

RES = Path(__file__).resolve().parent / "res"
CSS_WIDTH = 920
WEEK = "一二三四五六日"

GAME_META = {
    # game: (显示名, 徽标字, 主色, 浅色底, 前缀)
    "gs": ("原神", "原", "#e8a93b", "#fff6e3", "ys"),
    "sr": ("崩坏：星穹铁道", "铁", "#7c6cf2", "#f0eeff", "bt"),
    "zzz": ("绝区零", "绝", "#f08a24", "#fff1e4", "zzz"),
    "bbb": ("崩坏3", "崩", "#ec5f9c", "#ffedf5", "bbb"),
}
GAME_ORDER = ("gs", "sr", "zzz", "bbb")


def _furina_uri() -> str:
    for path in (RES / "furina.png", Path(__file__).resolve().parents[3] / "FurinaSign/FurinaSign/report/res/furina.png"):
        if path.is_file():
            return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    return ""


BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { width: 100%; font-family: "MiSans", "PingFang SC", "Microsoft YaHei", sans-serif; color: #1f3a5f;
  background: linear-gradient(160deg, #d9ecff 0%, #eef6ff 45%, #e3eeff 100%); }
.page { width: 100%; padding: 30px; display: flex; flex-direction: column; gap: 18px; }
.hero { position: relative; display: flex; align-items: center; gap: 18px; padding: 24px 26px; border-radius: 22px;
  background: linear-gradient(120deg, #4f8dff 0%, #5cb6ff 55%, #7fd3ff 100%); box-shadow: 0 10px 28px rgba(64,120,220,0.28); overflow: hidden; }
.bubble { position: absolute; border-radius: 999px; background: rgba(255,255,255,0.16); }
.avatar { width: 78px; height: 78px; border-radius: 999px; overflow: hidden; position: relative; flex: none;
  border: 3px solid rgba(255,255,255,0.88); background: #dff0ff; }
.avatar img { position: absolute; left: 0; top: 0; width: 100%; height: 100%; object-fit: cover; }
.hero-text { display: flex; flex-direction: column; gap: 8px; flex: 1; min-width: 0; }
.title-row { display: flex; align-items: center; gap: 12px; }
.title { font-size: 30px; font-weight: 630; color: #ffffff; letter-spacing: 1px; }
.tag { font-size: 14px; font-weight: 520; color: #2a5fb8; background: rgba(255,255,255,0.92); padding: 4px 12px; border-radius: 999px; }
.subtitle { font-size: 16px; color: rgba(255,255,255,0.96); }
.panel { display: flex; flex-direction: column; border-radius: 18px; background: #ffffff; border: 1px solid #d3e5fb;
  box-shadow: 0 6px 18px rgba(80,130,200,0.12); overflow: hidden; }
.footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 4px 6px 0 6px; }
.tips { display: flex; flex-direction: column; gap: 4px; font-size: 14px; color: #4a6890; flex: 1; min-width: 0; }
.brand { font-size: 13px; color: #7b93b5; text-align: right; line-height: 1.5; flex: none; }
"""


def _hero(title: str, subtitle: str, tag: str = "") -> str:
    avatar = _furina_uri()
    avatar_html = f'<div class="avatar"><img src="{avatar}"></div>' if avatar else ""
    tag_html = f'<div class="tag">{escape(tag)}</div>' if tag else ""
    return f"""<div class="hero">
  <div class="bubble" style="width:190px;height:190px;right:-46px;top:-76px"></div>
  <div class="bubble" style="width:96px;height:96px;right:160px;bottom:-54px"></div>
  {avatar_html}
  <div class="hero-text">
    <div class="title-row"><div class="title">{escape(title)}</div>{tag_html}</div>
    <div class="subtitle">{escape(subtitle)}</div>
  </div>
</div>"""


def _today_tag(today: Optional[dt.date] = None) -> str:
    today = today or dt.date.today()
    return f"{today:%Y-%m-%d} 周{WEEK[today.weekday()]}"


def _page(body: str, extra_css: str = "") -> str:
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{BASE_CSS}{extra_css}</style></head>'
        f'<body><div class="page">{body}</div></body></html>'
    )


async def _render(html: str) -> bytes:
    return await render_html_to_bytes(
        html, max_width=CSS_WIDTH * 2, dpi=192, root_max_width=CSS_WIDTH,
        default_font_size=15, allow_refit=True, image_format="png", lang="zh",
    )


def mask_qq(user_id: str) -> str:
    s = str(user_id)
    return s if len(s) <= 4 else f"{s[:2]}***{s[-2:]}"


# ---------------------------------------------------------------- 二维码卡片
QR_CSS = """
.qr-wrap { display: flex; gap: 26px; padding: 26px; align-items: center; }
.qr-box { width: 330px; height: 330px; flex: none; border-radius: 20px; background: #ffffff; border: 2px solid #d3e5fb;
  display: flex; align-items: center; justify-content: center; position: relative; overflow: hidden; }
.qr-box img { width: 306px; height: 306px; }
.steps { display: flex; flex-direction: column; gap: 14px; flex: 1; min-width: 0; }
.step { display: flex; align-items: center; gap: 12px; }
.num { width: 34px; height: 34px; border-radius: 999px; background: linear-gradient(135deg, #4f8dff, #7fd3ff); color: #fff;
  font-size: 17px; font-weight: 630; display: flex; align-items: center; justify-content: center; flex: none; }
.step-text { font-size: 18px; color: #1f3a5f; font-weight: 520; }
.note { margin-top: 6px; padding: 12px 14px; border-radius: 14px; background: #f2f8ff; border: 1px solid #dceafb;
  font-size: 14px; color: #4a6890; line-height: 1.6; }
.games { display: flex; gap: 8px; flex-wrap: wrap; }
.g-chip { display: flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 999px; font-size: 14px; font-weight: 520; }
"""


def make_qr_png(url: str) -> bytes:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=12, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=(24, 52, 96), back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_qr_html(qr_png: bytes, user_name: str, timeout: int, bot: str) -> str:
    qr_uri = "data:image/png;base64," + base64.b64encode(qr_png).decode()
    chips = "".join(
        f'<div class="g-chip" style="background:{GAME_META[g][3]};color:{GAME_META[g][2]}">{GAME_META[g][0]}</div>'
        for g in GAME_ORDER
    )
    minutes = max(1, round(timeout / 60))
    body = _hero("米哈游通行证 · 扫码绑定", f"为 {user_name} 生成的专属二维码，一次扫码绑定通行证下全部游戏账号", _today_tag())
    body += f"""<div class="panel"><div class="qr-wrap">
  <div class="qr-box"><img src="{qr_uri}"></div>
  <div class="steps">
    <div class="step"><div class="num">1</div><div class="step-text">打开「米游社」App</div></div>
    <div class="step"><div class="num">2</div><div class="step-text">点右上角「扫一扫」扫描左侧二维码</div></div>
    <div class="step"><div class="num">3</div><div class="step-text">在手机上点「确认登录」</div></div>
    <div class="games">{chips}</div>
    <div class="note">⏳ 二维码 {minutes} 分钟内有效，扫码后会自动撤回<br>🔐 只用于米游社查询和每日自动签到，请勿扫描别人的二维码</div>
  </div>
</div></div>
<div class="footer"><div class="tips"><div>💡 绑定后每天 10:00 自动签到，发「@{escape(bot)} 指令帮助」查看全部功能</div></div>
<div class="brand">Furina Miyoushe Assistant</div></div>"""
    return _page(body, QR_CSS)


async def render_qr_card(qr_png: bytes, user_name: str, timeout: int, bot: str) -> bytes:
    return await _render(build_qr_html(qr_png, user_name, timeout, bot))


# ---------------------------------------------------------------- 账号卡片
@dataclass
class Role:
    game: str
    uid: str
    nickname: str = ""
    level: Optional[int] = None
    region: str = ""
    main: bool = False
    sign: bool = False
    has_ck: bool = False


@dataclass
class AccountCard:
    user_name: str
    user_id: str
    roles: List[Role] = field(default_factory=list)
    mys_id: str = ""
    title: str = "绑定成功"
    subtitle: str = ""


ACC_CSS = """
.row { display: flex; align-items: center; gap: 16px; padding: 16px 22px; border-bottom: 1px solid #edf3fb; }
.row.last { border-bottom: none; }
.badge { width: 54px; height: 54px; border-radius: 16px; display: flex; align-items: center; justify-content: center;
  color: #ffffff; font-size: 24px; font-weight: 630; flex: none; }
.info { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; }
.line1 { display: flex; align-items: center; gap: 10px; }
.nick { font-size: 21px; font-weight: 630; color: #1f3a5f; overflow-wrap: anywhere; }
.main { font-size: 12px; font-weight: 630; color: #ffffff; padding: 2px 8px; border-radius: 999px; background: #4f8dff; }
.line2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.chip { padding: 3px 10px; border-radius: 999px; font-size: 14px; color: #35557f; background: #f2f8ff; border: 1px solid #dceafb; }
.chip b { font-weight: 630; color: #1d4f9c; }
.state { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex: none; }
.ok { font-size: 14px; font-weight: 630; color: #1d8a5a; background: #e6f7ef; border: 1px solid #bfe8d3; padding: 4px 12px; border-radius: 999px; }
.warn { font-size: 14px; font-weight: 630; color: #b26a00; background: #fff4e0; border: 1px solid #f5d9a8; padding: 4px 12px; border-radius: 999px; }
.sub { font-size: 14px; font-weight: 520; color: #5a7398; background: #f2f6fb; border: 1px solid #dde6f2; padding: 4px 12px; border-radius: 999px; }
.empty { padding: 30px; font-size: 17px; color: #4a6890; text-align: center; }
.sum { display: flex; gap: 10px; flex-wrap: wrap; }
.pill { display: flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: 999px; background: #ffffff;
  border: 1px solid #d3e5fb; font-size: 15px; color: #35557f; }
.pill b { font-weight: 630; color: #1d4f9c; }
.dot { width: 9px; height: 9px; border-radius: 999px; }
"""


def build_account_html(card: AccountCard, bot: str) -> str:
    roles = sorted(card.roles, key=lambda r: (GAME_ORDER.index(r.game), not r.main, -(r.level or 0)))
    rows = []
    for i, r in enumerate(roles):
        name, mark, color, light, prefix = GAME_META[r.game]
        chips = [f'<div class="chip">UID <b>{escape(r.uid)}</b></div>']
        if r.level:
            chips.append(f'<div class="chip">等级 <b>Lv.{r.level}</b></div>')
        if r.region:
            chips.append(f'<div class="chip">{escape(r.region)}</div>')
        state = []
        if r.sign:
            state.append('<div class="ok">✓ 自动签到已开启</div>')
        elif r.has_ck:
            state.append('<div class="ok">✓ 已登录</div>')
        elif not r.main:
            state.append('<div class="sub">副账号 · 未开签到</div>')
        else:
            state.append('<div class="warn">未登录 · 请重新扫码</div>')
        main_html = '<div class="main">当前</div>' if r.main else ""
        nick = escape(r.nickname or name)
        rows.append(
            f'<div class="row{" last" if i == len(roles) - 1 else ""}">'
            f'<div class="badge" style="background:linear-gradient(135deg,{color},{color}cc)">{mark}</div>'
            f'<div class="info"><div class="line1"><div class="nick">{nick}</div>{main_html}</div>'
            f'<div class="line2"><div class="chip" style="background:{light};color:{color};border-color:{light}">{name}</div>{"".join(chips)}</div></div>'
            f'<div class="state">{"".join(state)}<div class="chip">{prefix}每日 · {prefix}深渊</div></div></div>'
        )
    if not rows:
        rows.append('<div class="empty">这个通行证下还没有原神 / 星铁 / 绝区零 / 崩坏3 的游戏账号～</div>')
    counts: Dict[str, int] = {}
    for r in roles:
        counts[r.game] = counts.get(r.game, 0) + 1
    pills = [f'<div class="pill"><div class="dot" style="background:#4f8dff"></div>QQ <b>{escape(mask_qq(card.user_id))}</b></div>']
    if card.mys_id:
        pills.append(f'<div class="pill"><div class="dot" style="background:#22a06b"></div>通行证 <b>{escape(mask_qq(card.mys_id))}</b></div>')
    for g in GAME_ORDER:
        if g in counts:
            short = "星铁" if g == "sr" else GAME_META[g][0]
            pills.append(f'<div class="pill"><div class="dot" style="background:{GAME_META[g][2]}"></div>{short} <b>{counts[g]}</b></div>')
    subtitle = card.subtitle or f"{card.user_name} 的米游社游戏账号已全部就绪"
    body = _hero(card.title, subtitle, _today_tag())
    body += f'<div class="sum">{"".join(pills)}</div><div class="panel">{"".join(rows)}</div>'
    body += f"""<div class="footer"><div class="tips">
<div>📅 每天 10:00 自动签到，10:05 群里发签到战报</div>
<div>💡 试试：@{escape(bot)} ys每日 · ys芙宁娜 · ys深渊 · 更新展柜 · 指令帮助</div></div>
<div class="brand">Furina Miyoushe Assistant<br>账号出现异常请重新「绑定账号」</div></div>"""
    return _page(body, ACC_CSS)


async def render_account_card(card: AccountCard, bot: str) -> bytes:
    return await _render(build_account_html(card, bot))


# ---------------------------------------------------------------- 指令帮助卡片
HELP_CSS = """
.legend { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.legend-cap { font-size: 15px; color: #4a6890; font-weight: 520; }
.lg { display: flex; align-items: center; gap: 8px; padding: 7px 14px; border-radius: 999px; background: #ffffff; border: 1px solid #d3e5fb; font-size: 15px; color: #35557f; }
.lg b { font-weight: 630; }
.sec { display: flex; align-items: center; gap: 10px; padding: 2px 4px; }
.sec-bar { width: 6px; height: 22px; border-radius: 999px; }
.sec-title { font-size: 21px; font-weight: 630; color: #1f3a5f; }
.sec-sub { font-size: 14px; color: #6a85aa; }
.grid { display: flex; flex-wrap: wrap; gap: 14px; }
.item { width: 422px; display: flex; flex-direction: column; gap: 9px; padding: 16px 18px; border-radius: 16px; background: #ffffff;
  border: 1px solid #d3e5fb; box-shadow: 0 4px 14px rgba(80,130,200,0.10); }
.item-head { display: flex; align-items: center; gap: 10px; }
.icon { width: 36px; height: 36px; border-radius: 11px; display: flex; align-items: center; justify-content: center; font-size: 20px; flex: none; }
.item-title { font-size: 18px; font-weight: 630; color: #1f3a5f; }
.item-desc { font-size: 14px; color: #5a7398; line-height: 1.55; }
.cmds { display: flex; flex-wrap: wrap; gap: 7px; }
.cmd { padding: 4px 11px; border-radius: 9px; font-size: 14px; font-weight: 520; min-width: 0; }
"""

MYS_ITEMS = [
    ("🔗", "绑定账号", "扫一次码绑定通行证下全部游戏并开启签到；遇到验证码就「绑定设备」，不用了可「解除绑定」",
     ["绑定账号", "我的账号", "绑定设备", "解除绑定"]),
    ("🌙", "体力与每日", "树脂 / 开拓力 / 电量、每日委托、派遣等实时便笺", ["ys每日", "bt每日", "zzz每日", "bbb每日"]),
    ("🎭", "角色面板", "查询你自己账号里的角色面板；崩坏3 为女武神总览", ["ys芙宁娜", "bt黄泉", "zzz星见雅", "bbb查询"]),
    ("📘", "一图流攻略", "角色养成一图流；崩坏3 为往世乐土攻略", ["ys芙宁娜攻略", "bt黄泉攻略", "zzz雅攻略", "bbb人律攻略"]),
    ("🏯", "深渊战绩", "一次发全套：原神深渊·剧诗·危战｜星铁忘却·虚构·末日·异相·货币战争｜绝区零防卫战·强袭战·临界推演·零号空洞｜崩3超弦·战场·乐土",
     ["ys深渊", "bt深渊", "zzz深渊", "bbb深渊"]),
    ("📅", "今日素材", "今天能刷的天赋书与武器突破材料（原神）", ["ys今日素材"]),
    ("🔄", "更新展柜", "从米游社刷新全部游戏的角色面板（不用摆展柜）；加前缀只刷新单个游戏", ["更新展柜", "ys更新展柜"]),
    ("✅", "自动签到", "每天 10:00 自动签到四个游戏，10:05 在群里发签到战报；也可以手动补签", ["米游社签到"]),
]

FURINA_ITEMS = [
    ("💬", "AI 聊天", "带记忆、好感度和心情；发图或引用图片 @芙芙 还能看图追问", ["@芙芙 想说的话", "总结", "双击头像戳一戳"]),
    ("🔍", "搜图强化", "引用图片找原图出处；AI 高清放大，合并转发可批量强化", ["[引用图片] 搜图", "[引用图片] 强化"]),
    ("🖼️", "插画图库", "按角色从 Pixiv 点高收藏插画，或按作品 ID 取原图", ["来张芙宁娜的图", "pid113456789", "来两张图"]),
    ("⏰", "定时提醒", "一次性或每天 / 每周循环提醒，生成方案后回复确认即可", ["20分钟后提醒我收菜", "我的提醒", "取消提醒 1"]),
    ("🛡️", "群管设定", "引用消息设精华、代艾特传话、记住你的称呼喜好；资讯与封神榜自动推送", ["[引用消息] 设精华", "艾特张三 出来玩", "以后叫我旅行者"]),
]


def _items_html(items: Sequence, color: str, light: str) -> str:
    out = []
    for icon, title, desc, cmds in items:
        chips = "".join(f'<div class="cmd" style="background:{light};color:{color}">{escape(c)}</div>' for c in cmds)
        out.append(
            f'<div class="item"><div class="item-head"><div class="icon" style="background:{light}">{icon}</div>'
            f'<div class="item-title">{escape(title)}</div></div>'
            f'<div class="item-desc">{escape(desc)}</div><div class="cmds">{chips}</div></div>'
        )
    return "".join(out)


def build_help_html(bot: str) -> str:
    legend = "".join(
        f'<div class="lg"><div class="dot" style="width:9px;height:9px;border-radius:999px;background:{GAME_META[g][2]}"></div>'
        f'{GAME_META[g][0] if g != "sr" else "星铁"} <b style="color:{GAME_META[g][2]}">{GAME_META[g][4]}</b></div>'
        for g in GAME_ORDER
    )
    body = _hero(f"{bot}指令手册", f"所有功能都要先 @{bot} 再说指令哦，不 @ 的消息{bot}不会理睬", _today_tag())
    body += f'<div class="legend"><div class="legend-cap">游戏前缀</div>{legend}</div>'
    body += ('<div class="sec"><div class="sec-bar" style="background:#4f8dff"></div><div class="sec-title">米游社游戏助手</div>'
             '<div class="sec-sub">前缀 + 功能，例如 @芙芙 ys每日</div></div>')
    body += f'<div class="grid">{_items_html(MYS_ITEMS, "#2a5fb8", "#eaf3ff")}</div>'
    body += (f'<div class="sec"><div class="sec-bar" style="background:#7c6cf2"></div><div class="sec-title">{escape(bot)}的其他本领</div>'
             '<div class="sec-sub">同样需要 @ 才会触发</div></div>')
    body += f'<div class="grid">{_items_html(FURINA_ITEMS, "#5b4bd6", "#f0eeff")}</div>'
    body += f"""<div class="footer"><div class="tips">
<div>🔐 没绑定账号时查询个人数据，{escape(bot)}会先发二维码请你绑定</div>
<div>🎯 角色名支持常用别名，例如 ys芙芙、bt流萤</div></div>
<div class="brand">Furina Miyoushe Assistant<br>每日 10:00 自动签到</div></div>"""
    return _page(body, HELP_CSS + ".dot { flex: none; }")


async def render_help_card(bot: str) -> bytes:
    return await _render(build_help_html(bot))
