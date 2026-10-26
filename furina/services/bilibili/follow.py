"""群友让芙宁娜在 B 站关注 TA、她答应了，就真的去关注（二代仿真的 bili_follow 工具调用这里）。

- 认人：优先 UID / 个人主页链接（space.bilibili.com/数字），其次准确的 B 站昵称（B 站昵称不重名，只认完全一致的）；
- 上限：BILI_FOLLOW_DAILY_MAX（默认 0 = 不限，看她心情），同一个人只关注一次；开关 BILI_FOLLOW_ENABLED；
- B 站小号没登录、风控暂停中、演练模式（BILI_DRY_RUN）都不真关注；遇到风控和刷视频一样暂停。
不依赖 nonebot，聊天插件直接调用。
"""
from __future__ import annotations

import re
import time

from ...core import db, env
from . import store
from .client import BiliError, Client, NotLoggedIn, RiskControl

_SPACE = re.compile(r"space\.bilibili\.com/(\d{1,16})")
_UID = re.compile(r"(?i)(?:uid|mid)\s*[:：=]?\s*(\d{1,16})")
_NAME = re.compile(r"(?:b站|B站|哔哩哔哩|bilibili)\s*(?:id|ID|名字?|昵称|号|账号|用户名)?\s*(?:是|叫|为|[:：])?\s*[「“\"']?([^\s「」“”\"'，,。!！?？]{1,16})")


def enabled() -> bool:
    return env.get_bool("BILI_FOLLOW_ENABLED", True)


def daily_max() -> int:
    return env.get_int("BILI_FOLLOW_DAILY_MAX", 0)


def parse_target(*texts: str) -> tuple[str, str]:
    """从工具参数和群友原话里认出要关注谁：("mid", "123") / ("name", "昵称") / ("", "")。"""
    for t in texts:
        t = str(t or "").strip()
        if not t:
            continue
        m = _SPACE.search(t) or _UID.search(t)
        if m:
            return "mid", m.group(1)
        if re.fullmatch(r"\d{3,16}", t):
            return "mid", t
    for t in texts:
        t = str(t or "").strip()
        m = _NAME.search(t)
        if m and m.group(1) not in ("关注", "账号", "名字"):
            return "name", m.group(1).lstrip("@")
    first = str(texts[0] or "").strip().lstrip("@") if texts else ""
    if first and len(first) <= 16 and not re.search(r"\s", first):
        return "name", first
    return "", ""


def followed() -> list[dict]:
    return list(db.get(store.NS, "followed") or [])


async def follow_member(who: str, said: str = "", *, qq: str = "", gid=None) -> dict:
    """真去关注。返回 {"ok": bool, "name"?, "reason"?, "already"?}，reason 用她能直接理解的话写。"""
    if not enabled():
        return {"ok": False, "reason": "主人没开 B 站关注功能，别答应真关注"}
    if not store.logged_in():
        return {"ok": False, "reason": "你的 B 站小号还没登录"}
    stop, why = store.halted()
    if stop:
        return {"ok": False, "reason": f"B 站这会儿在暂停（{why}），晚点再关注"}
    cap = daily_max()
    if cap > 0 and store.used("follows") >= cap:
        return {"ok": False, "reason": f"今天已经关注了 {cap} 个人，再关注容易被 B 站风控，明天再说"}
    kind, value = parse_target(who, said)
    if not kind:
        return {"ok": False, "reason": "不知道 TA 的 B 站是哪个，先让 TA 发 UID 或个人主页链接"}
    async with Client() as cli:
        try:
            await cli.ensure_buvid()
            if kind == "name":
                mid = await cli.find_user_mid(value)
                if not mid:
                    return {"ok": False, "reason": f"B 站上没搜到叫「{value}」的人，让 TA 发 UID 或主页链接"}
            else:
                mid = value
            card = await cli.user_card(mid)
            if not card:
                return {"ok": False, "reason": "这个 UID 查不到人，让 TA 再确认一下"}
            if str(card["mid"]) == str(store.get_auth().get("mid") or ""):
                return {"ok": False, "reason": "这是你自己的号"}
            if card["following"] or any(str(f.get("mid")) == str(card["mid"]) for f in followed()):
                return {"ok": True, "already": True, "name": card["name"]}
            if env.get_bool("BILI_DRY_RUN", False):
                store.log_activity(f"（演练）想关注群友的 B 站「{card['name']}」", "follow")
                return {"ok": True, "name": card["name"], "note": "演练模式，没有真关注"}
            await cli.follow(card["mid"])
        except RiskControl as e:
            hours = env.get_float("BILI_RISK_PAUSE_HOURS", 24.0)
            store.halt(f"关注时触发风控 {e.code}", hours)
            store.log_activity(f"关注时触发风控（{e.code}），先停 {hours:.0f} 小时", "error")
            return {"ok": False, "reason": "B 站说操作太频繁，没关注上，先缓缓"}
        except NotLoggedIn:
            store.update_auth(invalid=True)
            return {"ok": False, "reason": "B 站小号登录失效了"}
        except BiliError as e:
            return {"ok": False, "reason": f"B 站没让关注（{e.message or e.code}）"}
    store.bump("follows")
    db.update(store.NS, "followed", lambda lst: (list(lst or []) + [
        {"mid": str(card["mid"]), "name": card["name"], "qq": str(qq), "gid": str(gid or ""), "ts": time.time()}])[-300:])
    store.log_activity(f"答应了群友，在 B 站关注了「{card['name']}」", "follow")
    return {"ok": True, "name": card["name"]}
