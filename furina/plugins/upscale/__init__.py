"""QQ 图片强化插件：引用（回复）图片 + 艾特机器人 + 输入"强化" → 用 RTX 5060 跑 Real-ESRGAN 输出 2 倍高清大图。"""

import asyncio
import io
import os
import re
import tempfile
import time
from pathlib import Path

import httpx
from nonebot import get_driver, logger, on_message
from ...core.botref import bot_for
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.rule import Rule
from PIL import Image

from . import config
from ...core import permissions, roles, usage as daily_limits
from .upscale import probe_nvidia_gpu_id, run_upscale
from ...core.task_queue import enqueue_upscale, queued_upscale_count

# 待发送图片的临时文件（发送完成后统一清理）
_pending_files = []
_bg_tasks: set = set()  # 保活后台任务（如 5 分钟后补发），避免被事件循环中途回收


def _cleanup_pending():
    while _pending_files:
        p = _pending_files.pop()
        try:
            if os.path.exists(p):
                os.unlink(p)
        except Exception:
            pass


def _make_image_segment_from_path(path: str):
    return MessageSegment.image(Path(path).as_uri())


async def _relay_to_subgroup(bot, group_id, image_path: str = None, forward_nodes: list = None):
    """把强化后的图片自动转发到小群（仅当在大群强化时）"""
    if not config.MAIN_GROUP_ID or not config.SUB_GROUP_ID or str(group_id) != config.MAIN_GROUP_ID:
        return
    try:
        if image_path:
            # 单张图片转发（只发图片，不带文字）
            await bot.send_group_msg(group_id=int(config.SUB_GROUP_ID), message=Message([
                _make_image_segment_from_path(image_path),
            ]))
            logger.info(f"[强化] 已将强化图片转发到小群 {config.SUB_GROUP_ID}")
        elif forward_nodes:
            # 合并转发转发
            await bot.send_forward_msg(group_id=int(config.SUB_GROUP_ID), messages=forward_nodes)
            logger.info(f"[强化] 已将批量强化合并转发转发到小群 {config.SUB_GROUP_ID}")
    except Exception as e:
        logger.warning(f"[强化] 转发到小群失败: {e}")


async def _delayed_resend(bot, group_id, nodes, since_ts: int, keep_paths, delay: int = 300):
    """send_forward_msg 报错后不打扰用户：5 分钟后核查群里是否已有这条转发，没有才补发一次，
    最后清理为补发保留的输出图。NapCat 常「API 先报错、QQ 内核随后才真正发出」，所以不提示、
    不立即重发，等 5 分钟让它自己发，确实没发再补。"""
    try:
        await asyncio.sleep(delay)
        already = False
        try:
            hist = await bot.get_group_msg_history(group_id=group_id, count=20)
            for m in hist.get("messages", []):
                if int(m.get("time", 0)) < since_ts - 1:
                    continue
                if str((m.get("sender") or {}).get("user_id")) == str(bot.self_id):
                    already = True
                    break
        except Exception:
            already = True  # 核查接口本身异常就别补了，保守不重复发
        if already:
            logger.info("[强化] 5 分钟后核查：合并转发已发出，无需补发")
        else:
            logger.warning("[强化] 5 分钟后核查仍无该转发，自动补发一次")
            try:
                await bot.send_forward_msg(group_id=group_id, messages=nodes)
            except Exception as e:
                logger.warning(f"[强化] 5 分钟自动补发异常: {e}")
    finally:
        for p in keep_paths:
            try:
                if p and os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass


# 强化关键词（支持自然语言）。英文短词 hd/4k 要求前后不是字母数字，避免误中普通单词。
_UPSCALE_WORDS = re.compile(
    r"(强化|高清|超清|放大|变清晰|弄清晰|更清楚|清楚一点|清晰一点|"
    r"画质增强|画质修复|锐化|增强一下|处理一下|修复一下|"
    r"2倍|4倍|两倍|四倍|upscale|enhance|sharpen|(?<![a-z0-9])(?:hd|4k)(?![a-z0-9]))",
    re.IGNORECASE,
)
# 消息段标记（[CQ:image,...,url=https://...] / [image:...]）。关键词只在纯文字里找：
# 旧版对整条 raw_message 搜索，图片 URL 里的随机 rkey/fileid 经常含 “hd”“4k”，
# 于是“直接发图 + @机器人 + 随便一句话”约有一成多概率被误判成强化请求（且 block 掉聊天）。
_SEGMENT_RE = re.compile(r"\[(?:CQ:)?[a-z_]+[:,][^\]]*\]", re.IGNORECASE)


