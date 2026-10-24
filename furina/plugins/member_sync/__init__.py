"""群成员缓存同步：定时拉取各群成员列表，写入数据库（files/group_members.json）。

- 加群成员自动添加；
- 退群成员自动删除；
- 昵称变化自动覆盖（取群名片优先，其次昵称）。
控制面板的「群友回复次数」页读取这份缓存并按昵称首字母排序。
"""

import asyncio

from nonebot import get_bot, get_driver, logger, on_notice

from ..chat import config
from ...core import docfile, env
from ...core.live_groups import set_groups as _set_live_groups, invalidate as _invalidate_live

DATA_PATH = "group_members.json"  # SQLite docs: files/group_members.json
SYNC_INTERVAL = env.get_int("MEMBER_SYNC_INTERVAL", 86400)

driver = get_driver()
_task: asyncio.Task | None = None
_sync_bot = None
_wake = asyncio.Event()


def _load() -> dict:
    data = docfile.load(DATA_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("groups"), dict):
        data = {"groups": {}, "group_names": {}}
    return data


def _save(data: dict):
    try:
        docfile.save(DATA_PATH, data)
    except Exception as e:
        logger.warning(f"[成员同步] 保存失败: {e}")


async def _sync_once(bot):
    data = _load()
    groups = data.setdefault("groups", {})
    group_names = data.setdefault("group_names", {})
    groups_to_sync = list(config.AI_CHAT_GROUPS)
    live_ids = set()
    try:
        group_list = await bot.call_api("get_group_list")
        if isinstance(group_list, list):
            ids = {str(g.get("group_id")) for g in group_list if g.get("group_id")}
            if ids:
                live_ids = ids
                groups_to_sync = sorted(ids)
                # 顺手刷新全局「实时在群」缓存，供米游社/封神榜等主动推送插件使用
                _set_live_groups(ids)
            logger.info(f"[成员同步] get_group_list 返回 {len(groups_to_sync)} 个群: {groups_to_sync}")
    except Exception as e:
        logger.warning(f"[成员同步] get_group_list 失败，回退到配置群: {e}")
    for gid_str in groups_to_sync:
        try:
            gid = int(gid_str)
        except (TypeError, ValueError):
            continue
        try:
            try:
                info = await bot.call_api("get_group_info", group_id=gid)
                name = (info or {}).get("group_name") or ""
                if name:
                    group_names[str(gid)] = name
            except Exception:
                pass
            members = await bot.call_api("get_group_member_list", group_id=gid)
            current = {}
            for m in members or []:
                uid = str(m.get("user_id") or "")
                if not uid:
                    continue
                name = (m.get("card") or m.get("nickname") or "").strip()
                current[uid] = name or uid
            groups[str(gid)] = current
            logger.info(f"[成员同步] 群{gid} 当前成员 {len(current)} 人")
        except Exception as e:
            logger.warning(f"[成员同步] 群{gid} 拉取失败: {e}")
    # 机器人已经退出的群从缓存里移除（旧版只增不减）；群列表没拉到时不动，避免误删
    if live_ids:
        for gid in [g for g in groups if g not in live_ids]:
            groups.pop(gid, None)
            group_names.pop(gid, None)
            logger.info(f"[成员同步] 机器人已不在群{gid}，已移除其成员缓存")
    _save(data)


async def _loop(bot):
    while True:
        _wake.clear()
        try:
            await _sync_once(bot)
        except Exception as e:
            logger.warning(f"[成员同步] 同步失败: {e}")
        # Coalesce bursts into at most one follow-up pass, not one full sync per notice.
        await asyncio.sleep(2)
        try:
            await asyncio.wait_for(_wake.wait(), timeout=max(5, SYNC_INTERVAL))
        except asyncio.TimeoutError:
            pass


group_notice = on_notice(priority=5, block=False)


@group_notice.handle()
async def _on_group_notice(event):
    if getattr(event, "notice_type", "") not in ("group_increase", "group_decrease"):
        return
    if _sync_bot is not None and str(getattr(event, "self_id", "")) == str(_sync_bot.self_id):
        _wake.set()


async def _cancel_sync():
    global _task
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@driver.on_bot_connect
async def _start_sync(bot):
    global _task, _sync_bot
    if _sync_bot is bot and _task and not _task.done():
        return
    await _cancel_sync()
    _sync_bot = bot
    _invalidate_live()
    _wake.clear()
    # _loop does the first sync itself; do not launch a duplicate _sync_once.
    _task = asyncio.create_task(_loop(bot))
    logger.info(f"[成员同步] 已启动，每 {SYNC_INTERVAL}s 同步一次")


@driver.on_bot_disconnect
async def _stop_sync(bot):
    global _sync_bot
    if _sync_bot is bot:
        _sync_bot = None
        await _cancel_sync()
        _invalidate_live()


@driver.on_shutdown
async def _shutdown_sync():
    await _cancel_sync()