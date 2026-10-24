# ============ 配置区（从环境变量读取，可在 .env 中配置） ============
import os
import tempfile

from ...core import env

_config = env.config_ns  # 旧键名，改读 .env

# 项目根目录（D:\old-qq-bot），由插件所在路径推导，避免散落的硬编码路径。
# 插件位于 <项目根>/bot/plugins/google_image_search/，其父目录上推 3 层即项目根。
PROJECT_ROOT = env.assets_root()

# 代理地址
PROXY_URL = getattr(_config, "proxy_url", "http://127.0.0.1:7890")

# 机器人QQ号（用于判断艾特）

# Edge 浏览器用户数据目录
EDGE_USER_DATA_DIR = getattr(_config, "edge_user_data_dir", os.path.join(PROJECT_ROOT, "edge_profile_v2"))

# 浏览器页面加载超时（秒）
PAGE_LOAD_TIMEOUT = int(getattr(_config, "page_load_timeout", 60))

# 搜图总超时（秒）
SEARCH_TIMEOUT = int(getattr(_config, "search_timeout", 300))
# 搜图开始后超过多少秒就不再访问新站点（直接用手头候选做视觉打分输出，防"有结果却超时"）
SEARCH_STOP_VISIT_SECONDS = int(getattr(_config, "search_stop_visit_seconds", 230))
# 单个站点页面访问预算（秒）：候选多/验证墙时最多花这么久，
# 避免一个站点把全局访问预算占满、挤掉后续站点和 Yandex 兜底
SITE_VISIT_SECONDS = int(getattr(_config, "site_visit_seconds", min(SEARCH_STOP_VISIT_SECONDS, 90)))
# Yandex 兜底阶段独立的访问时间预算（秒）：谷歌阶段把预算用完后，
# Yandex 来源访问不再沿用谷歌的时间起点，单独计时，避免"明明找到正确来源却被超时掐断"
SEARCH_YANDEX_BUDGET_SECONDS = int(getattr(_config, "search_yandex_budget_seconds", 100))

# ========== 反谷歌风控设置 ==========
# 同一用户两次搜图的最小间隔（秒）
SEARCH_COOLDOWN = int(getattr(_config, "search_cooldown", 30))
# 连续搜图之间的随机延迟范围（秒），打散请求节奏，避免被识别成程序行为
SEARCH_RANDOM_DELAY = (3, 8)
# 选站：缩略图相似度按 5 分一档；同一档内的站点视为"相似度接近"，此时按白名单优先级访问
SIM_NEAR_MARGIN = 5
# 选站门槛：裁剪宽容相似度 ≥ 此值视为"确认是同一张图"（50 分以下的多半是同角色但不同的画，
# 如 Zerochan 41/64 那种，应该在选站阶段就被排除，而不是打开页面下载完才发现不对）
SIM_GATE = 50
# 最多实际访问几个网站：默认 9（白名单全量兜底）。
# 规则：按白名单优先级逐个访问，第一个拿不到原图就继续下一个；
# 只访问白名单网站（非白名单站点不进页面），避免连环打开一堆杂站。
MAX_SITE_VISITS = 9
# Eagle 图库目录（没找到原图时随机选一张当"安慰奖"）
EAGLE_LIBRARY_DIR = getattr(_config, "eagle_library_dir", r"D:\Pictures\Eagle.library")

# One shared DeepSeek endpoint/key; no legacy provider fallback.
from ...core import llm as ai_backend  # 同一个 DeepSeek 接口：api_key() / endpoint() / models()
VISION_MODEL = ai_backend.models()[0]
VISION_MODELS = [VISION_MODEL]

# 视觉相似度门槛（0-100）：低于此分不发"相似图"
VISION_SIM_THRESHOLD = float(getattr(_config, "vision_sim_threshold", 55))
# 视觉相似度 ≥ 此值时记录"高相似但不同图"日志；模型判 same_image=False 时不再按原图输出
VISION_ORIGINAL_SIM_THRESHOLD = float(getattr(_config, "vision_original_sim_threshold", 90))
# 视觉打分最多评估的弱候选数量（每个约 3~5 秒）
VISION_MAX_CANDIDATES = int(getattr(_config, "vision_max_candidates", 5))

