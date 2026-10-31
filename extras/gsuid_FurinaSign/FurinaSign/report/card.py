"""签到总结卡片：GsCore 自带 Takumi HTML 渲染（MiSans + Twemoji，无豆腐块）。"""
from __future__ import annotations

import base64
import datetime as dt
from html import escape
from pathlib import Path
from typing import List, Optional

from gsuid_core.utils.html_render import render_html_to_bytes

from .collect import GAMES, Member, mask_qq

RES = Path(__file__).resolve().parent / "res"
CSS_WIDTH = 920
WEEK = "一二三四五六日"
GAME_COLOR = {"gs": "#e8a93b", "sr": "#7c6cf2", "bbb": "#ec5f9c", "zzz": "#f08a24"}
AVATAR_COLORS = ("#5aa9ff", "#7c6cf2", "#36c2b4", "#f08a24", "#ec5f9c", "#4f7cff", "#22a06b")
STATE_ICON = {"ok": "✓", "pending": "…", "fail": "✕", "off": "—"}


def _data_uri(name: str) -> str:
    path = RES / name
    if not path.is_file():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { width: 100%; font-family: "MiSans", "PingFang SC", "Microsoft YaHei", sans-serif; color: #1f3a5f;
  background: linear-gradient(160deg, #d9ecff 0%, #eef6ff 42%, #e3eeff 100%); }
.page { width: 100%; padding: 30px; display: flex; flex-direction: column; gap: 18px; }
.hero { position: relative; display: flex; align-items: center; gap: 18px; padding: 24px 26px; border-radius: 22px;
  background: linear-gradient(120deg, #4f8dff 0%, #5cb6ff 55%, #7fd3ff 100%); box-shadow: 0 10px 28px rgba(64,120,220,0.28); overflow: hidden; }
.bubble { position: absolute; border-radius: 999px; background: rgba(255,255,255,0.16); }
.avatar { width: 76px; height: 76px; border-radius: 999px; overflow: hidden; position: relative; flex: none;
  border: 3px solid rgba(255,255,255,0.85); background: #dff0ff; }
.avatar img { position: absolute; left: 0; top: 0; width: 100%; height: 100%; object-fit: cover; }
.hero-text { display: flex; flex-direction: column; gap: 8px; flex: 1; min-width: 0; }
.title-row { display: flex; align-items: center; gap: 12px; }
.title { font-size: 30px; font-weight: 630; color: #ffffff; letter-spacing: 1px; }
.date { font-size: 15px; font-weight: 520; color: #2a5fb8; background: rgba(255,255,255,0.92); padding: 4px 12px; border-radius: 999px; }
.subtitle { font-size: 16px; color: rgba(255,255,255,0.95); }
.pills { display: flex; flex-wrap: wrap; gap: 10px; }
.pill { display: flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: 999px; background: #ffffff;
  border: 1px solid #d3e5fb; font-size: 15px; color: #35557f; }
.pill b { font-weight: 630; color: #1d4f9c; }
.dot { width: 9px; height: 9px; border-radius: 999px; }
.table { display: flex; flex-direction: column; border-radius: 18px; overflow: hidden; background: #ffffff;
  border: 1px solid #d3e5fb; box-shadow: 0 6px 18px rgba(80,130,200,0.12); }
.thead { display: flex; align-items: center; padding: 13px 18px; background: #f2f8ff; border-bottom: 1px solid #dceafb;
  font-size: 15px; font-weight: 520; color: #4a6890; }
.row { display: flex; align-items: center; padding: 12px 18px; border-bottom: 1px solid #edf3fb; }
.row.alt { background: #fafcff; }
.who { width: 228px; flex: none; display: flex; align-items: center; gap: 12px; min-width: 0; }
.initial { width: 40px; height: 40px; border-radius: 999px; display: flex; align-items: center; justify-content: center;
  color: #ffffff; font-size: 18px; font-weight: 630; flex: none; }
.who-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.name { font-size: 17px; font-weight: 520; color: #1f3a5f; overflow-wrap: anywhere; }
.qq { font-size: 13px; color: #8aa0bd; }
.col { flex: 1; min-width: 0; display: flex; justify-content: center; padding: 0 4px; }
.col-head { flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; gap: 7px; }
.chip { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 7px 8px; border-radius: 12px; width: 100%; max-width: 150px; }
.chip-main { font-size: 15px; font-weight: 520; }
.chip-sub { font-size: 13px; }
.chip.ok { background: #e6f7ee; } .chip.ok .chip-main { color: #138a4b; } .chip.ok .chip-sub { color: #3f9d6b; }
.chip.pending { background: #fff4dc; } .chip.pending .chip-main { color: #b26a00; } .chip.pending .chip-sub { color: #c28a2c; }
.chip.fail { background: #fde9e9; } .chip.fail .chip-main { color: #cf3030; } .chip.fail .chip-sub { color: #d86a6a; }
.chip.off { background: #f2f5f9; } .chip.off .chip-main { color: #9aa9bc; } .chip.off .chip-sub { color: #b3bfcd; }
.empty { padding: 26px; text-align: center; font-size: 16px; color: #7b93b3; }
.footer { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 0 6px; }
.tips { display: flex; flex-direction: column; gap: 5px; font-size: 14px; color: #4f6d93; }
.brand { font-size: 13px; color: #7f97b8; text-align: right; }
"""


def _chip(cell) -> str:
    sub = f'<div class="chip-sub">{escape(cell.detail)}</div>' if cell.detail else ""
    icon = STATE_ICON.get(cell.state, "")
    return f'<div class="chip {cell.state}"><div class="chip-main">{icon} {escape(cell.label)}</div>{sub}</div>'


def _row(index: int, member: Member) -> str:
    name = member.name or "群友"
    initial = escape(name[:1])
    color = AVATAR_COLORS[sum(map(ord, member.user_id)) % len(AVATAR_COLORS)]
    cols = "".join(f'<div class="col">{_chip(member.cells[g])}</div>' for g, _, _ in GAMES)
    return (
        f'<div class="row{" alt" if index % 2 else ""}"><div class="who">'
        f'<div class="initial" style="background:{color}">{initial}</div>'
        f'<div class="who-text"><div class="name">{escape(name)}</div><div class="qq">({mask_qq(member.user_id)})</div></div>'
        f"</div>{cols}</div>"
    )


def build_html(
    members: List[Member],
    *,
    title: str,
    subtitle: str,
    sign_time: str,
    bot_name: str,
    today: Optional[dt.date] = None,
) -> str:
    today = today or dt.date.today()
    total = sum(1 for m in members for g, _, _ in GAMES if m.cells[g].state != "off")
    ok = sum(1 for m in members for g, _, _ in GAMES if m.cells[g].state == "ok")
    pills = [f'<div class="pill"><div class="dot" style="background:#4f8dff"></div>已绑用户 <b>{len(members)}</b></div>',
             f'<div class="pill"><div class="dot" style="background:#22a06b"></div>成功角色 <b>{ok}/{total}</b></div>']
    for game, label, _ in GAMES:
        game_total = sum(1 for m in members if m.cells[game].state != "off")
        game_ok = sum(1 for m in members if m.cells[game].state == "ok")
        pills.append(f'<div class="pill"><div class="dot" style="background:{GAME_COLOR[game]}"></div>{label} <b>{game_ok}/{game_total}</b></div>')
    head_cols = "".join(
        f'<div class="col-head"><div class="dot" style="background:{GAME_COLOR[g]}"></div>{label}</div>' for g, label, _ in GAMES
    )
    rows = "".join(_row(i, m) for i, m in enumerate(members)) or '<div class="empty">今天还没有需要汇总的账号～</div>'
    avatar = _data_uri("furina.png")
    avatar_html = f'<div class="avatar"><img src="{avatar}"></div>' if avatar else ""
    date_text = f"{today:%Y-%m-%d} 周{WEEK[today.weekday()]}"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body><div class="page">
<div class="hero">
  <div class="bubble" style="width:180px;height:180px;right:-40px;top:-70px"></div>
  <div class="bubble" style="width:90px;height:90px;right:150px;bottom:-50px"></div>
  {avatar_html}
  <div class="hero-text">
    <div class="title-row"><div class="title">{escape(title)}</div><div class="date">{date_text}</div></div>
    <div class="subtitle">{escape(subtitle)}</div>
  </div>
</div>
<div class="pills">{''.join(pills)}</div>
<div class="table">
  <div class="thead"><div class="who">群友 / 账号</div>{head_cols}</div>
  {rows}
</div>
<div class="footer">
  <div class="tips">
    <div>💡 未绑定：@{escape(bot_name)} 绑定账号，米游社扫码一次绑定全部游戏</div>
    <div>🔁 想立即补签：@{escape(bot_name)} 米游社签到　·　📖 全部功能：@{escape(bot_name)} 指令帮助</div>
  </div>
  <div class="brand">Furina Miyoushe Assistant<br>每日 {escape(sign_time)} 自动签到</div>
</div>
</div></body></html>"""


async def render_card(members: List[Member], **kwargs) -> bytes:
    html = build_html(members, **kwargs)
    # max_width 是设备像素：CSS 布局宽 920px，2x 清晰度
    return await render_html_to_bytes(
        html, max_width=CSS_WIDTH * 2, dpi=192, root_max_width=CSS_WIDTH,
        default_font_size=15, allow_refit=True, image_format="png", lang="zh",
    )
