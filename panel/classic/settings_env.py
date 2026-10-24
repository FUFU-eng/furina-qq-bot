"""设置中心里「按 .env 配置项」生成的页面：聊天、上下文、互动、纪要/封神榜、搜图/强化、JM、推送/转发、其他。

每项 = (键名, 名称, 类型, 默认值, 说明, 选项)。类型：
  bool 开关 / int 整数 / dec 小数 / pct 百分比（存 0~1）/ text 文本 / list 逗号分隔的号码 / secret 密码
选项：min、max、step、suffix、decimals、restart（改完要重启机器人才生效）、choices。
只写入改动过的键；.env 里原来没有、也没改的键保持不写（继续用程序默认值）。
"""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from ..services import envfile
from ..services import huoren_settings
from .settings import SettingsPage, ToggleSwitch, _button, _chip, _label, _polish_combo, _warn

R = {"restart": True}


def _o(**kw):
    return kw


CHAT = [
    ("基础", "触发方式与每日额度。", [
        ("AI_CHAT_PROBABILITY", "随机接话概率", "pct", 0.02,
         "没人艾特时，芙芙主动插一句的概率（平时）。这几种情况会提到各自的数，都能改、填 0 = 这种情况不加："
         "说话的人刚和她聊着 / 她刚在群里说过话还留着 / 常聊的人隔很久回来 / 群里有人提问 / 你说话"
         "（在「活人感 → 随机接话会变高的时候」），以及 TA 有近况可问（本页「说话活人感」）。"
         "这里设成 0 = 完全不随机接话，上面那些也都不生效。命中的话日志会写「命中线 N%：原因」。", {}),
        ("AI_CHAT_DAILY_CHAT_LIMIT", "每人每日艾特次数", "int", 30, "群主不受限制；0 = 不限制。", _o(max=9999, suffix="次")),
        ("AI_CHAT_DAILY_LIMIT_OVERRIDES", "个人额度例外", "text", "", "格式：QQ:次数 或 群号:QQ:次数，多个用逗号分隔；次数 0 = 无限。", {}),
        ("AI_CHAT_SUMMARY_COOLDOWN", "群聊总结冷却", "int", 120, "两次「总结一下」之间的最短间隔。", _o(max=86400, suffix="秒")),
        ("AI_CHAT_WELCOME_NEW_MEMBER", "欢迎新成员", "bool", True, "有人进群时芙芙打个招呼。", {}),
    ]),
    ("特别的人", "这两个号码会写进人设提示词。", [
        ("AI_CHAT_GROUP_OWNER_QQ", "群主 QQ", "list", "", "群主艾特不限次数，好感锁定满值。", {}),
        ("AI_CHAT_SPECIAL_QQ", "特别关照的人 QQ", "list", "", "芙芙格外偏爱的人，只认 QQ 号不认昵称。", {}),
    ]),
    ("回复风格", "模型参数与回复长度。", [
        ("AI_CHAT_TEMPERATURE", "温度", "dec", 0.6, "越高越跳脱、越低越稳；建议 0.5~0.9。", _o(max=2, decimals=2)),
        ("AI_CHAT_REPLY_SPLIT_PROBABILITY", "单行随机拆两条的概率", "pct", 0.55, "她只写了一行、又不算太长时，随机拆成两条发的概率（多行本来就一行一条）。实际会按这行的长短浮动："
         "不到 16 字从不拆；16~40 字按这个数的六成；41~60 字就是这个数；更长的按 1.4 倍（最多 90%）。", {}),
        ("AI_CHAT_MAX_TOKENS", "最大输出 tokens", "int", 512, "模型单次最多生成的长度。", _o(min=16, max=8192)),
        ("AI_CHAT_LLM_TIMEOUT", "模型超时", "int", 40, "单次调用最长等待时间。", _o(min=5, max=300, suffix="秒")),
    ]),
    ("发送节奏与防风控", "像真人一样先「打字」再发、等对方说完再回；限速防止刷屏被风控（参考小鲸鱼教程的防风控章节）。", [
        ("AI_CHAT_MAX_SEND_PER_MINUTE", "每群每分钟最多发", "int", 6, "防风控（教程作者 20/300 被限过，推荐 6）：超过就不发（她会知道那几句没发出去）；0 = 不限制。", _o(max=120, suffix="条")),
        ("AI_CHAT_MAX_SEND_PER_HOUR", "全部群每小时最多发", "int", 80, "推荐 80；超过就不发；0 = 不限制。", _o(max=5000, suffix="条")),
        ("AI_CHAT_HUMANIZE", "模拟打字延迟", "bool", True, "先按字数「打字」再发：1.2 秒 + 每字的打字时间，最多 6 秒，±20% 抖动，凌晨更慢；模型思考已经花掉的时间会扣掉。消息瞬间完整到达最像机器人。", {}),
        ("AI_CHAT_TYPING_MS_PER_CHAR", "每字打字时间", "int", 110, "仅在打开「模拟打字延迟」时有效。", _o(min=10, max=1000, suffix="毫秒")),
        ("AI_CHAT_LONG_GAP_PROBABILITY", "连发时偶尔愣一下", "pct", 0.3, "连发几条时，每条之间有这么大概率多停 4~12 秒，像被什么打断了。", {}),
        ("AI_CHAT_MERGE_SECONDS", "等对方说完", "dec", 6.0, "被艾特后，对方安静这么多秒才算说完、一起回；只发「在吗」会快一点回；0 = 关闭。", _o(max=30, decimals=1, suffix="秒")),
        ("AI_CHAT_MERGE_MAX_SECONDS", "最长等待", "dec", 20.0, "对方一直在连发时最多等这么久。", _o(min=1, max=120, decimals=1, suffix="秒")),
        ("AI_CHAT_UNFINISHED_WAIT_SECONDS", "话没说完时最多等", "dec", 90.0, "对方的话明显没说完（「然后」「就是」「等下」、逗号结尾）时安静 12 秒才算完，最多等这么久；等满了还没说完，她会像真人那样回「啥」「然后呢」。", _o(min=10, max=300, decimals=0, suffix="秒")),
    ]),
    ("和其他机器人互聊", "群里别的 AI 机器人艾特芙芙时，聊几轮就自然收尾，之后不再艾特、不再接话。", [
        ("AI_CHAT_PEER_END", "自然收尾", "bool", True, "关闭后和机器人互相艾特不限轮数（旧行为）。", {}),
        ("AI_CHAT_PEER_BOTS", "其他机器人 QQ", "list", "", "哪些 QQ 是机器人：多个用逗号分隔，可带备注，如 200000005(昔涟), 123456789(小派蒙)。改完立即生效，不用重启；面板里登记的本项目机器人号自动算在内。", {}),
        ("AI_CHAT_PEER_MAX_ROUNDS", "第几轮收尾", "int", 3, "前面正常聊，第 2 轮起回复变短、不反问，到这一轮收尾且不艾特。", _o(min=1, max=20, suffix="轮")),
        ("AI_CHAT_PEER_COOLDOWN", "收尾后安静多久", "int", 900, "这段时间里对方再艾特也不回、不随机接 TA 的话。", _o(min=0, max=86400, suffix="秒")),
        ("AI_CHAT_PEER_RESET_SECONDS", "隔多久算新话题", "int", 300, "两轮之间隔得比这久，重新从第 1 轮算。", _o(min=30, max=86400, suffix="秒")),
    ]),
    ("说话活人感", "回复长短随机变化、点名最近的口头禅；记住群友提过的近况，过后顺口关心。", [
        ("AI_CHAT_STYLE_VARIETY", "说话方式随机变化", "bool", True, "每次随机定回复长短（有时只回几个字），并提醒她避开最近老用的开头、语气词和自称。", {}),
        ("AI_CHAT_LIFE_EVENTS", "记住群友近况", "bool", True, "群友说「明天考试」「周五面试」「我感冒了」时记下来，事情过去后找机会问一句结果；每件事只问一次。", {}),
        ("AI_CHAT_LIFE_FOLLOWUP_PROBABILITY", "有近况可问时接话概率", "pct", 0.5, "TA 有事情已经过去、还没问过时，TA 下次说话（没艾特）芙芙接话的概率。", {}),
    ]),
    ("冷场主动开话题", "群里安静太久时芙芙像群友一样主动冒个泡。各群还要在「功能权限」里打开「冷场主动开话题」（默认关）。", [
        ("AI_CHAT_PROACTIVE", "总开关", "bool", True, "关掉后所有群都不主动开话题。", {}),
        ("AI_CHAT_PROACTIVE_IDLE_MINUTES", "安静多久后开口", "int", 60, "最后一条消息之后过了这么久才考虑冒泡（到点后还会随机晚几分钟）。", _o(min=10, max=1440, suffix="分钟")),
        ("AI_CHAT_PROACTIVE_MAX_IDLE_HOURS", "安静太久就不开口", "int", 6, "超过这么久没人说话，说明大家都不在，就不去自言自语了。", _o(min=1, max=72, suffix="小时")),
        ("AI_CHAT_PROACTIVE_HOURS", "活跃时段", "text", "10-23", "只在这个时段里开话题，格式：开始点-结束点，如 10-23。", {}),
        ("AI_CHAT_PROACTIVE_DAILY_MAX", "每群每天最多", "int", 5, "0 = 今天不再主动说。", _o(min=0, max=50, suffix="次")),
        ("AI_CHAT_PROACTIVE_GAP_MINUTES", "两次至少隔", "int", 90, "两次主动冒泡之间的最短间隔。", _o(min=10, max=1440, suffix="分钟")),
        ("AI_CHAT_PROACTIVE_MIN_MESSAGES", "群最近要有多活跃", "int", 10, "最近 24 小时群友至少说过这么多条，才会去开话题。", _o(min=0, max=500, suffix="条")),
    ]),
]

