"""群与群友功能权限（全部热生效，存储在 SQLite）。"""
from __future__ import annotations

from . import db, env

FEATURES: dict[str, str] = {
    "chat":          "艾特闲聊",
    "auto":          "随机接话",
    "search":        "以图搜图",
    "upscale":       "图片超分强化",
    "cmd_image":     "指令发图",
    "poke":          "戳一戳回应",
    "miyoushe":      "米游社资讯推送",
    "miyoushe_sign": "米游社签到",
    "game_query":    "游戏战绩与图鉴",
    "pixiv_rank":    "Pixiv 榜单推送",
    "pixiv_search":  "Pixiv 搜图点图",
    "pixiv_id":      "Pixiv ID 取图",
    "reminder":      "定时提醒",
    "jm":            "JM 下载",
    "proactive":     "冷场主动开话题",
    "voice":         "语音消息",
    "bili_share":    "B站刷到分享",
    "bili_pics":     "B站画师图片",
    "websearch":     "联网搜索",
    "miss":          "惦记常聊的人",
    "pic_inbox":     "收图筛选",
    "memewiki":      "查梗",
    "maker":         "认真做文件",
}

# 新入群的默认开关：米游社推送、Pixiv 榜单、定时提醒、冷场主动开话题需显式开启；
# 米游社签到（扫码绑定与自动签到）及其他日常功能默认开启，并自动回填到历史群配置。
# 语音消息默认开（还要 .env 配好 FURINA_TTS_*）；B站刷到分享默认关，按群手动打开。
# 惦记常聊的人默认开（常找她聊的人好久没出现，她会艾特 TA 随口说一两句）。
# 收图筛选默认关：只在想收图的群里打开（群友发的图会进筛图库，等人工留下/丢掉）。
# 认真做文件默认开（攻略、清单这类写成文件发群文件）。
# B站画师图片默认开：所有群共用UP名单，每群可独立关接收；初始名单为空，不会凭空推图。
DEFAULT_ON_FEATURES = frozenset(FEATURES) - {"miyoushe", "pixiv_rank", "pixiv_id", "reminder", "proactive", "pic_inbox"}


def _default_features() -> dict[str, bool]:
    return {k: (k in DEFAULT_ON_FEATURES) for k in FEATURES}


def _normalize(g: dict | None) -> dict:
    g = dict(g or {})
    g.setdefault("enabled", True)
    defaults = _default_features()
    existing_feats = g.get("features")
    if isinstance(existing_feats, dict):
        merged = dict(defaults)
        for k, v in existing_feats.items():
            if k in FEATURES:
                merged[k] = bool(v)
        g["features"] = merged
    else:
        g["features"] = defaults
    g.setdefault("disabled_users", [])
    g.setdefault("user_features", {})
    return g


def get_group(group_id: str | int) -> dict:
    # 每条消息都会查好几次权限：用只读缓存（_normalize 会浅拷贝一份再补默认值）
    return _normalize(db.get_cached("perm", str(group_id)))


def set_group_enabled(group_id: str | int, enabled: bool) -> None:
    db.update("perm", str(group_id), lambda g: {**_normalize(g), "enabled": bool(enabled)})


def set_feature_enabled(group_id: str | int, feature: str, enabled: bool) -> None:
    def _u(g):
        g = _normalize(g)
        g["features"][feature] = bool(enabled)
        return g
    db.update("perm", str(group_id), _u)


# 兼容别名
set_group_feature = set_feature_enabled


def set_user_disabled(group_id: str | int, user_id: str | int, disabled: bool) -> None:
    uid = str(user_id)
    def _u(g):
        g = _normalize(g)
        s = set(map(str, g["disabled_users"]))
        s.add(uid) if disabled else s.discard(uid)
        g["disabled_users"] = sorted(s)
        return g
    db.update("perm", str(group_id), _u)


def set_user_feature(group_id: str | int, user_id: str | int, feature: str, enabled: bool | None) -> None:
    uid = str(user_id)
    def _u(g):
        g = _normalize(g)
        uf = g["user_features"].setdefault(uid, {})
        if enabled is None:
            uf.pop(feature, None)
        else:
            uf[feature] = bool(enabled)
        return g
    db.update("perm", str(group_id), _u)


def allowed(group_id: str | int, feature: str, default: bool | None = None) -> bool:
    """检查某群是否开启了指定功能。"""
    g = get_group(group_id)
    if not g["enabled"]:
        return False
    fallback = (feature in DEFAULT_ON_FEATURES) if default is None else bool(default)
    return bool(g["features"].get(feature, fallback))


def is_Allowed(group_id: str | int, user_id: str | int, feature: str) -> bool:
    """群总开关 → 群功能开关 → 群友禁用 → 群友单独覆盖。主人永远放行（仅受群功能开关约束）。"""
    gid, uid = str(group_id), str(user_id)
    g = get_group(gid)
    if not g["enabled"]:
        return False
    if not g["features"].get(feature, feature in DEFAULT_ON_FEATURES):
        return False
    owner = env.get("AI_CHAT_GROUP_OWNER_QQ", "") or env.get("RELAY_OWNER_QQ", "")
    if uid and owner and uid == owner:
        return True
    if uid in set(map(str, g["disabled_users"])):
        return False
    uf = g["user_features"].get(uid) or {}
    if feature in uf:
        return bool(uf[feature])
    return True


def enabled_groups(feature: str) -> list[str]:
    """返回开启了指定被动推送功能的群号列表（供米游社/Pixiv 定时任务使用）。"""
    out = []
    for gid, g in db.items("perm").items():
        g = _normalize(g)
        if g["enabled"] and g["features"].get(feature, feature in DEFAULT_ON_FEATURES):
            out.append(str(gid))
    return out


def is_enabled(feature: str, group_id: str | int, default: bool | None = None) -> bool:
    """兼容旧签名 is_enabled(feature, group_id, default=True)。"""
    return allowed(group_id, feature, default=default)
