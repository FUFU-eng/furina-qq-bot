"""引用/消息段解析工具，供聊天与历史上下文共用。"""

from nonebot import logger
from ...core.botref import bot_for
from nonebot.adapters.onebot.v11 import GroupMessageEvent

from . import config

class QuoteInfo:
    """被引用消息的解析结果。failed=True 表示读取失败。"""

    def __init__(self, images: int, text: str, failed: bool = False,
                 image_segments: list | None = None, sender_name: str = "",
                 sender_id: str = "", forward: bool = False):
        self.forward = forward  # 引用的是合并转发 / 聊天记录（推送的帖子、转发的聊天）
        self.images = images
        self.text = text
        self.failed = failed
        self.image_segments = image_segments or []
        self.sender_name = sender_name
        self.sender_id = sender_id


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


def _node_to_text(msg, _depth: int = 0) -> str:
    """把合并转发里单个节点的消息转成纯文本（嵌套的转发带了内容就展开一层）。"""
    parts = []
    for stype, sdata in _iter_segments(msg):
        if stype == "text":
            t = str(sdata.get("text") or "").strip()
            if t:
                parts.append(t)
        elif stype == "image":
            parts.append("[图片]")
        elif stype == "video":
            parts.append("[视频]")
        elif stype == "face":
            parts.append("[表情]")
        elif stype == "forward":
            inner = sdata.get("content")
            if _depth < 1 and isinstance(inner, list) and inner:
                parts.extend(_node_to_text((n.get("data") or n).get("message") or (n.get("data") or n).get("content"),
                                           _depth + 1) for n in inner[:6] if isinstance(n, dict))
            else:
                parts.append("[合并转发]")
        elif stype == "at":
            parts.append(at_label(sdata.get("qq")) or "[@某人]")
    return " ".join(p for p in parts if p)[:400] if parts else ""


async def _fetch_quote(event: GroupMessageEvent) -> QuoteInfo | None:
    """读取被引用消息的内容。返回 None 表示本条消息没有引用。"""
    # 优先用适配器已经解析好的 event.reply（NoneBot 会把 [CQ:reply] 从 message 里
    # 摘出来存到 event.reply，直接在这里取才是可靠路径，搜图插件也是这么拿图的）
    reply_message = None
    reply_sender = None
    try:
        if event.reply is not None and event.reply.message:
            reply_message = event.reply.message
            reply_sender = event.reply.sender
    except Exception:
        pass

    if reply_message is None:
        # 适配器没能解析出引用（比如 NapCat 缓存缺失），退回去按 reply id 重新拉
        reply_id = ""
        for stype, sdata in _iter_segments(event.message):
            if stype == "reply":
                reply_id = str(sdata.get("id", "") or "")
                break
        if not reply_id or reply_id == "0":
            return None
        try:
            bot = bot_for(event)
            msg_info = await bot.get_msg(message_id=int(reply_id))
            reply_message = msg_info.get("message", [])
            reply_sender = msg_info.get("sender") or {}
        except Exception as e:
            logger.warning(f"[AI聊天] 读取引用消息失败: {e}")
            return QuoteInfo(images=0, text="", failed=True)

    # 被引用消息的发送者昵称（用于告诉模型"这句话是在回复谁"）
    sender_name = ""
    try:
        if isinstance(reply_sender, dict):
            sender_name = str(reply_sender.get("card") or reply_sender.get("nickname") or "")
        elif reply_sender is not None:
            sender_name = str(getattr(reply_sender, "card", "") or getattr(reply_sender, "nickname", "") or "")
    except Exception:
        pass

    # 被引用消息的发送者 QQ（用于判断是不是在引用机器人自己说的话）
    sender_id = ""
    try:
        if isinstance(reply_sender, dict):
            sender_id = str(reply_sender.get("user_id") or "")
        elif reply_sender is not None:
            sender_id = str(getattr(reply_sender, "user_id", "") or "")
    except Exception:
        pass

    info = await _parse_reply_message(reply_message, bot=bot_for(event))
    info.sender_name = sender_name
    info.sender_id = sender_id
    logger.info(f"[AI聊天] 引用了{'聊天记录' if info.forward else '一条消息'}（{sender_name or sender_id or '?'}）："
                f"{(info.text or '没读到内容')[:60]!r}")
    return info


