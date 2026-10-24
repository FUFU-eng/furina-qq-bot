"""图片下载：httpx 快速下载（带防盗链 Referer）、浏览器兜底、Danbooru cookies、Eagle 安慰奖。"""

import asyncio
import base64
import json
import os
import random
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from io import BytesIO

import httpx
from PIL import Image
from nonebot import logger

from . import browser, config
from .similarity import _looks_like_image
from . import runtime

# ffmpeg 路径（用于视频提取第一帧）
def _find_ffmpeg() -> str:
    import shutil as _sh
    from ...core import env as _env
    local = os.path.join(_env.assets_root(), "tools", "ffmpeg", "bin", "ffmpeg.exe")
    return local if os.path.exists(local) else (_sh.which("ffmpeg") or local)


_FFMPEG_PATH = _find_ffmpeg()


def _is_video_bytes(data: bytes) -> bool:
    """通过魔数判断是否是视频文件（MP4/WebM/AVI/MOV/MKV）"""
    if not data or len(data) < 12:
        return False
    # MP4/MOV: 偏移4-8字节是 "ftyp"
    if data[4:8] == b"ftyp":
        return True
    # WebM/MKV: 前4字节是 0x1A 0x45 0xDF 0xA3
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return True
    # AVI: 前4字节 "RIFF"，偏移8-11 "AVI "
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return True
    return False