def _plain_text(msg: str) -> str:
    return _SEGMENT_RE.sub(" ", msg or "")


def upscale_pattern_scan(msg: str) -> bool:
    return bool(_UPSCALE_WORDS.search(_plain_text(msg)))


# NapCat 新版发送的消息段是 [at:qq=X] / [reply:id=X] 这种「无 CQ: 前缀」的现代格式，
# 旧版是 [CQ:at,qq=X] / [CQ:reply,id=X]。两个都要兼容，否则艾特/引用检测失效、强化无法触发。
_AT_RE = re.compile(r"\[(?:CQ:)?at[:,]qq=(\d+)\]")
_REPLY_RE = re.compile(r"\[(?:CQ:)?reply[,:]id=(-?\d+)\]")
_IMG_RE = re.compile(r"\[(?:CQ:)?image\b")


def _check_trigger(event) -> bool:
    """检查消息是否触发图片强化（引用/直接发图片 + 艾特机器人 + 强化相关关键词，支持自然语言）"""
    if not isinstance(event, GroupMessageEvent):
        return False
    msg = event.raw_message
    at_ids = set(_AT_RE.findall(msg))
    self_ids = {str(getattr(event, "self_id", "")), str(config.BOT_QQ)} - {""}
    has_at_bot = bool(at_ids & self_ids) or roles.is_bot_at(event)
    has_reply = bool(_REPLY_RE.search(msg))
    has_image = bool(_IMG_RE.search(msg))
    if not (has_at_bot and (has_reply or has_image)):
        if upscale_pattern_scan(msg) and has_at_bot:
            logger.info(f"[强化] 检测到强化关键词+艾特，但缺少引用/图片，未触发: {msg[:60]}")
        return False
    # 强化相关关键词正则匹配（支持自然语言；只看纯文字，不看图片 URL）
    if upscale_pattern_scan(msg):
        trigger_type = "引用图片+艾特+强化关键词" if has_reply else "直接发图+艾特+强化关键词"
        logger.info(f"[强化] 群聊触发（{trigger_type}），群号: {event.group_id}, 用户: {event.user_id}")
        return True
    return False


upscale_matcher = on_message(rule=Rule(_check_trigger), priority=5, block=True)


def _iter_segments(msg):
    """统一遍历 OneBot 消息段：兼容 Message 对象、列表、dict 和纯文本。"""
    if msg is None:
        return
    if isinstance(msg, str):
        yield ("text", {"text": msg})
        return
    for seg in msg:
        if isinstance(seg, dict):
            yield (seg.get("type", ""), seg.get("data", {}) or {})
        else:
            yield (getattr(seg, "type", ""), getattr(seg, "data", {}) or {})


def _pick_single_image(segments):
    """从消息段中挑出唯一的查询图。多图/视频明确不支持强化，返回拒绝原因。"""
    images = []
    has_video = False
    for stype, sdata in _iter_segments(segments):
        if stype == "video":
            has_video = True
        elif stype == "image":
            images.append(sdata)
    if has_video:
        return "", "", "视频暂不支持强化"
    if len(images) > 1:
        return "", "", "多图暂不支持强化，请只引用一张图片"
    if len(images) == 1:
        return str(images[0].get("url", "") or ""), str(images[0].get("file", "") or ""), ""
    return "", "", ""


