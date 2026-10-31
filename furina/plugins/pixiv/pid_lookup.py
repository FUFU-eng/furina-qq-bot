"""艾特机器人 + ``pid<作品ID>`` → 直接把这部 Pixiv 作品发到群里（支持一条消息多个号码）。

- 触发：``@机器人 pid1234567``；一条消息里可以写多个，**换行 / 逗号 / 空格 / 顿号**隔开都认，
  例如 ``@机器人 pid111 pid222``、``pid111,pid222``、每个 pid 一行。必须艾特才触发。
- 取图：复用本插件的 ``provider``（按 ID 拉作品与全部原图地址）、``downloader``（下原图）、
  ``delivery.send_work``（单页普通消息 / 多页合并转发，且"发送结果不确定不重发"）。
- 收到号码先回一句「等着，本芙这就给你端上来」，没有冷却，随时可以再取。
- R18（``xRestrict`` 不为 0）或屏蔽作品：只艾特请求者回一句固定提示，不发图。
- 权限：独立开关「Pixiv ID 取图」（``pixiv_id``，默认关），同时要求本插件总开关开启。

本模块必须在 NoneBot 环境下导入（和本插件其它模块一样由 ``__init__`` 末尾加载）。
"""
from __future__ import annotations

import asyncio
import re
import uuid

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from . import _allowed, _config, _sender
from ...core import env, roles
from ...core.task_queue import TaskQueue
from ...services.pixiv.delivery import SendUncertain, send_work
from ...services.pixiv.provider import PixivAccessError, PixivDownloadError, PixivError, PixivNetworkError

# 触发词：pid + 一串数字，中间允许空格 / : / ： / #
PID_RE = re.compile(r"(?<![a-zA-Z0-9])pid\s*[:：#]?\s*(\d{4,12})(?![0-9a-zA-Z])", re.IGNORECASE)
# R18 / 屏蔽作品的固定回应
R18_TEXT = "这张图有露点，小孩子不能看哦~"
# 收到号码后的固定回应
ACK_TEXT = "等着，本芙这就给你端上来"
# 一次最多取几部（一条消息里塞太多会长时间占着发送队列）
MAX_BATCH = 10

_pid_queue = TaskQueue("pixiv_pid")


def _live():
    """取本插件当前运行期的共享对象（延迟拿，避免 import 时它们还是 None）。"""
    from . import _client, _store

    return _client, _store


def _plain(event) -> str:
    """只取消息里的文本段（@ 段与纯文本 @昵称 都会被剥离），避免紧贴汉字的 pid 漏识别。"""
    raw = getattr(event, "original_message", None) or event.message
    txt = "".join(s.data.get("text", "") for s in raw if s.type == "text").strip()
    return roles.strip_bot_at(txt, str(getattr(event, "self_id", "") or ""))


def _pids_in(text: str) -> list[str]:
    """一条消息里的所有 pid：换行 / 逗号 / 空格 / 顿号隔开的都认，去重且保持原顺序。"""
    seen, out = set(), []
    for m in PID_RE.finditer(text or ""):
        pid = m.group(1)
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _rule(event) -> bool:
    if not isinstance(event, GroupMessageEvent) or str(event.user_id) in {str(event.self_id), str(env.bot_qq())}:
        return False
    raw = getattr(event, "original_message", None) or event.message
    self_ids = {str(event.self_id), str(env.bot_qq())} - {""}
    has_at = any(s.type == "at" and str(s.data.get("qq")) in self_ids for s in raw) or roles.is_bot_at(event)
    if not has_at:
        return False
    return bool(_pids_in(_plain(event)))


pid_lookup = on_message(rule=Rule(_rule), priority=4, block=True)


async def _notice(bot, gid, uid, text: str):
    """艾特请求者 + 一句话。发送结果不确定时不重试（和本插件其它发送一致）。"""
    try:
        await bot.send_group_msg(
            group_id=int(gid),
            message=MessageSegment.at(uid) + MessageSegment.text(" " + text),
        )
    except Exception as exc:
        logger.warning(f"[Pixiv] ID 取图提示未确认：{type(exc).__name__}，不重试")


