"""芙宁娜趣味扩展能力：
1. 查岗主人桌面（peek_owner_desktop）：静默读取当前前台窗口与运行中的软件/游戏，并截图发到群里吐槽（自动避开密码/密钥等敏感窗口）。
2. Token 余额不足自动截图催充钱（balance_watch）：检测到 402 / Insufficient Balance / 余额不足时，自动后台截图 DeepSeek 充值页发群并 @主人 讨要小蛋糕。
3. 抖S「记小本本」与群友黑历史外号（black_book）：给群友起专属抖S绰号、记下斗嘴败绩或黑历史，并在聊天时冷不丁翻出来嘲笑；支持控制面板可视化管理。
4. 高傲的语音反击与撒娇语音加成（voice_boost）：当触发抖S嘲讽、傲娇嘴硬或讨要小蛋糕时，提高直接发语音条的概率。
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from nonebot import logger

from ...core import db, env

_NS_BOOK = "black_book"
_NS_META = "fun_features"

_PRIVACY_RE = re.compile(
    r"密码|password|1password|bitwarden|keepass|银行|网银|支付宝|Alipay|微信支付|"
    r"\.env\b|secret|token|私钥|助记词|wallet|metamask|登录凭据",
    re.I,
)
_BALANCE_ERR_RE = re.compile(
    r"402\b|insufficient[_\s-]*balance|insufficient[_\s-]*quota|余额不足|欠费|arrearage|account.*balance",
    re.I,
)
VOICE_BOOST_RE = re.compile(
    r"哼|啧|本芙|本水神|本大明星|本导演|皮痒|算盘珠子|做白日梦|做梦|想得美|"
    r"小蛋糕|马卡龙|充钱|粮草|罢工|原谅你|勉强|笨蛋|讨厌|跪下|叫声好听的|小本本"
)


# ---------------- 开关与配置 ----------------
def desktop_peek_enabled() -> bool:
    return env.get_bool("AI_CHAT_DESKTOP_PEEK_ENABLED", True)


def auto_balance_alert_enabled() -> bool:
    return env.get_bool("AI_CHAT_AUTO_BALANCE_ALERT", True)


def black_book_enabled() -> bool:
    return env.get_bool("AI_CHAT_BLACK_BOOK_ENABLED", True)


def voice_boost_enabled() -> bool:
    return env.get_bool("AI_CHAT_VOICE_BOOST_ENABLED", True)


def voice_boost_prob() -> float:
    return max(0.0, min(1.0, env.get_float("AI_CHAT_VOICE_BOOST_PROB", 0.45)))


# ---------------- 3. 抖S小本本（黑历史与绰号） ----------------
def list_black_book() -> list[dict]:
    doc = db.get(_NS_BOOK, "entries")
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        return [dict(x) for x in doc["items"] if isinstance(x, dict)]
    if isinstance(doc, list):
        return [dict(x) for x in doc if isinstance(x, dict)]
    return []


def save_black_book(items: list[dict]) -> list[dict]:
    clean: list[dict] = []
    seen = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        eid = str(it.get("id") or "").strip() or f"bb_{uuid.uuid4().hex[:8]}"
        if eid in seen:
            eid = f"bb_{uuid.uuid4().hex[:8]}"
        seen.add(eid)
        who = str(it.get("who") or it.get("name") or "").strip()[:40]
        uid = str(it.get("uid") or "").strip()[:24]
        nickname = str(it.get("nickname") or "").strip()[:40]
        event = str(it.get("event") or it.get("reason") or "").strip()[:200]
        gid = str(it.get("gid") or "").strip()[:24]
        if not (who or uid) or not (nickname or event):
            continue
        clean.append({
            "id": eid,
            "who": who or uid,
            "uid": uid,
            "gid": gid,
            "nickname": nickname,
            "event": event,
            "ts": float(it.get("ts") or time.time()),
            "date": str(it.get("date") or time.strftime("%Y-%m-%d %H:%M", time.localtime(float(it.get("ts") or time.time())))),
        })
    db.put(_NS_BOOK, "entries", {"items": clean[-200:]})
    return clean[-200:]


def add_black_book_entry(who: str, nickname: str = "", event: str = "", gid: str | int = "", uid: str = "") -> dict:
    who = (who or "").strip()[:40]
    nickname = (nickname or "").strip()[:40]
    event = (event or "").strip()[:200]
    uid = (uid or "").strip()[:24]
    gid_s = str(gid or "").strip()
    if not (who or uid) or not (nickname or event):
        return {"ok": False, "reason": "要写清楚记谁（who）以及起的外号（nickname）或黑历史（event）"}
    items = list_black_book()
    now = time.time()
    matched = None
    for it in reversed(items):
        if (uid and it.get("uid") == uid) or (who and it.get("who") == who):
            matched = it
            break
    if matched and len(items) >= 120:
        if nickname:
            matched["nickname"] = nickname
        if event:
            matched["event"] = event
        matched["ts"] = now
        matched["date"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        entry = matched
    else:
        entry = {
            "id": f"bb_{uuid.uuid4().hex[:8]}",
            "who": who or uid,
            "uid": uid,
            "gid": gid_s,
            "nickname": nickname,
            "event": event,
            "ts": now,
            "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        }
        items.append(entry)
    save_black_book(items)
    logger.info(f"[小本本] 芙芙记了一笔：{entry['who']} -> 外号「{nickname}」｜{event}")
    return {"ok": True, "entry": entry}


def delete_black_book_entry(entry_id: str) -> bool:
    items = list_black_book()
    left = [x for x in items if str(x.get("id")) != str(entry_id)]
    if len(left) == len(items):
        return False
    save_black_book(left)
    return True


def black_book_prompt_note(gid: str | int = "", speaker_uid: str = "", speaker_name: str = "", recent_text: str = "") -> str:
    if not black_book_enabled():
        return ""
    items = list_black_book()
    if not items:
        return ""
    gid_s = str(gid or "")
    s_uid = str(speaker_uid or "")
    s_name = (speaker_name or "").strip()
    r_text = (recent_text or "").strip()

    picked: list[dict] = []
    other: list[dict] = []
    for it in reversed(items):
        if len(picked) >= 6:
            break
        who = str(it.get("who") or "")
        uid = str(it.get("uid") or "")
        nick = str(it.get("nickname") or "")
        if (s_uid and uid == s_uid) or (s_name and who and (who in s_name or s_name in who)) or (who and who in r_text) or (nick and nick in r_text):
            picked.append(it)
        elif not gid_s or not it.get("gid") or str(it.get("gid")) == gid_s:
            other.append(it)

    for it in other[: max(0, 4 - len(picked))]:
        picked.append(it)

    if not picked:
        return ""
    lines = []
    for it in picked[:6]:
        who = it.get("who") or it.get("uid")
        nick = f"（你给他起的外号：「{it['nickname']}」）" if it.get("nickname") else ""
        ev = f"——黑历史：{it['event']}" if it.get("event") else ""
        lines.append(f"- {who}{nick}{ev}")
    return (
        "【芙芙的抖S小本本（你记下的群友外号与黑历史；平时斗嘴、调侃或看到这人说话时，可以冷不丁叫他这个外号或翻出黑历史嘲笑他；"
        "遇到有人顶嘴或开黄腔被你反杀，也可以调 note_black_book 给他记上一笔）】\n"
        + "\n".join(lines)
    )


# ---------------- 1. 查岗主人桌面（peek_owner_desktop） ----------------
def _inspect_desktop_sync(take_screenshot: bool = True) -> dict:
    ps_script = r"""
