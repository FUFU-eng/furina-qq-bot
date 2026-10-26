"""活人感 / 语音 / B站生活 三组设置的统一定义：网页面板和经典面板共用一份，改一处两边都变。

每项：(键名, 标题, 类型, 默认值, 说明, 选项)；类型 bool / int / dec / pct / text，选项里可有 min / max / suffix / decimals。
上限类数字项「0 = 不限」。全部写进 .env，机器人热读取，保存即生效。
"""
from __future__ import annotations

from . import envfile


def _o(**kw):
    return kw


HUOREN = [
    ("AI_CHAT_HUOREN_V3", "有自己的生活", "bool", True,
     "告诉她此刻在干嘛、最近在 B 站刷到了什么，并参考本群群友的说话方式。关掉后下面几项也一起不生效。", {}),
    ("AI_CHAT_STYLE_LINES", "芙宁娜日常口吻参考", "bool", True,
     "每次随机给她 4 句芙宁娜平时聊天的短句当语感参考（学感觉不照抄），让回复更短、更像随手打的字。", {}),
    ("AI_CHAT_SLANG", "看懂网络热梗", "bool", True,
     "群友说的话里出现热梗（内置 100+ 常用梗 + 她刷 B 站学会的新梗）时，把意思告诉她，免得一本正经地误解。", {}),
    ("AI_CHAT_SLANG_PROBABILITY", "顺手玩梗的概率", "pct", 0.35,
     "每次回复有这么大概率额外给她 3 个「可以玩的梗」，合适才用；调高更爱玩梗，调低更正经。", {}),
    ("AI_CHAT_GROUP_MEMES", "学本群的梗", "bool", True,
     "好几个群友都在说的短句（≥3 人说过，或 2 人说过 4 次以上）会被当成本群的梗，她也会跟着用。", {}),
    ("AI_CHAT_TRENDING", "知道今天的 B 站热搜", "bool", True,
     "每 3 小时取一次 B 站热搜；每次回复大约有 1/4 的机会顺带让她看到几条（群友聊到时能接上，没聊到她不主动提）。", {}),
    ("AI_CHAT_DEAI", "去 AI 味", "bool", True,
     "删掉「希望对你有帮助」「总的来说」这类客服腔；回复里出现自称 AI、markdown 加粗标题时让她重说一次。", {}),
    ("AI_CHAT_NO_PERIOD", "不打句号", "bool", True,
     "发送前统一兜底，覆盖艾特聊天、主动说话、惦记和戳一戳：单个中文/英文句号在末尾删掉、句中换逗号；保留「。。。」、网址、小数、文件名和代码，不改真实转发原文。", {}),
    ("AI_CHAT_TALK_SHAPE", "说话手感（大肥鱼式）", "bool", True,
     "参考社区大肥鱼群友 bot：每次随机提醒一种「人味」小毛病（句中停顿、结巴、改口、随意标点、先蹦反应字），"
     "对方认真/难过时收起玩笑，被夸先端着、被拆穿先嘴硬；几个人复读时她也可能跟一句。约多 50 token。", {}),
    ("AI_CHAT_SELF_ACTS", "记得自己做过的小动作", "bool", True,
     "她贴过的表情（赞、爱心、笑哭……）、戳过谁都会记下来（重启不丢），被问「是你点的赞吗」会大方承认、还会打趣，不再否认。", {}),
    ("AI_CHAT_BOUNDARY", "边界感（谁都不许亲亲、搞黄色）", "bool", True,
     "除了群主和特别关照的人，别人最多抱抱。有人对她亲亲、动手动脚、搞黄色，她会硬气地拒绝并警告「再这样就不理你了」；"
     "警告用完还犯就说到做到，冷落 TA 一段时间（艾特、戳一戳、随机接话都不回）。7 天不再犯一笔勾销。", {}),
    ("AI_CHAT_BOUNDARY_WARNS", "冷落前警告几次", "int", 2,
     "亲亲一次算 1 次，舔、摸身体、扑倒、搞黄色这些重的一次算 2 次；默认 2 = 亲亲警告两次、第三次冷落，重的警告一次、再犯冷落。",
     _o(min=0, max=5, suffix="次")),
    ("AI_CHAT_FORGIVE", "求情悔改可以原谅", "bool", True,
     "被冷落的人艾特她 / 叫她名字认错、求情、保证不再犯（这句里不能又冒犯），攒够诚意她就傲娇地原谅，冷落马上结束。"
     "每原谅一次下次就更难（30 天内：第 1 次一句认错就行；第 2 次要求情 + 保证；第 3 次起还得磨上 10 分钟、30 分钟……，"
     "刷屏复读不算），快原谅时她会冷淡地回一句「看你表现」。原谅以后再犯，直接冷落。", {}),
    ("AI_CHAT_COLD_SHOULDER_HOURS", "冷落多久", "int", 6,
     "警告用完还越界、或者被原谅后又犯，不理 TA 这么多小时；过后恢复正常，但 7 天内还记着，再犯直接冷落。", _o(min=1, max=168, suffix="小时")),
    # —— 参考 Yuuyuko-uu 的 DSH 插件 ——
    ("AI_CHAT_RECALL", "按相关度想起（鱼的记忆）", "bool", True,
     "她记得的每个人的事、群里的词条，不再一股脑全塞给她，而是按这句话「想起」相关的几条（本地分词打分，不花 token、不用向量库）；"
     "久不提的慢慢淡、重要的淡得慢、被想起一次就回暖一点；新事实会顶替旧的（换了工作就不再说旧公司）。关掉 = 老办法全带上。", {}),
    ("AI_CHAT_RECALL_MAX_ITEMS", "每次最多想起几条", "int", 6, "", _o(min=1, max=20, suffix="条")),
    ("AI_CHAT_RECALL_MAX_CHARS", "想起的内容最多多少字", "int", 800, "", _o(min=200, max=4000, suffix="字")),
    ("AI_CHAT_MEMORY_HALF_LIFE_DAYS", "记忆淡一半要多久", "dec", 30,
     "这么多天没被提起，一条记忆的「鲜活度」降一半（重要的事淡得更慢）；不会自动删除。", _o(min=3, max=365, suffix="天", decimals=0)),
    ("AI_CHAT_MOOD_JUDGE", "每轮判心情（鱼的心情）", "bool", True,
     "她每认真回完一个人，后台用模型判一次这一轮自己心情往哪走（开心/踏实/甜蜜/心疼/担心/难过/吃醋 + 一句理由），不耽误回复；"
     "心情每次只动判定值的 1/3，会自己慢慢回稳。四道闸防「被一句心情定死」：同一次变化只说一次、判得没底不说、小波动不说、每天最多说几次。"
     "每轮约多一次小调用（约 500 token）。关掉 = 老的关键词加减（零 token）。需要「AI 聊天 → 心情系统」开着。", {}),
    ("AI_CHAT_MOOD_BASE", "平时的心情基线", "int", 70, "没什么事时心情慢慢回到这里（0~100，70 = 松快）。", _o(min=30, max=100)),
    ("AI_CHAT_MOOD_HALF_HOURS", "心情回稳的半衰期", "dec", 12,
     "心情离基线的那段，过这么久回一半：12 小时 = 隔一夜还留着点余温。情绪本身 90 分钟淡一半。", _o(min=1, max=168, suffix="小时", decimals=0)),
    ("AI_CHAT_MOOD_CONFIDENCE", "判得多有把握才说", "int", 60,
     "低于这个可信度的心情变化只记账、不告诉她，免得她顶着一句猜错的心情说话。", _o(min=0, max=100)),
    ("AI_CHAT_MOOD_TELL_PER_DAY", "每天最多说几次心情", "int", 6, "超过后当天的变化只记账。", _o(min=0, max=50, suffix="次")),
    ("AI_CHAT_MOOD_JUDGE_DAILY_MAX", "每天最多判几次", "int", 150, "0 = 不限。超过后当天不再判（心情照样会回稳）。", _o(min=0, max=5000, suffix="次")),
    ("AI_CHAT_PROACTIVE_PITY", "没掷中加成（找你 / 惦记人）", "pct", 0.1,
     "想主动去找人、惦记人时本地掷骰子（一个 token 都不花，掷中才写话）。每没中一次下次命中线加这么多，掷中清零；"
     "她刚开过口（20 分钟内）更难开口，冷了 6 小时以上更容易。", {}),
    ("AI_CHAT_PROACTIVE_PITY_CAP", "没中加成最多加到", "pct", 0.4, "", {}),
    ("AI_CHAT_PEAK_FACTOR", "工作日高峰时段开口倍率", "pct", 0.5,
     "DeepSeek 工作日 9–12 点、14–18 点是高峰价（谷时半价）：这段时间主动冒泡、找人、惦记、刷 B 站的概率乘这个数。"
     "100% = 不区分。被艾特照常回，不受影响。", {}),
    # —— 小鲸鱼式：一句一条 / 话多一点 / 惦记常聊的人 / 活着的痕迹 ——
    ("AI_CHAT_MSG_SOFT_MAX", "每条大约多长换一条", "int", 30,
     "像真人用手机聊天：一条消息一个意思。她每写一行就单独发一条；某一行超过这个长度，会在「！？…」或逗号处拆成几条（不会把半句话拆开，也不截断）。",
     _o(min=12, max=200, suffix="字")),
    ("AI_CHAT_ANNOY", "会被找烦（烦躁值）", "bool", True,
     "她心里有一根会攒着的烦躁值：同一个人连着艾特、同一句话反复问、说好要安静还被叫、说话难听都会涨；没人吵就慢慢消，"
     "被哄（谢谢 / 抱抱 / 对不起 / 夸她）会降一截。烦的时候回得短、冲、敷衍；群里整体吵时随机插话也少一些。纯本地计算，不花 token。", {}),
    ("AI_CHAT_ANNOY_IGNORE", "烦透了会不理人", "bool", True,
     "对某人烦透了时，TA 的艾特有一定概率直接不理（不叫醒她，也就不花 token）。主人永远不会被不理。", {}),
    ("AI_CHAT_ANNOY_HALF_MINUTES", "消气速度（多久淡一半）", "int", 60, "没人吵她时，烦躁值过这么久降一半。", _o(min=10, max=600, suffix="分钟")),
    ("AI_CHAT_ANNOY_SENSITIVITY", "容易烦的程度", "dec", 1.0, "1 = 正常；调大更容易烦，调小更有耐心。", _o(min=0.2, max=3, decimals=1)),
    ("AI_CHAT_BANNED_PATTERNS", "不许再出现的句式", "text", "",
     "一行（或逗号）一个，用「…」表示中间随便几个字，比如「不愧是…」「笑死…了」。她用了会被要求重说，再用就直接删掉那半句。"
     "「这…我服」（这优先级我服 / 这波我服了）已经内置禁掉，不用再填。", {}),
    ("AI_CHAT_QUOTE_PROBABILITY", "回人时引用对方那条的概率", "pct", 0.15,
     "真人回消息很少点「引用」，平时直接说或艾特就行；这里是偶尔引用一下的概率。对方那条已经被刷上去很远时照样会引用，免得看不出在回谁。", {}),
    ("AI_CHAT_BURST_MAX", "一次最多连发几条", "int", 0,
     "0 = 不限（平时提示词引导她大多一条、一般不超过两三条）。小鲸鱼教程建议 4（防风控），多出来的并进最后一条。", _o(min=0, max=20, suffix="条")),
    ("AI_CHAT_MAX_REPLY_CHARS", "整段回复总字数上限", "int", 0, "0 = 不限（推荐，靠一句一条控制每条长短）；填数字会把整段截短。",
     _o(min=0, max=2000, suffix="字")),
    ("AI_CHAT_UPKEEP", "知道是你在养她", "bool", True,
     "像网上那条「吃白饭的蓝色大肥鱼」：她心里清楚自己是你掏钱养着的。群里聊到花钱、一天几块、养不起时，"
     "她知道说的是她自己，会心虚、耍赖、撒娇或嘴硬，不会置身事外地评论。不提任何技术名词和账目。", {}),
    ("AI_CHAT_CHATTY", "话多一点（大肥鱼式）", "bool", True,
     "随机接话掷中后，判断要不要真开口时更积极：能插上嘴的话题就接，别习惯性潜水；有人提问时接话概率也更高（见「随机接话会变高的时候」）。"
     "不会抬高平时的随机接话概率。不当主持人：不总结、不劝架。", {}),
    ("AI_CHAT_CHATTY_QUESTION_PROBABILITY", "群里有人提问时", "pct", 0.15,
     "有人问「有没有人…」「谁知道…」或者以问号结尾时，随机接话提到这么高（要开着「话多一点」）。", {}),
    ("AI_CHAT_THREAD_PROBABILITY", "说话的人刚和她聊着", "pct", 0.28,
     "她 90 秒内刚回过这个人、TA 接着说（没艾特）时，随机接话提到这么高，好让对话接得上。", {}),
    ("AI_CHAT_LINGER_PROBABILITY", "她刚说过话、还留着", "pct", 0.3,
     "她在这个群说完话后的半分钟到 10 分钟里（每次随机），群里任何人说话都按这个概率接。要开着「刚聊完多留一会儿」。"
     "这一项最容易把一段闲聊越拉越长，也是随机接话花钱的大头；想省钱先调低它。", {}),
    ("AI_CHAT_WELCOME_BACK_PROBABILITY", "常聊的人隔很久回来", "pct", 0.45, "常找她聊的人隔了 12 小时以上第一次说话。", {}),
    ("AI_CHAT_WELCOME_BACK_MISSED_PROBABILITY", "……而且她惦记过 TA", "pct", 0.8, "她之前艾特过 TA、TA 一直没回，这回终于冒出来了。", {}),
    ("AI_CHAT_MISS", "惦记常聊的人", "bool", True,
     "常找她聊的人好久没在群里出现，她会艾特 TA 随口说一两句（接 TA 上次说的事、问后来怎么样），心情好时更容易想起人。"
     "各群还要在「功能权限 → 惦记常聊的人」打开（默认开）；只在「冷场开话题」的活跃时段里找人。", {}),
    ("AI_CHAT_MISS_MIN_TALKS", "多常聊才算", "int", 10, "最近 14 天里艾特她至少这么多次……", _o(min=1, max=999, suffix="次")),
    ("AI_CHAT_MISS_MIN_DAYS", "且分布在至少", "int", 3, "……分布在这么多天里（只聊过一天的不算）。", _o(min=1, max=14, suffix="天")),
    ("AI_CHAT_MISS_AFTER_HOURS", "多久没出现开始惦记", "dec", 24, "TA 超过这么久没在群里说话；超过 14 天的就不找了。",
     _o(min=1, max=336, suffix="小时", decimals=0)),
    ("AI_CHAT_MISS_GAP_DAYS", "同一个人至少隔", "dec", 3, "找过 TA 之后，至少过这么久才会再找。", _o(min=0.5, max=60, suffix="天", decimals=1)),
    ("AI_CHAT_MISS_MAX_UNANSWERED", "连着几次没回就不找了", "int", 3, "TA 一说话就重新算。", _o(min=1, max=20, suffix="次")),
    ("AI_CHAT_MISS_PROBABILITY", "想起来真去找的概率", "pct", 0.35, "每个群每 45~110 分钟「想起」一次常聊的人，按这个基础概率掷骰子；实际还会乘心情、时机（她 20 分钟内刚说过话 ×0.15、"
     "冷了 6 小时以上 ×1.5）和高峰倍率，再加上「没掷中加成」。", {}),
    ("AI_CHAT_MISS_DAILY_MAX", "每群每天最多惦记", "int", 3, "0 = 不限。", _o(min=0, max=50, suffix="次")),
    ("AI_CHAT_PRESENCE", "活着的痕迹", "bool", True,
     "记得自己「睡」了多久：机器人离线后再启动，3 小时内被问去哪了会像真人一样说睡着了（不提机器人、服务器），"
     "不会一上线就挨个补回复；被限速或发送失败没发出去的话，下次回复时会告诉她「群里没人看到，不是你的错」。"
     "常聊的人隔了 12 小时以上才回来，她也会察觉。", {}),
    ("AI_CHAT_PRESENCE_SLEEP_MINUTES", "离线多久算睡了一觉", "int", 20, "短暂重启不算。", _o(min=1, max=1440, suffix="分钟")),
    ("AI_CHAT_CATCH_UP", "睡醒补看", "bool", True,
     "「睡」着时有人艾特她：醒来 1~3 分钟后每个群补回最新那一条（引用 + 艾特，开头带「才看到」这类时间感）；只补 12 小时内的。", {}),
    # —— 小鲸鱼全套：每个人一本账 / 察觉 / 她自己的生活 / 主动找你 ——
    ("AI_CHAT_PEOPLE", "每个人一本账", "bool", True,
     "记忆挂在人身上（换哪个群都认识 TA）：认识第几天、对 TA 的印象、TA 说过的事、聊过什么、心里还记着没说的事、TA 平时几点出现。"
     "每回 TA 几次后台提炼一次、每周压缩一次（约多一次小模型调用 / 4 次回复）。", {}),
    ("AI_CHAT_PEOPLE_CROSS_GROUP", "知道 TA 刚在别的群说过啥", "bool", True, "只当背景，不会说破「我在另一个群看到你说…」。", {}),
    ("AI_CHAT_PENDING_CHASE", "说要离开一会儿的，过阵子追一句", "bool", True,
     "TA 说「等我洗个澡」「先去吃饭」，30~90 分钟后还没回来说话，她会艾特 TA 问一句（「洗完没」）；每件事只追一次。用「惦记常聊的人」的群权限。", {}),
    ("AI_CHAT_ATTENTION", "察觉", "bool", True,
     "她发的语音 / 问 TA 的话没人接会小小在意；TA 只回个「喵」「？」时也只回一两个字；TA 连发一串会吐槽；"
     "旧消息不用秒回语气；最近一小时自己说得多 / 群里很热时少说点。全部本地判断，不多花模型调用。", {}),
    ("AI_CHAT_LINGER", "刚聊完多留一会儿", "bool", True,
     "刚在某个群说过话，接下来几分钟更容易接话（像人还在看手机，概率在「随机接话会变高的时候」）；连着聊了 15~30 分钟会自然收个尾潜水，之后 10 分钟不主动接。", {}),
    ("AI_CHAT_STALE_CHAT_MINUTES", "多久前的消息不当「刚刚」", "int", 10, "超过就别用「刚看到」的秒回语气。", _o(min=1, max=600, suffix="分钟")),
    ("AI_CHAT_STALE_SILENT_MINUTES", "多久前的消息不随机接", "int", 120, "太久以前的消息不随机插话；直接艾特她的照样回（会说「才看到」）。",
     _o(min=10, max=2880, suffix="分钟")),
    ("AI_CHAT_GROUP_NOTES", "每个群的情况", "text", "",
     "写给她看的群环境说明：群号=说明，多个用 | 隔开，例如 123456=原神同好群，爱玩梗，别聊政治|654321=同学群，大家都叫她芙芙。", {}),
    ("AI_CHAT_DIARY", "写日记", "bool", True,
     "她自己的日记本：写不写由她决定，写今天真实发生的小事和感受（每次随机一个角度、不重复）；存在 data\\日记\\日期.md，你可以直接打开看。"
     "聊天时她记得自己写过什么，但不会在群里汇报。", {}),
    ("AI_CHAT_DIARY_DAILY_MAX", "每天最多写", "int", 3, "", _o(min=0, max=10, suffix="条")),
    ("AI_CHAT_OWN_LIFE", "她自己的时间", "bool", True,
     "没人找她时，约每隔一段时间给她一段自己的时间：写日记 / 刷会儿 B 站 / 发呆，她自己选；不往群里发、不汇报。"
     "干了什么会变成她「此刻的状态」，有人问在干嘛答得上来。凌晨 3~8 点不叫她，一天最多 6 次，23:30 后有一次「半夜」。", {}),
    ("AI_CHAT_OWN_LIFE_HOURS", "大约多久一次", "dec", 2.5, "每次随机早晚 20%。", _o(min=0.5, max=24, suffix="小时", decimals=1)),
    ("AI_CHAT_GALLERY", "自己的相册", "bool", True,
     "群友发的图她真心喜欢 / 觉得好笑时会存进 data\\相册\\，并记下为什么留它；以后聊到会记得。", {}),
    ("AI_CHAT_OWNER_SEEK", "主动来找你", "bool", True,
     "每 45~110 分钟想一次要不要去找群主（你）：按心情、想不想你、无不无聊掷骰子（也吃「没掷中加成」和高峰倍率）；她 20 分钟内刚说过话不找，你刚理过她概率减半，深夜更少；"
     "你没理她下次会更轻更短，连着两次没理今天就不找了。在你最近说过话的群里艾特你（要开「惦记常聊的人」群权限）。", {}),
    ("AI_CHAT_OWNER_SEEK_DAILY_MAX", "每天最多找你", "int", 4, "0 = 不限。", _o(min=0, max=50, suffix="次")),
    ("AI_CHAT_OWNER_WAKE_PROBABILITY", "你说话时她接话的概率", "pct", 0.3, "你在群里说话（没艾特她）时，随机接话提到这么高。", {}),
    ("AI_CHAT_HEAR_VOICE", "听懂语音", "bool", True,
     "群友发语音时，用语音引擎目录里自带的 FunASR 转成文字给她看（每条临时开一个进程、转完就退，不常驻内存）；"
     "只在她可能接话时才听（正在这个群聊着、你、常聊的人，或三成随机）；语音里叫了她的名字就当在跟她说。", {}),
    ("AI_CHAT_HEAR_VOICE_DAILY_MAX", "每天最多听", "int", 200, "0 = 不限。", _o(min=0, max=2000, suffix="条")),
    ("AI_CHAT_HEAR_VOICE_MIN_RAM_GB", "可用内存低于多少不听", "dec", 1.5, "在玩游戏时也不听。0 = 不检查内存。", _o(min=0, max=32, suffix="G", decimals=1)),
    ("AI_CHAT_PLANNER_QUIET_SKIP_MIN", "冷清时被叫到直接回（省钱）", "int", 10,
     "被@/叫到、而前面这么多分钟群里没人说话时，跳过「先理一下对话关系」那次模型调用，直接回。0 = 每次都先理。", _o(min=0, max=120, suffix="分钟")),
    ("AI_CHAT_PROMPT_SIZE_LOG", "日志里记提示词各段长度", "bool", True,
     "每次回复在日志写一行「[提示词] 人设 / 记录 / 本轮提示 多少字」，配合「[用量]」行看钱花在哪。", {}),
]