async def _parse_reply_message(reply_message, bot=None) -> QuoteInfo:
    images = 0
    texts = []
    forward_ids = []
    image_segments = []
    inline_nodes: list = []
    is_forward = False

    for stype, sdata in _iter_segments(reply_message):
        if stype == "image":
            images += 1
            texts.append("[图片]")
            image_segments.append((str(sdata.get("file") or ""), str(sdata.get("url") or "")))
        elif stype == "video":
            texts.append("[视频]")
        elif stype == "forward":
            is_forward = True
            inline = sdata.get("content")  # 新版 NapCat 会把节点直接带在转发段里
            if isinstance(inline, list) and inline:
                inline_nodes.extend(inline)
            else:
                forward_ids.append(str(sdata.get("id", "") or ""))
        elif stype in ("json", "share"):
            card = _multimsg_text(sdata)
            if card is not None:  # 聊天记录卡片
                is_forward = True
                texts.append(card)
            else:
                texts.append(_card_text(sdata))
        elif stype == "text":
            t = str(sdata.get("text") or "").strip()
            if t:
                texts.append(t)
        elif stype == "face":
            texts.append("[表情]")
        elif stype == "at":
            texts.append(at_label(sdata.get("qq")) or "[@某人]")

    forward_image_segments = []
    image_limit = max(0, config.VISION_MAX_IMAGES - len(image_segments))
    texts.extend(await _expand_forwards(
        inline_nodes, forward_ids, bot,
        image_segments=forward_image_segments, image_limit=image_limit,
    ))
    for image_segment in forward_image_segments:
        if image_segment not in image_segments:
            image_segments.append(image_segment)
            images += 1

    full = "\n".join(t for t in texts if t)
    return QuoteInfo(images=images, text=full[: config.QUOTE_MAX_CHARS], failed=False, image_segments=image_segments,
                     forward=is_forward or bool(forward_ids))


def _append_forward_images(nodes, image_segments: list, max_images: int) -> None:
    """从合并转发节点收集可交给视觉模型的图片引用；限制节点数、图片数和 inline 嵌套深度。"""
    max_images = max(0, int(max_images))
    if max_images <= len(image_segments) or not isinstance(nodes, (list, tuple)):
        return
    seen = set(image_segments)

    def _walk(current_nodes, depth: int = 0) -> None:
        if len(image_segments) >= max_images or depth > 2:
            return
        for node in current_nodes[:config.FORWARD_MAX_NODES]:
            if not isinstance(node, dict):
                continue
            payload = node.get("data") if node.get("type") == "node" else node
            payload = payload if isinstance(payload, dict) else node
            message = payload.get("message") or payload.get("content") or []
            if isinstance(message, dict):
                message = [message]
            for stype, sdata in _iter_segments(message):
                if stype == "image":
                    ref = (str(sdata.get("file") or ""), str(sdata.get("url") or ""))
                    if (ref[0] or ref[1]) and ref not in seen:
                        image_segments.append(ref)
                        seen.add(ref)
                elif stype in ("forward", "longmsg") and depth < 2:
                    inline = sdata.get("content")
                    if isinstance(inline, list) and inline:
                        _walk(inline, depth + 1)
                if len(image_segments) >= max_images:
                    return

    _walk(nodes)


async def _inline_nested(bot, nodes) -> None:
    """SnowLuma 的内层合并转发只给 id：补成内联 content，后面的文字/看图展开才看得到里面。"""
    try:
        from ...core import forwards

        await forwards.inline_nested(bot, nodes)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[AI聊天] 补全内层合并转发失败: {e}")


