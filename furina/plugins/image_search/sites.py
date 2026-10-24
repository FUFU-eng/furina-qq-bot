"""各白名单网站的页面适配：从页面提取原图 URL、缩略图相似度匹配、URL 构造。"""

import json
import math
import os
import re
import time

from nonebot import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from . import browser, config, runtime
from .download import load_danbooru_cookie_header
from .runtime import _interruptible_sleep, aborted
from .similarity import _image_similarity_crop_tolerant, _is_placeholder_small, _looks_like_image


# 网站装饰/静态资源图的 URL 特征（logo、图标、分割线等），不是帖子内容图，参与相似度匹配只会产生误报
_DECORATION_KEYWORDS = (
    "static-resource", "hyl-static", "webstatic.hoyoverse", "/_static/",
    ".svg", "/favicon", "profile_images", "avatar", "emoji", "logo", "badge",
)


def _is_decoration_url(url: str) -> bool:
    return any(x in url for x in _DECORATION_KEYWORDS)


def _bobopic_original_urls(art_id: str) -> list:
    """获取 BoBoPic 某张作品的原图直链：
    go.bobopic.com/<id> 会 302 到 go.php 查看页，页面里 #main-img 的 src 是 sogoucdn 原图直链"""
    import httpx
    try:
        with httpx.Client(
            proxy=None,  # 国内站点，直连更快更稳
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
            timeout=12, follow_redirects=True
        ) as client:
            resp = client.get(f"https://go.bobopic.com/{art_id}")
            if resp.status_code != 200:
                return []
            m = re.search(r'id="main-img"[^>]*src="([^"]+)"', resp.text)
            if m:
                img_src = m.group(1).replace("&amp;", "&")
                logger.info(f"[搜图] BoBoPic 作品 {art_id} 原图直链: {img_src[:80]}")
                return [img_src]
    except Exception as e:
        logger.warning(f"[搜图] BoBoPic 获取原图链接失败 {art_id}: {e}")
    return []


def _twitter_original_url(url: str) -> str:
    """Normalize a Twitter media URL to the original-size image variant."""
    if not isinstance(url, str):
        return ""
    url = url.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    url = url.replace("&amp;", "&").strip()
    if not url.startswith(("http://", "https://", "//")):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if "pbs.twimg.com/" not in url:
        return ""
    base = url.split("?", 1)[0]
    return f"{base}?format=jpg&name=orig"


def _twitter_status_id(url: str) -> str:
    match = re.search(r"/status/(\d+)", url or "")
    return match.group(1) if match else ""


def _twitter_syndication_token(status_id: str) -> str:
    """Build the token accepted by cdn.syndication.twimg.com."""
    try:
        value = float(status_id) / 1_000_000_000_000_000 * math.pi
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        integer = int(value)
        whole = ""
        while integer:
            integer, remainder = divmod(integer, 36)
            whole = digits[remainder] + whole
        if not whole:
            whole = "0"
        fraction = value - int(value)
        if fraction:
            whole += "."
            for _ in range(16):
                fraction *= 36
                digit = int(fraction)
                whole += digits[digit]
                fraction -= digit
        return whole.replace("0", "").replace(".", "")
    except Exception:
        return ""


def _twitter_syndication_media_urls(status_id: str, timeout: float = 8.0) -> list:
    """Fetch public media metadata when X's HTML is a login-wall shell."""
    if not status_id:
        return []
    import httpx

    endpoint = "https://cdn.syndication.twimg.com/tweet-result"
    params = {
        "id": status_id,
        "lang": "en",
        "token": _twitter_syndication_token(status_id) or "1",
    }
    try:
        with httpx.Client(
            proxy=config.PROXY_URL if config.PROXY_URL else None,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            response = client.get(endpoint, params=params)
        if response.status_code != 200:
            logger.info(f"[搜图] Twitter syndication 返回 HTTP {response.status_code}")
            return []
        payload = response.json()
    except Exception as e:
        logger.info(f"[搜图] Twitter syndication 获取失败: {e}")
        return []

    urls = []
    for media in payload.get("mediaDetails") or []:
        if not isinstance(media, dict):
            continue
        media_url = media.get("media_url_https") or media.get("media_url") or ""
        normalized = _twitter_original_url(media_url)
        if normalized and normalized not in urls:
            urls.append(normalized)
    for photo in payload.get("photos") or []:
        if not isinstance(photo, dict):
            continue
        normalized = _twitter_original_url(photo.get("url") or "")
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def _extract_tweet_status_image(driver, epoch) -> list:
    """Extract original media from a tweet page, including login-wall fallbacks."""
    media_selector = (
        'img[src*="twimg.com/media"], img[data-src*="twimg.com/media"], '
        'img[src*="tweet_video_thumb"], img[src*="ext_tw_video_thumb"], '
        'video[poster*="twimg.com"], video[data-poster*="twimg.com"]'
    )
    try:
        WebDriverWait(driver, 4).until(lambda d: d.find_elements(By.CSS_SELECTOR, media_selector))
    except Exception:
        pass
    try:
        imgs = driver.find_elements(By.CSS_SELECTOR, media_selector)
        for el in imgs:
            try:
                if el.tag_name.lower() == "video":
                    img_url = el.get_attribute("poster") or el.get_attribute("data-poster") or ""
                else:
                    img_url = el.get_attribute("data-src") or el.get_attribute("src") or ""
                normalized = _twitter_original_url(img_url)
                if normalized:
                    logger.info(f"[搜图] Twitter 帖子页直接取原图（含视频封面）: {normalized[:70]}...")
                    return [normalized]
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[搜图] Twitter 帖子页 DOM 提取失败: {e}")

    # X may render only a login-wall shell. Its metadata and serialized app state
    # often still contain the media URL even when no <img> is mounted.
    fallback_urls = []
    try:
        meta_elements = driver.find_elements(
            By.CSS_SELECTOR,
            'meta[property="og:image"], meta[name="twitter:image"], meta[property="og:image:secure_url"]',
        )
        for el in meta_elements:
            normalized = _twitter_original_url(el.get_attribute("content") or "")
            if normalized and normalized not in fallback_urls:
                fallback_urls.append(normalized)
    except Exception:
        pass
    try:
        source = driver.page_source or ""
        source = source.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
        for match in re.findall(
            r"https?://pbs\.twimg\.com/(?:media|amplify_video_thumb|ext_tw_video_thumb)/[^\"'<>\\\s]+",
            source,
        ):
            normalized = _twitter_original_url(match)
            if normalized and normalized not in fallback_urls:
                fallback_urls.append(normalized)
    except Exception:
        pass
    if fallback_urls:
        logger.info("[搜图] Twitter 帖子页 DOM 无媒体图，已从 meta/页面源码提取媒体")
        return fallback_urls

    status_id = _twitter_status_id(getattr(driver, "current_url", ""))
    if status_id and not aborted(epoch):
        syndication_urls = _twitter_syndication_media_urls(status_id)
        if syndication_urls:
            logger.info(f"[搜图] Twitter syndication 获取到 {len(syndication_urls)} 个媒体原图: {status_id}")
            return syndication_urls
    logger.info("[搜图] Twitter 帖子页未渲染媒体图，meta/源码/syndication 也未取到图片")
    return []

def _find_best_tweet_status_url(driver, query_bytes: bytes, epoch: int) -> str:
    """在推特个人主页里，按相似度找出与查询图最匹配的那条推文，返回其 /status/ 链接。
    用于主页缩略图匹配失败时，改走单帖页直接取原图的兜底路径"""
    if not query_bytes:
        return ""
    try:
        imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="twimg.com/media"], img[data-src*="twimg.com/media"]')
        candidates = []  # (图片URL, 元素, 推文status链接)
        seen = set()
        for img in imgs:
            src = img.get_attribute("src") or ""
            data_src = img.get_attribute("data-src") or ""
            url = data_src if data_src.startswith("http") else src
            if not url.startswith("http"):
                continue
            base = url.split("?")[0]
            if base in seen:
                continue
            seen.add(base)
            status_url = ""
            try:
                link = img.find_element(By.XPATH, "./ancestor::a[contains(@href, '/status/')]")
                status_url = (link.get_attribute("href") or "").split("?")[0]
            except Exception:
                pass
            if not status_url:
                continue
            candidates.append((url, img, status_url))
            if len(candidates) >= 24:
                break
        best_status, best_score = "", 0
        for url, img, status_url in candidates:
            if aborted(epoch):
                raise runtime.SearchAbortedError("搜图已中止")
            try:
                loaded = False
                for _ in range(6):
                    if aborted(epoch):
                        raise runtime.SearchAbortedError("搜图已中止")
                    try:
                        if (img.get_attribute("complete") or "").lower() == "true" and int(img.get_attribute("naturalWidth") or 0) > 0:
                            loaded = True
                            break
                    except Exception:
                        pass
                    _interruptible_sleep(0.4, epoch)
                if not loaded:
                    continue
                data = img.screenshot_as_png
                if not data:
                    continue
                score = _image_similarity_crop_tolerant(query_bytes, data)
                logger.info(f"[搜图] 推文缩略图相似度 {score}/64 <- {status_url[:60]}")
                if score > best_score:
                    best_score, best_status = score, status_url
            except Exception:
                continue
        # 修复：原代码把"找到最相似推文"的返回写在了 except 里 continue 之后，永远执行不到；
        # 现在挪到循环结束后判断，主页匹配失败时可以改走单帖页取原图。
        # 阈值随 256 位 dHash 下移：弱相似图 35 分起
        if best_status and best_score >= 35:
            logger.info(f"[搜图] ✅ 找到最相似的推文: {best_status[:70]}（相似度 {best_score}/64），改走单帖页取原图")
            return best_status
        logger.info(f"[搜图] 主页推文中没有足够相似的（最高 {best_score}/64），放弃推特")
    except Exception as e:
        logger.warning(f"[搜图] 查找最相似推文失败: {e}")
    return ""


def _danbooru_original_from_sample(img_url: str) -> str:
    """Danbooru sample 图转真正的 original 直链。

    sample 文件名是 __tags__sample-<md5>.<ext>，原图文件名是 <md5>.<ext>，
    直接替换 /sample/ -> /original/ 会得到不存在的文件名，必须用 md5 重拼。
    """
    m = re.search(
        r"(https://[^/]+\.donmai\.us)/sample/([0-9a-f]{2}/[0-9a-f]{2})/"
        r".*__sample-([0-9a-f]{32})(\.[A-Za-z0-9]+)?(?:$|\?)",
        img_url,
    )
    if m:
        return f"{m.group(1)}/original/{m.group(2)}/{m.group(3)}{m.group(4) or ''}"
    # 旧式 sample：/sample/xx/yy/<md5>.<ext>
    m2 = re.search(
        r"(https://[^/]+\.donmai\.us)/sample/([0-9a-f]{2}/[0-9a-f]{2})/"
        r"([0-9a-f]{32})(\.[A-Za-z0-9]+)?(?:$|\?)",
        img_url,
    )
    if m2:
        return f"{m2.group(1)}/original/{m2.group(2)}/{m2.group(3)}{m2.group(4) or ''}"
    return ""


