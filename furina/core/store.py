"""命名空间级内存缓存：进程内读写走内存，别的进程改了数据库会自动重新载入。"""
from __future__ import annotations

import threading
from typing import Any, Iterable

from . import db


class NsCache:
    def __init__(self, ns: str):
        self.ns = ns
        self._data: dict[str, Any] | None = None
        self._stamp: tuple[int, float] | None = None
        self.lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self.lock:
            stamp = db.ns_stamp(self.ns)
            if self._data is None or stamp != self._stamp:
                self._data = db.items(self.ns)
                self._stamp = stamp
            return self._data

    def save(self, keys: Iterable[str] | None = None) -> None:
        with self.lock:
            if self._data is None:
                return
            ks = list(self._data.keys()) if keys is None else [str(k) for k in keys]
            payload = {k: self._data[k] for k in ks if k in self._data}
            if payload:
                db.put_many(self.ns, payload)
            self._stamp = db.ns_stamp(self.ns)

    def remove(self, key: str) -> None:
        with self.lock:
            if self._data is not None:
                self._data.pop(str(key), None)
            db.delete(self.ns, str(key))
            self._stamp = db.ns_stamp(self.ns)

    def invalidate(self) -> None:
        with self.lock:
            self._data = None
            self._stamp = None
