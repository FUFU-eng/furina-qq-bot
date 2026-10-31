"""QQ 谷歌搜图机器人插件入口：触发规则、消息处理、启动预加载。"""

import asyncio
from contextvars import ContextVar
import base64
import os
import random
import re
import tempfile
import threading
import time

from nonebot import get_driver, logger, on_command, on_message
from ...core.botref import bot_for
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.rule import Rule

from . import browser, config, download, runtime, search as search_mod
from ...core import images, permissions, roles, usage as daily_limits
from .download import _pick_eagle_consolation, download_image, make_image_segment
from ...core.task_queue import enqueue_search, queued_search_count

# 待发送图片的临时文件（发送完成后统一清理）
_pending_image_files = ContextVar("search_pending_image_files", default=None)

# SnowLuma / NapCat 等 OneBot V11 传输端会用无 CQ: 前缀的现代格式 [at:qq=X] / [reply:id=X]，旧版是 [CQ:at,qq=X] / [CQ:reply,id=X]；引用消息 ID 也可能为负数。
_AT_RE = re.compile(r"\[(?:CQ:)?at[:,]qq=(\d+)\]")
_REPLY_RE = re.compile(r"\[(?:CQ:)?reply[,:]id=(-?\d+)\]")


def _cleanup_pending_images():
    """删除发送用的临时图片文件，防止磁盘泄漏。"""
    pending = _pending_image_files.get()
    while pending:
        path = pending.pop()
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass


def _append_image_from_bytes(message: Message, data: bytes):
    """把图片字节以本地文件路径形式加入消息（避免超大 base64 导致发送超时）。"""
    seg, path = make_image_segment(data)
    pending = _pending_image_files.get()
    if pending is None:
        pending = []; _pending_image_files.set(pending)
    pending.append(path)
    message.append(seg)


async def _send_group_message_with_retry(group_id: int, message, retries: int = 2, event=None):
    """Compatibility name: send once. A timeout is uncertain, NOT proof of failure."""
    await bot_for(event).send_group_msg(group_id=group_id, message=message)


async def _check_trigger(event) -> bool:
    """检查消息是否触发搜图（唯一方式：引用目标图片 + 艾特机器人 + 输入"搜图"；其余一律无效）"""
    msg = event.raw_message
    # 真正的 [CQ:at]/[at:qq=] 段是首选判定；兼容"@芙宁娜/@芙芙"这类纯文本艾特（部分客户端不生成 CQ 段）
    at_ids = set(_AT_RE.findall(msg))
    self_ids = {str(getattr(event, "self_id", "")), str(config.BOT_QQ)} - {""}
    has_at_bot = bool(at_ids & self_ids) or roles.is_bot_at(event)
    has_reply = bool(_REPLY_RE.search(msg))
    # 支持多种搜图触发表达：搜图、搜一下图、搜张图、搜个图、搜搜图、找图、找一下图、识图、以图搜图
    has_search_word = bool(re.search(r"(搜|找|识|以图).{0,4}图", msg)) or "搜图" in msg

    # 群聊
    if isinstance(event, GroupMessageEvent):
        # 唯一触发方式：引用（回复）目标图片 + 艾特机器人 + 输入"搜图"
        if has_reply and has_at_bot and has_search_word:
            logger.info(f"[搜图] 群聊触发搜图（引用图片+艾特+搜图），群号: {event.group_id}, 用户: {event.user_id}")
            return True

    return False


# 搜图触发器
pic_search = on_message(rule=Rule(_check_trigger), priority=10, block=True)

# 测试命令
ping_cmd = on_command("ping", priority=5, block=True)


@ping_cmd.handle()
async def handle_ping(event):
    """测试命令：ping"""
    if isinstance(event, GroupMessageEvent):
        logger.info(f"[ping] 收到ping命令，群号: {event.group_id}, 用户: {event.user_id}")
    else:
        logger.info(f"[ping] 收到ping命令，私聊用户: {event.user_id}")
    await ping_cmd.finish("🏓 pong! 机器人在线~")


def _build_no_result_reply(user_id: str) -> Message:
    """谷歌零结果时的回复：艾特触发人 + 提示 + 赠礼（安慰奖）"""
    reply = Message()
    reply.append(MessageSegment.at(user_id))
    reply.append("\n❌ 没搜到结果，换张图试试？\n")
    try:
        consolation = _pick_eagle_consolation()
        if consolation:
            b64 = base64.b64encode(consolation).decode()
            reply.append(roles.flavor("🎁 收下吧，这是芙宁娜的赠礼~\n", "🎁 收下吧，这是{name}的赠礼~\n"))
            reply.append(MessageSegment.image(f"base64://{b64}"))
    except Exception as e:
        logger.warning(f"[搜图] 安慰奖发送失败: {e}")
    return reply


