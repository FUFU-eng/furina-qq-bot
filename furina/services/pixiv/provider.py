"""Read-only public Pixiv web endpoints; no cookie extraction, no authentication bypass.
These endpoints are not a guaranteed public API. HTTP 403/429 fails visibly.

Network note (2026-09-22): Pixiv's Cloudflare front started challenging plain
Python HTTP clients from some networks (HTTP 403 'Just a moment...' page).
When the optional `curl_cffi` package is installed, JSON requests use Chrome
TLS impersonation, which passes the challenge; otherwise the client falls
back to plain httpx. Image downloads (i.pximg.net) keep using httpx.
"""
import asyncio
import re
try:
    from nonebot import logger
except ImportError:
    import logging
    logger = logging.getLogger("pixiv_support")
import math
import time
import urllib.request
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

try:
    from curl_cffi import requests as _cffi_requests
    _HAVE_CFFI = True
except ImportError:  # optional; plain httpx still works where unchallenged
    _cffi_requests = None
    _HAVE_CFFI = False

try:
    from curl_cffi.requests.exceptions import InvalidJSONError as _CffiJSONError
    _JSON_ERRORS = (ValueError, _CffiJSONError)
except ImportError:
    _JSON_ERRORS = (ValueError,)

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Referer': 'https://www.pixiv.net/',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept': 'application/json',
}


from .errors import PixivError, PixivNetworkError, PixivAccessError, PixivDownloadError
from .downloader import download_original


_NETWORK_EXCEPTIONS = (httpx.TransportError, TimeoutError, OSError)
if _HAVE_CFFI:
    _NETWORK_EXCEPTIONS += (_cffi_requests.RequestsError,)



def safe_work(work):
    return (str(work.get('id', work.get('illust_id', ''))).isdigit()
            and str(work.get('illustType', work.get('illust_type', '0'))) == '0'
            and str(work.get('xRestrict', '')) == '0'
            and not work.get('isMasked', False))


# 图片下载兜底（只换图片来源，不涉及账号 / Cookie）：
# 1) 原图直连（限时）→ 2) 公共图片反代镜像 → 3) Pixiv 官方 1200px 高清版（直连 → 镜像）
ORIGINAL_BUDGET=150
TOTAL_IMAGE_BUDGET=360   # 与原来单张原图的总时限一致，兜底不会拖得更久
MIRROR_BUDGET=70
REGULAR_BUDGET=60
IMAGE_MIRRORS=('i.pixiv.re','i.pixiv.cat','i.yuki.sh')


def mirror_urls(url):
    p=urlparse(url)
    return [url.replace(f'//{p.hostname}/',f'//{host}/',1) for host in IMAGE_MIRRORS]


def regular_url(url):
    """img-original/.../123_p0.png  ->  img-master/.../123_p0_master1200.jpg"""
    m=re.match(r'^(https://[^/]+)/img-original/(img/.+?)(_p\d+)\.(?:png|jpe?g|gif|webp)$',url)
    return f'{m[1]}/img-master/{m[2]}{m[3]}_master1200.jpg' if m else None


def check_image_url(url):
    p = urlparse(url)
    if p.scheme != 'https' or not p.hostname or not p.hostname.endswith('.pximg.net') or p.port not in (None, 443) or p.username:
        raise PixivError('原图地址不属于 Pixiv 图片域名')
    return url


