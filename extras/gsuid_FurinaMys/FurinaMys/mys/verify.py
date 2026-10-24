"""米游社人机验证（免费，人工）。

米游社偶尔会给某个账号挂上 10035 / 1034 验证码。群友手机上不会弹（数据不是从他手机请求的），
真正需要过验证的是「芙芙这台电脑 + 这个账号」。这里不用任何打码平台：
  1. 查询被拦时，记下这个账号，并私聊提醒主人；
  2. 主人在本机浏览器打开 http://127.0.0.1:8765/furina/verify ，亲手拖一次极验滑块；
  3. 芙芙把结果交回米游社（官方 verifyVerification 接口），复查通过后在群里告诉群友可以再查了。
页面只监听本机 127.0.0.1，外网访问不到；页面上的账号都打码，不出现 Cookie。
"""

from __future__ import annotations

import copy
import time
import asyncio
import secrets
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from gsuid_core.logger import logger
from gsuid_core.utils.api.mys_api import mys_api
from gsuid_core.utils.api.mys.api import VERIFY, VERIFICATION
from gsuid_core.utils.api.mys.tools import get_ds_token
from gsuid_core.utils.api.mys.base_request import _CHALLENGE_META
from gsuid_core.utils.database.models import GsUser

PAGE_PATH = "/furina/verify"
NOTIFY_MASTER = False
LOCAL_URL = "http://127.0.0.1:8765" + PAGE_PATH
NOTIFY_GAP = 6 * 3600  # 同一个账号 6 小时内只提醒主人一次

# key（随机串，页面用）-> 记录
PENDING: Dict[str, Dict] = {}


def _mask(text: str) -> str:
    text = str(text or "")
    return text[:2] + "***" + text[-2:] if len(text) > 4 else "***"


def _key_of(mys_id: str) -> Optional[str]:
    return next((k for k, v in PENDING.items() if v["mys_id"] == mys_id), None)


async def _user_by_mys(mys_id: str) -> Optional[GsUser]:
    users = await GsUser.get_all_user(without_error=False)
    rows = [u for u in users if str(u.mys_id or "") == str(mys_id)]
    return rows[0] if rows else None


def _header(u: GsUser) -> Dict:
    h = copy.deepcopy(mys_api._HEADER)
    h["Cookie"] = u.cookie or ""
    if u.device_id:
        h["x-rpc-device_id"] = u.device_id
    if u.fp:
        h["x-rpc-device_fp"] = u.fp
    if u.device_info and len(u.device_info.split("/")) >= 4:
        df = u.device_info.split("/")
        h["User-Agent"] = (
            f"Mozilla/5.0 (Linux; Android 13; {df[1]} {df[3]} ; wv)AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Version/4.0 Chrome/104.0.5112.97Mobile Safari/537.36 miHoYoBBS/2{mys_api.mysVersion}"
        )
    return h


GAME_NAMES = {"gs": "原神", "sr": "星铁", "zzz": "绝区零", "bbb": "崩坏3"}


def _meta(game: str) -> Dict[str, str]:
    # 星铁用的是米游社定制的另一套极验（gt 不同），必须带星铁的挑战头去申请 / 提交，
    # 否则拿到的是通用验证码，过了也解不开星铁的拦截。绝区零 / 崩坏3 和原神共用通用那套。
    # 实测：绝区零带它自己的挑战头（challenge_game=8）提交，米游社一律回 -1；
    # 它和原神用的是同一套通用验证码（gt 相同），按原神的挑战头提交就能被接受。
    return dict(_CHALLENGE_META["sr"] if game == "sr" else _CHALLENGE_META["gs"])


async def probe_game(u: GsUser, game: str) -> Optional[bool]:
    """复查某个游戏：True=正常，False=还被拦，None=没法判断。"""
    async def judge(coro) -> Optional[bool]:
        try:
            r = await coro
        except Exception as exc:
            logger.warning(f"[FurinaMys] 验证复查异常: {exc}")
            return None
        if isinstance(r, int):
            return False if r in (10035, 1034, 5003, 10041, -999, 114514) else None
        return True

    if game == "sr" and u.sr_uid:
        from gsuid_core.plugins.StarRailUID.StarRailUID.utils.mys_api import mys_api as sr_api
        return await judge(sr_api.get_abyss_info(u.sr_uid))
    if game == "zzz" and u.zzz_uid:
        from gsuid_core.plugins.ZZZeroUID.ZZZeroUID.utils.zzzero_api import zzz_api
        return await judge(zzz_api.get_zzz_index_info(u.zzz_uid))
    if game == "gs" and u.uid:
        # 「ys每日」走的是小组件接口（几乎不会弹验证），这里用群友真正会触发验证的深渊接口检测
        return await judge(mys_api.get_spiral_abyss_info(u.uid))
    return None


