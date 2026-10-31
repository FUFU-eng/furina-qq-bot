"""Independent, bounded original downloads with measured progress and safe Range resume.

No cookies, account state or QQ send retries. A partial file is never offered to
the sender. Image failures do not trip API health.

2026-10-20：原图下载/发送经常失败（国内到 i.pximg.net 很慢，十几 MB 的原图要 100 秒以上，
QQ 上传也会超时）。现在：原图有独立时限；失败时由 provider 依次改用公共图片反代镜像、
再退到 Pixiv 官方 1200px 高清版；超过 8MB 的图在保持分辨率的前提下转成高质量 JPEG 再发。
2026-10-27：落盘后剥离 ICC_PROF/EXIF 并微调角落像素改变公开 MD5，提供防和谐重编码副本供群发兜底。
"""
import asyncio
import hashlib
import logging
from pathlib import Path
import re
import time
import httpx
from PIL import Image, ImageOps
from .errors import PixivError, PixivDownloadError, PixivAccessError
try:
    from nonebot import logger
except ImportError:
    logger=logging.getLogger(__name__)

ATTEMPTS=3
TOTAL_BUDGET=360
MIN_ATTEMPT_BUDGET=120
READ_TIMEOUT=25
BACKOFF=(2,5)


def _verify(path):
    with Image.open(path) as image:image.verify()


COMPACT_OVER=8*1024*1024     # 超过 8MB 的图 QQ 上传很容易超时
COMPACT_MAX_SIDE=4096


def _tweak_corner_pixel(im):
    """Flip 1 LSB in the bottom-right pixel so MD5 differs from public Pixiv hash."""
    w, h = im.size
    if w <= 0 or h <= 0:
        return
    px = im.getpixel((w - 1, h - 1))
    if isinstance(px, tuple) and len(px) >= 3:
        r, g, b = px[0], px[1], px[2] ^ 1
        im.putpixel((w - 1, h - 1), (r, g, b) + px[3:])
    elif isinstance(px, int):
        im.putpixel((w - 1, h - 1), px ^ 1)


def _compact(path):
    """剥离 ICC_PROF/EXIF 并微调角落像素改变公开 MD5；超过 8MB 的图转成高质量 JPEG。返回最终大小。"""
    path=Path(path);size=path.stat().st_size
    tmp=path.with_name(path.name+'.jpg.tmp')
    try:
        with Image.open(path) as im:
            if getattr(im,'is_animated',False):return size
            fmt=(im.format or 'JPEG').upper()
            im=im.convert('RGB')
            _tweak_corner_pixel(im)
            if size<=COMPACT_OVER and fmt in ('JPEG','JPG','PNG','WEBP'):
                im.save(tmp,format='JPEG',quality=95,optimize=True)
                new=tmp.stat().st_size
                if new<=COMPACT_OVER:
                    tmp.replace(path)
                    return new
            if max(im.size)>COMPACT_MAX_SIDE:
                im.thumbnail((COMPACT_MAX_SIDE,COMPACT_MAX_SIDE),Image.LANCZOS)
            for q in (92,88,82):
                im.save(tmp,format='JPEG',quality=q,optimize=True)
                if tmp.stat().st_size<=COMPACT_OVER:break
        new=tmp.stat().st_size
        if new<size or size<=COMPACT_OVER:
            tmp.replace(path);logger.info(f'[Pixiv下载] 图片已净化重编码 {size}->{new} 字节')
            return new
        return size
    except Exception as exc:
        logger.warning(f'[Pixiv下载] 图片重编码跳过：{type(exc).__name__}')
        return size
    finally:
        tmp.unlink(missing_ok=True)


def make_antishield_copy(src_path, dst_path=None, mode='soft'):
    """生成防 QQ 群聊屏蔽的临时图片副本（soft=微边框+四角微扰动重编码；rotate=逆时针旋转90度）。"""
    src = Path(src_path)
    dst = Path(dst_path) if dst_path else src.with_name(f"{src.stem}_{mode}.jpg")
    with Image.open(src) as im:
        im = im.convert('RGB')
        if mode == 'rotate':
            im = im.transpose(Image.Transpose.ROTATE_90)
        else:
            im = ImageOps.expand(im, border=2, fill=(252, 252, 250))
        w, h = im.size
        for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            if 0 <= x < w and 0 <= y < h:
                r, g, b = im.getpixel((x, y))
                im.putpixel((x, y), (r ^ 1, g ^ 1, b ^ 1))
        im.save(dst, format='JPEG', quality=90, optimize=True)
    return dst


def _validator(headers):
    etag=headers.get('etag','')
    if etag and not etag.startswith('W/'):return etag
    return headers.get('last-modified','')