CONTEXT = [
    ("记忆长度", "芙芙能记住、能看到多少群聊记录。", [
        ("AI_CHAT_HISTORY_LIMIT", "每群保留条数", "int", 60, "内存里每个群保留的最近消息数。", _o(min=10, max=1000, suffix="条")),
        ("AI_CHAT_HISTORY_SEED", "启动时加载条数", "int", 200, "机器人连上 QQ 时从群历史里拉取的条数。", _o(max=1000, suffix="条", restart=True)),
        ("AI_CHAT_TRANSCRIPT_LIMIT", "每次给模型看的条数", "int", 30, "带编号的群聊记录行数，最多给这么多条。为了让模型的缓存能命中（省钱），窗口是「攒着往后长、满了再往前挪一截」："
         "实际在这个数的六成到全部之间（30 → 18~30 条）；也不会超过上面的「每群保留条数」。", _o(min=5, max=200, suffix="条")),
        ("AI_CHAT_HISTORY_SEND_LIMIT", "旧格式上下文条数", "int", 20, "关闭「结构化上下文」时使用。", _o(min=1, max=200, suffix="条")),
        ("AI_CHAT_HISTORY_SEND_CHARS", "群友每条最多字数", "int", 100, "记录里每条群友消息截取的长度。", _o(min=20, max=2000, suffix="字")),
        ("AI_CHAT_HISTORY_SEND_CHARS_SELF", "自己每条最多字数", "int", 200, "记录里芙芙自己说过的话截取的长度。", _o(min=20, max=2000, suffix="字")),
        ("AI_CHAT_HISTORY_GAP_SECONDS", "新话题间隔", "int", 180, "两条消息相隔超过这么久，标注「过了 N 分钟」。", _o(min=30, max=86400, suffix="秒")),
    ]),
    ("理解对话", "防止问东答西、张冠李戴。", [
        ("AI_CHAT_STRUCTURED_CONTEXT", "结构化上下文", "bool", True, "记录带编号、时间、说话人和引用关系。", {}),
        ("AI_CHAT_PLANNER", "先判断再回复", "bool", True, "回复前先理清在接谁的话；随机接话时可判断不插嘴。", {}),
        ("AI_CHAT_PLANNER_TIMEOUT", "判断步骤超时", "int", 8, "超时就跳过判断直接回复。", _o(min=2, max=60, suffix="秒")),
    ]),
    ("引用与转发", "", [
        ("AI_CHAT_QUOTE_MAX_CHARS", "引用内容最多字数", "int", 1500, "被引用的消息/合并转发读取的长度。", _o(min=100, max=20000, suffix="字")),
        ("AI_CHAT_FORWARD_MAX_NODES", "合并转发最多读取", "int", 30, "", _o(min=1, max=500, suffix="条")),
        ("AI_CHAT_RECENT_IMAGE_SECONDS", "「刚才那张图」有效期", "int", 300, "群友问刚才的图时，往前找多久内的图片。没明说是图、只是一句很短的猜测（如「三月七？」「流萤吗」）时，只往前看 2 分钟（这里设得更短就按这里）。", _o(min=30, max=86400, suffix="秒")),
    ]),
]

