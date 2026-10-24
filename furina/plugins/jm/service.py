"""JM 本子下载核心：调用 jmcomic 下载整本并合并为 PDF，含磁盘缓存与清理。

这里只做「拿到 PDF 文件路径」，与 QQ / NoneBot 都无关（只依赖 loguru），
所以可以脱离机器人和 NoneBot 单独导入、单独跑。
"""
from __future__ import annotations

import asyncio
import glob
import os
import shutil
import time
from pathlib import Path

from loguru import logger

from . import config

_IMG_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")

_jmcomic = None


def _jm():
    """延迟导入 jmcomic：插件加载时少一个重量级依赖，导入失败也能给出清晰报错。"""
    global _jmcomic
    if _jmcomic is None:
        import jmcomic  # noqa: PLC0415

        # jmcomic 默认把每张图的下载日志按 INFO 打进 loguru，一本几百页会刷爆 bot.log。
        # 这里把它的日志执行器换成只写 DEBUG 的实现：正常的开始/结束/失败由插件自己记录。
        def _quiet_log(topic, msg, e=None):
            try:
                if e is not None or "error" in str(topic).lower() or "wrong" in str(topic).lower():
                    logger.warning(f"[JM] jmcomic: {msg}" + (f" ({e})" if e else ""))
                else:
                    logger.debug(f"[JM] jmcomic: {msg}")
            except Exception:
                pass

        jmcomic.JmModuleConfig.EXECUTOR_LOG = _quiet_log
        _jmcomic = jmcomic
    return _jmcomic


def album_dir(album_id: str) -> Path:
    return Path(config.cache_dir()) / str(album_id)


def pdf_path(album_id: str) -> Path:
    return album_dir(album_id) / "pdf" / f"{album_id}.pdf"


def _new_option(album_id: str):
    jmcomic = _jm()
    option = jmcomic.JmOption.default()
    # 关掉 jmcomic 自带的 log 配置（只影响它自己的人性化日志，不影响上面的执行器）
    option.log = False

    proxy = config.proxy()
    if proxy:
        option.client.postman.meta_data.proxies = proxy

    # 图片统一放在 <缓存根>/<车号>/img/... ，PDF 放在 <缓存根>/<车号>/pdf/<车号>.pdf
    option.dir_rule.base_dir = str(album_dir(album_id))
    return option


def pdf_ready(album_id: str) -> bool:
    """PDF 是否已经生成过：命中则秒回，不用再联网。"""
    try:
        p = pdf_path(album_id)
        return p.is_file() and p.stat().st_size > 1024
    except OSError:
        return False


def count_images(album_id: str) -> int:
    """统计缓存目录里已下载的图片数量，用于回报下载进度。"""
    total = 0
    img_root = album_dir(album_id)
    if not img_root.is_dir():
        return 0
    for root, _dirs, files in os.walk(img_root):
        if os.path.basename(root) == "pdf":
            continue
        for name in files:
            if name.lower().endswith(_IMG_SUFFIXES):
                total += 1
    return total


def _album_page_count(option, album_id: str) -> int:
    """查询本子总页数（在下载线程里调用）。查不到返回 -1，不阻塞下载。"""
    try:
        client = option.new_jm_client()
        detail = client.get_album_detail(album_id)
        return sum(len(photo) for photo in detail)
    except Exception as e:
        logger.debug(f"[JM] {album_id} 查询页数失败（忽略限制检查）: {e}")
        return -1