class Client:
    SESSION_TTL = 600
    REQUEST_TIMEOUT = 22
    CIRCUIT_SECONDS = 30
    API_ATTEMPTS = 3

    def __init__(self, proxy=None):
        self._explicit_proxy = proxy
        self._proxy = self._system_proxy()
        self.http = None
        self.session = None
        self._io_lock = asyncio.Lock()
        self.gate = asyncio.Lock()
        self.last = 0.0
        self._created = 0.0
        self._broken = False
        self._closed = False
        self._blocked_until = 0.0
        self._build_transports()

    def _system_proxy(self):
        proxies = urllib.request.getproxies()
        return self._explicit_proxy or proxies.get('https') or proxies.get('http')

    def _build_transports(self):
        self.http = httpx.AsyncClient(
            proxy=self._proxy, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(20,connect=8), headers=dict(BROWSER_HEADERS))
        self.session = None
        if _HAVE_CFFI:
            self.session = _cffi_requests.AsyncSession(
                impersonate='chrome', timeout=20,
                proxy=self._proxy, trust_env=False, headers=dict(BROWSER_HEADERS))
        self._created = time.monotonic()
        self._broken = False

    @property
    def transport(self):
        return 'curl_cffi(chrome)' if self.session is not None else 'httpx'

    async def _close_transports(self):
        session,http=self.session,self.http
        self.session=self.http=None
        if session is not None:
            try:await session.close()
            except Exception:pass
        if http is not None:
            await http.aclose()

    async def _ensure_transports(self):
        if self._closed:raise PixivError('Pixiv 客户端已关闭')
        proxy=self._system_proxy()
        if proxy!=self._proxy:
            self._proxy=proxy;self._broken=True;self._blocked_until=0
        if time.monotonic()<self._blocked_until:
            raise PixivNetworkError('Pixiv 请求链路正在冷却，请约一分钟后再试；本次未再次请求远端')
        if self._broken or time.monotonic()-self._created>=self.SESSION_TTL or self.http is None:
            await self._close_transports();self._build_transports()

    async def close(self):
        async with self._io_lock:
            self._closed=True
            await self._close_transports()

    async def _pace(self):
        async with self.gate:
            await asyncio.sleep(max(0,.45-(time.monotonic()-self.last)))
            self.last=time.monotonic()

    def _network_failure(self, stage, exc, attempt):
        # Numeric TLS/transport code, never proxy URLs, cookies or credentials.
        code = getattr(exc, 'code', None)
        if code is None:
            m = re.search(r'curl: \((\d+)\)', str(exc))
            code = m[1] if m else 'n/a'
        logger.warning(f'[Pixiv API诊断] stage={stage} transport={self.transport} attempt={attempt+1} error={type(exc).__name__} code={code}')
        self._broken=True
        if attempt>=self.API_ATTEMPTS-1:
            self._blocked_until=time.monotonic()+self.CIRCUIT_SECONDS
            raise PixivNetworkError(f'Pixiv {stage}请求失败（{type(exc).__name__}）；已换新连接重试{self.API_ATTEMPTS-1}次，暂停请求{self.CIRCUIT_SECONDS}秒') from None

    async def json(self, path, params=None):
        if not path.startswith('/') or path.startswith('//'):
            raise PixivError('无效 Pixiv API 路径')
        # Serialize session rotation with all users of both transports. No browser
        # cookies, redirect following or retries of QQ send operations are involved.
        async with self._io_lock:
            for attempt in range(self.API_ATTEMPTS):
                await self._ensure_transports()
                await self._pace()
                try:
                    async with asyncio.timeout(self.REQUEST_TIMEOUT):
                        if self.session is not None:
                            r=await self.session.get('https://www.pixiv.net'+path,params=params,allow_redirects=False)
                        else:
                            r=await self.http.get('https://www.pixiv.net'+path,params=params)
                    if r.status_code in (401,403,429):
                        raise PixivAccessError(f'Pixiv HTTP {r.status_code}，访问受限或限流；停止本次任务，不自动绕过')
                    if r.status_code in (408,500,502,503,504):
                        if attempt<self.API_ATTEMPTS-1:
                            await asyncio.sleep(1);continue
                        self._blocked_until=time.monotonic()+self.CIRCUIT_SECONDS
                        raise PixivNetworkError(f'Pixiv API HTTP {r.status_code}；远端暂不可用，暂停请求60秒')
                    if r.status_code!=200:raise PixivError(f'Pixiv HTTP {r.status_code}')
                    try:data=r.json()
                    except _JSON_ERRORS:raise PixivError('Pixiv 返回的不是有效 JSON') from None
                    if not isinstance(data,dict) or data.get('error'):
                        raise PixivError('Pixiv 未返回可用数据（作品可能已删除或访问受限）')
                    return data
                except asyncio.CancelledError:
                    self._broken=True;raise
                except _NETWORK_EXCEPTIONS as exc:
                    self._network_failure('API',exc,attempt)
                    await asyncio.sleep((1,3,5)[min(attempt,2)])
                # Programming errors retain their type/traceback; never relabel as network.
        raise PixivError('Pixiv 请求失败')

    async def ranking(self, board):
        rows = {}
        try:
            for page in range(1, 4):
                d = await self.json('/ranking.php', {'mode': board.mode, 'content': 'illust',
                                    'date': board.date.strftime('%Y%m%d'), 'format': 'json', 'p': page})
                if str(d.get('date')) != board.date.strftime('%Y%m%d') or d.get('mode') != board.mode:
                    raise PixivError('返回的榜单日期或类型不符，不用其他榜单替代')
                for item in d.get('contents', []):
                    rank = int(item['rank'])
                    if 1 <= rank <= 150:
                        rows[rank] = {'rank': rank, 'id': str(item['illust_id'])}
        except PixivError as exc:
            if 'HTTP 404' in str(exc):
                # Pixiv answers {"error": "不在排行榜统计范围内"} before publication.
                raise PixivError('榜单尚未公布，稍后重试') from None
            raise
        present = [rows[i] for i in range(1, 151) if i in rows]
        if len(present) < 147:
            raise PixivError(f'此插画榜只取得 {len(present)}/150 个名次，停止该批而不补入榜外作品')
        # A few ranks may legitimately disappear (deleted works); batches send
        # the ranks actually present and note the gap instead of failing all.
        return present

    async def detail(self, pid):
        if not str(pid).isdigit():
            raise PixivError('无效 PID')
        body = (await self.json(f'/ajax/illust/{pid}', {'lang': 'zh'})).get('body')
        if not isinstance(body, dict) or not safe_work(body):
            raise PixivError('此作品不是可发送的全年龄插画')
        return body

    async def pages(self, pid):
        if not str(pid).isdigit():
            raise PixivError('无效 PID')
        body = (await self.json(f'/ajax/illust/{pid}/pages')).get('body')
        if not isinstance(body, list) or not body:
            raise PixivError('作品页面为空')
        return [check_image_url(p['urls']['original']) for p in body]

    async def search(self, queries, count, pool_size=0):
        candidates = {}
        popular_ids = set()
        normalize = lambda s: ''.join(unicodedata.normalize('NFKC', s).casefold().split())
        originals = {normalize(q) for q in queries}
        pending = list(dict.fromkeys(queries))[:4]
        for query in pending:
            d = await self.json('/ajax/search/artworks/' + quote(query, safe=''), {
                'word': query, 'order': 'date_d', 'mode': 'safe', 'p': 1,
                's_mode': 's_tag', 'type': 'illust', 'lang': 'zh',
            })
            body = d.get('body', {})
            # Only exact name translations returned by Pixiv itself; never translate
            # every related tag (that would mix other characters into the request).
            translations = body.get('tagTranslation', {})
            if isinstance(translations, dict):
                for tag, names in translations.items():
                    if not isinstance(names, dict):
                        continue
                    if any(isinstance(v, str) and normalize(v) in originals for v in names.values()):
                        if tag not in pending and len(pending) < 4:
                            pending.append(tag)
            popular = body.get('popular', {})
            hot = popular.get('permanent', []) + popular.get('recent', [])
            recent = body.get('illustManga', {}).get('data', [])
            for item in hot + recent[:16]:
                if safe_work(item):
                    pid = str(item['id'])
                    candidates.setdefault(pid, item)
            popular_ids.update(str(w['id']) for w in hot if safe_work(w))
        # Take popular previews first, then a spread of recent candidates.
        # Return a larger pool than requested so the caller can rotate picks.
        pool = pool_size or max(12, count * 2)
        ids = sorted(candidates, key=lambda p: p not in popular_ids)[:pool]
        gate = asyncio.Semaphore(2)

        async def score_one(pid):
            async with gate:
                try:
                    work = await self.detail(pid)
                except (PixivNetworkError, PixivAccessError):
                    raise
                except PixivError:
                    return None
                bookmarks = int(work.get('bookmarkCount', 0))
                views = max(1, int(work.get('viewCount', 0)))
                score = math.log1p(bookmarks) + min(1, bookmarks / views) * 2
                return score, work

        # Retrieve details with bounded concurrency; the shared request gate still paces GETs.
        results = await asyncio.gather(*(score_one(pid) for pid in ids), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, PixivNetworkError):
                raise result
        ranked = [r for r in results if r is not None and not isinstance(r, BaseException)]
        if not ranked:
            network = next((r for r in results if isinstance(r,PixivNetworkError)),None)
            if network:raise network
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [work for _, work in ranked[:pool]]

    async def download(self, url, path, max_bytes):
        check_image_url(url)
        if self._closed:raise PixivError('Pixiv 客户端已关闭')
        # Independent connections: one slow original must not stop every group's API.
        deadline=time.monotonic()+TOTAL_IMAGE_BUDGET
        try:
            return await download_original(url,path,max_bytes,self._system_proxy,BROWSER_HEADERS,budget=ORIGINAL_BUDGET)
        except PixivError as exc:
            first=exc
        logger.warning(f'[Pixiv下载] 原图直连失败，改用镜像/高清版兜底：{str(first)[:80]}')
        plans=[(mirror_urls(url)[0],MIRROR_BUDGET)]
        regular=regular_url(url)
        if regular:
            plans+=[(regular,REGULAR_BUDGET)]+[(u,REGULAR_BUDGET) for u in mirror_urls(regular)]
        else:
            plans+=[(u,MIRROR_BUDGET) for u in mirror_urls(url)[1:]]
        for alt,budget in plans:
            remaining=deadline-time.monotonic()
            if self._closed or remaining<20:break
            try:
                size=await download_original(alt,path,max_bytes,self._system_proxy,BROWSER_HEADERS,budget=min(budget,remaining),attempts=2)
                logger.info(f'[Pixiv下载] 兜底成功 source={urlparse(alt).hostname} kind={"regular" if "master1200" in alt else "original"}')
                return size
            except PixivError:
                continue
        raise first