def _extract_video_first_frame(video_bytes: bytes) -> bytes | None:
    """用 ffmpeg 提取视频第一帧，返回 PNG 图片字节；失败返回 None"""
    if not os.path.exists(_FFMPEG_PATH):
        logger.warning(f"[搜图] ffmpeg 不存在: {_FFMPEG_PATH}，无法提取视频第一帧")
        return None
    tmp_in = None
    tmp_out = None
    try:
        fd_in, tmp_in = tempfile.mkstemp(suffix=".mp4")
        with os.fdopen(fd_in, "wb") as f:
            f.write(video_bytes)
        tmp_out = tmp_in + "_frame.png"
        result = subprocess.run(
            [_FFMPEG_PATH, "-y", "-i", tmp_in, "-vframes", "1", "-q:v", "2", tmp_out],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0 and os.path.exists(tmp_out):
            with open(tmp_out, "rb") as f:
                frame_data = f.read()
            if frame_data and len(frame_data) > 1000:
                logger.info(f"[搜图] ✅ 从视频提取第一帧成功，大小: {len(frame_data)} 字节")
                return frame_data
        logger.warning(f"[搜图] ffmpeg 提取第一帧失败: {result.stderr.decode('utf-8', errors='ignore')[:200]}")
    except Exception as e:
        logger.warning(f"[搜图] 提取视频第一帧异常: {e}")
    finally:
        for p in (tmp_in, tmp_out):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass
    return None


def make_image_segment(data: bytes):
    """把图片字节写成临时文件，用本地文件路径构造图片段。

    相比超大 base64 内嵌在 API 请求里，NapCat 发送本地文件更稳定，
    不容易触发 sendMsg 的 NTEvent 超时。返回 (MessageSegment, 临时文件路径)。
    """
    fmt = "jpg"
    try:
        with Image.open(BytesIO(data)) as im:
            fmt = (im.format or "jpg").lower()
            if fmt == "jpeg":
                fmt = "jpg"
    except Exception:
        pass
    fd, path = tempfile.mkstemp(prefix="pic_send_", suffix=f".{fmt}")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    from nonebot.adapters.onebot.v11 import MessageSegment

    return MessageSegment.image(Path(path).as_uri()), path


def load_danbooru_cookie_header() -> str:
    """加载 Danbooru cookies，拼成 'k=v; k2=v2' 请求头；文件不存在/失败返回 ''"""
    try:
        if not os.path.exists(config.DANBOORU_COOKIES_PATH):
            return ""
        with open(config.DANBOORU_COOKIES_PATH, "r", encoding="utf-8") as cf:
            saved_cookies = json.load(cf)
        return "; ".join(
            f"{c.get('name', '')}={c.get('value', '')}"
            for c in saved_cookies
            if c.get("name") and c.get("value")
        )
    except Exception:
        return ""


_access_denied_until = {}

def access_denied(url):
    return time.monotonic() < _access_denied_until.get(url, 0)


def _download_donmai_cdn_via_cf_edge(url: str, timeout: float = 15.0) -> bytes | None:
    """当代理节点对 cdn.donmai.us:443 触发连接重置时，复用同属 Cloudflare 的
    danbooru.donmai.us:443 边缘节点建连（TLS SNI 与 Host 仍保持 cdn.donmai.us）下载原图。"""
    if "donmai.us" not in (url or ""):
        return None
    import socket
    import ssl
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "cdn.donmai.us"
    path_q = parsed.path or "/"
    if parsed.query:
        path_q += "?" + parsed.query

    proxy_url = config.PROXY_URL or ""
    p_parsed = urlparse(proxy_url) if proxy_url else None
    proxy_host = (p_parsed.hostname if p_parsed else None) or "127.0.0.1"
    proxy_port = (p_parsed.port if p_parsed else None) or 7897

    raw_sock = None
    tls_sock = None
    try:
        raw_sock = socket.create_connection((proxy_host, proxy_port), timeout=min(timeout, 10.0))
        raw_sock.settimeout(timeout)
        connect_req = (
            "CONNECT danbooru.donmai.us:443 HTTP/1.1\r\n"
            "Host: danbooru.donmai.us:443\r\n"
            "Proxy-Connection: Keep-Alive\r\n\r\n"
        ).encode("ascii")
        raw_sock.sendall(connect_req)
        header_buf = b""
        while b"\r\n\r\n" not in header_buf:
            chunk = raw_sock.recv(1024)
            if not chunk:
                break
            header_buf += chunk
        if b" 200 " not in header_buf.split(b"\r\n", 1)[0]:
            return None

        ctx = ssl.create_default_context()
        tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
        tls_sock.settimeout(timeout)
        cookie_str = load_danbooru_cookie_header()
        cookie_hdr = f"Cookie: {cookie_str}\r\n" if cookie_str else ""
        http_req = (
            f"GET {path_q} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36\r\n"
            "Referer: https://danbooru.donmai.us/\r\n"
            "Accept: image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8\r\n"
            f"{cookie_hdr}"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        tls_sock.sendall(http_req)
        resp_chunks = []
        while True:
            buf = tls_sock.recv(65536)
            if not buf:
                break
            resp_chunks.append(buf)
        raw_resp = b"".join(resp_chunks)
        if b"\r\n\r\n" not in raw_resp:
            return None
        hdr_bytes, body = raw_resp.split(b"\r\n\r\n", 1)
        status_line = hdr_bytes.split(b"\r\n", 1)[0].decode("latin1", errors="ignore")
        if " 200 " not in status_line:
            return None
        headers_lower = hdr_bytes.decode("latin1", errors="ignore").lower()
        if "transfer-encoding: chunked" in headers_lower:
            decoded = bytearray()
            pos = 0
            while pos < len(body):
                line_end = body.find(b"\r\n", pos)
                if line_end < 0:
                    break
                size_str = body[pos:line_end].split(b";", 1)[0].strip()
                if not size_str:
                    break
                chunk_len = int(size_str, 16)
                if chunk_len == 0:
                    break
                start = line_end + 2
                decoded.extend(body[start:start + chunk_len])
                pos = start + chunk_len + 2
            body = bytes(decoded)
        if body and _looks_like_image(body):
            logger.info(f"[搜图] ✅ CF 边缘隧道下载 Danbooru 原图成功: {len(body)} 字节 ({url[:70]})")
            return body
    except Exception as e:
        logger.debug(f"[搜图] CF 边缘隧道下载 Danbooru 原图未命中: {e}")
    finally:
        for s in (tls_sock, raw_sock):
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
    return None


_PXIMG_RE = re.compile(r"pximg\.net/.*?/(\d+)_p(\d+)[^/]*?\.(jpg|jpeg|png|gif)", re.I)


def pixiv_mirror_urls(url: str) -> list:
    """pximg 原图 → pixiv.re 镜像地址。有的代理节点连不上 i.pximg.net（TLS 被断），镜像通常能通。
    单图作品是 /{id}.png，多图作品是 /{id}-{第几张}.png（从 1 开始）；扩展名镜像会自动纠正。"""
    m = _PXIMG_RE.search(url or "")
    if not m:
        m2 = re.search(r"pixiv\.re/(\d+)", url or "")
        return [url] if m2 else []
    pid, page, ext = m.group(1), int(m.group(2)), m.group(3).lower()
    out = [f"https://pixiv.re/{pid}-{page + 1}.{ext}"]
    if page == 0:
        out.insert(0, f"https://pixiv.re/{pid}.{ext}")
    return out


def _download_pixiv_mirror(url: str, deadline=None) -> bytes | None:
    for mirror in pixiv_mirror_urls(url):
        if deadline is not None and time.time() >= deadline:
            return None
        try:
            with httpx.Client(proxy=config.PROXY_URL or None, follow_redirects=True,
                              timeout=min(40, max(.1, deadline - time.time())) if deadline else 40,
                              headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}) as client:
                resp = client.get(mirror)
            if resp.status_code == 200 and resp.content:
                img = Image.open(BytesIO(resp.content))
                img.load()
                logger.info(f"[搜图] ✅ pixiv 镜像拿到原图（{len(resp.content)} 字节）: {mirror}")
                return resp.content
        except Exception as e:  # noqa: BLE001
            logger.info(f"[搜图] pixiv 镜像没拿到: {mirror} {str(e)[:60]}")
    return None


def _try_download_bytes(url: str, site: str = "", deadline=None) -> bytes:
    """httpx 快速下载图片（带防盗链 Referer），失败/非图片返回 None，交给浏览器兜底"""
    if "pixiv.re/" in (url or ""):
        return _download_pixiv_mirror(url, deadline)
    if "pximg.net" in (url or ""):
        # 先直连 pximg（短超时），连不上就走镜像；都不行再交给浏览器
        data = _try_download_plain(url, site, deadline, cap=8)
        return data or _download_pixiv_mirror(url, deadline)
    return _try_download_plain(url, site, deadline)


def _try_download_plain(url: str, site: str = "", deadline=None, cap: float = 15) -> bytes:
    try:
        if deadline is not None and time.time() >= deadline:
            return None
        if access_denied(url):
            return None
        is_china = any(d in url for d in ['qpic.cn', 'qq.com', 'baidu.com', 'aliyuncs.com', 'qiniucdn.com', 'bobopic.com', 'pixivdaily.com', 'sogoucdn.com'])
        # twimg 图片统一用 x.com 作 Referer；pixiv 图床必须用 pixiv.net 作 Referer
        # （BoBoPic 兜底来的 pximg 原图，当前访问站点是 bobopic，不能套用 bobopic 的 Referer）
        if "twimg.com" in url:
            referer = "https://x.com/"
        elif "pximg.net" in url or "pixiv.net" in url:
            referer = "https://www.pixiv.net/"
        else:
            referer = config._SITE_REFERERS.get(site, "https://www.google.com/")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Referer": referer,
        }
        # Danbooru 带上浏览器保存的 cookies
        if site == "danbooru":
            cookie_str = load_danbooru_cookie_header()
            if cookie_str:
                headers["Cookie"] = cookie_str
        with httpx.Client(
            proxy=config.PROXY_URL if config.PROXY_URL and not is_china else None,
            headers=headers, timeout=min(cap, max(.1, deadline-time.time())) if deadline else cap, follow_redirects=True
        ) as client:
            resp = client.get(url)
            if resp.status_code in (401,403,429):
                now = time.monotonic()
                for stale in list(_access_denied_until):
                    if _access_denied_until[stale] <= now:
                        _access_denied_until.pop(stale, None)
                _access_denied_until[url] = now + 120
                return None
            if resp.status_code == 200 and resp.content:
                # 必须是有效图片（排除错误页/HTML）
                try:
                    img = Image.open(BytesIO(resp.content))
                    img.load()
                    return resp.content
                except Exception:
                    pass
                # 不是图片：检查是否是视频，如果是，提取第一帧
                if _is_video_bytes(resp.content):
                    logger.info(f"[搜图] 检测到视频文件（{len(resp.content)} 字节），尝试提取第一帧: {url[:60]}...")
                    frame = _extract_video_first_frame(resp.content)
                    if frame:
                        return frame
    except Exception:
        pass
    if 'donmai.us' in (url or ''):
        rem = min(15.0, max(2.0, deadline - time.time())) if deadline else 15.0
        cf_bytes = _download_donmai_cdn_via_cf_edge(url, timeout=rem)
        if cf_bytes:
            return cf_bytes
    return None


