"""B 站 Web 接口的最小客户端：扫码登录、刷推荐/热门/搜索、读视频（简介/字幕/弹幕/热评）、点赞、评论、发动态、
回复评论区里回她的人（楼中楼）、给评论点赞、取表情列表。

- 不走系统代理（trust_env=False），B 站是国内站直连；
- 需要 WBI 签名的接口自动签名（密钥每天从 nav 接口刷新一次）；
- 风控（HTTP 412、-352、-412、-509、需要验证码……）一律抛 RiskControl，调用方必须立刻停手；
- 只依赖 httpx + 标准库，面板/测试都能导入。
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
import time
import urllib.parse

import httpx

from . import store

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
API = "https://api.bilibili.com"
PASSPORT = "https://passport.bilibili.com"
RISK_CODES = {-352, -412, -509, -799, 12015}
MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39,
         12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63,
         57, 62, 11, 36, 20, 34, 44, 52]


class BiliError(Exception):
    def __init__(self, code, message: str = ""):
        super().__init__(f"{code} {message}".strip())
        self.code = code
        self.message = message


class RiskControl(BiliError):
    """触发风控：立刻停止一切 B 站操作。"""


class NotLoggedIn(BiliError):
    pass


# ---------------- WBI 签名 ----------------
def mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN if i < len(raw))[:32]


def wbi_sign(params: dict, img_key: str, sub_key: str, wts: int | None = None) -> dict:
    p = dict(params)
    p["wts"] = int(wts if wts is not None else time.time())
    p = dict(sorted(p.items()))
    p = {k: "".join(ch for ch in str(v) if ch not in "!'()*") for k, v in p.items()}
    query = urllib.parse.urlencode(p)
    p["w_rid"] = hashlib.md5((query + mixin_key(img_key, sub_key)).encode()).hexdigest()
    return p


def _key_from_url(url: str) -> str:
    return url.rsplit("/", 1)[-1].split(".")[0] if url else ""


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def parse_duration(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    parts = [int(x) for x in re.findall(r"\d+", str(v or ""))]
    total = 0
    for x in parts:
        total = total * 60 + x
    return total


def check(resp: httpx.Response) -> dict:
    """统一校验返回：风控 / 未登录 / 其他错误分别抛异常，成功返回 data。"""
    if resp.status_code == 412:
        raise RiskControl(412, "HTTP 412 请求被拦截")
    try:
        j = resp.json()
    except ValueError:
        raise BiliError(resp.status_code, "返回的不是 JSON")
    code = j.get("code", 0)
    if code in RISK_CODES:
        raise RiskControl(code, j.get("message", ""))
    if code == -101:
        raise NotLoggedIn(code, j.get("message", "账号未登录"))
    if code != 0:
        raise BiliError(code, j.get("message", "") or j.get("msg", ""))
    return j.get("data") or {}


class Client:
    def __init__(self, cookies: dict | None = None):
        self.cookies = dict(cookies if cookies is not None else (store.get_auth().get("cookies") or {}))
        self._http = httpx.AsyncClient(
            trust_env=False, timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True,
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._http.aclose()

    # ---------- 基础 ----------
    def _cookie_header(self) -> dict:
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items() if v)} if self.cookies else {}

    @property
    def csrf(self) -> str:
        return self.cookies.get("bili_jct", "")

    async def _get(self, url: str, params: dict | None = None, *, wbi: bool = False) -> dict:
        if wbi:
            img, sub = await self.wbi_keys()
            params = wbi_sign(params or {}, img, sub)
        r = await self._http.get(url, params=params, headers=self._cookie_header())
        return check(r)

    async def _post(self, url: str, data: dict | None = None, *, json_body: dict | None = None,
                    params: dict | None = None) -> dict:
        r = await self._http.post(url, data=data, json=json_body, params=params, headers=self._cookie_header())
        return check(r)

    async def ensure_buvid(self) -> None:
        if self.cookies.get("buvid3"):
            return
        try:
            d = check(await self._http.get(API + "/x/frontend/finger/spi"))
            self.cookies["buvid3"] = d.get("b_3", "")
            self.cookies["buvid4"] = d.get("b_4", "")
            self.cookies.setdefault("b_nut", str(int(time.time())))
        except (BiliError, httpx.HTTPError):
            pass

    async def nav(self) -> dict:
        r = await self._http.get(API + "/x/web-interface/nav", headers=self._cookie_header())
        if r.status_code == 412:
            raise RiskControl(412, "HTTP 412")
        j = r.json()  # 未登录时 code=-101 但 data 里照样有 wbi_img
        return j.get("data") or {}

    async def wbi_keys(self) -> tuple[str, str]:
        cached = store.get_meta("wbi") or {}
        if cached.get("day") == time.strftime("%Y-%m-%d") and cached.get("img") and cached.get("sub"):
            return cached["img"], cached["sub"]
        d = await self.nav()
        w = d.get("wbi_img") or {}
        img, sub = _key_from_url(w.get("img_url", "")), _key_from_url(w.get("sub_url", ""))
        if img and sub:
            store.set_meta("wbi", {"day": time.strftime("%Y-%m-%d"), "img": img, "sub": sub})
        return img, sub

    # ---------- 扫码登录 ----------
    async def qr_generate(self) -> tuple[str, str]:
        d = check(await self._http.get(PASSPORT + "/x/passport-login/web/qrcode/generate"))
        return d["url"], d["qrcode_key"]

    async def qr_poll(self, key: str) -> tuple[int, dict]:
        """返回 (状态码, cookies)：0 成功 / 86101 未扫码 / 86090 已扫码待确认 / 86038 已过期。"""
        r = await self._http.get(PASSPORT + "/x/passport-login/web/qrcode/poll", params={"qrcode_key": key})
        d = check(r)
        code = int(d.get("code", -1))
        cookies: dict = {}
        if code == 0:
            for k, v in r.cookies.items():
                cookies[k] = v
            q = urllib.parse.parse_qs(urllib.parse.urlparse(d.get("url", "")).query)
            for k in ("DedeUserID", "DedeUserID__ckMd5", "SESSDATA", "bili_jct", "sid"):
                if k not in cookies and q.get(k):
                    cookies[k] = q[k][0]
            if d.get("refresh_token"):
                cookies["_refresh_token"] = d["refresh_token"]
        return code, cookies

    # ---------- 找视频 ----------
    async def feed(self, n: int = 12) -> list[dict]:
        idx = random.randint(1, 30)
        d = await self._get(API + "/x/web-interface/wbi/index/top/feed/rcmd", {
            "web_location": 1430650, "y_num": 4, "fresh_type": 4, "feed_version": "V8", "fresh_idx_1h": idx,
            "fetch_row": 1, "fresh_idx": idx, "brush": idx, "homepage_ver": 1, "ps": n}, wbi=True)
        out = []
        for it in d.get("item") or []:
            if it.get("goto") not in (None, "av") or not it.get("bvid"):
                continue
            out.append({"bvid": it["bvid"], "title": it.get("title", ""), "up": (it.get("owner") or {}).get("name", ""),
                        "duration": parse_duration(it.get("duration")), "source": "推荐", "pic": it.get("pic", "")})
        return out

    async def popular(self, n: int = 20) -> list[dict]:
        d = await self._get(API + "/x/web-interface/popular", {"ps": n, "pn": random.randint(1, 3)})
        return [{"bvid": v["bvid"], "title": v.get("title", ""), "up": (v.get("owner") or {}).get("name", ""),
                 "duration": parse_duration(v.get("duration")), "source": "热门", "pic": v.get("pic", "")}
                for v in d.get("list") or [] if v.get("bvid")]

    async def search(self, keyword: str) -> list[dict]:
        d = await self._get(API + "/x/web-interface/wbi/search/type",
                            {"search_type": "video", "keyword": keyword, "page": 1,
                             "order": random.choice(["totalrank", "pubdate", "click"])}, wbi=True)
        return [{"bvid": v["bvid"], "title": strip_html(v.get("title", "")), "up": v.get("author", ""),
                 "duration": parse_duration(v.get("duration")), "source": f"搜索「{keyword}」",
                 "pic": ("https:" + v["pic"]) if str(v.get("pic", "")).startswith("//") else v.get("pic", "")}
                for v in d.get("result") or [] if v.get("bvid")]

    # ---------- 读视频 ----------
    async def view(self, bvid: str) -> dict:
        return await self._get(API + "/x/web-interface/view", {"bvid": bvid})

    async def subtitle_text(self, bvid: str, cid: int, max_chars: int = 1500) -> str:
        try:
            d = await self._get(API + "/x/player/wbi/v2", {"bvid": bvid, "cid": cid}, wbi=True)
        except RiskControl:
            raise
        except BiliError:
            return ""
        subs = ((d.get("subtitle") or {}).get("subtitles")) or []
        if not subs:
            return ""
        subs.sort(key=lambda s: (0 if "zh" in str(s.get("lan", "")) else 1))
        url = subs[0].get("subtitle_url", "")
        if not url:
            return ""
        if url.startswith("//"):
            url = "https:" + url
        try:
            body = (await self._http.get(url)).json().get("body") or []
        except (httpx.HTTPError, ValueError):
            return ""
        text = "，".join(str(b.get("content", "")).strip() for b in body if b.get("content"))
        return text[:max_chars]

    async def danmaku(self, cid: int, n: int = 30) -> list[str]:
        try:
            r = await self._http.get(API + "/x/v1/dm/list.so", params={"oid": cid})
            xml = r.content.decode("utf-8", errors="ignore")
        except httpx.HTTPError:
            return []
        items = [html.unescape(t).strip() for t in re.findall(r"<d [^>]*>([^<]*)</d>", xml)]
        items = [t for t in dict.fromkeys(items) if 1 < len(t) <= 30 and "乾杯" not in t and "哔哩哔哩" not in t]
        random.shuffle(items)
        return items[:n]

    async def hot_comments(self, aid: int, n: int = 5) -> list[dict]:
        try:
            d = await self._get(API + "/x/v2/reply/wbi/main", {
                "oid": aid, "type": 1, "mode": 3, "pagination_str": json.dumps({"offset": ""}), "plat": 1,
                "web_location": 1315875}, wbi=True)
        except RiskControl:
            raise
        except BiliError:
            try:
                d = await self._get(API + "/x/v2/reply", {"type": 1, "oid": aid, "sort": 1, "ps": n, "pn": 1})
            except RiskControl:
                raise
            except BiliError:
                return []
        out = []
        for rp in (d.get("top_replies") or []) + (d.get("replies") or []):
            msg = ((rp.get("content") or {}).get("message") or "").strip()
            if msg:
                out.append({"user": (rp.get("member") or {}).get("uname", ""), "text": msg[:120],
                            "like": int(rp.get("like") or 0)})
        return out[:n]

    # ---------- 写操作 ----------
    async def like(self, aid: int) -> None:
        await self._post(API + "/x/web-interface/archive/like", {"aid": aid, "like": 1, "csrf": self.csrf})

    async def comment(self, aid: int, text: str, at: dict | None = None) -> str:
        data = {"oid": aid, "type": 1, "message": text, "plat": 1, "csrf": self.csrf}
        if at:
            data["at_name_to_mid"] = json.dumps(at, ensure_ascii=False)
        d = await self._post(API + "/x/v2/reply/add", data)
        return str(d.get("rpid_str") or d.get("rpid") or "")

    async def send_danmaku(self, aid: int, cid: int, text: str, progress_ms: int) -> str:
        """发一条普通白色滚动弹幕；progress_ms 是出现在视频里的毫秒位置。"""
        data = {"type": 1, "oid": cid, "aid": aid, "msg": text, "progress": max(0, int(progress_ms)),
                "color": 16777215, "fontsize": 25, "pool": 0, "mode": 1, "plat": 1,
                "rnd": int(time.time() * 1000000), "csrf": self.csrf}
        d = await self._post(API + "/x/v2/dm/post", data)
        return str(d.get("dmid_str") or d.get("dmid") or "")

    async def trending(self, n: int = 20) -> list[str]:
        """B 站热搜词（不需要登录）。先走搜索页的 square 接口，失败再走老热词接口。"""
        words: list[str] = []
        try:
            d = await self._get(API + "/x/web-interface/wbi/search/square", {"limit": 30, "platform": "web"}, wbi=True)
            words = [it.get("show_name") or it.get("keyword") for it in ((d.get("trending") or {}).get("list") or [])]
        except RiskControl:
            raise
        except (BiliError, httpx.HTTPError):
            words = []
        if not words:
            try:
                r = await self._http.get("https://s.search.bilibili.com/main/hotword", params={"limit": 30})
                words = [it.get("show_name") or it.get("keyword") for it in (r.json().get("list") or [])]
            except (httpx.HTTPError, ValueError):
                words = []
        return [w.strip() for w in words if w and str(w).strip()][:n]

    # ---------- 评论区互动（别人回复她） ----------
    async def reply_feed(self) -> list[dict]:
        """消息中心「回复我的」：最新一页（约 20 条），新的在前。"""
        d = await self._get(API + "/x/msgfeed/reply", {"platform": "web", "build": 0, "mobi_app": "web"})
        out = []
        for it in d.get("items") or []:
            item, user = it.get("item") or {}, it.get("user") or {}
            if not item.get("source_id") or not item.get("subject_id"):
                continue
            out.append({
                "id": str(it.get("id") or item.get("source_id")),
                "mid": str(user.get("mid") or ""), "name": str(user.get("nickname") or ""),
                "oid": int(item.get("subject_id") or 0), "type": int(item.get("business_id") or 1),
                "root": int(item.get("root_id") or 0), "rpid": int(item.get("source_id") or 0),
                "target": int(item.get("target_id") or 0),
                "text": str(item.get("source_content") or "").strip(),
                "mine": str(item.get("target_reply_content") or item.get("root_reply_content") or "").strip(),
                "title": str(item.get("title") or "").strip(), "uri": str(item.get("uri") or ""),
                "ts": int(it.get("reply_time") or 0)})
        return out

    async def reply_thread(self, oid: int, type_: int, root: int, n: int = 20) -> list[dict]:
        """一层楼（楼中楼）里的对话：楼主那条 + 前 n 条回复，按时间先后。"""
        d = await self._get(API + "/x/v2/reply/reply", {"oid": oid, "type": type_, "root": root, "ps": n, "pn": 1})
        rows = []
        for rp in ([d.get("root")] if d.get("root") else []) + list(d.get("replies") or []):
            msg = ((rp.get("content") or {}).get("message") or "").strip()
            if msg:
                rows.append({"rpid": int(rp.get("rpid") or 0), "mid": str(rp.get("mid") or ""),
                             "user": (rp.get("member") or {}).get("uname", ""), "text": msg[:120],
                             "ts": int(rp.get("ctime") or 0)})
        return rows

    async def reply_to(self, oid: int, type_: int, root: int, parent: int, text: str, at: dict | None = None) -> str:
        """在楼中楼里回复：root 是这层楼的楼主评论，parent 是要回的那条。"""
        data = {"oid": oid, "type": type_, "root": root, "parent": parent, "message": text, "plat": 1,
                "csrf": self.csrf}
        if at:
            data["at_name_to_mid"] = json.dumps(at, ensure_ascii=False)
        d = await self._post(API + "/x/v2/reply/add", data)
        return str(d.get("rpid_str") or d.get("rpid") or "")

    async def like_reply(self, oid: int, type_: int, rpid: int) -> None:
        await self._post(API + "/x/v2/reply/action",
                         {"oid": oid, "type": type_, "rpid": rpid, "action": 1, "csrf": self.csrf})

    async def emotes(self, limit: int = 80) -> list[str]:
        """评论区能用的表情（[doge] [笑哭] 这类文字形式），取账号表情面板里的。"""
        d = await self._get(API + "/x/emote/user/panel/web", {"business": "reply"})
        out: list[str] = []
        for pkg in d.get("packages") or []:
            for e in pkg.get("emote") or []:
                t = str(e.get("text") or "")
                if re.fullmatch(r"\[[^\[\]]{1,10}\]", t) and t not in out:
                    out.append(t)
        return out[:limit]

    async def find_user_mid(self, name: str) -> str:
        d = await self._get(API + "/x/web-interface/wbi/search/type",
                            {"search_type": "bili_user", "keyword": name, "page": 1}, wbi=True)
        for u in d.get("result") or []:
            if strip_html(u.get("uname", "")) == name:
                return str(u.get("mid") or "")
        return ""

    async def post_dynamic(self, text: str) -> str:
        mid = self.cookies.get("DedeUserID", "0")
        body = {"dyn_req": {
            "content": {"contents": [{"raw_text": text, "type": 1, "biz_id": ""}]},
            "scene": 1, "attach_card": None,
            "upload_id": f"{mid}_{int(time.time())}_{random.randint(1000, 9999)}",
            "meta": {"app_meta": {"from": "create.dynamic.web", "mobi_app": "web"}}}}
        try:
            d = await self._post(API + "/x/dynamic/feed/create/dyn", json_body=body,
                                 params={"platform": "web", "csrf": self.csrf})
            return str(d.get("dyn_id_str") or d.get("dyn_id") or "")
        except RiskControl:
            raise
        except BiliError:
            d = await self._post("https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/create", {
                "dynamic_id": 0, "type": 4, "rid": 0, "content": text, "csrf_token": self.csrf, "csrf": self.csrf})
            return str(d.get("dynamic_id_str") or d.get("dynamic_id") or "")
