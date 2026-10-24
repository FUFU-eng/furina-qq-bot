"""init"""
from gsuid_core.sv import Plugins

# 游戏前缀只给本插件用；所有触发器都要求 @机器人（to_me），不 @ 不响应。
Plugins(
    name="FurinaMys",
    force_prefix=["原神", "ys", "星铁", "崩铁", "bt", "绝区零", "zzz", "崩坏3", "崩三", "bbb"],
    allow_empty_prefix=False,
    priority=0,
    alias=["芙芙米游社助手"],
)
