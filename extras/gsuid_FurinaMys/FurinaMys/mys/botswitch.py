"""换机器人 QQ 号：把 GsCore 订阅里记着的旧机器人 QQ 改成新号。

自动签到、签到报告、体力推送等订阅都带着「当时的机器人 QQ」；上游有些「改订阅」操作按这个 QQ 精确匹配，
换号后就对不上了。NoneBot 侧（furina/plugins/bot_switch）在新机器人连上时调用这里的本机接口同步一次。
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from gsuid_core.logger import logger
from gsuid_core.utils.database.models import Subscribe

PATH = "/furina/bot-self"


def _mask(qq: str) -> str:
    qq = str(qq or "")
    return f"{qq[:2]}***{qq[-2:]}" if len(qq) > 4 else qq


async def migrate(bot_id: str, self_id: str) -> int:
    if not bot_id or not self_id.isdigit():
        return 0
    rows = await Subscribe.select_rows(distinct=False, bot_id=bot_id) or []
    old = [r for r in rows if str(r.bot_self_id or "") != self_id]
    for r in old:
        await Subscribe.update_data_by_data({"id": r.id}, {"bot_self_id": self_id})
    if old:
        olds = sorted({_mask(str(r.bot_self_id or "")) for r in old})
        logger.info(f"[FurinaMys] 机器人换号：{len(old)} 条订阅从 {'、'.join(olds)} 改到 {_mask(self_id)}")
    return len(old)


def register_routes() -> None:
    try:
        from gsuid_core.app_life import app
    except Exception as exc:
        logger.warning(f"[FurinaMys] 换号同步接口注册失败: {exc}")
        return
    if any(getattr(r, "path", "") == PATH for r in app.router.routes):
        return

    async def bot_self(request: Request):
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            return JSONResponse({"moved": 0}, status_code=403)
        data = await request.json()
        try:
            moved = await migrate(str(data.get("bot_id", "")), str(data.get("self_id", "")))
        except Exception as exc:
            logger.warning(f"[FurinaMys] 换号同步失败: {exc}")
            return JSONResponse({"moved": 0, "error": str(exc)[:200]}, status_code=500)
        return JSONResponse({"moved": moved})

    app.add_api_route(PATH, bot_self, methods=["POST"], include_in_schema=False)
    added = app.router.routes[-1:]
    del app.router.routes[-1:]
    app.router.routes[0:0] = added