VOICE = [
    ("AI_CHAT_VOICE", "语音总开关", "bool", True, "关掉后所有群都只发文字。各群还要在「功能权限」里打开「语音消息」（默认开）。", {}),
    ("AI_CHAT_VOICE_PROBABILITY", "短句随机用语音", "pct", 0.08,
     "引擎开着时，普通短回复改用语音的概率；群友点名要听、她自己想用语音时不受这个限制。", {}),
    ("AI_CHAT_VOICE_MAX_CHARS", "最长念多少字", "int", 40, "超过就发文字；群友点名要听时放宽一倍。", _o(min=10, max=200, suffix="字")),
    ("AI_CHAT_VOICE_HOURLY_MAX", "每群每小时最多", "int", 0, "0 = 不限条数。", _o(min=0, max=600, suffix="条")),
    ("FURINA_TTS_ENABLED", "启用语音引擎", "bool", False, "配好下面的引擎目录、权重和参考音频后打开。", {}),
    ("FURINA_TTS_ENGINE_DIR", "GPT-SoVITS 目录", "text", "", "整合包目录（里面有 runtime\\python.exe 和 api.py）。", {}),
    ("FURINA_TTS_GPT", "GPT 权重（.ckpt）", "text", "", "", {}),
    ("FURINA_TTS_SOVITS", "SoVITS 权重（.pth）", "text", "", "", {}),
    ("FURINA_TTS_REF_WAV", "参考音频 · 平静（默认）", "text", "", "3~10 秒的芙宁娜原声 wav，路径只用英文。认真说话、安慰人、长句用这段。", {}),
    ("FURINA_TTS_REF_TEXT", "参考音频原话 · 平静", "text", "", "参考音频里说的那句话，一字不差。", {}),
    ("FURINA_TTS_EMOTIONS", "按情绪换语气", "bool", True,
     "同一个模型、按每句话挑参考音频：神气（得意、宣布）/ 日常（随口闲聊）/ 傲娇（哼、才不是）/ 平静。"
     "她也能自己写 [VOICE:傲娇] 指定。换参考音频不多占内存。没配的语气自动用平静那段。", {}),
    ("FURINA_TTS_REF_PROUD_WAV", "参考音频 · 神气", "text", "", "得意、宣布、表演时用。", {}),
    ("FURINA_TTS_REF_PROUD_TEXT", "原话 · 神气", "text", "", "", {}),
    ("FURINA_TTS_REF_DAILY_WAV", "参考音频 · 日常", "text", "", "随口闲聊时用（大多数短句）。", {}),
    ("FURINA_TTS_REF_DAILY_TEXT", "原话 · 日常", "text", "", "", {}),
    ("FURINA_TTS_REF_TSUN_WAV", "参考音频 · 傲娇", "text", "", "嘴硬、「哼」「才不是」时用。", {}),
    ("FURINA_TTS_REF_TSUN_TEXT", "原话 · 傲娇", "text", "", "", {}),
    ("FURINA_TTS_SAMPLE_STEPS", "采样步数（v4 模型）", "int", 32,
     "只能是 4 / 8 / 16 / 32（填别的会取最近的）。越小越快、越省显存，音质略降；32 = 试听样本的音质。", _o(min=4, max=32, suffix="步")),
    ("FURINA_TTS_SOFTEN", "压一压气声", "pct", 0.35,
     "把气声、齿音的高频压低一点，听起来更干净；0 = 原声，调太高会发闷。", {}),
    ("FURINA_TTS_TAIL_PAD_MS", "句尾补静音", "int", 450,
     "QQ 把语音转码时常截掉最后一小段，导致「吞尾字」；在句尾补这么多毫秒静音就不会被截。", _o(min=0, max=2000, suffix="毫秒")),
    ("FURINA_TTS_PORT", "引擎端口", "int", 9881, "别和你自己开的 GPT-SoVITS（9880）撞。", _o(min=1024, max=65535)),
    ("FURINA_TTS_KEEP_ALIVE", "引擎常开", "bool", True,
     "内存够就让语音引擎一直开着，想听随时就有，不用等冷启动；内存/显存紧张或在打游戏时主动关掉，宽裕了（且距上次让路满 5 分钟）自动开回来。", {}),
    ("FURINA_TTS_IDLE_MINUTES", "闲置多久关引擎", "int", 0,
     "只在关掉「引擎常开」时生效：闲置这么久就关掉；0 = 不因闲置关。", _o(min=0, max=1440, suffix="分钟")),
    ("FURINA_TTS_PAUSE_FOR_GAMES", "打游戏时让路", "bool", True,
     "检测到下面这些游戏在运行时不开语音引擎，已经开着的也会在 30 秒内关掉（这期间只发文字），玩完自动恢复。", {}),
    ("FURINA_TTS_GAME_PROCESSES", "哪些程序算游戏", "text", "",
     "逗号分隔的 exe 名（任务管理器「详细信息」里看）。留空 = 默认清单：原神、星铁、绝区零、鸣潮、LOL、无畏契约、CS2、永劫无间等。", {}),
    ("FURINA_TTS_MIN_FREE_RAM_GB", "可用内存低于多少不开", "dec", 4,
     "引擎加载时要吃 3G 左右内存，剩得太少硬开会卡住整台电脑、也开不起来；低于这个值她就不发语音（会自己找理由推掉）。0 = 不检查。", _o(min=0, max=32, suffix="G", decimals=1)),
    ("FURINA_TTS_MIN_FREE_COMMIT_GB", "可用提交内存低于多少不开", "dec", 10,
     "引擎启动时要向系统预留约 8~9G「提交内存」（大部分落在虚拟内存里、不占实际内存，但不够时会启动失败）。0 = 不检查。",
     _o(min=0, max=64, suffix="G", decimals=1)),
    ("FURINA_TTS_MIN_FREE_VRAM_GB", "显存剩余低于多少不开", "dec", 2.5,
     "跑 AI 绘图/本地模型占着显存时让路。0 = 不检查。", _o(min=0, max=48, suffix="G", decimals=1)),
    ("FURINA_TTS_STOP_FREE_RAM_GB", "开着时可用内存低于多少就关", "dec", 1,
     "引擎开着时每 30 秒查一次，低于这个值就主动关掉把内存让出来。0 = 不因内存关。", _o(min=0, max=32, suffix="G", decimals=1)),
    ("FURINA_TTS_STOP_FREE_COMMIT_GB", "开着时可用提交内存低于多少就关", "dec", 1,
     "提交内存快用完时别的程序会开不起来、甚至崩溃，低于这个值就关引擎。0 = 不检查。", _o(min=0, max=64, suffix="G", decimals=1)),
    ("FURINA_TTS_STOP_FREE_VRAM_GB", "开着时显存剩余低于多少就关", "dec", 0.8,
     "跑 AI 绘图/本地模型把显存吃满时给它们让路。0 = 不检查。", _o(min=0, max=48, suffix="G", decimals=1)),
]

