"""从群消息里挑出要收的图：普通图片 + 合并转发里的图（递归），跳过表情包。"""
from __future__ import annotations

import asyncio
import os

import httpx
from nonebot import logger

MAX_PER_MESSAGE = 60
MAX_DEPTH = 3


def _seg_list(msg) -> list[tuple[str, dict]]:
    """统一成 [(type, data)]：兼容 Message 对象、segment 字典列表、CQ 字符串（字符串直接忽略）。"""
    out = []
    if msg is None or isinstance(msg, str):
        return out
    for seg in msg:
        if isinstance(seg, dict):
            out.append((str(seg.get("type") or ""), dict(seg.get("data") or {})))
        else:
            out.append((str(getattr(seg, "type", "") or ""), dict(getattr(seg, "data", {}) or {})))
    return out


def is_sticker_seg(data: dict) -> bool:
    if str(data.get("sub_type", "0")) == "1":
        return True
    summary = str(data.get("summary", "") or "")
    return "表情" in summary


async def gather_images(bot, message, sender_name: str = "", depth: int = 0, out: list | None = None) -> list[dict]:
    """返回 [{"file", "url", "name", "forward"}]，表情包已排除。"""
    out = [] if out is None else out
    for stype, data in _seg_list(message):
        if len(out) >= MAX_PER_MESSAGE:
            break
        if stype == "image":
            if is_sticker_seg(data):
                continue
            out.append({"file": str(data.get("file") or ""), "url": str(data.get("url") or ""),
                        "name": sender_name, "forward": depth > 0})
        elif stype == "forward" and depth < MAX_DEPTH:
            nodes = data.get("content")
            if not (isinstance(nodes, list) and nodes):
                fid = str(data.get("id") or "")
                nodes = []
                if fid:
                    try:
                        fwd = await bot.get_forward_msg(id=int(fid) if fid.isdigit() else fid) or {}
                        nodes = fwd.get("messages") or (fwd.get("data") or {}).get("messages") or []
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[筛图] 读合并转发失败: {e}")
            for node in nodes:
                if len(out) >= MAX_PER_MESSAGE:
                    break
                if not isinstance(node, dict):
                    continue
                nd = node.get("data") if node.get("type") == "node" else node
                nd = nd or {}
                sender = nd.get("sender") or {}
                name = str(nd.get("nickname") or sender.get("card") or sender.get("nickname") or "")
                inner = nd.get("message") if nd.get("message") is not None else nd.get("content")
                await gather_images(bot, inner, name, depth + 1, out)
    return out


async def fetch_bytes(bot, file: str, url: str) -> bytes | None:
    """优先读 NapCat 本地缓存，失败再直接下载（QQ 图床不走代理）。"""
    if file:
        try:
            info = await bot.get_image(file=file)
            p = str((info or {}).get("file") or "")
            if p and os.path.exists(p):
                return await asyncio.to_thread(_read, p)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[筛图] 本地缓存取图失败: {e}")
    if url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True, trust_env=False) as c:
                r = await c.get(url)
                if r.status_code == 200 and r.content:
                    return r.content
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[筛图] 下载图片失败: {e}")
    return None


def _read(p: str) -> bytes:
    with open(p, "rb") as f:
        return f.read()