async def _expand_forwards(inline_nodes: list, forward_ids: list, bot=None,
                           image_segments: list | None = None, image_limit: int | None = None) -> list[str]:
    """合并转发 → 「[合并转发内容]\n谁: 说了什么」；文字与图片都只按节点/视觉上限展开。"""
    texts = []
    max_images = config.VISION_MAX_IMAGES if image_limit is None else image_limit
    if inline_nodes:
        nodes = inline_nodes[: config.FORWARD_MAX_NODES]
        await _inline_nested(bot, nodes)
        if image_segments is not None:
            _append_forward_images(nodes, image_segments, max_images)
        lines = _forward_lines(nodes)
        texts.append("[合并转发内容]\n" + "\n".join(lines) if lines else "[合并转发]")
    for fid in forward_ids[:3]:
        try:
            bot = bot or bot_for()
            # NapCat 的转发 id 不一定是纯数字；适配器返回的已经是 data 本身（旧代码多取了一层 data，永远是空的）
            fwd = await bot.get_forward_msg(id=int(fid) if fid.isdigit() else fid)
            fwd = fwd or {}
            data = fwd.get("data") if isinstance(fwd, dict) else {}
            data = data if isinstance(data, dict) else {}
            node_msgs = (fwd.get("messages") or data.get("messages") or [])[: config.FORWARD_MAX_NODES]
            await _inline_nested(bot, node_msgs)
            if image_segments is not None:
                _append_forward_images(node_msgs, image_segments, max_images)
            lines = _forward_lines(node_msgs)
            texts.append("[合并转发内容]\n" + "\n".join(lines) if lines else "[合并转发]")
        except Exception as e:
            logger.warning(f"[AI聊天] 读取合并转发失败: {e}")
            texts.append("[合并转发]")
    return texts


async def forward_text(message, bot=None, image_segments: list | None = None) -> str:
    """展开直接发送的合并转发；可选同时返回其中的图片引用，供本轮视觉输入使用。"""
    inline_nodes: list = []
    forward_ids: list = []
    for stype, sdata in _iter_segments(message):
        if stype != "forward":
            continue
        inline = sdata.get("content")
        if isinstance(inline, list) and inline:
            inline_nodes.extend(inline)
        elif sdata.get("id"):
            forward_ids.append(str(sdata.get("id")))
    if not inline_nodes and not forward_ids:
        return ""
    expanded = await _expand_forwards(inline_nodes, forward_ids, bot, image_segments=image_segments)
    return "\n".join(expanded)[: config.QUOTE_MAX_CHARS]


def _forward_lines(nodes) -> list[str]:
    lines = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        data = node.get("data") if node.get("type") == "node" else None
        node = data or node
        sender = node.get("sender") or {}
        name = sender.get("card") or sender.get("nickname") or node.get("nickname") or node.get("name") or "群友"
        node_text = _node_to_text(node.get("message") or node.get("content"))
        if node_text:
            lines.append(f"{name}: {node_text}")
    return lines


def _multimsg_text(sdata):
    """QQ「聊天记录」卡片（com.tencent.multimsg）→ 「[聊天记录] 预览几行」；不是这种卡片返回 None。"""
    import json as _json
    raw = sdata.get("data") if isinstance(sdata, dict) else None
    try:
        card = _json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return None
    if not isinstance(card, dict) or not (card.get("app") == "com.tencent.multimsg" or "聊天记录" in str(card.get("prompt") or "")):
        return None
    detail = (card.get("meta") or {}).get("detail") or {}
    news = [str(x.get("text") or "").strip() for x in (detail.get("news") or []) if isinstance(x, dict)]
    news = [x for x in news if x]
    head = str(detail.get("source") or "聊天记录").strip()
    return f"[{head}]" + ("\n" + "\n".join(news[:6]) if news else "")


