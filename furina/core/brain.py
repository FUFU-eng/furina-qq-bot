"""聊天「大脑」的设置：四种模式、二代仿真的醒来条件、思考强度、表情包本子。

全部热读取 .env（面板「模式与内核」页写入），改完下一条消息生效。不依赖 nonebot，面板也能 import。

模式（AI_CHAT_MODE，每个群还可以在 AI_CHAT_MODE_GROUPS 单独指定）：
- 二代仿真 sim2：DSH 智能体内核。群消息先攒着，满足「醒来条件」时她醒过来看一眼未读，
  自己决定说话 / 发表情 / 收藏表情 / 查资料 / 继续潜水，所有发送仍过外层限速与权限；
- 一代仿真 sim1：原来那套（先判断再回复 + 随机接话 + 主动冒泡），默认；
- 聊天 chat：一问一答，只回艾特 / 叫名字 / 引用她的话，不随机接话、不主动冒泡；
- Agent agent：群里按「聊天」处理，主人艾特就交给 Agent；主人私聊她时交给能操作电脑的 DSH Agent
  （还要另外打开 AI_CHAT_AGENT_ENABLED，默认关）。二代仿真的群里主人明确派活时她也会自己交给 Agent。
"""
from __future__ import annotations

import re

from . import db, env

MODES = {"sim2": "二代仿真", "sim1": "一代仿真", "chat": "聊天", "agent": "Agent"}
MODE_CHOICES = ["二代仿真", "一代仿真", "聊天", "Agent"]
_MODE_ALIASES = {
    "sim2": "sim2", "二代": "sim2", "二代仿真": "sim2", "v2": "sim2", "2": "sim2", "dsh": "sim2",
    "sim1": "sim1", "一代": "sim1", "一代仿真": "sim1", "v1": "sim1", "1": "sim1", "legacy": "sim1",
    "chat": "chat", "聊天": "chat", "聊天模式": "chat", "问答": "chat",
    "agent": "agent", "智能体": "agent", "agent模式": "agent",
}
EFFORTS = ("off", "low", "high", "max")
EFFORT_NAMES = {"off": "不思考", "low": "低", "high": "高", "max": "最高", "auto": "自动"}
THINK_CHOICES = ["不思考", "低", "高", "最高", "自动"]
_EFFORT_ALIASES = {
    "off": "off", "关": "off", "关闭": "off", "不思考": "off", "none": "off", "0": "off", "disabled": "off",
    "low": "low", "低": "low", "1": "low",
    "high": "high", "高": "high", "2": "high", "medium": "high", "中": "high",
    "max": "max", "最高": "max", "最大": "max", "极限": "max", "3": "max",
    "auto": "auto", "自动": "auto",
}
_SPLIT = re.compile(r"[|｜;；\n]+")


def parse_mode(raw: str, default: str = "sim1") -> str:
    key = (raw or "").strip().lower().replace(" ", "")
    return _MODE_ALIASES.get(key, _MODE_ALIASES.get((raw or "").strip(), default))


def parse_effort(raw: str, default: str = "off") -> str:
    key = (raw or "").strip().lower().replace(" ", "")
    return _EFFORT_ALIASES.get(key, _EFFORT_ALIASES.get((raw or "").strip(), default))


def parse_group_map(raw: str) -> dict[str, str]:
    """「群号=值|群号=值」（也认 : ： 、逗号分隔）→ {群号: 值}。"""
    out: dict[str, str] = {}
    for part in _SPLIT.split((raw or "").replace("，", ",")):
        for item in part.split(","):
            item = item.strip()
            m = re.match(r"^(\d{5,12})\s*[=:：]\s*(.+)$", item)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


# ---------------- 模式 ----------------
def default_mode() -> str:
    return parse_mode(env.get("AI_CHAT_MODE", "一代仿真"), "sim1")


def mode_for(group_id=None) -> str:
    if group_id is not None:
        over = parse_group_map(env.get("AI_CHAT_MODE_GROUPS", "")).get(str(group_id))
        if over:
            return parse_mode(over, default_mode())
    return default_mode()


def legacy_random_allowed(group_id) -> bool:
    """随机接话 / 主动冒泡 / 惦记人这类「没人叫她也开口」的旧功能，只在一代仿真里跑（二代由醒来条件接管）。"""
    return mode_for(group_id) == "sim1"


def proactive_allowed(group_id) -> bool:
    """冷场开话题、主动找人：一代照旧；二代可选（AI_CHAT_SIM2_PROACTIVE）；聊天 / Agent 不主动。"""
    m = mode_for(group_id)
    return m == "sim1" or (m == "sim2" and env.get_bool("AI_CHAT_SIM2_PROACTIVE", False))


def agent_enabled() -> bool:
    return env.get_bool("AI_CHAT_AGENT_ENABLED", False)