def _classify_failure(album_id: str, exc: BaseException) -> str:
    """区分「车号不存在」和「网络/代理不通」，前者提示用户改车号，后者让用户重试。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    network_markers = (
        "timeout", "timed out", "proxy", "connect", "connection", "getaddrinfo",
        "ssl", "curl", "resolve", "unreachable", "socket", "502", "503", "504",
    )
    not_found_markers = ("404", "not found", "不存在", "没有找到", "无此", "missingalbum", "missing")
    if any(m in text for m in not_found_markers):
        return "not_found"
    if any(m in text for m in network_markers):
        logger.warning(f"[JM] {album_id} 看起来是网络/代理问题: {type(exc).__name__}: {exc}")
        return "network"
    logger.warning(f"[JM] {album_id} 下载异常: {type(exc).__name__}: {exc}")
    return "error"


async def download_pdf(album_id: str, on_progress=None):
    """下载整本并合并 PDF。

    返回 (pdf 路径, 错误码, 标题)：
      - 成功：错误码为空串；标题取不到时为空串。
      - 失败：pdf 路径为空串，错误码取 timeout / not_found / network / too_large / error 之一。
    ``on_progress`` 是可选协程回调，签名 ``async (count: int)``。
    """
    jmcomic = _jm()
    from jmcomic import Feature  # noqa: PLC0415

    if pdf_ready(album_id):
        logger.info(f"[JM] {album_id} 命中本地缓存，直接复用 PDF")
        return str(pdf_path(album_id)), "", ""

    album_dir(album_id).mkdir(parents=True, exist_ok=True)
    option = _new_option(album_id)
    out_pdf = pdf_path(album_id)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    limit = config.max_images()
    if limit > 0:
        pages = await asyncio.to_thread(_album_page_count, option, album_id)
        if pages > limit:
            logger.info(f"[JM] {album_id} 共 {pages} 页，超过上限 {limit}，拒绝")
            return "", "too_large", ""

    result_holder = {}

    def _work():
        result_holder["result"] = jmcomic.download_album(
            album_id,
            option,
            extra=Feature.export_pdf(
                pdf_dir=str(out_pdf.parent),
                filename_rule="Aid",
                delete_original_file=False,
            ),
        )

    task = asyncio.create_task(asyncio.to_thread(_work))
    stop = asyncio.Event()
    progress_task = None
    if on_progress is not None:
        progress_task = asyncio.create_task(_progress_loop(album_id, on_progress, stop, task))

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=config.timeout())
    except asyncio.TimeoutError:
        stop.set()
        if progress_task:
            progress_task.cancel()
        logger.warning(f"[JM] {album_id} 下载超时（>{config.timeout()}s）")
        return "", "timeout", ""
    except Exception as e:
        stop.set()
        if progress_task:
            progress_task.cancel()
        return "", _classify_failure(album_id, e), ""
    finally:
        stop.set()
        if progress_task:
            progress_task.cancel()

    title = ""
    try:
        detail = getattr(result_holder.get("result"), "detail", None)
        album_obj = getattr(detail, "album", None) or detail
        title = str(getattr(album_obj, "name", "") or "")
    except Exception:
        title = ""

    if not out_pdf.is_file():
        # jmcomic 偶尔会把 PDF 输出成别的文件名，兜底找一遍
        candidates = glob.glob(str(out_pdf.parent / "*.pdf"))
        if candidates:
            try:
                os.replace(candidates[0], out_pdf)
            except OSError:
                pass
    if not out_pdf.is_file():
        logger.warning(f"[JM] {album_id} 下载流程结束但没有生成 PDF")
        return "", "error", title

    size_mb = out_pdf.stat().st_size / 1024 / 1024
    logger.info(f"[JM] {album_id} 下载完成：《{title}》 {size_mb:.1f}MB")

    # 这里不做清理：发完群文件后由插件调用 clear_album() 删掉本子的缓存，
    # 不在磁盘上累积（见 furina/plugins/jm/__init__.py 的 _drop_album_cache）。
    return str(out_pdf), "", title


async def _progress_loop(album_id: str, on_progress, stop: asyncio.Event, task: asyncio.Task):
    """下载过程中周期性回报已下载页数。"""
    last = -1
    try:
        while not stop.is_set() and not task.done():
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.progress_interval())
                break
            except asyncio.TimeoutError:
                pass
            count = await asyncio.to_thread(count_images, album_id)
            if count != last:
                last = count
                try:
                    await on_progress(count)
                except Exception as e:
                    logger.debug(f"[JM] 进度提示发送失败: {e}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f"[JM] 进度监控异常: {e}")


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def cache_summary() -> str:
    """给运维/调试用的一句话缓存概况。"""
    root = Path(config.cache_dir())
    if not root.is_dir():
        return "JM 缓存：空"
    albums = [c for c in root.iterdir() if c.is_dir()]
    size_mb = _dir_size(str(root)) // 1024 // 1024
    pdfs = sum(1 for a in albums if pdf_ready(a.name))
    return f"JM 缓存：{len(albums)} 本（其中 {pdfs} 本已有 PDF），占用 {size_mb}MB"


def cache_bytes() -> int:
    root = Path(config.cache_dir())
    return _dir_size(str(root)) if root.is_dir() else 0


def clear_album(album_id: str) -> bool:
    """删除某一本的缓存（面板/排障用）。"""
    d = album_dir(album_id)
    if not d.is_dir():
        return False
    shutil.rmtree(str(d), ignore_errors=True)
    return True


def clear_all() -> int:
    """清空整个 JM 缓存，返回删除的本子数。"""
    root = Path(config.cache_dir())
    if not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(str(child), ignore_errors=True)
            removed += 1
    return removed


def cached_albums() -> list[dict]:
    """列出缓存里的本子，供面板显示与逐条清理。"""
    root = Path(config.cache_dir())
    if not root.is_dir():
        return []
    rows = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            size = _dir_size(str(child))
            mtime = child.stat().st_mtime
        except OSError:
            continue
        rows.append({
            "album_id": child.name,
            "size_mb": round(size / 1024 / 1024, 1),
            "has_pdf": pdf_ready(child.name),
            "mtime": mtime,
            "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
        })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


__all__ = [
    "album_dir",
    "pdf_path",
    "pdf_ready",
    "count_images",
    "download_pdf",
    "cache_summary",
    "cache_bytes",
    "cached_albums",
    "clear_album",
    "clear_all",
]