def _card_text(sdata) -> str:
    """QQ 小程序 / 分享卡片 → 「[卡片] 标题（简介）」，若含 B 站 BV 号或 b23.tv 短链也一并保留，方便直接看视频。"""
    import json as _json
    import re as _re
    raw = sdata.get("data") if isinstance(sdata, dict) else None
    raw_str = raw if isinstance(raw, str) else _json.dumps(sdata or {}, ensure_ascii=False)
    title = desc = ""
    try:
        card = _json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        card = {}
    if isinstance(card, dict):
        for item in (card.get("meta") or {}).values():
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("prompt") or "").strip()
                desc = str(item.get("desc") or "").strip()
                if title or desc:
                    break
        if not title:
            title = str(card.get("prompt") or "").strip()
    if not title and isinstance(sdata, dict):
        title = str(sdata.get("title") or "").strip()
        desc = desc or str(sdata.get("content") or "").strip()
    title, desc = title[:40], desc[:60]
    bili_ref = ""
    raw_unesc = raw_str.replace("\\/", "/")
    bm = _re.search(r"(BV[0-9A-Za-z]{10})", raw_unesc)
    if bm:
        bili_ref = f" {bm.group(1)}"
    else:
        um = _re.search(r"https?://(?:b23\.tv|bili2233\.cn|(?:www\.|m\.)?bilibili\.com/video)/[0-9A-Za-z_\-]+", raw_unesc)
        if um:
            bili_ref = f" {um.group(0)}"
    if desc and desc != title:
        base = f"[卡片] {title}（{desc}）" if title else f"[卡片] {desc}"
    else:
        base = f"[卡片] {title}" if title else "[卡片]"
    return base + bili_ref


def at_label(qq) -> str:
    """艾特段 → 「@昵称」（NapCat 的艾特段只有 QQ 号）：按这个人在群里说话时的名字认；认不出写「@某人」。
    艾特机器人自己返回空串（handlers 另外判断「艾特了她」）。"""
    qq = str(qq or "").strip()
    if not qq or qq == "0":
        return ""
    if qq == "all":
        return "@全体成员"
    try:
        from nonebot import get_bots
        if qq == str(config.BOT_QQ) or qq in get_bots():
            return ""
    except Exception:  # noqa: BLE001
        if qq == str(config.BOT_QQ):
            return ""
    if qq == str(config.GROUP_OWNER_QQ):
        return "@" + OWNER_LABEL
    name = ""
    try:
        from . import people
        name = str((people.get(qq) or {}).get("name") or "")
    except Exception:  # noqa: BLE001
        name = ""
    return "@" + (name or "某人")


def _extract_text(message) -> str:
    parts = []
    last = ""  # 艾特和前后的字是同一句话：「叫@小燕，不是我」，别拆成三行
    for stype, sdata in _iter_segments(message):
        if stype == "at":
            label = at_label(sdata.get("qq"))
            if label:
                if parts and last == "text":
                    parts[-1] += label
                else:
                    parts.append(label)
                last = "at"
            continue
        if stype == "text":
            t = str(sdata.get("text") or "").strip()
            if t and last == "at" and parts:
                parts[-1] += ("" if t[0] in "，,。.!！?？：:" else " ") + t
            elif t:
                parts.append(t)
            last = "text" if t else last
            continue
        last = stype
        if stype in ("json", "share"):
            card = _multimsg_text(sdata) if stype == "json" else None
            parts.append(card if card is not None else _card_text(sdata))
        elif stype == "record":
            parts.append("[语音]")
        elif stype == "forward":
            parts.append("[合并转发]")
    return "\n".join(parts)[: config.QUOTE_MAX_CHARS]


def _has_image(message) -> bool:
    return any(stype == "image" for stype, _ in _iter_segments(message))


# 群主在提示词里统一显示成这个名字（commands._match_member 据此把「艾特群主」认回真群主）
OWNER_LABEL = "我超爱芙啦（群主）"


def _sender_name(event) -> str:
    # 群主统一标注成"我超爱芙啦（群主）"，避免模型把群主认成别人
    if str(getattr(event, "user_id", "")) == config.GROUP_OWNER_QQ:
        return OWNER_LABEL
    sender = getattr(event, "sender", None)
    if sender is None:
        return f"群友{event.user_id}"
    # OneBot 的 sender 可能是 dict，也可能是 pydantic 的 Sender 对象，都要兼容
    if isinstance(sender, dict):
        card = sender.get("card") or sender.get("nickname") or ""
    else:
        card = getattr(sender, "card", "") or getattr(sender, "nickname", "") or ""
    return str(card or f"群友{event.user_id}")

