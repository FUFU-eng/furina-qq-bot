"""图片获取与编码：优先 NapCat 本地缓存，失败再下载；统一校验、缩放、转 data URL。"""
from __future__ import annotations

import asyncio
import base64
import io
import os

import httpx
from nonebot import logger
from PIL import Image

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers={"User-Agent": UA})
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def is_valid_image(data: bytes) -> bool:
    if not data:
        return False
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return True
    except Exception:
        return False


async def fetch_image_bytes(bot, file_id: str, url: str, timeout: float = 12.0) -> bytes | None:
    if file_id:
        try:
            info = await bot.get_image(file=file_id)
            local = info.get("file", "") if isinstance(info, dict) else getattr(info, "file", "")
            if local:
                # 读盘 + 校验放到线程里，不卡住事件循环（大图/机械硬盘时明显）
                data = await asyncio.to_thread(_read_valid, local)
                if data:
                    return data
        except Exception as e:
            logger.debug(f"[图片] 本地缓存获取失败: {e}")
    if not url:
        return None
    try:
        resp = await (await _get_client()).get(url, timeout=timeout)
        if resp.status_code == 200 and await asyncio.to_thread(is_valid_image, resp.content):
            return resp.content
    except Exception as e:
        logger.debug(f"[图片] 下载失败: {e}")
    return None


def _read_valid(path: str) -> bytes | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    return data if is_valid_image(data) else None


def encode_data_url(data: bytes, max_side: int = 1024) -> str:
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if max(img.size) > max_side:
            ratio = max_side / max(img.size)
            img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"[图片] 编码失败: {e}")
        return ""


def image_part(data: bytes) -> dict | None:
    url = encode_data_url(data)
    return {"type": "image_url", "image_url": {"url": url}} if url else None


async def encode_data_url_async(data: bytes, max_side: int = 1024) -> str:
    """解码 + 缩放 + 重新压缩是纯 CPU 活（大图上百毫秒），放到线程里做。"""
    return await asyncio.to_thread(encode_data_url, data, max_side)


async def image_parts(datas) -> list[dict]:
    """批量编码成多模态消息片段（线程里做），编码失败的直接丢掉。"""
    datas = list(datas or [])
    if not datas:
        return []
    return await asyncio.to_thread(lambda: [p for p in map(image_part, datas) if p])