async def _extract_all_images_from_reply(event, bot) -> list:
    """从引用消息中提取所有图片（包括合并转发/长消息），返回 [(url, file_id), ...]"""
    images = []

    def _collect_from_segments(segments) -> bool:
        found = False
        for stype, sdata in _iter_segments(segments):
            if stype == "image":
                url = str(sdata.get("url", "") or "")
                fid = str(sdata.get("file", "") or "")
                if url or fid:
                    images.append((url, fid))
                    found = True
        return found

    def _collect_from_forward_nodes(nodes, depth: int = 0) -> bool:
        """遍历节点消息；嵌套 inline 转发最多展开两层，避免异常输入造成无限递归。"""
        if depth > 2 or not isinstance(nodes, (list, tuple)):
            return False
        found = False
        for node in nodes:
            if not isinstance(node, dict):
                continue
            payload = node.get("data") if node.get("type") == "node" else node
            payload = payload if isinstance(payload, dict) else node
            message = payload.get("message") or payload.get("content") or []
            if isinstance(message, dict):
                message = [message]
            found = _collect_from_segments(message) or found
            if depth < 2:
                for stype, sdata in _iter_segments(message):
                    if stype in ("forward", "longmsg"):
                        inline = sdata.get("content")
                        if isinstance(inline, list) and inline:
                            found = _collect_from_forward_nodes(inline, depth + 1) or found
        return found

    async def _extract_forward(forward_id: str, inline_nodes=None):
        """从合并转发/长消息中提取所有图片；新版节点优先，旧版按 id 拉取。"""
        inline_found = _collect_from_forward_nodes(inline_nodes) if isinstance(inline_nodes, list) else False
        if inline_found or not forward_id or forward_id == "0":
            return
        try:
            forward_msg = await bot.get_forward_msg(message_id=forward_id)
            if isinstance(forward_msg, dict):
                data = forward_msg.get("data")
                data = data if isinstance(data, dict) else {}
                msgs = forward_msg.get("messages") or data.get("messages") or []
            else:
                msgs = []
            # SnowLuma 的内层合并转发只给 id，先补成内联 content 再取图
            from ...core import forwards
            await forwards.inline_nested(bot, msgs)
            _collect_from_forward_nodes(msgs)
            logger.info(f"[强化] 从合并转发 {forward_id} 提取到 {len(images)} 张图")
        except Exception as e:
            logger.warning(f"[强化] 获取合并转发消息失败: {e}")

    # 先从 event.reply.message 提取
    if getattr(event, "reply", None) is not None and event.reply.message:
        _collect_from_segments(event.reply.message)
        # 检查是否有合并转发/长消息
        for stype, sdata in _iter_segments(event.reply.message):
            if stype in ("forward", "longmsg"):
                fid = str(sdata.get("id", "") or sdata.get("res_id", "") or "")
                inline = sdata.get("content")
                await _extract_forward(fid, inline if isinstance(inline, list) else None)

    # 如果 event.reply 解析不出，用 get_msg 拉取原消息
    if not images:
        reply_id = ""
        for stype, sdata in _iter_segments(event.message):
            if stype == "reply":
                reply_id = str(sdata.get("id", "") or "")
                break
        if reply_id and reply_id != "0":
            try:
                msg_info = await bot.get_msg(message_id=int(reply_id))
                raw_msg = msg_info.get("message", []) if isinstance(msg_info, dict) else []
                _collect_from_segments(raw_msg)
                for stype, sdata in _iter_segments(raw_msg):
                    if stype in ("forward", "longmsg"):
                        fid = str(sdata.get("id", "") or sdata.get("res_id", "") or "")
                        inline = sdata.get("content")
                        await _extract_forward(fid, inline if isinstance(inline, list) else None)
            except Exception as e:
                logger.warning(f"[强化] get_msg 拉取引用消息失败: {e}")

    return images

