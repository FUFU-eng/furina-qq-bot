"""B 站视频分镜九宫格（融合 dsh-video-storyboard 与 dsh-bili-scroll 2.2）：

1. 直接取 B 站原生故事板雪碧图（/x/player/videoshot + pvdata 大端 uint16 兜底），不下载视频、不调 ffmpeg；
2. 图床请求强制 https 且不带 Cookie，防止 SESSDATA 随 http 图床泄露；
3. 拉普拉斯边缘锐度选帧（避开黑场、转场残影帧）；
4. 在每格左上角直接烧录高对比编号与时间码（#1 00:12 … #9 03:40），拼成单张 3×3 分镜九宫格图（约 1 张图即可看懂全片画面）；
5. 字幕按句号/问号/感叹号边界截断，绝不把句子拦腰截断。
"""
from __future__ import annotations

import asyncio
import io
import math
import re
import struct
from typing import Any

import httpx
from nonebot import logger
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from ...core import images, llm
from . import store
from .client import BiliError, Client, UA

_HTTP_TIMEOUT = 20.0
_TILE_W_DEFAULT = 480
_TILE_H_DEFAULT = 270
_SENTENCE_END = re.compile(r"[。！？!?；;\n]")


def _https(url: str) -> str:
    """把 B 站图片/pvdata 地址统一成 https，避免明文请求。"""
    u = str(url or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://"):
        return "https://" + u[len("http://"):]
    return u


def _image_headers() -> dict[str, str]:
    """图床请求头：只带 UA + Referer，绝不带 Cookie。"""
    return {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}


def _cover_url(pic: str, width: int = 512) -> str:
    base = _https(str(pic or "").split("@", 1)[0])
    return f"{base}@{width}w.jpg" if base else ""


def truncate_at_sentence(text: str, limit: int = 1200) -> str:
    """按句末标点截断字幕/长文本，避免把一句话拦腰切断（参考 dsh-video-storyboard）。"""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= limit:
        return s
    window = s[:limit]
    matches = list(_SENTENCE_END.finditer(window))
    if matches and matches[-1].end() >= int(limit * 0.55):
        return window[: matches[-1].end()].strip() + "…"
    for sep in ("，", ",", " "):
        idx = window.rfind(sep)
        if idx >= int(limit * 0.65):
            return window[:idx].strip() + "…"
    return window.strip() + "…"


def _snapshot_entries(data: dict, duration: float = 0.0) -> list[tuple[float, int]]:
    """把 videoshot 的 index 归一成 [(秒, 块序号), ...]。
    只有最大值超过视频时长 1.5 倍时才按毫秒除 1000，避免 6 小时视频秒数被误判成毫秒。"""
    raws: list[tuple[float, int]] = []
    for i, item in enumerate((data or {}).get("index") or []):
        ts = item.get("timestamp") if isinstance(item, dict) else item
        try:
            raws.append((float(ts), i))
        except (TypeError, ValueError):
            continue
    if not raws:
        return []
    biggest = max(v for v, _ in raws)
    looks_ms = duration > 0 and biggest > duration * 1.5
    scale = 1000.0 if looks_ms else 1.0
    return [(v / scale, i) for v, i in raws]


async def _fetch_bytes(url: str) -> bytes:
    u = _https(url)
    if not u:
        return b""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as hc:
        r = await hc.get(u, headers=_image_headers())
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.content


async def _entries_from_pvdata(data: dict) -> list[tuple[float, int]]:
    """index 为空时的兜底：pvdata 是大端 uint16 秒数数组。"""
    url = _https((data or {}).get("pvdata") or "")
    if not url:
        return []
    try:
        raw = await _fetch_bytes(url)
    except Exception:
        return []
    if not raw or len(raw) % 2:
        return []
    count = len(raw) // 2
    if count > 20000:
        return []
    return [(float(sec), i) for i, sec in enumerate(struct.unpack(f">{count}H", raw))]


def _sharpness_score(tile: Image.Image) -> float:
    """评估单帧清晰度：边缘方差（拉普拉斯近似）× 亮度惩罚（避开纯黑/纯白转场帧）。"""
    gray = tile.convert("L")
    stat = ImageStat.Stat(gray)
    mean_luma = stat.mean[0] if stat.mean else 128.0
    luma_weight = 0.15 if (mean_luma < 18 or mean_luma > 242) else 1.0
    edges = gray.filter(ImageFilter.FIND_EDGES)
    est = ImageStat.Stat(edges)
    variance = est.var[0] if est.var else 0.0
    return float(variance) * luma_weight


def _crop_raw_tile(sheet_img: Image.Image, tile_index: int, tw: int, th: int) -> Image.Image | None:
    cols = max(1, sheet_img.width // tw)
    rows = max(1, sheet_img.height // th)
    if tile_index < 0 or tile_index >= cols * rows:
        return None
    col, row = tile_index % cols, tile_index // cols
    return sheet_img.crop((col * tw, row * th, (col + 1) * tw, (row + 1) * th)).convert("RGB")


def _fmt_sec(sec: float) -> str:
    s = max(0, int(round(sec)))
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def _burn_badge(tile: Image.Image, cell_no: int, sec: float) -> Image.Image:
    """在画面左上角烧录高对比度 `#1 00:15` 编号与时间码（占格高约 13%），防模型数错格子。"""
    out = tile.copy().convert("RGBA")
    w, h = out.size
    badge_h = max(24, int(h * 0.135))
    font_size = max(15, int(badge_h * 0.68))
    font = None
    for fp in ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arialbd.ttf"):
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    label = f"#{cell_no}  {_fmt_sec(sec)}"
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = len(label) * (font_size // 2 + 2), font_size
    pad_x, pad_y = max(8, int(w * 0.02)), max(4, int(h * 0.015))
    box_w = tw + pad_x * 2
    box_h = max(badge_h, th + pad_y * 2)
    margin = max(6, int(min(w, h) * 0.025))
    x0, y0 = margin, margin
    draw.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=max(5, box_h // 4),
                           fill=(12, 16, 24, 215), outline=(255, 215, 80, 235), width=2)
    draw.text((x0 + pad_x, y0 + max(1, (box_h - th) // 2 - 2)), label, font=font, fill=(255, 248, 220, 255))
    return Image.alpha_composite(out, overlay).convert("RGB")


def _assemble_grid(tiles: list[tuple[Image.Image, float]], max_side: int = 1080) -> bytes:
    """把若干帧画面烧录编号后拼成单张九宫格（或 3×2 / 2×2）分镜图。"""
    n = len(tiles)
    if n == 0:
        return b""
    cols = 3 if n >= 5 else (2 if n >= 2 else 1)
    rows = math.ceil(n / cols)
    tw, th = tiles[0][0].size
    gap = 6
    canvas_w = cols * tw + (cols + 1) * gap
    canvas_h = rows * th + (rows + 1) * gap
    canvas = Image.new("RGB", (canvas_w, canvas_h), (18, 20, 26))
    for idx, (tile, sec) in enumerate(tiles, 1):
        badged = _burn_badge(tile, idx, sec)
        c, r = (idx - 1) % cols, (idx - 1) // cols
        x = gap + c * (tw + gap)
        y = gap + r * (th + gap)
        canvas.paste(badged, (x, y))
    if max(canvas.size) > max_side:
        ratio = max_side / max(canvas.size)
        canvas = canvas.resize((max(1, int(canvas.width * ratio)), max(1, int(canvas.height * ratio))), Image.LANCZOS)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=84)
    return buf.getvalue()


async def build_storyboard_sheet(
    cli: Client,
    bvid: str,
    *,
    cid: int = 0,
    duration: float = 0.0,
    pic: str = "",
    count: int = 9,
    at: list[float] | None = None,
) -> dict[str, Any]:
    """生成视频的 3×3 编号分镜九宫格（若视频无快照则自动退回封面图）。
    返回 {"ok": bool, "sheet_bytes": bytes, "timestamps": list[float], "is_cover": bool, "covered_sec": float}。"""
    if not cid or not duration or not pic:
        try:
            info = await cli.view(bvid)
            pages = info.get("pages") or []
            cid = cid or int(info.get("cid") or ((pages or [{}])[0].get("cid") or 0))
            duration = duration or float(info.get("duration") or 0)
            if pages and pages[0].get("duration"):
                covered = float(pages[0].get("duration") or duration)
            else:
                covered = duration
            pic = pic or str(info.get("pic") or "")
        except Exception as e:
            return {"ok": False, "error": f"取视频信息失败：{e}", "sheet_bytes": b"", "timestamps": [], "is_cover": False}
    else:
        covered = duration

    snap: dict = {}
    entries: list[tuple[float, int]] = []
    for _ in range(2):
        try:
            snap = await cli.videoshot(bvid, cid)
        except Exception:
            snap = {}
        entries = _snapshot_entries(snap, covered)
        if not entries and snap:
            entries = await _entries_from_pvdata(snap)
        if entries:
            break

    urls = [_https(u) for u in (snap.get("image") or []) if u]
    if entries and urls:
        x_len = max(1, int(snap.get("img_x_len") or 10))
        y_len = max(1, int(snap.get("img_y_len") or 10))
        tw = max(1, int(snap.get("img_x_size") or _TILE_W_DEFAULT))
        th = max(1, int(snap.get("img_y_size") or _TILE_H_DEFAULT))
        per_sheet = x_len * y_len

        span = covered if covered > 0 else max((t for t, _ in entries), default=0.0)
        if at:
            targets = [max(0.0, float(t)) for t in at[:9]]
        else:
            k = max(1, min(9, int(count or 9)))
            targets = [span * (i + 1) / (k + 1) for i in range(k)] if span > 0 else [0.0]

        chosen_entry_indices: list[int] = []
        used_global_idx: set[int] = set()
        for t in targets:
            best_pos = min(range(len(entries)), key=lambda pos: abs(entries[pos][0] - t))
            chosen_entry_indices.append(best_pos)

        needed_sheets: set[int] = set()
        for pos in chosen_entry_indices:
            _, gidx = entries[pos]
            needed_sheets.add(gidx // per_sheet)

        sheets_img: dict[int, Image.Image] = {}
        for sidx in sorted(needed_sheets):
            if 0 <= sidx < len(urls):
                try:
                    raw = await _fetch_bytes(urls[sidx])
                    sheets_img[sidx] = await asyncio.to_thread(lambda b=raw: Image.open(io.BytesIO(b)).convert("RGB"))
                except Exception as e:
                    logger.debug(f"[分镜] 下载拼图 #{sidx} 失败: {e}")

        def _pick_tiles() -> tuple[list[tuple[Image.Image, float]], list[float]]:
            picked: list[tuple[Image.Image, float]] = []
            ts_list: list[float] = []
            for pos in chosen_entry_indices:
                candidates = [p for p in (pos, pos - 1, pos + 1) if 0 <= p < len(entries)]
                best_tile = None
                best_sec = entries[pos][0]
                best_gidx = entries[pos][1]
                best_score = -1.0
                for p in candidates:
                    sec_p, gidx_p = entries[p]
                    if gidx_p in used_global_idx and len(entries) > len(chosen_entry_indices):
                        continue
                    sidx = gidx_p // per_sheet
                    sheet = sheets_img.get(sidx)
                    if sheet is None:
                        continue
                    tile = _crop_raw_tile(sheet, gidx_p % per_sheet, tw, th)
                    if tile is None:
                        continue
                    score = _sharpness_score(tile)
                    if p == pos:
                        score *= 1.12
                    if score > best_score:
                        best_score = score
                        best_tile = tile
                        best_sec = round(sec_p, 1)
                        best_gidx = gidx_p
                if best_tile is not None:
                    used_global_idx.add(best_gidx)
                    picked.append((best_tile, best_sec))
                    ts_list.append(best_sec)
            return picked, ts_list

        picked_tiles, ts_out = await asyncio.to_thread(_pick_tiles)
        if picked_tiles:
            sheet_bytes = await asyncio.to_thread(_assemble_grid, picked_tiles)
            if sheet_bytes:
                return {
                    "ok": True,
                    "sheet_bytes": sheet_bytes,
                    "timestamps": ts_out,
                    "is_cover": False,
                    "covered_sec": round(span, 1),
                }

    if pic:
        try:
            raw_cover = await _fetch_bytes(_cover_url(pic, 640))
            if raw_cover and images.is_valid_image(raw_cover):
                return {
                    "ok": True,
                    "sheet_bytes": raw_cover,
                    "timestamps": [],
                    "is_cover": True,
                    "covered_sec": round(covered, 1),
                }
        except Exception as e:
            logger.debug(f"[分镜] 下载封面兜底失败: {e}")

    return {"ok": False, "error": "无法获取视频分镜或封面", "sheet_bytes": b"", "timestamps": [], "is_cover": False}


async def describe_storyboard(
    sheet_bytes: bytes,
    title: str,
    *,
    up: str = "",
    duration: int = 0,
    timestamps: list[float] | None = None,
    is_cover: bool = False,
) -> str:
    """用看图模型把 3×3 编号分镜九宫格（或封面）转成精炼的画面描述。"""
    if not sheet_bytes:
        return ""
    data_url = await images.encode_data_url_async(sheet_bytes, max_side=1024)
    if not data_url:
        return ""
    if is_cover:
        prompt = (
            f"这是B站视频《{title}》（UP主：{up or '未知'}）的封面图。"
            "请用两三句话描述封面上的人物、画面内容、文字标题和整体氛围（100字以内，直接输出描述）："
        )
        return await llm.describe_image(data_url, max_len=140, prompt=prompt)
    ts_str = "、".join(f"#{i + 1}({_fmt_sec(t)})" for i, t in enumerate(timestamps or []))
    prompt = (
        f"这是B站视频《{title}》（UP主：{up or '未知'}，时长 {duration // 60}:{duration % 60:02d}）的 3×3 视频分镜九宫格，"
        f"每格左上角标了编号与时间码（{ts_str}）。"
        "请按 #1 到最后一格的时间顺序，简明描述视频画面里拍了什么（人物动作、场景变化、画面上的关键字幕或梗），"
        "220字以内，直接输出画面内容描述："
    )
    return await llm.describe_image(data_url, max_len=280, prompt=prompt)


BILI_MINIAPP_ICON = "https://miniapp.gtimg.cn/public/appicon/51f90239b78a2e4994c11215f4c4ba15_200.jpg"
_CARD_TITLE_RE = re.compile(r"(?:哔哩哔哩|卡片)[\s\]】]*[（(]([^）)\n]{3,80})[）)]|《([^》\n]{3,80})》")
_BILI_URL_STRIP_RE = re.compile(
    r"https?://(?:(?:www\.|m\.)?bilibili\.com/video/[0-9A-Za-z_\-/?=&]+|b23\.tv/[0-9A-Za-z_\-/?=&]+|bili2233\.cn/[0-9A-Za-z_\-/?=&]+)",
    re.I,
)


def _extract_search_query(raw_target: str) -> str:
    s = str(raw_target or "").strip()
    if not s:
        return ""
    m = _CARD_TITLE_RE.search(s)
    if m:
        return (m.group(1) or m.group(2) or "").strip()
    s = re.sub(r"^\[卡片\]\s*(?:哔哩哔哩)?\s*", "", s).strip()
    return s[:60]


async def watch_video_detail(
    raw_target: str,
    *,
    at_seconds: list[float] | None = None,
    with_vision: bool = True,
    record_watched: bool = True,
) -> dict[str, Any]:
    """完整看一条 B 站视频：解析 BV / 短链（或按小程序卡片标题搜） → 拉取详情、句边界字幕、弹幕、热评 + 3×3 分镜九宫格视觉描述。"""
    async with Client() as cli:
        await cli.ensure_buvid()
        bvid = await cli.resolve_bvid(raw_target)
        if not bvid:
            q = _extract_search_query(raw_target)
            if q and len(q) >= 2:
                try:
                    found = await cli.search(q)
                    if found and found[0].get("bvid"):
                        bvid = str(found[0]["bvid"])
                except Exception:
                    bvid = ""
        if not bvid:
            return {"ok": False, "reason": "没找到有效的 BV 号或 B 站视频链接（可以发 BV 号、bilibili 链接或 b23.tv 短链）"}
        try:
            info = await cli.view(bvid)
        except BiliError as e:
            return {"ok": False, "bvid": bvid, "reason": f"读取视频失败：{e}"}
        aid = int(info.get("aid") or 0)
        pages = info.get("pages") or []
        cid = int(info.get("cid") or ((pages or [{}])[0].get("cid") or 0))
        title = str(info.get("title") or bvid)
        up = str((info.get("owner") or {}).get("name") or "")
        desc = truncate_at_sentence(str(info.get("desc") or "").strip(), 240)
        duration = int(info.get("duration") or 0)
        pic = str(info.get("pic") or "")
        stat = info.get("stat") or {}

        sub_task = cli.subtitle_text(bvid, cid, max_chars=1600) if cid else asyncio.sleep(0, result="")
        dm_task = cli.danmaku(cid, n=30) if cid else asyncio.sleep(0, result=[])
        cm_task = cli.hot_comments(aid, n=12) if aid else asyncio.sleep(0, result=[])
        sb_task = (
            build_storyboard_sheet(cli, bvid, cid=cid, duration=duration, pic=pic, count=9, at=at_seconds)
            if with_vision else asyncio.sleep(0, result={"ok": False, "sheet_bytes": b""})
        )
        subtitle_raw, danmaku, comments, sb = await asyncio.gather(sub_task, dm_task, cm_task, sb_task, return_exceptions=True)
        if isinstance(subtitle_raw, Exception):
            subtitle_raw = ""
        if isinstance(danmaku, Exception):
            danmaku = []
        if isinstance(comments, Exception):
            comments = []
        if isinstance(sb, Exception) or not isinstance(sb, dict):
            sb = {"ok": False, "sheet_bytes": b"", "timestamps": [], "is_cover": False}

        subtitle = truncate_at_sentence(str(subtitle_raw or ""), 1200)
        frames_desc = ""
        sheet_bytes = sb.get("sheet_bytes") or b""
        if with_vision and sheet_bytes:
            frames_desc = await describe_storyboard(
                sheet_bytes,
                title,
                up=up,
                duration=duration,
                timestamps=sb.get("timestamps") or [],
                is_cover=bool(sb.get("is_cover")),
            )

        if record_watched:
            summary_thought = f"看了《{title[:24]}》"
            if frames_desc:
                summary_thought = truncate_at_sentence(frames_desc, 55)
            elif subtitle:
                summary_thought = truncate_at_sentence(subtitle, 55)
            store.add_watched([{
                "bvid": bvid,
                "aid": aid,
                "cid": cid,
                "title": title,
                "up": up,
                "tname": str(info.get("tname") or ""),
                "pic": pic,
                "duration": duration,
                "source": "聊天分享",
                "interest": 7,
                "thought": summary_thought,
                "acts": ["watch"],
            }])

        return {
            "ok": True,
            "bvid": bvid,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "title": title,
            "up": up,
            "pic": _https(pic) or BILI_MINIAPP_ICON,
            "duration": f"{duration // 60}:{duration % 60:02d}",
            "duration_sec": duration,
            "zone": str(info.get("tname") or ""),
            "stat": {
                "view": int(stat.get("view") or 0),
                "like": int(stat.get("like") or 0),
                "coin": int(stat.get("coin") or 0),
                "favorite": int(stat.get("favorite") or 0),
                "danmaku": int(stat.get("danmaku") or 0),
                "reply": int(stat.get("reply") or 0),
            },
            "desc": desc,
            "subtitle": subtitle or "（该视频无字幕，请结合下方 3×3 分镜画面描述与弹幕热评了解内容）",
            "storyboard_scenes": frames_desc or "（暂无画面分镜描述）",
            "storyboard_timestamps": [_fmt_sec(t) for t in (sb.get("timestamps") or [])],
            "storyboard_is_cover": bool(sb.get("is_cover")),
            "danmaku": list(danmaku or [])[:25],
            "hot_comments": [
                f"{c.get('user', '网友')}(赞{c.get('like', 0)})：{c.get('text') or c.get('message') or ''}"
                for c in (comments or [])[:10]
            ],
            "_sheet_bytes": sheet_bytes,
        }


# ---------------- QQ 小程序哔哩哔哩卡片 & 分气泡短评转发 ----------------
async def resolve_video_card_info(raw_target: str = "") -> dict[str, Any]:
    """解析要分享的 B 站视频信息（支持 BV 号 / 链接 / 搜索关键词 / 留空自动挑最近刷到的高分视频）。"""
    from ...core import db

    watched_all = list(db.get(store.NS, "watched") or [])
    raw = str(raw_target or "").strip()

    # 1. 留空：直接挑最近刷到的高分视频
    if not raw:
        recent = store.recent_watched(hours=72, limit=5, min_interest=4)
        pick = recent[-1] if recent else (watched_all[-1] if watched_all else None)
        if not pick or not pick.get("bvid"):
            return {"ok": False, "reason": "还没有最近刷到的 B 站视频记录，可以指定 BV 号、链接或搜索关键词"}
        raw = str(pick["bvid"])

    async with Client() as cli:
        await cli.ensure_buvid()
        bvid = await cli.resolve_bvid(raw)
        cached = next((w for w in reversed(watched_all) if w.get("bvid") == bvid), None) if bvid else None

        # 2. 不是现成 BV/链接：先在自己看过的视频里按标题/UP主/感想找，找不到就去 B 站搜
        if not bvid:
            q = _extract_search_query(raw)
            q_low = q.lower()
            for w in reversed(watched_all):
                hay = f"{w.get('title', '')} {w.get('up', '')} {w.get('thought', '')} {w.get('tags', '')}".lower()
                if q_low and q_low in hay and w.get("bvid"):
                    bvid = str(w["bvid"])
                    cached = w
                    break
            if not bvid and q:
                try:
                    found = await cli.search(q)
                except Exception as e:
                    return {"ok": False, "reason": f"搜索 B 站视频失败：{e}"}
                if found and found[0].get("bvid"):
                    bvid = str(found[0]["bvid"])
                    cached = found[0]

        if not bvid:
            return {"ok": False, "reason": f"找不到对应的 B 站视频：{raw[:40]}"}

        title = str((cached or {}).get("title") or "").strip()
        up = str((cached or {}).get("up") or "").strip()
        pic = _https(str((cached or {}).get("pic") or ""))
        desc = str((cached or {}).get("desc") or "").strip()
        thought = str((cached or {}).get("thought") or "").strip()

        if not title or not pic:
            try:
                info = await cli.view(bvid)
                title = str(info.get("title") or title or bvid).strip()
                up = str((info.get("owner") or {}).get("name") or up).strip()
                pic = _https(str(info.get("pic") or pic))
                desc = truncate_at_sentence(str(info.get("desc") or desc).strip(), 120)
            except Exception as e:
                if not title:
                    return {"ok": False, "bvid": bvid, "reason": f"获取视频详情失败：{e}"}

        url = f"https://www.bilibili.com/video/{bvid}"
        return {
            "ok": True,
            "bvid": bvid,
            "url": url,
            "title": title or bvid,
            "desc": desc or (f"UP主：{up}" if up else (title or bvid)),
            "up": up,
            "pic": pic or BILI_MINIAPP_ICON,
            "thought": thought,
        }


def _extract_ark_dict(res: Any) -> dict | None:
    import json as _json

    if isinstance(res, dict) and "app" not in res and "data" in res:
        res = res["data"]
    if isinstance(res, str):
        s = res.strip()
        if not s.startswith("{"):
            return None
        try:
            res = _json.loads(s)
        except ValueError:
            return None
    if isinstance(res, dict) and isinstance(res.get("app"), str) and ("meta" in res or "prompt" in res):
        return res
    return None


async def build_miniapp_ark(bot, info: dict[str, Any]) -> str | None:
    """调用 NapCat 的 get_mini_app_ark 接口签名生成真正的「QQ小程序 哔哩哔哩卡片」JSON 字符串。"""
    import inspect
    import json as _json

    call_api = getattr(bot, "call_api", None)
    if call_api is None:
        return None
    bvid = str(info.get("bvid") or "").strip()
    title = str(info.get("title") or bvid or "哔哩哔哩视频").strip()
    up = str(info.get("up") or "").strip()
    desc = str(info.get("desc") or (f"UP主：{up}" if up else title)).strip()
    pic = _https(str(info.get("pic") or "")) or BILI_MINIAPP_ICON
    url = str(info.get("url") or (f"https://www.bilibili.com/video/{bvid}" if bvid else "https://www.bilibili.com")).strip()
    try:
        maybe_coro = call_api(
            "get_mini_app_ark",
            type="bili",
            title="哔哩哔哩",
            desc=title,
            picUrl=pic,
            jumpUrl=url,
            webUrl=url,
        )
        if not inspect.isawaitable(maybe_coro):
            return None
        res = await maybe_coro
        ark_obj = _extract_ark_dict(res)
        if ark_obj is not None:
            d1 = ((ark_obj.get("meta") or {}).get("detail_1")) or {}
            # 若底层将 req.title 映射到了 detail_1.desc（导致卡片大标题变成“哔哩哔哩”），立即用视频标题重签一次
            if isinstance(d1, dict) and d1.get("desc") == "哔哩哔哩" and title != "哔哩哔哩":
                res2 = await call_api(
                    "get_mini_app_ark",
                    type="bili",
                    title=title,
                    desc=desc,
                    picUrl=pic,
                    jumpUrl=url,
                    webUrl=url,
                )
                ark_obj2 = _extract_ark_dict(res2)
                if ark_obj2 is not None:
                    ark_obj = ark_obj2
            return _json.dumps(ark_obj, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"[B站小程序卡片] get_mini_app_ark 签名未成功，回退普通链接：{e}")
    return None


def split_share_bubbles(comments: str | list[str] | None) -> list[str]:
    """把转发视频时的评语拆成 1~3 条短促自然的气泡（像真人先甩小程序卡片，再跟「这个好诶」「@主人 …」）。"""
    if not comments:
        return []
    raw_items = list(comments) if isinstance(comments, (list, tuple)) else [str(comments)]
    pieces: list[str] = []
    for item in raw_items:
        s = _BILI_URL_STRIP_RE.sub("", str(item or "")).strip()
        if not s:
            continue
        for part in re.split(r"[\r\n|｜]+", s):
            part = part.strip()
            if not part:
                continue
            # 如果中间带了 @某人（如「这个好诶 @主人 能不能给我加一个」），把 @某人 拆成独立跟评气泡
            at_m = re.search(r"\s+(@\S+.*)$", part)
            if at_m and at_m.start() >= 2:
                head = part[: at_m.start()].strip().rstrip("，,。；;")
                tail = at_m.group(1).strip()
                if head:
                    pieces.append(head)
                if tail:
                    pieces.append(tail)
                continue
            # 单条较长且中间有标点停顿：拆成两条短句气泡
            if len(part) > 14:
                m = re.search(r"[。！？!?～~；;，,]", part[3:-2])
                if m:
                    cut = 3 + m.end()
                    p1 = part[:cut].strip().rstrip("，,。；;")
                    p2 = part[cut:].strip().rstrip("。")
                    if p1 and p2:
                        pieces.append(p1)
                        pieces.append(p2)
                        continue
            pieces.append(part.rstrip("。"))
    out: list[str] = []
    for p in pieces:
        c = p.strip()
        if c and c not in out:
            out.append(c)
    return out[:3]


_OWNER_AT_RE = re.compile(r"^@(?:主人|群主|我超爱芙啦|沃超爱芙啦|主人)\s*(.*)$")


async def send_bili_card_and_bubbles(
    bot,
    sender,
    info: dict[str, Any],
    comments: str | list[str] | None = "",
    *,
    owner_qq: str = "",
    at_uid: str = "",
    pause_fn=None,
) -> dict[str, Any]:
    """像真人一样分享 B 站视频：
    第 1 条气泡：发送签名的「QQ小程序 哔哩哔哩卡片」（MessageSegment.json）；
    第 2~N 条气泡：停顿一下，分气泡发送短评（如「这个好诶」「@主人 …」）。
    若当前协议端不支持小程序卡片签名，则平滑退回链接消息。"""
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    bvid = str(info.get("bvid") or "").strip()
    title = str(info.get("title") or bvid).strip()
    url = str(info.get("url") or f"https://www.bilibili.com/video/{bvid}").strip()
    bubbles = split_share_bubbles(comments)
    ark_json = await build_miniapp_ark(bot, info)

    async def _wait(text_hint: str) -> None:
        if pause_fn is not None:
            try:
                await pause_fn(text_hint)
                return
            except Exception:
                pass
        await asyncio.sleep(0.8)

    def _build_bubble_msg(b: str, is_last: bool) -> Message:
        m_owner = _OWNER_AT_RE.match(b)
        target_qq = ""
        rest = b
        if m_owner and owner_qq and str(owner_qq).isdigit():
            target_qq = str(owner_qq)
            rest = m_owner.group(1).strip()
        elif b.startswith("@") and at_uid and str(at_uid).isdigit():
            rest = re.sub(r"^@\S+\s*", "", b).strip()
            target_qq = str(at_uid)
        elif is_last and at_uid and str(at_uid).isdigit() and not any(x.startswith("@") for x in bubbles):
            target_qq = str(at_uid)
        if target_qq:
            msg = Message([MessageSegment.at(int(target_qq))])
            if rest:
                msg += MessageSegment.text(f" {rest}")
            return msg
        return Message(rest)

    if ark_json:
        sent_card = await sender(Message(MessageSegment.json(data=ark_json)))
        if sent_card is None:
            return {"ok": False, "sent": 0, "card": False, "bvid": bvid, "title": title}
        sent_count = 1
        for idx, b in enumerate(bubbles):
            await _wait(b)
            res = await sender(_build_bubble_msg(b, idx == len(bubbles) - 1))
            if res is not None:
                sent_count += 1
        return {
            "ok": True,
            "sent": sent_count,
            "card": True,
            "bvid": bvid,
            "title": title,
            "url": url,
            "bubbles": bubbles,
        }

    # 兜底：协议端未开启 PacketBackend 或单测环境未 mock get_mini_app_ark
    if bubbles:
        first_line = f"{bubbles[0]}\n{url}"
        res0 = await sender(Message(first_line))
        if res0 is None:
            return {"ok": False, "sent": 0, "card": False, "bvid": bvid, "title": title}
        sent_count = 1
        for idx, b in enumerate(bubbles[1:], 1):
            await _wait(b)
            res = await sender(_build_bubble_msg(b, idx == len(bubbles) - 1))
            if res is not None:
                sent_count += 1
        return {
            "ok": True,
            "sent": sent_count,
            "card": False,
            "bvid": bvid,
            "title": title,
            "url": url,
            "bubbles": bubbles,
        }

    res0 = await sender(Message(f"《{title}》\n{url}"))
    if res0 is None:
        return {"ok": False, "sent": 0, "card": False, "bvid": bvid, "title": title}
    return {"ok": True, "sent": 1, "card": False, "bvid": bvid, "title": title, "url": url, "bubbles": []}

