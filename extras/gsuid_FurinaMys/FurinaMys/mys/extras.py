"""对上游输出的小幅加工 + 攻略兜底。

* 原神每日卡：上游「小组件」数据源拿不到「周本减半 / 参量质变 / 魔神任务」，会画成三行「未知」，
  这里把这三行裁掉，下面的派遣等内容整体上移。
* 攻略兜底：上游攻略图库还没收录的新角色（例如刚上线的角色），去米游社对应版区搜
  「角色名 一图流」攻略帖，取帖子里的攻略长图发出来，并注明作者与来源。
"""
from __future__ import annotations

import re
import time
import base64
import asyncio
from io import BytesIO
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote

from PIL import Image

from gsuid_core.logger import logger
from gsuid_core.models import Message
from gsuid_core.segment import MessageSegment

from .bridge import Output, find_module

# ---------------------------------------------------------------- 原神每日卡裁剪
RESIN_SIZE = (700, 1300)
RESIN_CUT = (798, 1032)      # 第 3~5 行（周本减半 / 参量质变 / 魔神任务）所在区域
RESIN_SIDE = (36, 664)       # 中间卡纸左右边界；两侧装饰取原底图对应位置，避免出现断层


def _seg_bytes(seg: Message) -> Optional[bytes]:
    data = seg.data
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(data, str) and data.startswith("base64://"):
        try:
            return base64.b64decode(data[len("base64://"):])
        except Exception:
            return None
    return None


def resin_uses_widget(uid: str) -> bool:
    """上游这张卡此时用的是不是「小组件」数据源（只有它会出现三行未知）。"""
    mod = find_module("gs", "draw_resin_card")
    use_widget = bool(getattr(mod, "use_widget", False)) if mod is not None else False
    return use_widget and uid[:1].isdigit() and int(uid[0]) <= 5


def _bg_image() -> Optional[Image.Image]:
    mod = find_module("gs", "draw_resin_card")
    path = getattr(mod, "TEXT_PATH", None) if mod is not None else None
    try:
        return Image.open(path / "bg.png").convert("RGBA") if path is not None else None
    except Exception:
        return None


def crop_resin_bytes(raw: bytes) -> Optional[bytes]:
    try:
        img = Image.open(BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
    if img.size != RESIN_SIZE:
        return None
    w, h = img.size
    a, b = RESIN_CUT
    out = Image.new("RGBA", (w, h - (b - a)))
    out.paste(img.crop((0, 0, w, a)), (0, 0))
    out.paste(img.crop((0, b, w, h)), (0, a))
    bg = _bg_image()
    if bg is not None and bg.size == RESIN_SIZE:
        # 左右两侧的桌面装饰：下半段换成原底图同样高度的底部，接缝放在卡纸外不显眼的位置
        y0 = a - 150
        left, right = RESIN_SIDE
        out.paste(bg.crop((0, y0 + (b - a), left, h)), (0, y0))
        out.paste(bg.crop((right, y0 + (b - a), w, h)), (right, y0))
    buf = BytesIO()
    out.convert("RGB").save(buf, format="JPEG", quality=92, subsampling=0)
    return buf.getvalue()


def trim_resin_card(out: Output, uid: Optional[str]) -> Output:
    if not uid or not out.has_image or not resin_uses_widget(uid):
        return out
    segs: List[Message] = []
    for seg in out.segments:
        if seg.type == "image":
            raw = _seg_bytes(seg)
            new = crop_resin_bytes(raw) if raw else None
            segs.append(MessageSegment.image(new) if new else seg)
        else:
            segs.append(seg)
    return Output(segments=segs, error=out.error)


# ---------------------------------------------------------------- 米游社攻略兜底
GIDS = {"bbb": 1, "gs": 2, "sr": 6, "zzz": 8}
SEARCH_API = "https://bbs-api.miyoushe.com/post/wapi/searchPosts?gids={gid}&size=20&keyword={kw}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36",
    "Referer": "https://www.miyoushe.com/",
}
GUIDE_WORDS = ("攻略", "一图流", "养成", "配队", "解析")
SKIP_WORDS = ("信赖", "邀约", "偶遇", "抽不抽", "抽取建议", "要抽", "cv", "CV", "档案", "影像", "会员册", "结婚")
MAX_WIDTH = 1080
_cache: Dict[Tuple[str, str], Tuple[float, Optional[Tuple[bytes, str]]]] = {}
CACHE_SECONDS = 6 * 3600


async def _get_json(url: str) -> Optional[dict]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            data = resp.json()
            return data if isinstance(data, dict) and data.get("retcode") == 0 else None
    except Exception as exc:
        logger.warning(f"[FurinaMys] 米游社攻略搜索失败: {exc}")
        return None


async def _get_bytes(url: str) -> Optional[bytes]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=40, headers=HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                return resp.content
    except Exception as exc:
        logger.warning(f"[FurinaMys] 攻略图下载失败: {exc}")
    return None