INTERACT = [
    ("戳一戳", "", [
        ("AI_CHAT_POKE_REPLY", "被戳时回应", "bool", True, "", {}),
        ("AI_CHAT_POKE_PROBABILITY", "回戳概率", "pct", 0.15, "回复后顺手戳回去的概率。", {}),
        ("AI_CHAT_POKE_COOLDOWN", "戳一戳冷却", "int", 20, "", _o(max=3600, suffix="秒")),
        ("AI_CHAT_DAILY_POKE_LIMIT", "每人每日回应次数", "int", 5, "0 = 不限制。", _o(max=999, suffix="次")),
    ]),
    ("表情包", "", [
        ("AI_CHAT_STICKER_PROBABILITY", "附带表情包概率", "pct", 0.3,
         "回复里附带一张收藏表情的比例：她自己挑的、她没挑时随机配的，加起来不超过这个数。"
         "下面「只回表情包」那种（整条只发一张图）另算。", {}),
        ("AI_CHAT_STICKER_MAX_COUNT", "收藏表情读取上限", "int", 60, "", _o(min=1, max=500, suffix="张")),
        ("AI_CHAT_STICKER_TTL", "表情列表缓存", "int", 600, "多久重新读取一次收藏表情。", _o(min=30, max=86400, suffix="秒")),
        ("AI_CHAT_STICKER_ONLY_PROBABILITY", "只回表情包的概率", "pct", 0.25, "对方只说「哈哈」「好」「6」这类很短的话时，不打字只回一张收藏表情的概率。", {}),
    ]),
    ("表情回应（贴表情）", "像真人一样给群消息点个赞、贴个笑哭。", [
        ("AI_CHAT_REACTION", "开启表情回应", "bool", True, "", {}),
        ("AI_CHAT_REACTION_PROBABILITY", "顺手贴表情概率", "pct", 0.06, "没艾特她的群消息里，内容合适（好笑、恭喜、晚安…）时顺手贴一个的概率。", {}),
        ("AI_CHAT_REACTION_INSTEAD_PROBABILITY", "贴表情代替回复", "pct", 0.5, "艾特她只说「谢谢」「好的」「晚安」「哈哈」时，只贴表情不回文字的概率。", {}),
        ("AI_CHAT_REACTION_COOLDOWN", "同群冷却", "int", 180, "同一个群两次顺手贴表情的最短间隔。", _o(min=0, max=86400, suffix="秒")),
    ]),
    ("看图", "", [
        ("AI_CHAT_VISION", "让芙芙看图", "bool", True, "被艾特且带图时把图片发给模型。", {}),
        ("AI_CHAT_VISION_MAX_IMAGES", "每次最多看几张", "int", 2, "", _o(min=1, max=10, suffix="张")),
        ("AI_CHAT_IMAGE_DESC", "后台描述群图片", "bool", False, "把群里的图片转成文字写进上下文（会额外消耗额度）。", {}),
        ("AI_CHAT_IMAGE_DESC_MAX_PER_HOUR", "每小时最多描述", "int", 15, "", _o(max=500, suffix="张")),
    ]),
    ("好感与心情", "", [
        ("AI_CHAT_FAVOR_ENABLED", "好感度系统", "bool", True, "常来聊天/夸奖会涨，说坏话会掉。", {}),
        ("AI_CHAT_EMOTION_ENABLED", "心情系统", "bool", True, "芙芙有自己的心情。默认每轮聊完后台判一次（「设置 → 活人感 → 鱼的心情」可调，可切回关键词加减）。", {}),
        ("AI_CHAT_EMOTION_START_MOOD", "初始心情", "int", 60, "0~100；只在第一次没有心情数据时用。", _o(max=100)),
    ]),
]

