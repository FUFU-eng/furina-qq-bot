"""面板「B站生活」页的数据与操作：实时状态、看过的视频、发过的内容、学到的梗、扫码登录、给机器人下指令。

面板和机器人是两个进程：状态都在同一个 SQLite（bili / slang 命名空间）里，
按钮通过 bili/request 交给机器人（每 10 秒取一次）；扫码登录由面板自己完成，登录信息直接写库，机器人下一轮就能用。
"""
from __future__ import annotations

import asyncio
import base64
import io
import threading
import time

from furina.core import db
from furina.services import slang
from furina.services.bilibili import store
from furina.services.bilibili.client import Client

from . import envfile

_login_lock = threading.Lock()
_login: dict = {}


def _int_env(env: dict, key: str, default: int) -> int:
    try:
        return int(float(env.get(key) or default))
    except ValueError:
        return default


def _links(p: dict) -> dict:
    p = dict(p)
    if p.get("kind") == "dynamic" and p.get("dyn_id"):
        p["url"] = f"https://t.bilibili.com/{p['dyn_id']}"
    elif p.get("kind") == "reply" and p.get("uri") and not p.get("bvid"):
        p["url"] = p["uri"]
    elif p.get("bvid"):
        p["url"] = f"https://www.bilibili.com/video/{p['bvid']}" + (f"?t={p['second']}" if p.get("second") else "")
    return p


def _group_memes(limit_groups: int = 8) -> list[dict]:
    out = []
    for key, _v in db.items(slang.NS).items():
        if key.startswith("group:"):
            gid = key.split(":", 1)[1]
            memes = slang.group_memes(gid, limit=8)
            if memes:
                out.append({"group": gid, "memes": memes})
    return out[:limit_groups]


def overview() -> dict:
    env = envfile.values()
    caps = {k: _int_env(env, key, d) for k, key, d in (
        ("rounds", "BILI_ROUNDS_DAILY", 6), ("likes", "BILI_LIKE_DAILY_MAX", 0), ("danmaku", "BILI_DANMAKU_DAILY_MAX", 0),
        ("comments", "BILI_COMMENT_DAILY_MAX", 0), ("replies", "BILI_REPLY_DAILY_MAX", 0),
        ("dynamics", "BILI_DYNAMIC_DAILY_MAX", 0))}
    tr = slang.trending()
    req = store.pending_request()
    return {
        "status": store.status(),
        "caps": caps,
        "enabled": str(env.get("BILI_ENABLED", "true")).lower() not in ("0", "false", "no", "off"),
        "dry_run": str(env.get("BILI_DRY_RUN", "false")).lower() in ("1", "true", "yes", "on"),
        "hours": env.get("BILI_HOURS") or "10-24",
        "mention": env.get("BILI_MENTION_NAME") or "",
        "reply_on": str(env.get("BILI_REPLY_ENABLED", "true")).lower() not in ("0", "false", "no", "off"),
        "live": store.get_live(),
        "activity": store.activity(50)[::-1],
        "watched": store.all_watched(30),
        "posts": [_links(p) for p in store.all_posts(40)],
        "learned": slang.learned(60)[::-1],
        "builtin_count": len(slang.BUILTIN),
        "trending": {"words": tr.get("words") or [], "ts": tr.get("ts") or 0},
        "group_memes": _group_memes(),
        "pending": req,
        "login": {k: _login.get(k) for k in ("state", "ts")} if _login else None,
        "now": time.time(),
    }


def request(action: str, keyword: str = "") -> dict:
    if action in ("pause", "resume"):  # 暂停/恢复面板直接改库就行，不用等机器人
        if action == "pause":
            store.halt("主人在面板上暂停", 24 * 365)
            store.log_activity("主人在面板上让我先别刷了", "end")
        else:
            store.resume()
            store.log_activity("恢复刷 B 站", "start")
        return {"done": True}
    if action == "logout":
        store.clear_auth()
        store.log_activity("退出了 B 站登录", "end")
        return {"done": True}
    extra = {"keyword": keyword.strip()[:30]} if keyword and keyword.strip() else {}
    return {"request": store.put_request(action, **extra)}


def cancel_request() -> bool:
    if store.pending_request():
        db.delete(store.NS, "request")
        return True
    return False


def delete_slang(term: str) -> None:
    slang.remove_learned(term)


# ---------------- 扫码登录（面板自己做） ----------------
def _qr_data_uri(url: str) -> str:
    import qrcode

    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def login_start() -> dict:
    async def _gen():
        async with Client(cookies={}) as cli:
            return await cli.qr_generate()
    with _login_lock:
        try:
            url, key = asyncio.run(_gen())
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"拿不到 B 站登录二维码：{e}") from e
        _login.clear()
        _login.update({"key": key, "url": url, "ts": time.time(), "state": "waiting"})
    return {"qr": _qr_data_uri(url), "state": "waiting", "expires_in": 180}


_STATES = {86101: "waiting", 86090: "scanned", 86038: "expired", 0: "ok"}


def login_poll() -> dict:
    if not _login.get("key"):
        return {"state": "none"}
    if _login.get("state") in ("ok", "expired"):
        return {"state": _login["state"], "uname": _login.get("uname", "")}
    if time.time() - float(_login.get("ts") or 0) > 185:
        _login["state"] = "expired"
        return {"state": "expired"}

    async def _poll():
        async with Client(cookies={}) as cli:
            code, cookies = await cli.qr_poll(_login["key"])
            if code == 0 and cookies.get("SESSDATA"):
                cli.cookies = {k: v for k, v in cookies.items() if not k.startswith("_")}
                await cli.ensure_buvid()
                nav = await cli.nav()
                store.save_auth(cli.cookies, uname=nav.get("uname", ""), mid=str(nav.get("mid", "")))
                if cookies.get("_refresh_token"):
                    store.set_meta("refresh_token", cookies["_refresh_token"])
                return code, nav.get("uname", "")
            return code, ""
    with _login_lock:
        try:
            code, uname = asyncio.run(_poll())
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"查询扫码状态失败：{e}") from e
        state = _STATES.get(code, "waiting")
        _login["state"] = state
        if state == "ok":
            _login["uname"] = uname
            store.log_activity(f"在面板上扫码登录了：{uname or '小号'}", "start")
    return {"state": state, "uname": uname}