[Console]::OutputEncoding=[Text.Encoding]::UTF8
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class FgWin {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
}
"@
$h = [FgWin]::GetForegroundWindow()
$sb = New-Object System.Text.StringBuilder 512
[void][FgWin]::GetWindowText($h, $sb, 512)
$fg = $sb.ToString()
$wins = Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object -First 15 ProcessName, MainWindowTitle | ForEach-Object { "$($_.ProcessName): $($_.MainWindowTitle)" }
@{ fg = $fg; wins = @($wins) } | ConvertTo-Json -Compress
"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    fg = ""
    wins: list[str] = []
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, creationflags=flags
        )
        import json as _json
        raw = (p.stdout or "").strip()
        if raw:
            data = _json.loads(raw.splitlines()[-1])
            fg = str(data.get("fg") or "").strip()
            w = data.get("wins") or []
            wins = [str(x) for x in (w if isinstance(w, list) else [w]) if str(x).strip()]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[查岗桌面] 获取窗口列表失败: {e}")

    safe_wins = [w for w in wins if not _PRIVACY_RE.search(w)]
    privacy_hit = bool(_PRIVACY_RE.search(fg))
    if privacy_hit:
        fg_display = "（主人当前窗口涉及隐私/密码，已自动打码跳过截图）"
    else:
        fg_display = fg or "桌面"

    shot_path = ""
    if take_screenshot and not privacy_hit:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            out = os.path.join(tempfile.gettempdir(), f"furina_peek_{uuid.uuid4().hex[:10]}.png")
            img.save(out, "PNG")
            if os.path.isfile(out) and os.path.getsize(out) > 1024:
                shot_path = out
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[查岗桌面] 截屏失败: {e}")

    return {
        "ok": True,
        "foreground": fg_display,
        "windows": safe_wins[:12],
        "screenshot_path": shot_path,
        "privacy_skipped": privacy_hit,
    }


