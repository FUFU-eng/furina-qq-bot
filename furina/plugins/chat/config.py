"""AI 聊天配置：全部热读取 .env（改完下一条消息就生效，不用重启）。

沿用旧项目键名。模块属性通过 ``__getattr__`` 按需计算，
所以旧代码里的 ``config.REPLY_PROBABILITY`` 写法不变，但永远是最新值。
"""
from __future__ import annotations

from ...core import env, llm, roles
from ...core.usage import effective_limit, parse_limit_overrides

# BOT_ALIASES（纯文本 @ 的称呼）跟随当前角色，见 __getattr__；芙宁娜角色仍是 芙宁娜/芙芙/水神大人

# 名称 -> (env 键, 类型, 默认值)
_SPEC: dict[str, tuple[str, type, object]] = {
    "BOT_QQ": ("BOT_QQ", str, ""),
    "REPLY_PROBABILITY": ("AI_CHAT_PROBABILITY", float, 0.02),
    "REPLY_SPLIT_PROBABILITY": ("AI_CHAT_REPLY_SPLIT_PROBABILITY", float, 0.55),
    "HISTORY_LIMIT": ("AI_CHAT_HISTORY_LIMIT", int, 60),
    "HISTORY_SEED_COUNT": ("AI_CHAT_HISTORY_SEED", int, 200),
    "HISTORY_SEND_LIMIT": ("AI_CHAT_HISTORY_SEND_LIMIT", int, 20),
    "HISTORY_SEND_CHARS": ("AI_CHAT_HISTORY_SEND_CHARS", int, 100),
    "HISTORY_SEND_CHARS_SELF": ("AI_CHAT_HISTORY_SEND_CHARS_SELF", int, 200),
    "HISTORY_GAP_SECONDS": ("AI_CHAT_HISTORY_GAP_SECONDS", int, 180),
    "RECENT_IMAGE_SECONDS": ("AI_CHAT_RECENT_IMAGE_SECONDS", int, 300),
    "AI_CHAT_TEMPERATURE": ("AI_CHAT_TEMPERATURE", float, 0.6),
    "LLM_TIMEOUT": ("AI_CHAT_LLM_TIMEOUT", float, 40.0),
    "AI_CHAT_MAX_TOKENS": ("AI_CHAT_MAX_TOKENS", int, 512),
    "AI_CHAT_MAX_REPLY_CHARS": ("AI_CHAT_MAX_REPLY_CHARS", int, 0),  # 整段回复总字数上限；0 = 不限（每条短靠分条）
    "QUOTE_MAX_CHARS": ("AI_CHAT_QUOTE_MAX_CHARS", int, 1500),
    "QUOTE_PROBABILITY": ("AI_CHAT_QUOTE_PROBABILITY", float, 0.15),  # 回人时引用对方那条的概率：真人偶尔才引用
    "FORWARD_MAX_NODES": ("AI_CHAT_FORWARD_MAX_NODES", int, 30),
    "GROUP_OWNER_QQ": ("AI_CHAT_GROUP_OWNER_QQ", str, ""),
    "SPECIAL_QQ": ("AI_CHAT_SPECIAL_QQ", str, ""),
    "FAVOR_ENABLED": ("AI_CHAT_FAVOR_ENABLED", bool, True),
    "POKE_PROBABILITY": ("AI_CHAT_POKE_PROBABILITY", float, 0.15),
    "POKE_REPLY": ("AI_CHAT_POKE_REPLY", bool, True),
    "POKE_COOLDOWN": ("AI_CHAT_POKE_COOLDOWN", float, 20.0),
    "DAILY_CHAT_LIMIT_PER_USER": ("AI_CHAT_DAILY_CHAT_LIMIT", int, 30),
    "DAILY_POKE_LIMIT_PER_USER": ("AI_CHAT_DAILY_POKE_LIMIT", int, 5),
    "SUMMARY_COOLDOWN": ("AI_CHAT_SUMMARY_COOLDOWN", float, 120.0),
    "WELCOME_NEW_MEMBER": ("AI_CHAT_WELCOME_NEW_MEMBER", bool, True),
    "STICKER_PROBABILITY": ("AI_CHAT_STICKER_PROBABILITY", float, 0.3),
    "STICKER_MAX_COUNT": ("AI_CHAT_STICKER_MAX_COUNT", int, 60),
    "STICKER_TTL": ("AI_CHAT_STICKER_TTL", float, 600.0),
    "CHAT_VISION": ("AI_CHAT_VISION", bool, True),
    "VISION_MAX_IMAGES": ("AI_CHAT_VISION_MAX_IMAGES", int, 2),
    "CHAT_IMAGE_DESC": ("AI_CHAT_IMAGE_DESC", bool, False),
    "CHAT_IMAGE_DESC_MAX_PER_HOUR": ("AI_CHAT_IMAGE_DESC_MAX_PER_HOUR", int, 15),
    "EMOTION_ENABLED": ("AI_CHAT_EMOTION_ENABLED", bool, True),
    "EMOTION_START_MOOD": ("AI_CHAT_EMOTION_START_MOOD", int, 60),
    "DIGEST_ENABLED": ("AI_CHAT_DIGEST_ENABLED", bool, True),
    # —— 活人感 P1：结构化上下文 / 先判断再回复 / 发送限速（连发合并、拟人延迟默认关闭）——
    "MERGE_SECONDS": ("AI_CHAT_MERGE_SECONDS", float, 6.0),  # 等 TA 把话说完：安静这么多秒才回（0=关闭）
    "MERGE_MAX_SECONDS": ("AI_CHAT_MERGE_MAX_SECONDS", float, 20.0),
    "UNFINISHED_WAIT_SECONDS": ("AI_CHAT_UNFINISHED_WAIT_SECONDS", float, 90.0),  # TA 话明显没说完（「然后」「就是」「等下」）最多再等多久
    "STRUCTURED_CONTEXT": ("AI_CHAT_STRUCTURED_CONTEXT", bool, True),
    "TRANSCRIPT_LIMIT": ("AI_CHAT_TRANSCRIPT_LIMIT", int, 30),
    "PLANNER": ("AI_CHAT_PLANNER", bool, True),
    "PLANNER_TIMEOUT": ("AI_CHAT_PLANNER_TIMEOUT", float, 8.0),
    # 省钱：被@/叫到、而前面这么多分钟群里都没人说话时，没什么上下文要理，跳过「先判断」那次调用（0=不跳）
    "PLANNER_QUIET_SKIP_MIN": ("AI_CHAT_PLANNER_QUIET_SKIP_MIN", float, 10.0),
    "PROMPT_SIZE_LOG": ("AI_CHAT_PROMPT_SIZE_LOG", bool, True),  # 日志里记每次回复提示词各段多长（看钱花在哪）
    "HUMANIZE": ("AI_CHAT_HUMANIZE", bool, True),  # 先「打字」再发（1.2 秒 + 每字 110 毫秒，最多 6 秒；消息瞬间到达最像机器人）
    "TYPING_MS_PER_CHAR": ("AI_CHAT_TYPING_MS_PER_CHAR", int, 110),
    "SEND_LIMIT_ENABLED": ("AI_CHAT_SEND_LIMIT_ENABLED", bool, True),  # 总开关：关掉 = 不管下面两个数，想发就发（熔断仍有效）
    "MAX_SEND_PER_MINUTE": ("AI_CHAT_MAX_SEND_PER_MINUTE", int, 6),   # 防风控：小鲸鱼作者 20/300 被腾讯限过
    "MAX_SEND_PER_HOUR": ("AI_CHAT_MAX_SEND_PER_HOUR", int, 80),
    "LONG_GAP_PROBABILITY": ("AI_CHAT_LONG_GAP_PROBABILITY", float, 0.3),  # 连发时偶尔愣 4~12 秒（像被打断）
    # —— 机器人互聊收尾：和群里其他 AI 机器人互相艾特时，聊几轮自然收尾，之后不再艾特/接话 ——
    "PEER_END_ENABLED": ("AI_CHAT_PEER_END", bool, True),
    "PEER_MAX_ROUNDS": ("AI_CHAT_PEER_MAX_ROUNDS", int, 3),          # 第几轮收尾
    "PEER_RESET_SECONDS": ("AI_CHAT_PEER_RESET_SECONDS", float, 300.0),  # 隔这么久算新话题
    "PEER_COOLDOWN": ("AI_CHAT_PEER_COOLDOWN", float, 900.0),        # 收尾后这段时间不再接对方
    # —— 活人感 P2：说话方式 / 表情回应 / 只发表情包 / 记得群友近况 / 冷场开话题 ——
    "STYLE_VARIETY": ("AI_CHAT_STYLE_VARIETY", bool, True),            # 每轮随机定回复长短、点名最近口头禅
    "REACTION_ENABLED": ("AI_CHAT_REACTION", bool, True),              # 给群消息贴表情（QQ 表情回应）
    "REACTION_PROBABILITY": ("AI_CHAT_REACTION_PROBABILITY", float, 0.06),  # 普通群消息顺手贴表情的概率
    "REACTION_INSTEAD_PROBABILITY": ("AI_CHAT_REACTION_INSTEAD_PROBABILITY", float, 0.5),  # 艾特只说谢谢/晚安时只贴表情
    "REACTION_COOLDOWN": ("AI_CHAT_REACTION_COOLDOWN", float, 180.0),  # 同一个群两次顺手贴表情的最短间隔
    "STICKER_ONLY_PROBABILITY": ("AI_CHAT_STICKER_ONLY_PROBABILITY", float, 0.25),  # 短句时只回一张表情包
    "LIFE_EVENTS": ("AI_CHAT_LIFE_EVENTS", bool, True),                # 记住群友近况并事后关心
    "LIFE_FOLLOWUP_PROBABILITY": ("AI_CHAT_LIFE_FOLLOWUP_PROBABILITY", float, 0.5),  # 有近况可问时随机接话概率
    "PROACTIVE_ENABLED": ("AI_CHAT_PROACTIVE", bool, True),            # 总开关；各群还要在功能权限里开
    "PROACTIVE_IDLE_MINUTES": ("AI_CHAT_PROACTIVE_IDLE_MINUTES", int, 60),
    "PROACTIVE_MAX_IDLE_HOURS": ("AI_CHAT_PROACTIVE_MAX_IDLE_HOURS", int, 6),
    "PROACTIVE_HOURS": ("AI_CHAT_PROACTIVE_HOURS", str, "10-23"),
    "PROACTIVE_DAILY_MAX": ("AI_CHAT_PROACTIVE_DAILY_MAX", int, 5),
    "PROACTIVE_GAP_MINUTES": ("AI_CHAT_PROACTIVE_GAP_MINUTES", int, 90),  # 两次主动冒泡至少隔多久
    "PROACTIVE_MIN_MESSAGES": ("AI_CHAT_PROACTIVE_MIN_MESSAGES", int, 10),
    # —— 小鲸鱼式：一条一个意群、多说几条；话多一点；惦记常聊的人；记得自己睡了多久 / 哪句没发出去 ——
    "MSG_SOFT_MAX": ("AI_CHAT_MSG_SOFT_MAX", int, 30),                 # 单条消息大约多长就该换一条（不截断，只拆）
    "BURST_MAX": ("AI_CHAT_BURST_MAX", int, 0),                        # 一次最多连发几条；0 = 不限（群主要求不限条数；教程建议 4，可在设置里改）。平时提示词引导 1~3 条
    "CHATTY": ("AI_CHAT_CHATTY", bool, True),                          # 话多一点：能接的话题就接，别习惯性潜水
    "CHATTY_QUESTION_PROBABILITY": ("AI_CHAT_CHATTY_QUESTION_PROBABILITY", float, 0.15),  # 群里有人抛问题时
    # 随机接话在这些情况下会提到多少（AI_CHAT_PROBABILITY 设成 0 时全都不生效；各自填 0 = 这种情况不加）
    "UPKEEP": ("AI_CHAT_UPKEEP", bool, True),                         # 知道群主在养她（像吃白饭的大肥鱼），不提技术和账目
    "THREAD_PROBABILITY": ("AI_CHAT_THREAD_PROBABILITY", float, 0.28),        # 说话的人刚和她聊着（90 秒内）
    "LINGER_PROBABILITY": ("AI_CHAT_LINGER_PROBABILITY", float, 0.3),         # 她刚在群里说过话、还「留着」
    "WELCOME_BACK_PROBABILITY": ("AI_CHAT_WELCOME_BACK_PROBABILITY", float, 0.45),  # 常聊的人隔了 12 小时以上回来
    "WELCOME_BACK_MISSED_PROBABILITY": ("AI_CHAT_WELCOME_BACK_MISSED_PROBABILITY", float, 0.8),  # ……而且她惦记过 TA
    "MISS_ENABLED": ("AI_CHAT_MISS", bool, True),                      # 惦记常聊的人（各群还要开「惦记常聊的人」权限，默认开）
    "MISS_MIN_TALKS": ("AI_CHAT_MISS_MIN_TALKS", int, 10),             # 14 天里艾特她至少这么多次
    "MISS_MIN_DAYS": ("AI_CHAT_MISS_MIN_DAYS", int, 3),                # 且分布在至少这么多天
    "MISS_AFTER_HOURS": ("AI_CHAT_MISS_AFTER_HOURS", float, 24.0),     # TA 多久没在群里说话开始惦记
    "MISS_GAP_DAYS": ("AI_CHAT_MISS_GAP_DAYS", float, 3.0),            # 同一个人两次惦记至少隔几天
    "MISS_MAX_UNANSWERED": ("AI_CHAT_MISS_MAX_UNANSWERED", int, 3),    # 连着几次没回就不再找 TA（TA 一说话就重置）
    "MISS_PROBABILITY": ("AI_CHAT_MISS_PROBABILITY", float, 0.35),     # 每次想起来真的去找的概率（45~110 分钟想一次）
    "MISS_DAILY_MAX": ("AI_CHAT_MISS_DAILY_MAX", int, 3),              # 每群每天最多惦记几次；0 = 不限
    "PRESENCE": ("AI_CHAT_PRESENCE", bool, True),                      # 记得自己「睡了」多久、哪几句没发出去
    "PRESENCE_SLEEP_MINUTES": ("AI_CHAT_PRESENCE_SLEEP_MINUTES", int, 20),  # 离线超过这么久醒来才算「睡了一觉」
    # —— 活人感 P3：生活状态 / B站经历 / 群友语感参考 / 语音消息 ——
    "HUOREN_V3": ("AI_CHAT_HUOREN_V3", bool, True),                   # 注入「此刻在干嘛、最近刷到啥、群友怎么说话」
    "VOICE_ENABLED": ("AI_CHAT_VOICE", bool, True),                   # 语音总开关（还要 .env 配好 FURINA_TTS_* 且群开了「语音消息」）
    "VOICE_PROBABILITY": ("AI_CHAT_VOICE_PROBABILITY", float, 0.08),  # 引擎开着时普通短句随机用语音的概率
    "VOICE_MAX_CHARS": ("AI_CHAT_VOICE_MAX_CHARS", int, 40),          # 超过这么多字不念（群友点名要听时放宽一倍）
    "VOICE_HOURLY_MAX": ("AI_CHAT_VOICE_HOURLY_MAX", int, 0),         # 每群每小时最多几条语音（0 = 不限）
    # —— 活人感：热梗 / 群梗 / 热搜 / 芙宁娜日常语料 / 去 AI 味 ——
    "SLANG": ("AI_CHAT_SLANG", bool, True),                           # 看懂群友说的梗（把出现的热词意思告诉她）
    "SLANG_PROBABILITY": ("AI_CHAT_SLANG_PROBABILITY", float, 0.35),  # 每次回复顺带给她几个「可以玩的梗」的概率
    "GROUP_MEMES": ("AI_CHAT_GROUP_MEMES", bool, True),               # 学本群多人重复说的梗
    "TRENDING": ("AI_CHAT_TRENDING", bool, True),                     # 偶尔知道今天 B 站热搜
    "STYLE_LINES": ("AI_CHAT_STYLE_LINES", bool, True),               # 每次给几句芙宁娜日常口吻当语感参考
    "BOUNDARY": ("AI_CHAT_BOUNDARY", bool, True),                     # 边界感：除群主和特别关照的人外最多抱抱，越界先警告、再犯冷落
    "COLD_SHOULDER_HOURS": ("AI_CHAT_COLD_SHOULDER_HOURS", float, 6.0),  # 警告过还越界：不理 TA 几小时
    "BOUNDARY_WARNS": ("AI_CHAT_BOUNDARY_WARNS", int, 2),               # 冷落前警告几次（亲亲 1 点、重的 2 点，到「次数+1」点冷落）
    "FORGIVE": ("AI_CHAT_FORGIVE", bool, True),                         # 被冷落的人求情悔改可以原谅（一次比一次难）
    "DEAI": ("AI_CHAT_DEAI", bool, True),                             # 去 AI 味（删客服腔、自称 AI 时重说）
    "SELF_ACTS": ("AI_CHAT_SELF_ACTS", bool, True),                   # 记得自己贴过的表情、戳过谁（被问到会承认）
    "NO_PERIOD": ("AI_CHAT_NO_PERIOD", bool, True),                   # 回复不打句号：句末删掉、句中换逗号（像真人用手机打字）
    "TALK_SHAPE": ("AI_CHAT_TALK_SHAPE", bool, True),                 # 大肥鱼式说话手感：先定回复形状、留一处「没用」的人味
    # —— 联网搜索：不知道的事自己上网查（必应 → 百度，免 Key）——
    "WEB_SEARCH": ("AI_CHAT_WEB_SEARCH", bool, True),                 # 总开关；各群还要在功能权限里开「联网搜索」（默认开）
    "WEB_SEARCH_RANDOM": ("AI_CHAT_WEB_SEARCH_RANDOM", bool, False),  # 随机接话时也允许搜（默认只在被艾特时搜）
    "WEB_SEARCH_RESULTS": ("AI_CHAT_WEB_SEARCH_RESULTS", int, 5),     # 每次给她看几条搜索结果
    "WEB_SEARCH_DAILY_MAX": ("AI_CHAT_WEB_SEARCH_DAILY_MAX", int, 0),  # 每群每天最多搜几次（0 = 不限）
    "WEB_SEARCH_SAY_WAIT": ("AI_CHAT_WEB_SEARCH_SAY_WAIT", float, 0.2),  # 要看资料时先说一句「等等/我想想」的概率
    # —— 小鲸鱼全套（ai-companion-kit / qq-bridge / QQ-agent 教程）——
    "PEOPLE": ("AI_CHAT_PEOPLE", bool, True),                         # 按「人」记一本账（跨群通用）：印象、TA 说过的事、聊过啥、心里还记着的事
    "PEOPLE_CROSS_GROUP": ("AI_CHAT_PEOPLE_CROSS_GROUP", bool, True), # 知道 TA 刚在别的群说过什么（只当背景，不说破）
    "PENDING_CHASE": ("AI_CHAT_PENDING_CHASE", bool, True),           # TA 说「等我一下 / 去洗个澡」后很久没回来，过 30~90 分钟艾特 TA 问一句
    "ATTENTION": ("AI_CHAT_ATTENTION", bool, True),                   # 察觉：没人理她发的语音、TA 连发一串、只回个「？」、只回个「喵」
    "LINGER": ("AI_CHAT_LINGER", bool, True),                         # 刚聊完会多留一会儿（更容易接话），聊太久会自己收尾潜水
    "STALE_CHAT_MINUTES": ("AI_CHAT_STALE_CHAT_MINUTES", int, 10),    # 超过这么久的消息不当「刚刚」接（别用秒回语气）
    "STALE_SILENT_MINUTES": ("AI_CHAT_STALE_SILENT_MINUTES", int, 120),  # 超过这么久的旧消息默认不回
    "CATCH_UP": ("AI_CHAT_CATCH_UP", bool, True),                     # 睡醒后：每个群最多补回一条离线期间艾特她的话（最新那条）
    "DIARY": ("AI_CHAT_DIARY", bool, True),                           # 她自己写日记（data/日记/日期.md），聊天时记得自己写过什么
    "DIARY_DAILY_MAX": ("AI_CHAT_DIARY_DAILY_MAX", int, 3),           # 一天最多写几条
    "OWN_LIFE": ("AI_CHAT_OWN_LIFE", bool, True),                     # 她自己的时间：约每 2.5 小时想干点啥（写日记 / 刷B站 / 发呆），不往群里发
    "OWN_LIFE_HOURS": ("AI_CHAT_OWN_LIFE_HOURS", float, 2.5),
    "OWNER_SEEK": ("AI_CHAT_OWNER_SEEK", bool, True),                 # 主动找群主（你）说话：45~110 分钟想一次，按心情/想念/无聊
    "OWNER_SEEK_DAILY_MAX": ("AI_CHAT_OWNER_SEEK_DAILY_MAX", int, 4),
    "OWNER_WAKE_PROBABILITY": ("AI_CHAT_OWNER_WAKE_PROBABILITY", float, 0.3),  # 群主没艾特她时她接话的概率（主人说话更容易叫醒她）
    "HEAR_VOICE": ("AI_CHAT_HEAR_VOICE", bool, True),                 # 听群友的语音（本机 FunASR 转文字，用完即退不占内存）
    "GALLERY": ("AI_CHAT_GALLERY", bool, True),                       # 喜欢的图收进自己的相册（data/相册），写一句为什么留
    "GROUP_NOTES": ("AI_CHAT_GROUP_NOTES", str, ""),                  # 每个群的环境说明：群号=说明，多个用 | 隔开（圈子主题、氛围、忌讳）
    "DIGEST_HOUR": ("AI_CHAT_DIGEST_HOUR", int, 23),
    "DIGEST_MINUTE": ("AI_CHAT_DIGEST_MINUTE", int, 50),
    "DIGEST_MAX_LINES": ("AI_CHAT_DIGEST_MAX_LINES", int, 200),
    "DIGEST_KEEP_DAYS": ("AI_CHAT_DIGEST_KEEP_DAYS", int, 30),
    # —— 参考 Yuuyuko-uu 的 DSH 插件（鱼的记忆 / 鱼的心情 / 主动搭话）——
    "RECALL": ("AI_CHAT_RECALL", bool, True),                       # 记忆按这句话的相关度「想起」，不再全量塞进提示词
    "RECALL_MAX_ITEMS": ("AI_CHAT_RECALL_MAX_ITEMS", int, 6),
    "RECALL_MAX_CHARS": ("AI_CHAT_RECALL_MAX_CHARS", int, 800),
    "MEMORY_HALF_LIFE_DAYS": ("AI_CHAT_MEMORY_HALF_LIFE_DAYS", float, 30.0),
    "MOOD_JUDGE": ("AI_CHAT_MOOD_JUDGE", bool, True),               # 每轮聊完后台判一次心情（关掉 = 老的关键词加减）
    "MOOD_BASE": ("AI_CHAT_MOOD_BASE", int, 70),
    "MOOD_HALF_HOURS": ("AI_CHAT_MOOD_HALF_HOURS", float, 12.0),
    "MOOD_CONFIDENCE": ("AI_CHAT_MOOD_CONFIDENCE", int, 60),
    "MOOD_TELL_PER_DAY": ("AI_CHAT_MOOD_TELL_PER_DAY", int, 6),
    "MOOD_JUDGE_DAILY_MAX": ("AI_CHAT_MOOD_JUDGE_DAILY_MAX", int, 150),
    "PROACTIVE_PITY": ("AI_CHAT_PROACTIVE_PITY", float, 0.1),
    "PROACTIVE_PITY_CAP": ("AI_CHAT_PROACTIVE_PITY_CAP", float, 0.4),
    "PEAK_FACTOR": ("AI_CHAT_PEAK_FACTOR", float, 0.5),
}


