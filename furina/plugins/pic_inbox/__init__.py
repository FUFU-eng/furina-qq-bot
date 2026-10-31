"""筛图收件箱（机器人这一侧）。

- 在「功能权限 → 收图筛选」打开的群里，群友发的图（含合并转发里的图）自动收进筛图库，表情包跳过；
- 搜图找到的原图、Pixiv 点图发出的图也会收进来（见 hook()，各自插件里调用）；
- 小于 1MB 的图在电脑空闲时（显卡/内存都闲）排队用 Real-ESRGAN 强化；
- 后台每 20 秒：把留下的图导进 Eagle 自动导入文件夹、清掉撤销窗口过了的丢弃图；
- 手机端接口 127.0.0.1:PIC_INBOX_PORT + 专用 Cloudflare 隧道。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from ...core import env, permissions
from ...services import pic_inbox as svc
from . import collect, idle

FEATURE = "pic_inbox"
_names: dict[str, str] = {}
_tasks: set[asyncio.Task] = set()
_state = {"enh_note": "", "tunnel": "", "last_idle_check": 0.0}


def group_names() -> dict[str, str]:
    return dict(_names)


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _wanted(event: GroupMessageEvent) -> bool:
    if not svc.enabled():
        return False
    if not permissions.allowed(event.group_id, FEATURE, False):
        return False
    uid = str(event.user_id)
    if uid == str(event.self_id) or uid in env.get_set("PEER_BOTS"):
        return False
    return any(seg.type in ("image", "forward") for seg in event.message)


async def _rule(event: GroupMessageEvent) -> bool:
    try:
        return _wanted(event)
    except Exception:  # noqa: BLE001
        return False


# 优先级 1 且不拦截：只登记一个后台任务就放行，其它插件照常处理这条消息
collector = on_message(rule=_rule, priority=1, block=False)


@collector.handle()
async def _on_group_image(bot: Bot, event: GroupMessageEvent):
    sender = event.sender.card or event.sender.nickname or str(event.user_id)
    _spawn(_collect(bot, event.group_id, event.user_id, sender, event.message))


async def _remember_group_name(bot: Bot, gid: str) -> None:
    if gid in _names:
        return
    try:
        info = await bot.get_group_info(group_id=int(gid))
        _names[gid] = str((info or {}).get("group_name") or "")
    except Exception:  # noqa: BLE001
        _names[gid] = ""


async def _collect(bot: Bot, gid, uid, sender: str, message) -> None:
    gid = str(gid)
    try:
        await _remember_group_name(bot, gid)
        imgs = await collect.gather_images(bot, message, sender)
        got = 0
        for im in imgs:
            data = await collect.fetch_bytes(bot, im["file"], im["url"])
            if not data:
                continue
            src = "forward" if im["forward"] else "group"
            iid, why = await asyncio.to_thread(svc.add_bytes, data, src, gid, "" if im["forward"] else uid,
                                               im["name"] or sender, True)
            got += 1 if iid else 0
        if got:
            logger.info(f"[筛图] 群 {gid} 收图 {got}/{len(imgs)} 张")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[筛图] 收图失败: {e}")


def hook(src: str, data: bytes | None = None, path: str | None = None, gid="", uid="") -> None:
    """搜图原图 / Pixiv 点图收进筛图库（非阻塞，出错不影响原功能）。"""
    try:
        if not svc.enabled():
            return
        key = {"search": "PIC_INBOX_FROM_SEARCH", "pixiv": "PIC_INBOX_FROM_PIXIV",
               "pixiv_rank": "PIC_INBOX_FROM_RANK"}.get(src)
        if key and not env.get_bool(key, True):
            return

        async def _run():
            try:
                if data:
                    iid, _ = await asyncio.to_thread(svc.add_bytes, data, src, gid, uid, "", False)
                else:
                    iid, _ = await asyncio.to_thread(svc.add_file, path, src, gid, uid, "", False)
                if iid:
                    label = {"search": "搜图原图", "pixiv": "Pixiv 点图", "pixiv_rank": "Pixiv 榜单图"}.get(src, src)
                    logger.info(f"[筛图] 收进{label} {iid}")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[筛图] hook 失败: {e}")

        _spawn(_run())
    except Exception:  # noqa: BLE001
        pass


# ---------------- 后台：导出 / 清理 / 强化 / 隧道 ----------------
async def _enhance_one() -> None:
    it = await asyncio.to_thread(svc.next_to_enhance)
    if not it:
        return
    try:
        from ...core.task_queue import upscale_queue
        from ..upscale.upscale import run_upscale
        from ..upscale import config as upscale_config
    except Exception as e:  # noqa: BLE001
        _state["enh_note"] = f"强化模块不可用: {e}"
        return
    # 有人在群里用「强化」就让着；自己只在队列空着的时候插一张
    if upscale_queue._task_lock.locked() or upscale_queue.queued_count() > 0:
        return
    ok, why = await idle.is_idle()
    if not ok:
        _state["enh_note"] = why
        return
    _state["enh_note"] = ""
    iid = it["id"]
    src = svc.pending_dir() / it["file"]
    if not src.exists():
        await asyncio.to_thread(svc.set_enh, iid, "fail")
        return
    work = svc.work_dir()
    out = work / f"{iid}_x.png"
    await asyncio.to_thread(svc.set_enh, iid, "run")
    try:
        ticket = upscale_queue.enqueue()
        async with ticket:
            await run_upscale(str(src), str(out))
        ok = await asyncio.to_thread(svc.apply_enhanced, iid, out,
                                     model=upscale_config.UPSCALE_MODEL,
                                     expected_scale=upscale_config.UPSCALE_SCALE)
        if not ok:
            # 用户可能在 GPU 运行时丢掉图；结果未应用绝不能标完成。
            # 留 todo 但 trash 不会被调度，撤销后才会重新强化。
            await asyncio.to_thread(svc.set_enh, iid, "todo")
            await asyncio.to_thread(svc.record_enhance_event, it, "skipped", reason="item_state_changed",
                                   model=upscale_config.UPSCALE_MODEL,
                                   expected_scale=upscale_config.UPSCALE_SCALE)
            logger.info(f"[筛图] 强化结果未应用 {iid}，撤销丢弃后可重新处理")
            return
        audit = await asyncio.to_thread(svc.enhance_audit, iid) or {}
        logger.info(f"[筛图] 已强化 {iid} "
                    f"{audit.get('input_w')}x{audit.get('input_h')} {audit.get('input_size')}B -> "
                    f"{audit.get('output_w')}x{audit.get('output_h')} {audit.get('output_size')}B "
                    f"md5={audit.get('output_md5')}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[筛图] 强化 {iid} 失败，按原图处理: {e}")
        await asyncio.to_thread(svc.set_enh, iid, "fail")
        await asyncio.to_thread(svc.record_enhance_event, it, "fail", reason=type(e).__name__,
                               model=upscale_config.UPSCALE_MODEL,
                               expected_scale=upscale_config.UPSCALE_SCALE)
    finally:
        Path(out).unlink(missing_ok=True)


async def _loop() -> None:
    await asyncio.sleep(15)
    # 上次机器人被杀时可能停在「强化中」：退回排队
    await asyncio.to_thread(svc.reset_running)
    _spawn(_tunnel_loop())
    while True:
        try:
            if svc.enabled():
                await asyncio.to_thread(svc.export_ready)
                await asyncio.to_thread(svc.purge)
                if svc.enhance_under_bytes():
                    await _enhance_one()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[筛图] 后台任务出错: {e}")
        await asyncio.sleep(20)


async def _tunnel_loop() -> None:
    """隧道看门狗单独跑：以前和强化挤在一个循环里，强化一张大图时好久才轮到检查隧道。"""
    while True:
        try:
            if svc.enabled():
                _state["tunnel"] = await _tunnel().ensure()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[筛图] 隧道检查出错: {e}")
        await asyncio.sleep(300)


def _tunnel():
    from . import tunnel
    return tunnel


@get_driver().on_startup
async def _startup():
    if not svc.enabled():
        return
    from . import api

    api.start()
    _spawn(_loop())


@get_driver().on_shutdown
async def _shutdown():
    try:
        from . import api

        api.stop()
    except Exception:  # noqa: BLE001
        pass