async def _fetch_image_bytes(bot, file_id: str, url: str) -> bytes | None:
    """取图片字节：优先协议端本地缓存（NapCat），再 httpx 下载刷新过的地址（SnowLuma）/消息原地址。"""
    from ...core import images

    # NapCat 本地缓存命中是毫秒级；若本地无此图（如合并转发内层图未缓存），
    # get_image 会尝试联网并卡到全局 API 超时（曾因此每张白等约 30 秒）。
    # 这里只给 4 秒，拿不到立刻转 httpx 下载，避免被全局长超时拖死。
    local_path, fresh = await images.lookup_image(bot, file_id, timeout=4.0)
    if local_path:
        try:
            with open(local_path, "rb") as f:
                data = f.read()
            if data:
                return data
        except OSError as e:
            logger.debug(f"[强化] 本地缓存获取失败: {e}")
    for u in images.download_candidates(fresh, url):
        try:
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
            ) as client:
                resp = await client.get(u)
                if resp.status_code == 200 and resp.content and len(resp.content) > 100:
                    return resp.content
        except Exception as e:
            logger.debug(f"[强化] 图片下载失败: {e}")
    return None


def _first_frame_rgb(data: bytes) -> bytes:
    """动图/表情包取第一帧并统一转 RGB PNG（保证 ncnn 输入兼容）。"""
    im = Image.open(io.BytesIO(data))
    if getattr(im, "is_animated", False) or (im.format or "").lower() == "gif":
        logger.info(f"[强化] 动图/表情包，截取第一帧 {im.size}")
        im.seek(0)
    im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _ensure_sendable(path: str) -> str:
    """超大 PNG 转 JPEG 重编码，避免 QQ 发送超时/超大小限制；返回最终发送路径。"""
    try:
        size = os.path.getsize(path)
        if size <= 18 * 1024 * 1024:
            return path
        im = Image.open(path)
        jpg_path = os.path.splitext(path)[0] + "_send.jpg"
        im.convert("RGB").save(jpg_path, "JPEG", quality=95)
        logger.info(f"[强化] 输出过大（{size / 1024 / 1024:.1f}MB），已转 JPEG q95")
        return jpg_path
    except Exception as e:
        logger.warning(f"[强化] 转 JPEG 失败，按原文件发送: {e}")
        return path


