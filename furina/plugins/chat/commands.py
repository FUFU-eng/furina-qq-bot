"""自然语言指令解析与执行（发图/艾特/设精华）。"""

import asyncio
import os
import random
import re
import tempfile
import uuid

from nonebot import get_driver, logger
from ...core.botref import bot_for
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from . import config
from ...core import env, permissions, roles, usage as daily_limits

# ========== 指令执行：艾特某人 / 发图发表情 / 设为精华 ==========

# Eagle 图库路径（与搜图赠礼共用同一个图库，同一个 .env 键 EAGLE_LIBRARY_DIR）
def _eagle_library_dir() -> str:
    return env.get("EAGLE_LIBRARY_DIR", r"D:\Pictures\Eagle.library")

# 设精华：整句必须就是“设精华”这类短指令。旧规则任何以“精”结尾（妖精/戏精/狐狸精）
# 或含“精华”（精华液）的话都会被当成设精华，机器人回“要引用一条消息才能设为精华”而不聊天。
_ESSENCE_RE = re.compile(
    r"^(?:请|麻烦|帮我|帮忙|给我)?\s*(?:把|给)?\s*(?:这条|这个|这句|这|它)?\s*(?:消息|话)?\s*"
    r"(?:"
    r"(?:设为|设成|设置为|设置成|设|标记为|标记|加|弄|整|搞|来|上)?\s*(?:个|一个|成|为)?\s*(?:精华消息|精华帖|精华|精帖)"
    r"|(?:加|设)\s*(?:个|一个|成|为)?\s*精"
    r")\s*(?:一下|吧|呗|啊|呀|哦|嘛)?\s*[~～!！。.]*$"
)

_CN_NUM = r"(?:[一二两三四五六七八九十\d]+)"
# 发图：动词必须在句首（允许“请/快/再/能不能/给我”等前缀和纯文本 @芙芙），修饰语不含
# 代词和“了”。旧规则用 search 不锚定句首，“他给我发了一张图”“我刚发的图”“给我看看你的头像”
# 这类陈述句都会被当成发图指令，随机甩出图库图片。
_IMG_CMD_RE = re.compile(
    r"^(?:@?(?:芙宁娜|芙芙)\s*[,，:：]?\s*)?"
    r"(?:请|麻烦|快|再|那|能不能|可以|可不可以|你)?\s*(?:给我|给大家|帮我)?\s*(?:再)?\s*"
    r"(?:发|来|给|整|弄|搞|要|有没有|来张|发张|整个|弄个|给我|我要|我想要)\s*"
    rf"{_CN_NUM}?\s*(?:张|个|点)?\s*"
    r"(?:(?![了你我他她它])[\u4e00-\u9fa5]){0,4}"
    r"(?:图|图片|表情|表情包|表情图|美图|壁纸|头像)"
    r"\s*(?:呗|吧|啊|呀|一下|嘛)?\s*[~～!！。.?？]*$"
)


def _strip_bot_prefix(t: str) -> str:
    """去掉句首对当前角色的纯文本称呼（如「@芙宁娜-黑芙 来张图」「@胡桃，来张图」→「来张图」）。"""
    for alias in roles.mention_aliases():
        m = re.match(rf"^[@＠]?{re.escape(alias)}(?:[-－—_·•\s]*(?:白芙|黑芙))?\s*[,，:：]?\s*", t, flags=re.IGNORECASE)
        if m:
            return t[m.end():]
    return t


def _owner_uid(entries, roles_by_uid, owner_qq: str) -> str:
    if owner_qq and any(uid == owner_qq for uid, _ in entries):
        return owner_qq
    for uid, _ in entries:
        if roles_by_uid.get(uid) == "owner":
            return uid
    return ""