def _shrink(raw: bytes) -> Optional[bytes]:
    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception:
        return None
    img = img.convert("RGB")
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def clean_name(name: str) -> str:
    """上游名字库里新角色常带测试服前缀，例如「(Test1)菲欧妮」，搜米游社前去掉。"""
    out = re.sub(r"^\s*[\(（\[【][^)）\]】]{0,12}[\)）\]】]\s*", "", str(name or "")).strip()
    return out or str(name or "").strip()


def _pick_image(images: List[dict]) -> Optional[str]:
    """帖子里第一张「竖长」的大图通常就是攻略长图（第一张多半是横版封面）。"""
    best = None
    for item in images[:6]:
        w, h = int(item.get("width") or 0), int(item.get("height") or 0)
        url = item.get("url")
        if not url or w < 600:
            continue
        if h >= w * 1.3:
            return url
        if best is None and h >= w:
            best = url
    return best


def _pick_strips(images: List[dict]) -> List[str]:
    """有的一图流被切成好几条同宽的横条依次贴在帖子里：找出最长的一段同宽连续横条（至少 3 条）。"""
    best: List[str] = []
    run: List[str] = []
    run_w = 0
    total = 0
    for item in images[:30]:
        w, h = int(item.get("width") or 0), int(item.get("height") or 0)
        url = item.get("url")
        if url and w >= 600 and h > 0 and w == run_w and total + h * MAX_WIDTH / w <= 30000:
            run.append(url)
            total += h * MAX_WIDTH / w
        else:
            run = [url] if url and w >= 600 and h > 0 else []
            run_w = w if run else 0
            total = h * MAX_WIDTH / w if run else 0
        if len(run) > len(best):
            best = list(run)
    return best if len(best) >= 3 else []


def _stitch(raws: List[bytes]) -> Optional[bytes]:
    parts = []
    for raw in raws:
        try:
            img = Image.open(BytesIO(raw))
            img.load()
        except Exception:
            return None
        img = img.convert("RGB")
        if img.width != MAX_WIDTH:
            img = img.resize((MAX_WIDTH, max(1, round(img.height * MAX_WIDTH / img.width))), Image.LANCZOS)
        parts.append(img)
    if not parts:
        return None
    canvas = Image.new("RGB", (MAX_WIDTH, sum(p.height for p in parts)), "white")
    y = 0
    for part in parts:
        canvas.paste(part, (0, y))
        y += part.height
    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _score(post: dict, name: str) -> int:
    subject = str((post.get("post") or {}).get("subject") or "")
    if name not in subject or any(w in subject for w in SKIP_WORDS):
        return -1
    score = sum(2 for w in GUIDE_WORDS if w in subject)
    if "一图流" in subject:
        score += 4
    # 帖子主角就是 TA（「菲欧妮」、开头就是名字），而不是「某某养成｜vs 菲欧妮」这种顺带提到
    head = re.sub(r"^\s*(?:【[^】]*】|\[[^\]]*\])\s*", "", subject)
    if f"「{name}」" in subject or head.startswith(name) or 0 <= head.find(name) <= 6:
        score += 3
    return score


async def _post_image(post: dict, strips: bool) -> Optional[bytes]:
    images = post.get("image_list") or []
    if not strips:
        url = _pick_image(images)
        raw = await _get_bytes(url) if url else None
        return _shrink(raw) if raw else None
    urls = _pick_strips(images)
    if not urls:
        return None
    raws = await asyncio.gather(*(_get_bytes(u) for u in urls))
    if not all(raws):
        return None
    return _stitch(list(raws))


async def mys_guide(game: str, name: str) -> Optional[Tuple[bytes, str]]:
    """返回 (攻略图, 来源说明)；找不到返回 None。结果缓存 6 小时。"""
    name = clean_name(name)
    key = (game, name)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    result: Optional[Tuple[bytes, str]] = None
    seen = set()
    candidates = []
    for kw in (f"{name} 一图流", f"{name} 攻略"):
        data = await _get_json(SEARCH_API.format(gid=GIDS[game], kw=quote(kw)))
        for post in ((data or {}).get("data") or {}).get("posts") or []:
            pid = str((post.get("post") or {}).get("post_id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            s = _score(post, name)
            if s > 0:
                candidates.append((s, post))
        if any(s >= 9 for s, _ in candidates):
            break
        await asyncio.sleep(0.5)
    candidates.sort(key=lambda x: -x[0])
    logger.info(f"[FurinaMys] 米游社攻略兜底「{name}」候选 {len(candidates)} 个")
    # 先找整张长图；都没有再试「切成几条横条」的帖子，拼起来发
    for strips in (False, True):
        for _, post in candidates[:5]:
            img = await _post_image(post, strips)
            if not img:
                continue
            author = str((post.get("user") or {}).get("nickname") or "米游社作者")
            subject = re.sub(r"\s+", " ", str((post.get("post") or {}).get("subject") or ""))[:40]
            result = (img, f"📖 {name} 攻略（来自米游社 @{author}《{subject}》，帖子 ID {post['post']['post_id']}）")
            break
        if result:
            break
    _cache[key] = (time.time() + (CACHE_SECONDS if result else 600), result)
    return result