def _download_thumb_bytes(url: str) -> bytes:
    """快速下载谷歌结果缩略图用于相似度排序（短超时 + 图片魔数校验）"""
    # 谷歌有时把缩略图以 data URI 内嵌在页面里，直接解码即可，不用下载
    if url.startswith("data:image/"):
        if url.startswith("data:image/svg"):
            return None  # SVG 不能参与 dHash 对比
        try:
            head, _, b64 = url.partition(",")
            if ";base64" in head:
                return base64.b64decode(b64)
            from urllib.parse import unquote
            return unquote(b64).encode("latin1")
        except Exception:
            return None
    try:
        is_china = any(d in url for d in ['qpic.cn', 'qq.com', 'baidu.com', 'aliyuncs.com', 'qiniucdn.com', 'bobopic.com', 'pixivdaily.com', 'sogoucdn.com'])
        with httpx.Client(
            proxy=config.PROXY_URL if config.PROXY_URL and not is_china else None,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
            timeout=6, follow_redirects=True
        ) as client:
            resp = client.get(url)
            if resp.status_code == 200 and resp.content and _looks_like_image(resp.content):
                return resp.content
    except Exception:
        pass
    return None


def _pick_eagle_consolation() -> bytes:
    """从 Eagle 图库随机选一张图作为"安慰奖"；失败返回 None"""
    try:
        images_dir = os.path.join(config.EAGLE_LIBRARY_DIR, "images")
        if not os.path.isdir(images_dir):
            logger.warning(f"[搜图] Eagle 图库目录不存在: {images_dir}")
            return None
        folders = [d for d in os.listdir(images_dir) if d.endswith(".info")]
        if not folders:
            logger.warning("[搜图] Eagle 图库为空")
            return None
        random.shuffle(folders)
        valid_ext = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
        for folder in folders[:20]:  # 随机试最多 20 个，跳过损坏/过大的
            folder_path = os.path.join(images_dir, folder)
            try:
                files = os.listdir(folder_path)
                img_files = [
                    f for f in files
                    if not f.endswith(".json")
                    and not f.endswith("_thumbnail.png")
                    and os.path.splitext(f)[1].lower() in valid_ext
                ]
                if not img_files:
                    continue
                img_path = os.path.join(folder_path, img_files[0])
                size = os.path.getsize(img_path)
                if size < 1024 or size > 10 * 1024 * 1024:
                    continue
                with open(img_path, "rb") as f:
                    data = f.read()
                # 校验确实是有效图片
                img = Image.open(BytesIO(data))
                img.load()
                logger.info(f"[搜图] 🎁 安慰奖选中: {img_path}")
                return data
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[搜图] 读取 Eagle 图库失败: {e}")
    return None


