# ============ 群主发图自动转发配置区（从 .env 读取） ============
import os

from ...core import env

_config = env.config_ns  # 旧键名，改读 .env

# 总开关：true=启用，false=停用
RELAY_ENABLED = str(getattr(_config, "relay_enabled", "true")).lower() == "true"

# 源群：监听群主发图的群（圣芙蕾雅学园）
RELAY_SOURCE_GROUP = int(getattr(_config, "relay_source_group", 0))

# 目标群：把图转发到的群（圣芙蕾雅分园）
RELAY_TARGET_GROUP = int(getattr(_config, "relay_target_group", 0))

# 群主 QQ 号：只有这个 QQ 在源群发的图片才会被转发
RELAY_OWNER_QQ = str(getattr(_config, "relay_owner_qq", ""))

# 开启词：群主在源群发这个词后，他后续发的图片才会被转发
RELAY_ARM_TRIGGER = str(getattr(_config, "relay_arm_trigger", "1"))

# 停止词：群主说这些词之一，立即结束本轮转发（逗号分隔，可留空）
RELAY_STOP_TRIGGERS = tuple(
    w for w in str(getattr(_config, "relay_stop_triggers", "发完了,不发了,结束,停止")).replace(" ", "").split(",") if w
)

# 连续发图判定：距上一张图超过该秒数仍未继续，则本轮自动停止
RELAY_ARM_TIMEOUT = int(getattr(_config, "relay_arm_timeout", "300"))

# 自动转发群友列表：这些QQ号在源群发的图片会自动转发到目标群，不需要触发开关（逗号分隔，后续可随时加）
RELAY_AUTO_USERS = tuple(
    w for w in str(getattr(_config, "relay_auto_users", "")).replace(" ", "").split(",") if w
)
