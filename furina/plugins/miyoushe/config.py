"""米游社官号推送插件配置（从 .env 读取，全部可配）。"""

import os

from ...core import env

_cfg = env.config_ns  # 旧键名，改读 .env

# 插件位于 <项目根>/bot/plugins/miyoushe_watch/
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = ""  # 记录改存 SQLite（docs: files/<文件名>）


def _env_list(name: str, default: str) -> list:
    raw = str(getattr(_cfg, name, "") or default)
    return [p.strip() for p in raw.split(",") if p.strip()]


def _env_bool(name: str, default: str) -> bool:
    return str(getattr(_cfg, name, "") or default).strip().lower() in ("1", "true", "yes", "on")


# 推送目标群（逗号分隔），机器人必须已加入这些群
GROUPS = [int(x) for x in _env_list("miyoushe_groups", "") if x.isdigit()]

# 机器人 QQ 号（合并转发节点里作为发送者显示）

# 轮询间隔（秒）
POLL_INTERVAL = int(getattr(_cfg, "miyoushe_poll_interval", 60))

# 监听的官方论坛 ID（逗号分隔）：28=原神, 53=星穹铁道, 58=绝区零, 6=崩坏三,
# 947=崩坏：因缘精灵, 949=星布谷地
FORUM_IDS = [int(x) for x in _env_list("miyoushe_forums", "28,53,58,6,947,949") if x.isdigit()]

# 每次拉取的帖子数（覆盖最近 30 条，轮询间隔内足够）
PAGE_SIZE = int(getattr(_cfg, "miyoushe_page_size", 30))

# 首次启动是否补推当前已有帖子：true=推；false=只记录当前帖子作为基线，之后只推新帖
BACKFILL = _env_bool("miyoushe_backfill", "false")

# 已读记录文件（记录已推送/已忽略的帖子 id）
SEEN_FILE = os.path.join(BOT_DIR, "miyoushe_seen.json")

# 合并转发上传速率估计文件（重启后保留，标题时机才能继续准确）
RATE_FILE = os.path.join(BOT_DIR, "miyoushe_rate.json")


def __getattr__(name):
    if name == "BOT_QQ":
        return env.bot_qq()
    raise AttributeError(name)
