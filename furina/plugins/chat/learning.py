"""群聊自主学习：记住群友外号、风格偏好、词条设定，并在回复时注入提示词。

词条/设定/外号的增删查由 AI 意图识别驱动（见 memory_intent.py），这里只做
确定性的读写与持久化；风格纠正仍被动捕捉。记录持久化到本地 JSON，重启不丢。
"""

import re
import threading

from nonebot import logger

from ...core import roles
from ...core.store import NsCache

# 记忆文件放在 bot 运行目录（D:\old-qq-bot\bot\learned.json）
_store = NsCache("learned")

# 各群记忆上限，防止无限膨胀
MAX_ALIASES = 50
MAX_STYLE_NOTES = 20
MAX_FACTS = 30
MAX_FACT_STORAGE = 200

_lock = threading.Lock()

# 无意义的 target，匹配到这些词时不记录设定
_MEANINGLESS_TARGETS = ("的", "了", "是", "在", "有", "和", "与", "或", "但", "而", "这", "那", "什么", "怎么", "为什么", "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "群主", "机器人", "大家", "所有人")
# 疑问词 value，匹配到这些词时不记录设定（说明是在问问题，不是在设定）
_QUESTION_VALUES = ("什么", "怎么", "为什么", "吗", "呢", "啥", "咋", "几多", "多少", "哪些")
# 规范词条格式：XX的设定：内容（兼容“的的设定”历史脏数据）
_SETTING_FACT_RE = re.compile(r"^(.{1,20}?)(?:的的|的)?设定[：:]\s*(.+)$")
# 词条目标里的引导词/角色词，规范化时剥掉
_TARGET_PREFIX_RE = re.compile(r"^(?:给|帮|把|为|向|对|添加|删除|删掉|查询|查|记住|记|问|设置|设|加)\s*")
_TARGET_ROLE_RE = re.compile(r"^(?:群里的|群内|我们|你们|这个|那个|这位|对象|群里|本群)\s*")


def _load() -> dict:
    """{群号: {aliases, style_notes, facts}}；存 SQLite，面板改动会自动重新载入。"""
    return _store.load()


def _save():
    try:
        _store.save()
    except Exception as e:
        logger.warning(f"[学习] 保存记忆失败: {e}")


def _group(gid) -> dict:
    data = _load()
    g = data.setdefault(str(gid), {"aliases": [], "style_notes": [], "facts": []})
    g.setdefault("aliases", [])
    g.setdefault("style_notes", [])
    g.setdefault("facts", [])
    return g


def _is_setting_fact(text: str) -> bool:
    """只有“XX的设定：内容”这种规范词条才算有效记忆，闲聊一律不算。"""
    text = (text or "").strip()
    m = _SETTING_FACT_RE.match(text)
    if not m:
        return False
    target = m.group(1).strip(" ，,。：:、")
    value = m.group(2).strip()
    if not target or not value:
        return False
    if target in _MEANINGLESS_TARGETS or target.endswith("的"):
        return False
    if any(q in value for q in _QUESTION_VALUES):
        return False
    return True


def _is_style_note(note: str) -> bool:
    """只注入像“说话要求”的风格记录，过滤提问和刷屏圣经类垃圾。"""
    note = (note or "").strip()
    if not note or len(note) > 80:
        return False
    return not any(k in note for k in ("群圣经", "？", "?", "是什么", "吗", "呢", "分别是什么", "认不清角色"))


def _recent_uids(gid, n: int = 40) -> set:
    try:
        from . import memory
        return {str(m.get("uid")) for m in (memory.get_history(int(gid)) or [])[-n:] if m.get("uid")}
    except Exception:  # noqa: BLE001
        return set()


def _pick_aliases(gid, aliases: list, query: str) -> list:
    """只带最近在场的人、或者这句话里提到的人的称呼（不是把 50 个人的外号全塞进去）。"""
    here = _recent_uids(gid)
    q = query or ""
    picked = [a for a in aliases if str(a.get("user_id")) in here
              or any(x and len(x) >= 2 and x in q for x in (str(a.get("alias") or ""), str(a.get("nickname") or "")))]
    return picked[-15:]


def _pick_facts(facts: list, query: str) -> list:
    """词条按这句话的相关度想起（鱼的记忆式召回）；点名提到的词条一定带上。"""
    from . import config, recall
    q = query or ""
    named = [f for f in facts if (_fact_target(f.get("fact", ""))[0] or "\u0000") in q]
    rows = recall.recall([f.get("fact", "") for f in facts], q, max_items=config.RECALL_MAX_ITEMS,
                         max_chars=config.RECALL_MAX_CHARS) if q.strip() else []
    hit = {r["item"] for r in rows}
    out = named + [f for f in facts if f.get("fact", "") in hit and f not in named]
    return out[:max(len(named), config.RECALL_MAX_ITEMS)]


def get_group_summary(gid, query: str | None = None) -> str:
    """把某群记住的信息拼成提示词片段；没有记忆时返回空串。

    query=None（老调用）：全部带上；给了 query 且开着「按相关度想起」：称呼只带在场/被提到的人，
    词条只带跟这句话有关的（说话要求一直带，那是规矩）。"""
    from . import config
    g = _group(gid)
    parts = []
    focused = query is not None and config.RECALL
    aliases = _pick_aliases(gid, g["aliases"], query or "") if focused else g["aliases"][-MAX_ALIASES:]
    if aliases:
        lines = "\n".join(
            f"- {a['nickname']}（{a['user_id']}）：叫他/她 {a['alias']}"
            for a in aliases
        )
        parts.append("你记住的群友称呼（聊天中可以用这些称呼叫对方）：\n" + lines)
    if g["style_notes"]:
        notes = [n for n in g["style_notes"] if _is_style_note(n.get("note", ""))]
        if notes:
            lines = "\n".join(f"- {n['note']}" for n in notes[-MAX_STYLE_NOTES:])
            parts.append("群友对你的说话要求（要认真遵守，别再犯）：\n" + lines)
    if g["facts"]:
        valid = [f for f in g["facts"] if _is_setting_fact(f.get("fact", ""))]
        valid = _pick_facts(valid, query or "") if focused else valid[-MAX_FACTS:]
        if valid:
            lines = "\n".join(f"- {f['fact']}" for f in valid)
            parts.append("你记住的群友词条/设定：\n" + lines)
    return "\n\n" + "\n\n".join(parts) if parts else ""


def get_group_names(gid) -> list:
    """返回群里记住的昵称/外号列表（用于检查回复是否提到了无关的人）。"""
    g = _group(gid)
    names = []
    for a in g.get("aliases", []):
        for key in ("alias", "nickname"):
            v = (a.get(key) or "").strip()
            if v and v not in names:
                names.append(v)
    for f in g.get("facts", []):
        ft, _ = _fact_target(f.get("fact", ""))
        if ft and ft not in names:
            names.append(ft)
    return names


def _fuzzy_resolve(target: str, names: list[str]) -> str:
    """把模型偶尔打错/简写的人名修正回已知人名；没有接近的名字就原样返回。"""
    t = (target or "").strip()
    if len(t) < 2:
        return t
    for n in names:
        n = (n or "").strip()
        if not n or n == t:
            continue
        # 等长且只有一个字不同：小木屁 -> 小木屑
        if len(n) == len(t) and sum(a != b for a, b in zip(n, t)) <= 1:
            return n
        # 短称呼/漏字：小木 -> 小木屑，且首字一致
        if n.startswith(t) and len(n) >= len(t):
            return n
    return t


def _upsert_into(items: list, key_field: str, key: str, new_item: dict) -> bool:
    """按 key 更新已有条目（保持插入位置）或追加新条目，返回是否发生变化。"""
    for i, it in enumerate(items):
        if it.get(key_field) == key:
            if it.get("value") == new_item.get("value"):
                return False
            items[i] = new_item
            return True
    items.append(new_item)
    return True


def _remember(gid: int, kind: str, new_item: dict) -> bool:
    """写入一条记忆，返回是否真的发生变化（用于日志）。"""
    with _lock:
        g = _group(gid)
        if kind == "alias":
            changed = _upsert_into(g["aliases"], "user_id", new_item["user_id"], new_item)
        elif kind == "style":
            # 风格要求按内容去重：同一句话只说一次
            for n in g["style_notes"]:
                if n["note"] == new_item["note"]:
                    return False
            g["style_notes"].append(new_item)
            # 只注入最近 MAX_STYLE_NOTES 条，存储也设上限，避免文件无限膨胀
            if len(g["style_notes"]) > MAX_STYLE_NOTES * 5:
                g["style_notes"] = g["style_notes"][-MAX_STYLE_NOTES * 5:]
            changed = True
        elif kind == "fact":
            for f in g["facts"]:
                if f["fact"] == new_item["fact"]:
                    return False
            g["facts"].append(new_item)
            changed = True
        else:
            return False
        if changed:
            _save()
        return changed


def normalize_target(raw: str, nickname: str = "") -> str:
    """把用户随口叫的人名整理成可匹配的规范名。"""
    t = (raw or "").strip()
    t = t.strip("“”\"'「」『』‘’ ，,。！？!?~～、")
    if t in ("我", "我自己", "说话人"):
        t = nickname or ""
    t = _TARGET_PREFIX_RE.sub("", t)
    t = _TARGET_ROLE_RE.sub("", t)
    # 去掉“的/的设定/的所有词条”这类尾巴
    t = re.sub(r"(?:的)?(?:所有|全部|整个)?(?:的)?(?:设定|词条|外号|绰号|昵称|标签|档案|记忆|资料|信息|记录)?$", "", t)
    t = re.sub(r"(?:的|啊|呀|呢|啦|吧|哦|喔)$", "", t).strip()
    # 说的是机器人自己：统一成当前角色名（芙宁娜角色仍是"芙宁娜"）
    if any(k in t for k in roles.self_words()):
        return roles.display_name()
    return t


def resolve_target(gid, raw: str, nickname: str = "") -> str:
    """解析目标：先规范化，再用已记住的外号映射回本人昵称。"""
    t = normalize_target(raw, nickname)
    if not t:
        return ""
    g = _group(gid)
    for a in g.get("aliases", []):
        alias = (a.get("alias") or "").strip()
        if alias and alias == t:
            nick = (a.get("nickname") or "").strip()
            return nick or alias
    known = []
    for a in g.get("aliases", []):
        nick = (a.get("nickname") or "").strip()
        if nick and nick not in known:
            known.append(nick)
    for f in g.get("facts", []):
        ft, _ = _fact_target(f.get("fact", ""))
        if ft and ft not in known:
            known.append(ft)
    return _fuzzy_resolve(t, known)


def _fact_target(fact_text: str) -> tuple[str, str]:
    """从“XX的设定：内容”里拆出目标和内容；格式不对返回空。"""
    m = re.match(r"^(.+?)的设定[：:]\s*(.*)$", fact_text or "")
    if not m:
        return "", ""
    return m.group(1).strip(), m.group(2).strip()


def add_setting(gid, target: str, value: str) -> tuple[bool, str]:
    """添加/更新一条词条，返回 (是否变化, 格式化后的词条文本)。"""
    target = resolve_target(gid, target)
    value = (value or "").strip().strip(" ，,。：:、")
    if not target or not value:
        return False, ""
    if len(target) > 20 or len(value) > 120:
        return False, ""
    fact = f"{target}的设定：{value}"
    with _lock:
        g = _group(gid)
        for f in g["facts"]:
            if f.get("fact") == fact:
                return False, fact
        g["facts"].append({"fact": fact, "ts": _now()})
        if len(g["facts"]) > MAX_FACT_STORAGE:
            g["facts"] = g["facts"][-MAX_FACT_STORAGE:]
        _save()
    return True, fact


def delete_settings(gid, target: str, value: str = "") -> tuple[int, list[str]]:
    """删除目标词条/外号；value 为空删除该目标全部。返回 (条数, 删除内容)。"""
    target = resolve_target(gid, target)
    value = (value or "").strip()
    deleted = []
    if not target:
        return 0, deleted
    with _lock:
        g = _group(gid)
        # 词条：只删“目标 的设定”前缀完全一致的内容，不再子串随便匹配
        new_facts = []
        for f in g["facts"]:
            ft, fv = _fact_target(f.get("fact", ""))
            if ft and ft == target and (not value or value in fv):
                deleted.append(f.get("fact", ""))
            else:
                new_facts.append(f)
        if len(new_facts) != len(g["facts"]):
            g["facts"] = new_facts
        # 外号：目标等于昵称/外号/QQ号时删除对应外号
        new_aliases = []
        for a in g["aliases"]:
            nick = (a.get("nickname") or "").strip()
            alias = (a.get("alias") or "").strip()
            uid = str(a.get("user_id", ""))
            if target in (nick, alias, uid) and (not value or value in alias):
                deleted.append(f"{nick} 的外号: {alias}")
            else:
                new_aliases.append(a)
        if len(new_aliases) != len(g["aliases"]):
            g["aliases"] = new_aliases
        if deleted:
            _save()
    return len(deleted), deleted


def delete_by_value(gid, *candidates: str) -> tuple[int, list[str]]:
    """只说了要删的内容、没说是谁的（「删除ntr苦主，铸币」）：按内容找设定删。

    候选从长到短试，第一个能对上的就删掉对上的那些：内容完全一样，或者候选至少 4 个字且是内容的一部分
    （太短的词只认完全一样，免得「删除张三」误删别人设定里提到张三的那条）。都对不上返回 (0, [])。"""
    cands = sorted({(c or "").strip().strip(" ，,。：:、") for c in candidates if c}, key=len, reverse=True)
    cands = [c for c in cands if len(c) >= 2]
    with _lock:
        g = _group(gid)
        for c in cands:
            hit = [f for f in g["facts"] if (fv := _fact_target(f.get("fact", ""))[1])
                   and (c == fv or (len(c) >= 4 and c in fv))]
            if hit:
                g["facts"] = [f for f in g["facts"] if f not in hit]
                _save()
                return len(hit), [f.get("fact", "") for f in hit]
    return 0, []


def query_settings(gid, target: str) -> tuple[list[str], list[str]]:
    """查询目标的词条与外号，返回 (词条列表, 外号描述列表)。"""
    target = resolve_target(gid, target)
    facts, aliases = [], []
    if not target:
        return facts, aliases
    with _lock:
        g = _group(gid)
        for f in g["facts"]:
            ft, fv = _fact_target(f.get("fact", ""))
            if ft and ft == target:
                facts.append(f.get("fact", ""))
        for a in g["aliases"]:
            nick = (a.get("nickname") or "").strip()
            alias = (a.get("alias") or "").strip()
            uid = str(a.get("user_id", ""))
            if target in (nick, alias, uid):
                aliases.append(f"{nick} 的外号: {alias}")
    return facts, aliases


# ========== 消息信号识别（仅保留高置信度的被动学习） ==========

# 外号声明：叫我XX / 以后叫我XX / XX是我外号 / 你可以叫我XX / 大家都叫我XX
# 全部要求整句以声明开头，且只在艾特机器人时记录，避免“怎么叫我了”这类误判
_ALIAS_RE = [
    re.compile(r"^(?:以后|平时|大家|你们|你可以|你也可以|就)?(?:叫|喊|称呼)我\s*[「『\"'“”]?\s*([^\s，。！？!?「」『』\"'“”]{2,8})\s*[」』\"'“”]?\s*(?:就行|就好|吧|叭|呗|哦|喔)?$"),
    re.compile(r"^([^\s，。！？!?「」『』\"'“”]{2,8})\s*(?:是|就是)\s*我(?:的)?(?:外号|绰号|昵称|花名|小名)"),
    re.compile(r"^(?:以后|平时|你可以|你们可以)?叫我\s*[「『\"'“”]?\s*([^\s，。！？!?「」『』\"'“”]{2,8})\s*[」』\"'“”]?"),
    re.compile(r"^(?:可以|都)叫我\s*[「『\"'“”]?\s*([^\s，。！？!?「」『』\"'“”]{2,8})\s*[」』\"'“”]?"),
]

# 风格纠正：别太自恋 / 少怼人 / 温柔一点 / 别总提群主 等
_STYLE_RE = [
    re.compile(r"(?:别|不要|别老|别总|少|少点|别那么|别这么)\s*(太)?\s*([\u4e00-\u9fa5]{2,6})"),
    re.compile(r"(?:可以|能不能|希望|要求|记住)\s*(?:说话|回复)?\s*(?:温柔|简短|正常|礼貌|自然|活泼|可爱|傲娇|克制|低调|高冷|话少|话多|幽默|贴心|稳重|低调)\s*(?:一点|一些|点|些)?$"),
    re.compile(r"(?:以后|接下来|从现在起)\s*(?:说话|回复|聊天)?\s*(?:温柔|简短|正常|礼貌|自然|低调|克制|少说)\s*(?:一点|一些|点|些)?"),
    # 纯要求句：艾特机器人时"温柔一点/说话简短点/话少点"这类直接记录
    re.compile(r"^(?:说话|回复|聊天)?\s*(?:温柔|简短|正常|礼貌|自然|活泼|可爱|傲娇|克制|低调|高冷|话少|话多|幽默|贴心|稳重)\s*(?:一点|一些|点|些)?$"),
]

# 触发风格纠正的"指令词"，避免把闲聊误判成要求
_STYLE_CMD = ("别", "不要", "少", "可以", "能不能", "希望", "要求", "记住", "以后", "接下来", "从现在起")
# 只有提到这些"风格词"才记成对我的要求，避免"别着急""别走"之类误判
_STYLE_KEYWORDS = (
    "自恋", "炫耀", "傲娇", "怼", "怼人", "温柔", "话多", "话少", "啰嗦",
    "长篇", "高冷", "凶", "礼貌", "脏话", "提群主", "刷屏", "表情", "图片",
    "句号", "机械", "生硬", "复读", "夸张", "中二", "粘人", "简短", "自然",
    "低调", "克制", "活泼", "可爱", "幽默", "贴心", "稳重", "正常",
)


def observe_message(gid: int, user_id: str, nickname: str, text: str, has_at_bot: bool) -> list[str]:
    """从一条群消息里捕捉可学习的信息。返回本次学到的新内容描述（用于日志）。

    词条/设定的增删查不走这里，由 AI 意图识别驱动；这里只保留外号和风格要求，
    且都要求艾特机器人，避免把普通闲聊记成长期记忆。
    """
    text = (text or "").strip()
    if not text:
        return []
    learned = []

    # 外号：艾特机器人时，且整句就是“叫我XX/XX是我外号”这类声明才记
    if has_at_bot and len(text) <= 40:
        for pat in _ALIAS_RE:
            m = pat.search(text)
            if m:
                alias = (m.group(1) or "").strip()
                # 去掉"就行/就好/吧/叭/呗/哦/喔/啦"这类尾缀，只留真正的称呼
                alias = re.sub(r"(?:就行|就好|就好了|吧|叭|呗|哦|喔|啦|呀|呢|嘛)$", "", alias).strip()
                # 去掉尾缀后只剩 1 个字（如“叫我干嘛”→“干”）不是外号
                if alias and 2 <= len(alias) <= 8:
                    changed = _remember(gid, "alias", {
                        "user_id": user_id,
                        "nickname": nickname,
                        "alias": alias,
                        "value": alias,
                        "ts": _now(),
                    })
                    if changed:
                        learned.append(f"记住 {nickname} 的外号: {alias}")
                break

    # 风格纠正：优先只在艾特机器人时记，避免把群友互相说的话记成对我的要求
    if has_at_bot:
        for pat in _STYLE_RE:
            m = pat.search(text)
            if m:
                groups = [x for x in m.groups() if x]
                topic = groups[-1] if groups else text.strip()
                # 纯要求句（正则以 ^ 开头，如"温柔一点""说话简短点"）本身就算指令；
                # 带"别/少/希望"等前缀的句子才额外要求出现指令词
                is_pure = pat.pattern.startswith("^")
                if topic and any(k in topic or k in text for k in _STYLE_KEYWORDS) \
                        and (is_pure or any(cmd in text for cmd in _STYLE_CMD)):
                    note = f"{nickname} 说：{text.strip()[:40]}"
                    if _remember(gid, "style", {"note": note, "ts": _now()}):
                        learned.append(f"记下风格要求: {note}")
                break

    return learned


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M")