# Yandex 兜底以图搜图：谷歌白名单找不到"同一张图"时，用 Yandex 再搜一次（动漫识图比桌面谷歌强）
_yandex_fallback = str(getattr(_config, "yandex_fallback", "true")).strip().lower()
YANDEX_FALLBACK = _yandex_fallback in ("1", "true", "yes", "on")
# 最多访问几个 Yandex 来源网页（按 Yandex 相似度顺序）
YANDEX_MAX_PAGES = int(getattr(_config, "yandex_max_pages", 4))
# ==================================

# ========== 项目内文件路径（统一从 PROJECT_ROOT 推导） ==========
DANBOORU_COOKIES_PATH = str(getattr(_config, "danbooru_cookies_path", os.path.join(PROJECT_ROOT, "danbooru_cookies.json")))
GOOGLE_RESULT_SCREENSHOT = os.path.join(tempfile.gettempdir(), "qqbot_google_lens_result.png")
GOOGLE_ERROR_SCREENSHOT = os.path.join(tempfile.gettempdir(), "qqbot_google_lens_error.png")
# Yandex 失败诊断截图同样放系统临时目录（旧版写在项目根目录，留下 yandex_debug.png）
YANDEX_DEBUG_SCREENSHOT = os.path.join(tempfile.gettempdir(), "qqbot_yandex_debug.png")
# ==============================================================


# 各站点图片防盗链所需的 Referer
_SITE_REFERERS = {
    "twitter": "https://x.com/",
    "danbooru": "https://danbooru.donmai.us/",
    "pixiv": "https://www.pixiv.net/",
    "zerochan": "https://www.zerochan.net/",
    "hoyolab": "https://www.hoyolab.com/",
    "arca": "https://arca.live/",
    "rule34": "https://rule34.us/",
    "bobopic": "https://bobopic.com/",
    "gelbooru": "https://gelbooru.com/",
    "skebetter": "https://skebetter.com/",
}


# ========== 白名单网站配置（模块级，选站/回复展示共用） ==========
# arca.live 因有人机验证且无法自动通过，已从白名单移除
# weight 只用于"缩略图相似度接近"时的访问优先级：
# Danbooru > pixiv > bobopic > gelbooru > rule34.us > Zerochan > X > hoyolab > skebetter
SITE_CONFIG = {
    "danbooru": {"weight": 9, "domains": ["donmai.us"]},
    "pixiv": {"weight": 8, "domains": ["pixiv.net", "pximg.net"]},
    "bobopic": {"weight": 7, "domains": ["bobopic.com", "pixivdaily.com"]},
    "gelbooru": {"weight": 6, "domains": ["gelbooru.com"]},
    "rule34": {"weight": 5, "domains": ["rule34.us"]},
    "zerochan": {"weight": 4, "domains": ["zerochan.net"]},
    "twitter": {"weight": 3, "domains": ["twimg.com", "x.com", "twitter.com"]},
    "hoyolab": {"weight": 2, "domains": ["hoyolab.com", "mihoyo.com"]},
    "skebetter": {"weight": 1, "domains": ["skebetter.com"]},
}


def _match_site(url):
    """根据域名判断 URL 属于哪个白名单网站"""
    if not url:
        return None
    for site_name, site_config in SITE_CONFIG.items():
        if any(domain in url for domain in site_config["domains"]):
            return site_name
    return None


def _site_display_name(site_key):
    """白名单站点在回复里显示的名称"""
    return {
        "danbooru": "Danbooru",
        "pixiv": "Pixiv",
        "twitter": "Twitter/X",
        "hoyolab": "HoYoLAB",
        "zerochan": "Zerochan",
        "rule34": "Rule34.us",
        "bobopic": "BoBoPic",
        "gelbooru": "Gelbooru",
        "skebetter": "Skebetter",
    }.get(site_key, site_key or "")


def _page_site_key(link):
    """来源网页去重用的站点标识：白名单站点用站点 key，其他网站用域名"""
    site = _match_site(link)
    if site:
        return site
    try:
        from urllib.parse import urlparse
        netloc = (urlparse(link).netloc or "").lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return link


def __getattr__(name):
    if name == "BOT_QQ":
        return env.bot_qq()
    raise AttributeError(name)
