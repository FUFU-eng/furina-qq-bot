"""JM 下载插件：群内发 ``/jm 车号`` → 下载整本并合成 PDF，以 QQ 群文件形式发到群里。

触发形态：群里直接发 ``/jm 350234``（也兼容 ``jm 350234``、``JM350234``、``/jm350234``）。
下载与 PDF 合成由 jmcomic + img2pdf 完成，产物缓存在 ``JM_CACHE_DIR``（默认 ``data/jm``），
同一本再次点播直接复用缓存。功能开关：``permissions`` 的 ``jm`` + ``.env`` 的 ``JM_ENABLED``。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from urllib.parse import quote

from nonebot import get_driver, logger, on_message
from ...core.botref import bot_for
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from . import config, service
from ...core import permissions, usage
from ...core.task_queue import enqueue_jm, queued_jm_count

# 保活后台任务（上传 / 送达核查 / 启动清理），避免被事件循环中途回收
_bg_tasks: set = set()

_USAGE = (
    "用法：/jm 车号\n"
    "例如 /jm 350234 —— 我会把整本下载并合成 PDF 发到群里（大文件可能要等几十秒）"
)

# 收到车号后的固定回应
_ACCEPT = "收到神秘号码，正在进入天堂下载"


def _nick(event) -> str:
    s = getattr(event, "sender", None)
    return str(getattr(s, "card", "") or getattr(s, "nickname", "") or "")


def _plain_text(event: GroupMessageEvent) -> str:
    """把消息里的 CQ 段去掉，只留纯文本，避免图片 URL 里的数字被当成车号。"""
    try:
        return event.message.extract_plain_text()
    except Exception:
        return (event.raw_message or "").split(" ", 1)[-1]


def _check_trigger(event) -> bool:
    """只在群聊里、且消息以 /jm 或 jm 开头时才触发。"""
    if not isinstance(event, GroupMessageEvent):
        return False
    text = _plain_text(event).strip()
    if not config.has_jm_head(text):
        return False
    logger.info(f"[JM] 群聊触发 /jm，群号: {event.group_id}, 用户: {event.user_id}")
    return True


jm_matcher = on_message(rule=Rule(_check_trigger), priority=5, block=True)


def _file_uri(path: str) -> str:
    """OneBot 11 规定本地文件用 file:/// URI；Windows 盘符与中文都要转义。"""
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + quote(p, safe="/:")


def _is_path_rejected(err) -> bool:
    """判断报错是不是「路径写法根本没被接受」（确定什么都没发出去）。"""
    text = str(err)
    return "识别URL失败" in text or "uri=" in text.lower()


async def _send_group_file(bot, group_id: int, pdf_path: str, filename: str):
    """把 PDF 作为群文件发送（正常情况下只发一次）。

    **不做超时重试**：NapCat 的超时几乎都是「文件其实已经发出去了，只是没回调」，
    重发就会变成群里两份。失败一律交给调用方用群历史核查来判定。
    只有「路径没被接受、确定什么都没发出去」的报错才换个路径写法再发一次。
    """
    uri = _file_uri(pdf_path)
    try:
        return await bot.call_api("upload_group_file", group_id=group_id, file=uri, name=filename)
    except Exception as e:
        if not _is_path_rejected(e):
            raise  # 超时/网络类：可能已经发出去了，绝不能重发
        logger.warning(f"[JM] file:// 写法被拒绝（{str(e)[:80]}），改用绝对路径再发一次")
    return await bot.call_api(
        "upload_group_file", group_id=group_id, file=os.path.abspath(pdf_path), name=filename
    )


def _response_ok(resp) -> bool:
    """判断 OneBot 返回是否成功。NapCat 失败时返回 status=failed + retcode=200。"""
    if not isinstance(resp, dict):
        return bool(resp)
    return str(resp.get("status", "")).lower() == "ok" and int(resp.get("retcode") or 0) == 0


def _history_messages(hist) -> list:
    """兼容两种历史结构：适配器已解包的 {messages: [...]}，以及 NapCat 原始信封 {data: {...}}。"""
    if not isinstance(hist, dict):
        return []
    msgs = hist.get("messages")
    if msgs is None:
        msgs = (hist.get("data") or {}).get("messages")
    return msgs or []


