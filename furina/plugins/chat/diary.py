"""她自己的日记本（参考 ai-companion-kit 的 diary.js / diary-author.js 与「进阶01·日记」）。

- 文件：data/日记/YYYY-MM-DD.md（人能直接打开看），索引在数据库里；
- 一天最多 AI_CHAT_DIARY_DAILY_MAX 条（默认 3），每条 1~4 句、第一人称口语，写具体的小事，不总结不升华；
- 每次随机一个「角度」（窗外、自己的小毛病、一件特别小的事、想他但没说的……），并告诉她最近 4 条写过什么别重复；
- 只写今天真实发生过的事（从今天的群聊里取素材），不编；
- 聊天时把最近 3 条注入提示词：「这是你写的，别装作不记得；但别在聊天里汇报」；
- 写不写由她自己决定（可以回「不写」），绝不代她写。
"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

from nonebot import logger

from . import config, llm, memory
from ...core import db, live_groups, paths, roles

NS = "diary"

ANGLES = [
    "窗外 / 天气 / 光线",
    "自己的一个小毛病",
    "今天一件特别小、特别具体的事",
    "突然想起以前的一件事",
    "嘴上没说出口的话",
    "今天偷偷干的一件事",
    "今天让你烦的一个东西",
    "想某个人但没说出来",
    "你正在等的一件事",
]

AUTHOR_PROMPT = """你是{me}。这是你自己的日记本，没人检查，想写什么写什么，也可以不写。
现在是 {now}。
{life}
今天群里跟你有关的一些真实片段（从旧到新，"你"是你自己说的）：
{today}
{people}
你最近写过的几篇（这次别重复这些内容和说法）：
{avoid}

这次的角度：{angle}（照这个角度写，别跑回老一套）