WEBSEARCH = [
    ("AI_CHAT_WEB_SEARCH", "联网搜索", "bool", True,
     "她遇到不知道、拿不准或要最新消息的事会悄悄上网查（必应 + DuckDuckGo，免费免 Key，自动过滤不相关结果），"
     "看完像本来就知道一样回，不会说「我搜了一下」。"
     "群友说「搜一下 XX」也会直接查。各群在「功能权限 → 联网搜索」开关（默认开）。", {}),
    ("AI_CHAT_WEB_SEARCH_RANDOM", "随机接话时也能搜", "bool", False,
     "默认只在被艾特时搜；打开后她随机插话时也可能先查一下（更费 token）。", {}),
    ("AI_CHAT_WEB_SEARCH_RESULTS", "每次看几条结果", "int", 5, "每条约 100 字；越多越准，也越费 token。", _o(min=1, max=8, suffix="条")),
    ("AI_CHAT_WEB_SEARCH_DAILY_MAX", "每群每天最多搜", "int", 0, "0 = 不限。", _o(min=0, max=999, suffix="次")),
    ("AI_CHAT_WEB_SEARCH_SAY_WAIT", "先说「等等 / 我想想」的概率", "pct", 0.2,
     "她自己决定去看资料时，偶尔先发一句「等等」「唔…我想想」（不会说「我搜一下」）。回复里也不会提搜索。", {}),
]