@upscale_matcher.handle()
async def handle_upscale(event: GroupMessageEvent):
    """处理图片强化请求（单张或批量）"""
    group_id = event.group_id
    if not permissions.allowed(group_id, "upscale", True):
        logger.info(f"[强化] 群{group_id} 已关闭强化权限，拒绝触发")
        await upscale_matcher.finish("这个群还没有开放图片强化功能哦~")
        return
    logger.info("[强化] 开始处理强化请求")
    user_id = str(event.user_id)
    _sender = getattr(event, "sender", None)
    daily_limits.track_user("upscale", group_id, user_id,
                            name=str(getattr(_sender, "card", "") or getattr(_sender, "nickname", "") or ""))
    bot = bot_for(event)

    # 从引用消息提取所有图片（包括合并转发）
    all_images = await _extract_all_images_from_reply(event, bot)

    # 如果引用消息中没有图片，尝试从当前消息中直接提取图片（直接发图+艾特+强化的方式）
    if not all_images:
        for stype, sdata in _iter_segments(event.message):
            if stype == "image":
                url = str(sdata.get("url", "") or "")
                fid = str(sdata.get("file", "") or "")
                if url or fid:
                    all_images.append((url, fid))
        if all_images:
            logger.info(f"[强化] 从当前消息直接提取到 {len(all_images)} 张图")

    if not all_images:
        logger.info("[强化] 引用消息和当前消息里都没有可直接点开的图片，不回复")
        return

    # 多张图片 → 批量强化
    if len(all_images) > 1:
        await _handle_batch_upscale(event, all_images)
        return

    # 单张图片 → 原有逻辑
    image_url, image_file_id = all_images[0]

    # 强化独立队列：同一时刻只处理一张强化
    ticket = enqueue_upscale()
    tmp_input, tmp_output = "", ""
    try:
        if ticket.position > 1:
            queued = queued_upscale_count()
            await upscale_matcher.send(f"⏳ 上一张图还在处理中，你已加入队列（第 {ticket.position} 个请求，当前排队人数：{queued}），请稍候…")
        async with ticket:
            if ticket.position > 1:
                await upscale_matcher.send(Message([
                    MessageSegment.text("✅ 轮到你了，开始强化！"),
                    MessageSegment.at(user_id),
                ]))
            else:
                await upscale_matcher.send(Message([
                    MessageSegment.at(user_id),
                    MessageSegment.text(roles.flavor(" 🖼️ 收到，芙宁娜这就帮你放大~", " 🖼️ 收到，{name}这就帮你放大~")),
                ]))

            bot = bot_for(event)
            image_data = await _fetch_image_bytes(bot, image_file_id, image_url)
            if not image_data:
                await upscale_matcher.finish("❌ 图片读取失败，请重试")
                return

            # 动图/表情包取第一帧，并统一转 PNG 输入
            png_data = _first_frame_rgb(image_data)
            fd, tmp_input = tempfile.mkstemp(prefix="upscale_in_", suffix=".png")
            with os.fdopen(fd, "wb") as f:
                f.write(png_data)
            tmp_output = os.path.splitext(tmp_input)[0] + "_out.png"

            start = time.time()
            try:
                gpu_id = await run_upscale(tmp_input, tmp_output)
            except asyncio.TimeoutError:
                logger.error("[强化] 超分超时")
                await upscale_matcher.finish("❌ 强化超时了，图可能太大，换张小一点的试试~")
                return
            except Exception as e:
                logger.error(f"[强化] 超分失败: {e}", exc_info=True)
                await upscale_matcher.finish("❌ 强化失败了，请稍后再试")
                return
            elapsed = time.time() - start
            logger.info(f"[强化] 超分完成，耗时 {elapsed:.1f}s, GPU 设备号: {gpu_id}")
            daily_limits.track("upscale", group_id)
            daily_limits.track_user("upscale_img", group_id, user_id)

            send_path = _ensure_sendable(tmp_output)
            _pending_files.append(send_path)
            reply = Message([
                MessageSegment.at(user_id),
                MessageSegment.text(roles.flavor(" ✨ 强化完成，收下本导演的恩赐吧", " ✨ 强化完成，收好啦")),
                _make_image_segment_from_path(send_path),
            ])
            try:
                await upscale_matcher.send(reply)
                # 大群强化的图片自动转发到小群
                await _relay_to_subgroup(bot, group_id, image_path=send_path)
            except ActionFailed as e:
                # NapCat 偶发：图片实际上已经发出去了，但 API 返回 ActionFailed（网络波动/超时）
                # 这种情况不再发错误提示，避免用户看到"图片太大发送失败"但图已经在群里了
                logger.warning(f"[强化] 发送返回异常（图片可能已发出）: retcode={getattr(e, 'retcode', '?')} message={getattr(e, 'message', e)}")
                return
    finally:
        ticket.cancel()
        _cleanup_pending()
        for p in (tmp_input, tmp_output):
            try:
                if p and os.path.exists(p) and p not in _pending_files:
                    os.unlink(p)
            except Exception:
                pass
    await upscale_matcher.finish()