def _read(name: str):
    key, typ, default = _SPEC[name]
    if typ is bool:
        return env.get_bool(key, default)  # type: ignore[arg-type]
    if typ is int:
        return env.get_int(key, default)  # type: ignore[arg-type]
    if typ is float:
        return env.get_float(key, default)  # type: ignore[arg-type]
    return env.get(key, default)  # type: ignore[arg-type]


def __getattr__(name: str):
    if name in _SPEC:
        value = _read(name)
        if name == "BOT_QQ":
            # 以实际在线的机器人为准：换了 QQ 号没改 .env 也能正确识别「艾特我」「引用我」
            value = env.bot_qq() or value or _runtime_bot_qq()
        return value
    if name == "UNLIMITED_CHAT_QQ" or name == "FAVOR_LOCKED_QQ":
        return {q for q in (_read("GROUP_OWNER_QQ"), _read("SPECIAL_QQ")) if q}
    if name == "PEER_BOTS":
        return env.get_set("AI_CHAT_PEER_BOTS")
    if name == "BOT_ALIASES":
        return tuple(roles.aliases())
    if name == "AI_CHAT_GROUPS":
        return env.get_set("AI_CHAT_GROUPS", env.get("MIYOUSHE_GROUPS"))
    raise AttributeError(name)


def _runtime_bot_qq() -> str:
    try:
        from nonebot import get_bots

        for bot_id in get_bots():
            return str(bot_id)
    except Exception:
        pass
    return ""


def get_config_version() -> int:
    return env.version()


def get_env_val(key: str, default: str = "") -> str:
    return env.get(key, default)


def get_daily_chat_limit() -> int:
    return _read("DAILY_CHAT_LIMIT_PER_USER")


_OVERRIDE_CACHE: dict = {"version": -1, "map": {}}


def get_limit_overrides() -> dict:
    ver = env.version()
    if _OVERRIDE_CACHE["version"] != ver:
        _OVERRIDE_CACHE.update(version=ver, map=parse_limit_overrides(env.get("AI_CHAT_DAILY_LIMIT_OVERRIDES")))
    return _OVERRIDE_CACHE["map"]


def get_user_chat_limit(group_id, user_id) -> int:
    return effective_limit(get_limit_overrides(), get_daily_chat_limit(), group_id, user_id)


def get_llm_key() -> str:
    return llm.api_key()