@pic_search.handle()
async def handle_pic_search(event):
    """处理搜图请求"""
    _pending_image_files.set([])  # Each request owns its send files, including while queued.
    group_id = event.group_id
    if not permissions.allowed(group_id, "search", True):
        await pic_search.finish("这个群还没有开放搜图功能哦~")
        return
    logger.info("[搜图] 开始处理搜图请求")

    # ===== 反谷歌风控：每用户冷却 =====
    user_id = str(event.user_id)
    _sender = getattr(event, "sender", None)
    daily_limits.track_user("search", group_id, user_id,
                            name=str(getattr(_sender, "card", "") or getattr(_sender, "nickname", "") or ""))
    now = time.time()
    last = runtime._user_last_search.get(user_id, 0)
    if now - last < config.SEARCH_COOLDOWN:
        wait_s = int(config.SEARCH_COOLDOWN - (now - last)) + 1
        logger.info(f"[搜图] 用户 {user_id} 触发冷却，需等待 {wait_s} 秒")
        await pic_search.finish(f"⏳ 搜图太频繁啦，请 {wait_s} 秒后再试~")
        return

    # 提取图片 URL 和 file_id
    image_url = ""
    image_file_id = ""

    def _pick_single_image(segments):
        """从消息段中挑出唯一的查询图。
        多图/视频明确不支持搜图，返回拒绝原因。返回 (url, file_id, 拒绝原因)"""
        images = []
        has_video = False
        for seg in segments:
            if isinstance(seg, dict):
                stype = seg.get("type", "")
                sdata = seg.get("data", {})
            else:
                stype = seg.type
                sdata = seg.data
            if stype == "video":
                has_video = True
            elif stype == "image":
                images.append(sdata)
        if has_video:
            return "", "", "视频暂不支持搜图"
        if len(images) > 1:
            return "", "", "多图暂不支持搜图，请只引用一张图片"
        if len(images) == 1:
            return images[0].get("url", ""), images[0].get("file", ""), ""
        return "", "", ""

    image_url, image_file_id, reject_reason = "", "", ""
    # 只从被引用的消息里提取图片（触发规则是"引用图片 + 艾特机器人"）
    if hasattr(event, 'reply') and event.reply:
        logger.info("[搜图] 从回复消息中提取图片...")
        image_url, image_file_id, reject_reason = _pick_single_image(event.reply.message)
    # NapCat 解析不出引用消息时（旧版客户端/消息缓存缺失），用 API 按回复 id 直接拉取原消息
    if not image_url and not image_file_id and not reject_reason:
        reply_id = ""
        for seg in event.message:
            if seg.type == "reply":
                reply_id = str(seg.data.get("id", ""))
                break
        if reply_id and reply_id != "0":
            try:
                bot = bot_for(event)
                logger.info(f"[搜图] 尝试用 API 按回复 id={reply_id} 拉取引用消息...")
                msg_info = await bot.get_msg(message_id=int(reply_id))
                image_url, image_file_id, reject_reason = _pick_single_image(msg_info.get("message", []))
                if image_url or image_file_id:
                    logger.info("[搜图] ✅ 通过 get_msg 成功获取回复消息的图片")
            except Exception as e:
                logger.warning(f"[搜图] 通过 get_msg 获取回复消息失败: {e}")

    # 引用的消息里没有"可以直接点开的图片"（纯文字/视频/读取失败）：不回复
    if reject_reason == "视频暂不支持搜图":
        logger.info("[搜图] 引用消息里只有视频、没有可点开的图片，不回复")
        return
    if reject_reason:
        if "多图" in reject_reason:
            await pic_search.finish("图需慢慢搜，心急则魂散，只引用一张图试试吧~")
            return
        await pic_search.finish(f"❌ {reject_reason}，请引用一张单独的图片并艾特机器人")
        return

    if not image_url and not image_file_id:
        logger.info("[搜图] 引用消息里没有可直接点开的图片，不回复")
        return

    logger.info(f"[搜图] 图片URL: {image_url[:100]}...")
    logger.info(f"[搜图] 图片file_id: {image_file_id}")

    try:
        image_data = None

        # 优先用协议端本地缓存（NapCat）；SnowLuma 没有本地缓存，但会给一个刚刷新过的下载地址
        fresh_url = ""
        if image_file_id:
            local_path, fresh_url = await images.lookup_image(bot_for(event), image_file_id)
            if local_path:
                try:
                    with open(local_path, "rb") as f:
                        image_data = f.read()
                    logger.info(f"[搜图] 从本地缓存获取图片成功: {local_path}")
                except OSError as e:
                    logger.warning(f"[搜图] 读本地缓存失败，尝试下载: {e}")

        # 本地没有的话，再尝试下载（先用刷新过的地址，再用消息里的原地址）
        if not image_data:
            try:
                for candidate in images.download_candidates(fresh_url, image_url):
                    image_data = await download_image(candidate)
                    if image_data:
                        break
                if not image_data:
                    await pic_search.finish("❌ 图片下载失败了")
                    return
            except Exception as e:
                logger.error(f"[搜图] 下载图片异常: {e}")
                await pic_search.finish("❌ 图片下载失败了")
                return

        logger.info(f"[搜图] 图片获取成功，大小: {len(image_data)} 字节")

        # 动图/表情包：取第一帧作为查询图（Google Lens 对动图支持不稳定）
        try:
            from PIL import Image
            from io import BytesIO
            im = Image.open(BytesIO(image_data))
            fmt = (im.format or "").lower()
            if fmt == "gif" or (fmt == "webp" and getattr(im, "is_animated", False)):
                frame = im.convert("RGB")
                buf = BytesIO()
                frame.save(buf, "JPEG", quality=90)
                image_data = buf.getvalue()
                logger.info(f"[搜图] 动图已取第一帧，大小: {len(image_data)} 字节")
        except Exception as e:
            logger.warning(f"[搜图] 动图取帧失败（按原图处理）: {e}")

        # 保存图片到临时文件
        try:
            tmp_file = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            tmp_file.write(image_data)
            tmp_file.close()
            tmp_path = tmp_file.name
        except Exception as e:
            logger.error(f"[搜图] 保存临时图片失败: {e}")
            await pic_search.finish("❌ 图片处理失败，请重试")
            return

        # ===== 搜图独立队列：同一时刻只处理一张搜图（FIFO 排队） =====
        # 入队时就记下冷却起点：旧版只在轮到开始搜时才记录，同一个人排队期间可以连续塞好几单
        runtime._user_last_search[user_id] = time.time()
        ticket = enqueue_search()
        search_elapsed = 0.0
        try:
            if ticket.position > 1:
                queued = queued_search_count()
                await pic_search.send(f"⏳ 上一张图还在处理中，你已加入队列（第 {ticket.position} 个请求，当前排队人数：{queued}），请稍候…")
            async with ticket:
                # 排队等待轮到自己
                # 记录本次搜图开始时间（用于冷却和上限统计）
                runtime._user_last_search[user_id] = time.time()
                # 反风控：如果刚搜完不久，随机等几秒再开始，打散请求节奏
                if time.time() - runtime._last_search_end < 60:
                    delay = random.uniform(*config.SEARCH_RANDOM_DELAY)
                    logger.info(f"[搜图] 连续搜图，随机延迟 {delay:.1f} 秒")
                    await asyncio.sleep(delay)
                if ticket.position > 1:
                    # 轮到排队用户时，艾特发起搜图的人，避免在群里分不清是谁的请求
                    await pic_search.send(Message([
                        MessageSegment.text("✅ 轮到你了，开始搜索！"),
                        MessageSegment.at(user_id),
                    ]))
                else:
                    await pic_search.send("🎬 聚光灯就位，搜寻开始……")

                # 在线程池中运行谷歌搜图，加超时，防止卡住
                # 超时只计算实际搜图时间，不包含排队等待时间
                search_start_time = time.time()
                try:
                    results = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(
                            runtime.executor,
                            search_mod.search_with_google,
                            tmp_path
                        ),
                        timeout=config.SEARCH_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.error("[搜图] 搜图超时，强制中止并重置浏览器")
                    # 线程池里的线程无法被 asyncio 取消，必须主动中止它并销毁浏览器，
                    # 否则它会一直占着浏览器和锁，导致后续所有搜图全部超时
                    browser._force_reset_driver()
                    await pic_search.finish(f"❌ 搜图超时了（{config.SEARCH_TIMEOUT}秒），请重试一下~")
                    return
                search_elapsed = time.time() - search_start_time
                # 本次搜图已结束（浏览器锁已释放）
        finally:
            ticket.cancel()
            runtime._last_search_end = time.time()
            # 确保临时文件一定被删除，防止泄漏
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                    logger.debug(f"[搜图] 临时文件已删除: {tmp_path}")
            except Exception as e:
                logger.warning(f"[搜图] 删除临时文件失败: {e}")

        if not results.get("original_image") and not results.get("pages") and not results.get("yandex_sources"):
            if results.get("search_error"):
                if results["search_error"] == "browser_setup":
                    await pic_search.finish("⚠️ 以图搜图的 Edge 驱动未准备好，暂时无法搜索。请检查驱动版本/下载网络；这不是 Pixiv 点图错误。")
                else:
                    reason = {
                        'upload_control_missing': 'Google 图片上传界面未就绪',
                        'upload_reference_invalid': 'Google 未接受上传图片，重新上传仍未恢复',
                        'verification_required': 'Google 要求人工验证，自动搜索已停止',
                        'result_wait_timeout': 'Google 检索结果加载超时',
                    }.get(results.get('google_error'), '搜索来源或视觉比对服务暂时不可用')
                    await pic_search.finish(f"⚠️ {reason}；备用搜索也未得到可靠结果。这不是已确认的无匹配结果。")
                return
            if results.get("proxy_error"):
                await pic_search.finish("⚠️ 网络或代理异常（梯子可能掉了），请检查代理后再试~")
                return
            logger.warning(f"[搜图] 没找到任何结果")
            await pic_search.finish(_build_no_result_reply(user_id))
            return

        # 构造回复消息
        reply = Message()
        reply.append(MessageSegment.at(user_id))
        reply.append("\n")

        def _append_consolation():
            """附赠图：从 Eagle 图库随机选一张 + 文案"""
            try:
                consolation = _pick_eagle_consolation()
                if consolation:
                    reply.append(roles.flavor("🎁 收下吧，这是芙宁娜的赠礼~\n", "🎁 收下吧，这是{name}的赠礼~\n"))
                    _append_image_from_bytes(reply, consolation)
            except Exception as e:
                logger.warning(f"[搜图] 安慰奖发送失败: {e}")

        daily_limits.track("search", group_id)
        # 原图（放在最前面）
        if results.get("original_image"):
            vision = results.get("vision") or {}
            dhash_sim = results.get("original_similarity", 100)
            vision_sim = float(vision.get("similarity") or 0)
            same_image = bool(vision.get("same_image"))
            same_character = bool(vision.get("same_character"))
            # 同一张图：视觉确认同一张，或视觉不可用时 dHash 直接命中；
            # 视觉模型判 same_image=False 时，相似度再高也只算相似图，避免同角色不同图冒充原图
            is_original = (
                bool(results.get("original_from_exact_match"))
                or same_image
                or (not vision and dhash_sim >= 50)
            )
            if (same_character and not same_image
                    and vision_sim >= config.VISION_ORIGINAL_SIM_THRESHOLD):
                logger.info(f"[搜图] 视觉相似度 {vision_sim} 但 same_image=False，不按高清原图输出")
            if is_original:
                reply.append("🖼️ 高清原图\n")
            elif (same_character and vision_sim >= config.VISION_SIM_THRESHOLD) or dhash_sim >= 35:
                reply.append("😔 很遗憾，只寻得相似图\n")
            else:
                reply.append("😔 只找到一张不确定是否相关的图，仅供参考\n")
            try:
                # 优先用已经下载好的图片数据，没有的话再下载
                img_data = results.get("original_image_data")
                if not img_data:
                    img_data = await download_image(results["original_image"])

                if img_data:
                    # 原图原样发送，不做任何压缩/尺寸缩减（用户要求输出必须是原图大小）
                    # 用本地临时文件路径发送，避免超大 base64 触发 NapCat 发送超时
                    logger.info(f"[搜图] 发送原图大小: {len(img_data)} 字节")
                    try:  # 找到的原图收进筛图库（不影响发送）
                        from ..pic_inbox import hook as _pic_hook
                        _pic_hook("search", data=img_data, gid=group_id, uid=user_id)
                    except Exception:
                        pass
                    _append_image_from_bytes(reply, img_data)
                else:
                    reply.append(f"原图链接：{results['original_image']}\n")
                reply.append("\n")
            except Exception as e:
                logger.warning(f"[搜图] 发送原图失败: {e}")
                reply.append(f"原图链接：{results['original_image']}\n")
            if not is_original:
                # 只找到相似图：同样附赠图
                _append_consolation()
        else:
            if results.get('search_error'):
                reply.append("⚠️ 部分搜索/取图服务失败，未取得可信原图；不能据此认定没有匹配。\n")
            else:
                reply.append("❌ 未在白名单网站找到原图\n")
                _append_consolation()

        # Yandex 定位到来源但图片被反爬拦截：展示来源链接让用户手动打开
        yandex_sources = results.get("yandex_sources") or []
        if yandex_sources and not results.get("original_image"):
            reply.append("\n🔎 Yandex 候选来源（尚未确认是同一张图，可手动核对）：\n")
            for i, link in enumerate(yandex_sources[:2]):
                reply.append(f"{'①' if i == 0 else '②'} {link}\n")

        # 来源网页：只在取到图时显示，且只写最终取图的那个网站
        if results.get("original_image") and results.get("original_page"):
            reply.append("\n📄 来源网页：\n")
            reply.append(f"{results['original_page']}\n")

        # 末尾附上本次搜寻用时
        reply.append(f"\n（本次搜寻用时：{search_elapsed:.1f} 秒）")

        # 回复发出去后再后台预加载 Lens 上传页，避免和"下载原图"抢浏览器
        browser.preload_upload_page_in_background()
        try:
            await _send_group_message_with_retry(event.group_id, reply, event=event)
        except ActionFailed as e:
            logger.error(
                f"[搜图] 消息发送失败: retcode={getattr(e, 'retcode', '?')}, "
                f"message={getattr(e, 'message', e)}"
            )
            # 兜底：只发文字信息，避免用户什么都收不到
            try:
                fallback = Message()
                fallback.append(MessageSegment.at(event.user_id))
                fallback.append("\n搜图完成，但图片消息发送超时了（可能图片过大）。\n")
                if results.get("original_image"):
                    fallback.append(f"原图链接：{results['original_image']}\n")
                elif results.get("original_page"):
                    fallback.append(f"来源页面：{results['original_page']}\n")
                fallback.append(f"（本次搜寻用时：{search_elapsed:.1f} 秒）")
                await _send_group_message_with_retry(event.group_id, fallback, retries=1, event=event)
            except Exception as e2:
                logger.error(f"[搜图] 兜底消息发送失败: {e2}")
        finally:
            _cleanup_pending_images()
        await pic_search.finish()

    except Exception as e:
        # 跳过 NoneBot 的 finish 异常（这是正常的）
        if "FinishedException" in str(type(e)):
            raise
        if isinstance(e, ActionFailed):
            logger.error(
                f"[搜图] 发送失败: retcode={getattr(e, 'retcode', '?')} "
                f"message={getattr(e, 'message', e)}"
            )
        else:
            logger.error(f"[搜图] 出错了: {e}", exc_info=True)
        if isinstance(e, ActionFailed) or "NetWorkError" in type(e).__name__:
            logger.warning("[搜图] QQ 发送结果不确定，不重复发送错误通知")
            return
        await pic_search.finish("❌ 以图搜图暂时失败，详细原因已记录到日志。")
    finally:
        _cleanup_pending_images()