async def peek_owner_desktop() -> dict:
    if not desktop_peek_enabled():
        return {"ok": False, "reason": "主人在控制面板里关掉了「查岗主人桌面」开关"}
    return await asyncio.to_thread(_inspect_desktop_sync, True)


# ---------------- 2. Token 余额不足自动截图催充钱 ----------------
def is_balance_error(err_text: str) -> bool:
    return bool(_BALANCE_ERR_RE.search(err_text or ""))


async def maybe_alert_low_balance(err_text: str = "", *, force: bool = False) -> dict:
    """检测到余额不足（或手动测试 force=True）时，静默截图 DeepSeek 充值页并到群里 @主人 讨要小蛋糕。"""
    if not force:
        if not auto_balance_alert_enabled():
            return {"ok": False, "reason": "未开启余额不足自动提醒"}
        if not is_balance_error(err_text):
            return {"ok": False, "reason": "非余额不足错误"}
        last_ts = float(db.get(_NS_META, "last_balance_alert") or 0.0)
        if time.time() - last_ts < 6 * 3600:
            return {"ok": False, "reason": "6小时内已提醒过主人充钱"}

    db.put(_NS_META, "last_balance_alert", time.time())
    from . import config, kernel
    from nonebot import get_bots
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    out_path = os.path.join(tempfile.gettempdir(), f"furina_topup_{uuid.uuid4().hex[:8]}.png")
    ok, info = await asyncio.to_thread(kernel._capture_url_sync, "https://platform.deepseek.com/top_up", out_path)

    bots = list(get_bots().values())
    if not bots:
        return {"ok": False, "reason": "当前没有在线的机器人实例", "screenshot": out_path if ok else ""}
    bot = bots[0]
    owner_qq = str(config.GROUP_OWNER_QQ or env.get("AI_CHAT_GROUP_OWNER_QQ", "")).split(",")[0].strip()

    target_gid = None
    try:
        if kernel._groups:
            target_gid = max(kernel._groups.values(), key=lambda s: s.last_active or 0.0).gid
        if not target_gid:
            glist = await bot.get_group_list()
            if glist:
                target_gid = int(glist[0]["group_id"])
    except Exception:  # noqa: BLE001
        pass

    lines = [
        "喂！！本芙的粮草（Token 余额）都见底啦！你看看这充值页面空得像什么样！赶紧给本大明星充钱买小蛋糕，不然本芙今天要罢工了哼！",
        "抓紧时间！本水神大人的 Token 池子快干涸了，截图证据直接甩你脸上，还不快去给本芙把买小蛋糕的经费补上！",
    ]
    text = random.choice(lines)

    try:
        if target_gid:
            msg = Message()
            if owner_qq and owner_qq.isdigit():
                msg += MessageSegment.at(int(owner_qq))
                msg += MessageSegment.text(" ")
            msg += MessageSegment.text(text)
            if ok and os.path.isfile(out_path):
                msg += MessageSegment.image(Path(out_path).resolve().as_uri())
            await bot.send_group_msg(group_id=int(target_gid), message=msg)
            logger.info(f"[余额提醒] 已在群 {target_gid} 截图充值页并 @主人 催充钱")
            return {"ok": True, "group_id": target_gid, "screenshot": out_path if ok else ""}
        elif owner_qq and owner_qq.isdigit():
            msg = Message(MessageSegment.text(text))
            if ok and os.path.isfile(out_path):
                msg += MessageSegment.image(Path(out_path).resolve().as_uri())
            await bot.send_private_msg(user_id=int(owner_qq), message=msg)
            logger.info(f"[余额提醒] 已私聊主人 {owner_qq} 截图充值页催充钱")
            return {"ok": True, "user_id": owner_qq, "screenshot": out_path if ok else ""}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[余额提醒] 发送失败: {e}")
        return {"ok": False, "reason": str(e)[:160]}
    return {"ok": False, "reason": "未找到可发送的群或主人 QQ"}
