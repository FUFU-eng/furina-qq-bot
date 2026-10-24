"""米游社公开接口封装：官方论坛帖子列表 + 账号映射 + 帖子块解析 + 文件下载。

独立于 NoneBot，方便单独测试。
"""

import httpx  # noqa: F401  （测试/类型）

from ...core import http as _http

API_BASE = "https://api-takumi.miyoushe.com"

# 官方论坛配置：forum_id -> 展示名 / 帖子链接 slug
# 28=原神, 53=星穹铁道, 58=绝区零, 6=崩坏3, 949=星布谷地, 947=崩坏：因缘精灵
FORUM_INFO = {
    28: {"name": "原神", "slug": "ys"},
    53: {"name": "星穹铁道", "slug": "sr"},
    58: {"name": "绝区零", "slug": "zzz"},
    6: {"name": "崩坏3", "slug": "bh3"},
    949: {"name": "星布谷地", "slug": "planet"},
    947: {"name": "崩坏：因缘精灵", "slug": "hna"},
}

# 需要推送的官号账号：uid -> 推送前缀
# 原神：原神、飞翔的丘丘人、原神千星奇域、原神-万有铺子（1号客服娘未找到公开 UID，暂不加）
ACCOUNT_PREFIX = {
    "75276539": "原神资讯推送",   # 原神
    "75276545": "原神资讯推送",   # 飞翔的丘丘人
    "378227958": "原神资讯推送",  # 原神千星奇域
    "218080295": "原神资讯推送",  # 原神-万有铺子
    "288909600": "崩铁资讯推送",  # 崩坏星穹铁道
    "152039148": "绝区零资讯推送",  # 绝区零
    "152039149": "绝区零资讯推送",  # 米仔
    "448340772": "崩缘资讯推送",  # 崩坏因缘精灵
    "378227988": "星布谷地资讯推送",  # 星布谷地
    "73565430": "崩坏三资讯推送",   # 爱酱小跟班
    "371779117": "崩坏三资讯推送",  # 乐于助人马克兔
    "50111872": "崩坏三资讯推送",   # 崩坏3空港集市
}

# 每个官方论坛只推送这些账号的帖子（uid 集合）
FORUM_ACCOUNTS = {
    28: {"75276539", "75276545", "378227958", "218080295"},
    53: {"288909600"},
    58: {"152039148", "152039149"},
    6: {"73565430", "371779117", "50111872"},
    947: {"448340772"},
    949: {"378227988"},
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.miyoushe.com/",
    "x-rpc-app_version": "2.70.1",
    "Accept": "application/json, text/plain, */*",
}

def _api():
    """接口请求共用一个连接池（每轮轮询 6 个论坛，不再反复握手）。"""
    return _http.client("miyoushe-api", headers=_HEADERS)


def _files():
    return _http.client("miyoushe-files", follow_redirects=True,
                        headers={"User-Agent": _HEADERS["User-Agent"], "Referer": "https://www.miyoushe.com/"})


async def fetch_forum_post_list(forum_id: int, page_size: int = 30, timeout: float = 15.0):
    """拉取官方论坛最新帖子列表（sort_type=2 按发布时间倒序）。返回原始 list 项。"""
    url = f"{API_BASE}/post/api/getForumPostList"
    params = {
        "forum_id": forum_id,
        "is_good": "false",
        "is_hot": "false",
        "sort_type": "2",
        "page_size": page_size,
    }
    resp = await _api().get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("retcode") != 0:
        raise RuntimeError(
            f"getForumPostList retcode={data.get('retcode')} msg={data.get('message')}"
        )
    return data.get("data", {}).get("list", []) or []


async def fetch_full_post(post_id, timeout: float = 15.0):
    """拉取帖子详情，返回帖子本体 dict（含 structured_content 用于拆块）。"""
    url = f"{API_BASE}/post/api/getPostFull"
    resp = await _api().get(url, params={"post_id": post_id}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("retcode") != 0:
        raise RuntimeError(
            f"getPostFull retcode={data.get('retcode')} msg={data.get('message')}"
        )
    return (data.get("data", {}).get("post", {}) or {}).get("post", {}) or {}


async def probe_file_size(url: str, timeout: float = 15.0):
    """HEAD 探测文件大小；不支持 HEAD 或探测失败时返回 None。"""
    try:
        resp = await _files().head(url, timeout=timeout)
        resp.raise_for_status()
        length = resp.headers.get("Content-Length")
        return int(length) if length and length.isdigit() else None
    except Exception:
        return None


async def download_file(url: str, timeout: float = 40.0, max_bytes: int | None = None) -> bytes:
    """下载米游社图片/视频文件，返回原始字节。

    max_bytes 设置后使用流式下载，超出上限立即中止并抛出 ValueError，
    避免个别超大视频（数百 MB）把内存和发送队列拖垮。
    """
    client = _files()  # 共享连接池，不在这里关闭
    if max_bytes is None:
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    async with client.stream("GET", url, timeout=timeout) as resp:
        resp.raise_for_status()
        chunks = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"文件超过 {max_bytes} 字节，已中止下载")
            chunks.append(chunk)
        return b"".join(chunks)