@pid_lookup.handle()
async def handle_pid(bot: Bot, event: GroupMessageEvent):
    gid, uid = event.group_id, event.user_id
    if not _allowed(gid, "pixiv_id"):
        await _notice(bot, gid, uid, "这个群还没有开启「Pixiv ID 取图」，请在控制面板的群权限中开启。")
        return

    pids = _pids_in(_plain(event))
    if not pids:
        return
    extra = ""
    if len(pids) > MAX_BATCH:
        extra = f"（一次最多 {MAX_BATCH} 部，先给你发前 {MAX_BATCH} 部）"
        pids = pids[:MAX_BATCH]

    # 收到号码先说一句，然后入队后台逐个处理（不阻塞事件循环，没有冷却）
    await _notice(bot, gid, uid, ACK_TEXT + extra)
    task = asyncio.create_task(_queued(bot, gid, uid, pids))
    task.add_done_callback(lambda _: None)


async def _queued(bot, gid, uid, pids):
    """多个 pid：逐个下载攒着，最后合并成**一条**转发发出去；某部失败不影响后面的。"""
    if len(pids) == 1:
        await _queued_one(bot, gid, uid, pids[0])
        return
    ticket = _pid_queue.enqueue()
    try:
        async with ticket:
            await _lookup_batch(bot, gid, uid, pids)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"[Pixiv] 批量取图失败 {pids}：{type(exc).__name__}: {exc}")
        await _notice(bot, gid, uid, "这次没能取到图，稍后再试一次吧。")
    finally:
        ticket.cancel()


async def _queued_one(bot, gid, uid, pid):
    ticket = _pid_queue.enqueue()
    try:
        async with ticket:
            await _lookup(bot, gid, uid, pid)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"[Pixiv] ID 取图失败 pid={pid}：{type(exc).__name__}: {exc}")
        await _notice(bot, gid, uid, f"PID {pid} 这次没取到，其余继续。")
    finally:
        ticket.cancel()


async def _fetch_meta(client, store, key, pid, gid, uid, bot, quiet=False):
    """查作品元信息并做发送前检查。

    返回 (work, None) 可发；否则返回 (None, 提示语或 None)。
    ``quiet=True``（批量）时不逐条发提示，改为把提示语返回给调用方统一处理。
    """
    try:
        body = (await client.json(f"/ajax/illust/{pid}", {"lang": "zh"})).get("body")
    except PixivError as exc:
        # 404 = 这个号不存在（或已删除）；其它 PixivError 当普通失败处理
        store.finish(key, "failed", f"查询失败：{exc}")
        tip = f"找不到 PID {pid} 这个作品，检查一下号码？" if "404" in str(exc) \
            else f"PID {pid} 查询失败：{str(exc)[:60]}"
        if not quiet:
            await _notice(bot, gid, uid, tip)
        return None, tip
    if not isinstance(body, dict) or not str(body.get("id", "")).isdigit():
        store.finish(key, "failed", f"找不到作品 {pid}")
        tip = f"找不到 PID {pid} 这个作品，检查一下号码？"
        if not quiet:
            await _notice(bot, gid, uid, tip)
        return None, tip

    # R18 / 屏蔽作品：不发图，只艾特请求者说一句
    if str(body.get("xRestrict", "0")) != "0" or body.get("isMasked"):
        store.finish(key, "r18", f"作品 {pid} 属于 R18/屏蔽内容，未发送")
        logger.info(f"[Pixiv] PID {pid} 属于 R18/屏蔽作品，按规则拒绝发送")
        if not quiet:
            await _notice(bot, gid, uid, R18_TEXT)
        return None, R18_TEXT

    if str(body.get("illustType", "0")) != "0":
        store.finish(key, "failed", f"作品 {pid} 不是插画")
        tip = f"PID {pid} 不是插画作品，我发不了。"
        if not quiet:
            await _notice(bot, gid, uid, tip)
        return None, tip

    return {
        "id": str(body["id"]),
        "userName": body.get("userName", ""),
        "pageCount": body.get("pageCount", 1),
    }, None


