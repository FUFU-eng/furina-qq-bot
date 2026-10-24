"""角色（人格）系统 + 共享设定系统。

- 角色库（SQLite persona/roles）：每个机器人 QQ 绑定一个角色；角色 prompt 只放"性格人格"。
- 通用指令/操作规则 DAILY_RULES（含发图 / 艾特 / 搜图 / 强化 / 精华 / 词条等标记）
  由代码自动拼到每个角色后面，无需写进角色提示词。
- 共享设定（SQLite persona/shared_lore）：故事 / 背景类长设定，可与角色多对多关联；
  仅当对方消息命中该设定触发词时才注入，平时不占 token。
- 某 QQ 绑定的 role_id 记录在 persona/profiles。
"""

from ...core import db, roles as _roles

from .persona import DAILY_RULES, LORE, LORE_FULL, PERSONA, _IDENTITY_KEYWORDS, daily_rules_for

# 播种的芙宁娜背景设定 ID：它的"适用角色"为空时只对芙宁娜类角色生效，不会串到胡桃等其他角色身上
FURINA_LORE_ID = "furina_genshin_lore"

# 存 SQLite（docs 命名空间 persona）：roles={"roles":[...]}、profiles=[...]、shared_lore={"items":[...]}
_NS = "persona"
ROLES_PATH, PROFILES_PATH, SHARED_LORE_PATH = "roles", "profiles", "shared_lore"

# 旧角色提示词里若已自带这套通用规则，就不重复追加
_RULES_MARKER = ("[SEND_IMAGE", "指令标记")


def _load_json(key):
    return db.get(_NS, key)


def _save_json(key, data):
    try:
        db.put(_NS, key, data)
        return True
    except Exception:
        return False


def load_roles():
    data = _load_json(ROLES_PATH)
    if isinstance(data, dict) and isinstance(data.get("roles"), list):
        return data["roles"]
    return []


def load_profiles():
    data = _load_json(PROFILES_PATH)
    return data if isinstance(data, list) else []


def load_shared_lore():
    """读取共享设定列表；文件不存在时播种一条'芙宁娜原神完整背景'。"""
    data = _load_json(SHARED_LORE_PATH)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    seeding = data is None
    seed = [{
        "id": FURINA_LORE_ID,
        "name": "芙宁娜·原神完整背景",
        "keywords": "，".join(_IDENTITY_KEYWORDS),
        "content": LORE_FULL,
        "role_ids": [],  # 空列表 = 关联全部角色
    }]
    # 只在文件确实不存在时才播种写盘。旧版文件读失败（控制面板正在保存、半截 JSON）
    # 也会直接用种子覆盖，用户在面板里维护的全部共享设定就此丢失。
    if seeding:
        _save_json(SHARED_LORE_PATH, {"items": seed})
    return seed


def _active_qq():
    from . import config
    return config.BOT_QQ


def _resolve_role(qq: str):
    """返回当前 QQ 绑定的角色 dict；没绑定或找不到返回 None。"""
    if not qq:
        qq = _active_qq()
    role_id = ""
    for p in load_profiles():
        if str(p.get("qq")) == str(qq):
            role_id = p.get("role_id", "")
            break
    if not role_id:
        return None
    for r in load_roles():
        if str(r.get("id")) == str(role_id):
            return r
    return None


def _has_rules(base: str) -> bool:
    return any(marker in base for marker in _RULES_MARKER)


def build_persona(qq: str = "") -> str:
    """构造当前 QQ 的 system prompt：角色人格 + 自动追加的通用指令/操作规则。"""
    role = _resolve_role(qq)
    if role is None:
        return PERSONA + "\n\n" + LORE + "\n\n" + DAILY_RULES
    base = (role.get("prompt") or "").strip()
    if not base:
        return PERSONA + "\n\n" + LORE + "\n\n" + DAILY_RULES
    # 兼容旧角色：提示词里已自带指令规则就不重复追加
    if _has_rules(base):
        return base
    # 芙宁娜类角色原样带 DAILY_RULES；其他角色带去掉芙宁娜专属内容的通用版
    return base + "\n\n" + daily_rules_for(role)


def build_shared_lore(qq: str, text: str) -> str:
    """挑出'当前角色已关联 且 触发词命中当前消息'的共享设定拼成注入文本，无则返回空串。"""
    role = _resolve_role(qq)
    rid = str(role.get("id")) if role else ""
    furina = _roles.is_furina(role)
    t = (text or "").lower()
    hits = []
    for item in load_shared_lore():
        role_ids = [str(x) for x in (item.get("role_ids") or [])]
        # 指定了关联角色，且当前角色不在其中 → 跳过（空列表=关联全部）
        if role_ids and rid and rid not in role_ids:
            continue
        # 芙宁娜背景没指定适用角色时只给芙宁娜类角色，别让其他角色被问"你是谁"时背出芙宁娜的身世
        if not role_ids and item.get("id") == FURINA_LORE_ID and not furina:
            continue
        raw_kw = (item.get("keywords") or "").replace("，", ",").replace("、", ",")
        kws = [k.strip().lower() for k in raw_kw.split(",") if k.strip()]
        # 没填触发词的设定不自动注入（避免误常驻烧 token）；填了则需命中其一
        if not kws or not any(k in t for k in kws):
            continue
        content = (item.get("content") or "").strip()
        if content:
            hits.append(content)
    if not hits:
        return ""
    return (
        "【相关背景设定（对方问起时自然使用，像本人回忆一样说出来，"
        "别机械背书、也别主动一次性倒完）】\n" + "\n\n".join(hits)
    )
