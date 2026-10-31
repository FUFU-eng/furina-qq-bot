"""功能列表 / 指令大全 / 日志页的界面搭建（PanelWindow 的 mixin，由 window.py 拆出）。"""
from ..common import *  # noqa: F401,F403
from ..widgets import *  # noqa: F401,F403
from panel.services.logs import transport_name


class InfoPagesMixin:
    def _build_feature_page(self):
        from PySide6.QtWidgets import QScrollArea
        page = QWidget()
        page.setObjectName("stackPage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(14)

        groups = [
            ("芙宁娜专属特权 · 全权电脑掌控", [
                ("七分傲三分娇 · 抖S反杀", "融合原版芙宁娜的戏剧张力与群友活人感，遇到开黄腔/互损直接高傲毒舌反杀，绝不冷落拒聊，回复条数完全自由", "人设"),
                ("查岗主人桌面", "群友起哄或芙芙好奇时，静默查看主人当前打开的窗口/游戏并截桌面图发群里吐槽（自动过滤密码与隐私窗口）", "查岗"),
                ("抖S「记小本本」", "给顶嘴或出糗的群友起绰号、记黑历史，随时翻旧账嘲笑 · 角色管理 → 📕 芙芙的小本本", "小本本"),
                ("余额救急自动截图", "接口真没钱（402）时自动截 DeepSeek 充值页图发群里并 @主人 撒娇喊充钱", "救急"),
                ("全权操作电脑 & 画图截网页", "群聊/私聊随时翻 Eagle 足迹图库、截任意网页、手绘图片、执行电脑命令与收发本地文件", "全权"),
                ("角色人设热更新", "在「角色管理」页修改或同步提示词后点击保存，无需重启，下一条消息立即生效", "热更新"),
            ]),
            ("AI 智能", [
                ("AI 聊天", "群聊接话 · 艾特回复 · 多轮话题锁定与记忆系统", "聊天"),
                ("群聊总结", "引用消息或自然语言触发，汇总最近群聊", "总结"),
                ("模糊指令", "艾特 / 发图 / 精华 / 搜图 / 强化，自然语言识别", "智能"),
                ("看图聊天", "发图或引用图片追问，结合最近的图片理解上下文", "视觉"),
                ("表情回应", "偶尔给群消息贴表情；她记得自己贴过什么、戳过谁，被问会承认", "互动"),
                ("联网搜索", "不知道就悄悄上网查（必应 + DuckDuckGo，自动过滤不相关结果），看完像本来就知道一样说 · 设置 → 联网搜索", "搜索"),
            ]),
            ("活人感", [
                ("有自己的生活", "知道自己此刻在干嘛、最近在 B 站刷到了什么，聊天时自然提起", "生活"),
                ("热梗与群梗", "看懂网络热梗和本群的梗，也会跟着用", "梗"),
                ("说话手感", "大肥鱼式：句中停顿、结巴、改口、先蹦反应字；认真时收起玩笑 · 设置 → 活人感", "手感"),
                ("去 AI 味", "删掉客服腔；自称 AI、markdown 标题时让她重说", "去AI味"),
            ]),
            ("语音", [
                ("语音消息", "芙宁娜的声音（本机 GPT-SoVITS）· 神气 / 日常 / 傲娇 / 平静四种语气按情境换", "语音"),
                ("高傲语音反击", "打出「哼、杂鱼、就这、才没有、小蛋糕、充钱」等抖S反杀或撒娇台词时语音触发率大幅提升并自动唤醒引擎", "反杀"),
                ("省内存", "只在要发语音时开引擎、闲置 10 分钟关；打游戏或内存紧张时自动让路", "省内存"),
            ]),
            ("B站生活", [
                ("自己刷 B 站", "小号定时刷视频，读简介、字幕、弹幕和热评 · 左侧「B站生活」实时看", "冲浪"),
                ("点赞 / 弹幕 / 评论 / 动态", "做不做全看她自己的意愿，默认不限；遇到风控立刻暂停", "互动"),
                ("评论区回复", "有人回她的评论会回（含楼中楼、B站表情），在 B 站叫你主人", "回复"),
                ("B站刷到分享", "刷到好玩的视频分享到群里", "分享"),
            ]),
            ("图像处理与资源", [
                ("以图搜图", "Google 相似图检索，引用即可触发", "搜索"),
                ("图片强化", "高清重绘与放大，单张 / 批量共享队列", "图像"),
                ("指令发图", "发 N 张图，自动从图库随机发送", "图库"),
                ("Pixiv ID 取图", "发送 #p作品ID 或 pixiv 作品ID 直接抓原图", "取图"),
                ("JM 下载", "发送 jm 车号 自动下载漫画打包发送并支持本地缓存", "资源"),
            ]),
            ("米游社与群聊推送", [
                ("米游社签到", "GsCore 官方米游社扫码与四游签到 · 账号、时间和订阅在官方控制台管理", "签到"),
                ("米游社资讯", "原神 / 星铁 / 崩坏3 / 绝区零 官方论坛新帖与兑换码自动推送", "资讯"),
                ("Pixiv 插画", "日/周/月榜前150名分批发送 · 角色/游戏点图", "画廊"),
                ("定时提醒", "一次性 / 每天 / 每周 · 自然语言 · 离线30分钟内补发", "提醒"),
                ("封神榜", "每周三 / 日群聊趣味封神统计", "统计"),
            ]),
        ]
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        v = QVBoxLayout(host)
        v.setContentsMargins(2, 2, 8, 2)
        v.setSpacing(14)

        for gname, items in groups:
            card = QFrame()
            card.setObjectName("featureGroup")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(16, 14, 14, 12)
            cv.setSpacing(7)
            cv.addWidget(self._label(gname, "featGroupTitle", 12))
            for k, (fname, fdesc, ftag) in enumerate(items):
                if k:
                    sep = QFrame()
                    sep.setObjectName("featSep")
                    sep.setFixedHeight(1)
                    cv.addWidget(sep)
                row = QFrame()
                row.setObjectName("featureRow")
                rh = QHBoxLayout(row)
                rh.setContentsMargins(10, 8, 10, 8)
                rh.setSpacing(12)
                txt = QVBoxLayout()
                txt.setSpacing(2)
                txt.addWidget(self._label(fname, "featName", 13))
                txt.addWidget(self._label(fdesc, "featDesc", 11))
                rh.addLayout(txt, 1)
                tag = self._label(ftag, "featTag", 10)
                tag.setAlignment(Qt.AlignVCenter)
                rh.addWidget(tag, 0, Qt.AlignVCenter)
                cv.addWidget(row)
            v.addWidget(card)
        v.addStretch(1)

        scroll.setWidget(host)
        outer.addWidget(scroll, 1)
        return page

    def _build_commands_page(self):
        from PySide6.QtWidgets import QScrollArea
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        command_groups = [
            ("GsCore", "官方游戏服务", "#82d6cf", "", []),
            (
                "AI 聊天",
                "AI 聊天 · 视觉看图 · 群聊总结",
                "#ffd166",
                "支持当前角色昵称唤醒或直接 @机器人",
                [
                    ("日常艾特对话", "艾特机器人或在句子里带上角色称呼即可对话，自带好感度、心情与上下文记忆", "需 @ 或称呼", ["@芙芙 今天吃什么好", "芙芙 在吗"]),
                    ("看图聊天与追问", "发送图片同时艾特机器人，或引用群内图片艾特提问；也可对最近 5 分钟的图直接问「这谁/这是啥」", "带图/引用 + @", ["[引用图片] @芙芙 这张图里是谁"]),
                    ("群聊智能总结", "自动梳理并吐槽群内最近 100 条聊天记录的重点话题与群友名场面（同群冷却 120 秒）", "需 @机器人", ["@芙芙 总结", "@芙芙 总结一下"]),
                    ("双击头像戳一戳", "在群内双击机器人头像触发「戳一戳」，随机掉落专属性格台词回应", "双击头像", ["双击机器人头像"]),
                ],
            ),
            (
                "搜图强化",
                "以图搜图 · AI 高清强化",
                "#b48cf2",
                "先引用目标图片或合并转发，再 @机器人",
                [
                    ("以图搜图找原图", "引用一张图片并艾特说「搜图」，自动检索推特(X)/Pixiv/Danbooru 画师出处并打包发回原画质大图", "引用图片 + @", ["[引用图片] @芙芙 搜图", "[引用图片] @芙芙 找原图"]),
                    ("单张图片高清强化", "引用一张图片并艾特说「强化」或「超分」，自动进行 AI 降噪重绘与超分辨率放大", "引用图片 + @", ["[引用图片] @芙芙 强化", "[引用图片] @芙芙 超分"]),
                    ("合并转发批量强化", "引用一条含多张图片的合并转发聊天记录并艾特说「强化」，按队列批量放大全部图片", "引用转发 + @", ["[引用合并转发] @芙芙 强化"]),
                ],
            ),
            (
                "插画图库",
                "Pixiv 点图 · PID 取图 · 本地图库",
                "#ec4899",
                "需 @机器人 触发，可在功能权限中按群开关",
                [
                    ("Pixiv 角色/作品点图", "指定角色名或作品名点图（单次 1~10 张），自动从 Pixiv 筛选高收藏插画发送", "需 @机器人", ["@芙芙 来张芙宁娜的图", "@芙芙 来3张星穹铁道插画"]),
                    ("Pixiv 作品 ID 取图", "直接输入 Pixiv 作品数字 ID（PID），精准提取原图（多页插画自动打包合并转发）", "需 @机器人", ["@芙芙 pid113456789", "@芙芙 p站id 113456789"]),
                    ("本地图库随机发图", "不带具体角色名时，从本地 Eagle 精选图库随机抽取 1~10 张美图或表情包发送", "需 @机器人", ["@芙芙 来两张图", "@芙芙 发张壁纸"]),
                ],
            ),
            (
                "JM 漫画",
                "JM 漫画整本下载",
                "#f59e0b",
                "群聊直接发送或 @机器人 均可触发",
                [
                    ("车号下载合成 PDF", "输入车号即可下载整本漫画并自动合成为 PDF 上传至 QQ 群文件（已下过的本子自动秒发缓存）", "直接发或 @", ["/jm 350234", "jm 350234"]),
                ],
            ),
            (
                "定时提醒",
                "群内个人定时提醒",
                "#14b8a6",
                "需 @机器人 触发，创建前会先列出方案供确认",
                [
                    ("创建一次性提醒", "用自然语言说出提醒时间与事项，机器人生成方案后回复「确认提醒」即可生效", "需 @机器人", ["@芙芙 20分钟后提醒我收菜", "@芙芙 明晚8点叫我打周本"]),
                    ("创建周期重复提醒", "支持每天、每周、每月固定时间循环提醒；机器人短暂离线 30 分钟内会自动补发", "需 @机器人", ["@芙芙 每天上午9点提醒我喝水", "@芙芙 每周日15点提醒我周常"]),
                    ("确认 / 查看 / 取消提醒", "确认刚才的提醒方案、查看自己在本群的生效提醒列表，或按编号取消提醒", "需 @机器人", ["@芙芙 确认提醒", "@芙芙 我的提醒", "@芙芙 取消提醒 1"]),
                ],
            ),
            (
                "群管设定",
                "群管辅助 · 记忆设定 · 定时榜单",
                "#22d3ee",
                "日常群务管理与人设记忆互动",
                [
                    ("引用消息设为群精华", "引用群友的精彩发言并艾特说「设精华」，即可加入群精华消息（需机器人为群管理员）", "引用消息 + @", ["[引用消息] @芙芙 设精华", "[引用消息] @芙芙 加精"]),
                    ("代艾特群友传话", "按群名片或昵称匹配群成员并帮你艾特对方传话", "需 @机器人", ["@芙芙 艾特张三 出来玩"]),
                    ("专属称呼与记忆设定", "用自然语言告诉机器人你的外号、喜好或群内设定，机器人会自动写入长期记忆", "需 @机器人", ["@芙芙 以后叫我旅行者", "@芙芙 记住我喜欢喝冰美式"]),
                    ("自动资讯与封神榜", "开启对应权限后自动运行：米游社官方新帖与前瞻兑换码推送、Pixiv 日周月榜推送、每周三/日 21:30 封神榜", "后台自动定时", ["无需指令（自动推送）"]),
                ],
            ),
        ]

        # 米游社游戏指令：全部由 GsCore 插件 FurinaMys 处理，都要 @机器人；四个官方插件的原始指令只对主人开放。
        # 前缀：原神 ys/原神，星铁 bt/星铁/崩铁，绝区零 zzz/绝区零，崩坏3 bbb/崩坏3/崩三。
        game_groups = [
            (
                "米游社账号", "米游社账号 · 扫码绑定与自动签到", "#38bdf8", "所有指令都要先 @芙芙，不 @ 不会响应",
                [
                    ("扫码绑定全部账号", "群里发米哈游通行证二维码，扫码后自动撤回；一次绑定通行证下原神/星铁/绝区零/崩坏3 全部账号，并开启每日自动签到", "需 @机器人", ["@芙芙 绑定账号"]),
                    ("查看已绑定账号", "一张卡片列出全部游戏账号的昵称、UID、等级、服务器与签到状态", "需 @机器人", ["@芙芙 我的账号"]),
                    ("指令帮助卡片", "发送一张全功能指令卡片（不含 JM）", "需 @机器人", ["@芙芙 指令帮助"]),
                    ("更新展柜", "刷新全部游戏的角色面板数据；加游戏前缀只刷新单个游戏", "需绑定", ["@芙芙 更新展柜", "@芙芙 ys更新展柜"]),
                    ("自动签到与战报", "每天 10:00 自动签到，10:05 在开启「米游社签到」权限的群发签到战报；也可以手动补签", "需绑定", ["@芙芙 米游社签到", "@芙芙 米游社战报"]),
                    ("主人专用：官方完整指令", "四个官方插件的原始指令只对主人开放（不用 @），例如官方帮助图、抽卡记录", "仅主人", ["ys帮助", "bt帮助", "zzz帮助", "bbb帮助"]),
                ],
            ),
            (
                "原神", "原神 · GenshinUID", "#f2b544", "前缀 ys 或 原神，先 @芙芙 再说指令",
                [
                    ("体力与每日", "树脂、每日委托、探索派遣、参量质变仪等实时便笺", "需绑定", ["@芙芙 ys每日"]),
                    ("角色面板", "查询自己账号里的角色面板，支持常用别名；展柜里没有会自动刷新一次", "需绑定", ["@芙芙 ys芙宁娜", "@芙芙 ys六命芙宁娜"]),
                    ("一图流攻略", "角色养成一图流攻略", "无需绑定", ["@芙芙 ys芙宁娜攻略"]),
                    ("深渊全套战绩", "一次发出深境螺旋、幻想真境剧诗、幽境危战", "需绑定", ["@芙芙 ys深渊"]),
                    ("今日素材", "今天能刷的天赋书与武器突破材料", "无需绑定", ["@芙芙 ys今日素材"]),
                ],
            ),
            (
                "星铁", "崩坏：星穹铁道 · StarRailUID", "#8b7cf6", "前缀 bt、星铁 或 崩铁，先 @芙芙 再说指令",
                [
                    ("开拓力与每日", "开拓力、每日实训、委托派遣等实时便笺", "需绑定", ["@芙芙 bt每日"]),
                    ("角色面板", "查询自己账号里的角色面板，支持常用别名", "需绑定", ["@芙芙 bt黄泉", "@芙芙 bt流萤"]),
                    ("一图流攻略", "角色养成一图流攻略", "无需绑定", ["@芙芙 bt黄泉攻略"]),
                    ("深渊全套战绩", "一次发出忘却之庭、虚构叙事、末日幻影、异相仲裁、货币战争", "需绑定", ["@芙芙 bt深渊"]),
                ],
            ),
            (
                "绝区零", "绝区零 · ZZZeroUID", "#f97316", "前缀 zzz 或 绝区零，先 @芙芙 再说指令",
                [
                    ("电量与每日", "电量、活跃度、录像店经营、刮刮卡等实时便笺", "需绑定", ["@芙芙 zzz每日"]),
                    ("角色面板", "查询自己账号里的代理人面板，支持常用别名", "需绑定", ["@芙芙 zzz星见雅"]),
                    ("一图流攻略", "代理人养成一图流攻略", "无需绑定", ["@芙芙 zzz星见雅攻略"]),
                    ("深渊全套战绩", "一次发出式舆防卫战、危局强袭战、临界推演、零号空洞", "需绑定", ["@芙芙 zzz深渊"]),
                ],
            ),
            (
                "崩坏3", "崩坏3 · BBBUID", "#ec4899", "前缀 bbb、崩坏3 或 崩三，先 @芙芙 再说指令",
                [
                    ("体力与每日", "体力、每日任务等实时便笺", "需绑定", ["@芙芙 bbb每日"]),
                    ("女武神总览", "账号总览与女武神练度（崩坏3 没有单角色面板）", "需绑定", ["@芙芙 bbb查询"]),
                    ("往世乐土攻略", "按女武神查询往世乐土攻略", "无需绑定", ["@芙芙 bbb人律攻略"]),
                    ("深渊全套战绩", "一次发出超弦空间/量子流形、记忆战场、往世乐土", "需绑定", ["@芙芙 bbb深渊"]),
                ],
            ),
        ]
        command_groups[0:1] = game_groups
        total_cmds = sum(len(g[4]) for g in command_groups)

        # 顶部标题行
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        # 页标题由窗口统一显示，这里只留一句说明
        s = QLabel("没有特别标注的指令都要先 @芙芙 · 点右侧的口令可以直接复制")
        s.setObjectName("pageSubLabel")
        title_box.addWidget(s)
        head.addLayout(title_box)
        head.addStretch(1)
        self.cmd_count_label = QLabel(f"共 {len(command_groups)} 大类 · {total_cmds} 项指令")
        self.cmd_count_label.setObjectName("pageCountLabel")
        head.addWidget(self.cmd_count_label, 0, Qt.AlignVCenter)
        layout.addLayout(head)

        # 搜索与分类筛选工具栏
        toolbar = QFrame()
        toolbar.setObjectName("cmdToolbar")
        tb_layout = QVBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 10, 12, 10)
        tb_layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.cmd_search_input = QLineEdit()
        self.cmd_search_input.setObjectName("searchInput")
        self.cmd_search_input.setPlaceholderText("搜索指令或关键词（例如：深渊、每日、签到、ys、bt、zzz、bbb、搜图、强化、pid、提醒、jm）…")
        self.cmd_search_input.setClearButtonEnabled(True)
        search_row.addWidget(self.cmd_search_input, 1)
        tb_layout.addLayout(search_row)

        cat_row = QHBoxLayout()
        cat_row.setSpacing(6)
        self._cmd_active_cat = "全部"
        self.cmd_cat_group = QButtonGroup(self)
        self.cmd_cat_group.setExclusive(True)
        categories = ["全部"] + [g[0] for g in command_groups]
        # 两行分类：第一行「全部 + 四个游戏 + 米游社签到」，第二行其他群聊功能，避免一行挤满。
        split_at = 1 + len(game_groups)
        row_two = QHBoxLayout()
        row_two.setSpacing(6)
        for row_layout, caption in ((cat_row, "游戏"), (row_two, "功能")):
            cap = QLabel(caption)
            cap.setObjectName("cmdCatCaption")
            cap.setFixedWidth(34)
            row_layout.addWidget(cap)
        for idx, cat_name in enumerate(categories):
            cbtn = QPushButton(cat_name)
            cbtn.setObjectName("cmdCatBtn")
            cbtn.setCheckable(True)
            cbtn.setChecked(idx == 0)
            cbtn.setCursor(Qt.PointingHandCursor)
            cbtn.clicked.connect(lambda checked=False, c=cat_name: self._on_cmd_category_changed(c))
            self.cmd_cat_group.addButton(cbtn, idx)
            (cat_row if idx < split_at else row_two).addWidget(cbtn)
        cat_row.addStretch(1)
        row_two.addStretch(1)
        tb_layout.addLayout(cat_row)
        tb_layout.addLayout(row_two)
        layout.addWidget(toolbar)

        # 滚动内容区
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        vbox = QVBoxLayout(wrap)
        vbox.setContentsMargins(2, 2, 6, 8)
        vbox.setSpacing(12)

        self._cmd_group_records = []
        self._cmd_total_count = total_cmds

        for cat_short, gname, gcolor, ghint, items in command_groups:
            card = QFrame()
            card.setObjectName("featureGroup")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 12, 16, 12)
            cl.setSpacing(6)

            gh = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {gcolor}; font-size: 11px;")
            gt = QLabel(gname)
            gt.setObjectName("featGroupTitle")
            gh_hint = QLabel(ghint)
            gh_hint.setObjectName("pageSubLabel")
            gh.addWidget(dot)
            gh.addWidget(gt)
            gh.addStretch(1)
            gh.addWidget(gh_hint)
            cl.addLayout(gh)

            row_records = []
            for idx, (name, desc, trigger_mode, examples) in enumerate(items):
                sep = None
                if idx > 0:
                    sep = QFrame()
                    sep.setObjectName("featSep")
                    sep.setFrameShape(QFrame.HLine)
                    cl.addWidget(sep)

                row = QFrame()
                row.setObjectName("featureRow")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(8, 8, 8, 8)
                rl.setSpacing(12)

                left = QVBoxLayout()
                left.setSpacing(4)
                title_line = QHBoxLayout()
                title_line.setSpacing(8)
                nl = QLabel(name)
                nl.setObjectName("featName")
                title_line.addWidget(nl)
                if trigger_mode and trigger_mode != "需 @机器人":  # 「需 @机器人」是默认情况，页顶已说明，不再每行重复
                    trig = QLabel(trigger_mode)
                    trig.setObjectName("cmdTriggerTag")
                    title_line.addWidget(trig)
                title_line.addStretch(1)

                dl = QLabel(desc)
                dl.setObjectName("featDesc")
                dl.setWordWrap(True)
                left.addLayout(title_line)
                left.addWidget(dl)

                right = QHBoxLayout()
                right.setSpacing(6)
                for ex in examples:
                    pill = QPushButton(ex)
                    pill.setObjectName("cmdBadgeBtn")
                    pill.setCursor(Qt.PointingHandCursor)
                    pill.setToolTip("点击复制该指令示例")
                    pill.clicked.connect(lambda checked=False, btn=pill, text=ex: self._copy_command_example(btn, text))
                    right.addWidget(pill)

                rl.addLayout(left, 1)
                rl.addLayout(right, 0)
                cl.addWidget(row)

                search_blob = " ".join([cat_short, gname, name, desc, trigger_mode] + list(examples)).lower()
                row_records.append((row, sep, search_blob))

            vbox.addWidget(card)
            self._cmd_group_records.append((cat_short, card, row_records))

        self.cmd_empty_label = QLabel("没有找到匹配的指令，换个关键词试试吧~")
        self.cmd_empty_label.setObjectName("pageSubLabel")
        self.cmd_empty_label.setAlignment(Qt.AlignCenter)
        self.cmd_empty_label.setVisible(False)
        vbox.addWidget(self.cmd_empty_label)
        vbox.addStretch(1)

        scroll.setWidget(wrap)
        layout.addWidget(scroll, 1)

        self.cmd_search_input.textChanged.connect(self._filter_commands_view)
        return page

    def _on_cmd_category_changed(self, category: str):
        self._cmd_active_cat = category or "全部"
        self._filter_commands_view()

    def _filter_commands_view(self):
        q = (self.cmd_search_input.text() if hasattr(self, "cmd_search_input") else "").strip().lower()
        cat = getattr(self, "_cmd_active_cat", "全部")
        shown_cmds = 0
        shown_groups = 0
        for cat_short, card, row_records in getattr(self, "_cmd_group_records", []):
            cat_ok = (cat == "全部" or cat == cat_short)
            visible_in_card = 0
            for row, sep, blob in row_records:
                match = cat_ok and (not q or q in blob)
                row.setVisible(match)
                if sep is not None:
                    sep.setVisible(match and visible_in_card > 0)
                if match:
                    visible_in_card += 1
                    shown_cmds += 1
            card.setVisible(visible_in_card > 0)
            if visible_in_card > 0:
                shown_groups += 1
        if hasattr(self, "cmd_empty_label"):
            self.cmd_empty_label.setVisible(shown_cmds == 0)
        if hasattr(self, "cmd_count_label"):
            total = getattr(self, "_cmd_total_count", shown_cmds)
            if not q and cat == "全部":
                self.cmd_count_label.setText(f"共 {len(self._cmd_group_records)} 大类 · {total} 项指令")
            else:
                self.cmd_count_label.setText(f"筛选结果：{shown_cmds} / {total} 项指令")

    def _copy_command_example(self, btn: QPushButton, text: str):
        clean = text
        for prefix in ("[引用图片] ", "[引用合并转发] ", "[引用消息] "):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
        try:
            QApplication.clipboard().setText(clean)
            orig = btn.property("_orig_text") or text
            btn.setProperty("_orig_text", orig)
            btn.setText("✓ 已复制")
            QTimer.singleShot(1200, lambda b=btn, t=orig: b.setText(t))
            self.add_log("info", f"[指令大全] 已复制指令：{clean}")
        except Exception as e:
            self.add_log("error", f"[指令大全] 复制失败: {e}")

    def _build_log_page(self):
        page = QWidget()
        page.setObjectName("stackPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(self._label("实时查看机器人的运行记录，日志按增量方式加载", "pageSubLabel", 11), 1)
        live = QLabel("● 实时同步")
        live.setObjectName("livePill")
        live.setAlignment(Qt.AlignCenter)
        header.addWidget(live, 0, Qt.AlignTop)
        layout.addLayout(header)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self._tool_button("重启机器人", self.restart_bot))
        action_row.addWidget(self._tool_button("清空日志", self.clear_log))
        action_row.addWidget(self._tool_button("刷新日志", self.refresh_logs))
        action_row.addStretch(1)
        action_row.addWidget(self._label("最多保留最近 3000 行", "surfaceHint", 10), 0, Qt.AlignVCenter)
        layout.addLayout(action_row)

        log_surface = QFrame()
        log_surface.setObjectName("logSurface")
        log_layout = QVBoxLayout(log_surface)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_layout.setSpacing(0)
        self.log_text = QPlainTextEdit()
        self.log_text.setObjectName("logText")
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(3000)
        self.log_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        log_layout.addWidget(self.log_text, 1)
        layout.addWidget(log_surface, 3)

        # 当前 QQ 接入端日志独立卡片：和上方机器人日志上下对照
        napcat_surface = QFrame()
        napcat_surface.setObjectName("logSurface")
        napcat_layout = QVBoxLayout(napcat_surface)
        napcat_layout.setContentsMargins(10, 8, 10, 10)
        napcat_layout.setSpacing(6)
        nap_head = QHBoxLayout()
        active_transport = transport_name()
        nap_head.addWidget(self._label(f"{active_transport} 日志 · 连接 / 登录 / 收发报文", "sectionLabel", 11), 1)
        self.napcat_status = self._label(f"等待 {active_transport} 日志…", "surfaceHint", 10)
        nap_head.addWidget(self.napcat_status, 0, Qt.AlignVCenter)
        napcat_layout.addLayout(nap_head)
        self.napcat_text = QPlainTextEdit()
        self.napcat_text.setObjectName("logText")
        self.napcat_text.setReadOnly(True)
        self.napcat_text.setMaximumBlockCount(1500)
        self.napcat_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.napcat_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        napcat_layout.addWidget(self.napcat_text, 1)
        layout.addWidget(napcat_surface, 2)

        self._napcat_log_path = None
        self._napcat_log_pos = 0
        # 启动时先加载最近一段 bot.log，之后以增量方式流式追加，避免覆盖实时日志
        self._log_file_pos = max(0, self._bot_log_size() - 24000)
        self.log_refresh_timer = QTimer(self)
        self.log_refresh_timer.timeout.connect(self.refresh_logs)
        self.log_refresh_timer.start(2000)
        self.refresh_logs()
        return page

    def _bot_log_size(self):
        try:
            return os.path.getsize(BOT_STDOUT_LOG)
        except Exception:
            return 0
