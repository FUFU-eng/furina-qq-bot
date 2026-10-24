"""FurinaMys 配置。"""
from typing import Dict

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.gs_config import StringConfig
from gsuid_core.utils.plugins_config.models import GSC, GsIntConfig, GsStrConfig, GsBoolConfig

CONFIG_DEFAULT: Dict[str, GSC] = {
    "BotName": GsStrConfig("称呼", "回复和卡片里机器人的自称", "芙芙"),
    "QrTimeout": GsIntConfig("二维码有效时间（秒）", "米游社二维码本身约 120 秒失效，超过 120 按 120 算", 120, 120),
    "PublicNeedBind": GsBoolConfig(
        "攻略 / 今日素材也要求先绑定",
        "开启时：没绑定的人查任何东西都会先引导扫码绑定；关闭时攻略、今日素材不需要绑定",
        True,
    ),
    "AutoSignOnBind": GsBoolConfig("绑定后自动开启每日签到", "扫码绑定成功后为每个游戏账号开启上游插件的自动签到", True),
    "RefreshOnBind": GsBoolConfig("绑定后后台刷新面板", "扫码绑定成功后在后台静默刷新一次全部游戏的角色面板（米游社数据源，不需要摆展柜）", True),
}

CONFIG_PATH = get_res_path("FurinaMys") / "config.json"
FM_CONFIG = StringConfig("FurinaMys", CONFIG_PATH, CONFIG_DEFAULT)


def bot_name() -> str:
    return str(FM_CONFIG.get_config("BotName").data or "芙芙")


def cfg(key: str):
    return FM_CONFIG.get_config(key).data