def _match_member(members, target: str, owner_qq: str | None = None) -> str:
    """按群名片/昵称找群友 QQ：先精确匹配，再“名字包含目标”，最后“目标包含名字”。

    旧逻辑只看 nickname，且 `nickname in target` 对空昵称恒为真，会艾特到第一个没有昵称的人。
    群主按 QQ 号认：目标里带「群主」、或正是提示词里群主的显示名时直接找真群主
    （.env 的 AI_CHAT_GROUP_OWNER_QQ，没配就看群角色 owner）；同一档里有好几个同名的人时，
    优先群主、再管理员，避免有人把昵称改成和群主一样就被艾特成“群主”。
    """
    from .quotes import OWNER_LABEL

    target = (target or "").strip()
    if not target:
        return ""
    owner_qq = str(config.GROUP_OWNER_QQ if owner_qq is None else owner_qq or "")
    entries, roles_by_uid = [], {}
    for m in members or []:
        uid = str(m.get("user_id", "") or "")
        if not uid:
            continue
        names = [str(m.get(k) or "").strip() for k in ("card", "nickname")]
        entries.append((uid, [n for n in names if n]))
        roles_by_uid[uid] = str(m.get("role") or "")
    owner_label = re.sub(r"[（(]群主[)）]", "", OWNER_LABEL).strip()
    if "群主" in target or target in (OWNER_LABEL, owner_label):
        uid = _owner_uid(entries, roles_by_uid, owner_qq)
        if uid:
            return uid
        target = re.sub(r"[（(]?群主[)）]?", "", target).strip() or target

    def _best(uids: list[str]) -> str:
        if not uids:
            return ""
        if owner_qq in uids:
            return owner_qq
        for want in ("owner", "admin"):
            for uid in uids:
                if roles_by_uid.get(uid) == want:
                    return uid
        return uids[0]

    tiers = (
        lambda names: target in names,
        lambda names: any(target in n for n in names),
        lambda names: any(len(n) >= 2 and n in target for n in names),
    )
    for hit in tiers:
        uid = _best([uid for uid, names in entries if hit(names)])
        if uid:
            return uid
    return ""