def _construct_original_url(img_url: str, site: str) -> str:
    """从缩略图 URL 构造原图 URL（无页面可访问时的备用方案）"""
    if site == "danbooru":
        if "preview" in img_url:
            return img_url.replace("/preview/", "/original/").replace("thumbnail_", "")
        if "large" in img_url:
            return img_url.replace("/large/", "/original/")
        if "sample" in img_url:
            return _danbooru_original_from_sample(img_url)
        # danbooru 缩略图 /180x180/xx/yy/<md5>.jpg -> 原图 /original/xx/yy/<md5>.<ext>
        # （Yandex 给的 danbooru 链接常是 180x180/360x360 缩略图）
        m = re.search(r"(https://[^/]+\.donmai\.us)/(?:180x180|360x360)/([0-9a-f]{2}/[0-9a-f]{2})/([0-9a-f]{32})(?:\.|$)", img_url)
        if m:
            return f"{m.group(1)}/original/{m.group(2)}/{m.group(3)}.png"
    elif site == "pixiv":
        if "img-master" in img_url:
            u = img_url.replace("img-master", "img-original").replace("_master1200", "")
            return re.sub(r'/c/[^/]+/', '/', u)
    elif site == "twitter":
        base = img_url.split("?")[0]
        return f"{base}?format=jpg&name=orig"
    elif site == "skebetter":
        # skebetter 的主图/缩略图都在 twimg 上，转全尺寸原图
        if "twimg.com" in img_url:
            base = img_url.split("?")[0]
            return f"{base}?format=jpg&name=orig"
    elif site == "hoyolab":
        return img_url.split("?")[0]
    elif site == "zerochan":
        if re.search(r'\.\d+\.\d+\.(?:webp|jpg|png)$', img_url):
            return re.sub(r'\.\d+\.(\d+)\.(?:webp|jpg|png)$', r'.full.\1.png', img_url)
    elif site == "rule34":
        # rule34.us 缩略图 /thumbnails/xx/yy/thumbnail_<hash>.jpg -> 原图 /images/xx/yy/<hash>.<ext>
        m = re.search(r"/thumbnails/([0-9a-f]{2}/[0-9a-f]{2})/thumbnail_([0-9a-f]{32})\.", img_url)
        if m:
            return f"https://img2.rule34.us/images/{m.group(1)}/{m.group(2)}.png"
    elif site == "bobopic":
        # BoBoPic 缩略图/查看原图链接 -> go.bobopic.com/<id> 跳转页（内含 sogoucdn 原图直链）
        m = re.search(r"go\.bobopic\.com/(\d+)", img_url)
        if m:
            return f"https://go.bobopic.com/{m.group(1)}"
        m = re.search(r"pixivdaily\.com/(?:small|tx_img)/(\d+)", img_url)
        if m:
            return f"https://go.bobopic.com/{m.group(1)}"
    elif site == "gelbooru":
        # gelbooru 缩略图 /thumbnails/xx/yy/thumbnail_<md5>.jpg -> 原图 /images/xx/yy/<md5>.<ext>
        # （img 子域可能省略，Yandex 给的链接常是裸 gelbooru.com）
        m = re.search(r"(https://(?:img\d+\.)?gelbooru\.com)/thumbnails/([0-9a-f]{2}/[0-9a-f]{2})/thumbnail_([0-9a-f]{32})(?:\.|$)", img_url)
        if m:
            return f"{m.group(1)}/images/{m.group(2)}/{m.group(3)}.png"
        # sample 图 -> 原图
        m = re.search(r"(https://(?:img\d+\.)?gelbooru\.com)/samples?/([0-9a-f]{2}/[0-9a-f]{2})/sample_([0-9a-f]{32})(?:\.|$)", img_url)
        if m:
            return f"{m.group(1)}/images/{m.group(2)}/{m.group(3)}.png"
    return ""


def _find_best_image_on_page(driver, site: str, query_bytes: bytes, epoch: int, min_score: int = 20, deadline: float = None) -> str:
    """在页面（个人主页/帖子列表等）的所有图里，按相似度找出与查询图最匹配的那张，返回其原图URL。
    min_score：最相似图的最低相似度门槛（默认 20 分，弱匹配交给视觉模型最终判断）"""
    if not query_bytes:
        return ""
    def _deadline_hit():
        return deadline is not None and time.time() >= deadline
    if _deadline_hit():
        logger.info("[搜图] 页面找图已超过单站预算，直接跳过")
        return ""
    try:
        if site == "twitter":
            media_selector = ('img[src*="twimg.com/media"], img[data-src*="twimg.com/media"], '
                              'img[src*="tweet_video_thumb"], img[src*="ext_tw_video_thumb"], '
                              'video[poster*="twimg.com"], video[data-poster*="twimg.com"]')
        else:
            media_selector = 'img[src*="http"], img[data-src*="http"]'
        candidates = []
        seen = set()

        def _collect_candidates():
            """收集当前视口内的图片候选（去重、过滤图标/头像）"""
            nonlocal candidates, seen
            try:
                imgs = driver.find_elements(By.CSS_SELECTOR, media_selector)
            except Exception:
                return
            for img in imgs:
                if len(candidates) >= max_candidates:
                    return
                try:
                    if img.tag_name.lower() == "video":
                        # 视频元素用封面 poster 作为图片 URL
                        src = img.get_attribute("poster") or img.get_attribute("data-poster") or ""
                        data_src = ""
                    else:
                        src = img.get_attribute("src") or ""
                        data_src = img.get_attribute("data-src") or ""
                    url = data_src if data_src.startswith("http") else src
                except Exception:
                    continue
                if not url.startswith("http"):
                    continue
                if _is_decoration_url(url):
                    continue
                base = url.split("?")[0]
                if base in seen:
                    continue
                seen.add(base)
                candidates.append((url, img))

        # 关键：先收集顶部视口的图，再分步向下滚动、每步都收集累加。
        # 不能一次性滚到底再收集——X 时间线是虚拟化的，滚动后顶部已加载的图片会被
        # 卸载出 DOM（这就是"主页有图却提取到 0 张"的根因）。
        max_candidates = 24 if site == "twitter" else 15  # 非推特页面少截几张，避免太慢
        _collect_candidates()
        for _ in range(4):
            if len(candidates) >= max_candidates or aborted(epoch) or _deadline_hit():
                break
            try:
                driver.execute_script("window.scrollBy(0, window.innerHeight);")
            except Exception:
                pass
            _interruptible_sleep(0.6, epoch)
            _collect_candidates()
        _interruptible_sleep(0.5, epoch)
        if not candidates:
            logger.info("[搜图] 页面里没有可匹配的图片")
            return ""
        best_url = ""
        best_score = 0
        # 优先用浏览器里已加载图片的元素截图对比：
        # 页面本来就打开了、图片已经渲染好，不用再走 httpx 重新下载（twimg 等走代理很慢，
        # 6 秒超时会把目标图漏掉，这就是"图在主页前10张却没识别到"的根因）
        for url, img in candidates:
            if aborted(epoch):
                raise runtime.SearchAbortedError("搜图已中止")
            if _deadline_hit():
                logger.info("[搜图] 页面截图匹配达到单站预算，提前结束")
                break
            try:
                # 等图片真正加载出来（懒加载的图可能还没渲染）
                loaded = False
                for _ in range(6):
                    if aborted(epoch):
                        raise runtime.SearchAbortedError("搜图已中止")
                    if _deadline_hit():
                        break
                    try:
                        if (img.get_attribute("complete") or "").lower() == "true" and int(img.get_attribute("naturalWidth") or 0) > 0:
                            loaded = True
                            break
                    except Exception:
                        pass
                    _interruptible_sleep(0.4, epoch)
                if _deadline_hit():
                    break
                if not loaded:
                    continue
                data = img.screenshot_as_png
                if not data:
                    continue
                if _is_placeholder_small(data):
                    continue
                score = _image_similarity_crop_tolerant(query_bytes, data)
                logger.info(f"[搜图] 页面截图相似度 {score}/64 <- {url[:70]}")
                if score > best_score:
                    best_score = score
                    best_url = url
            except Exception:
                continue
        # httpx 兜底：用页面图片直链下载后重新比对
        # - 其他站点：仅截图最高分低于 50 时兜底；
        # - hoyolab：必须始终以"下载字节"的分数为准——页面截图分数不可靠
        #   （装饰图截图会虚高到 51 分、真实图截图反而偏低，曾导致选错图）
        if best_score < 50 or site == "hoyolab":
            import httpx
            # 兜底只查前 15 张（目标图一般在前 10 张；太多会拖慢整体搜图）
            download_best_url, download_best_score = "", 0
            for url, _ in candidates[:15]:
                if aborted(epoch):
                    raise runtime.SearchAbortedError("搜图已中止")
                if _deadline_hit():
                    logger.info("[搜图] 页面 httpx 兜底达到单站预算，提前结束")
                    break
                try:
                    is_china = any(d in url for d in ['qpic.cn', 'qq.com', 'baidu.com', 'aliyuncs.com', 'qiniucdn.com'])
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                        "Referer": config._SITE_REFERERS.get(site, "https://www.google.com/"),
                    }
                    request_timeout = 25
                    if deadline is not None:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.info("[搜图] 页面 httpx 兜底达到单站预算，提前结束")
                            break
                        request_timeout = max(1.0, min(25, remaining))
                    with httpx.Client(
                        proxy=config.PROXY_URL if config.PROXY_URL and not is_china else None,
                        headers=headers, timeout=request_timeout, follow_redirects=True
                    ) as client:
                        resp = client.get(url)
                        data = resp.content if resp.status_code == 200 and resp.content else None
                    if not data:
                        continue
                    if _is_placeholder_small(data):
                        continue
                    score = _image_similarity_crop_tolerant(query_bytes, data)
                    if score > download_best_score:
                        download_best_score, download_best_url = score, url
                except Exception:
                    continue
            if site == "hoyolab":
                # hoyolab 直接以下载字节比对结果为准（截图分数不可靠）
                best_url, best_score = download_best_url, download_best_score
            elif download_best_score > best_score:
                best_score, best_url = download_best_score, download_best_url
        if best_score < min_score or not best_url:
            logger.info(f"[搜图] 页面里没找到足够相似的目标图（最高 {best_score}/64），跳过该网站")
            return ""
        # 转成原图尺寸
        if "twimg.com" in best_url:
            best_url = best_url.split("?")[0] + "?format=jpg&name=orig"
        else:
            constructed = _construct_original_url(best_url, site)
            if constructed:
                best_url = constructed
        logger.info(f"[搜图] ✅ 在页面中匹配到目标图 [{site}] 相似度{best_score}/64: {best_url[:80]}")
        return best_url
    except Exception as e:
        logger.warning(f"[搜图] 页面找图失败: {e}")
        return ""