DIGEST = [
    ("群聊纪要", "每天定时把当天聊天整理成纪要。", [
        ("AI_CHAT_DIGEST_ENABLED", "启用群聊纪要", "bool", True, "", {}),
        ("AI_CHAT_DIGEST_HOUR", "生成时间（时）", "int", 23, "", _o(max=23, suffix="时")),
        ("AI_CHAT_DIGEST_MINUTE", "生成时间（分）", "int", 50, "", _o(max=59, suffix="分")),
        ("AI_CHAT_DIGEST_MAX_LINES", "每次最多整理", "int", 200, "", _o(min=20, max=5000, suffix="条")),
        ("AI_CHAT_DIGEST_KEEP_DAYS", "纪要保留天数", "int", 30, "", _o(min=1, max=3650, suffix="天")),
    ]),
    ("封神榜", "按周统计群里最活跃的人。改完需要重启机器人。", [
        ("FENGSHEN_ENABLED", "启用封神榜", "bool", True, "", R),
        ("FENGSHEN_GROUPS", "发送的群", "list", "", "群号，逗号分隔。", R),
        ("FENGSHEN_WEEKDAYS", "星期几发送", "list", "2,6", "1=周一 … 7=周日，逗号分隔。", R),
        ("FENGSHEN_HOUR", "发送时间（时）", "int", 21, "", _o(max=23, suffix="时", restart=True)),
        ("FENGSHEN_MINUTE", "发送时间（分）", "int", 30, "", _o(max=59, suffix="分", restart=True)),
        ("FENGSHEN_TOP_N", "上榜人数", "int", 5, "", _o(min=1, max=50, suffix="人", restart=True)),
    ]),
]

