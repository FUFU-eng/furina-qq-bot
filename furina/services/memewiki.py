"""查梗（参考大肥鱼的 memewiki）：「X 是什么梗」先翻自己的梗本子，再查萌娘百科，最后才上网搜。

查到的写进缓存（30 天），也顺手记进学会的梗（slang.add_learned），下次不用再查。
只返回给模型看的简短说明；在群里怎么说由她自己决定。
"""
from __future__ import annotations

import re
import time

from ..core import db, http

NS = "memewiki"
CACHE_DAYS = 30
MOEGIRL_API = "https://zh.moegirl.org.cn/api.php"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def clean_term(term: str) -> str:
    t = re.sub(r"(是什么梗|是啥梗|什么梗|啥梗|是什么意思|啥意思|什么意思|梗)$", "", (term or "").strip())
    return t.strip(" 「」“”\"'？?。.")[:30]


def _local(term: str) -> str:
    try:
        from . import slang

        hit = slang.lexicon().get(term)
        return hit[0] if hit else ""
    except Exception:  # noqa: BLE001
        return ""


def _cached(term: str) -> str:
    v = db.get(NS, term)
    if isinstance(v, dict) and time.time() - float(v.get("ts") or 0) < CACHE_DAYS * 86400:
        return str(v.get("text") or "")
    return ""


def _save(term: str, text: str, source: str) -> None:
    db.put(NS, term, {"text": text[:600], "source": source, "ts": time.time()})
    try:
        from . import slang

        first = re.split(r"[。！!\n]", text, maxsplit=1)[0][:60]
        if first:
            slang.add_learned([{"term": term[:16], "meaning": first}], source="查梗:" + source)
    except Exception:  # noqa: BLE001
        pass


async def moegirl(term: str) -> str:
    params = {"action": "query", "format": "json", "prop": "extracts", "exintro": "1", "explaintext": "1",
              "redirects": "1", "titles": term}
    try:
        r = await http.client("memewiki", headers={"User-Agent": UA}).get(MOEGIRL_API, params=params, timeout=12)
        if r.status_code != 200:
            return ""
        pages = (r.json().get("query") or {}).get("pages") or {}
        for p in pages.values():
            text = str(p.get("extract") or "").strip()
            if text and "missing" not in p:
                return re.sub(r"\s+", " ", text)[:600]
    except Exception:  # noqa: BLE001
        return ""
    return ""


async def lookup(term: str, group_id=None, web=None) -> dict:
    """返回 {"ok", "term", "text", "source"}；web 是兜底的联网搜索协程函数 (query, gid) -> str|None。"""
    t = clean_term(term)
    if not t:
        return {"ok": False, "term": t, "text": "", "source": ""}
    for source, fn in (("梗本子", _local), ("缓存", _cached)):
        text = fn(t)
        if text:
            return {"ok": True, "term": t, "text": text, "source": source}
    text = await moegirl(t)
    if text:
        _save(t, text, "萌娘百科")
        return {"ok": True, "term": t, "text": text, "source": "萌娘百科"}
    if web is not None:
        found = await web(f"{t} 梗 是什么意思", group_id)
        if found:
            _save(t, found[:600], "联网")
            return {"ok": True, "term": t, "text": found[:1500], "source": "联网"}
    return {"ok": False, "term": t, "text": "", "source": ""}
