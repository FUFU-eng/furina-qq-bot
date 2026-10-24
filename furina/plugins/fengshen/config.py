# ============ 群聊封神榜配置区（从 .env 读取） ============
import os

from ...core import env

_config = env.config_ns  # 旧键名，改读 .env

# 总开关
FENGSHEN_ENABLED = str(getattr(_config, "fengshen_enabled", "true")).lower() == "true"

# 要运行封神榜的群（逗号分隔）
FENGSHEN_GROUPS = tuple(
    g for g in str(getattr(_config, "fengshen_groups", "")).replace(" ", "").split(",") if g
)

# 封神日：星期几触发（0=周一, 2=周三, 6=周日），默认周三+周日
FENGSHEN_WEEKDAYS = tuple(
    int(x) for x in str(getattr(_config, "fengshen_weekdays", "2,6")).replace(" ", "").split(",") if x
)

# 封神时间（小时:分钟）
FENGSHEN_HOUR = int(getattr(_config, "fengshen_hour", 21))
FENGSHEN_MINUTE = int(getattr(_config, "fengshen_minute", 30))

# 榜单人数
FENGSHEN_TOP_N = int(getattr(_config, "fengshen_top_n", 5))

# 数据存 SQLite（docs: files/fengshen_data.json）
FENGSHEN_DATA_PATH = "fengshen_data.json"