SEARCH = [
    ("以图搜图", "改完需要重启机器人。", [
        ("PROXY_URL", "代理地址", "text", "http://127.0.0.1:7890", "访问 Google / Yandex 等站点用的代理。", R),
        ("SEARCH_COOLDOWN", "搜图冷却", "int", 30, "同一群两次搜图的最短间隔。", _o(max=3600, suffix="秒", restart=True)),
        ("SEARCH_TIMEOUT", "单次搜图总超时", "int", 300, "", _o(min=30, max=3600, suffix="秒", restart=True)),
        ("SEARCH_STOP_VISIT_SECONDS", "停止访问来源站点", "int", 230, "超过这个时间不再打开新的来源网页。", _o(min=10, max=3600, suffix="秒", restart=True)),
        ("SITE_VISIT_SECONDS", "单个站点访问时长", "int", 90, "", _o(min=10, max=3600, suffix="秒", restart=True)),
        ("SEARCH_YANDEX_BUDGET_SECONDS", "Yandex 时间预算", "int", 100, "", _o(min=10, max=3600, suffix="秒", restart=True)),
        ("PAGE_LOAD_TIMEOUT", "网页加载超时", "int", 60, "", _o(min=5, max=600, suffix="秒", restart=True)),
        ("YANDEX_FALLBACK", "Google 没结果时用 Yandex", "bool", True, "", R),
        ("YANDEX_MAX_PAGES", "Yandex 最多翻页", "int", 4, "", _o(min=1, max=20, suffix="页", restart=True)),
        ("VISION_SIM_THRESHOLD", "相似度门槛", "int", 55, "AI 比对低于这个分数的候选不算命中。", _o(max=100, suffix="分", restart=True)),
        ("VISION_ORIGINAL_SIM_THRESHOLD", "原图判定门槛", "int", 90, "高于这个分数视为同一张原图。", _o(max=100, suffix="分", restart=True)),
        ("VISION_MAX_CANDIDATES", "最多比对候选", "int", 5, "", _o(min=1, max=30, suffix="张", restart=True)),
        ("EAGLE_LIBRARY_DIR", "Eagle 图库目录", "text", "", "「指令发图」从这里随机取图。", {}),
    ]),
    ("图片强化", "Real-ESRGAN 超分参数。改完需要重启机器人。", [
        ("UPSCALE_MODEL", "模型", "choice", "realesr-animevideov3", "动漫图推荐 animevideov3。",
         _o(restart=True, choices=["realesr-animevideov3", "realesrgan-x4plus-anime", "realesrgan-x4plus"])),
        ("UPSCALE_SCALE", "放大倍数", "int", 2, "", _o(min=2, max=4, suffix="倍", restart=True)),
        ("UPSCALE_GPU_ID", "使用的显卡编号", "int", 1, "多显卡时选择用哪块（从 0 开始）。", _o(max=8, restart=True)),
        ("UPSCALE_TILE", "分块大小", "int", 0, "显存不够时调小（如 200）；0 = 自动。", _o(max=2048, restart=True)),
        ("UPSCALE_TIMEOUT", "单张超时", "int", 300, "", _o(min=30, max=3600, suffix="秒", restart=True)),
        ("MAIN_GROUP_ID", "强化图转发：来源群", "list", "", "这个群里强化完的图，同时转发到下面的群；留空 = 不转发。", R),
        ("SUB_GROUP_ID", "强化图转发：目标群", "list", "", "", R),
    ]),
    ("筛图收集", "收哪些群在「功能权限 → 收图筛选」里按群开关。标「需重启」的项要重启机器人后生效，其余实时生效。", [
        ("PIC_INBOX_ENABLED", "启用筛图收集", "bool", True, "总开关：关掉后不收图、不强化、手机端也连不上。", R),
        ("PIC_INBOX_FROM_RANK", "Pixiv 定时榜单的图也收", "bool", True, "", {}),
        ("PIC_INBOX_FROM_SEARCH", "搜图找到的原图也收", "bool", True, "", {}),
        ("PIC_INBOX_FROM_PIXIV", "Pixiv 点图发的图也收", "bool", True, "", {}),
        ("PIC_INBOX_MIN_SIDE", "短边小于这个不收", "int", 360, "挡掉表情包和小图；动图、GIF、标成表情的图一律不收。", _o(max=4000, suffix="像素")),
        ("PIC_INBOX_ENHANCE_UNDER_KB", "小于多大的图自动强化", "int", 1024, "用上面「图片强化」的模型放大；0 = 不强化。", _o(max=102400, suffix="KB")),
        ("PIC_INBOX_IDLE_GPU", "显卡占用低于才强化", "int", 30, "打游戏、跑 AI 时显卡忙，就先不强化。", _o(min=1, max=100, suffix="%")),
        ("PIC_INBOX_IDLE_RAM_MB", "剩余内存高于才强化", "int", 3000, "", _o(max=65536, suffix="MB")),
        ("PIC_INBOX_EXPORT_DIR", "留下的图放到", "text", r"D:\Pictures\Eagle", "填 Eagle 设置里的「自动导入」文件夹。Eagle 会在里面按图库名再建一层子文件夹、只认那一层，会自动放进去。", {}),
        ("PIC_INBOX_DIR", "待筛的图暂存在", "text", r"D:\FurinaPicInbox", "丢掉的图 5 分钟内可撤销，之后直接删除。", {}),
        ("PIC_INBOX_PORT", "手机接口端口", "int", 52190, "只在本机监听，由隧道转到外网。", _o(min=1024, max=65535, restart=True)),
        ("PIC_INBOX_TUNNEL", "自动拉起外网隧道", "bool", True, "手机不在家也能筛图（Cloudflare 隧道，只转筛图接口）。", R),
        ("PIC_INBOX_PUBLIC_URL", "手机连接地址", "text", "", "手机 App 里填这个地址。", {}),
        ("PIC_INBOX_REQUIRE_PASSWORD", "手机连接要密码", "bool", False, "默认不要密码，打开地址就能筛；打开后手机端要填「筛图 → 手机连接」里的密码。", {}),
    ]),
]

JM = [
    ("JM 下载", "保存后实时生效。", [
        ("JM_ENABLED", "启用 JM 下载", "bool", True, "关闭后 /jm 一律不响应。", {}),
        ("JM_PROXY", "下载代理", "text", "", "留空 = 跟随系统代理。", {}),
        ("JM_TIMEOUT", "单本下载超时", "int", 900, "整本下载 + 合成 PDF 的总时长。", _o(min=60, max=7200, suffix="秒")),
        ("JM_MAX_IMAGES", "单本页数上限", "int", 0, "超过直接拒绝；0 = 不限制。", _o(max=5000, suffix="页")),
        ("JM_DAILY_USER_LIMIT", "每人每日次数", "int", 5, "0 = 不限制。", _o(max=999, suffix="次")),
        ("JM_DAILY_GROUP_LIMIT", "每群每日次数", "int", 20, "0 = 不限制。", _o(max=9999, suffix="次")),
        ("JM_PROGRESS_INTERVAL", "进度提示间隔", "int", 45, "", _o(min=5, max=3600, suffix="秒")),
        ("JM_CACHE_MAX_MB", "缓存容量上限", "int", 20480, "0 = 不限制。", _o(max=1048576, suffix="MB")),
        ("JM_CACHE_DIR", "缓存目录", "text", "", "留空 = data\\jm。", {}),
    ]),
]