async def download_image(url: str, retries: int = 2) -> bytes:
    """下载图片（带重试机制）"""
    # base64 data URI 图片（Google Lens 外观匹配区的图经常是这种）：直接解码，不用下载
    if url.startswith("data:image"):
        try:
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64)
        except Exception as e:
            logger.warning(f"[搜图] base64 图片解码失败: {e}")
            return None

    # 判断是否是国内网站，国内网站不走代理
    # 注意：这里必须是代理地址字符串或 None，不能写成布尔值（httpx 收到 True 会报错）
    is_china_site = any(domain in url for domain in ['qpic.cn', 'qq.com', 'baidu.com', 'aliyuncs.com', 'qiniucdn.com', 'bobopic.com', 'pixivdaily.com', 'sogoucdn.com'])
    use_proxy = config.PROXY_URL if config.PROXY_URL and not is_china_site else None

    # 根据不同网站设置不同的 Referer，绕过防盗链
    referer = "https://im.qq.com/"
    if "twimg.com" in url or "twitter.com" in url or "x.com" in url:
        referer = "https://x.com/"
    elif "donmai.us" in url or "danbooru" in url:
        referer = "https://danbooru.donmai.us/"
    elif "pixiv.net" in url or "pximg.net" in url:
        referer = "https://www.pixiv.net/"
    elif "zerochan.net" in url:
        referer = "https://www.zerochan.net/"
    elif "hoyolab.com" in url or "mihoyo.com" in url:
        referer = "https://www.hoyolab.com/"
    elif "arca.live" in url:
        referer = "https://arca.live/"
    elif "rule34.us" in url:
        referer = "https://rule34.us/"
    elif "bobopic.com" in url or "sogoucdn.com" in url or "pixivdaily.com" in url:
        referer = "https://bobopic.com/"
    elif "gelbooru.com" in url:
        referer = "https://gelbooru.com/"
    elif "skebetter.com" in url:
        referer = "https://skebetter.com/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer,
    }

    last_error = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(
                proxy=use_proxy,
                headers=headers,
                follow_redirects=True,
                timeout=30
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.content:
                    if attempt > 0:
                        logger.info(f"[搜图] 图片下载重试成功（第{attempt + 1}次）: {url[:60]}...")
                    return resp.content
                else:
                    last_error = f"状态码: {resp.status_code}, 内容长度: {len(resp.content)}"
                    logger.warning(f"[搜图] 下载图片失败，{last_error}，第{attempt + 1}次尝试，URL: {url[:60]}...")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[搜图] 下载图片异常: {e}，第{attempt + 1}次尝试，URL: {url[:60]}...")

        # 如果还有重试机会，等一下再试
        if attempt < retries:
            await asyncio.sleep(1)

    if "donmai.us" in (url or ""):
        cf_bytes = await asyncio.get_running_loop().run_in_executor(
            runtime.executor, _download_donmai_cdn_via_cf_edge, url, 20.0
        )
        if cf_bytes:
            return cf_bytes
    logger.error(f"[搜图] 图片下载最终失败（共{retries + 1}次尝试）: {last_error}, URL: {url[:60]}...")

    # httpx 拿不到的 Google 图库（lens.usercontent 会返回 200 但空内容），改用浏览器直接抓
    if "usercontent.google.com" in url or "encrypted-tbn" in url or "gstatic.com" in url:
        try:
            img_data = await asyncio.get_running_loop().run_in_executor(
                runtime.executor, browser._browser_fetch_google_image, url
            )
            if img_data:
                logger.info(f"[搜图] ✅ 浏览器抓取 Google 图成功: {url[:60]}...")
                return img_data
        except Exception as e:
            logger.warning(f"[搜图] 浏览器抓取 Google 图失败: {e}")
    return None


# 视觉模型池运行状态（与 ai_chat 同策略）：额度类问题永久跳过，瞬时问题短冷却
_vision_model_state = {"dead": set(), "cooldown": {}}
_vision_model_lock = threading.Lock()


def _vision_classify(status: int, body: str) -> tuple[bool, bool]:
    """返回 (是否鉴权/额度类问题, 是否瞬时可重试)；与 ai_chat.llm._classify 口径一致。"""
    body = (body or "").lower()
    quota_words = (
        "quota", "额度", "免费", "余额", "欠费", "insufficient", "balance", "arrear",
        "billing", "payment", "激活", "activated", "access denied",
    )
    quota = status in (401, 402, 403) or (status == 400 and any(w in body for w in quota_words))
    retryable = status in (408, 429, 500, 502, 503, 504)
    return quota, retryable


def _vision_bool(value) -> bool:
    """兼容模型把布尔值输出成字符串的情况（bool('false') 会错误地变成 True）"""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "是")
    return bool(value)