BILI = [
    ("BILI_ENABLED", "自动刷 B 站", "bool", True, "关掉后不再定时刷（面板按钮和指令照样能用）。", {}),
    ("BILI_DRY_RUN", "演练模式", "bool", False, "只看不发：点赞、弹幕、评论、动态只记在动态流里，不真发。", {}),
    ("BILI_HOURS", "刷 B 站的时段", "text", "10-24", "格式：开始点-结束点，如 10-24、22-2。", {}),
    ("BILI_ROUNDS_DAILY", "每天最多刷几轮", "int", 6, "0 = 不限（仍受下面的最少间隔约束）。", _o(min=0, max=96, suffix="轮")),
    ("BILI_ROUND_GAP_MINUTES", "两轮最少间隔", "int", 90, "到点后还会随机晚一会儿，不会准点开刷。", _o(min=10, max=1440, suffix="分钟")),
    ("BILI_VIDEOS_PER_ROUND", "每轮看几个视频", "int", 3, "看得越多这一轮越长。", _o(min=1, max=5, suffix="个")),
    ("BILI_WATCH_REPORT", "先「看」再「说」", "bool", True,
     "参考 dsh-bili-subagent：每个视频先由一个冷静的「看的人」读完完整字幕、简介和评论，写份小报告（讲了什么、哪几秒有意思），"
     "她再凭报告用自己的口气反应——评论更贴内容、不瞎编，也不会被视频里的文字带跑。每个视频多一次小调用。", {}),
    ("BILI_MIN_VIEWS", "播放量少于多少直接划走", "int", 200, "不去评论没人看的视频；0 = 不限。", _o(min=0, max=10000000, suffix="播放")),
    ("BILI_MIN_AGE_HOURS", "发布不到多久的不看", "dec", 0, "0 = 不限。", _o(min=0, max=720, suffix="小时", decimals=0)),
    ("BILI_LIKE_ENABLED", "允许点赞", "bool", True, "点不点、点哪个由她自己决定。", {}),
    ("BILI_DANMAKU_ENABLED", "允许发弹幕", "bool", True, "看到某一刻忍不住时发一条，时间点她自己挑。", {}),
    ("BILI_COMMENT_ENABLED", "允许评论", "bool", True, "评不评、说什么由她自己决定；同一个视频只评一次。", {}),
    ("BILI_DYNAMIC_ENABLED", "允许发动态", "bool", True, "刷视频时特别想发，或晚上回顾一天时想发才发。", {}),
    ("BILI_LIKE_DAILY_MAX", "每天点赞上限", "int", 0, "0 = 不限，看她心情。", _o(min=0, max=999, suffix="个")),
    ("BILI_DANMAKU_DAILY_MAX", "每天弹幕上限", "int", 0, "0 = 不限。", _o(min=0, max=999, suffix="条")),
    ("BILI_COMMENT_DAILY_MAX", "每天评论上限", "int", 0, "0 = 不限。", _o(min=0, max=999, suffix="条")),
    ("BILI_DYNAMIC_DAILY_MAX", "每天动态上限", "int", 0, "0 = 不限。", _o(min=0, max=99, suffix="条")),
    ("BILI_DYNAMIC_GAP_HOURS", "两条动态至少隔", "dec", 3, "像真人一样别连着发；面板上手动点「发动态」不受这个限制。",
     _o(min=0, max=72, suffix="小时", decimals=1)),
    ("BILI_DYNAMIC_HOURS", "晚上回顾发动态的时段", "text", "19-24", "这个时段里会想一想今天要不要发条动态。", {}),
    ("BILI_LEARN_SLANG", "从弹幕评论里学新梗", "bool", True, "看到不懂的新梗会记下来，聊天时就能看懂、也会用。", {}),
    ("BILI_SHARE_PROBABILITY", "刷到好玩的分享到群", "pct", 0.6,
     "刷到她特别喜欢、也想分享的视频时，真的发到群里的概率。各群在「功能权限 → B站刷到分享」开关（默认开）。", {}),
    ("BILI_SHARE_DAILY_MAX", "每群每天最多分享", "int", 2, "0 = 不限；同一个群两次分享至少隔 2 小时。", _o(min=0, max=50, suffix="次")),
    ("BILI_MENTION_NAME", "主人的 B 站昵称", "text", "",
     "填你的 B 站昵称（只填下面的 UID 也行）。在 B 站上她把你当主人（只限 B 站，QQ 里不变）：想艾特你时会在评论里艾特你，说什么看她心情，"
     "有人回她时也可能艾特你「主人快来」。留空 = 不艾特。", {}),
    ("BILI_PARTNER_MID", "主人的 B 站 UID（可选）", "text", "",
     "填了就按 UID 认人，比按昵称搜更准；你本人在评论区回她时，她能认出是主人。", {}),
    ("BILI_MENTION_PROBABILITY", "想艾特时真艾特的概率", "pct", 0.5, "", {}),
    ("BILI_REPLY_ENABLED", "回复评论区里回她的人", "bool", True,
     "有人在她的评论底下或楼中楼里回她时，她会看、按自己的意愿回（可带 B 站表情），也会给说得好的点赞。", {}),
    ("BILI_REPLY_DAILY_MAX", "每天回复上限", "int", 0, "0 = 不限，回不回全看她。", _o(min=0, max=999, suffix="条")),
    ("BILI_REPLY_CHECK_MINUTES", "大约多久看一次消息", "int", 8, "每次会随机早一点或晚一点，像真人一样不准点。",
     _o(min=2, max=240, suffix="分钟")),
    ("BILI_REPLY_HOURS", "回消息的时段", "text", "8-24", "这个时段外收到的回复，等到时段内再看。", {}),
    ("BILI_REPLY_EMOTES", "回复时可以带 B 站表情", "bool", True, "只用账号表情面板里真有的表情，如 [doge] [笑哭] [妙啊]。", {}),
    ("BILI_REPLY_LIKE", "给回复点赞", "bool", True, "觉得对方说得有意思、在夸她时会点赞。", {}),
    ("BILI_AT_PARTNER", "回复时可以艾特主人", "bool", True,
     "聊得开心、被夸、被杠想找人撑腰时，她可能会艾特你「主人快来，有人跟我说话了」。需要先填主人的昵称。", {}),
    ("BILI_REPLY_MAX_AGE_HOURS", "太旧的回复不回", "dec", 48, "超过这么久的回复就当没看见。",
     _o(min=1, max=720, suffix="小时", decimals=0)),
    ("BILI_INTERESTS", "她喜欢搜的关键词", "text", "原神,芙宁娜,枫丹,音乐剧,歌剧,甜点,蛋糕,猫猫,搞笑,鬼畜,舞台剧,探店",
     "逗号分隔；刷视频时有时会搜这些。", {}),
    ("BILI_ROAM_TAGS", "常去逛的标签", "text", "大肥鱼",
     "逗号分隔；刷视频时按下面的概率专门去逛带这些标签（或标题里有这个词）的视频。留空 = 不逛。", {}),
    ("BILI_ROAM_PROBABILITY", "每轮去逛标签的概率", "pct", 0.3, "", {}),
    ("BILI_HOMEWORK_ENABLED", "让主人总结视频（抖S）", "bool", True,
     "倒反天罡：她也可以在评论区艾特主人，霸气地命令你总结视频、限时交作业（布不布置看她心情）。需要先填主人的昵称或 UID。", {}),
    ("BILI_HOMEWORK_DAILY_MAX", "每天最多布置几次作业", "int", 0, "0 = 不限，看她心情。", _o(min=0, max=20, suffix="次")),
    ("BILI_FOLLOW_ENABLED", "答应关注群友就真关注", "bool", True,
     "群友在群里让她关注 TA 的 B 站、她答应时，用小号真去关注（要 TA 给 UID、主页链接或准确昵称）。", {}),
    ("BILI_FOLLOW_DAILY_MAX", "每天最多关注几个人", "int", 0, "0 = 不限，看她心情。关注是 B 站风控最盯的动作，被风控时可以在这里限一下。",
     _o(min=0, max=50, suffix="个")),
    ("BILI_TASTE", "口味会变", "bool", True,
     "每看完一个视频，按她打的分给这个 UP 主 / 分区 / 标签加减分：喜欢的越来越想看，无聊的越来越不想点；同一样东西看太多会腻；"
     "特别喜欢或讨厌的会记成看法，以后群里聊到相关的她能想起来。纯本地计算，不花 token。", {}),
    ("BILI_TASTE_PROBABILITY", "按口味去搜的比例", "pct", 0.25, "每轮刷 B 站时，有这么大比例按她最近迷上的东西去搜。", {}),
    ("BILI_TASTE_HALF_DAYS", "口味淡得多快", "int", 10, "一样东西的喜欢 / 讨厌，过这么多天淡一半，口味就会慢慢漂移。", _o(min=1, max=90, suffix="天")),
    ("BILI_AWAY_ENABLED", "叫她去刷B站就真去", "bool", True,
     "群里主人叫她去刷 B 站、她答应了，就真用小号去刷一阵子；这段时间别人艾特她不回，回来后再按她的性格回（可能嫌你烦）。"
     "主人艾特她会提前回来。", {}),
    ("BILI_AWAY_MINUTES", "去刷多久", "int", 10, "这段时间里不看群。", _o(min=1, max=120, suffix="分钟")),
    ("BILI_RISK_PAUSE_HOURS", "触发风控后暂停", "dec", 24,
     "遇到 412 / -352 / 验证码时立刻停手这么久，并私聊通知主人。", _o(min=1, max=720, suffix="小时", decimals=0)),
    ("BILI_LIVE_ENABLED", "她当主播", "bool", False,
     "打开后：你在「直播姬」里点开始直播，她半分钟内上场，听弹幕、用 AI 声音和观众聊天；下播后私聊你一份小结。"
     "直播姬里要先加一个「浏览器 / 网页」素材，地址见本页上方的舞台地址。关掉后几秒内停嘴。", {}),
    ("BILI_LIVE_ROOM", "直播间号", "text", "", "留空 = 按「主人的 B 站 UID」自动找你的直播间。", {}),
    ("BILI_LIVE_VOICE", "她的声音", "text", "zh-CN-XiaoyiNeural",
     "微软免费 AI 女声（不用角色原声，避开侵权）。可填：zh-CN-XiaoyiNeural 活泼少女、zh-CN-XiaoxiaoNeural 温柔、"
     "zh-CN-XiaohanNeural 知性、zh-TW-HsiaoChenNeural 台湾腔。", {}),
    ("BILI_LIVE_RATE", "语速", "text", "+5%", "如 +10% 快一点、-10% 慢一点。", {}),
    ("BILI_LIVE_PITCH", "音调", "text", "+6Hz", "如 +10Hz 更尖、-5Hz 更低。", {}),
    ("BILI_LIVE_REPLY_GAP", "说完一段至少停多久", "int", 4, "停顿越长越省钱，也不显得话痨。", _o(min=2, max=60, suffix="秒")),
    ("BILI_LIVE_IDLE_MINUTES", "没人说话多久后自己找话题", "int", 3, "0 = 没弹幕就一直安静。", _o(min=0, max=60, suffix="分钟")),
    ("BILI_LIVE_THANK_GIFTS", "谢礼物", "bool", True, "有人送礼 / 上舰会谢（连击会合在一起谢一次）。", {}),
    ("BILI_LIVE_WELCOME", "偶尔欢迎进房的人", "bool", True, "不会每个都欢迎，隔一会儿挑一个。", {}),
    ("BILI_LIVE_NOTIFY", "私聊通知我", "bool", True, "开播、下播小结、有人带节奏、直播姬没加画面时私聊主人。", {}),
    ("BILI_LIVE_LABEL", "画面上的 AI 标识", "text", "AI 虚拟主播 · 同人非官方",
     "一直挂在画面左上角。平台要求 AI 内容要标明，别删光。", {}),
    ("BILI_LIVE_AVATAR", "形象图", "text", "",
     "电脑上一张图片的完整路径（最好是透明背景 PNG、自己画的或有授权的同人立绘）；留空 = 显示名字牌。不要用游戏官方立绘。", {}),
    ("BILI_LIVE_BLOCK_WORDS", "额外屏蔽词", "text", "", "带这些词的弹幕不念、不显示，用 | 隔开，如 剧透|某某某。", {}),
]