PUSH = [
    ("米游社官号推送", "改完需要重启机器人。", [
        ("MIYOUSHE_GROUPS", "推送的群", "list", "", "群号，逗号分隔。", R),
        ("MIYOUSHE_FORUMS", "关注的版块", "list", "28,53,58,6,947,949", "米游社官号版块 ID，逗号分隔。", R),
        ("MIYOUSHE_POLL_INTERVAL", "检查间隔", "int", 60, "", _o(min=20, max=86400, suffix="秒", restart=True)),
        ("MIYOUSHE_PAGE_SIZE", "每次拉取条数", "int", 30, "", _o(min=5, max=100, suffix="条", restart=True)),
        ("MIYOUSHE_BACKFILL", "启动时补推旧帖", "bool", False, "", R),
    ]),
    ("图片转发", "把一个群里的图片转发到另一个群。改完需要重启机器人。", [
        ("RELAY_ENABLED", "启用图片转发", "bool", True, "", R),
        ("RELAY_SOURCE_GROUP", "来源群", "list", "", "", R),
        ("RELAY_TARGET_GROUP", "目标群", "list", "", "", R),
        ("RELAY_OWNER_QQ", "可以开启转发的人", "list", "", "", R),
        ("RELAY_ARM_TRIGGER", "开始转发口令", "text", "1", "", R),
        ("RELAY_STOP_TRIGGERS", "停止转发口令", "text", "发完了,不发了,结束,停止", "逗号分隔。", R),
        ("RELAY_ARM_TIMEOUT", "无新图自动停止", "int", 300, "", _o(min=10, max=86400, suffix="秒", restart=True)),
        ("RELAY_AUTO_USERS", "自动转发的群友", "list", "", "这些 QQ 在来源群发的图自动转发，不用口令。", R),
    ]),
    ("群成员同步", "", [
        ("AI_CHAT_GROUPS", "同步成员的群", "list", "", "留空 = 使用「推送的群」。", R),
        ("MEMBER_SYNC_INTERVAL", "同步间隔", "int", 86400, "", _o(min=600, max=604800, suffix="秒", restart=True)),
    ]),
]

SCHEMA_PAGES = [
    ("chat", "聊天回复", "触发、额度、风格、节奏", "芙芙怎么回、回多少、多快回。保存后实时生效，不用重启。", CHAT),
    ("context", "聊天上下文", "记忆长度与理解对话", "芙芙能看到多少聊天记录、怎么理解谁在跟谁说话。保存后实时生效。", CONTEXT),
    ("interact", "互动与表情", "戳一戳、表情、看图、好感", "保存后实时生效。", INTERACT),
    ("digest", "纪要与封神榜", "每日纪要、每周封神榜", "定时任务。标「需重启」的项要重启机器人后生效。", DIGEST),
    ("search", "搜图与强化", "以图搜图、超分参数", "以图搜图和图片强化的参数。大多要重启机器人后生效。", SEARCH),
    ("jm", "JM 下载", "代理、额度、缓存", "保存后实时生效。", JM),
    ("push", "推送与转发", "米游社推送、图片转发", "改完需要重启机器人后生效。", PUSH),
    # 活人感 / 语音 / B站生活 / 联网搜索：每组单独一页，字段与网页面板共用 panel/services/huoren_settings.py
    *huoren_settings.classic_pages(),
]

# 其它页已经管理的键（账号页 / 模型页），「其他配置项」里不再重复列出
MANAGED_ELSEWHERE = {"BOT_QQ", "AI_CHAT_LLM_API_KEY", "AI_CHAT_LLM_BASE_URL", "AI_CHAT_LLM_MODEL", "AI_CHAT_LLM_MODELS"}
# 改错会直接连不上 / 找不到程序的底层项：只读显示
READ_ONLY = {"HOST", "PORT", "NAPCAT_DIR", "ASSETS_ROOT", "EDGE_USER_DATA_DIR", "PYTHONIOENCODING", "PYTHONUTF8"}
_SECRET_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|COOKIE", re.I)


def schema_keys() -> set:
    return {f[0] for *_x, cards in SCHEMA_PAGES for _t, _d, fields in cards for f in fields}


class DecimalSpin(QSpinBox):
    """显示小数的整数步进框（继承 QSpinBox，沿用设置中心的输入框样式）。"""

    def __init__(self, decimals=1, low=0.0, high=10.0):
        super().__init__()
        self._dec = decimals
        self._scale = 10 ** decimals
        self.setRange(int(round(low * self._scale)), int(round(high * self._scale)))

    def textFromValue(self, value):
        return f"{value / self._scale:.{self._dec}f}"

    def valueFromText(self, text):
        try:
            return int(round(float(text.replace(self.suffix(), "").strip() or 0) * self._scale))
        except ValueError:
            return self.value()

    def validate(self, text, pos):
        body = text.replace(self.suffix(), "").strip()
        if body in ("", ".", "-"):
            return QValidator.Intermediate, text, pos
        try:
            float(body)
            return QValidator.Acceptable, text, pos
        except ValueError:
            return QValidator.Invalid, text, pos

    def real(self):
        return self.value() / self._scale

    def set_real(self, v):
        self.setValue(int(round(float(v) * self._scale)))


def _fmt_num(v) -> str:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s or "0"


