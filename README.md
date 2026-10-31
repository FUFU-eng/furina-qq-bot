<div align="center">

# furina-qq-bot

一个住在 QQ 群里的芙宁娜

[![license](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)
[![python](https://img.shields.io/badge/python-3.11+-3776AB)](https://www.python.org)
[![nonebot](https://img.shields.io/badge/NoneBot-2.5-EA5252)](https://nonebot.dev)
[![onebot](https://img.shields.io/badge/OneBot-v11-black)](https://onebot.dev)

</div>

最早只是想在自己群里放个能聊天的芙芙，后来群友要什么就加什么，搜图、强化、Pixiv、米游社、JM……一路堆到了现在这个样子。中间整个重写过一次，这是第二版。

代码是给我们群写的，很多默认值和说法都带着我们群的习惯，开源出来主要是给想做类似东西的人参考。直接拿去用也行，但大概率要按你自己的群改一改，尤其是人设。

基于 [NoneBot2](https://github.com/nonebot/nonebot2) + OneBot v11。QQ 端可以用 [NapCat](https://github.com/NapNeko/NapCatQQ)，也可以用 SnowLuma，`.env` 里 `QQ_TRANSPORT` 选一个就行（见下面「QQ 端」）。

## 她能干什么

**聊天**

这是花时间最多的部分，目标是让她像个群友，而不是一个问答机器。

她有四种模式，可以整体选，也可以每个群单独选（`AI_CHAT_MODE` / `AI_CHAT_MODE_GROUPS`，面板「模式与内核」里点一下就行）：

- **一代仿真**（默认）：先判断要不要回，再回复；没人叫她也会随机接话、主动冒泡
- **二代仿真**：换成 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（DSH）智能体内核。群消息先攒着，满足「醒来条件」（被艾特、被叫名字、有人回复她、她刚说完话有人接、隔一阵子自己看一眼等，每条都能单独开关）她才醒过来看一眼未读，然后自己决定：说一句还是几句、要不要贴个表情回应、发表情包、还是继续潜水。比一代更像真人，也更费 token，所以加了一个上限：群会话超过一定长度（默认约 6 万 token）就换一个新会话，只带上最近几十条聊天，长期记忆不受影响
- **聊天**：最朴素的一问一答
- **Agent**：主人私聊派活，她在电脑上帮你跑命令、查资料、发文件，做完把结果发回来。二代仿真的群里主人明确派活时，她也会自己转给 Agent。默认关（`AI_CHAT_AGENT_ENABLED`）

不管哪种模式，下面这些都在：

- 艾特她会回，没艾特时也会偶尔自己插一句（默认 2%）
- 有心情，会被夸开心、被怼生气，心情会影响说话的语气
- 记得群友：谁说过什么、谁拜托过她什么，过几天会主动问起
- 会自己找群主聊天，深夜会少说话
- 有边界感：除了群主和设置里「特别关照的人」，其他人最多抱抱。亲亲会被警告，三次就不理你了，认错求情能原谅，但一次比一次难
- 戳一戳、发表情包、看图、回语音（语音要自己准备 GPT-SoVITS 模型）
- 群里的梗和黑话有一张小词典，她能听懂一部分
- 可以联网查东西，但不会跟你说「我搜了一下」
- 回复里不打句号。这条是我专门加的，加了以后真的顺眼很多
- 认识常见的网络梗，别人玩梗她就用梗接，一句话，不解释
- 会烦：一个人短时间内一直艾特她，「烦躁值」会涨，说话越来越敷衍，过一阵自己消气。面板里能看到她现在烦谁
- 有口味：B 站刷到的视频、群里聊过的东西会留下观后感，慢慢形成自己喜欢和不喜欢的东西，口味还会随时间变淡、改变
- 不说 AI 腔：一些很像 AI 的套路句式是禁用的（比如「这 X 我服」），写出来会被重写或删掉，也可以在 `AI_CHAT_BANNED_PATTERNS` 里自己加
- 让她「少说话」是真的会少说；不会把内部的 JSON、括号标记发到群里；引用回复只偶尔用
- 新人进群会艾特欢迎一句，可以有点调皮
- 发言有限流：每分钟、每小时都有上限（按群算），面板里一键开关；每人每天艾特次数用完时，她会用自己的口吻说一句再停，不会突然不理人
- 省钱：可以接一个本机的免费模型服务（OpenAI 兼容接口）当第一线路，失败了自动换回付费模型（`AI_CHAT_FREE_*`）
- 知道自己花了多少钱：最近 24 小时花得越多，她越懒得「没人叫也开口」，被艾特、被叫名字照样回。可以设每天上限和每个群每天上限（`AI_CHAT_DAILY_CAP_YUAN` / `AI_CHAT_GROUP_DAILY_CAP_YUAN`），超了就只回叫到她的。二代仿真每一轮花了多少，面板「模型花费」里按群按天都能看到
- 说话算话：她随口答应的「等下发你」「明天帮你看看」会记下来，到点自己想起来去兑现；群友让她「半小时后叫我」「明天提醒小明交作业」，她也会真记下来，到点艾特人提醒（`AI_CHAT_COMMITMENTS`，默认开）
- 引用不再失忆：群友引用一条很早的消息，或者机器人重启、QQ 重登之前的消息，她也知道引的是哪句话（每个群自己存了一份最近的消息原文）
- 查梗：「X 是什么梗」先翻自己的梗本子，再查萌娘百科，最后才上网搜，查到的会记住
- 认真做文件：让她「写一份 / 整理一份 / 做个清单」，她会开深度思考写好，当群文件发出来（功能权限里的「认真做文件」）
- 合并转发里的图她也能看到，嵌套的合并转发会一层层展开
- 一些整活功能：群友起哄时查岗主人的电脑桌面（会跳过密码类窗口，面板里能关）、真没余额了自动截充值页喊你充钱、给群友记「小本本」起外号翻黑历史

人设在 `furina/plugins/chat/persona.py`，很长，可以整个换掉。

**图片**

- 搜图：用 Google Lens 和 Yandex 识图，再到原站找高清原图；有本地 Eagle 图库的话先在库里找
- 强化：Real-ESRGAN 放大，私聊发图给她会自动强化再发回来
- Pixiv：每日榜单推送，说「来张 xx」会去 Pixiv 找图
- 图片筛选：把群里发的图收起来，在手机或浏览器上左右滑着筛，留下的丢进 Eagle 的自动导入文件夹

**其他**

- 米游社资讯推送、全角色一图流攻略
- 原神 / 星铁 / 绝区零 / 崩坏3 的账号查询，走 [gsuid_core](https://github.com/Genshin-bots/gsuid_core)，我自己的插件放在 `extras/gsuid_FurinaMys`
- `/jm 车号` 下载后合成 PDF 发群文件
- 定时提醒、每周封神榜（群聊发言排行）、群主发图转发到别的群
- B 站：给她一个小号，她会自己刷视频、点赞、发弹幕、回评论区、发动态、关注喜欢的 UP 主。在群里说「去刷 B 站了」就真的离开十分钟，回来还会跟你聊看到了什么。碰到风控会立刻停
- 看 B 站视频：群友甩来一个视频，她会读字幕、弹幕、热评，再用 B 站自带的预览图拼一张带编号和时间码的 3×3 分镜九宫格「看」画面（不下载视频），然后再吐槽
- B 站画师图片：在面板或群里登记几个 UP 主（全群共用一份名单），每 20 分钟看一次有没有发新图，有就推到开了这个权限的群。只读 B 站，不会替你关注或发动态（`BILI_PICS_*`）
- B 站直播：她可以当主播，见下面
- 群友活跃统计：谁用得多一目了然

## B 站直播

让她在你自己的直播间里当 AI 主播，没人看着也能一直播：

- 读弹幕，每轮挑 1~3 条回，醒目留言和上舰一定会回，也会谢礼物、欢迎进来的人
- 回复用 AI 语音念出来（[edge-tts](https://github.com/rany2/edge-tts) 的微软免费女声），同时在舞台网页上出字幕
- 舞台网页是 `http://127.0.0.1:<机器人端口>/live/stage`，上面有立绘、字幕和「AI 虚拟主播」标识，用直播姬的「浏览器」源采进去就行
- 有屏蔽词，回复频率有上限（默认约 10 秒一条）
- QQ 里对她说「闭麦」「开麦」「直播状态」可以控制；面板「B站生活 → 设置 → 直播」里能改

打开方法：`.env` 里 `BILI_LIVE_ENABLED=true`，`BILI_LIVE_ROOM` 填直播间号（或者设好 `BILI_PARTNER_MID`，自动找你的直播间），重启机器人。

注意：

- 粉丝少于 5000 的账号不能用 OBS 推流，只能用 B 站的「直播姬」（电脑版）、App 或网页开播
- 开播要实名，AI 主播要按 B 站规定标明是 AI，舞台上的标识别去掉
- 直播用的是通用 AI 女声，**不是**芙宁娜原声。用游戏角色的原声或克隆声音直播有侵权风险，别这么干

## 控制面板

双击 `打开控制面板.bat`。

所有功能都能在面板里开关，按群、按人单独设置，改完马上生效，不用重启机器人。还能看她最近的心情、聊天记录、B 站刷了什么、每天调了多少次模型、花了多少钱。

桌面版用的是 PySide6，最近整个重新排过版：壁纸只放在主页，其它页面都是实色底，字清楚多了；每页顶上有标题，长页面整页滚动，不再在卡片里塞小滚动框。设置窗口按「账号与模型 / 聊天 / 功能 / 系统」分组，设置项多的页面可以折叠，右上角一键全部展开，左上角能直接搜任何一项设置。

没装 PySide6 的话用 `打开控制面板(浏览器模式).bat`，会在浏览器里打开网页版。

## 跑起来

需要：

- Windows（我只在 Windows 上跑过，路径、浏览器之类的地方默认都按 Windows 写的）
- Python 3.11 以上
- 一个 OneBot v11 实现：NapCat 或 SnowLuma
- 一个 OpenAI 兼容的模型接口，我用的是 DeepSeek
- （想用二代仿真的话）能装 Python 包的网络，见下面

```bash
git clone https://github.com/FUFU-eng/furina-qq-bot.git
cd furina-qq-bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

打开 `.env`，至少填这几项：

| 键 | 说明 |
| --- | --- |
| `BOT_QQ` | 机器人的 QQ 号 |
| `AI_CHAT_GROUP_OWNER_QQ` | 你自己的 QQ 号 |
| `AI_CHAT_LLM_API_KEY` | 模型接口的 key |
| `AI_CHAT_LLM_BASE_URL` / `AI_CHAT_LLM_MODEL` | 接口地址和模型名 |

然后接上 QQ 端（见下面「QQ 端」），再启动：

```bash
python bot.py
```

群里艾特她说句话，有回应就是通了。

### QQ 端

`.env` 里的 `QQ_TRANSPORT` 决定用哪个，默认 `napcat`。

**NapCat**：在 NapCat 里加一个反向 WebSocket，地址填 `ws://127.0.0.1:8080/onebot/v11/ws`（端口跟 `.env` 里的 `PORT` 一致）。

**SnowLuma**：

1. `.env` 里设 `QQ_TRANSPORT=snowluma`，`SNOWLUMA_DIR` 指向 SnowLuma 的目录，再填两个至少 32 位的随机令牌：`SNOWLUMA_HTTP_TOKEN` 和 `ONEBOT_V11_ACCESS_TOKEN`
2. 面板里启动 SnowLuma，它会按 `.env` 写好 SnowLuma 的 OneBot 配置（HTTP 接口默认 `127.0.0.1:13000`，反向 WS 连机器人）
3. 在 SnowLuma 自己的本地管理页里登录 QQ，然后回面板确认是哪个号

确认之前机器人什么都不收、什么都不发（`SNOWLUMA_ACCOUNT_CONFIRMED`），防止接错号。SnowLuma 的历史消息默认只用本地记录，想让它去服务器补拉就开 `SNOWLUMA_HISTORY_SYNC`。

剩下的设置不用去翻 `.env`，打开面板慢慢调就行。

### 想用二代仿真

1. `.env` 里设 `AI_CHAT_DSH_RUNTIME_DIR`：DSH 运行环境装在哪（要几百 MB，放哪个盘都行，默认是 `G:\FurinaDshRuntime`，没有 G 盘一定要改）
2. 打开面板「模式与内核」，点「安装 / 修复运行时」，它会在那个目录里建一个独立的 Python 环境，装好官方的 `deepseek-harness-sdk`
3. 把模式切成「二代仿真」（可以先只给测试群开：`AI_CHAT_MODE_GROUPS`）

DSH 只在用到时才在后台启动，空闲一段时间自己退出；只监听本机，请求要带 `data/agent/ipc.key` 里的密钥。内核出问题时默认自动退回一代仿真（`AI_CHAT_DSH_FALLBACK`）。

省钱相关的两个设置：`AI_CHAT_DSH_CONTEXT_MAX_K`（群会话最长多少，单位千 token，默认 60，0 = 不限）和 `AI_CHAT_DSH_RECAP_LINES`（换新会话时带上最近几条，默认 30）。DSH 自己的压缩要等上下文快 80 万 token 才动手，不设上限的话一天能烧掉十几块钱。

### 可选的外部依赖

这些东西太大，没放进仓库，用哪个功能就准备哪个，放到 `.env` 里 `ASSETS_ROOT` 指向的目录：

- 图片强化：[Real-ESRGAN ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN)
- 语音：[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 和芙宁娜的模型，还有 ffmpeg
- 搜图：Edge 浏览器（Selenium 驱动），有些站点要自己登录后导出 cookies
- 二代仿真：DSH 运行环境，面板里一键安装（见上面）
- 直播：B 站直播姬；语音用的 edge-tts 已经在 requirements 里，要联网
- 米游社查询：gsuid_core，按它的文档装好后把 `extras/gsuid_FurinaMys` 放进它的 plugins 目录
- 图片筛选外网访问：Cloudflare Tunnel，不需要外网就不用管

## 目录

```
bot.py              启动入口
furina/
  core/             配置读取、数据库、权限、模型调用这些公共的东西
  plugins/          每个功能一个插件，chat 是聊天，bililive 是直播，bili_pics 是 B 站画师图片
  services/         语音、搜索、B 站客户端、图片筛选、查梗、做文件等
panel/              控制面板（PySide6 桌面版 + 网页版）
tests/              测试
tools/              dsh_worker.py 和 dsh_bridge/ 是二代仿真的内核进程和工具桥；其余是从旧版迁移数据用的脚本，一般用不到
extras/             gsuid_core 插件
```

数据都在 `data/furina.db`（SQLite），日志在 `logs/`，这两个目录不进仓库。

## 测试

```bash
python -m unittest discover -s tests -t .
```

测试不联网、不调模型，也不会碰真实数据，现在一共 812 个。

## 一些说明

- B 站功能请用小号（直播除外，直播用你自己的号）。会不会被风控、会不会被封号我没法保证
- 二代仿真和 Agent 模式比一代费钱，开之前看一眼面板里的「模型花费」
- Agent 模式能在你电脑上执行命令，只认主人，危险操作要谨慎；不需要就别开
- JM 功能请自己判断要不要开，默认是关的
- 芙宁娜是米哈游《原神》里的角色，相关版权归米哈游所有，这个项目跟米哈游没有任何关系
- 语音模型不在仓库里。我用的是 B 站 红血球AE3803 和 白菜工厂1145号员工 训练的模型，协议是 CC BY-NC，不能商用
- 代码注释几乎都是中文，有些地方写得比较随意，见谅

有问题或者想法可以开 Issue。

## 感谢

- [NoneBot2](https://github.com/nonebot/nonebot2)、[NapCat](https://github.com/NapNeko/NapCatQQ)、SnowLuma
- [gsuid_core](https://github.com/Genshin-bots/gsuid_core)、[GenshinUID](https://github.com/KimigaiiWuyi/GenshinUID)
- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)、[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)
- [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)、[edge-tts](https://github.com/rany2/edge-tts)
- [dsh-fish-tone](https://github.com/MX3a48c5/dsh-fish-tone)，说话手感那部分参考了它
- 社区里那些大肥鱼相关的项目和讨论，活人感很多想法是看了它们才有的
- 群友们，提需求、找 bug、陪她聊天

## License

[MIT](./LICENSE)