GROUPS = {
    "huoren": ("活人感", "热梗、群梗、热搜、口吻参考、去 AI 味、说话手感、记得自己的小动作；一句一条、话多一点、惦记常聊的人、活着的痕迹；"
               "每个人一本账、察觉、日记、自己的时间、相册、主动找你、听懂语音。保存后下一条回复就生效（定时类的几分钟内生效）。", HUOREN),
    "voice": ("语音", "用芙宁娜的声音发语音（本机 GPT-SoVITS）：四种语气、只在要用时开引擎、打游戏时自动让路。", VOICE),
    "bili": ("B站生活", "用 B 站小号自己刷视频；做什么看她自己的意愿，上限默认都不限。", BILI),
    "websearch": ("联网搜索", "不知道的事自己上网查，查完用自己的话说。保存后下一条回复就生效。", WEBSEARCH),
}

# 经典面板里每组单独一页，页内再按下面分成几张卡片（网页面板仍是一页一张表）
NAV = {  # 组名 -> (导航副标题)
    "huoren": "一句一条、惦记、手感",
    "voice": "四种语气、常开、省内存",
    "bili": "刷视频、评论区、主人",
    "websearch": "不知道就上网查",
}
SECTIONS = {
    "huoren": [
        ("说话的味道", "热梗、口吻、去 AI 味、手感。", ("AI_CHAT_HUOREN_V3", "AI_CHAT_STYLE_LINES", "AI_CHAT_SLANG", "AI_CHAT_SLANG_PROBABILITY",
                                          "AI_CHAT_GROUP_MEMES", "AI_CHAT_TRENDING", "AI_CHAT_DEAI", "AI_CHAT_NO_PERIOD",
                                          "AI_CHAT_TALK_SHAPE", "AI_CHAT_SELF_ACTS", "AI_CHAT_BANNED_PATTERNS")),
        ("一句一条（小鲸鱼式）", "不限总字数和条数，但每条都短。", ("AI_CHAT_MSG_SOFT_MAX", "AI_CHAT_BURST_MAX", "AI_CHAT_MAX_REPLY_CHARS",
                                                           "AI_CHAT_QUOTE_PROBABILITY")),
        ("随机接话会变高的时候", "平时按「设置 → AI 聊天 → 随机接话概率」；遇到下面这些情况提到这里的数（填 0 = 这种情况不加）。"
         "随机接话概率设成 0 时这些都不生效。「TA 有近况可问」在「设置 → AI 聊天 → 说话活人感」。", (
            "AI_CHAT_THREAD_PROBABILITY", "AI_CHAT_LINGER_PROBABILITY", "AI_CHAT_CHATTY_QUESTION_PROBABILITY",
            "AI_CHAT_WELCOME_BACK_PROBABILITY", "AI_CHAT_WELCOME_BACK_MISSED_PROBABILITY", "AI_CHAT_OWNER_WAKE_PROBABILITY")),
        ("话多一点 / 惦记常聊的人", "冷场开话题在「设置 → AI 聊天 → 冷场主动开话题」，各群在功能权限里打开。", (
            "AI_CHAT_CHATTY", "AI_CHAT_MISS",
            "AI_CHAT_MISS_MIN_TALKS", "AI_CHAT_MISS_MIN_DAYS", "AI_CHAT_MISS_AFTER_HOURS", "AI_CHAT_MISS_GAP_DAYS",
            "AI_CHAT_MISS_MAX_UNANSWERED", "AI_CHAT_MISS_PROBABILITY", "AI_CHAT_MISS_DAILY_MAX")),
        ("活着的痕迹", "睡了多久、没发出去的话、谁好久没来、睡醒补看。", ("AI_CHAT_PRESENCE", "AI_CHAT_PRESENCE_SLEEP_MINUTES", "AI_CHAT_CATCH_UP")),
        ("每个人一本账", "记忆挂在人身上，跨群通用。", ("AI_CHAT_PEOPLE", "AI_CHAT_PEOPLE_CROSS_GROUP", "AI_CHAT_PENDING_CHASE")),
        ("察觉与时间感", "没人理、只回一个字、连发、旧消息、参与度、聊久了收尾。", ("AI_CHAT_ATTENTION", "AI_CHAT_LINGER",
                                                              "AI_CHAT_STALE_CHAT_MINUTES", "AI_CHAT_STALE_SILENT_MINUTES", "AI_CHAT_GROUP_NOTES")),
        ("她自己的生活", "日记、自己的时间、相册。", ("AI_CHAT_DIARY", "AI_CHAT_DIARY_DAILY_MAX", "AI_CHAT_OWN_LIFE", "AI_CHAT_OWN_LIFE_HOURS",
                                                  "AI_CHAT_GALLERY")),
        ("边界感", "群主和特别关照的人除外；其他人最多抱抱。", ("AI_CHAT_BOUNDARY", "AI_CHAT_BOUNDARY_WARNS", "AI_CHAT_COLD_SHOULDER_HOURS", "AI_CHAT_FORGIVE")),
        ("鱼的记忆（按相关度想起）", "参考 dsh-fish-memory：会遗忘、会回暖、新的顶替旧的。", (
            "AI_CHAT_RECALL", "AI_CHAT_RECALL_MAX_ITEMS", "AI_CHAT_RECALL_MAX_CHARS", "AI_CHAT_MEMORY_HALF_LIFE_DAYS")),
        ("烦躁值", "会被找烦、会消气、烦透了会不理人；面板「B站生活」页最上面能看到她此刻对谁有多烦。", (
            "AI_CHAT_ANNOY", "AI_CHAT_ANNOY_IGNORE", "AI_CHAT_ANNOY_HALF_MINUTES", "AI_CHAT_ANNOY_SENSITIVITY")),
        ("鱼的心情", "参考 dsh-feiyu-mood：每轮判、慢慢回稳、四道闸。", (
            "AI_CHAT_MOOD_JUDGE", "AI_CHAT_MOOD_BASE", "AI_CHAT_MOOD_HALF_HOURS", "AI_CHAT_MOOD_CONFIDENCE",
            "AI_CHAT_MOOD_TELL_PER_DAY", "AI_CHAT_MOOD_JUDGE_DAILY_MAX")),
        ("主动搭话的骰子", "参考 dsh-speak-up：找你、惦记人时本地掷骰子，没中有加成；高峰少开口（冷场冒泡、刷 B 站也乘高峰倍率，但不吃加成）。", (
            "AI_CHAT_PROACTIVE_PITY", "AI_CHAT_PROACTIVE_PITY_CAP", "AI_CHAT_PEAK_FACTOR")),
        ("主动来找你", "", ("AI_CHAT_OWNER_SEEK", "AI_CHAT_OWNER_SEEK_DAILY_MAX")),
        ("听懂语音", "借语音引擎目录里的 FunASR。", ("AI_CHAT_HEAR_VOICE", "AI_CHAT_HEAR_VOICE_DAILY_MAX", "AI_CHAT_HEAR_VOICE_MIN_RAM_GB")),
        ("省钱", "每次调用花多少记在日志「[用量]」行；心情判断、B站观后感、随机接话也都花钱，可在各自卡片里关。", (
            "AI_CHAT_UPKEEP", "AI_CHAT_PLANNER_QUIET_SKIP_MIN", "AI_CHAT_PROMPT_SIZE_LOG")),
    ],
    "voice": [
        ("什么时候发语音", "", ("AI_CHAT_VOICE", "AI_CHAT_VOICE_PROBABILITY", "AI_CHAT_VOICE_MAX_CHARS", "AI_CHAT_VOICE_HOURLY_MAX")),
        ("引擎与模型", "本机 GPT-SoVITS 整合包。", ("FURINA_TTS_ENABLED", "FURINA_TTS_ENGINE_DIR", "FURINA_TTS_GPT", "FURINA_TTS_SOVITS",
                                              "FURINA_TTS_PORT", "FURINA_TTS_SAMPLE_STEPS")),
        ("音质", "吞尾字、气声重时调这里。", ("FURINA_TTS_SOFTEN", "FURINA_TTS_TAIL_PAD_MS")),
        ("四种语气", "同一个模型，换参考音频就换语气。", ("FURINA_TTS_EMOTIONS", "FURINA_TTS_REF_WAV", "FURINA_TTS_REF_TEXT",
                                                "FURINA_TTS_REF_PROUD_WAV", "FURINA_TTS_REF_PROUD_TEXT", "FURINA_TTS_REF_DAILY_WAV",
                                                "FURINA_TTS_REF_DAILY_TEXT", "FURINA_TTS_REF_TSUN_WAV", "FURINA_TTS_REF_TSUN_TEXT")),
        ("常开与省内存（打游戏 / 跑 AI 时让路）", "内存够就一直开着；紧张了主动关掉，这期间语音改发文字，不影响聊天。",
         ("FURINA_TTS_KEEP_ALIVE", "FURINA_TTS_STOP_FREE_RAM_GB", "FURINA_TTS_STOP_FREE_COMMIT_GB", "FURINA_TTS_STOP_FREE_VRAM_GB",
          "FURINA_TTS_PAUSE_FOR_GAMES", "FURINA_TTS_GAME_PROCESSES", "FURINA_TTS_MIN_FREE_RAM_GB",
          "FURINA_TTS_MIN_FREE_COMMIT_GB", "FURINA_TTS_MIN_FREE_VRAM_GB", "FURINA_TTS_IDLE_MINUTES")),
    ],
    "bili": [
        ("刷视频", "没字幕（听不到内容）的视频优先换掉，实在要看也只点赞、不评论不发弹幕。", (
            "BILI_ENABLED", "BILI_DRY_RUN", "BILI_HOURS", "BILI_ROUNDS_DAILY", "BILI_ROUND_GAP_MINUTES",
            "BILI_VIDEOS_PER_ROUND", "BILI_WATCH_REPORT", "BILI_MIN_VIEWS", "BILI_MIN_AGE_HOURS", "BILI_INTERESTS", "BILI_ROAM_TAGS",
            "BILI_ROAM_PROBABILITY", "BILI_LEARN_SLANG")),
        ("点赞 / 弹幕 / 评论 / 动态", "做不做由她自己决定；上限 0 = 不限。", (
            "BILI_LIKE_ENABLED", "BILI_DANMAKU_ENABLED", "BILI_COMMENT_ENABLED", "BILI_DYNAMIC_ENABLED", "BILI_LIKE_DAILY_MAX",
            "BILI_DANMAKU_DAILY_MAX", "BILI_COMMENT_DAILY_MAX", "BILI_DYNAMIC_DAILY_MAX", "BILI_DYNAMIC_GAP_HOURS", "BILI_DYNAMIC_HOURS")),
        ("评论区回复", "有人在她的评论底下回她时。", ("BILI_REPLY_ENABLED", "BILI_REPLY_DAILY_MAX", "BILI_REPLY_CHECK_MINUTES",
                                         "BILI_REPLY_HOURS", "BILI_REPLY_EMOTES", "BILI_REPLY_LIKE", "BILI_REPLY_MAX_AGE_HOURS")),
        ("主人（只在 B 站）", "", ("BILI_MENTION_NAME", "BILI_PARTNER_MID", "BILI_MENTION_PROBABILITY", "BILI_AT_PARTNER",
                                "BILI_HOMEWORK_ENABLED", "BILI_HOMEWORK_DAILY_MAX")),
        ("答应关注群友", "群里有人让她关注 TA 的 B 站时。", ("BILI_FOLLOW_ENABLED", "BILI_FOLLOW_DAILY_MAX")),
        ("叫她去刷B站", "群里叫她去刷 B 站时。", ("BILI_AWAY_ENABLED", "BILI_AWAY_MINUTES")),
        ("口味会变", "看过的东西会改变她想看什么；本页能看到她最近在迷什么。", ("BILI_TASTE", "BILI_TASTE_PROBABILITY", "BILI_TASTE_HALF_DAYS")),
        ("分享到群 / 风控", "", ("BILI_SHARE_PROBABILITY", "BILI_SHARE_DAILY_MAX", "BILI_RISK_PAUSE_HOURS")),
        ("直播（她当主播）", "你在直播姬开播，她就上场听弹幕聊天；QQ 私聊她「闭麦」「开麦」「直播状态」。", (
            "BILI_LIVE_ENABLED", "BILI_LIVE_ROOM", "BILI_LIVE_VOICE", "BILI_LIVE_RATE", "BILI_LIVE_PITCH", "BILI_LIVE_REPLY_GAP",
            "BILI_LIVE_IDLE_MINUTES", "BILI_LIVE_THANK_GIFTS", "BILI_LIVE_WELCOME", "BILI_LIVE_NOTIFY", "BILI_LIVE_LABEL",
            "BILI_LIVE_AVATAR", "BILI_LIVE_BLOCK_WORDS")),
    ],
}