async def probe(u: GsUser) -> Optional[bool]:
    """复查：True=正常，False=还被拦，None=没法判断（没有可查的游戏 / 其它错误）。"""
    async def judge(coro) -> Optional[bool]:
        try:
            r = await coro
        except Exception as exc:
            logger.warning(f"[FurinaMys] 验证复查异常: {exc}")
            return None
        if isinstance(r, int):
            return False if r in (10035, 1034, 5003, 10041, -999, 114514) else None
        return True

    if u.sr_uid:
        from gsuid_core.plugins.StarRailUID.StarRailUID.utils.mys_api import mys_api as sr_api
        return await judge(sr_api.get_abyss_info(u.sr_uid))
    if u.zzz_uid:
        from gsuid_core.plugins.ZZZeroUID.ZZZeroUID.utils.zzzero_api import zzz_api
        return await judge(zzz_api.get_zzz_index_info(u.zzz_uid))
    if u.uid:
        return await judge(mys_api.get_spiral_abyss_info(u.uid))
    return None


async def report(bot, ev, game: str, uid: str) -> None:
    """设备重新登记后仍被拦：记下来，并私聊提醒主人去本机页面过一次验证。"""
    try:
        from .bind import game_col

        mys_id = str(await GsUser.get_user_attr_by_uid(uid, "mys_id", game_col(game)) or "")
        if not mys_id:
            return
        key = _key_of(mys_id)
        now = time.time()
        if key is None:
            key = secrets.token_urlsafe(8)
            PENDING[key] = {"mys_id": mys_id, "games": set(), "since": now, "notified": 0.0, "targets": {}}
        rec = PENDING[key]
        rec["games"].add(game)
        group_id = getattr(ev, "group_id", None)
        rec["targets"][str(ev.user_id)] = {
            "ws": bot.bot.bot_id, "bot_id": ev.bot_id, "bot_self_id": ev.bot_self_id,
            "group_id": str(group_id) if group_id else None, "user_id": str(ev.user_id),
        }
        # 实测：电脑上拖滑块只能管几分钟，验证太频繁还会被米游社限流（10306），所以不再私聊催主人去拖；
        # 这里只记下来，验证页里仍能看到（需要时可以手动用）。
        if not NOTIFY_MASTER or now - rec["notified"] < NOTIFY_GAP:
            logger.info(f"[FurinaMys] 通行证 {_mask(mys_id)} 遇到米游社验证（{game}），已记录")
            return
        rec["notified"] = now
        from gsuid_core.config import core_config

        masters = [str(m) for m in (core_config.get_config("masters") or []) if str(m)]
        text = (
            f"有群友的米游社账号（{_mask(mys_id)}）被米游社要求人机验证，芙芙暂时查不到他的数据。\n"
            f"请在运行芙芙的这台电脑上用浏览器打开：{LOCAL_URL}\n"
            "拖一下滑块就能恢复，不花钱、不用打码平台。"
        )
        for master in masters:
            try:
                await bot.bot.target_send(text, "direct", master, ev.bot_id, ev.bot_self_id)
            except Exception as exc:
                logger.warning(f"[FurinaMys] 提醒主人过验证失败: {exc}")
        logger.info(f"[FurinaMys] 通行证 {_mask(mys_id)} 需要人工验证，已提醒主人")
    except Exception as exc:
        logger.warning(f"[FurinaMys] 记录待验证账号失败: {exc}")


async def _tell_done(rec: Dict) -> None:
    from gsuid_core.gss import gss

    for t in rec.get("targets", {}).values():
        ws = gss.active_bot.get(t["ws"]) or next(iter(gss.active_bot.values()), None)
        if ws is None:
            return
        try:
            if t["group_id"]:
                from gsuid_core.segment import MessageSegment

                await ws.target_send(
                    [MessageSegment.at(t["user_id"]), MessageSegment.text(" 你的米游社账号已经恢复啦，可以再查一次了～")],
                    "group", t["group_id"], t["bot_id"], t["bot_self_id"],
                )
            else:
                await ws.target_send("你的米游社账号已经恢复啦，可以再查一次了～", "direct", t["user_id"], t["bot_id"], t["bot_self_id"])
        except Exception as exc:
            logger.warning(f"[FurinaMys] 通知群友恢复失败: {exc}")