def _history_has_file(hist, filename: str, echo: str = "") -> bool:
    """在群历史里找这个群文件：先按消息 id（echo）精确匹配，再退回按文件名匹配。

    NapCat 的 upload_group_file 返回的 file_id 可能为空，所以文件名兜底也要看。
    """
    stem = os.path.splitext(filename)[0]
    for m in _history_messages(hist):
        raw = json.dumps(m, ensure_ascii=False)
        if echo and f'"{echo}"' in raw:
            return True
        for seg in (m.get("message") or []):
            if not isinstance(seg, dict) or seg.get("type") != "file":
                continue
            data = seg.get("data") or {}
            name = str(data.get("file") or data.get("name") or "")
            if name == filename:
                return True
            # 兜底：文件段有时只带 file_id（没有可读文件名），但车号仍然在名字里。
            # 只认同一个车号，避免把群里别的 PDF 当成自己发的那本。
            if data.get("file_id") and stem and stem in name:
                return True
    return False


async def _verify_file_upload(bot, group_id: int, resp, filename: str, album_id: str = "", delay: int = 60) -> bool:
    """上传接口报错后的核查：等一会儿看群里到底有没有这个文件，返回是否确认送达。

    只核查、不自动补发：报告里说「发送失败」但其实已经发出去的情况很常见，
    自动补发一个几十 MB 的群文件反而可能真的发两遍。确认送达后同样删掉缓存。
    """
    try:
        echo = str((resp or {}).get("echo") or "")
        await asyncio.sleep(delay)
        try:
            hist = await bot.get_group_msg_history(group_id=group_id, count=50)
        except Exception as e:
            logger.warning(f"[JM] 核查群文件是否送达失败: {e}")
            return False
        if _history_has_file(hist, filename, echo):
            logger.info(f"[JM] 核查确认 {filename} 已送达群 {group_id}（API 只是先报了超时）")
            if album_id:
                _drop_album_cache(album_id, "核查确认已送达")
            return True
        logger.warning(f"[JM] 核查后仍未在群 {group_id} 看到 {filename}；用户重发一次即可")
        return False
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f"[JM] 送达核查异常: {e}")
        return False


def _fmt_size(num_bytes: int) -> str:
    mb = num_bytes / 1024 / 1024
    if mb >= 1:
        return f"{mb:.1f}MB"
    return f"{num_bytes / 1024:.0f}KB"


def _drop_album_cache(album_id: str, why: str):
    """发出去之后立刻删掉这本的缓存（图片 + PDF），不留在磁盘上累积。"""
    try:
        from . import service as _service

        if _service.clear_album(album_id):
            logger.info(f"[JM] {why}，已删除 JM{album_id} 的缓存")
    except Exception as e:
        logger.debug(f"[JM] 删除 JM{album_id} 缓存失败（不影响发送结果）: {e}")


async def _upload_pdf(bot, group_id: int, pdf_path: str, filename: str, album_id: str = "") -> bool:
    """发送群文件，返回「文件是否已经出去了」（是否该接着艾特人）。

    调用方要等这个函数返回再艾特，这样「@xxx」一定排在文件后面。
    接口报超时/失败时也照样返回 True（NapCat 这种报错几乎都已经把文件发出去了），
    另外挂一个后台核查任务去确认真伪并清理缓存，不阻塞艾特。
    """
    try:
        resp = await _send_group_file(bot, group_id, pdf_path, filename)
    except Exception as e:
        logger.warning(f"[JM] 群文件上传返回异常（核查是否已送达）: {e}")
        resp = {"status": "failed", "message": str(e)}
    if _response_ok(resp):
        size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0
        logger.info(f"[JM] 已发送群文件 {filename}（{_fmt_size(size)}）到群 {group_id}")
        if album_id:
            _drop_album_cache(album_id, "发送成功")
        return True
    logger.warning(f"[JM] 群文件上传接口报错（文件可能已发出）: {str(resp)[:200]}")
    verify = asyncio.create_task(_verify_file_upload(bot, group_id, resp, filename, album_id))
    _bg_tasks.add(verify)
    verify.add_done_callback(_bg_tasks.discard)
    return True


