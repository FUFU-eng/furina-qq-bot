"""面板后端：FastAPI，只监听 127.0.0.1 的随机端口；/api 需要每次启动随机生成的令牌。"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from furina.core import paths

from . import __version__
from .services import assets, bili_panel, control, data, envfile, huoren_settings, logs, model, napcat, wallpapers, winproc

WEB = Path(__file__).resolve().parent / "web"
TOKEN = secrets.token_urlsafe(24)
EXTRA_STATIC: dict[str, bytes] = {}  # Qt 宿主注入 qwebchannel.js
ctl = control.controller


def _ok(**kw):
    return {"ok": True, **kw}


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.headers.get("x-panel-token") != TOKEN:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        try:
            resp = await call_next(request)
        except (ValueError, RuntimeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(ValueError)
    async def _value_error(_req, exc):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def _runtime_error(_req, exc):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (WEB / "index.html").read_text(encoding="utf-8")
        return html.replace("__PANEL_TOKEN__", TOKEN).replace("__VERSION__", __version__)

    @app.get("/static/qwebchannel.js")
    def qwebchannel():
        return Response(EXTRA_STATIC.get("qwebchannel.js", b""), media_type="application/javascript")

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    # ---------------- 素材 ----------------
    @app.get("/media/asset/{name}")
    def media_asset(name: str):
        p = assets.named(name)
        if not p:
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "max-age=3600"})

    @app.get("/media/bg/{kind}/{index}")
    def media_bg(kind: str, index: int):
        p = assets.background(kind, index)
        if not p:
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "max-age=3600"})

    @app.get("/media/wp/{ident}")
    def media_wp(ident: str):
        p = wallpapers.path_of(ident)
        if not p:
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "max-age=3600"})

    @app.get("/media/thumb/{ident}")
    def media_thumb(ident: str):
        b = wallpapers.thumbnail(ident)
        if b is None:
            raise HTTPException(404)
        return Response(b, media_type="image/jpeg", headers={"Cache-Control": "max-age=600"})

    @app.get("/media/qrcode")
    def media_qr():
        p = napcat.qrcode_path()
        age = napcat.qr_age()
        if not p or age is None or age > napcat.QR_MAX_AGE:
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-store"})

    # ---------------- 总览 / 控制 ----------------
    @app.get("/api/overview")
    def overview():
        st = dict(ctl.state)
        qq = envfile.get("BOT_QQ", "")
        nick = next((p.get("nickname", "") for p in data.profiles() if str(p.get("qq")) == qq), "")
        live = napcat.groups.get()
        return _ok(state=st, busy=ctl.busy, busy_label=ctl.busy_label, bot_qq=qq, nickname=nick,
                   groups=len(live) if live else None, models=data.model_usage_today(),
                   today_chat=data.today_chat_total(), backgrounds=assets.backgrounds(),
                   now=time.time(), version=__version__,
                   transport_name="SnowLuma" if envfile.get("QQ_TRANSPORT", "napcat").lower() == "snowluma" else "NapCat",
                   snowluma_confirmed=envfile.get("SNOWLUMA_ACCOUNT_CONFIRMED", "false").lower() in ("1", "true", "yes", "on"),
                   connected_qq=(st.get("health") or {}).get("user_id", ""))

    @app.get("/api/events")
    def events(since: int = 0):
        return _ok(events=ctl.events_since(since))

    @app.post("/api/qq-transport/confirm-account")
    def confirm_snowluma_account(body: dict = Body(...)):
        from .services.qq_transport import confirm_account
        saved = confirm_account(str(body.get("uin") or ""))
        restarted = ctl.start() if not control.find_bot_processes() else False
        return _ok(**saved, bot_start_requested=restarted)

    @app.post("/api/control/{action}")
    def control_action(action: str):
        fn = {"start": ctl.start, "stop": ctl.stop, "restart": ctl.restart,
              "restart_all": lambda: ctl.restart(with_napcat=True),
              "restart_napcat": ctl.restart_napcat_for_login}.get(action)
        if action == "test":
            return _ok(**ctl.test_connection())
        if not fn:
            raise HTTPException(404)
        if not fn():
            return {"ok": False, "error": f"正在{ctl.busy_label}，请稍候"}
        return _ok()

    @app.get("/api/login")
    def login_state():
        qq = envfile.get("BOT_QQ", "")
        st = ctl.state
        if envfile.get("QQ_TRANSPORT", "napcat").lower() == "snowluma":
            gateway = napcat.is_running()
            live = st.get("health") or {}
            actual = str(live.get("user_id") or "")
            confirmed = envfile.get("SNOWLUMA_ACCOUNT_CONFIRMED", "false").lower() in ("1", "true", "yes", "on")
            if not gateway:
                state = "snowluma_stopped"
            elif live.get("state") == "online" and actual and (not confirmed or actual != qq):
                state = "snowluma_confirm"
            elif live.get("state") == "online" and confirmed and actual == qq:
                state = "online"
            elif live.get("state") == "mismatch":
                state = "snowluma_confirm"
            else:
                state = "snowluma_waiting"
            return _ok(state=state, provider="snowluma", provider_name="SnowLuma", qq=qq,
                       detected_qq=actual, confirmed=confirmed, detail=live.get("detail") or "等待连接",
                       webui=napcat.webui_url(), gateway_running=gateway, qr=False, qr_left=0, warning="")
        if ctl.busy and "NapCat" in ctl.busy_label:
            state = "restarting"
        elif st["health"]["state"] == "online":
            state = "online"
        elif not st["napcat"]:
            state = "no_napcat"
        else:
            state = {"offline": "offline", "mismatch": "mismatch"}.get(st["health"]["state"], "waiting")
        age = napcat.qr_age()
        clients = napcat.detect_other_qq() if state not in ("online", "restarting") else []
        return _ok(state=state, qq=qq, detail=st["health"]["detail"], qr=age is not None and age <= napcat.QR_MAX_AGE,
                   qr_left=max(0, int(napcat.QR_MAX_AGE - age)) if age is not None else 0,
                   warning=napcat.other_qq_warning(clients, qq), webui=bool(napcat.napcat_dir()))

    @app.post("/api/open/{what}")
    def open_thing(what: str, body: dict = Body(default={})):
        if not winproc.IS_WIN:
            return _ok(note="仅 Windows 可打开")
        if what == "webui":
            os.startfile(napcat.webui_url())  # type: ignore[attr-defined]
        elif what == "folder":
            target = {"root": paths.ROOT, "data": paths.DATA_DIR, "logs": paths.LOG_DIR,
                      "napcat": napcat.napcat_dir(), "assets": assets.root()}.get(body.get("key", "root"))
            if target and Path(target).exists():
                os.startfile(str(target))  # type: ignore[attr-defined]
        elif what == "url":
            # 只放行 B 站链接（「B站生活」页里点视频 / 动态 / 主页）
            url = str(body.get("url") or "")
            if re.match(r"^https://(?:www|t|space|m)\.bilibili\.com/[\w/?=&.#%-]*$", url):
                os.startfile(url)  # type: ignore[attr-defined]
            else:
                raise ValueError("只能打开 B 站链接")
        elif what in ("google", "x"):
            # 搜图浏览器资料夹被机器人占用时 Edge 打不开：先只停 NoneBot（NapCat / QQ 登录保留）
            if control.find_bot_processes():
                if not body.get("stop_bot"):
                    return {"ok": False, "need_stop": True, "error": "需要先停止机器人"}
                ctl._kill_bot()
                ctl.emit("info", "[系统] 已停止机器人，以便登录搜图浏览器；登录完关闭浏览器后请点「启动机器人」")
                time.sleep(2)
            profile = Path(assets.root()) / "edge_profile_v2"
            url = "https://lens.google.com/" if what == "google" else "https://x.com/login"
            subprocess.Popen(["cmd", "/c", "start", "", "msedge", f"--user-data-dir={profile}", url],
                             creationflags=winproc.NO_WINDOW)
        else:
            raise HTTPException(404)
        return _ok()

    @app.get("/api/paths")
    def get_paths():
        return _ok(items=[
            {"key": "root", "label": "程序目录", "path": str(paths.ROOT)},
            {"key": "data", "label": "数据库与缓存", "path": str(paths.DATA_DIR)},
            {"key": "logs", "label": "日志", "path": str(paths.LOG_DIR)},
            {"key": "napcat", "label": "NapCat", "path": str(napcat.napcat_dir() or "未配置 NAPCAT_DIR")},
            {"key": "assets", "label": "素材 / 超分模型 / 浏览器资料", "path": str(assets.root())},
        ])

    # ---------------- 统计 / 权限 ----------------
    @app.get("/api/stats")
    def get_stats(days: int = 1):
        return _ok(**data.stats(days))

    @app.get("/api/member_usage")
    def get_member_usage(days: int = 30, gid: str = "", total: float = 0.0):
        return _ok(**data.member_usage(days, gid, total))

    @app.post("/api/member_usage/weights")
    def set_member_usage_weights(body: dict = Body(...)):
        return _ok(weights=data.save_usage_weights(body.get("weights") or {}))

    @app.get("/api/groups")
    def get_groups(refresh: bool = False):
        if refresh:
            napcat.groups.refresh_sync()
        return _ok(groups=data.groups_list(), features=data.permission_features())

    @app.get("/api/perms/{gid}")
    def get_perms(gid: str):
        return _ok(**data.get_permissions(gid))

    @app.post("/api/perms/{gid}")
    def set_perms(gid: str, body: dict = Body(...)):
        return _ok(**data.save_permissions(gid, body.get("features") or {}))

    # ---------------- 日志 ----------------
    @app.get("/api/logs")
    def get_logs(source: str = "bot", pos: int = -1, npos: int = -1, nfile: str = ""):
        nap = {"file": None, "pos": 0, "lines": []} if npos == -2 else \
            logs.napcat_lines(nfile or None, None if npos < 0 else npos)
        return _ok(bot=logs.bot_lines(source, None if pos < 0 else pos), napcat=nap,
                   transport_name="SnowLuma" if envfile.get("QQ_TRANSPORT", "napcat").lower() == "snowluma" else "NapCat")

    @app.get("/api/logs/end")
    def logs_end():
        return _ok(**logs.end_positions())

    # ---------------- 回复次数 ----------------
    @app.get("/api/members")
    def get_members():
        return _ok(**data.members())

    @app.post("/api/limit/unified")
    def set_unified(body: dict = Body(...)):
        data.set_unified_limit(int(body.get("value", 30)))
        return _ok(unified=data.unified_limit())

    @app.get("/api/limit/{gid}/{uid}")
    def get_limit(gid: str, uid: str):
        return _ok(**data.user_limit(gid, uid))

    @app.post("/api/limit/{gid}/{uid}")
    def set_limit(gid: str, uid: str, body: dict = Body(...)):
        desc = data.save_user_limit(gid, uid, body.get("mode", "follow"), body.get("scope", "group"),
                                    int(body.get("value") or 30))
        return _ok(desc=desc)

    # ---------------- 角色 / 账号 / 共享设定 ----------------
    @app.get("/api/roles")
    def get_roles():
        qq = envfile.get("BOT_QQ", "")
        active = next((p.get("role_id", "") for p in data.profiles() if str(p.get("qq")) == qq), "")
        return _ok(roles=data.roles(), active=active, bot_qq=qq)

    @app.post("/api/roles")
    def save_roles(body: dict = Body(...)):
        data.save_roles(body.get("roles") or [])
        return _ok()

    @app.post("/api/roles/reset")
    def reset_role(body: dict = Body(...)):
        role = data.reset_role_prompt(str(body.get("role_id") or "furina"))
        return _ok(role=role, roles=data.roles())

    @app.get("/api/black_book")
    def get_black_book():
        return _ok(items=data.black_book())

    @app.post("/api/black_book")
    def add_black_book(body: dict = Body(...)):
        res = data.add_black_book(
            who=str(body.get("who") or ""),
            nickname=str(body.get("nickname") or ""),
            event=str(body.get("event") or ""),
            gid=str(body.get("gid") or ""),
        )
        if not res.get("ok"):
            raise ValueError(str(res.get("reason") or "添加失败"))
        return _ok(entry=res.get("entry"), items=data.black_book())

    @app.delete("/api/black_book/{eid}")
    def del_black_book(eid: str):
        return _ok(deleted=data.delete_black_book(eid), items=data.black_book())

    @app.post("/api/roles/activate")
    def activate_role(body: dict = Body(...)):
        qq = envfile.get("BOT_QQ", "")
        if not qq:
            raise ValueError("还没有设置机器人 QQ")
        rid = str(body.get("role_id") or "")
        nick = next((p.get("nickname", "") for p in data.profiles() if str(p.get("qq")) == qq), "")
        data.save_profile(qq, nick, rid)
        return _ok()

    @app.get("/api/lore")
    def get_lore():
        return _ok(**data.shared_lore(), roles=[{"id": r.get("id"), "name": r.get("name")} for r in data.roles()])

    @app.post("/api/lore")
    def save_lore(body: dict = Body(...)):
        return _ok(item=data.save_lore_item(body))

    @app.delete("/api/lore/{iid}")
    def del_lore(iid: str):
        data.delete_lore_item(iid)
        return _ok()

    @app.get("/api/accounts")
    def get_accounts():
        return _ok(accounts=data.profiles(), bot_qq=envfile.get("BOT_QQ", ""),
                   transport_name="SnowLuma" if envfile.get("QQ_TRANSPORT", "napcat").lower() == "snowluma" else "NapCat",
                   roles=[{"id": r.get("id"), "name": r.get("name")} for r in data.roles()])

    @app.post("/api/accounts")
    def save_account(body: dict = Body(...)):
        data.save_profile(str(body.get("qq", "")), str(body.get("nickname", "")).strip(), str(body.get("role_id") or ""))
        return _ok()

    @app.delete("/api/accounts/{qq}")
    def del_account(qq: str):
        if qq == envfile.get("BOT_QQ", ""):
            raise ValueError("不能删除正在使用的账号，请先切换到其它账号")
        return _ok(removed=data.delete_profile(qq))

    @app.get("/api/accounts/nickname/{qq}")
    def account_nick(qq: str):
        try:
            return _ok(nickname=napcat.stranger_nickname(qq))
        except Exception:
            return _ok(nickname="")

    @app.post("/api/accounts/use")
    def use_account(body: dict = Body(...)):
        if envfile.get("QQ_TRANSPORT", "napcat").lower() == "snowluma":
            raise ValueError("SnowLuma账号必须先在本地管理页手动加载，再从登录助手确认；不能通过旧的NapCat账号切换动作自动登录")
        qq = str(body.get("qq", ""))
        if not qq.isdigit():
            raise ValueError("QQ 号格式不正确")
        envfile.set_values({"BOT_QQ": qq})
        created = napcat.ensure_onebot_config(qq)
        restarted = ctl.restart(with_napcat=True) if body.get("restart", True) else False
        return _ok(config_created=created, restarting=restarted)

    # ---------------- 设置：模型 / Pixiv / 提醒 ----------------
    @app.get("/api/model")
    def get_model():
        return _ok(**model.get())

    @app.post("/api/model")
    def set_model(body: dict = Body(...)):
        model.save(body.get("base_url", ""), body.get("api_key", ""))
        return _ok()

    @app.post("/api/model/test")
    def test_model(body: dict = Body(...)):
        return _ok(**model.test(body.get("base_url", ""), body.get("api_key", "")))

    @app.get("/api/pixiv")
    def get_pixiv():
        return _ok(settings=data.pixiv_settings(), **data.pixiv_jobs())

    @app.post("/api/pixiv")
    def set_pixiv(body: dict = Body(...)):
        return _ok(settings=data.save_pixiv_settings(body))

    @app.post("/api/pixiv/aliases/check")
    def check_aliases(body: dict = Body(...)):
        return _ok(text=data.validate_pixiv_aliases(body.get("aliases")))

    @app.post("/api/pixiv/push")
    def pixiv_push(body: dict = Body(...)):
        return _ok(request=data.pixiv_push_now(body.get("batches") or [], body.get("groups") or None, body.get("after") or ""))

    @app.delete("/api/pixiv/push")
    def pixiv_cancel():
        return _ok(cancelled=data.pixiv_cancel_push())

    @app.get("/api/reminder")
    def get_reminder():
        return _ok(settings=data.reminder_settings(), counts=data.reminder_counts())

    @app.post("/api/reminder")
    def set_reminder(body: dict = Body(...)):
        return _ok(settings=data.save_reminder_settings(body))

    # ---------------- 设置：JM 下载 ----------------
    @app.get("/api/jm")
    def get_jm():
        return _ok(**data.jm_settings())

    @app.post("/api/jm")
    def set_jm(body: dict = Body(...)):
        return _ok(**data.save_jm_settings(body))

    @app.delete("/api/jm/cache")
    def clear_jm_cache_all():
        return _ok(removed=data.clear_jm_cache(), **data.jm_settings())

    @app.delete("/api/jm/cache/{album_id}")
    def clear_jm_cache_one(album_id: str):
        return _ok(removed=data.clear_jm_album(album_id), **data.jm_settings())


    # ---------------- 设置：米游社签到 ----------------
    # Dedicated game management; every route is protected by the panel token guard.
    @app.get("/api/games")
    def get_games():
        from .services import games
        return _ok(**games.snapshot())

    @app.post("/api/games/config")
    def set_games(body: dict = Body(...)):
        from .services import games
        return _ok(**games.save_settings(body))

    @app.post("/api/games/account/{qq}")
    def set_game_account(qq: str, body: dict = Body(...)):
        from .services import games
        return _ok(**games.save_account_settings(qq, body))

    @app.post("/api/games/start")
    def start_games():
        from .services import games
        return _ok(**games.ensure_running())

    @app.get("/api/games/preview/{game}")
    def game_preview(game: str):
        raise HTTPException(410, "旧自制卡片已停用；请发送对应游戏的官方帮助指令查看真实上游图片。")

    @app.get("/api/mihoyo")
    def get_mihoyo():
        return _ok(**data.mihoyo_settings())

    @app.post("/api/mihoyo")
    def set_mihoyo(body: dict = Body(...)):
        return _ok(**data.save_mihoyo_settings(body))

    @app.delete("/api/mihoyo/account/{qq}")
    def del_mihoyo_account(qq: str):
        return _ok(removed=data.delete_mihoyo_account(qq), **data.mihoyo_settings())

    # ---------------- B站生活 ----------------
    @app.get("/api/bili")
    def get_bili():
        return _ok(**bili_panel.overview())

    @app.post("/api/bili/action")
    def bili_action(body: dict = Body(...)):
        return _ok(**bili_panel.request(str(body.get("action") or ""), str(body.get("keyword") or "")))

    @app.delete("/api/bili/action")
    def bili_cancel():
        return _ok(cancelled=bili_panel.cancel_request())

    @app.post("/api/bili/login")
    def bili_login():
        return _ok(**bili_panel.login_start())

    @app.get("/api/bili/login")
    def bili_login_poll():
        return _ok(**bili_panel.login_poll())

    @app.post("/api/bili/slang/delete")
    def bili_slang_delete(body: dict = Body(...)):
        bili_panel.delete_slang(str(body.get("term") or ""))
        return _ok()

    # ---------------- 全局画师图片订阅 ----------------
    @app.get("/api/bili-pics")
    def get_bili_pics():
        return _ok(**data.bili_pics_data())

    @app.post("/api/bili-pics/settings")
    def save_bili_pics(body: dict = Body(...)):
        return _ok(**data.save_bili_pics(body))

    @app.post("/api/bili-pics/subscriptions")
    def add_bili_pic(body: dict = Body(...)):
        return _ok(**data.add_bili_pic(str(body.get("target") or "")))

    @app.delete("/api/bili-pics/subscriptions/{uid}")
    def remove_bili_pic(uid: str):
        return _ok(removed=data.remove_bili_pic(uid))

    @app.post("/api/bili-pics/check")
    def check_bili_pics():
        return _ok(queued=data.check_bili_pics())

    @app.post("/api/bili-pics/retry")
    def retry_bili_pics(body: dict = Body(...)):
        return _ok(retried=data.retry_bili_pics(body.get("confirmed") is True))

    # ---------------- 设置：活人感 / 语音 / B站生活（统一定义见 services/huoren_settings.py） ----------------
    @app.get("/api/envgroup/{name}")
    def get_envgroup(name: str):
        return _ok(**huoren_settings.get_group(name))

    @app.post("/api/envgroup/{name}")
    def set_envgroup(name: str, body: dict = Body(...)):
        return _ok(**huoren_settings.save_group(name, body.get("values") or {}))

    # ---------------- 壁纸 ----------------
    @app.get("/api/wallpapers")
    def get_wallpapers():
        return _ok(**wallpapers.public())

    @app.post("/api/wallpapers/upload")
    async def upload_wallpaper(request: Request):
        from urllib.parse import unquote

        name = unquote(request.headers.get("x-filename", ""))
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > wallpapers.MAX_UPLOAD:
                raise ValueError("文件太大（上限 512MB）")
            chunks.append(chunk)
        import anyio

        item = await anyio.to_thread.run_sync(wallpapers.add_upload, name, iter(chunks))
        return _ok(item=item)

    @app.post("/api/wallpapers/playlist")
    def wp_playlist(body: dict = Body(...)):
        return _ok(playlist=wallpapers.set_playlist(body.get("ids") or []))

    @app.post("/api/wallpapers/interval")
    def wp_interval(body: dict = Body(...)):
        return _ok(interval_seconds=wallpapers.set_interval(int(body.get("seconds", 30))))

    @app.post("/api/wallpapers/remove")
    def wp_remove(body: dict = Body(...)):
        return _ok(removed=wallpapers.remove(body.get("ids") or []))

    @app.post("/api/wallpapers/clear")
    def wp_clear():
        wallpapers.clear()
        return _ok()

    return app


class ServerThread(threading.Thread):
    """在后台线程运行 uvicorn；port=0 时自动挑空闲端口。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        super().__init__(daemon=True, name="panel-server")
        import socket

        import uvicorn

        if port == 0:
            with socket.socket() as s:
                s.bind((host, 0))
                port = s.getsockname()[1]
        self.host, self.port = host, port
        config = uvicorn.Config(create_app(), host=host, port=port, log_level="warning", access_log=False,
                                log_config=None)
        self.server = uvicorn.Server(config)

    def run(self):
        self.server.run()

    def wait_ready(self, timeout: float = 15) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.started:
                return True
            time.sleep(0.05)
        return False

    def stop(self):
        self.server.should_exit = True

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"