async def _handle_batch_upscale(event: GroupMessageEvent, images: list):
    """批量强化图片，最后以合并转发形式发送"""
    group_id = event.group_id
    user_id = str(event.user_id)
    bot = bot_for(event)

    if len(images) > 30:
        await upscale_matcher.finish(f"一次最多强化30张图，你引用了{len(images)}张，少选几张试试~")
        return

    ticket = enqueue_upscale()
    tmp_files = []
    defer_paths = set()  # 发送异常需 5 分钟后补发的输出图：先保留，补发任务结束再删
    try:
        await upscale_matcher.send(Message([
            MessageSegment.at(user_id),
            MessageSegment.text(f" 🖼️ 收到{len(images)}张图，开始批量强化，请稍候..."),
        ]))

        async with ticket:
            output_paths = []
            failed = 0
            for i, (url, file_id) in enumerate(images):
                try:
                    image_data = await _fetch_image_bytes(bot, file_id, url)
                    if not image_data:
                        failed += 1
                        continue

                    png_data = _first_frame_rgb(image_data)
                    fd, tmp_input = tempfile.mkstemp(prefix="upscale_in_", suffix=".png")
                    with os.fdopen(fd, "wb") as f:
                        f.write(png_data)
                    tmp_output = os.path.splitext(tmp_input)[0] + "_out.png"
                    tmp_files.extend([tmp_input, tmp_output])

                    start = time.time()
                    try:
                        await run_upscale(tmp_input, tmp_output)
                    except asyncio.TimeoutError:
                        logger.error(f"[强化] 第{i+1}张超分超时")
                        failed += 1
                        continue
                    except Exception as e:
                        logger.error(f"[强化] 第{i+1}张超分失败: {e}")
                        failed += 1
                        continue

                    send_path = _ensure_sendable(tmp_output)
                    if send_path != tmp_output:
                        tmp_files.append(send_path)  # 超大图转出的 _send.jpg 也要清理（旧版会残留在 %TEMP%）
                    output_paths.append(send_path)
                    logger.info(f"[强化] 第{i+1}/{len(images)}张完成，耗时{time.time()-start:.1f}s")
                except Exception as e:
                    logger.error(f"[强化] 第{i+1}张处理异常: {e}")
                    failed += 1

            if not output_paths:
                await upscale_matcher.finish("❌ 所有图片强化都失败了，请稍后再试")
                return

            # 组装合并转发
            name = roles.display_name()
            uin = config.BOT_QQ
            nodes = []
            for path in output_paths:
                seg = _make_image_segment_from_path(path)
                nodes.append({"type": "node", "data": {"name": name, "uin": uin, "content": [seg]}})

            # 发送合并转发。NapCat 处理含大图的转发时经常「API 先报错、QQ 内核随后才真正发出」，
            # 所以这里不提示失败、也不立即重发：报错就挂一个后台任务，5 分钟后核查，群里仍没有再补发。
            # 主流程不等待，照常往下发完成提示；图片原样发送、不压缩。
            t0 = int(time.time())
            try:
                await bot.send_forward_msg(group_id=group_id, messages=nodes)
                logger.info(f"[强化] 批量强化合并转发发送成功，共{len(output_paths)}张")
            except Exception as e:
                logger.warning(f"[强化] 合并转发 API 异常，安排 5 分钟后核查补发、不提示用户: {e}")
                kept = [os.path.abspath(p) for p in output_paths]
                defer_paths.update(kept)  # 保留输出图，别被 finally 提前删掉
                _t = asyncio.create_task(_delayed_resend(bot, group_id, nodes, t0, kept))
                _bg_tasks.add(_t)
                _t.add_done_callback(_bg_tasks.discard)
            daily_limits.track("upscale", group_id)
            daily_limits.track_user("upscale_img", group_id, user_id, count=len(output_paths))

            # 艾特触发者告知完成（紧跟大群合并转发，避免被小群转发阻塞拖慢）
            finish_msg = roles.flavor(" ✨ {n}张图片强化完成，收下本导演的恩赐吧", " ✨ {n}张图片强化完成，收好啦", n=len(output_paths))
            if failed > 0:
                finish_msg += f"（失败{failed}张）"
            await upscale_matcher.send(Message([
                MessageSegment.at(user_id),
                MessageSegment.text(finish_msg),
            ]))

            # 大群强化的图片自动转发到小群（放最后；小群失败不影响大群与完成提示）
            try:
                await _relay_to_subgroup(bot, group_id, forward_nodes=nodes)
            except Exception as e:
                logger.warning(f"[强化] 转发到小群失败（不影响大群）: {e}")
    finally:
        ticket.cancel()
        _cleanup_pending()
        for p in tmp_files:
            try:
                if p and os.path.abspath(p) in defer_paths:
                    continue  # 留给 5 分钟补发任务，补发/核查后由它清理
                if p and os.path.exists(p) and p not in _pending_files:
                    os.unlink(p)
            except Exception:
                pass
    await upscale_matcher.finish()


# ========== 启动时后台探测 NVIDIA 独显（RTX 5060），确保第一次强化就直接用独显 ==========
@get_driver().on_startup
async def _preload_gpu_on_startup():
    """bot启动后后台探测并缓存 NVIDIA 独显设备号。"""
    task = asyncio.create_task(probe_nvidia_gpu_id())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# 主人私聊发图 → 自动强化后私聊发回
from . import private as _private  # noqa: E402,F401
