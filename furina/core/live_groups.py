"""实时在群列表服务（单一权威来源）。

机器人「当前到底在哪些群」只认 NapCat 的 get_group_list，不再依赖 .env 里手写的静态群名单：
- 换机器人 QQ / 加入新群 / 退出群后，最多一个同步周期就能自动反映；
- 主动推送（米游社、封神榜）的目标群 = 实时在群 ∩ 该功能权限开启的群，
  因此绝不会再往「机器人已经不在的群」发消息而反复失败刷红。

member_sync 每次成功拉到 get_group_list 后会调用 set_groups() 顺手刷新这里的缓存，
群增减事件 / bot 连接成功时也会刷新；其他插件直接读缓存即可，不必各自再请求一次接口。
"""

import time

from nonebot import get_bot, logger

# 缓存的实时在群（str 群号集合）与刷新时间戳
_cache: set[str] = set()
_ts: float = 0.0
# 缓存有效期（秒）：过期后下次 get_live_groups() 会重新请求接口
_TTL = 120.0


def cached() -> set[str]:
    """同步读取最近一次缓存的在群列表（不发请求）。缓存为空时返回空集合。"""
    return set(_cache)


def set_groups(ids) -> None:
    """用一次成功的 get_group_list 结果刷新缓存（member_sync / 连接成功时调用）。"""
    global _cache, _ts
    try:
        fresh = {str(g) for g in ids if str(g)}
    except Exception:
        return
    if fresh:
        _cache = fresh
        _ts = time.time()


def invalidate() -> None:
    """清空缓存（切换 QQ、重连时调用），强制下次重新拉取。"""
    global _cache, _ts
    _cache = set()
    _ts = 0.0


async def get_live_groups(force: bool = False) -> set[str]:
    """获取当前机器人实时在的群（str 群号集合）。

    - 缓存未过期直接返回；force=True 或过期则请求 get_group_list；
    - 请求失败时退而返回旧缓存（可能为空），调用方据此决定是否保守处理。
    """
    global _cache, _ts
    now = time.time()
    if not force and _cache and now - _ts < _TTL:
        return set(_cache)
    try:
        bot = get_bot()
        group_list = await bot.call_api("get_group_list")
        if isinstance(group_list, list):
            ids = {
                str(g.get("group_id"))
                for g in group_list
                if isinstance(g, dict) and g.get("group_id")
            }
            if ids:
                _cache = ids
                _ts = now
                logger.info(f"[实时群列表] 当前在 {len(_cache)} 个群: {sorted(_cache)}")
    except Exception as e:
        logger.warning(f"[实时群列表] get_group_list 失败，沿用上次缓存: {e}")
    return set(_cache)
