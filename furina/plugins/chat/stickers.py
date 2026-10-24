"""机器人自己的 QQ 表情包收藏：拉取、缓存、按语境挑选并发送。"""

import re
import time

from nonebot import logger

from . import config

_cache: dict = {"ts": 0.0, "urls": []}

# 模型在回复里输出 <<STICKER:n>> 表示想发第 n 张收藏表情（从 1 开始）。
# qwen 偶尔会把尖括号方向写反（>>STICKER:14<<），两种方向都要兼容。
_MARKER_RE = re.compile(r"[<>]{1,2}\s*STICKER:\s*(\d+)\s*[<>]{1,2}", re.IGNORECASE)


async def fetch_sticker_urls(bot) -> list[str]:
    """拉取机器人 QQ 号的表情包收藏，返回去重后的表情 URL 列表（带缓存）。"""
    now = time.time()
    if _cache["urls"] and now - _cache["ts"] < config.STICKER_TTL:
        return list(_cache["urls"])
    # 上次没拉到（接口不支持/收藏为空）时 60 秒内不再重试，避免每条回复都白调两个接口
    if not _cache["urls"] and now < _cache.get("empty_until", 0):
        return []

    urls: list[str] = []
    # 先试详情接口（返回对象列表，字段更全），不行再退回 URL 列表接口
    try:
        detail = await bot.call_api("fetch_custom_face_detail", count=config.STICKER_MAX_COUNT)
        if isinstance(detail, list):
            for item in detail:
                if isinstance(item, str):
                    if item.startswith("http"):
                        urls.append(item)
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("emojiUrl") or item.get("emoji_url") or ""
                    if isinstance(u, str) and u.startswith("http"):
                        urls.append(u)
    except Exception as e:
        logger.warning(f"[AI聊天] 拉取表情收藏详情失败: {e}")

    if not urls:
        try:
            urls = await bot.call_api("fetch_custom_face", count=config.STICKER_MAX_COUNT) or []
            urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
        except Exception as e:
            logger.warning(f"[AI聊天] 拉取表情收藏失败: {e}")

    # 去重
    seen: set[str] = set()
    clean: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            clean.append(u)
    _cache.update({"ts": now, "urls": clean, "empty_until": 0 if clean else now + 60})
    if clean:
        logger.info(f"[AI聊天] 表情包收藏已缓存 {len(clean)} 张")
    return list(clean)


def extract_sticker_marker(text: str) -> tuple[str, int | None]:
    """解析回复文本里的 <<STICKER:n>> 标记。返回 (去掉标记的正文, 表情序号或None)。"""
    matches = list(_MARKER_RE.finditer(text or ""))
    if not matches:
        return text, None
    idx = int(matches[-1].group(1)) - 1
    text = _MARKER_RE.sub("", text or "").strip()
    # 模型常写成"...送你一张图片<<STICKER:3>>"，剥掉标记后把残留在末尾的"图片/表情"也清掉
    for w in ("图片", "表情包", "表情", "一张"):
        if text.endswith(w):
            text = text[: -len(w)].rstrip(" ，,。~～、")
            break
    return text, idx