@jm_matcher.handle()
async def handle_jm(event: GroupMessageEvent):
    group_id = event.group_id
    user_id = str(event.user_id)

    if not config.enabled():
        return
    if not permissions.allowed(group_id, "jm", True):
        logger.info(f"[JM] 群{group_id} 未开放 JM 下载，忽略")
        return

    album_id = config.parse_album_id(_plain_text(event).strip())
    if not album_id:
        await jm_matcher.finish(_USAGE)

    # 每日限额：每人 5 次 / 每群 20 次（0 表示不限制）
    user_limit = config.daily_user_limit()
    if user_limit > 0 and not usage.consume("jm_user", group_id, user_id, user_limit):
        await jm_matcher.finish(f"今天你已经点过 {user_limit} 本啦，明天再来吧~")
    group_limit = config.daily_group_limit()
    if group_limit > 0 and not usage.consume("jm_group", group_id, "all", group_limit):
        await jm_matcher.finish(f"本群今天已经下载 {group_limit} 本了，休息一下吧~")

    bot = bot_for(event)
    name = f"{album_id}.pdf"

    # 收到号码先回一句固定的回应
    await jm_matcher.send(_ACCEPT)

    # 命中缓存（上次发出前没删干净的残留）：先发文件，发出后再艾特
    if service.pdf_ready(album_id):
        if await _upload_pdf(bot, group_id, str(service.pdf_path(album_id)), name, album_id):
            await jm_matcher.send(Message([MessageSegment.at(user_id)]))
        usage.track("jm", group_id)
        usage.track_user("jm", group_id, user_id, name=_nick(event))
        return

    ticket = enqueue_jm()
    try:
        if ticket.position > 1:
            queued = queued_jm_count()
            await jm_matcher.send(Message([
                MessageSegment.at(user_id),
                MessageSegment.text(
                    f" ⏳ 前面还有 {queued} 本在下载，你排在第 {ticket.position} 位，"
                    f"好了我就把 {name} 发上来~"
                ),
            ]))
        async with ticket:
            if ticket.position > 1:
                await jm_matcher.send(Message([
                    MessageSegment.at(user_id),
                    MessageSegment.text(f" ✅ 轮到你了，开始下载 JM{album_id}…"),
                ]))

            progress_state = {"last": 0.0}

            async def on_progress(count: int):
                now = time.time()
                if now - progress_state["last"] < config.progress_interval():
                    return
                progress_state["last"] = now
                await jm_matcher.send(f"⏬ JM{album_id} 已下载 {count} 页…")

            pdf_path, err, title = await service.download_pdf(album_id, on_progress)

            if err:
                if err == "timeout":
                    msg = f"⌛ JM{album_id} 下载超时了，这本可能太大，稍后再试一次吧"
                elif err == "too_large":
                    msg = f"📕 JM{album_id} 页数太多（超过 {config.max_images()} 页），这次先不下了"
                elif err == "not_found":
                    msg = f"❌ 找不到 JM{album_id}，检查一下车号？"
                elif err == "network":
                    msg = f"🌐 JM{album_id} 下载时网络/代理不通，稍后再试一次吧"
                else:
                    msg = f"❌ JM{album_id} 下载失败了，稍后再试一次吧"
                await jm_matcher.send(Message([
                    MessageSegment.at(user_id),
                    MessageSegment.text(" " + msg),
                ]))
                return

            # 先把 PDF 发出去，确认送达后再艾特发号码的人（艾特放在文件后面）
            if await _upload_pdf(bot, group_id, pdf_path, name, album_id):
                await jm_matcher.send(Message([MessageSegment.at(user_id)]))
            usage.track("jm", group_id)
            usage.track_user("jm", group_id, user_id, name=_nick(event))
    finally:
        ticket.cancel()
    await jm_matcher.finish()


# ========== 兜底：启动时收拾上次强杀留下的残留缓存 ==========
@get_driver().on_startup
async def _startup_sweep():
    """正常情况下每本发完就删了；这里只清理上次进程被杀后留下的残留（不影响正在发的）。"""
    async def _run():
        try:
            leftover = await asyncio.to_thread(service.cached_albums)
            for row in leftover:
                await asyncio.to_thread(service.clear_album, row["album_id"])
            if leftover:
                logger.info(f"[JM] 启动清理上次残留的 {len(leftover)} 本缓存")
        except Exception as e:
            logger.warning(f"[JM] 启动缓存清扫失败: {e}")

    task = asyncio.create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