# ========== 浏览器预启动：bot启动时就打开浏览器，预加载Google Lens，加快首次响应速度 ==========
@get_driver().on_startup
async def _preload_browser_on_startup():
    """bot启动完成后，后台预启动浏览器并加载Google Lens页面"""
    def _preload_thread():
        acquired = False
        try:
            logger.info("[搜图] 正在预启动浏览器，预加载Google Lens...")
            driver = browser._get_driver(force_reset_on_stuck=False, lock_timeout=5.0)
            if driver is not None:
                acquired = True
                from . import lens_ui
                lens_ui.open_upload(driver, lambda fn: browser.minimized_navigate(driver, fn))
                browser.keep_window_minimized(driver)
                logger.info("[搜图] 浏览器预启动完成，已验证 Google 图片上传控件（尚未验证检索）")
        except Exception as e:
            logger.warning(f"[搜图] 浏览器预启动失败: {e}")
        finally:
            # 关键：预加载拿到浏览器后必须释放锁，否则第一个搜图请求会永远等锁直到超时；
            # 没拿到浏览器（锁被别人占用）时不能释放，否则会误放别人的锁
            if acquired:
                browser._release_driver()

    # 后台线程运行，不阻塞bot启动
    threading.Thread(target=_preload_thread, daemon=True).start()
