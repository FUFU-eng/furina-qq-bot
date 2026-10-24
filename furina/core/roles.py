"""当前角色（人格）信息：让聊天和各插件知道"机器人现在是谁"。

角色存在 SQLite（docs 命名空间 persona）：roles={"roles":[...]}、profiles=[...]（QQ → role_id）。
每个角色除了 id / name / prompt 外，还可带两个可选字段：
- aliases：群友怎么叫 TA（逗号分隔），用于纯文本 @TA 的识别、"TA 自己"的指代识别；
  第一个称呼同时作为固定台词里的名字。没填时取角色名里"-"前面的部分（如「胡桃-默认」→「胡桃」）。
- self_names：TA 的自称（逗号分隔），用于识别"说的是机器人自己"。

名字里带「芙宁娜」的角色、以及没绑定角色时的内置人格，都按芙宁娜处理：
固定台词、称呼和通用规则保持原来的芙宁娜口吻，完全不变。

只依赖 core：面板、工具和各插件都可以直接导入。
"""
from __future__ import annotations

import re
from typing import Any

from . import db, env

_NS = "persona"
FURINA_ALIASES = ("芙宁娜", "芙芙", "水神大人")
FURINA_SELF_WORDS = ("芙芙", "芙宁娜", "本水神", "本导演", "本大导演", "水神大人", "本大明星", "芙宁娜大人")
_CURRENT = object()  # 哨兵：表示"当前 QQ 绑定的角色"
_AT_CQ_RE = re.compile(r"\[(?:CQ:)?at[:,]qq=(\d+)[^\]]*\]")


def load_roles() -> list[dict]:
    data = db.get(_NS, "roles")
    if isinstance(data, dict) and isinstance(data.get("roles"), list):
        return [r for r in data["roles"] if isinstance(r, dict)]
    return []


def load_profiles() -> list[dict]:
    data = db.get(_NS, "profiles")
    return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []


def current_role(qq: str = "") -> dict | None:
    """当前（或指定）机器人 QQ 绑定的角色；没绑定、找不到都返回 None（= 内置芙宁娜人格）。"""
    qq = str(qq or env.bot_qq() or "")
    role_id = ""
    # 热路径（每条消息读很多次）：用只读缓存，面板改了下一次读取即生效
    profiles = db.get_cached(_NS, "profiles")
    for p in (profiles if isinstance(profiles, list) else []):
        if not isinstance(p, dict):
            continue
        if str(p.get("qq")) == qq:
            role_id = str(p.get("role_id") or "")
            break
    if not role_id:
        return None
    data = db.get_cached(_NS, "roles")
    roles = data.get("roles") if isinstance(data, dict) else None
    for r in (roles if isinstance(roles, list) else []):
        if isinstance(r, dict) and str(r.get("id")) == role_id:
            return r  # 共享的缓存对象：只读
    return None


def split_names(raw) -> list[str]:
    """"胡桃, 胡堂主、堂主" / ["胡桃", "胡堂主"] → ["胡桃", "胡堂主", "堂主"]（去空、去重、保序）。"""
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = re.split(r"[,，、;；\s]+", str(raw or ""))
    out: list[str] = []
    for x in items:
        x = x.strip().lstrip("@＠")
        if x and x not in out:
            out.append(x)
    return out


def _pick(role):
    return current_role() if role is _CURRENT else role


def _base_name(role: dict) -> str:
    name = str(role.get("name") or "").strip()
    return re.split(r"\s*[-－—·•|｜(（]\s*", name, maxsplit=1)[0].strip() or name


def is_furina(role=_CURRENT) -> bool:
    role = _pick(role)
    if role is None:
        return True
    text = str(role.get("name") or "") + " " + " ".join(split_names(role.get("aliases")))
    return "芙宁娜" in text or "芙芙" in text


def aliases(role=_CURRENT) -> list[str]:
    """群友对机器人的称呼（第一个是主名字）。"""
    role = _pick(role)
    if role is None:
        return list(FURINA_ALIASES)
    names = split_names(role.get("aliases"))
    if not names:
        base = _base_name(role)
        names = [base] if base else []
    if is_furina(role):
        names = ["芙宁娜"] + [n for n in names if n != "芙宁娜"]
        names += [n for n in FURINA_ALIASES if n not in names]
    return names