def _vision_number(value, default: float = 0.0) -> float:
    """兼容模型把数字输出成字符串/带百分号的情况"""
    if value is None:
        return default
    try:
        return float(str(value).strip().rstrip("%"))
    except Exception:
        return default


def vision_judge_similarity(query_bytes: bytes, candidate_bytes: bytes) -> dict:
    """用视觉模型（DeepSeek）判断两张图是否相似。
    用于 dHash 无法区分（20~49 分）的弱候选：视觉模型能看出"同一角色不同画"和"完全无关"的区别。
    返回 {"same_image": bool, "same_character": bool, "similarity": float}；全部模型失败返回 {}"""
    if not config.ai_backend.api_key():
        return {}
    models = config.VISION_MODELS or [config.VISION_MODEL]
    if not models:
        return {}

    def _to_b64(data: bytes, max_side: int = 384) -> str:
        img = Image.open(BytesIO(data)).convert("RGB")
        if max(img.size) > max_side:
            r = max_side / max(img.size)
            img = img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    content = [
        {"type": "image_url", "image_url": {"url": _to_b64(query_bytes)}},
        {"type": "image_url", "image_url": {"url": _to_b64(candidate_bytes)}},
        {"type": "text", "text": "第一张是查询图，第二张是候选图。请严格判断：1)是否同一张图；"
                                  "2)是否同一个角色（人物形象、发型、服饰特征一致）；"
                                  "3)画面内容相似度0-100。注意：只有角色确实相同才给高相似度，"
                                  "角色不同即使画风像也要给低分。只输出JSON，不要任何其他文字、不要代码块，格式："
                                  "{\"same_image\":false,\"same_character\":true,\"similarity\":70}"},
    ]
    last_error = ""
    for model in models:
        with _vision_model_lock:
            if model in _vision_model_state["dead"]:
                continue
            if time.time() < _vision_model_state["cooldown"].get(model, 0):
                continue
        try:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "stream": False,
                "max_tokens": 512,
                "thinking": {"type": "disabled"},
                # Parse JSON defensively; do not require provider-specific JSON mode.
            }
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    config.ai_backend.endpoint(),
                    headers={"Authorization": f"Bearer {config.ai_backend.api_key()}", "Content-Type": "application/json"},
                    json=payload,
                )
            if resp.status_code != 200:
                quota, retryable = _vision_classify(resp.status_code, resp.text)
                if quota and len(models) > 1:
                    with _vision_model_lock:
                        _vision_model_state["dead"].add(model)
                    logger.warning(f"[搜图] 视觉模型 {model} 额度用完/不可用（HTTP {resp.status_code}），本次运行跳过，换下一个")
                elif quota:
                    # 单模型不永久拉黑：旧版任何非 200/非瞬时错误（含某张候选图导致的 400）
                    # 都会让视觉比对停用到重启，之后所有搜图只能靠 dHash 判断原图/相似图。
                    with _vision_model_lock:
                        _vision_model_state["cooldown"][model] = time.time() + 600
                    logger.warning(f"[搜图] 视觉模型 {model} 鉴权/额度异常（HTTP {resp.status_code}），冷却 600s")
                elif retryable:
                    with _vision_model_lock:
                        _vision_model_state["cooldown"][model] = time.time() + 120
                    logger.warning(f"[搜图] 视觉模型 {model} 请求失败（HTTP {resp.status_code}）: ，冷却 120s，换下一个")
                else:
                    # 单次请求被拒（如某张候选图无法解析）只跳过这一次；连续 3 次都被拒说明
                    # 模型/配置本身不支持看图，冷却 10 分钟，避免每次搜图都白白调用
                    with _vision_model_lock:
                        streak = _vision_model_state.setdefault("bad_streak", {})
                        streak[model] = streak.get(model, 0) + 1
                        if streak[model] >= 3:
                            streak[model] = 0
                            _vision_model_state["cooldown"][model] = time.time() + 600
                    logger.warning(f"[搜图] 视觉模型 {model} 请求失败（HTTP {resp.status_code}）")
                last_error = f"HTTP {resp.status_code}"
                continue
            text = resp.json()["choices"][0]["message"]["content"]
            # 容错解析 JSON（模型可能输出多余字段/换行/代码块）
            try:
                data = json.loads(text)
            except Exception:
                m = re.search(r"\{.*\}", text, re.S)
                data = json.loads(m.group(0)) if m else {}
            if not data or "similarity" not in data:
                raise ValueError('Invalid vision comparison JSON')
            result = {
                "same_image": _vision_bool(data.get("same_image", False)),
                "same_character": _vision_bool(data.get("same_character", False)),
                "similarity": _vision_number(data.get("similarity")),
            }
            logger.info(f"[搜图] 视觉模型 {model} 返回: same_image={result['same_image']}, same_character={result['same_character']}, similarity={result['similarity']}")
            with _vision_model_lock:
                _vision_model_state.setdefault("bad_streak", {}).pop(model, None)
            return result
        except Exception as e:
            logger.warning(f"[搜图] 视觉模型 {model} 调用失败: {e}")
            last_error = str(e)
            continue
    if last_error:
        logger.warning(f"[搜图] 视觉模型池全部不可用: {last_error}")
    return {}