async def _lookup(bot, gid, uid, pid: str):
    """单个 pid：跟以前一样，单页普通消息、多页合并转发，立即发送。"""
    client, store = _live()
    send = _sender(bot, gid)
    key = f"pid:{gid}:{uuid.uuid4().hex[:8]}"
    try:
        store.claim(key, detail=f"按 ID 取图 {pid}")
        work, _tip = await _fetch_meta(client, store, key, pid, gid, uid, bot)
        if work is None:
            return
        try:
            sent = await send_work(client, store, work, send, _config(), prefix="", allowed=lambda: True)
            store.finish(key, "completed", f"已发送 {sent} 页")
        except SendUncertain as exc:
            store.finish(key, "uncertain", str(exc))
            logger.warning(f"[Pixiv] PID {pid} 图片被QQ拦截或回执超时，发送直链兜底：{exc}")
            author = str(work.get("userName") or "未知作者")
            pages_n = int(work.get("pageCount") or 1)
            mirror = f"https://pixiv.re/{pid}.jpg" if pages_n <= 1 else f"https://pixiv.re/{pid}-1.jpg"
            await _notice(bot, gid, uid, f"PID {pid}（作者：{author}）的图片被 QQ 拦截发不出来，给你直链看原图：{mirror}")
        except PixivDownloadError as exc:
            store.finish(key, "failed", f"原图下载失败：{exc}")
            await _notice(bot, gid, uid, "原图下载失败了，稍后再试一次吧。")
        except PixivError as exc:
            store.finish(key, "failed", str(exc))
            await _notice(bot, gid, uid, f"这部作品发不出来：{str(exc)[:80]}")
    except (PixivNetworkError, PixivAccessError) as exc:
        store.finish(key, "failed", f"网络/访问失败：{exc}")
        logger.warning(f"[Pixiv] ID 取图网络失败 pid={pid}：{exc}")
        await _notice(bot, gid, uid, "连不上 Pixiv（或者代理不通），稍后再试一次吧。")
    finally:
        store.cleanup()


async def _sink_send(*args, **kwargs):
    """批量模式的占位发送函数：这一阶段只是把原图下下来攒着，真正的群发在最后统一做。"""
    return None


def _plan_chunks(items, max_bytes=18 * 1024 * 1024, max_nodes=99, max_images=8):
    """把「(路径, 署名)」按体积/张数切成若干组，每条转发只承担一组。

    一条 51MB / 11 张的合并转发会让 NapCat 直接超时（实测群里收不到），
    拆成每条 ≤18MB、≤8 张的小转发才发得出去。
    """
    chunks, cur, cur_bytes = [], [], 0
    for path, credit in items:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if cur and (cur_bytes + size > max_bytes or len(cur) >= max_images):
            chunks.append(cur)
            cur, cur_bytes = [], 0
        cur.append((path, credit))
        cur_bytes += size
    if cur:
        chunks.append(cur)
    return chunks


async def _send_chunk(send, caption: str, chunk) -> bool:
    """先按合并转发发；转发报错就回退成普通图片消息（一条一张，带署名）。

    为什么要回退：这套 QQ 环境里 `send_forward_msg` 经常在 NapCat 上传资源那一步被拒
    （报 `上传转发消息资源失败` / `ActionFailed`），而这种失败**根本没送达**（群里看不到，
    只是本地回显），所以回退重发不会造成重复；普通图片消息则一直稳定送达。
    """
    paths = [p for p, _ in chunk]
    credits = [c for _, c in chunk]
    try:
        await send(caption, paths, forward=True, start_page=1,
                   total_pages=len(paths), node_captions=credits)
        return True
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        logger.warning(f"[Pixiv] 合并转发未成功（{type(exc).__name__}），回退为普通图片消息逐张发")
    sent_any = False
    for path, credit in chunk:
        # 回退时逐张发：每部作品的第一张带「作者 · PID」，其余张带批量的标题
        line = credit if credit else caption
        try:
            await send(line, [path])
            sent_any = True
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            logger.warning(f"[Pixiv] 回退发单张也失败（{type(exc).__name__}），跳过这一张")
    return sent_any


