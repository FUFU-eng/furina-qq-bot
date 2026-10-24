"""聊天插件共享的内存态（频率统计、最近图片、灌历史与戳一戳冷却）。"""

from collections import deque

# 每个群的表情包发送统计，用来把实际发送频率压在 STICKER_PROBABILITY 以内
_sticker_stats: dict[int, dict] = {}

# 每个群最近收到的图片（字节 + 后台生成的一句话描述），回复时可以"看到"并联系上下文
_group_recent_images: dict[int, deque] = {}

# 每个群图片描述调用时间戳（用于限频，控制免费额度消耗）
_desc_stats: dict[int, list] = {}

# 记录已经灌过历史的群，避免重连时重复灌
_seeded_groups: set[int] = set()

# 同一个群同一个人戳一戳的最小回复间隔（秒），防刷屏、防额度被戳空
_poke_cooldown: dict[tuple[int, int], float] = {}

# 每个群上一次“即时总结”的时间戳（一次总结要读 200 条消息、约 1.2 万字输入，需限频）
_summary_last: dict[int, float] = {}

