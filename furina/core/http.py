"""共享的 httpx 异步客户端：同一用途复用连接池（TLS 握手、DNS 只做一次），不再每次请求新建。

``client("miyoushe", headers=..., follow_redirects=True)`` 按名字取；客户端绑定在当前事件循环上，
换了循环（测试里多次 asyncio.run）会自动新建。进程退出时调用 ``aclose_all()``。
"""
from __future__ import annotations

import asyncio

import httpx

_clients: dict[tuple[str, int], httpx.AsyncClient] = {}


def client(name: str, **kwargs) -> httpx.AsyncClient:
    loop_id = id(asyncio.get_running_loop())
    key = (name, loop_id)
    c = _clients.get(key)
    if c is None or c.is_closed:
        for k in [k for k in _clients if k[0] == name and k[1] != loop_id]:
            _clients.pop(k, None)  # 旧循环已经结束的客户端直接丢弃
        kwargs.setdefault("timeout", 15.0)
        c = _clients[key] = httpx.AsyncClient(**kwargs)
    return c


async def aclose_all() -> None:
    loop_id = id(asyncio.get_running_loop())
    for key in list(_clients):
        c = _clients.pop(key)
        if key[1] == loop_id and not c.is_closed:
            try:
                await c.aclose()
            except Exception:
                pass