# ---------------- 思考强度 ----------------
def thinking_setting(group_id=None) -> str:
    """面板设的档位（可能是 auto）。群单独设的优先。"""
    base = parse_effort(env.get("AI_CHAT_THINKING", "不思考"), "off")
    if group_id is not None:
        over = parse_group_map(env.get("AI_CHAT_THINKING_GROUPS", "")).get(str(group_id))
        if over:
            return parse_effort(over, base)
    return base


def auto_level() -> str:
    lv = parse_effort(env.get("AI_CHAT_THINKING_AUTO_LEVEL", "高"), "high")
    return lv if lv in EFFORTS and lv != "off" else "high"


def auto_at_level() -> str:
    """自动档下被艾特 / 引用 / 叫名字、但不像在提问时想多深（默认低：理解谁在跟谁说话、接得通顺）。"""
    lv = parse_effort(env.get("AI_CHAT_THINKING_AUTO_AT", "低"), "low")
    return lv if lv in EFFORTS else "low"


def auto_follow_level() -> str:
    """自动档下「她刚说完有人接话」时想多深（默认低：先看清是不是在接她的话）。"""
    lv = parse_effort(env.get("AI_CHAT_THINKING_AUTO_FOLLOW", "低"), "low")
    return lv if lv in EFFORTS else "low"


_QUESTION = re.compile(r"[?？]|怎么|为什么|为啥|是不是|能不能|可不可以|会不会|多少|哪[个里些儿]|什么|啥|吗|呢\s*$|帮我|查一?下|搜一?下|解释|分析|推荐|对比|区别|算一?下")


