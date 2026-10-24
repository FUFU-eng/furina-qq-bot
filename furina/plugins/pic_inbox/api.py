"""手机端筛图接口：只监听 127.0.0.1，由 Cloudflare 隧道转出去；每个请求都要带连接密码。

单独一个 uvicorn，跑在后台线程里（不占 NoneBot 的 8080，也不会接管 Ctrl+C）。
"""
from __future__ import annotations

import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from nonebot import logger

from ...services import pic_inbox as svc

_fails: dict[str, list[float]] = {}
_FAIL_MAX, _FAIL_WINDOW = 10, 600


def _client_ip(req: Request) -> str:
    return req.headers.get("cf-connecting-ip") or (req.client.host if req.client else "?")


def _auth(req: Request) -> None:
    if not svc.require_password():
        return  # 默认不要密码（自己用）；设置里打开「手机连接要密码」才校验
    ip = _client_ip(req)
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < _FAIL_WINDOW]
    if len(recent) >= _FAIL_MAX:
        raise HTTPException(429, "试错太多次了，过十分钟再来")
    auth = req.headers.get("authorization", "")
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else req.query_params.get("k", "")
    if not svc.check_token(given):
        recent.append(now)
        _fails[ip] = recent
        raise HTTPException(401, "密码不对")
    _fails.pop(ip, None)


def _names() -> dict[str, str]:
    from . import group_names
    return group_names()


def _item(it: dict, names: dict) -> dict:
    d = svc.public(it)
    d["gname"] = names.get(str(it.get("gid") or ""), "")
    return d


def build_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/ping")
    def ping(req: Request):
        _auth(req)
        return {"ok": True, "name": "芙宁娜筛图", "stats": svc.stats()}

    @app.get("/api/list")
    def list_items(req: Request, limit: int = 40, newest: int = 0):
        _auth(req)
        names = _names()
        rows = svc.pending(min(max(limit, 1), 200), newest_first=bool(newest))
        return {"items": [_item(it, names) for it in rows], "stats": svc.stats()}

    @app.get("/api/img/{iid}")
    def img(iid: str, req: Request, size: int = 1600):
        _auth(req)
        p = svc.preview(iid, max(0, min(size, 4096)))
        if not p:
            raise HTTPException(404, "没有这张图")
        return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/api/decide")
    async def decide(req: Request):
        _auth(req)
        body = await req.json()
        res = svc.decide(str(body.get("id") or ""), bool(body.get("keep")))
        return {**res, "stats": svc.stats()}

    @app.post("/api/undo")
    async def undo(req: Request):
        _auth(req)
        body = await req.json()
        res = svc.undo(str(body.get("id") or ""))
        return {**res, "stats": svc.stats()}

    @app.exception_handler(HTTPException)
    async def _err(_req, exc: HTTPException):
        return JSONResponse({"ok": False, "reason": exc.detail}, status_code=exc.status_code)

    return app


_server = None


def start() -> bool:
    """在后台线程启动接口；端口被占用时只记日志不影响机器人。"""
    global _server
    if _server is not None:
        return True
    try:
        import uvicorn

        cfg = uvicorn.Config(build_app(), host="127.0.0.1", port=svc.port(), log_level="warning",
                             access_log=False, lifespan="off")
        _server = uvicorn.Server(cfg)
        threading.Thread(target=_server.run, name="pic-inbox-api", daemon=True).start()
        logger.info(f"[筛图] 手机接口已启动 127.0.0.1:{svc.port()}")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[筛图] 手机接口启动失败: {e}")
        _server = None
        return False


def stop() -> None:
    if _server is not None:
        _server.should_exit = True