def _pixiv_original_url_by_pid(pid: str) -> str:
    """通过 pixiv ajax 接口按作品 ID 获取原图直链（公开作品未登录也可用），失败返回 ''。
    用于 BoBoPic 直链拿不到真图时的兜底：BoBoPic 的站内 ID 就是 pixiv 作品号"""
    import httpx
    try:
        api_url = f"https://www.pixiv.net/ajax/illust/{pid}"
        with httpx.Client(
            proxy=config.PROXY_URL if config.PROXY_URL else None,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Referer": "https://www.pixiv.net/",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=12, follow_redirects=True
        ) as client:
            resp = client.get(api_url)
            if resp.status_code == 200:
                data = resp.json()
                orig = ((data.get("body") or {}).get("urls") or {}).get("original")
                if orig:
                    logger.info(f"[搜图] pixiv ajax 获取到作品 {pid} 原图直链: {orig[:80]}")
                    return orig
            else:
                logger.warning(f"[搜图] pixiv ajax 返回状态码 {resp.status_code} (pid={pid})")
    except Exception as e:
        logger.warning(f"[搜图] pixiv ajax 获取作品 {pid} 原图失败: {e}")
    return ""


# 记录 BoBoPic 候选直链对应的作品 ID（sogoucdn 直链临时失败时，用它重新走一遍查看页会话）
_bobopic_candidate_pids = {}
# BoBoPic 页面比对阶段已下载的原图字节缓存（pid -> bytes），避免搜索循环重复下载
_bobopic_bytes_cache = {}


def _bobopic_candidates_with_pixiv_fallback(bobopic_urls: list, pid: str) -> list:
    """BoBoPic 直链候选列表 + pixiv ajax 原图兜底，去重后返回"""
    urls = list(bobopic_urls or [])
    for u in urls:
        _bobopic_candidate_pids[u] = pid
    pixiv_url = _pixiv_original_url_by_pid(pid)
    if pixiv_url and pixiv_url not in urls:
        urls.append(pixiv_url)
    return urls


def _bobopic_pid_of(url: str) -> str:
    """查询某条 BoBoPic 直链对应的作品 ID"""
    return _bobopic_candidate_pids.get(url, "")


def _bobopic_download_bytes(pid: str) -> bytes:
    """重新走一遍 BoBoPic 查看页会话流程并直接下载原图字节。
    sogoucdn 直链有时效性/会被临时限流，用新会话重新拿链接再下载成功率更高。
    成功返回图片字节（已校验为有效图片），失败返回 None"""
    # 页面比对阶段如果已经下载过这张图，直接复用，不再重复下载
    cached = _bobopic_bytes_cache.get(pid)
    if cached:
        logger.info(f"[搜图] BoBoPic 复用比对阶段已下载原图[{pid}] {len(cached)} 字节")
        return cached
    import httpx
    try:
        with httpx.Client(
            proxy=None,  # 国内站点直连
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
            timeout=15, follow_redirects=True
        ) as client:
            resp = client.get(f"https://go.bobopic.com/{pid}")
            if resp.status_code != 200:
                return None
            m = re.search(r'id="main-img"[^>]*src="([^"]+)"', resp.text)
            if not m:
                return None
            img_src = m.group(1).replace("&amp;", "&")
            r = client.get(img_src)
            if r.status_code == 200 and r.content:
                from io import BytesIO
                from PIL import Image
                img = Image.open(BytesIO(r.content))
                img.load()
                logger.info(f"[搜图] ✅ BoBoPic 重试下载原图成功[{pid}] 大小: {len(r.content)} 字节")
                return r.content
    except Exception as e:
        logger.warning(f"[搜图] BoBoPic 重试下载原图失败[{pid}]: {e}")
    return None


def _browser_fetch_pixiv_original(driver, pximg_url: str, epoch: int) -> bytes:
    """从 pixiv 作品页上下文抓取 pximg 原图。

    直接打开 pximg 直链会因缺少 pixiv 的 Referer/登录态被 CDN 拒绝（只返回 48x48 占位图），
    httpx 直连也会被代理对 pximg 的 TLS 拦截。正确姿势：先打开作品页让浏览器带上
    pixiv 的 Referer 和 cookies，再在页面上下文里 fetch 原图。失败返回 b''。"""
    import base64
    try:
        m = re.search(r'/(\d+)_p\d+\.', pximg_url)
        if not m:
            logger.warning(f"[搜图] 无法从 pximg URL 解析作品 ID: {pximg_url[:60]}...")
            return b""
        pid = m.group(1)
        # 复用第一个标签页打开 pixiv 作品页（Lens 结果页已经提取完毕，可以离开）
        try:
            browser.minimized_navigate(driver, lambda: driver.switch_to.window(driver.window_handles[0]))
        except Exception:
            pass
        browser.minimized_navigate(driver, lambda: driver.get(f"https://www.pixiv.net/artworks/{pid}"))
        browser.keep_window_minimized(driver)
        try:
            # 不依赖整页加载完：导航到 pixiv 域后 Referer 就已正确，fetch 可在加载中发起
            WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
        except Exception:
            pass
        _interruptible_sleep(1, epoch)
        # 在作品页上下文里 fetch 原图（浏览器自动带上 pixiv 的 Referer 和 cookies）
        # 用 AbortController 限时 8 秒，避免 pximg 不可达时挂到脚本超时
        base64_data = driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), 8000);
            fetch(arguments[0], {credentials: 'include', signal: ctrl.signal})
                .then(r => { clearTimeout(timer); if (!r.ok) throw new Error('bad status'); return r.arrayBuffer(); })
                .then(buf => {
                    let bin = '';
                    const bytes = new Uint8Array(buf);
                    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                    done(btoa(bin));
                })
                .catch(e => { clearTimeout(timer); done(''); });
        """, pximg_url)
        if base64_data:
            candidate = base64.b64decode(base64_data)
            # 过滤 CDN 拒绝时返回的 48x48 占位图
            if _looks_like_image(candidate) and len(candidate) > 10000:
                logger.info(f"[搜图] ✅ 从 pixiv 作品页上下文抓取原图成功[{pid}] 大小: {len(candidate)} 字节")
                return candidate
            logger.warning(f"[搜图] pixiv 作品页上下文拿到的图太小或无效[{pid}]（{len(candidate)} 字节），疑似被 CDN 拒绝")
        else:
            logger.warning(f"[pixiv] page-context fetch failed[{pid}] (403/timeout/empty): {pximg_url[:60]}...")
    except Exception as e:
        logger.warning(f"[搜图] pixiv 作品页上下文抓图失败[{pximg_url[:60]}]: {e}")
    return b""



def _fetch_danbooru_post_api(post_id: str, timeout: float = 10.0) -> dict:
    """使用合规 API User-Agent 直调 Danbooru JSON 接口，绕过页面 Cloudflare Turnstile 质询。"""
    if not post_id or not str(post_id).isdigit():
        return {}
    import httpx
    try:
        with httpx.Client(
            proxy=config.PROXY_URL if config.PROXY_URL else None,
            headers={
                "User-Agent": "FurinaBot/2.0 (by FurinaQQBot)",
                "Accept": "application/json",
            },
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            resp = client.get(f"https://danbooru.donmai.us/posts/{post_id}.json")
            if resp.status_code == 200:
                data = resp.json()
                return (data.get("post") if isinstance(data, dict) else None) or (data if isinstance(data, dict) else {})
    except Exception as e:
        logger.debug(f"[搜图] Danbooru REST API 获取帖子 {post_id} 失败: {e}")
    return {}


def _browser_fetch_danbooru_original(driver, file_url: str, post_url: str, epoch: int, deadline: float = None) -> bytes:
    """从 Danbooru 帖子页上下文抓取 cdn.donmai.us 原图。

    直接在新标签页打开 cdn.donmai.us 直链会丢失 danbooru.donmai.us 的 Referer/cookie 上下文，
    容易遇到 Cloudflare 403 或 404。正确姿势：保持在帖子页，用页面上下文 fetch 原图，
    浏览器自动带上 danbooru 域的 Referer 和 cookies。失败返回 b''。
    """
    import base64
    try:
        if deadline is not None and time.time() >= deadline:
            logger.info("[搜图] Danbooru 抓图已超过单站预算，跳过")
            return b""
        # 确保仍在帖子页；如果之前被切走则切回
        current = (driver.current_url or "")
        if post_url not in current:
            try:
                browser.minimized_navigate(driver, lambda: driver.get(post_url))
                browser.keep_window_minimized(driver)
                try:
                    wait_seconds = 10.0
                    if deadline is not None:
                        wait_seconds = max(1.0, min(10.0, deadline - time.time()))
                    WebDriverWait(driver, wait_seconds).until(lambda d: d.execute_script("return document.readyState") == "complete")
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"[搜图] 切回 Danbooru 帖子页失败: {e}")
                return b""
        # 在帖子页上下文里 fetch 原图（浏览器自动带上 danbooru 的 Referer 和 cookies）
        fetch_timeout_ms = 12000
        if deadline is not None:
            remaining_ms = int((deadline - time.time()) * 1000)
            fetch_timeout_ms = max(1000, min(12000, remaining_ms))
        base64_data = driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), arguments[1]);
            fetch(arguments[0], {credentials: 'include', signal: ctrl.signal})
                .then(r => { clearTimeout(timer); if (!r.ok) throw new Error('bad status:' + r.status); return r.arrayBuffer(); })
                .then(buf => {
                    let bin = '';
                    const bytes = new Uint8Array(buf);
                    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                    done(btoa(bin));
                })
                .catch(e => { clearTimeout(timer); done('ERR:' + (e.message || e.name || 'unknown')); });
        """, file_url, fetch_timeout_ms)
        if base64_data:
            if isinstance(base64_data, str) and base64_data.startswith("ERR:"):
                logger.info(f"[搜图] Danbooru 帖子页 fetch 失败: {base64_data}，尝试 CF 边缘隧道直取")
                from .download import _download_donmai_cdn_via_cf_edge
                cf_data = _download_donmai_cdn_via_cf_edge(file_url, timeout=15.0)
                if cf_data and _looks_like_image(cf_data) and not _is_placeholder_small(cf_data):
                    return cf_data
                return b""
            candidate = base64.b64decode(base64_data)
            if _looks_like_image(candidate) and len(candidate) > 1000:
                if _is_placeholder_small(candidate):
                    logger.warning(f"[搜图] Danbooru 帖子页上下文拿到疑似占位图（{len(candidate)} 字节），丢弃")
                    return b""
                logger.info(f"[搜图] ✅ 从 Danbooru 帖子页上下文抓取原图成功 大小: {len(candidate)} 字节")
                return candidate
            logger.warning(f"[搜图] Danbooru 帖子页上下文拿到的图无效（{len(candidate)} 字节）")
    except Exception as e:
        logger.warning(f"[搜图] Danbooru 帖子页上下文抓图失败: {e}")
    return b""


