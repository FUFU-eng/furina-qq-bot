"""把旧插件的“一个 JSON 文件”映射成 SQLite 里的一行（docs 命名空间 ``files``，key=文件名）。

让搬过来的插件只改几行 IO 调用即可改存数据库，业务逻辑不动：
    docfile.exists("miyoushe_seen.json") / docfile.load(...) / docfile.save(..., data)
路径参数只取文件名部分，所以旧代码里拼好的完整路径也能直接传进来。
"""
from __future__ import annotations

import os
from typing import Any

from . import db

NS = "files"


def _key(path) -> str:
    return os.path.basename(os.fspath(path))


def exists(path) -> bool:
    return db.get(NS, _key(path)) is not None


def load(path, default: Any = None) -> Any:
    val = db.get(NS, _key(path))
    return default if val is None else val


def save(path, data: Any, **_ignored) -> None:
    db.put(NS, _key(path), data)