class _Field:
    """一个配置项：负责建控件、读值、写值。"""

    def __init__(self, key, label, kind, default, help_text, opts):
        self.key, self.label, self.kind, self.default, self.help, self.opts = key, label, kind, default, help_text, opts or {}
        self.restart = bool(self.opts.get("restart"))
        self.loaded = None  # 读入时 .env 的原始文本（None = .env 里没有这一项）
        self.widget = self._make()

    def _make(self):
        k, o = self.kind, self.opts
        suffix = (" " + o["suffix"]) if o.get("suffix") else ""
        if k == "bool":
            return ToggleSwitch()
        if k == "int":
            w = QSpinBox()
            w.setRange(int(o.get("min", 0)), int(o.get("max", 999999)))
            w.setSuffix(suffix)
            w.setAccelerated(True)
            w.setFixedWidth(170)
            return w
        if k == "dec":
            w = DecimalSpin(int(o.get("decimals", 1)), float(o.get("min", 0)), float(o.get("max", 100)))
            w.setSuffix(suffix)
            w.setFixedWidth(170)
            return w
        if k == "pct":
            w = DecimalSpin(1, 0, 100)
            w.setSuffix(" %")
            w.setFixedWidth(170)
            return w
        if k == "choice":
            w = QComboBox()
            w.setEditable(True)
            w.addItems(o.get("choices") or [])
            w.setFixedWidth(260)
            _polish_combo(w)
            return w
        w = QLineEdit()
        w.setMinimumWidth(320)
        if k == "secret":
            w.setEchoMode(QLineEdit.Password)
        if k == "list":
            w.setPlaceholderText("多个用英文或中文逗号分隔")
        return w

    def connect(self, slot):
        w = self.widget
        if isinstance(w, ToggleSwitch):
            w.toggled.connect(slot)
        elif isinstance(w, QSpinBox):
            w.valueChanged.connect(slot)
        elif isinstance(w, QComboBox):
            w.currentTextChanged.connect(slot)
        else:
            w.textChanged.connect(slot)

    def set_text(self, raw):
        """把 .env 文本（或默认值）放进控件。"""
        w, k = self.widget, self.kind
        val = self.default if raw is None or str(raw).strip() == "" else raw
        try:
            if k == "bool":
                w.setChecked(val if isinstance(val, bool) else str(val).strip().lower() in ("1", "true", "yes", "on"))
            elif k == "int":
                w.setValue(int(float(val)))
            elif k == "dec":
                w.set_real(float(val))
            elif k == "pct":
                w.set_real(float(val) * 100)
            elif k == "choice":
                w.setCurrentText(str(val))
            else:
                w.setText(str(val))
        except (TypeError, ValueError):
            self.set_text(None) if raw is not None else None

    def text(self) -> str:
        w, k = self.widget, self.kind
        if k == "bool":
            return "true" if w.isChecked() else "false"
        if k == "int":
            return str(w.value())
        if k == "dec":
            return _fmt_num(w.real())
        if k == "pct":
            return _fmt_num(w.real() / 100)
        if k == "choice":
            return w.currentText().strip()
        txt = w.text().strip()
        if k == "list":
            txt = ",".join(p.strip() for p in txt.replace("，", ",").split(",") if p.strip())
        return txt

    def default_text(self) -> str:
        """默认值按控件的显示精度格式化，和 text() 的输出可直接比较。"""
        k, d = self.kind, self.default
        if k == "bool":
            return "true" if d else "false"
        if k == "int":
            return str(int(d))
        if k == "dec":
            return _fmt_num(round(float(d), int(self.opts.get("decimals", 1))))
        if k == "pct":
            return _fmt_num(round(float(d) * 100, 1) / 100)
        if k == "list":
            return ",".join(p.strip() for p in str(d).replace("，", ",").split(",") if p.strip())
        return str(d).strip()

    def validate(self):
        if self.kind == "list":
            bad = [p for p in self.text().split(",") if p and not p.isdigit()]
            if bad and self.key not in ("FENGSHEN_WEEKDAYS",):
                raise ValueError(f"「{self.label}」只能填数字，用逗号分隔（有问题的：{'、'.join(bad[:3])}）")
            if self.key == "FENGSHEN_WEEKDAYS" and any(not p.isdigit() or not 1 <= int(p) <= 7 for p in self.text().split(",") if p):
                raise ValueError("「星期几发送」只能填 1~7")


class EnvPage(SettingsPage):
    """按上面的清单自动生成的设置页。"""

    footer_hint = "保存后写入 .env"
    _cards: list = []

    def build(self):
        self.fields: list[_Field] = []
        for card_title, card_desc, fields in self._cards:
            lay = self.card(card_title, card_desc or None)
            for spec in fields:
                f = _Field(*spec)
                self.fields.append(f)
                self._row(lay, f)
                f.connect(self.mark_dirty)

    def _row(self, layout, f):
        row = QHBoxLayout()
        row.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_label(f.label, "fieldLabel"))
        if f.restart:
            tag = _chip("需重启", "warn")
            tag.setToolTip("改完要重启机器人才生效")
            head.addWidget(tag)
        head.addStretch(1)
        texts.addLayout(head)
        if f.help:
            texts.addWidget(_label(f.help, "help", wrap=True))
        key = QLabel(f.key)
        key.setObjectName("help")
        key.setStyleSheet("color: #4f5f78; font-size: 11px;")
        key.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addWidget(key)
        row.addLayout(texts, 1)
        row.addWidget(f.widget, 0, Qt.AlignVCenter | Qt.AlignRight)
        layout.addLayout(row)

    def load(self):
        values = envfile.values()
        for f in self.fields:
            f.loaded = values.get(f.key)
            f.set_text(f.loaded)
        n = sum(1 for f in self.fields if f.loaded not in (None, ""))
        self.set_chip(f"{len(self.fields)} 项", "info")
        self.footer_hint = f"共 {len(self.fields)} 项，其中 {n} 项在 .env 里有设置，其余用默认值"

    def changed(self) -> dict:
        out = {}
        for f in self.fields:
            new = f.text()
            old = f.loaded if f.loaded not in (None, "") else f.default_text()
            if new != old:
                out[f.key] = new
        return out

    def save(self):
        for f in self.fields:
            f.validate()
        updates = self.changed()
        if not updates:
            return "没有改动"
        envfile.set_values(updates)
        need_restart = [f.label for f in self.fields if f.key in updates and f.restart]
        self.log("success", f"[设置] 已保存 {len(updates)} 项：{'、'.join(updates)}")
        for f in self.fields:
            if f.key in updates:
                f.loaded = updates[f.key]
        if need_restart:
            self._offer_restart(need_restart)
            return f"已保存 {len(updates)} 项；「{'、'.join(need_restart[:3])}」等要重启机器人后生效"
        return f"已保存 {len(updates)} 项，实时生效"

    def _offer_restart(self, names):
        restart = getattr(self.panel, "restart_bot", None)
        if not callable(restart) or not getattr(self.panel, "bot_running", False):
            return
        from .settings import _ask
        if _ask(self, "需要重启机器人", f"「{'、'.join(names[:4])}」要重启机器人后才生效。\n现在重启吗？（保留 QQ 登录，不用重新扫码）"):
            restart(False)