async def scan() -> int:
    """检测全部已绑定账号，把被拦的加入待验证列表。"""
    found = 0
    users = await GsUser.get_all_user(without_error=False)
    seen = set()
    for u in users:
        mid = str(u.mys_id or "")
        if not mid or mid in seen or not u.cookie:
            continue
        seen.add(mid)
        bad = set()
        for game in ("sr", "zzz", "gs"):
            if await probe_game(u, game) is False:
                bad.add(game)
        if bad:
            found += 1
            key = _key_of(mid)
            if key is None:
                key = secrets.token_urlsafe(8)
                PENDING[key] = {"mys_id": mid, "games": set(), "since": time.time(), "notified": time.time(), "targets": {}}
            PENDING[key]["games"] = bad
    return found


def listing() -> List[Dict]:
    out = []
    for k, v in PENDING.items():
        games = sorted(v["games"]) or ["gs"]
        for g in games:
            out.append({"key": k, "game": g, "account": _mask(v["mys_id"]), "games": [g],
                        "since": time.strftime("%m-%d %H:%M", time.localtime(v["since"]))})
    return out


async def start(key: str, game: str = "gs", high: bool = False) -> Dict:
    rec = PENDING.get(key)
    if not rec:
        return {"ok": False, "msg": "这个账号已经不在待验证列表里了，刷新一下页面"}
    u = await _user_by_mys(rec["mys_id"])
    if u is None:
        PENDING.pop(key, None)
        return {"ok": False, "msg": "这个账号已经解绑了"}
    h = _header(u)
    h.update(_meta(game))
    # 普通 / 高强度两种验证码轮流试：有的账号普通那种提交后米游社不认（-1），换高强度的就行
    q = f"is_high={'true' if high else 'false'}"
    h["DS"] = get_ds_token(q)
    url = VERIFICATION.get().replace("is_high=false", q)
    r = await mys_api._mys_request(url=url, method="GET", header=h)
    if not isinstance(r, dict) or r.get("retcode") != 0:
        return {"ok": False, "msg": f"向米游社申请验证失败：{r if isinstance(r, int) else r.get('retcode')}"}
    d = r["data"]
    return {"ok": True, "gt": d["gt"], "challenge": d["challenge"], "success": d.get("success", 1),
            "new_captcha": d.get("new_captcha", 1)}


async def _recheck(mys_id: str, game: str) -> None:
    """验证通过后隔几分钟再各查一次，日志里记下这次验证管用了多久。"""
    for wait in (120, 600):
        await asyncio.sleep(wait)
        u = await _user_by_mys(mys_id)
        if u is None:
            return
        ok = await probe_game(u, game)
        logger.info(f"[FurinaMys] 通行证 {_mask(mys_id)} {game} 验证后 {wait // 60} 分钟复查: {ok}")
        if ok is False:
            return


