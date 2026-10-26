"""手机端筛图接口：只监听 127.0.0.1，由 Cloudflare 隧道转出去；默认不需要密码，可在设置里开启连接密码。

单独一个 uvicorn，跑在后台线程里（不占 NoneBot 的 8080，也不会接管 Ctrl+C）。
"""
from __future__ import annotations

import threading
import re
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from nonebot import logger
from starlette.concurrency import run_in_threadpool

from ...services import pic_inbox as svc

_fails: dict[str, list[float]] = {}
_FAIL_MAX, _FAIL_WINDOW = 10, 600
_op_locks = [threading.Lock() for _ in range(32)]


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


def _once(body: dict, action) -> dict:
    """Android 的请求带随机操作号，回包丢了重发也不会再次删除/导出。只存操作结果，不存密码。"""
    key = str(body.get("op") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        return action()
    ns = "pic_inbox_app_ops"
    with _op_locks[int(key[:8], 16) % len(_op_locks)]:
        old = svc.db.get(ns, key)
        if old:
            return old["result"]
        res = action()
        busy = res.get("reason") == "busy" or "busy" in (res.get("reasons") or {}).values()
        if not busy:
            svc.db.put(ns, key, {"ts": time.time(), "result": res})
            # 滚动保留三天；不在每张图上扫数据库。
            if key.endswith("00"):
                cutoff = time.time() - 3 * 86400
                for k, v in svc.db.items(ns).items():
                    if v.get("ts", 0) < cutoff:
                        svc.db.delete(ns, k)
        return res


def build_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/ping")
    def ping(req: Request):
        _auth(req)
        return {"ok": True, "name": "芙宁娜筛图", "stats": svc.stats()}

    @app.get("/api/list")
    def list_items(req: Request, limit: int = 40, newest: int = 0, offset: int = 0):
        _auth(req)
        names = _names()
        rows = svc.pending(min(max(limit, 1), 500), newest_first=bool(newest), offset=offset)
        svc.warm_previews([it["id"] for it in rows])  # 后台先把缩略图做好
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
        res = await run_in_threadpool(_once, body, lambda: svc.decide(str(body.get("id") or ""), bool(body.get("keep"))))
        return {**res, "stats": svc.stats()}

    @app.post("/api/decide_many")
    async def decide_many(req: Request):
        _auth(req)
        body = await req.json()
        res = await run_in_threadpool(_once, body, lambda: svc.decide_many([str(i) for i in (body.get("ids") or [])][:500], bool(body.get("keep"))))
        return {**res, "stats": svc.stats()}

    @app.post("/api/undo_many")
    async def undo_many(req: Request):
        _auth(req)
        body = await req.json()
        res = await run_in_threadpool(_once, body, lambda: svc.undo_many([str(i) for i in (body.get("ids") or [])][:500]))
        return {**res, "stats": svc.stats()}

    @app.post("/api/undo")
    async def undo(req: Request):
        _auth(req)
        body = await req.json()
        res = await run_in_threadpool(_once, body, lambda: svc.undo(str(body.get("id") or "")))
        return {**res, "stats": svc.stats()}

    @app.get("/app")
    def app_apk():
        """手机 App 安装包：浏览器打开这个地址就能下载安装（安装包里没有任何密码）。"""
        p = svc.root() / "app" / "furina-pics.apk"
        if not p.exists():
            raise HTTPException(404, "安装包还没放上来")
        return FileResponse(p, media_type="application/vnd.android.package-archive",
                            filename="furina-pics.apk", headers={"Cache-Control": "no-cache"})

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