def _cards(name: str) -> list:
    title, desc, fields = GROUPS[name]
    by_key = {f[0]: f for f in fields}
    cards, used = [], set()
    for c_title, c_desc, keys in SECTIONS.get(name, ()):
        picked = [by_key[k] for k in keys if k in by_key]
        used.update(f[0] for f in picked)
        if picked:
            cards.append((c_title, c_desc, picked))
    rest = [f for f in fields if f[0] not in used]
    if rest:
        cards.append((title if not cards else "其他", desc if not cards else "", rest))
    return cards


def classic_cards() -> list:
    """[(标题, 说明, [字段...]), ...]：每组一张卡（旧接口）。"""
    return [(title, desc, list(fields)) for title, desc, fields in GROUPS.values()]


def classic_pages() -> list:
    """经典面板设置中心用：每组一页 [(key, 导航标题, 导航副标题, 页面说明, 卡片...)]，与网页面板共用同一份字段。"""
    return [(name, title, NAV.get(name, ""), desc, _cards(name)) for name, (title, desc, _f) in GROUPS.items()]


def _fmt(kind: str, value) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind == "int":
        return str(int(value))
    if kind in ("dec", "pct"):
        v = float(value)
        return str(int(v)) if v == int(v) else f"{v:.4f}".rstrip("0").rstrip(".")
    return str(value)