def _hoyolab_click_through_images(driver, query_bytes: bytes, epoch: int, min_score: int = 40) -> str:
    """HoYoLAB 多图帖：点击上方缩略图切换大图，逐张比对，找到目标图返回其大图 URL。

    HoYoLAB 多图帖在页面上只显示一张大图（或打开灯箱），其余是小图缩略图，必须点击缩略图
    才会切换大图；直接匹配缩略图拿到的 URL 是缩略图尺寸，下载后相似度会掉（曾出现页面截图
    50/64、下载后仅 32/64 被拒的情况）。
    兼容两种交互：点击缩略图后 ① 页面上内联切换大图；② 打开 hoyolab-image-viewer 灯箱，
    此时在灯箱里用"下一张"翻页评估。"""
    from selenium.webdriver.common.keys import Keys
    try:
        hoyolab_sel = ('img[src*="hoyolab.com"], img[src*="mihoyo.com"], img[src*="hoyoverse.com"], '
                       'img[data-src*="hoyolab.com"], img[data-src*="mihoyo.com"]')
        viewer_sel = '[class*="hoyolab-image-viewer"], [class*="viewer-container"], [class*="image-viewer"]'

        def _visible_rect(img):
            try:
                return driver.execute_script("""
                    const r = arguments[0].getBoundingClientRect();
                    const s = getComputedStyle(arguments[0]);
                    if (s.display === 'none' || s.visibility === 'hidden' || r.width < 5 || r.height < 5) return null;
                    return {w: r.width, h: r.height};
                """, img)
            except Exception:
                return None

        def _current_large():
            """当前页面上渲染面积最大的可见图片（即大图）"""
            best, best_area = None, 0
            for img in driver.find_elements(By.CSS_SELECTOR, hoyolab_sel):
                try:
                    if _is_decoration_url(_img_url(img)):
                        continue
                except Exception:
                    pass
                rect = _visible_rect(img)
                if not rect:
                    continue
                area = rect["w"] * rect["h"]
                if area > best_area:
                    best_area, best = area, img
            return best

        def _img_url(img):
            try:
                return (img.get_attribute("currentSrc")
                        or img.get_attribute("data-src")
                        or img.get_attribute("src") or "").split("?")[0]
            except Exception:
                return ""

        def _score_img(img):
            # 等图片加载完成
            for _ in range(6):
                if aborted(epoch):
                    raise runtime.SearchAbortedError("搜图已中止")
                try:
                    if (img.get_attribute("complete") or "").lower() == "true" and int(img.get_attribute("naturalWidth") or 0) > 0:
                        break
                except Exception:
                    pass
                _interruptible_sleep(0.4, epoch)
            try:
                data = img.screenshot_as_png
                if not data:
                    return -1
                return _image_similarity_crop_tolerant(query_bytes, data)
            except Exception:
                return -1

        def _find_container():
            node = _current_large()
            if node is None:
                return None
            for _ in range(8):
                try:
                    node = node.find_element(By.XPATH, "..")
                except Exception:
                    return None
                try:
                    if len(node.find_elements(By.CSS_SELECTOR, hoyolab_sel)) >= 2:
                        return node
                except Exception:
                    return None
            return None

        def _dismiss_overlays():
            """关掉 HoYoLAB 可能弹出的登录浮层（hyv-account-frame），避免挡点击"""
            try:
                has = driver.execute_script(
                    "return !!document.querySelector('#hyv-account-frame, [src*=\"login-platform\"], [class*=\"login-popup\"]');"
                )
                if not has:
                    return
                ok = driver.execute_script("""
                    // 只关登录浮层自己的关闭按钮；不能用 [data-viewer-action="close"]，
                    // 否则会把刚打开的图片灯箱当成浮层关掉
                    const el = document.querySelector('[class*="login-popup"] [class*="close"], [class*="hyv-login"] [class*="close"]');
                    if (el) { el.click(); return true; }
                    return false;
                """)
                if not ok:
                    try:
                        from selenium.webdriver.common.action_chains import ActionChains
                        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    except Exception:
                        pass
                    _interruptible_sleep(0.5, epoch)
                # 还在的话直接禁用覆盖层，避免拦截后续 JS 点击
                driver.execute_script("""
                    const f = document.querySelector('#hyv-account-frame, [src*="login-platform"]');
                    if (f) { f.style.pointerEvents = 'none'; f.style.visibility = 'hidden'; }
                """)
            except Exception:
                pass

        def _find_viewer():
            try:
                return driver.find_element(By.CSS_SELECTOR, viewer_sel)
            except Exception:
                return None

        def _viewer_active_img(viewer):
            """灯箱里当前显示的大图（可见且渲染面积最大的 img）"""
            best, ba = None, 0
            for img in viewer.find_elements(By.TAG_NAME, "img"):
                try:
                    r = driver.execute_script("""
                        const r = arguments[0].getBoundingClientRect();
                        const s = getComputedStyle(arguments[0]);
                        if (s.display === 'none' || s.visibility === 'hidden' || r.width < 5 || r.height < 5) return null;
                        return {w: r.width, h: r.height};
                    """, img)
                    if not r:
                        continue
                    a = r["w"] * r["h"]
                    if a > ba:
                        ba, best = a, img
                except Exception:
                    continue
            return best

        def _click_viewer_next():
            try:
                return driver.execute_script("""
                    const b = document.querySelector('[data-viewer-action="next"]');
                    if (b && !(b.className || '').toString().includes('disabled')) { b.click(); return true; }
                    return false;
                """)
            except Exception:
                return False

        def _close_viewer():
            try:
                driver.execute_script("""
                    const b = document.querySelector('[data-viewer-action="close"]');
                    if (b) { b.click(); return; }
                """)
            except Exception:
                pass
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            except Exception:
                pass
            _interruptible_sleep(0.3, epoch)

        # 0. 页面懒加载：先滚动几屏触发图片渲染，否则下方缩略图不在 DOM 里收集不到
        for _ in range(5):
            if aborted(epoch):
                raise runtime.SearchAbortedError("搜图已中止")
            try:
                driver.execute_script("window.scrollBy(0, window.innerHeight);")
            except Exception:
                pass
            _interruptible_sleep(0.5, epoch)
        try:
            driver.execute_script("window.scrollTo(0, 0);")
        except Exception:
            pass
        _interruptible_sleep(0.5, epoch)

        # 1. 先评估当前大图（单图帖或目标就是第一张时直接命中，不用点击）
        _dismiss_overlays()
        cur = _current_large()
        if cur is not None:
            s0 = _score_img(cur)
            logger.info(f"[搜图] HoYoLAB 当前大图相似度 {s0}/64 <- {_img_url(cur)[:60]}")
            # 当前大图用严格门槛（50 分"确认同一张图"）：
            # 40~49 分的宽松匹配会把"另一张画风相近的图"误判为命中，从而不再点击缩略图
            if s0 >= config.SIM_GATE:
                return _img_url(cur)

        # 2. 定位文章内容容器（从大图向上找包含多张图片的容器），避免点到右侧推荐列表
        container = _find_container()
        if container is None:
            logger.info("[搜图] HoYoLAB 未找到多图容器，跳过点击流程")
            return ""

        # 3. 收集容器内的小图（缩略图）URL，逐个截图打分，选最像目标的那张
        def _collect_thumb_urls():
            urls = []
            seen = set()
            for img in container.find_elements(By.CSS_SELECTOR, hoyolab_sel):
                if aborted(epoch):
                    raise runtime.SearchAbortedError("搜图已中止")
                try:
                    url = _img_url(img)
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    if _is_decoration_url(url):
                        continue
                    rect = _visible_rect(img)
                    # 内容图（缩略图/帖子图）：排除头像/图标等过小的图。
                    # 注意不能按绝对尺寸上限过滤——真实窗口下帖子内容图可能渲染到 700px+ 宽，
                    # 之前按 <500x500 过滤导致真实环境里一张都收集不到
                    if rect and rect["w"] * rect["h"] >= 60 * 60:
                        urls.append(url)
                except Exception:
                    continue
            return urls

        thumb_urls = _collect_thumb_urls()
        if not thumb_urls:
            logger.info("[搜图] HoYoLAB 页面上没找到可点击的缩略图")
            return ""

        best_thumb_url, best_thumb_score = "", 0
        for url in thumb_urls:
            if aborted(epoch):
                raise runtime.SearchAbortedError("搜图已中止")
            target = None
            try:
                for img in container.find_elements(By.CSS_SELECTOR, hoyolab_sel):
                    if _img_url(img) == url:
                        target = img
                        break
            except Exception:
                    pass
            if target is None:
                continue
            score = _score_img(target)
            logger.info(f"[搜图] HoYoLAB 缩略图相似度 {score}/64 <- {url[:60]}")
            if score > best_thumb_score:
                best_thumb_score, best_thumb_url = score, url

        if best_thumb_score < min_score or not best_thumb_url:
            logger.info(f"[搜图] HoYoLAB 没有足够相似的缩略图（最高 {best_thumb_score}/64），跳过该网站")
            return ""

        # 4. 点击最像的那张缩略图，取出它对应的大图/灯箱 URL
        #    （缩略图 URL 剥离参数后是缩略图尺寸，下载后相似度会掉，必须取切换后的大图）
        logger.info(f"[搜图] HoYoLAB 点击最相似缩略图（{best_thumb_score}/64）: {best_thumb_url[:60]}")
        page_url = driver.current_url or ""
        target = None
        try:
            for img in container.find_elements(By.CSS_SELECTOR, hoyolab_sel):
                if _img_url(img) == best_thumb_url and _visible_rect(img):
                    target = img
                    break
        except Exception:
            pass
        if target is None:
            return ""
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", target)
        except Exception as e:
            logger.warning(f"[搜图] HoYoLAB 点击缩略图失败: {e}")
            return ""
        _interruptible_sleep(0.8, epoch)
        _dismiss_overlays()
        # 防止缩略图是 <a> 包裹导致点击后跳页
        try:
            if page_url and driver.current_url != page_url:
                logger.info("[搜图] HoYoLAB 点击缩略图跳页了，返回原页面")
                browser.minimized_navigate(driver, lambda: driver.get(page_url))
                browser.keep_window_minimized(driver)
                _interruptible_sleep(2, epoch)
                return ""
        except Exception:
            pass
        # 灯箱模式：点击缩略图后灯箱可能停在第一张而不是点击的那张，
        # 需要在灯箱里翻页，按 URL 找到与最佳缩略图同一张的 slide 再取完整 URL
        viewer = _find_viewer()
        if viewer is not None:
            for _ in range(12):
                img = _viewer_active_img(viewer)
                if img is not None:
                    url = _img_url(img)
                    if url and url.rstrip("/").split("?")[0] == best_thumb_url.rstrip("/").split("?")[0]:
                        logger.info(f"[搜图] HoYoLAB 灯箱取到目标大图 URL: {url[:80]}")
                        return url
                if not _click_viewer_next():
                    break
                _interruptible_sleep(0.5, epoch)
                viewer = _find_viewer()
                if viewer is None:
                    break
            _close_viewer()
            return ""
        # 内联模式：取点击后页面上切换出来的大图
        cur2 = _current_large()
        if cur2 is not None:
            url = _img_url(cur2)
            if url and url.rstrip("/").split("?")[0] == best_thumb_url.rstrip("/").split("?")[0]:
                logger.info(f"[搜图] HoYoLAB 内联大图 URL: {url[:80]}")
                return url
        return ""
    except Exception as e:
        logger.warning(f"[搜图] HoYoLAB 点击缩略图流程失败: {e}")
        return ""


