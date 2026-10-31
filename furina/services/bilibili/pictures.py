"""全群共用的UP图片订阅：名单、近期动态游标和每群发送回执。

没有历史作品查询、角色识别或LLM调用。动态原文/链接不作为推送正文。
面板和插件共用本服务；对外只读B站，绝不自动关注UP或发布动态。
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import re
import time
import urllib.parse
import uuid
from pathlib import Path

import httpx

from ...core import db, env, http, paths
from . import store
from .client import BiliError, Client, NotLoggedIn, RiskControl, UA

NS = "bili_pics"
FEATURE = "bili_pics"
RETENTION = 7 * 86400
SEEN_LIMIT = 500
CHUNK_IMAGES = 9
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class DeliveryRejected(Exception):
    """确定没有发出的失败（下载失败、QQ明确拒绝），可以退避重试。"""


def settings() -> dict:
    return {"enabled": env.get_bool("BILI_PICS_ENABLED", True),
            "interval_minutes": max(5, min(1440, env.get_int("BILI_PICS_INTERVAL_MINUTES", 20))),
            "scan_batch": max(1, min(10, env.get_int("BILI_PICS_SCAN_BATCH", 3)))}


def parse_uid(raw) -> str:
    text = str(raw or "").strip()
    if re.fullmatch(r"[1-9]\d{0,19}", text):
        return text
    text = re.sub(r"^(?:uid|UID)\s*[:：=]?\s*", "", text)
    if re.fullmatch(r"[1-9]\d{0,19}", text):
        return text
    try:
        url = urllib.parse.urlsplit(text if "://" in text else "https://" + text)
        if url.scheme in ("http", "https") and not url.username and not url.password:
            pat = r"/([1-9]\d{0,19})/?" if url.hostname == "space.bilibili.com" else r"/space/([1-9]\d{0,19})/?"
            if url.hostname in ("space.bilibili.com", "m.bilibili.com", "bilibili.com", "www.bilibili.com"):
                match = re.fullmatch(pat, url.path)
                if match:
                    return match[1]
    except ValueError:
        pass
    raise ValueError("请给UP的B站UID或个人主页链接，昵称不拿来猜")


def image_url(raw) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 2048:
        return ""
    try:
        url = urllib.parse.urlsplit("https:" + raw if raw.startswith("//") else raw)
        host = (url.hostname or "").lower()
        if url.scheme not in ("http", "https") or url.username or url.password or url.port not in (None, 80, 443):
            return ""
        if not any(host == d or host.endswith("." + d) for d in ("hdslb.com", "biliimg.com")):
            return ""
        path = url.path
        if re.search(r"\.(?:png|jpe?g|gif|webp|avif)@", path, re.I):
            path = path.split("@", 1)[0]
        if not path or any(c.isspace() for c in path):
            return ""
        return urllib.parse.urlunsplit(("https", host, path, url.query, ""))
    except (TypeError, ValueError):
        return ""


def _obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def _name(value) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:80]


def _id(item) -> str:
    value = str(_obj(item).get("id_str") or "")
    return value if re.fullmatch(r"[1-9]\d{0,39}", value) else ""


def _published(item) -> float:
    try:
        return float(_obj(_obj(_obj(item).get("modules")).get("module_author")).get("pub_ts") or 0)
    except (ValueError, TypeError, OverflowError):
        return 0


def extract_post(item, uid: str) -> dict | None:
    item = _obj(item)
    if item.get("type") != "DYNAMIC_TYPE_DRAW" or not _id(item):
        return None  # 视频/文章封面、转发、emoji不是UP新发的图片动态
    mods = _obj(item.get("modules"))
    author = _obj(mods.get("module_author"))
    if str(author.get("mid") or "") != str(uid) or _published(item) <= 0:
        return None
    major = _obj(_obj(mods.get("module_dynamic")).get("major"))
    if major.get("type") == "MAJOR_TYPE_OPUS":
        rows, key = _obj(major.get("opus")).get("pics"), "url"
    elif major.get("type") == "MAJOR_TYPE_DRAW":
        rows, key = _obj(major.get("draw")).get("items"), "src"
    else:
        return None
    if not isinstance(rows, list) or len(rows) > 99:
        return None
    images = list(dict.fromkeys(u for r in rows if (u := image_url(_obj(r).get(key)))))
    if not images:
        return None
    return {"post_id": _id(item), "uid": str(uid), "name": _name(author.get("name")),
            "published": _published(item), "images": images}


def _base(value=None) -> dict:
    value = dict(value or {})
    for key in ("ups", "jobs", "groups"):
        value.setdefault(key, {})
    return value


def _read() -> dict:
    return _base(db.get(NS, "state"))


def _change(fn):
    return db.update(NS, "state", lambda value: fn(_base(value)))


def _prune(state: dict, now: float) -> None:
    for key, job in list(state["jobs"].items()):
        up = state["ups"].get(job["uid"])
        if not up or up["token"] != job["subscription_token"] or float(job["expires"]) <= now:
            del state["jobs"][key]


def snapshot(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    state = _read()
    counts = {"pending": 0, "uncertain": 0, "failed": 0, "sent": 0}
    for job in state["jobs"].values():
        if job["expires"] <= now:
            continue
        for target in job["targets"].values():
            status = target["state"]
            if status == "sending" and target.get("lease_until", 0) <= now:
                status = "unknown"
            key = {"pending": "pending", "sending": "pending", "unknown": "uncertain", "failed": "failed", "sent": "sent"}.get(status)
            if key:
                counts[key] += 1
    stopped, why = store.halted(now)
    rows = [{k: up.get(k) for k in ("uid", "name", "added_at", "last_check", "next_check", "last_error")} for up in state["ups"].values()]
    rows.sort(key=lambda row: (str(row["name"]).casefold(), row["uid"]))
    return {**settings(), **counts, "subscriptions": rows, "halted": stopped, "halt_reason": why,
            "shared": True, "history_search": False, "now": now}


def sync_groups(groups, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    active = {str(g) for g in groups if str(g).isdigit()}
    def update(state):
        _prune(state, now)
        for gid in set(state["groups"]) | active:
            old = state["groups"].get(gid) or {}
            enabled = gid in active
            since = old.get("since", now) if old.get("enabled") and enabled else now
            state["groups"][gid] = {**old, "enabled": enabled, "since": since}
        for job in state["jobs"].values():
            for gid, target in job["targets"].items():
                group = state["groups"].get(gid) or {}
                if target["state"] == "pending" and (not group.get("enabled") or job["published"] < group["since"]):
                    target["state"] = "skipped"
        return state
    _change(update)


def registered_groups() -> set[str]:
    return {gid for gid, row in _read()["groups"].items() if row.get("enabled")}


def _request_error(exc) -> str:
    if isinstance(exc, RiskControl):
        store.halt("图片订阅遇到B站风控，暂停请求", max(1, env.get_float("BILI_RISK_PAUSE_HOURS", 24)))
        return "B站暂时限制了请求，已暂停，稍后再试"
    if isinstance(exc, NotLoggedIn):
        store.halt("B站登录失效，请重新登录后恢复", 1)
        return "B站登录失效了，请在面板重新登录B站小号"
    return "暂时读不到B站动态，稍后会再试"


async def subscribe(raw, *, client=None, now: float | None = None) -> dict:
    uid = parse_uid(raw)
    old = _read()["ups"].get(uid)
    if old:
        return {"created": False, "uid": uid, "name": old["name"]}
    if store.halted()[0]:
        raise ValueError("B站请求正在暂停，恢复后再添加UP")
    if client is None:
        if not store.logged_in():
            raise ValueError("先在面板登录B站小号，再添加UP")
        async with Client() as cli:
            return await subscribe(uid, client=cli, now=now)
    now = time.time() if now is None else now
    try:
        card = await client.user_card(uid)
        if not card or str(card.get("mid")) != uid or not _name(card.get("name")):
            raise ValueError("没有核对到这个UP，请检查UID")
        feed = await client.user_dynamics(uid)
        if not isinstance(feed.get("items"), list):
            raise ValueError("B站动态返回不完整，暂时没有加入名单")
    except (BiliError, httpx.HTTPError) as exc:
        raise ValueError(_request_error(exc)) from None
    created = [False]
    def update(state):
        if uid not in state["ups"]:
            state["ups"][uid] = {"uid": uid, "name": _name(card["name"]), "token": uuid.uuid4().hex,
                "added_at": now, "last_check": now, "next_check": now + settings()["interval_minutes"] * 60,
                "seen": list(dict.fromkeys(_id(r) for r in feed["items"] if _id(r)))[-SEEN_LIMIT:],
                "offset": "", "last_error": "", "errors": 0, "lease_until": 0}
            created[0] = True
        return state
    state = _change(update)
    return {"created": created[0], "uid": uid, "name": state["ups"][uid]["name"]}


def unsubscribe(raw) -> bool:
    state = _read()
    try:
        uid = parse_uid(raw)
    except ValueError:
        matches = [u for u, row in state["ups"].items() if row["name"] == str(raw).strip()]
        if len(matches) != 1:
            raise ValueError("请用UID取消；同名或未找到的UP不猜") from None
        uid = matches[0]
    removed = [False]
    def update(current):
        removed[0] = current["ups"].pop(uid, None) is not None
        for key, job in list(current["jobs"].items()):
            if job["uid"] == uid:
                del current["jobs"][key]
        return current
    _change(update)
    return removed[0]


def make_due() -> int:
    count = [0]
    def update(state):
        for up in state["ups"].values():
            up["next_check"] = 0
            count[0] += 1
        return state
    _change(update)
    return count[0]


async def scan_due(*, client=None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    conf = settings()
    if not conf["enabled"] or store.halted(now)[0]:
        return {"checked": 0, "paused": True}
    due = sorted((u for u in _read()["ups"].values() if u["next_check"] <= now and u.get("lease_until", 0) <= now), key=lambda u: u["next_check"])[:conf["scan_batch"]]
    if not due:
        return {"checked": 0}
    if client is None:
        if not store.logged_in():
            return {"checked": 0, "paused": True, "reason": "B站未登录"}
        async with Client() as cli:
            return await scan_due(client=cli, now=now)
    checked = 0
    for candidate in due:
        uid, token, lease = candidate["uid"], candidate["token"], uuid.uuid4().hex
        claimed = [None]
        def claim(state):
            up = state["ups"].get(uid)
            if up and up["token"] == token and up["next_check"] <= now and up.get("lease_until", 0) <= now:
                up.update(lease_until=now + 180, scan_lease=lease)
                claimed[0] = copy.deepcopy(up)
            return state
        _change(claim)
        if claimed[0] is None:
            continue
        up = claimed[0]
        records, cursor, more = [], up.get("offset", ""), False
        try:
            for _ in range(3):
                feed = await client.user_dynamics(uid, offset=cursor)
                rows = feed["items"]
                records.extend(rows)
                next_cursor = str(feed.get("offset") or "")
                tail_time = _published(rows[-1]) if rows else 0
                more = bool(feed.get("has_more")) and bool(next_cursor) and next_cursor != cursor and tail_time > up["last_check"]
                cursor = next_cursor if more else ""
                if not more:
                    break
                await asyncio.sleep(1)
            def apply(state):
                current = state["ups"].get(uid)
                if not current or current["token"] != token or current.get("scan_lease") != lease:
                    return state
                _prune(state, now)
                seen = set(current["seen"])
                for item in sorted(records, key=_published):
                    post = extract_post(item, uid)
                    if not post or post["post_id"] in seen or post["published"] < max(int(current["added_at"]), now - RETENTION) or post["published"] > now + 3600:
                        continue
                    name = post["name"] or current["name"]
                    current["name"] = name
                    targets = {gid: {"state": "pending", "next": 0, "attempts": 0} for gid, group in state["groups"].items() if group["enabled"] and post["published"] >= group["since"]}
                    if targets:
                        for i in range(0, len(post["images"]), CHUNK_IMAGES):
                            key = f'{uid}:{post["post_id"]}:{i // CHUNK_IMAGES}'
                            state["jobs"].setdefault(key, {"id": key, "uid": uid, "post_id": post["post_id"], "subscription_token": token,
                                "caption": name, "images": post["images"][i:i + CHUNK_IMAGES], "published": post["published"],
                                "expires": now + RETENTION, "targets": copy.deepcopy(targets)})
                ids = [_id(row) for row in records if _id(row)]
                current["seen"] = list(dict.fromkeys(current["seen"] + ids))[-SEEN_LIMIT:]
                current.update(offset=cursor, next_check=now + (60 if more else conf["interval_minutes"] * 60), lease_until=0,
                               last_error="", errors=0)
                if not more:
                    current["last_check"] = now
                return state
            _change(apply)
            checked += 1
        except (BiliError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            why = _request_error(exc)
            def fail(state):
                current = state["ups"].get(uid)
                if current and current["token"] == token and current.get("scan_lease") == lease:
                    n = current.get("errors", 0) + 1
                    current.update(errors=n, last_error=why, next_check=now + min(3600, 60 * 2 ** min(n, 6)), lease_until=0)
                return state
            _change(fail)
            if isinstance(exc, (RiskControl, NotLoggedIn)):
                break
    return {"checked": checked}


def is_current(job: dict, gid) -> bool:
    state = _read()
    up = state["ups"].get(job["uid"])
    group = state["groups"].get(str(gid)) or {}
    return bool(up and up["token"] == job["subscription_token"] and job["id"] in state["jobs"] and group.get("enabled"))


def _claim_delivery(gid: str, now: float):
    selected = [None]
    def update(state):
        _prune(state, now)
        group = state["groups"].get(gid) or {}
        if not group.get("enabled") or group.get("next_delivery", 0) > now:
            return state
        for job in sorted(state["jobs"].values(), key=lambda row: (row["published"], row["id"])):
            target = job["targets"].get(gid)
            if not target:
                continue
            if target["state"] == "sending" and target.get("lease_until", 0) <= now:
                target.update(state="unknown", error="上次发送中断，结果不确定，未自动重发")
            if target["state"] != "pending" or target.get("next", 0) > now:
                continue
            if job["published"] < group["since"]:
                target["state"] = "skipped"
                continue
            target.update(state="sending", claim=uuid.uuid4().hex, lease_until=now + 180, attempts=target.get("attempts", 0) + 1)
            group["next_delivery"] = now + 60
            selected[0] = (copy.deepcopy(job), target["claim"])
            break
        return state
    _change(update)
    return selected[0]


def _finish(job_id, gid, claim, outcome, now, message_id="", error=""):
    def update(state):
        job = state["jobs"].get(job_id)
        target = job["targets"].get(gid) if job else None
        if target and target.get("claim") == claim and target["state"] == "sending":
            target.update(state=outcome, error=error, message_id=str(message_id), lease_until=0)
            if outcome == "pending":
                target["next"] = now + min(3600, 60 * 2 ** min(target["attempts"], 6))
                if target["attempts"] >= 5:
                    target["state"] = "failed"
        return state
    _change(update)


async def dispatch(sender, groups, *, now: float | None = None, online: bool = True) -> dict:
    now = time.time() if now is None else now
    sent = uncertain = 0
    conf = settings()
    if not online or not conf["enabled"] or store.halted(now)[0]:
        return {"sent": 0, "uncertain": 0}
    for gid in sorted(str(g) for g in groups):
        claimed = _claim_delivery(gid, now)
        if not claimed:
            continue
        job, claim = claimed
        if not is_current(job, gid):
            _finish(job["id"], gid, claim, "skipped", now)
            continue
        try:
            result = await sender(job, gid)
            mid = result.get("message_id") if isinstance(result, dict) else None
            if mid is None:
                raise TimeoutError("没有收到明确发送回执")
            _finish(job["id"], gid, claim, "sent", now, message_id=mid)
            sent += 1
        except DeliveryRejected:
            _finish(job["id"], gid, claim, "pending", now, error="图片下载或QQ明确拒绝，稍后重试")
        except asyncio.CancelledError:
            _finish(job["id"], gid, claim, "unknown", now, error="发送中断，结果不确定，未重发")
            raise
        except Exception:
            _finish(job["id"], gid, claim, "unknown", now, error="发送结果不确定，未自动重发")
            uncertain += 1
    return {"sent": sent, "uncertain": uncertain}


def retry_unconfirmed(*, now: float | None = None) -> int:
    now = time.time() if now is None else now
    count = [0]
    def update(state):
        _prune(state, now)
        for job in state["jobs"].values():
            for target in job["targets"].values():
                expired = target["state"] == "sending" and target.get("lease_until", 0) <= now
                if target["state"] in ("unknown", "failed") or expired:
                    target.update(state="pending", next=0, attempts=0)
                    count[0] += 1
        return state
    _change(update)
    return count[0]


def _validate(data: bytes) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    if len(data) > 8 * 1024 * 1024:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((4096, 4096))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=90)
            return out.getvalue()
    return data


async def load_image(url: str, *, client=None) -> bytes:
    url = image_url(url)
    if not url:
        raise DeliveryRejected("不是有效的B站图片地址")
    folder = paths.DATA_DIR / "bili_pics" / "cache"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(url.encode()).hexdigest() + ".img")
    if path.exists():
        data = await asyncio.to_thread(path.read_bytes)
        try:
            return await asyncio.to_thread(_validate, data)
        except Exception:
            path.unlink(missing_ok=True)
    client = client or http.client("bili-pics-images", trust_env=False, follow_redirects=False,
        headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}, timeout=25)
    try:
        async with client.stream("GET", url, headers={"Cookie": ""}, follow_redirects=False) as response:
            if response.status_code != 200:
                raise DeliveryRejected("图片暂时下载不了")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise DeliveryRejected("图片过大")
                chunks.append(chunk)
        data = await asyncio.to_thread(_validate, b"".join(chunks))
        tmp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            await asyncio.to_thread(tmp.write_bytes, data)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return data
    except DeliveryRejected:
        raise
    except Exception:
        raise DeliveryRejected("图片暂时下载或校验失败") from None


def prune_cache(now: float | None = None) -> None:
    now = time.time() if now is None else now
    folder = paths.DATA_DIR / "bili_pics" / "cache"
    files = sorted((p for p in folder.glob("*.img") if re.fullmatch(r"[0-9a-f]{64}\.img", p.name)), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for path in files:
        if now - path.stat().st_mtime > 86400 or total > 256 * 1024 * 1024:
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
            except OSError:
                pass