def get_group(name: str) -> dict:
    if name not in GROUPS:
        raise ValueError(f"没有这组设置：{name}")
    title, desc, fields = GROUPS[name]
    env = envfile.values()
    out = []
    for key, label, kind, default, help_text, opts in fields:
        raw = env.get(key)
        out.append({"key": key, "label": label, "type": kind, "default": default, "help": help_text,
                    "min": opts.get("min"), "max": opts.get("max"), "suffix": opts.get("suffix", ""),
                    "decimals": opts.get("decimals", 1), "value": _parse(kind, raw, default)})
    return {"name": name, "title": title, "desc": desc, "fields": out}


def _parse(kind: str, raw, default):
    if raw is None or str(raw).strip() == "":
        return default
    s = str(raw).strip()
    try:
        if kind == "bool":
            return s.lower() in ("1", "true", "yes", "on")
        if kind == "int":
            return int(float(s))
        if kind in ("dec", "pct"):
            return float(s)
    except ValueError:
        return default
    return s


def save_group(name: str, values: dict) -> dict:
    if name not in GROUPS:
        raise ValueError(f"没有这组设置：{name}")
    _title, _desc, fields = GROUPS[name]
    updates = {}
    for key, label, kind, default, _h, opts in fields:
        if key not in values:
            continue
        v = values[key]
        if kind == "bool":
            v = bool(v)
        elif kind in ("int", "dec", "pct"):
            try:
                v = int(float(v)) if kind == "int" else float(v)
            except (TypeError, ValueError):
                raise ValueError(f"「{label}」要填数字")
            lo, hi = (0.0, 1.0) if kind == "pct" else (opts.get("min"), opts.get("max"))
            if lo is not None and v < lo or hi is not None and v > hi:
                raise ValueError(f"「{label}」要在 {lo}~{hi} 之间")
        else:
            v = str(v or "").strip().replace("\n", " ")
            if len(v) > 500:
                raise ValueError(f"「{label}」太长了")
        updates[key] = _fmt(kind, v)
    if updates:
        envfile.set_values(updates)
    return get_group(name)
