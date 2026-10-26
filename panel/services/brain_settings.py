"""「模式与内核」页的设置项清单（不依赖 Qt；经典面板 classic/brain_page.py 用它生成页面）。

每项 = (键名, 名称, 类型, 默认值, 说明, 选项)，格式同 classic/settings_env.py。
默认值要和 furina/core/brain.py 里的一致（tests/test_brain.py 会核对）。
"""

MODE_CHOICES = ["二代仿真", "一代仿真", "聊天", "Agent"]
THINK_CHOICES = ["不思考", "低", "高", "最高", "自动"]
R = {"restart": True}


def _o(**kw):
    return kw


BRAIN = [
    ("聊天模式", "她在群里用哪套方式聊天。可以全局设一种，再给个别群单独设。", [
        ("AI_CHAT_MODE", "聊天模式", "choice", "一代仿真",
         "二代仿真（推荐）：像真人刷群，平时潜水，满足下面的「醒来条件」才看一眼群，一次看完新消息再决定说不说，"
         "说话 / 贴表情 / 发表情包 / 等人接话都由她自己决定（用 DeepSeek Harness 内核，需要先装运行时）。"
         "一代仿真：原来的方式，每条消息按概率决定接不接话。聊天：只回艾特和引用，不随机插话、不主动冒泡。"
         "Agent：群里和「聊天」一样；主人在群里艾特她就按 Agent 处理（要打开下面的「允许 Agent 操作电脑」）。",
         _o(choices=MODE_CHOICES)),
        ("AI_CHAT_MODE_GROUPS", "个别群单独设", "text", "",
         "格式：群号=模式，多个用 | 分隔，例如 123456=聊天|654321=二代仿真。没写的群用上面的模式。", {}),
        ("AI_CHAT_SIM2_PROACTIVE", "二代仿真也主动冒泡", "bool", False,
         "二代仿真下是否保留一代的冷场开话题、惦记常聊的人、主动找群主（这些在各自页面里还有单独开关）。"
         "关着 = 二代只靠醒来条件开口。", {}),
    ]),
    ("二代仿真：醒来条件", "每一项都能单独开关。醒来 ≠ 一定说话：随手刷到的话题她觉得不适合插嘴就继续潜水。", [
        ("AI_CHAT_WAKE_AT", "被艾特时醒来", "bool", True, "有人艾特她。关掉后被艾特也不理（只在特殊情况下用）。", {}),
        ("AI_CHAT_WAKE_REPLY", "有人引用她的话时醒来", "bool", True, "有人回复（引用）了她说的话。", {}),
        ("AI_CHAT_WAKE_NAME", "有人叫她名字时醒来", "bool", True, "群里提到她的名字或外号（不用艾特）。", {}),
        ("AI_CHAT_WAKE_FOLLOW_SECONDS", "说完话后跟着看一会儿", "int", 90,
         "她在群里说完话的这段时间内，有人再发消息就醒来看看是不是在接她的话（不一定回）。"
         "这样她不用守着等人接话，聊得更连贯。0 = 关。", _o(max=600, suffix="秒")),
        ("AI_CHAT_WAKE_IDLE_MINUTES", "潜水多久后醒来看看", "int", 30,
         "她这么久没看群 / 没说话后，下一条群消息会让她醒来刷一下。0 = 不按时间醒。", _o(max=1440, suffix="分钟")),
        ("AI_CHAT_WAKE_RANDOM", "随机醒来概率", "pct", 0.05, "每条群消息有这么大概率让她顺手刷一下群。0 = 不随机醒。", {}),
        ("AI_CHAT_WAKE_SELF_ADJUST", "允许她自己调整醒来方式", "bool", True,
         "她觉得自己醒得太勤或太少时，可以自己改「随机醒来概率」和「潜水多久后醒来」（按群分别记，下面能看到、能一键恢复）。"
         "关掉 = 只按这里的设置。", {}),
        ("AI_CHAT_WAKE_SELF_MAX_RANDOM", "她自己最多调到", "pct", 0.2, "她自己调随机醒来概率时的上限。", {}),
    ]),
    ("二代仿真：说话手感", "让她说话更像真人群友。", [
        ("AI_CHAT_SIM2_SCENES", "带上示例场景", "bool", True,
         "每次醒来按群里在聊的内容，挑一两个「这种场合真人一般怎么回」的例子给她参考（只学长短和分寸，不照抄）。", {}),
    ]),
    ("思考强度", "回复前先「想一想」：更聪明、更少答非所问，但更慢、更贵（思考的字数按输出价计费）。"
                 "改完从下一条消息开始生效，不用重启。今天思考花了多少钱见下面「今天的花费」。", [
        ("AI_CHAT_THINKING", "思考强度", "choice", "不思考",
         "不思考：最快最省。低 / 高 / 最高：每次回复都想，越高越慢越贵。"
         "自动：随机插话和主动冒泡不想；被问问题、查资料、看图之后想得深（用下面的档位），被叫到的其他时候想一下（默认低）。一代、二代、Agent 都按这里。",
         _o(choices=THINK_CHOICES)),
        ("AI_CHAT_THINKING_GROUPS", "个别群单独设", "text", "",
         "格式：群号=档位，多个用 | 分隔，例如 123456=自动|654321=不思考。没写的群用上面的档位。", {}),
        ("AI_CHAT_THINKING_AUTO_LEVEL", "「自动」时想多深", "choice", "高", "自动档需要思考时用的档位。", _o(choices=["低", "高", "最高"])),
        ("AI_CHAT_THINKING_AUTO_AT", "「自动」时被叫到也想一下", "choice", "低",
         "有人艾特 / 引用 / 叫她名字、但不像在提问时（比如「唱歌给我听」「你去找张图」）想多深。"
         "低 = 花很少的钱先理清谁在跟谁说话、对方要什么，接话更通顺；不思考 = 和以前一样。",
         _o(choices=["不思考", "低", "高"])),
    ]),
    ("省钱：免费模型", "打开后普通文字请求先走本机的免费模型服务（our-free-model），不行就自动换回付费 DeepSeek，"
                       "免费线路随后歇一会儿再试，不会卡住回复。带图的请求、二代仿真内核仍走付费模型。"
                       "免费模型的请求会经过第三方，别在群里说要保密的事。", [
        ("AI_CHAT_FREE_ENABLED", "先用免费模型", "bool", False, "总开关。关掉 = 全部走付费 DeepSeek。", {}),
        ("AI_CHAT_FREE_MODELS", "免费模型", "text", "deepseek-v4.1-flash-free",
         "按顺序试，逗号分隔。默认是免费版 DeepSeek V4.1 Flash；它被限流时会自动改用付费模型。", {}),
        ("AI_CHAT_FREE_TIMEOUT", "免费模型最多等", "int", 15, "超过就改用付费模型回这一条。", _o(min=5, max=120, suffix="秒")),
        ("AI_CHAT_FREE_COOLDOWN_MIN", "被限流后歇多久", "int", 30,
         "免费线路被限流 / 拒绝后，这段时间内直接走付费模型，不再多等。", _o(min=1, max=1440, suffix="分钟")),
        ("AI_CHAT_FREE_BG_ENABLED", "后台任务也用其他免费模型", "bool", True,
         "记忆整理、心情判断这类群友看不到的后台任务，上面的免费模型用不了时，再试下面这几个免费模型（慢一点，偶尔整理得差一点），"
         "都不行才用付费模型。要先打开「先用免费模型」。", {}),
        ("AI_CHAT_FREE_BG_MODELS", "后台任务的免费模型", "text", "nvidia/nemotron-3-ultra-550b-a55b:free,poolside/laguna-s-2.1:free",
         "按顺序试，逗号分隔。回的格式不对会自动换下一个。", {}),
        ("AI_CHAT_FREE_AUTOSTART", "自动启动免费模型服务", "bool", True, "服务没开时在后台（无窗口）启动它。", {}),
        ("AI_CHAT_FREE_BASE_URL", "免费模型服务地址", "text", "http://127.0.0.1:18900/v1", "一般不用改。", {}),
    ]),
    ("表情包本子", "她的表情包每张都有一句备注，发的时候按备注挑。关掉总开关 = 回到原来的「发 QQ 收藏里的第几张」。", [
        ("AI_CHAT_STICKERBOOK", "表情包本子", "bool", True, "总开关。", {}),
        ("AI_CHAT_STICKER_IMPORT_FAV", "导入 QQ 收藏表情", "bool", True, "每 6 小时把机器人 QQ 号收藏的表情同步进本子（只增不删）。", {}),
        ("AI_CHAT_STICKER_COLLECT", "收藏群友的表情包", "bool", True, "群友发的表情包她真心喜欢时自己收藏，并写一句备注。", {}),
        ("AI_CHAT_STICKER_COLLECT_DAILY_MAX", "每天最多收藏", "int", 5, "0 = 不限制。", _o(max=200, suffix="张")),
        ("AI_CHAT_STICKER_AUTO_NOTE", "没备注的用看图补上", "bool", True, "导入的、没写备注的表情，用看图模型补一句备注（要模型支持看图）。", {}),
        ("AI_CHAT_STICKER_NOTE_PER_HOUR", "看图补备注每小时最多", "int", 12, "控制看图的花费；0 = 不补。", _o(max=500, suffix="张")),
        ("AI_CHAT_STICKER_PROMPT_MAX", "每次给她看的表情数", "int", 40, "一代仿真每轮提示里列出的表情数（常用的优先），越多越费 token。", _o(min=5, max=200, suffix="张")),
    ]),
    ("DSH 内核（二代仿真 / Agent 共用）", "DeepSeek Harness 官方运行时，装在单独的目录里，不影响机器人自己的 Python 环境；"
                                         "用到时才在后台启动（无窗口），空闲一段时间自己退出。", [
        ("AI_CHAT_DSH_MAX_STEPS", "每次醒来最多几步", "int", 8, "一步 = 一次模型请求；到了就收手，防止绕圈烧钱。", _o(min=2, max=40, suffix="步")),
        ("AI_CHAT_DSH_TIMEOUT", "每次醒来最长", "int", 120, "超时就结束这一轮。", _o(min=20, max=900, suffix="秒")),
        ("AI_CHAT_DSH_CONTEXT_MAX_K", "群会话最长多少（省钱）", "int", 60,
         "每次醒来都要把这个群的会话整个发给模型一遍，会话越长越贵。超过这么多（千 token）就换新会话，"
         "带上最近的聊天接着聊；人物记忆、口味、设定不受影响。调大 = 记得更远但更贵；0 = 不限（最贵）。", _o(max=900, suffix="千 token")),
        ("AI_CHAT_DSH_RECAP_LINES", "换新会话时带上最近几条", "int", 30, "也用于机器人重启后第一次醒来。", _o(min=5, max=120, suffix="条")),
        ("AI_CHAT_DSH_MERGE_SECONDS", "被叫到后先等一下", "dec", 4.0, "等对方把话说完再醒来一起看。", _o(max=30, decimals=1, suffix="秒")),
        ("AI_CHAT_DSH_FALLBACK", "内核出错时用一代回复", "bool", True, "被艾特 / 叫到时内核出错或没装好，就按一代仿真回这一条。", {}),
        ("AI_CHAT_DSH_IDLE_EXIT_MIN", "空闲多久自动退出", "int", 30, "内核闲置这么久就退出释放内存，下次用到再启动；0 = 不退出。", _o(max=1440, suffix="分钟")),
        ("AI_CHAT_DSH_MAX_TOKENS", "单次最多输出", "int", 8192, "含思考的字数。", _o(min=1024, max=65536, suffix="tokens")),
        ("AI_CHAT_DSH_MODEL", "内核用的模型", "text", "", "留空 = 和「AI 模型」页一样。", {}),
        ("AI_CHAT_DSH_BASE_URL", "内核接口地址", "text", "",
         "DSH 走 DeepSeek 的 Messages（Anthropic 兼容）接口；留空 = 官方地址，密钥用「AI 模型」页那个。", {}),
        ("AI_CHAT_DSH_RUNTIME_DIR", "运行时目录", "text", r"G:\FurinaDshRuntime", "官方 SDK 装在这里（约 80MB）。", R),
        ("AI_CHAT_DSH_PORT", "本机端口", "int", 53212, "内核只监听本机；这个端口和下一个端口被占用时改一下。", dict(min=1024, max=65533, restart=True)),
    ]),
    ("Agent（主人派活操作电脑）", "主人私聊她派活（二代仿真的群里直接说也行），她可以在这台电脑上用命令行、读写文件来完成。默认关闭。"
                               "发「新会话」可以清空 Agent 的上下文。", [
        ("AI_CHAT_AGENT_ENABLED", "允许 Agent 操作电脑", "bool", False,
         "打开后，主人（「聊天回复」页里的群主 QQ）私聊发文字会交给 Agent；二代仿真的群里你明确让她做事时，"
         "她会自己判断交给 Agent，做完把结果发到群里（只认你本人的话）。删除、安装、改系统设置这类操作她会先问你。"
         "只给你自己用；别的群友永远用不了。", {}),
        ("AI_CHAT_AGENT_MAX_STEPS", "每个任务最多几步", "int", 30, "", _o(min=4, max=80, suffix="步")),
        ("AI_CHAT_AGENT_TIMEOUT", "每个任务最长", "int", 600, "", _o(min=30, max=1800, suffix="秒")),
        ("AI_CHAT_AGENT_CWD", "默认工作目录", "text", "", "留空 = 内核自己的工作区（data\\agent 下）。", {}),
    ]),
]