async def _lookup_batch(bot, gid, uid, pids):
    """批量：全部下载攒起来，按体积拆成几条**合并转发**依次发出去（中途不逐部单发）。"""
    client, store = _live()
    send = _sender(bot, gid)
    key = f"pidbatch:{gid}:{uuid.uuid4().hex[:8]}"
    items: list = []          # [(路径, 该图的署名)]
    problems: list[str] = []
    total = 0
    try:
        store.claim(key, detail=f"批量按 ID 取图 {' '.join(pids)}")
        for pid in pids:
            meta, tip = await _fetch_meta(client, store, key, pid, gid, uid, bot, quiet=True)
            if meta is None:
                if tip:
                    problems.append(tip)
                continue
            credit = f'作者：{meta["userName"] or "未知作者"} · PID：{pid}'
            try:
                # 关键：这里用 _sink_send 占位，只下载不发；否则会先逐部发一遍再发合并转发
                got = await send_work(client, store, meta, _sink_send, _config(), prefix="",
                                      allowed=lambda: True, keep_paths=True, flat=True)
            except SendUncertain as exc:
                logger.warning(f"[Pixiv] 批量里 PID {pid} 发送不确定：{exc}")
                problems.append(f"PID {pid} 没取到。")
                continue
            except (PixivNetworkError, PixivAccessError):
                raise
            except PixivError as exc:
                logger.info(f"[Pixiv] 批量里 PID {pid} 跳过：{exc}")
                problems.append(f"PID {pid} 没取到。")
                continue
            got = list(got or [])
            if not got:
                problems.append(f"PID {pid} 没取到。")
                continue
            # 每部作品的第一页标上作者/PID，后面的页留空
            items.extend([(got[0], credit)] + [(p, "") for p in got[1:]])
            total += 1

        if not items:
            store.finish(key, "failed", "批量取图没有可发送的作品")
            await _notice(bot, gid, uid, "这一批没取到能发的作品，检查一下号码？")
        else:
            chunks = _plan_chunks(items)
            done_images = done_chunks = failed_chunks = 0
            for i, chunk in enumerate(chunks, 1):
                caption = f"Pixiv {total} 部作品（共 {len(items)} 张）"
                if len(chunks) > 1:
                    caption += f" · 第 {i}/{len(chunks)} 条"
                ok = await _send_chunk(send, caption, chunk)
                if ok:
                    done_chunks += 1
                    done_images += len(chunk)
                    logger.info(f"[Pixiv] 第 {i}/{len(chunks)} 条已发出（{len(chunk)} 张）")
                else:
                    failed_chunks += 1
            state = "completed" if not failed_chunks else "partial"
            store.finish(key, state,
                         f"发出 {done_chunks}/{len(chunks)} 条，共 {done_images}/{len(items)} 张；失败 {failed_chunks} 条")
            if failed_chunks:
                await _notice(bot, gid, uid,
                              f"有 {failed_chunks} 条没能发出来，可以单独把对应的 pid 再发一次。")
            if problems:
                await _notice(bot, gid, uid, "；".join(problems)[:180])
    except (PixivNetworkError, PixivAccessError) as exc:
        store.finish(key, "failed", f"网络/访问失败：{exc}")
        logger.warning(f"[Pixiv] 批量取图网络失败：{exc}")
        await _notice(bot, gid, uid, "连不上 Pixiv（或者代理不通），稍后再试一次吧。")
    finally:
        for p, _credit in items:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        store.cleanup()