def extract_original_image(driver, url: str, site_name: str, epoch: int, query_bytes: bytes = None, deadline: float = None) -> list:
    """从网站页面提取原图 URL（query_bytes 用于在个人主页/帖子列表里按相似度找目标图）"""
    # ========== Danbooru：先访问页面取中间图（#image），API 只做兜底 ==========
    api_file_url = None
    if site_name == "danbooru":
        # 只有"单帖页"才值得处理；标签/列表页（点进去是一大堆图的网格）直接跳过，
        # 不做 API/页面/相似度匹配/下载等任何操作，交给下一个网站
        if not re.search(r'/posts/\d+', url):
            logger.info(f"[搜图] Danbooru 页面不是单帖页（标签/列表页），直接跳过: {url[:90]}")
            return []
        try:
            # 从 URL 里提取 post ID
            match = re.search(r'/posts/(\d+)', url)
            if match:
                post_id = match.group(1)
                api_url = f"https://danbooru.donmai.us/posts/{post_id}.json"

                logger.info(f"[搜图] 尝试 Danbooru API 获取原图: {post_id}")

                # 用 httpx 调用 API，走代理（注意新版 httpx 参数是 proxy=，不是 proxies=）
                import httpx
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                # 把浏览器里保存的 Danbooru cookies 带上，减少需要登录的帖子被 403
                cookie_str = load_danbooru_cookie_header()
                if cookie_str:
                    headers["Cookie"] = cookie_str
                with httpx.Client(proxy=config.PROXY_URL if config.PROXY_URL else None, headers=headers, timeout=10, follow_redirects=True) as client:
                    response = client.get(api_url)
                    logger.info(f"[搜图] Danbooru API 状态码: {response.status_code}")
                    if response.status_code == 200:
                        data = response.json()
                        file_url = data.get("file_url")
                        if file_url:
                            # 处理相对 URL
                            if file_url.startswith("//"):
                                file_url = "https:" + file_url
                            logger.info(f"[搜图] Danbooru API 获取到原图URL（作为兜底）: {file_url[:80]}")
                            api_file_url = file_url
                    else:
                        logger.warning(f"[搜图] Danbooru API 返回错误: {response.text[:200]}")
        except Exception as e:
            logger.warning(f"[搜图] Danbooru API 失败， fallback 到网页: {e}")

    # ========== Skebetter：只处理单帖页（URL 含 /illust/<数字>），标签/列表页直接跳过 ==========
    if site_name == "skebetter" and not re.search(r'/illust/\d+', url):
        logger.info(f"[搜图] Skebetter 页面不是单帖页（标签/列表页），直接跳过: {url[:90]}")
        return []

    try:
        page_deadline = deadline if deadline is not None else time.time() + config.SITE_VISIT_SECONDS

        def _extract_deadline_hit():
            return time.time() >= page_deadline

        if _extract_deadline_hit():
            logger.info(f"[搜图] 提取 {site_name} 页面已超过单站预算，直接跳过")
            return []

        # 设置页面加载超时（30秒，给足加载时间；zerochan 页面加载慢，35 秒后直接进 DOM 提取）
        driver.set_page_load_timeout(35 if site_name == "zerochan" else 30)

        # 同一标签页内直接导航（不再每个站点新开/关闭标签页，减少标签页堆积；
        # Lens 结果页此时已提取完毕，离开没有影响，搜完后会由预加载逻辑切回上传页）
        browser.minimized_navigate(driver, lambda: driver.get(url))
        browser.keep_window_minimized(driver)

        logger.info(f"[搜图] 访问 {site_name} 页面: {url[:80]}...")
        # none 页面加载策略下，新标签页可能还停在 about:blank（其 readyState 已经是 complete），
        # 必须等到地址变成目标网站再继续，否则后面的提取会落在空页面上
        try:
            from urllib.parse import urlparse
            target_host = urlparse(url).netloc
            if target_host:
                WebDriverWait(driver, 25).until(lambda d: target_host in (d.current_url or ""))
        except Exception:
            pass
        try:
            if site_name == "zerochan":
                # zerochan 经常加载不完，不等 complete，readyState 到 interactive 就够提取了
                WebDriverWait(driver, 8).until(
                    lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
                )
            else:
                WebDriverWait(driver, 25).until(lambda d: d.execute_script("return document.readyState") == "complete")
        except Exception:
            pass
        _interruptible_sleep(0.5 if site_name == "zerochan" else 1.5, epoch)
        if _extract_deadline_hit():
            logger.info(f"[搜图] 提取 {site_name} 页面已超过单站预算，直接跳过")
            return []

        # 检测是否遇到人机验证页面
        is_challenge = False
        try:
            page_title = driver.title.lower()
            page_text = driver.find_element(By.TAG_NAME, "body").text.lower()

            # 常见的验证页面关键词
            challenge_keywords = [
                "just a moment", "checking your browser",
                "captcha", "recaptcha", "turnstile",
                "验证", "人机验证", "安全验证",
                "cloudflare", "attention required"
            ]

            for keyword in challenge_keywords:
                if keyword in page_title or keyword in page_text:
                    is_challenge = True
                    logger.info(f"[搜图] 检测到验证页面: {keyword}")
                    break
        except Exception:
            pass

        # 如果遇到验证，等待并尝试绕过
        if is_challenge:
            logger.info("[搜图] 遇到人机验证，尝试自动通过...")

            def _challenge_cleared():
                try:
                    t = (driver.title or "").lower()
                    if any(k in t for k in ("just a moment", "请稍候", "正在验证", "attention required", "安全验证", "人机验证", "checking your browser")):
                        return False
                    return True
                except Exception:
                    return False

            def _try_click_checkbox():
                """切进 Cloudflare Turnstile iframe 点击复选框（比在外层点 iframe 更可靠）"""
                try:
                    iframes = driver.find_elements(
                        By.CSS_SELECTOR,
                        "iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile'], "
                        "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], "
                        "iframe[title*='验证'], iframe[title*='challenge'], iframe[title*='recaptcha'], iframe[title*='hcaptcha']"
                    )
                    for iframe in iframes:
                        try:
                            driver.switch_to.frame(iframe)
                            boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox'], [role='checkbox'], [class*='challenge'] button")
                            for box in boxes:
                                try:
                                    if box.is_displayed():
                                        box.click()
                                        logger.info("[搜图] 已点击验证复选框")
                                        return True
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        finally:
                            try:
                                driver.switch_to.default_content()
                            except Exception:
                                pass
                except Exception:
                    pass
                return False

            # 最多 2 轮：点击复选框 + 轮询等待通过（Cloudflare 有时会自动过，最长等 15 秒），失败则刷新重试
            for attempt in range(2):
                if aborted(epoch):
                    raise runtime.SearchAbortedError("搜图已中止")
                if _extract_deadline_hit():
                    logger.info("[搜图] 提取页面验证等待达到单站预算，提前跳过")
                    return []
                _interruptible_sleep(2, epoch)
                _try_click_checkbox()
                for _ in range(15):
                    if aborted(epoch):
                        raise runtime.SearchAbortedError("搜图已中止")
                    if _extract_deadline_hit():
                        logger.info("[搜图] 提取页面验证等待达到单站预算，提前跳过")
                        return []
                    if _challenge_cleared():
                        break
                    _interruptible_sleep(1, epoch)
                if _challenge_cleared():
                    logger.info("[搜图] 人机验证通过")
                    break
                logger.info(f"[搜图] 人机验证仍未通过（第{attempt + 1}次），刷新页面重试...")
                try:
                    browser.minimized_navigate(driver, lambda: driver.refresh())
                    browser.keep_window_minimized(driver)
                except Exception:
                    pass
                _interruptible_sleep(3, epoch)

            # 模拟滚动页面，更像真实用户
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                _interruptible_sleep(1, epoch)
                driver.execute_script("window.scrollTo(0, 0);")
                _interruptible_sleep(1, epoch)
            except Exception:
                pass

        if site_name == "danbooru":
            candidates = []
            api_json = ""
            has_authoritative = False
            _m_pid = re.search(r"/posts/(\d+)", url)
            if _m_pid:
                _rest_post = _fetch_danbooru_post_api(_m_pid.group(1))
                for _k in ("file_url", "large_file_url"):
                    _fu = (_rest_post or {}).get(_k) or ""
                    if _fu:
                        if _fu.startswith("//"):
                            _fu = "https:" + _fu
                        if _fu not in candidates:
                            candidates.append(_fu)
                        if _k == "file_url":
                            has_authoritative = True
                            logger.info(f"[搜图] Danbooru REST API 直接获取到原图URL: {_fu[:80]}")
            # Cloudflare 验证有时要几秒才通过、帖子内容才渲染出来：先等 #image 或 data-file-url 出现，
            # 等不到就刷新一次再等（页面没渲染出内容时提取会拿到空候选）
            for _ in range(2):
                if _extract_deadline_hit():
                    logger.info("[搜图] Danbooru 等待帖子内容达到单站预算，提前跳过")
                    return []
                try:
                    WebDriverWait(driver, 15).until(
                        lambda d: d.find_elements(By.CSS_SELECTOR, "#image") or d.find_elements(By.CSS_SELECTOR, "[data-file-url]")
                    )
                    break
                except Exception:
                    if _ == 0:
                        logger.info("[搜图] Danbooru 帖子内容未渲染，刷新页面重试...")
                        try:
                            browser.minimized_navigate(driver, lambda: driver.refresh())
                            browser.keep_window_minimized(driver)
                        except Exception:
                            pass
            # API 由浏览器同源 fetch 获取（httpx 被 Cloudflare 403，但浏览器已过页面验证、
            # 同源请求不会拦）：拿到权威 file_url 直链，无需构造
            try:
                m2 = re.search(r'/posts/(\d+)', url)
                if not m2:
                    raise RuntimeError("no post id")
                api_url = f"https://danbooru.donmai.us/posts/{m2.group(1)}.json"
                api_json = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    fetch(arguments[0], {credentials: 'include'})
                        .then(r => r.ok ? r.text() : Promise.reject('bad'))
                        .then(t => done(t))
                        .catch(() => done(''));
                """, api_url)
                if api_json:
                    import json as _json
                    data = _json.loads(api_json)
                    fu = (data.get("post") or {}).get("file_url") or data.get("file_url") or ""
                    if fu:
                        if fu.startswith("//"):
                            fu = "https:" + fu
                        candidates.append(fu)
                        has_authoritative = True
                        logger.info(f"[搜图] Danbooru 浏览器API获取到原图URL: {fu[:80]}")
            except Exception as e:
                logger.warning(f"[搜图] Danbooru 浏览器API获取失败: {e}")
            # 最权威：页面图片容器的 data-file-url 就是全尺寸原图直链（无需构造，不会 404）
            try:
                sec = driver.find_element(By.CSS_SELECTOR, "[data-file-url]")
                file_url = sec.get_attribute("data-file-url") or ""
                if file_url.startswith("http"):
                    candidates.append(file_url)
                    has_authoritative = True
                    logger.info(f"[搜图] Danbooru data-file-url 原图直链: {file_url[:80]}")
            except Exception:
                pass
            # 先定位页面屏幕中间的 #image，确认要下载的是哪张图
            center_url = ""
            try:
                img = driver.find_element(By.CSS_SELECTOR, "#image")
                center_url = img.get_attribute("src") or ""
                logger.info(f"[搜图] Danbooru 页面中间图: {center_url[:80]}")
            except Exception:
                pass
            # 从页面 Original/下载链接收集精确原图 URL（顺带拿正确的扩展名）
            orig_hrefs = []
            try:
                for link in driver.find_elements(By.XPATH, "//a[contains(@href, 'original') or contains(@href, 'cdn.donmai.us')]"):
                    href = link.get_attribute("href") or ""
                    if href and ("/original/" in href or "cdn.donmai.us" in href):
                        orig_hrefs.append(href)
            except Exception:
                pass
            try:
                for link in driver.find_elements(By.CSS_SELECTOR, "a[download]"):
                    href = link.get_attribute("href") or ""
                    if href.startswith("http"):
                        orig_hrefs.append(href)
            except Exception:
                pass
            # 成功访问页面，保存 cookies
            try:
                cookies = driver.get_cookies()
                with open(config.DANBOORU_COOKIES_PATH, 'w', encoding='utf-8') as f:
                    json.dump(cookies, f, ensure_ascii=False, indent=2)
                logger.info(f"[搜图] ✅ 已保存 Danbooru cookies ({len(cookies)} 个)")
            except Exception as e:
                logger.warning(f"[搜图] 保存 cookies 失败: {e}")
            # 中间图：有权威原图直链时不再构造（sample 文件名转 original 是错误猜测，必 404），
            # 只把页面当前显示的 sample 图本身留作下载兜底；无权威直链时才按 md5 重拼 original。
            if center_url:
                if "/sample/" in center_url:
                    if not has_authoritative:
                        full_url = _danbooru_original_from_sample(center_url)
                        if full_url:
                            logger.info(f"[搜图] Danbooru 从中间图构造全尺寸原图: {full_url[:80]}")
                            candidates.append(full_url)
                    else:
                        logger.info("[搜图] Danbooru 已有权威原图直链，跳过中间图构造")
                    logger.info(f"[搜图] Danbooru 中间图作为候选兜底: {center_url[:80]}")
                    candidates.append(center_url)
                else:
                    logger.info(f"[搜图] Danbooru 中间图即全尺寸: {center_url[:80]}")
                    candidates.append(center_url)
            # 2. 页面 Original 精确链接
            candidates.extend(orig_hrefs)
            # 3. API 兜底（file_url 就是全尺寸）
            if api_file_url:
                logger.info("[搜图] Danbooru 页面无图，使用 API 兜底原图 URL")
                candidates.append(api_file_url)
            # 4. 最后试试 download 链接
            try:
                link = driver.find_element(By.CSS_SELECTOR, 'a[download]')
                candidates.append(link.get_attribute("href"))
            except Exception:
                pass
            # ===== Danbooru 父子帖：真正的目标图可能在子帖里 =====
            # 帖子页默认只显示当前帖（父帖），子帖要点击 "This post has N children"
            # 区域里的缩略图才能看到；这里把子帖原图直链也加入候选，交给后续相似度比较统一挑选。
            try:
                cur_post_m = re.search(r'/posts/(\d+)', url)
                cur_post_id = cur_post_m.group(1) if cur_post_m else ""
                child_ids = []
                search_found_urls = False
                post_obj = {}
                if isinstance(api_json, str) and api_json:
                    _cdata = json.loads(api_json)
                    post_obj = _cdata.get("post") or _cdata
                child_raw = (post_obj or {}).get("children") or []
                for c in child_raw:
                    if isinstance(c, dict):
                        cid = str(c.get("id") or "")
                    else:
                        cid = str(c or "")
                    if cid.isdigit() and cid != cur_post_id:
                        child_ids.append(cid)
                # API 的 post 对象不一定带 children 数组，用标准检索接口按 parent 元标签找子帖
                if not child_ids and cur_post_id:
                    try:
                        search_url = f"https://danbooru.donmai.us/posts.json?tags=parent:{cur_post_id}&limit=20"
                        sjson = driver.execute_async_script(
                            "const done = arguments[arguments.length - 1];"
                            "fetch(arguments[0], {credentials: 'include'})"
                            ".then(r => r.ok ? r.text() : Promise.reject('bad'))"
                            ".then(t => done(t)).catch(() => done(''));",
                            search_url,
                        )
                        if sjson:
                            slist = json.loads(sjson)
                            if isinstance(slist, dict):
                                slist = slist.get("posts") or []
                            for p in slist:
                                if not isinstance(p, dict):
                                    continue
                                pid = str(p.get("id") or "")
                                if pid.isdigit() and pid != cur_post_id and pid not in child_ids:
                                    child_ids.append(pid)
                                pfu = p.get("file_url") or ""
                                if pfu:
                                    if pfu.startswith("//"):
                                        pfu = "https:" + pfu
                                    candidates.append(pfu)
                                    search_found_urls = True
                    except Exception:
                        pass
                # API 没给 children 时，从页面的子帖缩略图区域抓链接兜底
                if not child_ids:
                    for sel in (
                        "#post-children a[href*='/posts/']",
                        "[id*='child'] a[href*='/posts/']",
                        "[class*='child'] a[href*='/posts/']",
                    ):
                        try:
                            for _a in driver.find_elements(By.CSS_SELECTOR, sel):
                                _h = _a.get_attribute("href") or ""
                                _m = re.search(r"/posts/(\d+)", _h)
                                if _m and _m.group(1) != cur_post_id and _m.group(1) not in child_ids:
                                    child_ids.append(_m.group(1))
                        except Exception:
                            continue
                        if child_ids:
                            break
                if child_ids:
                    logger.info(f"[搜图] Danbooru 帖子 {cur_post_id} 检测到子帖: {child_ids[:6]}，一并加入候选")
                if child_ids and not search_found_urls:
                    for cid in child_ids[:4]:
                        try:
                            child_api_url = f"https://danbooru.donmai.us/posts/{cid}.json"
                            cjson = driver.execute_async_script(
                                "const done = arguments[arguments.length - 1];"
                                "fetch(arguments[0], {credentials: 'include'})"
                                ".then(r => r.ok ? r.text() : Promise.reject('bad'))"
                                ".then(t => done(t)).catch(() => done(''));",
                                child_api_url,
                            )
                            if not cjson:
                                continue
                            cdata = json.loads(cjson)
                            cp = cdata.get("post") or cdata
                            cfu = cp.get("file_url") or cdata.get("file_url") or ""
                            if cfu.startswith("//"):
                                cfu = "https:" + cfu
                            if cfu:
                                candidates.append(cfu)
                                logger.info(f"[搜图] Danbooru 子帖 {cid} 原图直链: {cfu[:90]}")
                        except Exception as e:
                            logger.warning(f"[搜图] Danbooru 子帖 {cid} 获取原图失败: {e}")
            except Exception as e:
                logger.warning(f"[搜图] Danbooru 子帖处理失败: {e}")
            # 去重
            seen = set()
            unique = [u for u in candidates if u and u not in seen and not seen.add(u)]
            # 优先排静态图片（jpg/png/webp），跳过视频（mp4/webm/gif）
            # Danbooru 父帖常是动图/视频，子帖第一个才是静态原图；
            # 如果有静态图片就只返回图片，避免反复下载视频失败浪费时间
            _img_exts = (".jpg", ".jpeg", ".png", ".webp")
            _vid_exts = (".mp4", ".webm", ".gif", ".mov")
            img_candidates = [u for u in unique if any(u.lower().split("?")[0].endswith(ext) for ext in _img_exts)]
            vid_candidates = [u for u in unique if any(u.lower().split("?")[0].endswith(ext) for ext in _vid_exts)]
            other_candidates = [u for u in unique if u not in img_candidates and u not in vid_candidates]
            if img_candidates:
                logger.info(f"[搜图] Danbooru 候选中静态图 {len(img_candidates)} 张、视频 {len(vid_candidates)} 个，优先用静态图")
                return img_candidates + other_candidates
            logger.info(f"[搜图] Danbooru 候选中无静态图，只有视频 {len(vid_candidates)} 个")
            return unique

        elif site_name == "zerochan":
            # Zerochan: 找包含 ".full." 的链接，就是原图
            try:
                links = driver.find_elements(By.CSS_SELECTOR, 'a[href*=".full."]')
                if links:
                    original_url = links[0].get_attribute("href")
                    logger.info(f"[搜图] Zerochan 找到原图链接: {original_url[:80]}")
                    return [original_url]
            except Exception:
                pass
            # 备用方案：从预览图 URL 构造原图 URL
            try:
                imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="zerochan.net"]')
                for img in imgs:
                    src = img.get_attribute("src") or ""
                    if "1024" in src or "240" in src:
                        # 把预览图改成原图
                        # 例如：xxx.1024.4593348.webp -> xxx.full.4593348.png
                        # 修正：保留图片 id，例如 xxx.1024.4593348.webp -> xxx.full.4593348.png
                        original_url = re.sub(r'\.\d+\.(\d+)\.(?:webp|jpg|png)$', r'.full.\1.png', src)
                        logger.info(f"[搜图] Zerochan 构造原图 URL: {original_url[:80]}")
                        return [original_url]
            except Exception:
                pass

        elif site_name == "pixiv":
            # Pixiv：优先用 ajax 接口拿原图直链（未登录也能拿到公开作品的原图）
            try:
                import httpx
                m = re.search(r'/artworks/(\d+)', url)
                if m:
                    art_id = m.group(1)
                    api_url = f"https://www.pixiv.net/ajax/illust/{art_id}"
                    with httpx.Client(
                        proxy=config.PROXY_URL if config.PROXY_URL else None,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                            "Referer": "https://www.pixiv.net/",
                            "Accept-Language": "zh-CN,zh;q=0.9",
                        },
                        timeout=12, follow_redirects=True
                    ) as client:
                        resp = client.get(api_url)
                        if resp.status_code == 200:
                            data = resp.json()
                            orig = ((data.get("body") or {}).get("urls") or {}).get("original")
                            if orig:
                                logger.info(f"[搜图] Pixiv ajax 获取到原图URL: {orig[:80]}")
                                return [orig]
                        else:
                            logger.warning(f"[搜图] Pixiv ajax 返回状态码: {resp.status_code}")
            except Exception as e:
                logger.warning(f"[搜图] Pixiv ajax 获取原图失败: {e}")
            # ajax 拿不到（多半是代理节点连 pixiv 不稳）：加一个 pixiv.re 镜像候选兜底
            _pm = re.search(r'/artworks/(\d+)', url)
            mirror = [f"https://pixiv.re/{_pm.group(1)}.png"] if _pm else []
            # 页面 meta 兜底（需要登录的 R18 等作品，浏览器登录后能拿到 og:image）
            try:
                # 先试试从页面的 JS 变量里找
                # 或者从 meta 标签提取，然后转换成原图 URL
                meta = driver.find_element(By.CSS_SELECTOR, 'meta[property="og:image"]')
                img_url = meta.get_attribute("content")
                logger.info(f"[搜图] Pixiv og:image: {img_url[:80]}")

                # 转换成原图 URL
                if "img-master" in img_url:
                    # 替换路径
                    img_url = img_url.replace("img-master", "img-original")
                    # 去掉 _master1200 后缀
                    img_url = img_url.replace("_master1200", "")
                    # 去掉尺寸参数（如果有的话）
                    if "/c/" in img_url:
                        # 比如 /c/600x1200_90_webp/ 这样的路径，要去掉
                        img_url = re.sub(r'/c/[^/]+/', '/', img_url)

                logger.info(f"[搜图] Pixiv 转换后原图: {img_url[:80]}")
                return [img_url] + mirror
            except Exception as e:
                logger.warning(f"[搜图] Pixiv 提取失败: {e}")
            if mirror:
                return mirror

        elif site_name == "twitter":
            # X 未登录时会显示受限视图（"Continue to X"/"Sign in"），个人主页无法下滑加载更多图片
            try:
                body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
                login_wall = ("continue to x" in body_text or "sign in to x" in body_text
                              or "log in to x" in body_text or "登录 x" in body_text)
            except Exception:
                login_wall = False
            if login_wall and "/status/" not in url:
                logger.warning("[搜图] X 显示登录墙（未登录或会话过期），个人主页无法滚动加载更多图片；"
                               "请在控制面板「设置 → 账号与角色」点「登录 X 账号」登录后再启动机器人")
                return []
            if "/status/" in url:
                # 单条推文页：直接用页面里的图片构造原图，不做缩略图比较
                # x.com 是重 JS 页面，图片可能晚出现，先等一会儿再取
                status_imgs = _extract_tweet_status_image(driver, epoch)
                if status_imgs:
                    return status_imgs
                # 提取失败再退回相似度匹配
                if query_bytes:
                    picked = _find_best_image_on_page(driver, "twitter", query_bytes, epoch, deadline=page_deadline)
                    if picked:
                        return [picked]
                return []
            # 个人主页/帖子列表：用相似度在所有帖子里找目标图，找不到就跳过该网站
            if query_bytes:
                # X 时间线是重 JS 页面，图片渲染得比较晚；先等媒体图真正出现在 DOM 里再匹配，
                # 否则候选为空（之前"图就在主页前10帖却识别不到"的根因之一）
                def _wait_timeline_images():
                    try:
                        WebDriverWait(driver, 20).until(
                            lambda d: d.find_elements(By.CSS_SELECTOR, 'img[src*="twimg.com/media"], img[data-src*="twimg.com/media"]')
                        )
                    except Exception:
                        pass
                for attempt in range(3):
                    if aborted(epoch):
                        raise runtime.SearchAbortedError("搜图已中止")
                    _wait_timeline_images()
                    # 页面里根本没有媒体图：重试也不会凭空出现（登录墙变体/媒体被拦截），
                    # 最多刷新重试一次，之后直接放弃，避免白等几十秒
                    media_count = len(driver.find_elements(By.CSS_SELECTOR, 'img[src*="twimg.com/media"], img[data-src*="twimg.com/media"]'))
                    if media_count == 0:
                        if attempt == 0:
                            logger.info("[搜图] Twitter 主页暂无媒体图，刷新一次再试...")
                            try:
                                browser.minimized_navigate(driver, lambda: driver.refresh())
                                browser.keep_window_minimized(driver)
                            except Exception:
                                pass
                            continue
                        logger.warning("[搜图] Twitter 主页刷新后仍无媒体图（登录墙变体/媒体限制），跳过该网站")
                        break
                    picked = _find_best_image_on_page(driver, "twitter", query_bytes, epoch, deadline=page_deadline)
                    if picked:
                        return [picked]
                    if attempt == 0:
                        # 第一次没匹配到：很可能是时间线 API 请求因网络抖动失败（页面有壳但没推文），
                        # 刷新页面重试一次
                        logger.info("[搜图] Twitter 主页第一次未匹配到目标图，刷新页面重试一次...")
                        try:
                            browser.minimized_navigate(driver, lambda: driver.refresh())
                            browser.keep_window_minimized(driver)
                        except Exception:
                            pass
                    elif attempt == 1:
                        # 第二次还不行：直接重新导航一次（refresh 有时不会重新触发时间线请求）
                        logger.info("[搜图] Twitter 主页第二次未匹配到目标图，重新导航重试...")
                        try:
                            browser.minimized_navigate(driver, lambda: driver.get(url))
                            browser.keep_window_minimized(driver)
                        except Exception:
                            pass
                # 主页直接匹配失败：在主页里按相似度找最像的那条推文，
                # 打开它的单帖页直接取原图（单帖页路径最可靠，保证推特尽量成功）
                status_url = _find_best_tweet_status_url(driver, query_bytes, epoch)
                if status_url:
                    try:
                        browser.minimized_navigate(driver, lambda: driver.get(status_url))
                        browser.keep_window_minimized(driver)
                        try:
                            WebDriverWait(driver, 25).until(
                                lambda d: d.execute_script("return document.readyState") == "complete"
                            )
                        except Exception:
                            pass
                        _interruptible_sleep(1, epoch)
                        status_imgs = _extract_tweet_status_image(driver, epoch)
                        if status_imgs:
                            return status_imgs
                    except Exception as e:
                        logger.warning(f"[搜图] 打开最相似推文单帖页失败: {e}")
            # 诊断：记录推特页面最终状态，便于定位"时间线没加载"的具体原因
            try:
                cur = driver.current_url or ""
                title = driver.title or ""
                media_count = len(driver.find_elements(By.CSS_SELECTOR, 'img[src*="twimg.com/media"], img[data-src*="twimg.com/media"]'))
                body_text = (driver.find_element(By.TAG_NAME, "body").text or "")[:150].replace("\n", " | ")
                logger.info(f"[搜图] 推特页面状态: URL={cur[:80]} | title={title[:60]} | 媒体图={media_count} | body={body_text}")
            except Exception as e:
                logger.warning(f"[搜图] 推特页面状态读取失败: {e}")
            logger.info("[搜图] Twitter 主页未匹配到目标图，跳过该网站")

        elif site_name == "hoyolab":
            # Hoyolab：帖子通常有多张图，优先用相似度找与查询图最匹配的那张
            if query_bytes:
                # 多图页面匹配最相似的图。当前大图提前返回用严格 50 分（避免误判不再点击），
                # 缩略图选最像用宽松 30 分：动图/表情包缩略图截图打分偏低（可能只有 30~45 分），
                # 准确与否交给"下载后第一帧比对 ≥40"的最终兜底。
                # 注意多图帖必须点击缩略图/开灯箱才能看全。
                picked = _hoyolab_click_through_images(driver, query_bytes, epoch, min_score=30)
                if picked:
                    return [picked]
                # 兜底：点不了缩略图时退回原来的页面截图匹配
                picked = _find_best_image_on_page(driver, "hoyolab", query_bytes, epoch, min_score=20, deadline=page_deadline)
                if picked:
                    return [picked]
            # 兜底：找文章里的主图（最大的那张）
            try:
                # 先找文章里的主图
                imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="hoyolab.com"]')
                if not imgs:
                    imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="mihoyo.com"]')

                if imgs:
                    # 找最大的那张图（通常是原图）
                    best_img = None
                    max_size = 0
                    for img in imgs:
                        try:
                            w = int(img.get_attribute("width") or 0)
                            h = int(img.get_attribute("height") or 0)
                            size = w * h
                            if size > max_size:
                                max_size = size
                                best_img = img
                        except Exception:
                            pass

                    if best_img:
                        img_url = best_img.get_attribute("src")
                        logger.info(f"[搜图] Hoyolab 图片 URL: {img_url[:80]}")

                        # 去掉尺寸参数，获取原图
                        if "?" in img_url:
                            # 去掉 x-oss-process 之类的参数
                            base_url = img_url.split("?")[0]
                            img_url = base_url

                        return [img_url]
            except Exception as e:
                logger.warning(f"[搜图] Hoyolab 提取原图失败: {e}")

        elif site_name == "arca":
            # Arca.live: 韩国图片社区
            try:
                # 找文章内容里的图片
                imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="arca.live"]')
                if not imgs:
                    # 也可能是 cdn 域名
                    imgs = driver.find_elements(By.CSS_SELECTOR, 'article img')

                if imgs:
                    # 找最大的那张图
                    best_img = None
                    max_size = 0
                    for img in imgs:
                        try:
                            w = int(img.get_attribute("width") or 0)
                            h = int(img.get_attribute("height") or 0)
                            # 过滤太小的图
                            if w < 200 or h < 200:
                                continue
                            size = w * h
                            if size > max_size:
                                max_size = size
                                best_img = img
                        except Exception:
                            pass

                    if best_img:
                        img_url = best_img.get_attribute("src")
                        logger.info(f"[搜图] Arca.live 图片 URL: {img_url[:80]}")

                        # 去掉参数，获取原图
                        if "?" in img_url:
                            base_url = img_url.split("?")[0]
                            img_url = base_url

                        return [img_url]
            except Exception as e:
                logger.warning(f"[搜图] Arca.live 提取原图失败: {e}")

        elif site_name == "rule34":
            # rule34.us 帖子页：页面里的 <img>/Original 链接就是原图直链
            orig_urls = []
            try:
                for a in driver.find_elements(By.CSS_SELECTOR, 'a[href*="rule34.us/images/"]'):
                    href = a.get_attribute("href") or ""
                    if href.startswith("http"):
                        orig_urls.append(href)
                for img in driver.find_elements(By.CSS_SELECTOR, 'img[src*="rule34.us/images/"]'):
                    src = img.get_attribute("src") or ""
                    if src.startswith("http"):
                        orig_urls.append(src)
            except Exception:
                pass
            seen = set()
            uniq = []
            for u in orig_urls:
                if u and u not in seen and not seen.add(u):
                    uniq.append(u)
            if uniq:
                logger.info(f"[搜图] Rule34.us 帖子页获取到原图直链: {uniq[0][:80]}")
                return uniq
            # 列表页/主页：按相似度在缩略图里找目标图，再构造原图（扩展名不确定，多给几个候选）
            if query_bytes:
                try:
                    imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="rule34.us/thumbnails/"]')
                    cands = []
                    seen2 = set()
                    for img in imgs:
                        src = (img.get_attribute("src") or "").split("?")[0]
                        if src in seen2:
                            continue
                        seen2.add(src)
                        cands.append(src)
                        if len(cands) >= 15:
                            break
                    best_url, best_score = "", 0
                    import httpx
                    for src in cands:
                        try:
                            with httpx.Client(
                                proxy=config.PROXY_URL if config.PROXY_URL else None,
                                headers={
                                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                                    "Referer": "https://rule34.us/",
                                },
                                timeout=6, follow_redirects=True
                            ) as client:
                                resp = client.get(src)
                                data = resp.content if resp.status_code == 200 else None
                            if not data:
                                continue
                            score = _image_similarity_crop_tolerant(query_bytes, data)
                            if score > best_score:
                                best_score, best_url = score, src
                        except Exception:
                            continue
                    if best_url and best_score >= 20:
                        m = re.search(r"/thumbnails/([0-9a-f]{2}/[0-9a-f]{2})/thumbnail_([0-9a-f]{32})\.", best_url)
                        if m:
                            base = f"https://img2.rule34.us/images/{m.group(1)}/{m.group(2)}"
                            logger.info(f"[搜图] ✅ Rule34.us 列表页匹配到目标图（相似度 {best_score}/64），构造原图候选")
                            return [f"{base}.png", f"{base}.jpg", f"{base}.jpeg", f"{base}.gif", f"{base}.webp"]
                except Exception as e:
                    logger.warning(f"[搜图] Rule34.us 列表页匹配失败: {e}")
            return []

        elif site_name == "bobopic":
            # BoBoPic：文章页是 Pixiv 图集，每张图都带"查看原图"链接 go.bobopic.com/<id>
            # 图集里有多张图，需要先按相似度选出与查询图最匹配的那张，再拿它的原图直链
            try:
                # 谷歌直接给到图片/CDN 链接时，直接从 URL 里取作品 ID（图片页可能被 Cloudflare 挡住）
                m = re.search(r"pixivdaily\.com/(?:small|tx_img)/(\d+)", url)
                if not m:
                    m = re.search(r"go\.bobopic\.com/(\d+)", url)
                if m:
                    logger.info(f"[搜图] BoBoPic 谷歌直接给了图片链接，作品 ID: {m.group(1)}")
                    return _bobopic_candidates_with_pixiv_fallback(_bobopic_original_urls(m.group(1)), m.group(1))
                art_ids = []
                seen_ids = set()
                for a in driver.find_elements(By.CSS_SELECTOR, 'a[href*="go.bobopic.com/"]'):
                    href = a.get_attribute("href") or ""
                    m = re.search(r"go\.bobopic\.com/(\d+)", href)
                    if m and m.group(1) not in seen_ids:
                        seen_ids.add(m.group(1))
                        art_ids.append(m.group(1))
                for img in driver.find_elements(By.CSS_SELECTOR, 'img[src*="pixivdaily.com"]'):
                    src = img.get_attribute("src") or ""
                    m = re.search(r"pixivdaily\.com/(?:small|tx_img)/(\d+)", src)
                    if m and m.group(1) not in seen_ids:
                        seen_ids.add(m.group(1))
                        art_ids.append(m.group(1))
                    if len(art_ids) >= 15:
                        break
                if not art_ids:
                    logger.info("[搜图] BoBoPic 页面未找到图集图片，跳过该网站")
                    return []
                # 只有一张图：直接取原图（直链失败时带 pixiv ajax 兜底）
                if len(art_ids) == 1:
                    return _bobopic_candidates_with_pixiv_fallback(_bobopic_original_urls(art_ids[0]), art_ids[0])
                # 多张图：逐张下载原图做相似度比较，选最匹配的
                if query_bytes:
                    best_url, best_score, best_id = "", 0, ""
                    for aid in art_ids:
                        try:
                            import httpx
                            viewer_url = f"https://go.bobopic.com/{aid}"
                            with httpx.Client(
                                proxy=None,
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
                                timeout=12, follow_redirects=True
                            ) as client:
                                vresp = client.get(viewer_url)
                                if vresp.status_code != 200:
                                    continue
                                m = re.search(r'id="main-img"[^>]*src="([^"]+)"', vresp.text)
                                if not m:
                                    continue
                                img_src = m.group(1).replace("&amp;", "&")
                                r = client.get(img_src)
                                data = r.content if r.status_code == 200 else None
                            if not data:
                                continue
                            score = _image_similarity_crop_tolerant(query_bytes, data)
                            logger.info(f"[搜图] BoBoPic 作品 {aid} 与原图相似度 {score}/64")
                            if score > best_score:
                                best_score, best_url, best_id = score, img_src, aid
                        except Exception:
                            continue
                    if best_url and best_score >= 20:
                        logger.info(f"[搜图] ✅ BoBoPic 选中作品 {best_id}（相似度 {best_score}/64）: {best_url[:80]}")
                        # 直链可能拿到的是 CDN 错误图（sogoucdn 防盗链/时效），追加 pixiv 原图作为兜底
                        return _bobopic_candidates_with_pixiv_fallback([best_url], best_id)
                    logger.info(f"[搜图] BoBoPic 图集中没有足够相似的作品（最高 {best_score}/64），跳过该网站")
                    return []
                # 没有查询图时返回第一张
                return _bobopic_candidates_with_pixiv_fallback(_bobopic_original_urls(art_ids[0]), art_ids[0])
            except Exception as e:
                logger.warning(f"[搜图] BoBoPic 提取失败: {e}")
            return []

        elif site_name == "gelbooru":
            # gelbooru 帖子页：img#image 通常就是原图；遇到 sample 时用 md5+扩展名构造原图
            orig_urls = []
            try:
                img = driver.find_element(By.CSS_SELECTOR, "img#image")
                src = img.get_attribute("src") or ""
                if src.startswith("http"):
                    if "/samples/" in src or "sample_" in src:
                        m = re.search(r"(https://img\d+\.gelbooru\.com)/samples?/([0-9a-f]{2}/[0-9a-f]{2})/sample_([0-9a-f]{32})\.", src)
                        if m:
                            ext = ""
                            try:
                                sec = driver.find_element(By.CSS_SELECTOR, "section.image-container")
                                ext = (sec.get_attribute("data-file-ext") or "").strip()
                            except Exception:
                                pass
                            base = f"{m.group(1)}/images/{m.group(2)}/{m.group(3)}"
                            if ext:
                                ext = ext if ext.startswith(".") else "." + ext
                                orig_urls.append(base + ext)
                            orig_urls.append(base + ".jpg")
                            orig_urls.append(base + ".png")
                    else:
                        orig_urls.append(src)
            except Exception:
                pass
            # 页面里的原图直链（Original 链接）
            try:
                for a in driver.find_elements(By.CSS_SELECTOR, 'a[href*="gelbooru.com/images/"]'):
                    href = a.get_attribute("href") or ""
                    if href.startswith("http"):
                        orig_urls.append(href)
            except Exception:
                pass
            seen = set()
            uniq = []
            for u in orig_urls:
                if u and u not in seen and not seen.add(u):
                    uniq.append(u)
            if uniq:
                logger.info(f"[搜图] Gelbooru 帖子页获取到原图直链: {uniq[0][:80]}")
                return uniq
            # 列表页/主页：缩略图相似度匹配后构造原图（扩展名不确定，多给几个候选）
            if query_bytes:
                try:
                    imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="gelbooru.com/thumbnails/"]')
                    cands = []
                    seen2 = set()
                    for img in imgs:
                        src = (img.get_attribute("src") or "").split("?")[0]
                        if src in seen2:
                            continue
                        seen2.add(src)
                        cands.append(src)
                        if len(cands) >= 15:
                            break
                    best_url, best_score = "", 0
                    import httpx
                    for src in cands:
                        try:
                            with httpx.Client(
                                proxy=config.PROXY_URL if config.PROXY_URL else None,
                                headers={
                                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                                    "Referer": "https://gelbooru.com/",
                                },
                                timeout=6, follow_redirects=True
                            ) as client:
                                resp = client.get(src)
                                data = resp.content if resp.status_code == 200 else None
                            if not data:
                                continue
                            score = _image_similarity_crop_tolerant(query_bytes, data)
                            if score > best_score:
                                best_score, best_url = score, src
                        except Exception:
                            continue
                    if best_url and best_score >= 20:
                        m = re.search(r"(https://img\d+\.gelbooru\.com)/thumbnails/([0-9a-f]{2}/[0-9a-f]{2})/thumbnail_([0-9a-f]{32})\.", best_url)
                        if m:
                            base = f"{m.group(1)}/images/{m.group(2)}/{m.group(3)}"
                            logger.info(f"[搜图] ✅ Gelbooru 列表页匹配到目标图（相似度 {best_score}/64），构造原图候选")
                            return [f"{base}.png", f"{base}.jpg", f"{base}.jpeg", f"{base}.gif", f"{base}.webp"]
                except Exception as e:
                    logger.warning(f"[搜图] Gelbooru 列表页匹配失败: {e}")
            return []

        elif site_name == "skebetter":
            # 只有"单个帖子页"才处理（URL 含 /illust/<数字>）；标签/列表页（一堆图）直接跳过
            # skebetter 偶尔有广告弹窗，尽量关掉（失败也无所谓，不影响取图）
            try:
                for sel in ("[class*='modal'] button", "[class*='popup'] button",
                            "button[class*='close']", "[class*='ad-close']", "[class*='ad-close-button']"):
                    for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                        try:
                            if btn.is_displayed():
                                btn.click()
                        except Exception:
                            pass
            except Exception:
                pass
            orig_urls = []
            # 1. og:image 就是主图
            try:
                meta = driver.find_element(By.CSS_SELECTOR, 'meta[property="og:image"]')
                img_url = meta.get_attribute("content") or ""
                if img_url.startswith("http") and "favicon" not in img_url:
                    orig_urls.append(img_url)
            except Exception:
                pass
            # 2. 页面主图：pbs.twimg.com/media 且不带 :small/_normal 后缀（带后缀的是作者其他作品缩略图）
            try:
                for img in driver.find_elements(By.CSS_SELECTOR, 'img[src*="pbs.twimg.com/media"], img[data-src*="pbs.twimg.com/media"]'):
                    src = img.get_attribute("data-src") or img.get_attribute("src") or ""
                    if src.startswith("http") and ":small" not in src and "_normal" not in src:
                        orig_urls.append(src.split("?")[0])
            except Exception:
                pass
            seen = set()
            uniq = []
            for u in orig_urls:
                if u and u not in seen and not seen.add(u):
                    uniq.append(u)
            if uniq:
                logger.info(f"[搜图] Skebetter 单帖页获取到原图: {uniq[0][:80]}")
                # twimg 图片转全尺寸原图
                return [f"{u}?format=jpg&name=orig" if "twimg.com" in u else u for u in uniq]
            logger.info("[搜图] Skebetter 单帖页未找到主图，跳过该网站")
            return []

        elif site_name == "generic":
            # 通用模式：未知网站，找页面上最大的图片
            try:
                imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="http"]')
                if imgs:
                    best_img = None
                    max_size = 0
                    for img in imgs:
                        try:
                            w = int(img.get_attribute("width") or 0)
                            h = int(img.get_attribute("height") or 0)
                            # 过滤掉太小的图标和头像
                            if w < 200 or h < 200:
                                continue
                            size = w * h
                            if size > max_size:
                                max_size = size
                                best_img = img
                        except Exception:
                            pass

                    if best_img:
                        img_url = best_img.get_attribute("src")
                        logger.info(f"[搜图] 通用模式找到最大图片: {img_url[:80]} (尺寸: {max_size})")
                        return [img_url]
            except Exception as e:
                logger.warning(f"[搜图] 通用模式提取失败: {e}")

        # 通用兜底：在页面所有图里按相似度找目标图（个人主页/帖子列表等场景）
        if query_bytes:
            picked = _find_best_image_on_page(driver, site_name, query_bytes, epoch, deadline=page_deadline)
            if picked:
                return [picked]

        return []
    except Exception as e:
        logger.warning(f"[搜图] 提取 {site_name} 原图失败: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        # 恢复页面加载超时设置（恢复成配置的完整超时，而不是写死30）
        try:
            driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)
        except Exception:
            pass