async def download_original(url,path,max_bytes,proxy,headers,budget=None,attempts=None):
    path=Path(path);part=path.with_name(path.name+'.part')
    part.unlink(missing_ok=True)
    start=time.monotonic();deadline=start+(budget or TOTAL_BUDGET)
    attempts=attempts or ATTEMPTS
    received=0;expected=None;validator='';last_error='unknown';stage='connect'
    asset=hashlib.sha256(url.encode()).hexdigest()[:12]
    max_loop_lag=0.0
    async def heartbeat():
        nonlocal max_loop_lag
        while True:
            before=time.monotonic();await asyncio.sleep(1)
            max_loop_lag=max(max_loop_lag,time.monotonic()-before-1)
    monitor=asyncio.create_task(heartbeat())
    try:
        for attempt in range(attempts):
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            offset=part.stat().st_size if part.exists() and validator else 0
            if not offset:
                part.unlink(missing_ok=True);received=0
            request_headers=dict(headers)
            request_headers.update({'Accept':'image/*','Accept-Encoding':'identity'})
            if offset:
                request_headers.update({'Range':f'bytes={offset}-','If-Range':validator})
            t0=time.monotonic();stage='connect'
            try:
                async with asyncio.timeout(min(remaining,MIN_ATTEMPT_BUDGET)) as budget:
                    async with httpx.AsyncClient(proxy=proxy(),trust_env=False,follow_redirects=False,
                         timeout=httpx.Timeout(READ_TIMEOUT,connect=10),headers=request_headers) as http:
                        async with http.stream('GET',url) as response:
                            stage='headers';status=response.status_code
                            if status in (401,403,429):
                                raise PixivAccessError(f'原图 HTTP {status}，停止该任务，不自动绕过')
                            if status==416 and offset:
                                part.unlink(missing_ok=True);validator='';expected=None
                                raise httpx.RemoteProtocolError('range rejected; restart clean')
                            if status in (408,500,502,503,504):raise httpx.ReadError(f'HTTP_{status}')
                            if status not in (200,206) or not response.headers.get('content-type','').startswith('image/'):
                                raise PixivError(f'原图 HTTP {status} 或内容不是图片')
                            new_validator=_validator(response.headers)
                            if status==206:
                                match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('content-range',''))
                                if not offset or not match or int(match[1])!=offset or (expected is not None and int(match[3])!=expected) or new_validator!=validator:
                                    part.unlink(missing_ok=True);validator='';expected=None
                                    raise httpx.RemoteProtocolError('unsafe range response; restart clean')
                                if int(match[2])!=int(match[3])-1:
                                    part.unlink(missing_ok=True);validator='';expected=None
                                    raise httpx.RemoteProtocolError('incomplete range response')
                                expected=int(match[3]);mode='ab'
                            else:
                                offset=0;mode='wb';validator=new_validator
                                length=response.headers.get('content-length','')
                                expected=int(length) if length.isdigit() and int(length)>0 else None
                            if expected and expected>max_bytes:raise PixivError('原图超过本插件空间上限，不降质替换')
                            per_attempt=max(MIN_ATTEMPT_BUDGET,(expected or max_bytes)/131072+30)
                            budget.reschedule(asyncio.get_running_loop().time()+min(deadline-time.monotonic(),per_attempt))
                            received=offset;last_log=time.monotonic();stage='body'
                            with part.open(mode) as f:
                                async for chunk in response.aiter_bytes(128*1024):
                                    received+=len(chunk)
                                    if received>max_bytes or (expected and received>expected):raise PixivError('原图超出声明长度或空间上限')
                                    f.write(chunk)
                                    if time.monotonic()-last_log>=15:
                                        logger.info(f'[Pixiv下载] asset={asset} attempt={attempt+1} bytes={received}/{expected or "?"} elapsed={time.monotonic()-start:.1f}s')
                                        last_log=time.monotonic()
                            if not received or (expected and received!=expected):raise httpx.RemoteProtocolError('incomplete image body')
                    stage='verify';await asyncio.to_thread(_verify,part)
                    part.replace(path)
                    received=await asyncio.to_thread(_compact,path)
                logger.info(f'[Pixiv下载] completed asset={asset} bytes={received} elapsed={time.monotonic()-start:.1f}s attempts={attempt+1}')
                return received
            except asyncio.CancelledError:raise
            except (httpx.TransportError,TimeoutError) as exc:
                last_error=type(exc).__name__
                logger.warning(f'[Pixiv下载] retry asset={asset} attempt={attempt+1} stage={stage} bytes={received}/{expected or "?"} elapsed={time.monotonic()-t0:.1f}s error={last_error} loop_lag_max={max_loop_lag:.1f}s')
                if attempt<attempts-1 and time.monotonic()<deadline:
                    await asyncio.sleep(min(BACKOFF[attempt],max(0,deadline-time.monotonic())))
            except (OSError,ValueError) as exc:
                raise PixivError(f'原图保存或校验失败（{type(exc).__name__}），阶段 {stage}') from None
        raise PixivDownloadError(f'单张原图下载未完成（{last_error}，阶段{stage}，收到{received}/{expected or "未知"}字节，耗时{time.monotonic()-start:.0f}秒）；其他作品可继续')
    finally:
        monitor.cancel();await asyncio.gather(monitor,return_exceptions=True)
        part.unlink(missing_ok=True)
