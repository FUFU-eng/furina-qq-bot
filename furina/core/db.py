"""SQLite 存储（WAL）：取代旧项目里散落各处的 JSON 文件。

两类表：
- ``docs``：按 (namespace, key) 存一行 JSON，写入是单行事务，天然原子；
  多个进程（机器人 + 网页面板）同时读写也安全，不会再出现“半截 JSON 把记忆清空”。
- ``usage``：按天的计数（每人每日次数、各功能统计）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from . import paths

SCHEMA_VERSION = 1
_MISSING = object()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs (
    ns TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated REAL NOT NULL,
    PRIMARY KEY (ns, key)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind, group_id, user_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS usage_day_kind ON usage(day, kind);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None
# 只读热点缓存（get_cached）：本进程写入时 _gen+1；别的进程（面板）提交时 SQLite 的
# PRAGMA data_version 会变。两者都没变就直接返回内存里的对象，不查库也不重新解析 JSON。
_gen = 0
_cache: dict[tuple[str, str], tuple[int, int, Any]] = {}


def _connect() -> sqlite3.Connection:
    global _conn, _conn_path
    target = str(paths.DB_PATH)
    if _conn is not None and _conn_path == target:
        return _conn
    if _conn is not None:
        _conn.close()
    _cache.clear()
    _bump()
    paths.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=10, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    _conn, _conn_path = conn, target
    return conn


def _bump() -> None:
    global _gen
    _gen += 1


def close() -> None:
    global _conn, _conn_path
    with _lock:
        _cache.clear()
        if _conn is not None:
            _conn.close()
        _conn = _conn_path = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            _bump()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


# ---------------- docs ----------------

def get(ns: str, key: str, default: Any = None) -> Any:
    with _lock:
        row = _connect().execute("SELECT value FROM docs WHERE ns=? AND key=?", (ns, str(key))).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def get_cached(ns: str, key: str, default: Any = None) -> Any:
    """与 get 相同，但结果在数据库没变时复用同一个对象——调用方只能读、不能改它。

    适合每条消息都要读很多次的小配置（角色绑定、群权限）。面板改了会在下一次读取时自动生效。
    """
    k = (ns, str(key))
    with _lock:
        conn = _connect()
        dv = conn.execute("PRAGMA data_version").fetchone()[0]
        hit = _cache.get(k)
        if hit is not None and hit[0] == dv and hit[1] == _gen:
            return default if hit[2] is _MISSING else hit[2]
        gen = _gen
        row = conn.execute("SELECT value FROM docs WHERE ns=? AND key=?", k).fetchone()
    value: Any = _MISSING
    if row is not None:
        try:
            value = json.loads(row[0])
        except ValueError:
            value = _MISSING
    with _lock:
        if gen == _gen:
            _cache[k] = (dv, gen, value)
    return default if value is _MISSING else value


def put(ns: str, key: str, value: Any) -> None:
    payload = _dumps(value)
    with _lock:
        _bump()
        _connect().execute(
            "INSERT INTO docs(ns, key, value, updated) VALUES(?,?,?,?) "
            "ON CONFLICT(ns, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (ns, str(key), payload, time.time()),
        )


def put_many(ns: str, items: dict[str, Any]) -> None:
    now = time.time()
    rows = [(ns, str(k), _dumps(v), now) for k, v in items.items()]
    with transaction() as conn:
        conn.executemany(
            "INSERT INTO docs(ns, key, value, updated) VALUES(?,?,?,?) "
            "ON CONFLICT(ns, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            rows,
        )


def delete(ns: str, key: str) -> None:
    with _lock:
        _bump()
        _connect().execute("DELETE FROM docs WHERE ns=? AND key=?", (ns, str(key)))


def items(ns: str) -> dict[str, Any]:
    with _lock:
        rows = _connect().execute("SELECT key, value FROM docs WHERE ns=? ORDER BY key", (ns,)).fetchall()
    out: dict[str, Any] = {}
    for k, v in rows:
        try:
            out[k] = json.loads(v)
        except ValueError:
            continue
    return out


def update(ns: str, key: str, fn: Callable[[Any], Any], default: Any = None) -> Any:
    """读-改-写一行（同一事务内），fn 返回新值；返回新值。"""
    with transaction() as conn:
        row = conn.execute("SELECT value FROM docs WHERE ns=? AND key=?", (ns, str(key))).fetchone()
        cur = json.loads(row[0]) if row else default
        new = fn(cur)
        conn.execute(
            "INSERT INTO docs(ns, key, value, updated) VALUES(?,?,?,?) "
            "ON CONFLICT(ns, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (ns, str(key), _dumps(new), time.time()),
        )
        return new


# ---------------- usage counters ----------------

def usage_get(day: str, kind: str, group_id: str, user_id: str = "") -> int:
    with _lock:
        row = _connect().execute(
            "SELECT count FROM usage WHERE day=? AND kind=? AND group_id=? AND user_id=?",
            (day, kind, str(group_id), str(user_id)),
        ).fetchone()
    return int(row[0]) if row else 0


def usage_add(day: str, kind: str, group_id: str, user_id: str = "", n: int = 1, limit: int = 0) -> bool:
    """计数 +n；limit>0 且已达上限时不计数并返回 False。"""
    with transaction() as conn:
        row = conn.execute(
            "SELECT count FROM usage WHERE day=? AND kind=? AND group_id=? AND user_id=?",
            (day, kind, str(group_id), str(user_id)),
        ).fetchone()
        used = int(row[0]) if row else 0
        if limit > 0 and used >= limit:
            return False
        conn.execute(
            "INSERT INTO usage(day, kind, group_id, user_id, count) VALUES(?,?,?,?,?) "
            "ON CONFLICT(day, kind, group_id, user_id) DO UPDATE SET count=count+excluded.count",
            (day, kind, str(group_id), str(user_id), n),
        )
        return True


def usage_rows(since_day: str, kinds: tuple[str, ...] | None = None) -> list[tuple[str, str, str, str, int]]:
    sql = "SELECT day, kind, group_id, user_id, count FROM usage WHERE day>=?"
    args: list = [since_day]
    if kinds:
        sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        args.extend(kinds)
    with _lock:
        return [tuple(r) for r in _connect().execute(sql, args).fetchall()]


def usage_prune(before_day: str, keep_prefix: str = "") -> None:
    """删掉 before_day 之前的计数；keep_prefix 开头的种类（如按人记的用量 who:*）永久保留。"""
    with _lock:
        _bump()
        if keep_prefix:
            _connect().execute("DELETE FROM usage WHERE day<? AND substr(kind, 1, ?)<>?",
                               (before_day, len(keep_prefix), keep_prefix))
        else:
            _connect().execute("DELETE FROM usage WHERE day<?", (before_day,))


def ns_stamp(ns: str) -> tuple[int, float]:
    """某命名空间的 (行数, 最后修改时间)，用来判断别的进程（如面板）是否改过。"""
    with _lock:
        row = _connect().execute("SELECT count(*), coalesce(max(updated), 0) FROM docs WHERE ns=?", (ns,)).fetchone()
    return int(row[0]), float(row[1])
