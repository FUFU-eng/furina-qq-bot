"""Google Lens 以图搜图主流程：上传图片、提取结果、选站、下载原图。"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from nonebot import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from . import browser, config, download, runtime, sites, lens_ui
from .download import _download_thumb_bytes, _try_download_bytes
from .runtime import SearchAbortedError, _interruptible_sleep, aborted
from .similarity import _image_similarity, _image_similarity_crop_tolerant, _is_placeholder_small, _looks_like_image, _crop_ui_query_bytes
from .sites import _construct_original_url, extract_original_image


def _is_direct_image_url(url: str) -> bool:
    """判断 URL 是否直接指向图片文件"""
    return bool(re.search(r"\.(?:jpg|jpeg|png|webp|gif|bmp)(?:$|\?)", url, re.I))


def _google_result_link(raw_url: str, base_url: str = "") -> str:
    """解码 Google 结果跳转链接，并把相对地址解析成绝对地址。"""
    from urllib.parse import parse_qs, urljoin, urlparse

    link = (raw_url or "").strip()
    if not link:
        return ""
    link = urljoin(base_url or "https://www.google.com/", link)
    parsed = urlparse(link)
    if "google.com/url" in link:
        query = parse_qs(parsed.query)
        target = (query.get("q") or query.get("url") or [""])[0]
        if target.startswith("http"):
            link = target
    return link


def _find_google_exact_match_url(driver) -> str:
    """从 Lens 普通结果页找到“完全匹配”入口（通常是 udm=48）。"""
    try:
        current_url = driver.current_url or "https://www.google.com/"
        for link_elem in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
            href = link_elem.get_attribute("href") or ""
            if "udm=48" not in href:
                continue
            text = (getattr(link_elem, "text", "") or "").strip().lower()
            jsname = (link_elem.get_attribute("jsname") or "").strip()
            if (
                "完全匹配" in text
                or "exact match" in text
                or "exact matches" in text
                or jsname == "pxBnId"
            ):
                return _google_result_link(href, current_url)
    except Exception:
        pass
    return ""


def _extract_google_result_pages(driver, limit: int = 20, exact: bool = False) -> list:
    """提取当前 Google 结果页的外部来源页面，供普通页和精确匹配页共用。"""
    from urllib.parse import urlparse

    pages = []
    seen = set()
    try:
        current_url = driver.current_url or "https://www.google.com/"
        links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
        for link_elem in links:
            link = _google_result_link(link_elem.get_attribute("href") or "", current_url)
            if not link or not link.startswith("http"):
                continue
            if "google" in urlparse(link).netloc.lower() or link in seen:
                continue
            seen.add(link)

            title = ""
            for sel in (".Yt787", ".gpZmoc", "h3", "[role='heading']"):
                try:
                    title = (link_elem.find_element(By.CSS_SELECTOR, sel).text or "").strip()
                    if title:
                        break
                except Exception:
                    pass
            if not title:
                title = (getattr(link_elem, "text", "") or "").strip()
            if not title or title == link or title.startswith("http"):
                title = ""
            else:
                title = title[:100]

            thumb = ""
            try:
                node = link_elem
                for _ in range(6):
                    for im in node.find_elements(By.CSS_SELECTOR, "img"):
                        src = (im.get_attribute("data-src") or im.get_attribute("src") or "").strip()
                        if src.startswith("data:image/") and len(src) > 2000:
                            thumb = src
                            break
                        if src.startswith("http") and "favicon" not in src and "google.com/s2" not in src:
                            thumb = src
                            break
                    if thumb:
                        break
                    node = node.find_element(By.XPATH, "..")
            except Exception:
                pass

            pages.append({"title": title, "link": link, "thumb": thumb, "exact": exact})
            if len(pages) >= limit:
                break
    except Exception:
        pass
    return pages


def _prioritize_exact_pages(pages: list) -> list:
    """精确匹配来源排在普通 Lens 来源前，详情页再排在个人主页前，并按链接去重。"""
    detail_markers = (
        "/status/", "/posts/", "/post/", "/pin/", "/pins/",
        "/artworks/", "/article/", "/articles/", "/thread/",
    )

    def _sort_key(page):
        link = (page.get("link", "") or "").lower()
        is_detail = any(marker in link for marker in detail_markers)
        return (bool(page.get("exact")), is_detail)

    ordered = []
    seen = set()
    for page in sorted(pages or [], key=_sort_key, reverse=True):
        link = page.get("link", "")
        if not link or link in seen:
            continue
        seen.add(link)
        ordered.append(page)
    return ordered


def _is_trusted_exact_page(page_url: str) -> bool:
    """判断链接是否是 Google 完全匹配的具体详情页，而不是作者主页。"""
    link = (page_url or "").lower()
    return any(marker in link for marker in (
        "/status/", "/posts/", "/post/", "/pin/", "/pins/",
        "/artworks/", "/article/", "/articles/", "/thread/",
    ))

def _alternate_ext_urls(url: str) -> list:
    """同一图片 URL 换常见扩展名（png/jpg/jpeg/webp/gif），应对"构造原图时猜错扩展名"的 404"""
    head, _, query = url.partition("?")
    base, ext = os.path.splitext(head)
    if not ext:
        return []
    alts = []
    for alt in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if alt.lower() != ext.lower():
            alts.append(base + alt + ("?" + query if query else ""))
    return alts


def _image_pixel_area(data: bytes) -> int:
    """候选图像素面积，用于同相似度时优先保留更高分辨率"""
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as img:
            return (img.width or 0) * (img.height or 0)
    except Exception:
        return len(data or b"")


def _trim_vision_candidates(candidates: list, max_count: int) -> None:
    """按 (dHash, 像素面积) 保留弱候选：同相似度下保留更大更清楚的图"""
    candidates.sort(key=lambda c: (c.get("dhash", 0), c.get("area", 0)), reverse=True)
    if len(candidates) <= max_count:
        return
    dropped = candidates[max_count:]
    for cand in dropped:
        logger.info(
            f"[搜图] 弱候选已满，丢弃更低质量候选 {cand.get('url', '')[:70]} "
            f"(dHash {cand.get('dhash', 0)}/64, 面积 {cand.get('area', 0)})"
        )
    del candidates[max_count:]


def _pick_best_vision_candidate(candidates: list, sim_threshold: float, existing_same_image: bool = False):
    """从已打分的弱候选中选最值得发送的图：同一张图优先，同分时优先更高分辨率"""
    eligible = []
    for cand in candidates:
        vision = cand.get("vision") or {}
        same_image = bool(vision.get("same_image"))
        same_character = bool(vision.get("same_character"))
        similarity = float(vision.get("similarity") or 0)
        if same_image or (same_character and similarity >= sim_threshold):
            eligible.append(cand)
    if not eligible:
        return None
    eligible.sort(key=lambda c: (
        bool((c.get("vision") or {}).get("same_image")),
        float((c.get("vision") or {}).get("similarity") or 0),
        int(c.get("area") or 0),
    ), reverse=True)
    best = eligible[0]
    if existing_same_image and not bool((best.get("vision") or {}).get("same_image")):
        return None
    return best


def search_with_yandex(driver, image_path: str, epoch: int, max_results: int = 12) -> list:
    """Yandex 以图搜图：上传图片，返回按相似度排序的来源网页/图片直链列表。
    Yandex 结果本身按相似度排序（动漫识图比桌面谷歌强），直接按顺序取前几个即可"""
    from urllib.parse import parse_qs, urlparse
    results = []
    try:
        def _upload_and_wait(wait_loops):
            """上传图片并轮询等待结果出现；返回是否成功"""
            file_input = None
            for _ in range(30):
                if aborted(epoch):
                    return False
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
                    if els:
                        file_input = els[0]
                        break
                except Exception:
                    pass
                _interruptible_sleep(0.5, epoch)
            if file_input is None:
                logger.warning("[搜图] Yandex 上传框未出现")
                return False
            try:
                # 文件上传可能触发窗口激活，用 minimized_navigate 包装
                browser.minimized_navigate(driver, lambda: file_input.send_keys(image_path))
                browser.keep_window_minimized(driver)
            except Exception as e:
                logger.warning(f"[搜图] Yandex 上传失败: {e}")
                return False
            _interruptible_sleep(1, epoch)
            # 轮询直到页面上出现外部站点链接或 Sites 标签（结果渲染需要几秒~十几秒）
            for _ in range(wait_loops):
                if aborted(epoch):
                    return False
                try:
                    body_text = (driver.find_element(By.TAG_NAME, "body").text or "")
                    ext_count = 0
                    for a in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
                        href = (a.get_attribute("href") or "").lower()
                        if href.startswith("http") and "yandex" not in href:
                            ext_count += 1
                            if ext_count >= 2:
                                break
                    if ext_count >= 2 or "Sites" in body_text:
                        return True
                except Exception:
                    pass
                _interruptible_sleep(1, epoch)
            return False

        browser.minimized_navigate(driver, lambda: driver.get("https://yandex.com/images/search"))
        browser.keep_window_minimized(driver)
        # page_load_strategy=none 下页面 JS 初始化需要几秒，先等它准备好再找上传框（实测不等必失败）
        _interruptible_sleep(6, epoch)
        ok = _upload_and_wait(30)
        if not ok:
            # 重试一次：重新加载页面再上传（Yandex 偶发上传不生效）
            logger.warning("[搜图] Yandex 首次上传未出结果，重新加载再试一次...")
            try:
                browser.minimized_navigate(driver, lambda: driver.get("https://yandex.com/images/search"))
                browser.keep_window_minimized(driver)
            except Exception:
                pass
            _interruptible_sleep(6, epoch)
            ok = _upload_and_wait(20)
        if not ok:
            # 失败诊断：保存截图 + 当前状态，便于排查
            try:
                driver.save_screenshot(config.YANDEX_DEBUG_SCREENSHOT)
            except Exception:
                pass
            logger.warning(
                f"[搜图] Yandex 上传未出结果，已保存调试截图 {config.YANDEX_DEBUG_SCREENSHOT}（URL: {(driver.current_url or '')[:80]}）"
            )
        # 人机验证检测
        try:
            body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
            if "captcha" in body_text or "robot check" in body_text:
                logger.warning("[搜图] Yandex 出现人机验证，跳过 Yandex 兜底")
                return []
        except Exception:
            pass
        seen = set()
        for a in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
            if aborted(epoch):
                break
            try:
                href = a.get_attribute("href") or ""
                netloc = urlparse(href).netloc.lower()
                if not netloc or "yandex" in netloc:
                    continue
                # Yandex 可能用跳转链接包裹，解出真实地址
                if "rpt=imageview" in href:
                    q = parse_qs(urlparse(href).query).get("url")
                    if q and q[0].startswith("http"):
                        href = q[0]
                        netloc = urlparse(href).netloc.lower()
                        if "yandex" in netloc:
                            continue
                # 去掉统计参数
                href = href.split("&utm_")[0]
                if netloc in seen:
                    continue
                seen.add(netloc)
                results.append(href)
                if len(results) >= max_results:
                    break
            except Exception:
                continue
        logger.info(f"[搜图] Yandex 提取到 {len(results)} 个来源")
        for r in results[:5]:
            logger.info(f"[搜图]   Yandex 来源: {r[:80]}")
    except Exception as e:
        logger.warning(f"[搜图] Yandex 搜索失败: {e}")
    return results


def search_with_google(image_path: str) -> dict:
    """用谷歌 Lens 进行以图搜图（同步函数，在线程池中运行）"""
    result = {
        "images": [],
        "pages": [],
        "thumb_pages": {},
        "original_image": "",
        "original_source": "",
        "original_similarity": 100,
        "original_page": "",
        "original_from_exact_match": False,
        "proxy_error": False,
    }
    search_start = time.time()  # 记录本次搜图开始时间，用于访问站点的时间预算控制

    driver = None
    search_window = None
    epoch = runtime._search_epoch  # 记录本次搜索的版本号，超时/强制重置时能快速退出

    try:
        # 获取浏览器实例（复用）
        driver = browser._get_driver()

        # 复用浏览器第一个标签页（预加载好的 Google Lens 上传页），不再每次新开标签页
        logger.info("[搜图] 复用浏览器标签页进行搜图...")
        search_window = driver.window_handles[0]
        browser.minimized_navigate(driver, lambda: driver.switch_to.window(search_window))
        browser.keep_window_minimized(driver)
        # 启动持续后台压制线程：整个搜图期间每 200ms 最小化浏览器窗口
        browser._start_suppression(epoch)

        try:
            def _check_upload_cancel():
                if aborted(epoch):
                    raise SearchAbortedError("搜图已中止")
    
            logger.info("[搜图] 打开当前 Google 图片上传对话框...")
            lens_ui.upload(driver, image_path,
                           lambda fn: browser.minimized_navigate(driver, fn), _check_upload_cancel)
            browser.keep_window_minimized(driver)
    
            # 等待搜索结果加载（手动轮询，期间定期最小化窗口，防止页面跳转触发弹窗）
            logger.info("[搜图] 等待搜索结果加载...")
    
            def _results_ready(d):
                # 搜图被中止（超时/强制重置）时立刻退出，不再空轮询
                if aborted(epoch):
                    raise SearchAbortedError("搜图已中止")
                try:
                    # Lens 结果页可能是 google.com/search 或 google.com/imgres，都算谷歌结果页
                    if "google.com" not in (d.current_url or ""):
                        return False
                    # 真实缩略图在 data-src 里（src 是1px占位图），两种属性都要检查
                    # 兼容谷歌多种缩略图格式：encrypted-tbn / gstatic.com / googleusercontent.com / base64 data URI
                    tbn = d.find_elements(By.CSS_SELECTOR, "img[src*='encrypted-tbn'], img[data-src*='encrypted-tbn'], img[src*='gstatic.com'], img[data-src*='gstatic.com'], img[src*='usercontent.google.com'], img[data-src*='usercontent.google.com']")
                    # 过滤掉1px占位图和过小的图（favicon级别），只算真实缩略图
                    real_tbn = 0
                    for im in tbn:
                        try:
                            w = im.size.get("width", 0)
                            h = im.size.get("height", 0)
                            if w > 50 and h > 50:
                                real_tbn += 1
                        except Exception:
                            real_tbn += 1
                    if real_tbn >= 3:
                        return True
                    ext = 0
                    for a in d.find_elements(By.CSS_SELECTOR, "#search a[href^='http']"):
                        href = a.get_attribute("href") or ""
                        if "google" not in href:
                            ext += 1
                            if ext >= 3:
                                return True
                except Exception:
                    pass
                return False
    
            _wait_deadline = time.time() + 60
            _last_minimize = 0.0
            _results_loaded = False
            _upload_retried = False
            while time.time() < _wait_deadline:
                if aborted(epoch):
                    raise SearchAbortedError("搜图已中止")
                # 定期确认窗口状态：瞬态弹窗压回，用户手动打开的窗口不再抢（keep 内部判断）
                if time.time() - _last_minimize > 0.5:
                    browser.keep_window_minimized(driver)
                    _last_minimize = time.time()
                page_error = lens_ui.page_error(driver)
                if page_error == 'upload_reference_invalid' and not _upload_retried:
                    _upload_retried = True
                    logger.warning('[搜图] 上传引用失效，从当前上传界面重新上传一次')
                    lens_ui.upload(driver, image_path,
                                   lambda fn: browser.minimized_navigate(driver, fn), _check_upload_cancel)
                    continue
                if page_error:
                    raise lens_ui.LensUIError(page_error)
                if _results_ready(driver):
                    _results_loaded = True
                    break
                _interruptible_sleep(0.5, epoch)
            if not _results_loaded:
                raise lens_ui.LensUIError('result_wait_timeout')
            browser.keep_window_minimized(driver)
            _interruptible_sleep(1.5, epoch)
    
            logger.info("[搜图] 提取结果...")
    
            # 先截图保存，方便调试
            try:
                driver.save_screenshot(config.GOOGLE_RESULT_SCREENSHOT)
                logger.info(f"[搜图] 已保存页面截图: {config.GOOGLE_RESULT_SCREENSHOT}")
            except Exception as e:
                logger.warning(f"[搜图] 截图失败: {e}")
    
            # 滚动几次，触发懒加载加载更多内容（增加滚动次数和等待时间，确保所有结果都加载出来）
            for _ in range(4):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                _interruptible_sleep(0.8, epoch)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
                _interruptible_sleep(0.5, epoch)
            driver.execute_script("window.scrollTo(0, 0);")
            _interruptible_sleep(0.8, epoch)
    
            # ========== 提取网页 + 相似图（结果太少时自动重试一次） ==========
            def _extract_results():
                # 提取匹配的网页（从整个结果页收集外部链接，覆盖"来源网页"卡片和"相似图"卡片两类结构）
                try:
                    from urllib.parse import urlparse, parse_qs
                    seen_pages = set()
                    for link_elem in driver.find_elements(By.CSS_SELECTOR, "a[href^='http']"):
                        try:
                            link = link_elem.get_attribute("href") or ""
                            if not link.startswith("http"):
                                continue
                            # 谷歌的跳转链接，解码出真实地址
                            if "google.com/url?" in link:
                                q = parse_qs(urlparse(link).query).get("q")
                                if q and q[0].startswith("http"):
                                    link = q[0]
                            # 排除谷歌自己的页面/图片CDN（按域名判断，能覆盖 about.google / lens.google 等）
                            if "google" in urlparse(link).netloc:
                                continue
                            if link in seen_pages:
                                continue
                            seen_pages.add(link)
                            # 标题优先取卡片标题（来源网页卡 .Yt787，相似图卡 .gpZmoc），
                            # 谷歌改版时类名会变：补充语义化选择器（h3/role=heading）和整段文字兜底
                            title = ""
                            for sel in (".Yt787", ".gpZmoc", "h3", "[role='heading']"):
                                try:
                                    t_span = link_elem.find_element(By.CSS_SELECTOR, sel)
                                    title = (t_span.text or "").strip()
                                    if title:
                                        break
                                except Exception:
                                    pass
                            if not title:
                                title = (link_elem.text or "").strip()
                            title = (title or "").strip()
                            # 标题不能是 URL 本身（否则回复里同一个链接会重复出现两遍）
                            if not title or title == link or title.startswith("http"):
                                title = ""
                            else:
                                title = title[:100]
                            # 卡片缩略图（用于后续相似度对比），相似图卡片的图片在锚点的兄弟节点里，需要向上找
                            thumb = ""
                            try:
                                node = link_elem
                                for _ in range(6):
                                    imgs = node.find_elements(By.CSS_SELECTOR, "img")
                                    for im in imgs:
                                        src = (im.get_attribute("data-src") or im.get_attribute("src") or "").strip()
                                        if not src:
                                            continue
                                        # 谷歌有时把结果缩略图以 base64 内嵌（data URI），要保留下来用于相似度对比
                                        if src.startswith("data:image/") and len(src) > 2000:
                                            thumb = src
                                            break
                                        if src.startswith("http") and "favicon" not in src and "google.com/s2" not in src:
                                            thumb = src
                                            break
                                    if thumb:
                                        break
                                    node = node.find_element(By.XPATH, "..")
                            except Exception:
                                pass
                            result["pages"].append({"title": title, "link": link, "thumb": thumb})
                            # 只取谷歌结果前 20 条（靠后的图片相似度会下降）
                            if len(result["pages"]) >= 20:
                                break
                        except Exception:
                            continue
                    logger.info(f"[搜图] 提取到 {len(result['pages'])} 个来源网页")
                except Exception as e:
                    logger.warning(f"[搜图] 提取来源网页失败: {e}")
    
                # 提取相似图片（外观匹配区域 + 结果页缩略图）
                # 注意：Google 结果图来自 encrypted-tbn*.gstatic.com 或 base64 data URI，
                # 不能按 "gstatic" 过滤，否则一张都提取不到（这是之前"0张图片"的根因）
                try:
                    seen_imgs = set()
                    preferred = []   # 真实结果缩略图（优先发送）
                    fallback = []    # 其他图 / base64
                    thumb_to_page = {}  # 缩略图URL -> 卡片外链（用于相似度对比定位网站）
                    try:
                        # 先滚动到相似图区域，触发缩略图懒加载
                        # 兼容谷歌多种class命名（改版频繁），增加更多选择器
                        first_card = None
                        for sel in (".Bn16af", ".cRH23c", ".zO5mFe", ".V4dun", ".iELo6", ".RAyjq", "[data-tbnid]", "div[jscontroller]"):
                            try:
                                els = driver.find_elements(By.CSS_SELECTOR, sel)
                                if els:
                                    first_card = els[0]
                                    break
                            except Exception:
                                pass
                        if first_card:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", first_card)
                            _interruptible_sleep(1, epoch)
                        else:
                            # 特定 class 全部未命中：直接滚动到页面中间触发懒加载
                            driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
                            _interruptible_sleep(1, epoch)
                    except Exception:
                        # 特定 class 未命中（谷歌改版）：退而滚动到第一个真实缩略图
                        try:
                            first_thumb = None
                            for sel in ("img[data-src*='encrypted-tbn']", "img[src*='encrypted-tbn']", "img[data-src*='gstatic.com']", "img[src*='gstatic.com']", "img[src*='usercontent.google.com']"):
                                try:
                                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                                    if els:
                                        first_thumb = els[0]
                                        break
                                except Exception:
                                    pass
                            if first_thumb:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", first_thumb)
                                _interruptible_sleep(1, epoch)
                            else:
                                # 兜底：直接滚动到底部再回来，触发所有懒加载
                                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                                _interruptible_sleep(1, epoch)
                                driver.execute_script("window.scrollTo(0, 0);")
                                _interruptible_sleep(0.5, epoch)
                        except Exception:
                            pass
                    imgs = driver.find_elements(By.CSS_SELECTOR, "img")
                    for img in imgs:
                        try:
                            src = img.get_attribute("src") or ""
                            data_src = img.get_attribute("data-src") or ""
                            # 懒加载图片的真实地址通常在 data-src
                            if data_src.startswith("http"):
                                src = data_src
                            if not src:
                                continue
                            if src.startswith("data:image"):
                                # base64 图片：过滤 1px 占位图和过小的图（favicon 级别），保留真正预览图
                                if len(src) > 500 and "R0lGODlhAQABAIAAAP" not in src and src not in seen_imgs:
                                    seen_imgs.add(src)
                                    fallback.append(src)
                            elif src.startswith("http"):
                                # 排除 logo / 头像 / favicon / svg
                                if any(x in src for x in (
                                    ".svg", "/favicon", "faviconV2", "favicon-tbn",
                                    "gstatic.com/images/branding", "ssl.gstatic.com/gb/images",
                                    "lh3.googleusercontent.com/ogw", "accounts.google.com"
                                )):
                                    continue
                                if src not in seen_imgs:
                                    seen_imgs.add(src)
                                    # 真实结果图：谷歌缩略图（httpx可直接下载）
                                    # 放宽判断：只要是 gstatic.com 的图片就算真实缩略图（兼容谷歌改版）
                                    # encrypted-tbn 格式通常是 encrypted-tbn0.gstatic.com/images?q=tbn:...
                                    if "encrypted-tbn" in src or ("gstatic.com" in src and "images" in src):
                                        preferred.append(src)
                                    elif "usercontent.google.com" in src or "lens.google.com" in src:
                                        # Google 会话代理图，httpx/浏览器都经常拿不到，直接不发送
                                        continue
                                    elif "google.com" in src or "googleusercontent.com" in src or "gstatic.com" in src:
                                        continue  # 其他 google 域名资源不要
                                    else:
                                        fallback.append(src)
                                    # 尝试从卡片里找到这张图对应的外链（用于相似度对比）
                                    if src not in thumb_to_page:
                                        try:
                                            node = img
                                            for _ in range(6):
                                                node = node.find_element(By.XPATH, "..")
                                                links = node.find_elements(By.CSS_SELECTOR, "a[href^='http']")
                                                if links:
                                                    href = links[0].get_attribute("href") or ""
                                                    if "google" not in href:
                                                        thumb_to_page[src] = href
                                                    break
                                        except Exception:
                                            pass
                        except Exception:
                            continue
                        if len(preferred) + len(fallback) >= 40:
                            break
                    result["images"] = preferred + fallback
                    result["thumb_pages"] = thumb_to_page
                    logger.info(f"[搜图] 提取到 {len(result['images'])} 张相似图片（其中真实缩略图 {len(preferred)} 张）")
                except Exception as e:
                    logger.warning(f"[搜图] 提取相似图片失败: {e}")
    
            _extract_results()
            # 结构变化告警：页面里明明有外链却一条来源网页都没提取到 → 谷歌页面结构可能变了
            if not result["pages"]:
                try:
                    ext_links = len(driver.find_elements(By.CSS_SELECTOR, "#search a[href^='http']"))
                    if ext_links >= 3:
                        logger.warning("[搜图] ⚠️ 谷歌结果页结构可能已变化：页面存在外链但未提取到来源网页（class 选择器可能失效）")
                except Exception:
                    pass
            # 结果太少时（Google 懒加载/渲染慢），等几秒滚动后再提取一次
            if len(result["pages"]) < 3 and len(result["images"]) < 6:
                logger.info("[搜图] 结果较少，滚动触发懒加载后重新提取...")
                for _ in range(2):
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    _interruptible_sleep(0.5, epoch)
                # 轮询等待 DOM 里的结果继续增长（懒加载完成即继续），比固定等 3 秒更早进入重新提取
                try:
                    def _dom_result_count():
                        return len(driver.find_elements(By.CSS_SELECTOR, "#search a[href^='http']")) + \
                               len(driver.find_elements(By.CSS_SELECTOR, "img[data-src*='encrypted-tbn'], img[src*='encrypted-tbn'], img[data-src*='gstatic.com'], img[src*='gstatic.com']"))
                    dom_before = _dom_result_count()
                    for _ in range(5):
                        if aborted(epoch):
                            break
                        _interruptible_sleep(0.6, epoch)
                        if _dom_result_count() > dom_before:
                            break
                except Exception:
                    pass
                result["pages"] = []
                result["images"] = []
                _extract_results()
    
            # Lens 的普通“全部”页有时只给同角色图片，精确来源会藏在“完全匹配”(udm=48) 页面。
            # 该页面里的 X 状态页通常能直接取到 pbs.twimg.com 原图，必须优先于 X 个人主页。
            exact_pages = []
            normal_result_url = driver.current_url or ""
            exact_match_url = _find_google_exact_match_url(driver)
            if exact_match_url and "udm=48" not in normal_result_url:
                from urllib.parse import urlparse
                exact_log_url = f"{urlparse(exact_match_url).netloc}{urlparse(exact_match_url).path}"[:120]
                logger.info(f"[搜图] 发现 Lens 完全匹配页，开始提取精确来源: {exact_log_url}")
                try:
                    browser.minimized_navigate(driver, lambda: driver.get(exact_match_url))
                    browser.keep_window_minimized(driver)
                    # page_load_strategy=none 下等待外部来源锚点出现，避免精确页尚未渲染就提前结束。
                    for _ in range(20):
                        if aborted(epoch):
                            raise SearchAbortedError("搜图已中止")
                        exact_pages = _extract_google_result_pages(driver, limit=12, exact=True)
                        if exact_pages:
                            break
                        _interruptible_sleep(0.75, epoch)
                    if exact_pages:
                        logger.info(f"[搜图] 完全匹配页提取到 {len(exact_pages)} 个精确来源")
                        for page in exact_pages[:5]:
                            parsed = urlparse(page.get("link", ""))
                            logger.info(f"[搜图]   精确来源: {parsed.netloc}{parsed.path}"[:180])
                    else:
                        logger.info("[搜图] 完全匹配页暂未提取到外部来源")
                except SearchAbortedError:
                    raise
                except Exception as e:
                    logger.warning(f"[搜图] 完全匹配页提取失败: {e}")
            elif "udm=48" in normal_result_url:
                # 兼容浏览器复用时恰好停留在精确页的情况。
                exact_pages = _extract_google_result_pages(driver, limit=12, exact=True)
                if exact_pages:
                    logger.info(f"[搜图] 当前已在 Lens 完全匹配页，提取到 {len(exact_pages)} 个精确来源")
    
            if exact_pages:
                result["pages"] = _prioritize_exact_pages(exact_pages + result["pages"])[:20]
        except lens_ui.LensUIError as exc:
            result['google_error'] = str(exc)
            result['search_error'] = 'google_unavailable'
            result['pages'], result['images'] = [], []
            logger.warning(f'[搜图] Google 阶段不可用：{exc}；继续备用搜索')
            try:
                driver.save_screenshot(config.GOOGLE_ERROR_SCREENSHOT)
            except Exception:
                pass

        # ========== 选站：缩略图相似度排序 + 接近时白名单优先级（前 15 条结果） ==========
        # 规则：相似度用"裁剪宽容"方式计算（缩略图被裁剪过也能得到公平分数）；
        # 优先访问相似度高的网站；只有相似度接近（同一档，默认 5 分内）的网站，
        # 才按白名单优先级访问（Danbooru > pixiv > bobopic > gelbooru > rule34.us
        #  > X > hoyolab > Zerochan > skebetter）；只实际访问白名单网站。
        logger.info("[搜图] 寻找高清原图...")

        # 查询图字节（用于缩略图相似度排序和"无关图防护"粗校验）
        query_bytes = None
        try:
            with open(image_path, "rb") as f:
                query_bytes = f.read()
        except Exception:
            pass

        # 竖屏截图类查询图（顶部状态栏+底部播放器控件）会拉低内部相似度/视觉打分：
        # 先裁剪掉 UI 部分、只留画面主体，再用于缩略图匹配和视觉打分
        # （is_small_image 用裁剪前的原始字节判断，避免格式转换后误判）
        raw_query_bytes = query_bytes
        query_bytes = _crop_ui_query_bytes(query_bytes) if query_bytes else None
        if query_bytes and raw_query_bytes and query_bytes != raw_query_bytes:
            logger.info("[搜图] 查询图为竖屏截图，已裁剪顶部状态栏/底部控件，用于内部相似度与视觉打分")

        # 方案C：小图/表情包/动图（<30KB 或 GIF）缩略图打分普遍偏低，
        # 选站门槛从 50 放宽到 40，避免真图因打分低被排除；
        # 下载后的字节比对 PIL 默认取 GIF 第一帧，天然规避动图随机帧问题
        is_small_image = bool(raw_query_bytes) and (
            len(raw_query_bytes) < 30 * 1024
            or raw_query_bytes[:6] in (b"GIF87a", b"GIF89a")
        )
        effective_gate = 40 if is_small_image else config.SIM_GATE
        if is_small_image:
            logger.info(f"[搜图] 查询图为小图/动图（{len(query_bytes)} 字节），选站门槛放宽到 {effective_gate}")

        top_pages = result["pages"][:15]
        thumb_scores = {}
        for page in top_pages:
            thumb_scores[page.get("link", "")] = -1
        # 缩略图下载+打分并行（串行要 10 多秒，4 路并行只要 3~5 秒）
        def _score_page(page):
            link = page.get("link", "")
            thumb = page.get("thumb", "")
            if not thumb or not query_bytes:
                return link, -1
            data = _download_thumb_bytes(thumb)
            if not data:
                return link, -1
            return link, _image_similarity_crop_tolerant(query_bytes, data)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for link, sim in pool.map(_score_page, top_pages):
                thumb_scores[link] = sim
                if sim >= 0:
                    logger.info(f"[搜图] 缩略图相似度 {sim}/64（裁剪宽容） <- {link[:70]}")

        def _visit_sort_key(page):
            """相似度档位高的优先（裁剪宽容哈希）；同一档（相似度接近）内按白名单优先级；同站相似度高的在前"""
            link = page.get("link", "")
            sim = thumb_scores.get(link, -1)
            site = config._match_site(link)
            prio = config.SITE_CONFIG.get(site, {}).get("weight", -1) if site else -1
            band = sim // config.SIM_NEAR_MARGIN if sim >= 0 else -1
            return (band, prio, sim)

        # 只实际访问白名单网站：非白名单站点（Reddit/YouTube/Facebook 等）仅用于"来源网页"展示，
        # 不逐个打开，避免浪费时间也降低被谷歌风控盯上的风险
        whitelist_visit = [p for p in top_pages if config._match_site(p.get("link", ""))]
        # 精确匹配页没有可比较的 Google 缩略图，不能让 -1 分落入低分站点的数量上限。
        # 这类来源必须先访问，尤其是 X /status/ 页面可以直接取到原图媒体。
        exact_visit = [p for p in whitelist_visit if p.get("exact")]
        regular_whitelist = [p for p in whitelist_visit if not p.get("exact")]
        primary_visit = [p for p in regular_whitelist if thumb_scores.get(p.get("link", ""), -1) >= effective_gate]
        # 访问顺序：精确来源 > 达到门槛的普通站点 > 其余普通白名单站。
        # <35 分的普通低分站最多访问 5 个，避免一个结果页拖垮整轮搜索。
        # 总访问数由外层 MAX_SITE_VISITS 兜底。
        rest = sorted([p for p in regular_whitelist if p not in primary_visit], key=_visit_sort_key, reverse=True)
        high_rest = [p for p in rest if thumb_scores.get(p.get("link", ""), -1) >= 35]
        low_rest = [p for p in rest if thumb_scores.get(p.get("link", ""), -1) < 35][:5]
        visit_order = exact_visit + sorted(primary_visit, key=_visit_sort_key, reverse=True) + high_rest + low_rest
        if exact_visit:
            logger.info(f"[搜图] {len(exact_visit)} 个完全匹配来源进入优先访问队列")
        if primary_visit:
            logger.info(f"[搜图] {len(primary_visit)} 个白名单站点达到门槛（≥{effective_gate}），先访问它们；其余按相似度访问（<35 分低分站限 5 个）")
        else:
            logger.info(f"[搜图] 没有普通白名单站点达到门槛（≥{effective_gate}），按相似度访问白名单站（<35 分低分站限 5 个）")
        skipped_non_whitelist = len(top_pages) - len(whitelist_visit)
        if skipped_non_whitelist:
            logger.info(f"[搜图] 跳过 {skipped_non_whitelist} 个非白名单站点（不实际访问）")
        if visit_order:
            best_link = visit_order[0].get("link", "")
            best_site = config._match_site(best_link) or "非白名单"
            logger.info(f"[搜图] 第一个访问的站点: [{best_site}] 相似度 {thumb_scores.get(best_link, -1)}/64")

        def _is_valid_image(candidate: bytes) -> bool:
            """候选必须是有效图片（404/错误页返回的 HTML 会被拒）"""
            try:
                from PIL import Image
                from io import BytesIO
                img = Image.open(BytesIO(candidate))
                img.load()  # 确保是有效图片（404/错误页返回的 HTML 会被拒）
                return _looks_like_image(candidate)
            except Exception:
                return False

        # 按访问顺序逐页处理（浏览器窗口保持最小化，不打扰用户工作）
        visited_count = 0
        vision_candidates = []  # 弱候选（dHash 20~49 分）：收集起来交给视觉模型打分

        def _accept_candidate(original_url, image_data, via, site, page_url, exact_source=False):
            """接受候选：dHash≥50 直接作为高清原图返回 True；
            Google 完全匹配的具体详情页允许 dHash≥40 直接返回；
            其余 dHash 20~49 的弱候选收集起来（交给视觉模型打分），返回 False 继续找"""
            nonlocal vision_candidates
            if not image_data or len(image_data) <= 1000:
                return False
            # 不是有效图片时：检查是否是视频，如果是，提取第一帧
            if not _is_valid_image(image_data):
                if download._is_video_bytes(image_data):
                    logger.info(f"[搜图] 候选是视频（{len(image_data)} 字节），尝试提取第一帧: {original_url[:60]}...")
                    frame = download._extract_video_first_frame(image_data)
                    if frame and _is_valid_image(frame):
                        image_data = frame
                        original_url = original_url + "#frame"  # 标记是视频第一帧
                    else:
                        return False
                else:
                    return False
            if _is_placeholder_small(image_data):
                logger.warning(f"[搜图] 候选疑似占位图（{len(image_data)} 字节），丢弃: {original_url[:70]}")
                return False
            sim = _image_similarity(query_bytes, image_data) if query_bytes else 64
            trusted_exact = exact_source and _is_trusted_exact_page(page_url)
            accept_threshold = 40 if trusted_exact else 50
            if sim >= accept_threshold:
                result["original_image"] = original_url
                result["original_source"] = site if site != "generic" else config._page_site_key(page_url)
                result["original_page"] = page_url
                result["original_image_data"] = image_data
                result["original_similarity"] = sim
                result["original_from_exact_match"] = trusted_exact
                confidence = "（Google完全匹配详情页，dHash门槛40）" if trusted_exact else ""
                logger.info(f"[搜图] 🏆 从 [{site}] 获取高清原图成功{via}{confidence}: {original_url[:80]}")
                return True
            # 弱候选：收集起来交给视觉模型打分（限制数量，按 dHash 从高到低保留）
            if sim >= 20:
                vision_candidates.append({
                    "url": original_url,
                    "source": site if site != "generic" else config._page_site_key(page_url),
                    "page": page_url,
                    "data": image_data,
                    "dhash": sim,
                    "area": _image_pixel_area(image_data),
                    "via": via,
                })
                _trim_vision_candidates(vision_candidates, config.VISION_MAX_CANDIDATES)
                logger.info(f"[搜图] 收集弱候选（dHash {sim}/64, {via}）: {original_url[:70]}")
            return False

        source_deadline = None
        attempted_direct = set()

        def _process_page(page_url, site, via, enforce_budget=True, exact_source=False):
            """访问一个来源页面：提取候选 URL → 下载 → 尝试接受；找到 ≥50 原图返回 True"""
            try:
                browser.ensure_live_window(driver)
                logger.info(f"[搜图] 访问 [{site}] 页面（{via}）: {page_url[:70]}...")
                page_start = time.time()
                page_deadline = min(page_start + config.SITE_VISIT_SECONDS, source_deadline or float("inf"))

                def _visit_budget_exhausted():
                    return enforce_budget and time.time() >= page_deadline

                if aborted(epoch):
                    raise SearchAbortedError("搜图已中止")
                candidate_urls = extract_original_image(driver, page_url, site, epoch, query_bytes, deadline=page_deadline) or []
                # 同一个原图 URL 可能被返回多次，去重避免重复下载浪费时间
                seen_urls = set()
                deduped = []
                for u in candidate_urls:
                    if u not in seen_urls:
                        seen_urls.add(u)
                        deduped.append(u)
                candidate_urls = deduped
                browser_candidates = []
                for original_url in candidate_urls:
                    if aborted(epoch):
                        raise SearchAbortedError("搜图已中止")
                    if _visit_budget_exhausted():
                        logger.info(f"[搜图] [{site}] 当前页面访问已超过单站预算，停止剩余候选")
                        return False
                    # 快通道：httpx 直连（twitter/zerochan 等几秒内完成）
                    if site == "bobopic":
                        bobopic_pid = sites._bobopic_pid_of(original_url)
                        image_data = sites._bobopic_download_bytes(bobopic_pid) if bobopic_pid else _try_download_bytes(original_url, site)
                    else:
                        image_data = _try_download_bytes(original_url, site, deadline=page_deadline)
                    if image_data and len(image_data) > 1000:
                        if _accept_candidate(original_url, image_data, via, site, page_url, exact_source):
                            return True
                    elif download.access_denied(original_url):
                        result['source_access_error'] = True
                    else:
                        browser_candidates.append(original_url)
                # httpx 都拿不到时，用浏览器逐个试（防盗链/需登录的站）
                if not result["original_image"] and browser_candidates:
                    if aborted(epoch):
                        raise SearchAbortedError("搜图已中止")
                    if _visit_budget_exhausted():
                        logger.info(f"[搜图] [{site}] 当前页面访问已超过单站预算，停止浏览器候选")
                        return False
                    # 每个页面最多用浏览器试 2 个候选，避免被 Cloudflare 卡死的 CDN 拖住；
                    # 但 Danbooru 帖子可能带子帖、候选较多，放宽到 6 个，
                    # 否则父帖的 URL 会把前两个槽位占满，子帖原图永远轮不到
                    browser_limit = 6 if site == "danbooru" else 2
                    for original_url in browser_candidates[:browser_limit]:
                        if aborted(epoch):
                            raise SearchAbortedError("搜图已中止")
                        if _visit_budget_exhausted():
                            logger.info(f"[搜图] [{site}] 当前页面访问已超过单站预算，停止浏览器候选剩余项")
                            return False
                        image_data = None
                        # Danbooru：优先在帖子页上下文 fetch（保留 Referer/cookie）
                        if site == "danbooru":
                            image_data = sites._browser_fetch_danbooru_original(driver, original_url, page_url, epoch, deadline=page_deadline)
                        if not image_data and "pximg.net" in original_url:
                            image_data = sites._browser_fetch_pixiv_original(driver, original_url, epoch)
                        if not image_data:
                            image_data = browser._fetch_image_bytes_via_browser(
                                driver, original_url, epoch, site, quick=True, deadline=page_deadline
                            )
                        if image_data and len(image_data) > 1000:
                            if _accept_candidate(original_url, image_data, via + "浏览器", site, page_url, exact_source):
                                return True
                        # 扩展名兜底：data-file-url / 构造 URL 可能扩展名不对（png 实际是 jpg 等）
                        found = False
                        for alt_url in _alternate_ext_urls(original_url):
                            if aborted(epoch):
                                raise SearchAbortedError("搜图已中止")
                            if _visit_budget_exhausted():
                                logger.info(f"[搜图] [{site}] 当前页面访问已超过单站预算，停止扩展名兜底")
                                return False
                            alt_data = None
                            if site == "danbooru":
                                alt_data = sites._browser_fetch_danbooru_original(driver, alt_url, page_url, epoch, deadline=page_deadline)
                            if not alt_data:
                                alt_data = browser._fetch_image_bytes_via_browser(
                                    driver, alt_url, epoch, site, quick=True, deadline=page_deadline
                                )
                            if alt_data and len(alt_data) > 1000:
                                if _accept_candidate(alt_url, alt_data, via + "浏览器", site, page_url, exact_source):
                                    found = True
                                    break
                        if found:
                            return True
                        logger.warning(f"[搜图] [{site}] 浏览器下载原图失败(404等)，换下一个候选: {original_url[:70]}...")
                return False
            except SearchAbortedError:
                raise
            except Exception as e:
                logger.warning(f"[搜图] 访问 [{site}] 失败: {e}")
                return False

        def _process_direct_url(original_url, site, via):
            """直接下载一个图片 URL 并尝试接受（Yandex 给的原图直链）"""
            if original_url in attempted_direct or (source_deadline and time.time() >= source_deadline):
                return False
            attempted_direct.add(original_url)
            image_data = _try_download_bytes(original_url, site, deadline=source_deadline)
            # A confirmed access denial is not a guessed extension problem.
            if download.access_denied(original_url):
                result['source_access_error'] = True
                return False
            if not image_data:
                image_data = browser._fetch_image_bytes_via_browser(
                    driver, original_url, epoch, site, quick=True, deadline=source_deadline)
            if image_data and len(image_data) > 1000:
                return _accept_candidate(original_url, image_data, via, site, original_url)
            return False


        def _vision_select():
            """对收集的弱候选做视觉打分，选最相似的输出。
            已有结果时，只有新候选是"同一张图"（或旧结果只是同角色）才替换"""
            nonlocal vision_candidates
            if not vision_candidates:
                return
            existing_same_image = bool((result.get("vision") or {}).get("same_image"))
            if result["original_image"]:
                if not result.get("vision"):
                    return  # dHash≥50 找到的高清原图，不再重复打分
                if existing_same_image:
                    return  # 已有同一张图的视觉结果
            vision_judged = False  # 视觉模型是否至少成功返回过一次判断
            if config.ai_backend.api_key():
                try:
                    for cand in vision_candidates:
                        judge = download.vision_judge_similarity(query_bytes, cand["data"])
                        if not judge:
                            result['vision_error'] = True
                            continue
                        vision_judged = True
                        cand["vision"] = judge
                        logger.info(f"[搜图] 视觉评分 {judge.get('similarity', 0)}/100 "
                                    f"same_image={judge.get('same_image', False)} "
                                    f"same_character={judge.get('same_character', False)} <- {cand['url'][:70]}")
                    best_cand = _pick_best_vision_candidate(
                        vision_candidates, config.VISION_SIM_THRESHOLD, existing_same_image
                    )
                    if best_cand:
                        best_vsim = float((best_cand.get("vision") or {}).get("similarity") or 0)
                        result["original_image"] = best_cand["url"]
                        result["original_source"] = best_cand["source"]
                        result["original_page"] = best_cand["page"]
                        result["original_image_data"] = best_cand["data"]
                        result["original_similarity"] = best_cand["dhash"]
                        result["vision"] = best_cand.get("vision") or {}
                        logger.info(f"[搜图] ✅ 视觉模型选定相似图（{best_vsim}/100）: {best_cand['url'][:70]}")
                except Exception as e:
                    logger.warning(f"[搜图] 视觉打分失败: {e}")
            # 视觉模型完全没给出判断（未配置/全部失败）时，才退回 dHash≥35 保底；
            # 视觉已明确拒绝的候选不能用 dHash 兜底，避免无关图被标成"相似图"
            if not result["original_image"] and not vision_judged:
                for cand in vision_candidates:
                    if cand["dhash"] >= 35:
                        result["original_image"] = cand["url"]
                        result["original_source"] = cand["source"]
                        result["original_page"] = cand["page"]
                        result["original_image_data"] = cand["data"]
                        result["original_similarity"] = cand["dhash"]
                        logger.info(f"[搜图] 视觉不可用，退回 dHash 弱候选（{cand['dhash']}/64）")
                        break

        for page in visit_order:
            if result["original_image"]:
                break
            if time.time() - search_start > config.SEARCH_STOP_VISIT_SECONDS:
                logger.info(
                    f"[搜图] 已搜 {time.time() - search_start:.0f}s，超过 {config.SEARCH_STOP_VISIT_SECONDS}s，"
                    "不再访问新站点，直接输出已有候选"
                )
                break
            visited_count += 1
            if visited_count > config.MAX_SITE_VISITS:
                logger.info(f"[搜图] 已按白名单优先级访问完 {config.MAX_SITE_VISITS} 个站点，不再继续打开其他网站")
                break
            site = config._match_site(page.get("link", "")) or "generic"
            if _process_page(
                page.get("link", ""), site, "谷歌来源",
                exact_source=bool(page.get("exact")),
            ):
                break

        # 第一轮视觉打分（谷歌候选）
        _vision_select()

        # Yandex 兜底：谷歌没找到"同一张图"（无结果或只有同角色图）时，用 Yandex 再搜
        need_yandex = (not result["original_image"]) or (result.get("vision") and not result["vision"].get("same_image"))
        if need_yandex and config.YANDEX_FALLBACK:
            browser.ensure_live_window(driver)
            yandex_pages = search_with_yandex(driver, image_path, epoch)
            if yandex_pages:
                # 记录 Yandex 定位到的来源（图片下载不到时回复里展示链接）
                result["yandex_sources"] = yandex_pages[:3]
                logger.info(f"[搜图] Yandex 兜底按相似度顺序访问前 {config.YANDEX_MAX_PAGES} 个来源")
                visited = 0
                # Yandex 阶段独立计时：谷歌阶段预算用完后，Yandex 来源访问不再被整体超时掐断
                yandex_start = time.time()
                source_deadline = yandex_start + config.SEARCH_YANDEX_BUDGET_SECONDS
                for page_url in yandex_pages[:config.YANDEX_MAX_PAGES]:
                    if result["original_image"] and (result.get("vision") or {}).get("same_image"):
                        break
                    if time.time() - yandex_start > config.SEARCH_YANDEX_BUDGET_SECONDS:
                        logger.info(
                            f"[搜图] Yandex 来源访问已耗时 {time.time() - yandex_start:.0f}s，"
                            f"超过 {config.SEARCH_YANDEX_BUDGET_SECONDS}s，停止访问 Yandex 来源"
                        )
                        break
                    # 快速探测只做记录：httpx 403 可能是 Cloudflare 人机验证墙，
                    # 但真实浏览器（可见窗口 + 用户 profile）可能能过验证，所以仍尝试访问
                    try:
                        import httpx as _httpx
                        with _httpx.Client(
                            proxy=config.PROXY_URL if config.PROXY_URL else None,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                            timeout=8, follow_redirects=True
                        ) as _c:
                            _r = _c.get(page_url)
                        if _r.status_code in (401,403,429):
                            result['source_access_error'] = True
                            logger.warning(f'[搜图] 来源拒绝访问 HTTP {_r.status_code}，跳过，不刷新/猜扩展名')
                            continue
                        if _r.status_code >= 400:
                            logger.info(f"[搜图] Yandex 来源 httpx 返回 HTTP {_r.status_code}（可能是验证墙），仍尝试浏览器访问: {page_url[:70]}")
                    except Exception:
                        pass  # 探测失败也尝试访问（可能只是网络抖动）
                    visited += 1
                    site = config._match_site(page_url) or "generic"
                    # 直链（或缩略图 URL）优先构造原图 URL 直接下载；
                    # Yandex 给的缩略图链接常不带扩展名，但只要构造出原图 URL 就走直链
                    constructed = _construct_original_url(page_url, site)
                    if _is_direct_image_url(page_url) or constructed:
                        target = constructed or page_url
                        if _process_direct_url(target, site, "yandex直链"):
                            break
                    else:
                        if _process_page(page_url, site, "yandex来源", enforce_budget=True):
                            break
                # 第二轮视觉打分（合并 Yandex 候选）
                _vision_select()
                # 若取到的是图片直链，来源网页换成 Yandex 结果里对应的页面链接（更好看、更有用）
                if result.get("original_image") and _is_direct_image_url(result.get("original_page", "")):
                    for page_url in yandex_pages:
                        if not _is_direct_image_url(page_url):
                            result["original_page"] = page_url
                            logger.info(f"[搜图] 来源网页改用页面链接: {page_url[:80]}")
                            break

        if not result["original_image"]:
            if result.get('vision_error') or result.get('source_access_error'):
                result.setdefault('search_error', 'upstream_unavailable')
            logger.info("[搜图] 白名单网站均未获取到可信原图")

        logger.info(f"[搜图] 提取完成: {len(result['images'])} 张图片, {len(result['pages'])} 个网页, 原图: {'有' if result['original_image'] else '无'}")

        # 没提取到任何来源网页时，快速探测谷歌连通性，给用户明确提示而不是笼统的"没搜到"；
        # 只有连接/超时类异常才判定代理故障，HTTP 非 204 不直接算梯子掉了
        if not result["pages"]:
            proxy_ok = False
            proxy_failed = False
            try:
                with httpx.Client(
                    proxy=config.PROXY_URL if config.PROXY_URL else None,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=6, follow_redirects=True
                ) as client:
                    resp = client.get("https://www.google.com/generate_204")
                    proxy_ok = resp.status_code in (200, 204)
            except httpx.NetworkError as e:
                proxy_failed = True
                logger.warning(f"[搜图] 代理/网络连通性探测失败: {e}")
            except Exception as e:
                logger.warning(f"[搜图] 谷歌连通性探测异常（不判定代理故障）: {e}")
            if not proxy_ok and proxy_failed:
                logger.warning("[搜图] 检测到代理/网络异常（谷歌不可达），可能是梯子掉了")
                result["proxy_error"] = True

    except SearchAbortedError:
        logger.warning("[搜图] 搜图被中止（超时/强制重置）")
    except Exception as e:
        result["search_error"] = "browser_setup" if "DRIVER_" in str(e) or "EDGE_VERSION_" in str(e) else "search_runtime"
        result["error_type"] = type(e).__name__
        logger.error(f"[搜图] 谷歌搜图失败: {e}")
        import traceback
        traceback.print_exc()

        # 如果浏览器出错了，就销毁实例，下次重新创建
        if driver is runtime._driver:
            logger.warning("[搜图] 浏览器出错，销毁实例...")
            try:
                driver.quit()
            except Exception:
                pass
            runtime._driver = None
    finally:
        # 复用标签页模式：搜图用的就是第一个标签页，保留它复用，不关闭
        try:
            if driver and driver.window_handles:
                browser.minimized_navigate(driver, lambda: driver.switch_to.window(driver.window_handles[0]))
                browser.keep_window_minimized(driver)
        except Exception:
            pass

        # 停止持续后台压制线程
        browser._stop_suppression()

        # 释放锁（不关闭浏览器，复用）
        browser._release_driver()

    return result




