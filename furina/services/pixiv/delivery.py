"""Send original images with their attribution. Never blindly replay a send."""
import hashlib
import json
import asyncio
from urllib.parse import urlparse
from pathlib import Path

from .provider import PixivError


class SendUncertain(PixivError):
    pass


async def send_work(client, store, work, send, settings, prefix='', allowed=lambda: True, receipt_scope=None,
                    keep_paths=False, flat=False):
    """send(text, paths) must return only after the OneBot send API acknowledges.

    Single-page works use an ordinary message. Multi-page works always use
    merged forwarding, including the last one-page chunk. Split only beyond
    99 image nodes / 64 MiB, keeping attribution and exact page numbers.

    keep_paths=True: 下载好的原图**不删**，把路径返回给调用方
    （批量取图要把多部作品合并成一条转发，需要先攒着）。
    flat=True: 多页作品也按普通消息发（批量时最终统一用合并转发，这里不逐部转发）。
    """
    pid = str(work['id'])
    if receipt_scope and store.work_sent(receipt_scope,pid):return 0
    if receipt_scope and store.work_uncertain(receipt_scope,pid):
        raise SendUncertain('该作品存在发送不确定记录，需人工核查，不自动重发')
    urls = await client.pages(pid)
    if len(urls) != int(work.get('pageCount', len(urls))):
        raise PixivError('页面数不完整，停止该作品而不伪报全部发送')
    directory = store.temp_dir()
    cap = settings['cache_mb'] * 1024 * 1024
    paths, first, chunk_bytes = [], 1, 0
    sent = 0
    retain_pending = False
    kept = []

    async def flush(last):
        nonlocal paths, first, chunk_bytes, sent
        if not paths:
            return
        if not allowed():
            raise PixivError('功能已关闭或本批已到期，停止发送')
        caption = f'{prefix}作者：{work.get("userName", "未知作者")} · PID：{pid}'
        def signature():
            h=hashlib.sha256(json.dumps(urls[first-1:last],ensure_ascii=False).encode())
            for path in paths:
                with path.open('rb') as f:
                    for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
            return h.hexdigest()
        must_send=True
        if receipt_scope:
            sig=await asyncio.to_thread(signature)
            try:must_send=store.begin_chunk(receipt_scope,pid,first,last,sig)
            except RuntimeError as exc:raise SendUncertain(str(exc)) from None
        if must_send:
            try:
                if len(urls) >= 2 and not flat:
                    await send(caption,list(paths),forward=True,start_page=first,total_pages=len(urls))
                else:
                    await send(caption,list(paths))
            except BaseException as exc:
                if receipt_scope:store.finish_chunk(receipt_scope,pid,first,last,'unknown')
                if isinstance(exc,asyncio.CancelledError):raise
                raise SendUncertain('QQ 发送未得到成功确认；已记录不确定状态，不自动重发') from None
            if receipt_scope:store.finish_chunk(receipt_scope,pid,first,last,'sent')
        sent += len(paths)
        if keep_paths:
            kept.extend(paths)
        else:
            for p in paths:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        paths, first, chunk_bytes = [], last + 1, 0
        await asyncio.sleep(settings['interval_seconds'])

    try:
        for i, url in enumerate(urls, 1):
            if not allowed():
                raise PixivError('功能已关闭或本批已到期')
            store.evict_assets(min(64*1024*1024,cap),cap)
            available = cap - store.usage()
            if available < 1024 * 1024:
                raise PixivError('临时缓存已达上限，请检查占用；未继续下载')
            suffix = Path(urlparse(url).path).suffix.lower()
            if suffix not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                suffix = '.img'
            p = directory / f'{pid}_p{i-1}{suffix}'
            size = await store.download_cached(client,url,p,min(64*1024*1024,available),cap)
            if paths and (len(paths) >= 99 or chunk_bytes + size > 64 * 1024 * 1024):
                await flush(i - 1)
            paths.append(p)
            chunk_bytes += size
        await flush(len(urls))
        if receipt_scope:store.mark_work_sent(receipt_scope,pid)
        return kept if keep_paths else sent
    except SendUncertain:
        retain_pending = True
        raise
    except PixivError:
        if sent and not receipt_scope and not keep_paths:
            raise SendUncertain('此作品已有部分页面发出，后续页面失败；不自动重发整部作品') from None
        raise
    except asyncio.CancelledError:
        retain_pending = bool(receipt_scope and store.work_uncertain(receipt_scope,pid))
        raise
    finally:
        # A timed-out OneBot call may still be uploading in QQ. Keep originals
        # within our bounded private cache until the normal one-hour sweep.
        # keep_paths=True 时交给调用方处理（批量要合并发送后再清）。
        if not retain_pending and not keep_paths:
            store.remove_temp(directory)