class OtherEnvPage(SettingsPage):
    """.env 里其余没有专门页面的配置项：逐项显示，底层项只读。"""

    key = "other"
    title = "其他配置项"
    desc = ".env 里其余的配置。这些是底层设置，改错可能导致机器人连不上；不确定就别动。灰色的项只能查看。"
    footer_hint = "保存后写入 .env；大部分要重启机器人后生效"

    def build(self):
        self.fields: dict[str, QLineEdit] = {}
        self.loaded: dict[str, str] = {}
        self.list_card = self.card("配置项", "键名 = 值。密码类的值默认隐藏。")
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.list_card.addLayout(self.rows)
        add = self.card("新增配置项", "填写键名和值后保存，会追加到 .env 末尾。")
        line = QHBoxLayout()
        line.setSpacing(10)
        self.new_key = QLineEdit()
        self.new_key.setPlaceholderText("键名，例如 SOME_OPTION")
        self.new_val = QLineEdit()
        self.new_val.setPlaceholderText("值")
        line.addWidget(self.new_key, 1)
        line.addWidget(self.new_val, 2)
        add.addLayout(line)
        self.new_key.textChanged.connect(self.mark_dirty)
        self.new_val.textChanged.connect(self.mark_dirty)

    def _clear_rows(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def load(self):
        self._clear_rows()
        self.fields.clear()
        values = envfile.values()
        skip = schema_keys() | MANAGED_ELSEWHERE
        keys = [k for k in values if k not in skip]
        self.loaded = {k: values[k] for k in keys}
        for k in keys:
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(12)
            name = _label(k, "fieldLabel")
            name.setMinimumWidth(280)
            name.setTextInteractionFlags(Qt.TextSelectableByMouse)
            hl.addWidget(name)
            edit = QLineEdit(values[k])
            if _SECRET_RE.search(k):
                edit.setEchoMode(QLineEdit.Password)
            if k in READ_ONLY:
                edit.setReadOnly(True)
                edit.setStyleSheet("color: #6f809c;")
                edit.setToolTip("底层路径/端口，改错会导致机器人连不上，这里只读")
            else:
                edit.textChanged.connect(self.mark_dirty)
            hl.addWidget(edit, 1)
            if edit.echoMode() == QLineEdit.Password:
                eye = _button("显示")
                eye.setFixedWidth(64)
                eye.clicked.connect(lambda _=False, e=edit, b=eye: (
                    e.setEchoMode(QLineEdit.Normal if e.echoMode() == QLineEdit.Password else QLineEdit.Password),
                    b.setText("隐藏" if e.echoMode() == QLineEdit.Normal else "显示")))
                hl.addWidget(eye)
            self.rows.addWidget(holder)
            self.fields[k] = edit
        self.new_key.clear()
        self.new_val.clear()
        self.set_chip(f"{len(keys)} 项", "info")

    def save(self):
        updates = {k: e.text().strip() for k, e in self.fields.items()
                   if k not in READ_ONLY and e.text().strip() != self.loaded.get(k, "")}
        nk = self.new_key.text().strip()
        if nk:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nk):
                raise ValueError("键名只能包含英文字母、数字和下划线，且不能以数字开头")
            updates[nk] = self.new_val.text().strip()
        if not updates:
            return "没有改动"
        envfile.set_values(updates)
        self.log("success", f"[设置] 已保存 {len(updates)} 项：{'、'.join(updates)}")
        self.reload()
        return f"已保存 {len(updates)} 项；大部分要重启机器人后生效"


def make_schema_page_class(key, title, desc, cards):
    """SettingsCenter 用 cls(center) 构造页面：给每个清单页生成一个子类。"""
    return type(f"EnvPage_{key}", (EnvPage,), {"key": key, "title": title, "desc": desc, "_cards": cards})


def schema_page_specs():
    """返回 (key, 导航标题, 导航副标题, 页面类)，追加到设置中心的 PAGES。"""
    specs = [(k, t, sub, make_schema_page_class(k, t, d, cards)) for k, t, sub, d, cards in SCHEMA_PAGES]
    specs.append(("other", "其他配置项", ".env 里其余的设置", OtherEnvPage))
    return specs


__all__ = ["EnvPage", "OtherEnvPage", "SCHEMA_PAGES", "schema_keys", "schema_page_specs", "_warn"]
