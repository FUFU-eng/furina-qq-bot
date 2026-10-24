"""按机器人 QQ 分开的数据命名空间（目前只有好感度按号独立；情绪、学到的设定全号共用）。

主号（.env 的 BOT_QQ，或没配置时）沿用原来的命名空间，已有数据原样保留；
其他号各用一份 ``<ns>@<QQ>``，从默认值开始各自养成。
面板等其他进程也可以导入这里来找到对应号的数据。
"""
from __future__ import annotations

from . import env


def ns_for(base: str, bot_qq=None) -> str:
    qq = str(bot_qq or "").strip()
    primary = env.get("BOT_QQ", "").strip()
    if not qq or not primary or qq == primary:
        return base
    return f"{base}@{qq}"


def favor_ns(bot_qq=None) -> str:
    return ns_for("favor", bot_qq)