def _unlink_quietly(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


async def send_eagle_images(send, count: int, tag: str = "cmd") -> int:
    """从 Eagle 图库随机取图并发送，返回发送张数（0=没取到图）。

    - 读图库放到线程里，避免大图库 listdir/读图卡住事件循环；
    - 临时文件名带 uuid，避免同一秒内两次请求互相覆盖；
    - 发送成功后立即删临时文件；发送异常（可能仍在上传）时 5 分钟后再删，不再永久残留。
    """
    img_list = await asyncio.to_thread(_pick_eagle_image, count)
    if not img_list:
        return 0
    paths = []
    try:
        msg = Message()
        for img_data in img_list:
            path = os.path.join(tempfile.gettempdir(), f"eagle_{tag}_{uuid.uuid4().hex}.jpg")
            with open(path, "wb") as f:
                f.write(img_data)
            paths.append(path)
            msg.append(MessageSegment.image(f"file:///{path}"))
        await send(msg)
    except Exception:
        loop = asyncio.get_running_loop()
        for p in paths:
            loop.call_later(300, _unlink_quietly, p)
        paths = []
        raise
    finally:
        for p in paths:
            _unlink_quietly(p)
    return len(img_list)


def _pick_eagle_image(count: int = 1) -> list[bytes]:
    """从 Eagle 图库随机选 count 张图，返回图片字节列表（最多10张）。"""
    import os
    from io import BytesIO
    from PIL import Image
    count = max(1, min(count, 10))
    results = []
    try:
        images_dir = os.path.join(_eagle_library_dir(), "images")
        if not os.path.isdir(images_dir):
            logger.warning(f"[指令] Eagle 图库目录不存在: {images_dir}")
            return results
        folders = [d for d in os.listdir(images_dir) if d.endswith(".info")]
        if not folders:
            logger.warning("[指令] Eagle 图库为空")
            return results
        random.shuffle(folders)
        valid_ext = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
        for folder in folders[:50]:
            if len(results) >= count:
                break
            folder_path = os.path.join(images_dir, folder)
            try:
                files = os.listdir(folder_path)
                img_files = [
                    f for f in files
                    if not f.endswith(".json")
                    and not f.endswith("_thumbnail.png")
                    and os.path.splitext(f)[1].lower() in valid_ext
                ]
                if not img_files:
                    continue
                img_path = os.path.join(folder_path, img_files[0])
                size = os.path.getsize(img_path)
                if size < 1024 or size > 10 * 1024 * 1024:
                    continue
                with open(img_path, "rb") as f:
                    data = f.read()
                img = Image.open(BytesIO(data))
                img.load()
                results.append(data)
                logger.info(f"[指令] 从 Eagle 图库选中: {img_path}")
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[指令] 读取 Eagle 图库失败: {e}")
    return results


_TALK_TO_ME_RE = re.compile(r"(?:跟|和|给|同|对)\s*(?:我|咱|俺|我们|咱们|大家)\s*(?:说|讲|聊|介绍|科普|解释|唠|谈)")
_PRONOUN_TARGET_RE = re.compile(r"^(?:我|你|您|咱|俺|他|她|它|大家)|说|讲|聊")


def _parse_command(text: str, raw: str = "") -> dict | None:
    """模糊解析用户指令，返回 {type: 'at'|'image'|'essence', target: str, content: str, target_qq: str} 或 None。
    支持多种自然说法，不严格匹配格式。
    raw 用于提取真正的 QQ 艾特 [CQ:at,qq=xxx]。"""
    t = (text or "").strip()
    if not t:
        return None

    # Named requests belong to Pixiv, never silently substitute random Eagle images.
    from ...core.pixiv_intent import parse_request
    if parse_request(t) is not None:
        return None

    # 1) 设为精华消息：精华 / 加精 / 设为精华 / 标记精华 / 加个精 / 弄个精华 / 这条精华一下
    if _ESSENCE_RE.search(t):
        return {"type": "essence", "target": "", "content": "", "target_qq": ""}

    # 2) 发图/发表情：发张图 / 来张图 / 发个表情 / 来个表情包 / 发张表情包
    #    也支持：给我发张图 / 整个图 / 弄张图 / 有没有图 / 来张好看的图 / 发个图呗
    #    支持：我要一张图 / 来2张图 / 来两张图 / 来10张涩图（阿拉伯数字和中文数字都支持，带修饰词也支持）
    if _IMG_CMD_RE.search(t) or _IMG_CMD_RE.search(_strip_bot_prefix(t)):
        # 提取数量，支持阿拉伯数字和中文数字
        count_match = re.search(rf"({_CN_NUM})\s*(?:张|个)", t)
        count = 1
        if count_match:
            num_str = count_match.group(1)
            if num_str.isdigit():
                count = int(num_str)
            else:
                # 中文数字转阿拉伯数字
                cn_map = {"一":1, "二":2, "两":2, "三":3, "四":4, "五":5, "六":6, "七":7, "八":8, "九":9, "十":10}
                count = cn_map.get(num_str, 1)
        return {"type": "image", "target": "", "content": "", "target_qq": "", "count": count}
    # 纯"图""表情""表情包"也触发
    if re.search(r"^(?:图|图片|表情|表情包|表情图|美图)\s*[？?呗吧啊呀~～]?$", t):
        return {"type": "image", "target": "", "content": "", "target_qq": "", "count": 1}

    # 3) 艾特某人：先从 raw 中提取真正的 QQ 艾特 [CQ:at,qq=xxx]
    #    排除机器人自己的艾特，取第一个非机器人的艾特作为目标
    target_qq = ""
    at_matches = re.findall(r"\[(?:CQ:)?at[:,]qq=(\d+)", raw or "")
    for qq in at_matches:
        if qq != str(config.BOT_QQ):
            target_qq = qq
            break

    # 「跟我说说 XX」「和我聊聊」「给我讲讲」是在跟她聊天，不是让她去艾特别人
    if not target_qq and _TALK_TO_ME_RE.search(t):
        return None

    # 匹配"艾特/叫/喊/@ + 人名 + 说/告诉/跟他说/和他说/告诉他/说一声 + 内容"
    # 也支持：你去叫一下XX / 帮我喊XX / 跟XX说 / 告诉XX / 叫XX来 / 喊XX一下
    # 注意：目标人名最多6个字符，且必须包含"说/告诉/跟他说"等词才触发，避免误判
    m = re.search(
        r"(?:艾特|叫|喊|帮我叫|帮我喊|帮我艾特|你去叫|你去喊|去叫|去喊|喊一下|叫一下|跟|告诉|和)\s*"
        r"([^\s，。！？!?@\"“”]{1,6})\s*"
        r"(?:说|告诉|跟他说|和他说|告诉他|说一声|来|一下|呗|吧)\s*[：:]?\s*(.+)",
        t
    )
    if m:
        target = m.group(1).strip()
        content = m.group(2).strip().lstrip("，,。.、 ")
        if _PRONOUN_TARGET_RE.match(target):  # 「我说说仆人」「你讲讲」这种不是人名
            return None
        # 过滤掉"机器人""芙宁娜""芙芙"等自己，以及"我""你"等代词
        _me = roles.self_words()
        if target not in ("机器人", "芙宁娜", "芙芙", "你", "自己", "我", "大家", "所有人", "群里") \
           and "芙宁娜" not in target and "芙芙" not in target and not any(w in target for w in _me):
            # 如果有真正的 QQ 艾特，优先用 QQ 号
            if target_qq:
                return {"type": "at", "target": target, "content": content, "target_qq": target_qq}
            # 没有真正的 QQ 艾特时，必须有内容才触发（避免"叫XX"这种不完整的话误判）
            if content and len(target) >= 2:
                return {"type": "at", "target": target, "content": content, "target_qq": ""}

    return None


async def _execute_command(event: GroupMessageEvent, group_id: int, cmd: dict) -> bool:
    """执行指令，返回是否成功执行（成功则不再走正常聊天）。"""
    bot = bot_for(event)
    cmd_type = cmd.get("type", "")

    if cmd_type == "essence":
        # 设为精华消息：需要引用一条消息，把引用的消息设为精华
        raw = event.raw_message
        if re.search(r"\[(?:CQ:)?reply[,:]id=", raw):
            # 从引用中提取消息ID
            m = re.search(r"\[(?:CQ:)?reply[,:]id=(-?\d+)", raw)
            if m:
                msg_id = int(m.group(1))
                try:
                    await bot.set_essence_msg(message_id=msg_id)
                    await bot.send_group_msg(group_id=group_id, message=MessageSegment.text("已设为精华~"))
                    logger.info(f"[指令] 设为精华成功: msg_id={msg_id}")
                    return True
                except Exception as e:
                    logger.warning(f"[指令] 设为精华失败: {e}")
                    await bot.send_group_msg(group_id=group_id, message=MessageSegment.text("设精华失败了，可能是权限不够~"))
                    return True
        await bot.send_group_msg(group_id=group_id, message=MessageSegment.text("要引用一条消息才能设为精华哦~"))
        return True

    if cmd_type == "image":
        if not permissions.allowed(group_id, "cmd_image", True):
            await bot.send_group_msg(group_id=group_id, message=MessageSegment.text("这个群还没有开放指令发图哦~"))
            return True
        # 发图/发表情：从 Eagle 图库随机选 N 张图（最多10张）
        count = cmd.get("count", 1)
        try:
            sent = await send_eagle_images(
                lambda m: bot.send_group_msg(group_id=group_id, message=m), count, "cmd"
            )
            if sent:
                daily_limits.track("cmd_image", group_id)
                logger.info(f"[指令] 发图成功，共{sent}张")
                return True
        except Exception as e:
            logger.warning(f"[指令] 发图失败: {e}")
            return True  # 发送结果不确定（可能已发出），不再补一句“没找到图”
        await bot.send_group_msg(group_id=group_id, message=MessageSegment.text("图库没找到合适的图~"))
        return True

    if cmd_type == "at":
        # 艾特某人：优先用 target_qq（真正的 QQ 艾特），否则按昵称匹配
        target_name = cmd.get("target", "")
        content = cmd.get("content", "")
        target_qq = cmd.get("target_qq", "")
        if not target_name and not target_qq:
            return False
        try:
            target_user_id = target_qq
            if not target_user_id:
                # 没有真正的 QQ 艾特，按群名片/昵称匹配
                members = await bot.get_group_member_list(group_id=group_id)
                target_user_id = _match_member(members, target_name)
            if target_user_id:
                msg = MessageSegment.at(target_user_id) + MessageSegment.text(f" {content}" if content else "")
                await bot.send_group_msg(group_id=group_id, message=msg)
                logger.info(f"[指令] 艾特 {target_name}({target_user_id}) 成功")
                return True
            else:
                # 群里没这个人：多半是误判（比如「跟仆人说说…」），交给正常聊天自然地接，不发生硬的「没找到」
                logger.info(f"[指令] 没找到叫「{target_name}」的群友，按普通聊天处理")
                return False
        except Exception as e:
            logger.warning(f"[指令] 艾特失败: {e}")
            await bot.send_group_msg(group_id=group_id, message=MessageSegment.text(f"艾特{target_name}失败了~"))
            return True

    return False