写法：第一人称、口语、1~4 句；写具体的事和当时的感受，不要复述对话、不要总结一天、不要升华、不要 markdown；
提到人就写名字（别写"用户""群友A"）；只写现在和今天真实发生过的，不编没发生的事；不要写"今天又是美好的一天"这种话。
只输出 JSON：{{"title": "12 字以内的小标题", "mood": "两三个字的心情", "tags": ["标签"], "text": "正文"}}
今天实在没什么想写的，就只输出：不写"""


def _dir() -> Path:
    d = paths.DATA_DIR / "日记"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _today(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))


def entries(limit: int = 20) -> list[dict]:
    idx = db.get(NS, "index") or []
    return list(idx)[-limit:]


def today_count(now: float | None = None) -> int:
    day = _today(now)
    return sum(1 for e in entries(50) if e.get("day") == day)


def add(entry: dict, now: float | None = None) -> dict:
    ts = time.time() if now is None else now
    e = {"day": _today(ts), "time": time.strftime("%H:%M", time.localtime(ts)), "ts": ts,
         "title": str(entry.get("title") or "")[:12], "mood": str(entry.get("mood") or "")[:4],
         "tags": [str(t)[:8] for t in (entry.get("tags") or [])][:4], "text": str(entry.get("text") or "").strip()[:300]}
    if not e["text"]:
        return {}
    db.update(NS, "index", lambda lst: (list(lst or []) + [e])[-500:])
    try:
        path = _dir() / f"{e['day']}.md"
        head = "" if path.exists() else f"# {e['day']}\n\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{head}## {e['time']} {e['title']}（{e['mood']}）\n\n{e['text']}\n\n")
    except OSError as err:
        logger.warning(f"[日记] 写文件失败（索引已存）: {err}")
    return e


def context_note(n: int = 3, now: float | None = None) -> str:
    """聊天时注入：她最近写的几条日记。"""
    if not config.DIARY:
        return ""
    recent = entries(n)
    if not recent:
        return ""
    ts = time.time() if now is None else now
    lines = []
    for e in recent:
        when = "今天" if e.get("day") == _today(ts) else ("昨天" if e.get("day") == _today(ts - 86400) else e.get("day", ""))
        lines.append(f"- {when}{e.get('time', '')}《{e.get('title', '')}》{e.get('text', '')[:80]}")
    return ("【你最近写的日记（这是你自己写的，别装作不记得；但别在聊天里汇报「我写了日记」，"
            "被问到或自然聊到才提）】\n" + "\n".join(lines))


def _today_material(now: float, limit: int = 24) -> str:
    """今天各群里跟她有关的片段：她说的话 + 紧挨着的前一句。"""
    start = time.mktime(time.strptime(_today(now), "%Y-%m-%d"))
    picked = []
    groups = live_groups.cached() or set()
    for gid in groups:
        try:
            hist = memory.get_history(int(gid))
        except (TypeError, ValueError):
            continue
        prev = None
        for e in hist:
            ts = float(e.get("ts") or 0)
            if ts < start:
                prev = e
                continue
            if e.get("role") == "assistant":
                if prev is not None and prev.get("role") == "user":
                    picked.append((float(prev.get("ts") or ts), str(prev.get("content") or "")[:60]))
                picked.append((ts, "你：" + str(e.get("content") or "")[:60]))
            prev = e
    picked.sort()
    seen, out = set(), []
    for _ts, line in picked:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return "\n".join(out[-limit:]) or "（今天群里没怎么跟你说话）"


def _people_hint() -> str:
    try:
        owner = str(config.GROUP_OWNER_QQ or "")
        if not owner:
            return ""
        from . import people
        rec = people.get(owner)
        if rec and (rec.get("impression") or rec.get("traits")):
            bits = rec.get("impression") or "；".join(people.texts(rec, "traits", 3))
            return f"你对{rec.get('name') or '群主'}的印象：{bits}"
    except Exception:  # noqa: BLE001
        pass
    return ""


def parse(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if not raw or raw.startswith("不写") or raw in ("不写。", "不想写"):
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    text = str(data.get("text") or "").strip()
    text = re.sub(r"^#+\s*|\*\*", "", text)
    if len(text) < 4:
        return None
    data["text"] = text
    return data


async def write(now: float | None = None, rng: random.Random | None = None, life: str = "") -> dict | None:
    """让她写一条日记（她可以不写）。返回写好的条目或 None。"""
    if not config.DIARY:
        return None
    ts = time.time() if now is None else now
    if today_count(ts) >= max(0, config.DIARY_DAILY_MAX):
        return None
    r = rng or random
    avoid = "\n".join(f"- 《{e.get('title', '')}》{e.get('text', '')[:60]}" for e in entries(4)) or "（还没写过）"
    prompt = AUTHOR_PROMPT.format(
        me=roles.display_name(), now=time.strftime("%m月%d日 %H:%M", time.localtime(ts)),
        life=life or "", today=_today_material(ts), people=_people_hint(), avoid=avoid, angle=r.choice(ANGLES))
    try:
        raw = await llm.chat([{"role": "user", "content": prompt}], temperature=1.15)
    except llm.LLMError as e:
        logger.warning(f"[日记] 生成失败: {e}")
        return None
    data = parse(raw)
    if not data:
        logger.info("[日记] 她这次不想写")
        return None
    e = add(data, ts)
    if e:
        logger.info(f"[日记] 她写了一条：《{e['title']}》{e['text'][:40]}")
    return e or None


def nudge_due(now: float | None = None) -> bool:
    """深夜提醒自己写日记的时机：22~02 点，或者 20 点后今天还一条没写；一天最多 2 次、至少隔 3 小时。"""
    ts = time.time() if now is None else now
    h = time.localtime(ts).tm_hour
    st = db.get(NS, "nudge") or {}
    day = _today(ts - 4 * 3600)  # 凌晨 2 点前算前一天
    if st.get("day") != day:
        st = {"day": day, "count": 0, "last": 0}
    if int(st.get("count") or 0) >= 2 or ts - float(st.get("last") or 0) < 3 * 3600:
        return False
    if not (h >= 22 or h < 2 or (h >= 20 and today_count(ts) == 0)):
        return False
    return today_count(ts) < max(0, config.DIARY_DAILY_MAX)


def mark_nudged(now: float | None = None) -> None:
    ts = time.time() if now is None else now
    day = _today(ts - 4 * 3600)

    def _u(st):
        st = dict(st or {})
        if st.get("day") != day:
            st = {"day": day, "count": 0}
        st["count"] = int(st.get("count") or 0) + 1
        st["last"] = ts
        return st
    db.update(NS, "nudge", _u)