async def download_image(url: str, timeout: float = 20.0) -> bytes:
    """兼容旧名：下载图片文件。"""
    return await download_file(url, timeout=timeout)


def is_official_post(item: dict) -> bool:
    """判断列表项是否为官号发布的帖子。"""
    post = item.get("post", {}) or {}
    return bool((post.get("post_status") or {}).get("is_official"))


def build_push_title(prefix: str, subject: str) -> str:
    """组装推送到群的文字：xx资讯推送 + 帖子标题。"""
    title = str(subject or "").strip()
    return f"{prefix}：{title}"


def parse_post_blocks(full_post: dict) -> list:
    """把帖子的结构化内容解析成按原排版顺序的块列表。

    返回：[{"type": "text", "text": str},
           {"type": "image", "url": str},
           {"type": "video", "url": str, "cover": str, "duration": int}, ...]
    相邻的连续文字会合并成一块，空文字块会被去掉。
    米游社的「下拉窗口」（fold）会递归展开：标题变成文字块，折叠内容里的图片/文字/视频按原顺序并入。
    """
    raw = full_post.get("structured_content") or ""
    if not raw:
        return []
    try:
        import json

        ops = json.loads(raw)
    except Exception:
        return []

    blocks = _parse_delta_ops(ops)
    # 去掉空文字块
    cleaned = []
    for b in blocks:
        if b["type"] == "text" and not b["text"].strip():
            continue
        cleaned.append(b)
    return cleaned


def _delta_text(delta_str: str) -> str:
    """从 fold 的 title/content 等 Delta JSON 字符串里提取纯文字。"""
    try:
        import json

        ops = json.loads(delta_str or "[]")
    except Exception:
        return ""
    parts = []
    for op in ops:
        ins = op.get("insert")
        if isinstance(ins, str):
            parts.append(ins)
    return "".join(parts)


def _parse_delta_ops(ops: list) -> list:
    blocks = []
    for op in ops:
        ins = op.get("insert")
        if isinstance(ins, str):
            text = ins
            if blocks and blocks[-1]["type"] == "text":
                blocks[-1]["text"] += text
            else:
                blocks.append({"type": "text", "text": text})
        elif isinstance(ins, dict):
            if "image" in ins:
                url = str(ins["image"] or "").strip()
                if url.startswith("http"):
                    blocks.append({"type": "image", "url": url})
            elif "vod" in ins:
                vod = ins["vod"] or {}
                resolutions = vod.get("resolutions") or []
                if resolutions:
                    best = max(resolutions, key=lambda r: r.get("width") or 0)
                    blocks.append(
                        {
                            "type": "video",
                            "url": best.get("url", ""),
                            "cover": vod.get("cover", ""),
                            "duration": vod.get("duration", 0),
                        }
                    )
                elif vod.get("cover"):
                    blocks.append({"type": "image", "url": vod["cover"]})
            elif "fold" in ins:
                # 下拉窗口：只取折叠内容里的图片/文字/视频，标题文字直接跳过
                fold = ins["fold"] or {}
                content = fold.get("content") or ""
                try:
                    import json

                    inner_ops = json.loads(content)
                    blocks.extend(_parse_delta_ops(inner_ops))
                except Exception:
                    pass
            elif "link_card" in ins:
                # 链接卡片：展示标题和原文链接
                card = ins["link_card"] or {}
                title = str(card.get("title") or card.get("desc") or "").strip()
                origin = str(card.get("origin_url") or "").strip()
                if title and origin:
                    blocks.append({"type": "text", "text": f"{title}\n{origin}"})
                elif origin:
                    blocks.append({"type": "text", "text": origin})
    return blocks