def mention_aliases(qq: str = "") -> list[str]:
    """返回可用于识别/剥离 `@机器人` 的全部称呼（按长度降序排列，确保先匹配『芙宁娜-黑芙』再匹配『芙宁娜』）。"""
    target_qq = str(qq or env.bot_qq() or "")
    role = current_role(target_qq)
    cand: list[str] = []

    def _add(val: str) -> None:
        v = (val or "").strip().lstrip("@＠")
        if v and v not in cand:
            cand.append(v)

    for a in aliases(role):
        _add(a)

    if role:
        full_name = str(role.get("name") or "").strip()
        _add(full_name)
        for part in re.split(r"\s*[-－—·•|｜(（)）]\s*", full_name):
            if len(part.strip()) >= 2:
                _add(part.strip())

    for p in load_profiles():
        if not target_qq or str(p.get("qq")) == target_qq:
            nick = str(p.get("nickname") or "").strip()
            _add(nick)
            for part in re.split(r"\s*[-－—·•|｜(（)）]\s*", nick):
                if len(part.strip()) >= 2:
                    _add(part.strip())

    if is_furina(role):
        for fn in ("芙宁娜-白芙", "芙宁娜-黑芙", "白芙", "黑芙", "芙宁娜", "芙芙", "水神大人"):
            _add(fn)

    cand.sort(key=len, reverse=True)
    return cand


def is_bot_at(event: Any) -> bool:
    """统一判断消息事件是否艾特了当前机器人（兼容任意切换后的机器人 QQ、CQ 码与纯文本 @角色名/群名片）。"""
    if bool(getattr(event, "to_me", False)):
        return True
    self_id = str(getattr(event, "self_id", "") or "")
    cfg_qq = str(env.bot_qq() or "")
    bot_ids = {x for x in (self_id, cfg_qq) if x}

    orig = getattr(event, "original_message", None) or getattr(event, "message", None)
    if orig is not None:
        try:
            for seg in orig:
                stype = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
                sdata = (seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", None)) or {}
                if stype == "at" and str(sdata.get("qq") or "") in bot_ids:
                    return True
        except Exception:
            pass

    raw = str(getattr(event, "raw_message", "") or "")
    if raw and bot_ids and (set(_AT_CQ_RE.findall(raw)) & bot_ids):
        return True

    text_check = raw
    try:
        pt = event.get_plaintext()
        if pt:
            text_check = f"{raw} {pt}"
    except Exception:
        pass

    for name in mention_aliases(self_id):
        if f"@{name}" in text_check or f"＠{name}" in text_check:
            return True
    return False


def strip_bot_at(text: str, qq: str = "") -> str:
    """剥离消息中的 [CQ:at]/[at:qq=...] 以及纯文本 `@机器人昵称`（如 `@芙宁娜-黑芙 绑定账号` -> `绑定账号`）。"""
    s = _AT_CQ_RE.sub("", text or "").strip()
    for name in mention_aliases(qq):
        s = re.sub(rf"^[@＠]{re.escape(name)}(?:[-－—_·•\s]*(?:白芙|黑芙))?\s*[,，:：]?\s*", "", s, flags=re.IGNORECASE)
        s = s.replace(f"@{name}", "").replace(f"＠{name}", "")
    return s.strip()


def display_name(role=_CURRENT) -> str:
    names = aliases(role)
    return names[0] if names else "我"


def self_words(role=_CURRENT) -> list[str]:
    """聊天里"指的是机器人自己"的词：称呼 + 自称。"""
    role = _pick(role)
    if is_furina(role):
        words = list(FURINA_SELF_WORDS)
    else:
        words = []
    for w in aliases(role) + (split_names(role.get("self_names")) if role else []):
        if w not in words and w not in ("我", "我们", "你"):
            words.append(w)
    return words


def flavor(furina_text: str, generic_text: str, role=_CURRENT, **fmt) -> str:
    """固定台词：芙宁娜角色原样用 furina_text；其他角色用 generic_text（可用 {name} 表示角色名）。"""
    role = _pick(role)
    if is_furina(role):
        return furina_text.format(**fmt) if fmt else furina_text
    return generic_text.format(name=display_name(role), **fmt)
