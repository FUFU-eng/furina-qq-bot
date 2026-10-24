"""联网搜索：芙宁娜遇到不知道、拿不准或要最新消息的事，自己上网查一下再回答。

- 免 Key：必应中国（直连）和 DuckDuckGo（走 .env 的 PROXY_URL，没配就跳过）同时查，百度兜底（常被验证码拦）；
  合并去重后按「和关键词沾不沾边」打分，把「绝（汉语文字）」这种被拆词搜出来的无关结果扔掉。
- 关键词先整理：去掉模型爱加的「2026年10月」、把「当前/现在」换成「最新」，主语加引号防止必应拆词。
- 只取搜索结果页的标题 + 摘要（每条约 100~200 字），不打开网页正文，省 token 也快（一般 1~2 秒）。
- 同一个关键词 30 分钟内直接用缓存，不重复查。
- 只依赖标准库 + httpx，面板/测试也能导入（导入时不联网）。
"""
from __future__ import annotations

import html
import re
import time

import httpx

try:  # 机器人进程里用 NoneBot 的日志；面板/测试里退回标准 logging
    from nonebot import logger as _log
except Exception:  # pragma: no cover
    import logging

    _log = logging.getLogger("furina.websearch")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}
CACHE_SECONDS = 1800
_cache: dict[str, tuple[float, list]] = {}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(fragment: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment or "", flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)  # 百度把一大段 JSON 塞在注释里
    text = re.sub(r"</?(?:em|strong|b|i|font|span|a)\b[^>]*>", "", text, flags=re.I)  # 高亮这类行内标签直接去掉，别把词拆开
    text = html.unescape(_TAG_RE.sub(" ", text))
    text = text.replace("\u2002", " ").replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def parse_bing(page: str, n: int = 5) -> list[dict]:
    out = []
    for block in (page or "").split('class="b_algo"')[1:]:
        m = re.search(r"<h2[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not m:
            continue
        title = _clean(m.group(2))
        cap = block.split('class="b_caption"', 1)[1] if 'class="b_caption"' in block else block[m.end():]
        sm = re.search(r"<p[^>]*>(.*?)</p>", cap, re.S)
        snippet = _clean(sm.group(1)) if sm else ""
        if title:
            out.append({"title": title[:90], "url": html.unescape(m.group(1)), "snippet": snippet[:230]})
        if len(out) >= n:
            break
    return out


def parse_baidu(page: str, n: int = 5) -> list[dict]:
    out = []
    for block in re.split(r"<div[^>]+class=\"result(?:-op)? c-container", page or "")[1:]:
        block = block[:6000]
        m = re.search(r"<h3[^>]*>.*?<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not m:
            continue
        title = _clean(m.group(2))
        body = _clean(block[m.end():])
        body = re.sub(r"^(?:[\w\s]{0,6}(?:百度|广告))\s*", "", body)
        if title:
            out.append({"title": title[:90], "url": html.unescape(m.group(1)), "snippet": body[:230]})
        if len(out) >= n:
            break
    return out


def parse_ddg(page: str, n: int = 5) -> list[dict]:
    out = []
    for block in re.split(r'class="[^"]*\bresult__body\b', page or "")[1:]:
        m = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        title = _clean(m.group(2))
        if title:
            out.append({"title": title[:90], "url": html.unescape(m.group(1)), "snippet": _clean(sm.group(1))[:230] if sm else ""})
        if len(out) >= n:
            break
    return out


def _proxy() -> str:
    try:
        from furina.core import env
        return (env.get("PROXY_URL", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


async def _get(url: str, params: dict, timeout: float, proxy: str = "") -> str:
    async with httpx.AsyncClient(headers=UA, timeout=timeout, follow_redirects=True, trust_env=False,
                                 proxy=proxy or None) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        if "captcha" in str(r.url) or "antispider" in str(r.url):
            raise RuntimeError("被要求验证码")
        return r.text


_STOP = ("最新", "当前", "目前", "现在", "版本", "什么", "多少", "哪个", "怎么", "是不是", "今天", "最近")
_DATE_RE = re.compile(r"20\d\d\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*[日号])?|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?(?:份)?")


def normalize(query: str) -> str:
    """整理关键词：去掉模型自己加的日期、「当前/现在/目前」换成「最新」、去重空格。"""
    q = _WS_RE.sub(" ", (query or "").strip())
    if re.search(r"最新|当前|目前|现在|今天|最近", q):
        q = _DATE_RE.sub(" ", q)
    q = re.sub(r"当前|目前|现在", "最新", q)
    q = re.sub(r"(最新)(\s*最新)+", r"\1", q)
    return _WS_RE.sub(" ", q).strip()[:60]


def _bing_query(q: str) -> str:
    """主语（第一个词，2~10 个汉字/字母）加引号：必应会把「绝区零」拆成「绝」去搜字典。"""
    parts = q.split(" ")
    if len(parts) > 1 and 2 <= len(parts[0]) <= 10 and '"' not in parts[0]:
        parts[0] = f'"{parts[0]}"'
    return " ".join(parts)


def _grams(text: str) -> set:
    t = re.sub(r"[\s\W_]+", "", (text or "").lower())
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else ({t} if t else set())


def relevance(query: str, item: dict) -> float:
    """0~1：结果和关键词沾边的程度（关键词去掉「最新/版本」这类泛词后的二字组命中率；主语不在结果里打四折）。"""
    words = [w for w in query.replace('"', "").split(" ") if w]
    hay = re.sub(r"[\s\W_]+", "", (item.get("title", "") + " " + item.get("snippet", "")).lower())
    if not words:
        return 0.0
    head = re.sub(r"[\s\W_]+", "", words[0].lower())  # 「崩坏星穹铁道」要能对上「崩坏：星穹铁道」
    penalty = 0.4 if len(words) > 1 and head not in hay else 1.0  # 主语都不在结果里，多半是拆词搜偏了
    core = "".join(w for w in words if w not in _STOP) or "".join(words)
    for sw in _STOP:
        core = core.replace(sw, "") or core
    g = _grams(core)
    if not g:
        return 1.0 if head in hay else 0.0
    hay_g = _grams(hay)
    return penalty * len(g & hay_g) / len(g)


async def _bing(q: str, n: int, timeout: float) -> list[dict]:
    return parse_bing(await _get("https://cn.bing.com/search", {"q": _bing_query(q), "ensearch": "0"}, timeout), n + 3)


async def _ddg(q: str, n: int, timeout: float) -> list[dict]:
    proxy = _proxy()
    if not proxy:
        return []
    return parse_ddg(await _get("https://html.duckduckgo.com/html/", {"q": q, "kl": "cn-zh"}, timeout, proxy), n + 3)


async def _baidu(q: str, n: int, timeout: float) -> list[dict]:
    return parse_baidu(await _get("https://www.baidu.com/s", {"wd": q, "rn": "10"}, timeout), n + 3)


BACKENDS = (("必应", _bing), ("DuckDuckGo", _ddg), ("百度", _baidu))
MIN_RELEVANCE = 0.34


async def _run(name, fn, q, n, timeout) -> list[dict]:
    try:
        return await fn(q, n, timeout)
    except Exception as e:  # noqa: BLE001  一家挂了不影响别家
        _log.info(f"[联网搜索] {name}搜「{q}」失败：{type(e).__name__}: {str(e)[:80]}")
        return []


def merge(query: str, lists: list[list[dict]], n: int) -> list[dict]:
    """多家结果合并：去重、扔掉不沾边的，按相关度排（同分保持原顺序，必应在前）。"""
    seen, scored = set(), []
    order = 0
    for lst in lists:
        for it in lst or []:
            key = re.sub(r"[\s\W_]+", "", it.get("title", "").lower())[:30]
            if not key or key in seen:
                continue
            seen.add(key)
            score = relevance(query, it)
            if score >= MIN_RELEVANCE:
                scored.append((-round(score, 1), order, it))
            order += 1
    scored.sort(key=lambda x: (x[0], x[1]))
    return [it for _s, _o, it in scored[:n]]


async def search(query: str, n: int = 5, timeout: float = 8.0) -> list[dict]:
    """返回 [{title, url, snippet}]；全部失败返回 []（调用方照实说没查到）。"""
    q = normalize(query)
    if len(q) < 2:
        return []
    n = max(1, min(8, int(n or 5)))
    hit = _cache.get(q)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1][:n]
    import asyncio

    main = [(name, fn) for name, fn in BACKENDS if name != "百度"]
    lists = await asyncio.gather(*(_run(name, fn, q, n, timeout) for name, fn in main))
    res = merge(q, list(lists), n)
    if len(res) < 2:  # 前两家都不行再试百度（百度经常弹验证码）
        lists = list(lists) + [await _run("百度", _baidu, q, n, timeout)]
        res = merge(q, lists, n)
    raw = "/".join(str(len(x)) for x in lists)
    _log.info(f"[联网搜索] 搜「{q}」：各家原始 {raw} 条（必应/DDG/百度），沾边的 {len(res)} 条")
    if res:
        _cache[q] = (time.time(), res)
        if len(_cache) > 200:
            _cache.pop(next(iter(_cache)))
    return res


def format_results(query: str, results: list[dict], now: float | None = None) -> str:
    day = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
    if not results:
        return (f"【关于「{query}」你没找到靠谱的信息】就当自己也不太清楚：像真人那样随口说「这个我还真不确定诶」"
                "「好像没注意过」之类，别编具体数字/版本号；也别提搜索、搜索结果、网上，更别吐槽搜出来的东西。")
    lines = []
    for i, r in enumerate(results, 1):
        snip = f"：{r['snippet']}" if r.get("snippet") else ""
        lines.append(f"{i}. {r['title']}{snip}")
    return (f"【你刚瞄了一眼的资料：「{query}」（今天是 {day}；只有你自己看得见）】\n" + "\n".join(lines) + "\n"
            "把它当成你本来就知道、或者前两天刚刷到的事，用你自己的口吻随口说一两个重点（比如「3.3 了吧，前两天刚出」）。"
            "绝对不要提「搜」「查」「搜索结果」「网上说」「资料显示」，不贴链接、不列清单；里面混着不相关的条目就当没看见。"
            "日期要分清：标着几天前/最新的才算现在，旧版本别当成现在的；看不出答案就说不太确定，别编。")
