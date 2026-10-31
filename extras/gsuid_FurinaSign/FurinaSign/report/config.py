"""FurinaSign 配置：只管战报，签到时间/开关仍以各上游插件配置为准。"""
import json
from typing import Dict, List, Tuple

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.gs_config import StringConfig
from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsStrConfig,
    GsBoolConfig,
    GsListStrConfig,
)

UPSTREAM = ("GenshinUID", "StarRailUID", "ZZZeroUID", "BBBUID")


def upstream_sign_times() -> List[Tuple[int, int]]:
    """读取四个上游插件当前的 SignTime（只读）。"""
    out: List[Tuple[int, int]] = []
    for name in UPSTREAM:
        try:
            path = get_res_path(name) / "config.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("SignTime", {})
            value = value.get("data", value) if isinstance(value, dict) else value
            out.append((int(value[0]) % 24, int(value[1]) % 60))
        except Exception:
            continue
    return out


def latest_sign_time() -> Tuple[int, int]:
    times = upstream_sign_times()
    return max(times) if times else (9, 0)


def _default_report_time() -> List[str]:
    hour, minute = latest_sign_time()
    total = hour * 60 + minute + 15
    return [str(total // 60 % 24), f"{total % 60:02d}"]


CONFIG_DEFAULT: Dict[str, GSC] = {
    "ReportEnable": GsBoolConfig(
        "群签到战报",
        "四游自动签到完成后，在目标群发送一句问候 + 一张米游社签到总结卡片",
        True,
    ),
    "ReportTime": GsListStrConfig(
        "战报发送时间（时，分）",
        "建议比各游戏插件的签到时间晚 15 分钟以上；修改后热生效，无需重启",
        _default_report_time(),
    ),
    "ReportGroups": GsListStrConfig(
        "指定推送群（留空 = 自动）",
        "留空时：芙宁娜面板「功能权限 → 米游社签到」开启、且群里至少有一位已订阅自动签到的群友的群",
        [],
    ),
    "BotName": GsStrConfig(
        "称呼",
        "问候语和卡片里机器人的自称",
        "芙芙",
    ),
}

CONFIG_PATH = get_res_path("FurinaSign") / "config.json"
FS_CONFIG = StringConfig("FurinaSign", CONFIG_PATH, CONFIG_DEFAULT)
STATE_PATH = get_res_path("FurinaSign") / "state.json"


def report_time() -> Tuple[int, int]:
    try:
        value = FS_CONFIG.get_config("ReportTime").data
        return int(value[0]) % 24, int(value[1]) % 60
    except Exception:
        hour, minute = latest_sign_time()
        total = hour * 60 + minute + 15
        return total // 60 % 24, total % 60


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)