def looks_like_question(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 4 and bool(_QUESTION.search(t))


def effort_for(group_id=None, kind: str = "random", text: str = "") -> str:
    """这一轮用多大的思考强度：off / low / high / max。

    kind：random（随机插话 / 醒来看看）、follow（刚说完有人接话）、proactive（主动冒泡）、at（被艾特 / 叫到 / 引用）、
    search（查资料之后）、tool（用工具之后）、private（主人私聊 / Agent）。
    自动档：随机插话和主动冒泡不思考；被问问题、查资料、用工具时用「自动档思考强度」；
    被叫到但不像提问时用「被叫到也想一下」的档位（默认低）。
    """
    s = thinking_setting(group_id)
    if s != "auto":
        return s if s in EFFORTS else "off"
    if kind in ("search", "tool", "private"):
        return auto_level()
    if kind == "at":
        return auto_level() if looks_like_question(text) else auto_at_level()
    if kind == "follow":
        return auto_follow_level()
    return "off"


def escalated_effort(group_id=None, base: str = "off") -> str:
    """自动档下用了查资料 / 看图之类工具后升到的档位；固定档就不变。"""
    if thinking_setting(group_id) == "auto":
        return auto_level()
    return base


def thinking_budget(effort: str, base: int) -> int:
    """思考要占输出 token：开思考时给足预算，否则会只想不说（被截断成空回复）。"""
    extra = {"low": 2048, "high": 6144, "max": 16384}.get(effort, 0)
    return base + extra


# ---------------- 二代仿真：醒来条件 ----------------
WAKE_KEYS = ("at", "name", "reply", "random", "idle_minutes", "self_adjust")
_WAKE_NS = "brain_wake"


def wake_defaults() -> dict:
    return {
        "at": env.get_bool("AI_CHAT_WAKE_AT", True),
        "name": env.get_bool("AI_CHAT_WAKE_NAME", True),
        "reply": env.get_bool("AI_CHAT_WAKE_REPLY", True),
        "random": max(0.0, min(1.0, env.get_float("AI_CHAT_WAKE_RANDOM", 0.05))),
        "idle_minutes": max(0, env.get_int("AI_CHAT_WAKE_IDLE_MINUTES", 30)),
        "self_adjust": env.get_bool("AI_CHAT_WAKE_SELF_ADJUST", True),
    }


def wake_follow_seconds() -> int:
    """她刚说完话的这么多秒内，群里再来新消息就醒来看看是不是在接她的话（0 = 关）。"""
    return max(0, min(600, env.get_int("AI_CHAT_WAKE_FOLLOW_SECONDS", 90)))


def scenes_enabled() -> bool:
    """二代仿真每轮带 1~2 个「这种场合真人怎么回」的示例场景。"""
    return env.get_bool("AI_CHAT_SIM2_SCENES", True)


def wake_random_cap() -> float:
    return max(0.0, min(1.0, env.get_float("AI_CHAT_WAKE_SELF_MAX_RANDOM", 0.2)))


def wake_overrides(group_id) -> dict:
    """她自己调过的（存数据库，面板可以一键清掉）。"""
    v = db.get(_WAKE_NS, str(group_id), {}) or {}
    return v if isinstance(v, dict) else {}


def wake_conf(group_id) -> dict:
    conf = wake_defaults()
    if conf["self_adjust"]:
        over = wake_overrides(group_id)
        if "random" in over:
            conf["random"] = max(0.0, min(wake_random_cap(), float(over["random"])))
        if "idle_minutes" in over:
            conf["idle_minutes"] = max(0, int(over["idle_minutes"]))
    return conf


def adjust_wake(group_id, random_p=None, idle_minutes=None, reason: str = "") -> dict:
    """她自己改醒来方式（有上下限，主人关掉「允许她自己调整」就改不了）。返回生效后的配置或 {error}。"""
    if not wake_defaults()["self_adjust"]:
        return {"error": "主人没有允许你自己调整醒来方式"}
    import time

    def _upd(cur):
        cur = dict(cur or {})
        if random_p is not None:
            cur["random"] = round(max(0.0, min(wake_random_cap(), float(random_p))), 3)
        if idle_minutes is not None:
            m = int(idle_minutes)
            cur["idle_minutes"] = 0 if m <= 0 else max(10, min(720, m))
        cur["reason"] = str(reason or "")[:60]
        cur["ts"] = time.time()
        return cur

    db.update(_WAKE_NS, str(group_id), _upd, default={})
    return wake_conf(group_id)


def clear_wake_overrides(group_id=None) -> int:
    if group_id is not None:
        db.delete(_WAKE_NS, str(group_id))
        return 1
    keys = list((db.items(_WAKE_NS) or {}).keys())
    for k in keys:
        db.delete(_WAKE_NS, k)
    return len(keys)


def all_wake_overrides() -> dict:
    return dict(db.items(_WAKE_NS) or {})


# ---------------- DSH 内核 ----------------
def dsh_settings() -> dict:
    return {
        "max_steps": max(2, min(40, env.get_int("AI_CHAT_DSH_MAX_STEPS", 8))),
        "timeout": max(20, min(900, env.get_int("AI_CHAT_DSH_TIMEOUT", 120))),
        "fallback": env.get_bool("AI_CHAT_DSH_FALLBACK", True),
        "merge_seconds": max(0.0, min(30.0, env.get_float("AI_CHAT_DSH_MERGE_SECONDS", 4.0))),
        "idle_exit_min": max(0, env.get_int("AI_CHAT_DSH_IDLE_EXIT_MIN", 30)),
        "agent_steps": max(4, min(80, env.get_int("AI_CHAT_AGENT_MAX_STEPS", 30))),
        "agent_timeout": max(30, min(1800, env.get_int("AI_CHAT_AGENT_TIMEOUT", 600))),
        # 群会话的上下文上限（千 token）：DSH 自带的压缩要到模型窗口的 80% 才动手（DeepSeek 是 1M 窗口 ≈ 80 万），
        # 每次醒来都把十几万字的旧聊天重发一遍，钱全花在这。超过就换新会话，带上最近几十条聊天接着聊。0 = 不限。
        # 2026-10-30 起默认 30k：60k 时平均每次请求带 3.4 万 token，命中缓存也是一大笔；30k 换会话更勤但每次都轻
        "context_max_k": max(0, min(900, env.get_int("AI_CHAT_DSH_CONTEXT_MAX_K", 30))),
        "recap_lines": max(5, min(120, env.get_int("AI_CHAT_DSH_RECAP_LINES", 30))),
        # qq_say 发出去 / qq_silence 之后直接结束这一轮：省掉模型最后那次「好，结束」的请求（每次都要重发整段上下文）
        "end_after_say": env.get_bool("AI_CHAT_DSH_END_AFTER_SAY", True),
        # 每轮「你现在的状态」最多多少字；同一个会话里已经给过的段落不再重发（会话里本来就有）
        "dynamic_max": max(0, min(6000, env.get_int("AI_CHAT_SIM2_DYNAMIC_MAX", 1500))),
    }


def follow_gate() -> bool:
    """接话醒来前先用免费模型（或本地规则）判断新消息是不是在接她的话，不是就不叫醒主模型。"""
    return env.get_bool("AI_CHAT_WAKE_FOLLOW_GATE", True)


def peak_wake_factor() -> float:
    """高峰时段（价格翻倍）接话 / 潜水醒来的概率乘这个数（1 = 不减）。"""
    return max(0.0, min(1.0, env.get_float("AI_CHAT_WAKE_PEAK_FACTOR", 0.6)))


# ---------------- 表情包本子 ----------------
def sticker_settings() -> dict:
    return {
        "enabled": env.get_bool("AI_CHAT_STICKERBOOK", True),
        "collect": env.get_bool("AI_CHAT_STICKER_COLLECT", True),
        "collect_daily": max(0, env.get_int("AI_CHAT_STICKER_COLLECT_DAILY_MAX", 5)),
        "auto_note": env.get_bool("AI_CHAT_STICKER_AUTO_NOTE", True),
        "note_hourly": max(0, env.get_int("AI_CHAT_STICKER_NOTE_PER_HOUR", 12)),
        "import_fav": env.get_bool("AI_CHAT_STICKER_IMPORT_FAV", True),
        "prompt_max": max(5, min(200, env.get_int("AI_CHAT_STICKER_PROMPT_MAX", 40))),
    }