async def submit(key: str, challenge: str, validate: str, seccode: str, game: str = "gs", high: bool = False) -> Dict:
    rec = PENDING.get(key)
    if not rec:
        return {"ok": False, "msg": "这个账号已经不在待验证列表里了"}
    u = await _user_by_mys(rec["mys_id"])
    if u is None:
        PENDING.pop(key, None)
        return {"ok": False, "msg": "这个账号已经解绑了"}
    body = {"geetest_challenge": challenge, "geetest_validate": validate, "geetest_seccode": seccode or f"{validate}|jordan"}
    h = _header(u)
    h.update(_meta(game))
    h["DS"] = get_ds_token("", body)
    r = await mys_api._mys_request(url=VERIFY.get(), method="POST", header=h, data=body)
    code = r.get("retcode") if isinstance(r, dict) else r
    logger.info(
        f"[FurinaMys] 通行证 {_mask(rec['mys_id'])} {game} 提交验证(high={high}): retcode={code} "
        f"message={(r.get('message') if isinstance(r, dict) else '')!r} ch_len={len(challenge)}"
    )
    if code != 0:
        return {"ok": False, "msg": f"米游社没有接受这次验证（{code}），再点一次「开始验证」重新拖"}
    ok = await probe_game(u, game)
    logger.info(f"[FurinaMys] 通行证 {_mask(rec['mys_id'])} {game} 人工验证后复查: {ok}")
    if ok is False:
        return {"ok": False, "msg": "验证已提交，但米游社还在拦，稍等一分钟再点一次「开始验证」"}
    asyncio.create_task(_recheck(rec["mys_id"], game))
    rec["games"].discard(game)
    if rec["games"]:
        left = "、".join(GAME_NAMES.get(g, g) for g in sorted(rec["games"]))
        return {"ok": True, "msg": f"{GAME_NAMES.get(game, game)}已恢复！还剩 {left} 要验证一次"}
    PENDING.pop(key, None)
    await _tell_done(rec)
    return {"ok": True, "msg": "已恢复！群友现在可以正常查询了"}


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>芙芙 · 米游社验证</title>
<style>
body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:#0e1626;color:#e2e8f0}
.wrap{max-width:720px;margin:40px auto;padding:0 20px}
h1{font-size:22px;margin:0 0 6px}.sub{color:#8ea0ba;font-size:13px;margin-bottom:22px;line-height:1.7}
.card{background:#14203a;border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:16px 18px;margin-bottom:12px;
display:flex;align-items:center;gap:12px}
.card b{font-size:15px}.meta{color:#8ea0ba;font-size:12px;margin-top:4px}.grow{flex:1}
button{background:#38bdf8;color:#0b1220;border:0;border-radius:9px;padding:9px 16px;font-size:14px;font-weight:600;cursor:pointer}
button.ghost{background:#1e2a44;color:#cbd5e1}button:disabled{opacity:.5;cursor:default}
.msg{font-size:13px;margin-top:6px}.ok{color:#3ecf8e}.bad{color:#ffb4b4}.empty{color:#8ea0ba;padding:30px 0;text-align:center}
</style></head><body><div class="wrap">
<h1>米游社人机验证</h1>
<div class="sub">下面是被米游社要求验证的群友账号。点「开始验证」，拖一下滑块即可恢复。<br>
只在本机可以打开；不使用任何打码平台，也不会显示 Cookie。</div>
<div style="margin-bottom:14px"><button class="ghost" id="scan">检测全部账号</button> <span id="scanmsg" class="msg"></span></div>
<div id="list"></div></div>
<script src="https://static.geetest.com/static/tools/gt.js"></script>
<script>
async function api(path, body){
  const r = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)} : {});
  return r.json();
}
const GN = {gs:"原神", sr:"星铁", zzz:"绝区零", bbb:"崩坏3"};
async function load(){
  const d = await api("/furina/verify/list");
  const box = document.getElementById("list");
  if(!d.items.length){ box.innerHTML = '<div class="empty">现在没有需要验证的账号 🎉</div>'; return; }
  box.innerHTML = "";
  for(const it of d.items){
    const el = document.createElement("div"); el.className = "card";
    const games = (GN[it.game] || it.game) + (it.game === "sr" ? "（星铁专用验证）" : "（原神 / 绝区零通用验证）");
    el.innerHTML = `<div class="grow"><b>通行证 ${it.account}</b><div class="meta">${games} · ${it.since}</div>
      <div class="msg"></div></div><button>开始验证</button>`;
    const btn = el.querySelector("button"), msg = el.querySelector(".msg");
    btn.onclick = () => verify(it.key, it.game, btn, msg);
    box.appendChild(el);
  }
}
const FAILS = {};
async function verify(key, game, btn, msg){
  const id = key + game, high = (FAILS[id] || 0) % 2 === 1;
  btn.disabled = true; msg.className = "msg"; msg.textContent = "正在向米游社申请验证…";
  const s = await api("/furina/verify/start", {key, game, high});
  if(!s.ok){ msg.className = "msg bad"; msg.textContent = s.msg; btn.disabled = false; return; }
  if(typeof initGeetest !== "function"){ msg.className = "msg bad"; msg.textContent = "极验脚本没加载出来，检查一下这台电脑的网络"; btn.disabled = false; return; }
  const cfg = {gt:s.gt, challenge:s.challenge, offline:!s.success, new_captcha:!!s.new_captcha, product:"bind", lang:"zh-cn"};
  const server = (FAILS[id] || 0) % 2 === 1 ? "api.geevisit.com" : "api.geetest.com";
  cfg.api_server = server;
  initGeetest(cfg, function(obj){
    obj.onReady(function(){ msg.textContent = "请完成弹出的验证"; obj.verify(); });
    obj.onSuccess(async function(){
      const v = obj.getValidate(); msg.textContent = "提交中…";
      const r = await api("/furina/verify/submit", {key, game, high, challenge:v.geetest_challenge, validate:v.geetest_validate, seccode:v.geetest_seccode});
      if(!r.ok) FAILS[id] = (FAILS[id] || 0) + 1;
      msg.className = "msg " + (r.ok ? "ok" : "bad"); msg.textContent = r.msg; btn.disabled = false;
      if(r.ok) setTimeout(load, 1500);
    });
    obj.onClose(function(){ btn.disabled = false; if(msg.textContent === "请完成弹出的验证") msg.textContent = ""; });
    obj.onError(function(e){
      FAILS[id] = (FAILS[id] || 0) + 1;
      const code = e ? (e.error_code || e.code || "") : "";
      const text = e ? (e.user_error || e.msg || "") : "";
      api("/furina/verify/log", {game, server, err: e ? JSON.stringify(e) : ""});
      msg.className = "msg bad"; msg.textContent = `验证出错了（${code} ${text}），已记录，再点一次「开始验证」试试`; btn.disabled = false;
    });
  });
}
document.getElementById("scan").onclick = async function(){
  this.disabled = true; const m = document.getElementById("scanmsg"); m.className = "msg"; m.textContent = "检测中，账号多的话要十几秒…";
  const r = await api("/furina/verify/scan", {}); m.textContent = `检测完成，发现 ${r.found} 个被拦的账号`; this.disabled = false; load();
};
load();
</script></body></html>"""


def register_routes() -> None:
    """把验证页挂到 GsCore 自己的网页服务上（只监听 127.0.0.1）。"""
    try:
        from gsuid_core.app_life import app
    except Exception as exc:
        logger.warning(f"[FurinaMys] 验证页注册失败: {exc}")
        return
    if any(getattr(r, "path", "") == PAGE_PATH for r in app.router.routes):
        return

    def local(request: Request) -> bool:
        host = request.client.host if request.client else ""
        return host in ("127.0.0.1", "::1", "localhost")

    async def page(request: Request):
        if not local(request):
            return HTMLResponse("forbidden", status_code=403)
        return HTMLResponse(PAGE)

    async def items(request: Request):
        if not local(request):
            return JSONResponse({"items": []}, status_code=403)
        return JSONResponse({"items": listing()})

    async def do_scan(request: Request):
        if not local(request):
            return JSONResponse({"found": 0}, status_code=403)
        return JSONResponse({"found": await scan()})

    async def do_start(request: Request):
        if not local(request):
            return JSONResponse({"ok": False, "msg": "forbidden"}, status_code=403)
        data = await request.json()
        return JSONResponse(await start(str(data.get("key", "")), str(data.get("game", "gs")), bool(data.get("high"))))

    async def do_submit(request: Request):
        if not local(request):
            return JSONResponse({"ok": False, "msg": "forbidden"}, status_code=403)
        data = await request.json()
        return JSONResponse(await submit(str(data.get("key", "")), str(data.get("challenge", "")),
                                         str(data.get("validate", "")), str(data.get("seccode", "")),
                                         str(data.get("game", "gs")), bool(data.get("high"))))

    async def do_log(request: Request):
        if not local(request):
            return JSONResponse({"ok": False}, status_code=403)
        data = await request.json()
        logger.info(f"[FurinaMys] 验证页极验报错: game={data.get('game')} server={data.get('server')} err={str(data.get('err'))[:300]}")
        return JSONResponse({"ok": True})

    routes = [
        (PAGE_PATH, page, ["GET"]), (PAGE_PATH + "/list", items, ["GET"]), (PAGE_PATH + "/scan", do_scan, ["POST"]),
        (PAGE_PATH + "/start", do_start, ["POST"]), (PAGE_PATH + "/submit", do_submit, ["POST"]),
        (PAGE_PATH + "/log", do_log, ["POST"]),
    ]
    for path, fn, methods in routes:
        app.add_api_route(path, fn, methods=methods, include_in_schema=False)
    # 放到最前面，避免被控制台前端的兜底路由抢先匹配
    added = app.router.routes[-len(routes):]
    del app.router.routes[-len(routes):]
    app.router.routes[0:0] = added
    logger.info(f"[FurinaMys] 人工验证页已就绪：{LOCAL_URL}")
