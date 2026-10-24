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

基于 [NoneBot2](https://github.com/nonebot/nonebot2) + OneBot v11，QQ 端我用的是 [NapCat](https://github.com/NapNeko/NapCatQQ)。

## 她能干什么

**聊天**

这是花时间最多的部分，目标是让她像个群友，而不是一个问答机器。

- 艾特她会回，没艾特时也会偶尔自己插一句（默认 2%）
- 有心情，会被夸开心、被怼生气，心情会影响说话的语气
- 记得群友：谁说过什么、谁拜托过她什么，过几天会主动问起
- 会自己找群主聊天，深夜会少说话
- 有边界感：除了群主和设置里「特别关照的人」，其他人最多抱抱。亲亲会被警告，三次就不理你了，认错求情能原谅，但一次比一次难
- 戳一戳、发表情包、看图、回语音（语音要自己准备 GPT-SoVITS 模型）
- 群里的梗和黑话有一张小词典，她能听懂一部分
- 可以联网查东西，但不会跟你说「我搜了一下」
- 回复里不打句号。这条是我专门加的，加了以后真的顺眼很多

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
- B 站：给她一个小号，她会自己刷视频、点赞、发弹幕、回评论区、发动态。碰到风控会立刻停
- 群友活跃统计：谁用得多一目了然

## 控制面板

双击 `打开控制面板.bat`。

所有功能都能在面板里开关，按群、按人单独设置，改完马上生效，不用重启机器人。还能看她最近的心情、聊天记录、B 站刷了什么、每天调了多少次模型。

桌面版用的是 PySide6，没装的话用 `打开控制面板(浏览器模式).bat`，会在浏览器里打开网页版。

## 跑起来

需要：

- Windows（我只在 Windows 上跑过，路径、浏览器之类的地方默认都按 Windows 写的）
- Python 3.11 以上
- 一个 OneBot v11 实现，推荐 NapCat
- 一个 OpenAI 兼容的模型接口，我用的是 DeepSeek

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

然后在 NapCat 里加一个反向 WebSocket，地址填 `ws://127.0.0.1:8080/onebot/v11/ws`（端口跟 `.env` 里的 `PORT` 一致），再启动：

```bash
python bot.py
```

群里艾特她说句话，有回应就是通了。

剩下的设置不用去翻 `.env`，打开面板慢慢调就行。

### 可选的外部依赖

这些东西太大，没放进仓库，用哪个功能就准备哪个，放到 `.env` 里 `ASSETS_ROOT` 指向的目录：

- 图片强化：[Real-ESRGAN ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN)
- 语音：[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 和芙宁娜的模型，还有 ffmpeg
- 搜图：Edge 浏览器（Selenium 驱动），有些站点要自己登录后导出 cookies
- 米游社查询：gsuid_core，按它的文档装好后把 `extras/gsuid_FurinaMys` 放进它的 plugins 目录
- 图片筛选外网访问：Cloudflare Tunnel，不需要外网就不用管

## 目录

```
bot.py              启动入口
furina/
  core/             配置读取、数据库、权限、模型调用这些公共的东西
  plugins/          每个功能一个插件，chat 是聊天
  services/         语音、搜索、B 站客户端、图片筛选等
panel/              控制面板（PySide6 桌面版 + 网页版）
tests/              测试
tools/              从旧版迁移数据用的脚本，一般用不到
extras/             gsuid_core 插件
```

数据都在 `data/furina.db`（SQLite），日志在 `logs/`，这两个目录不进仓库。

## 测试

```bash
python -m unittest discover -s tests -t .
```

测试不联网、不调模型，也不会碰真实数据，现在一共 472 个。

## 一些说明

- B 站功能请用小号。会不会被风控、会不会被封号我没法保证
- JM 功能请自己判断要不要开，默认是关的
- 芙宁娜是米哈游《原神》里的角色，相关版权归米哈游所有，这个项目跟米哈游没有任何关系
- 语音模型不在仓库里。我用的是 B 站 红血球AE3803 和 白菜工厂1145号员工 训练的模型，协议是 CC BY-NC，不能商用
- 代码注释几乎都是中文，有些地方写得比较随意，见谅

有问题或者想法可以开 Issue。

## 感谢

- [NoneBot2](https://github.com/nonebot/nonebot2)、[NapCat](https://github.com/NapNeko/NapCatQQ)
- [gsuid_core](https://github.com/Genshin-bots/gsuid_core)、[GenshinUID](https://github.com/KimigaiiWuyi/GenshinUID)
- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)、[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)
- [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)
- 社区里那些大肥鱼相关的项目和讨论，活人感很多想法是看了它们才有的
- 群友们，提需求、找 bug、陪她聊天

## License

[MIT](./LICENSE)
