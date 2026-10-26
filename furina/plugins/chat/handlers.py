"""NoneBot 事件处理函数；matcher 由 __init__.py 注册后传入。"""

import random
import re
import time

from nonebot import logger
from ...core.botref import bind as bind_bot, bot_for
from nonebot.adapters.onebot.v11 import (
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PokeNotifyEvent,
)

from . import bot_dialog, config, context, digest, emotion, favor, learning, life_events, llm, memory, presence, reactions, transcript, turns
from . import attention, boundary, humanize, kernel, people, stickerlib
from ...core import brain, permissions, roles, usage as daily_limits
from ...services import slang
from .chat import _collect_message_images, _handle_memory_intent, _reply, _schedule_image_desc
from .cleanup import _desc_allowed
from .commands import _execute_command, _parse_command
from .history import _history_segment_text
from .quotes import _extract_text, _fetch_quote, _has_image, _iter_segments, _sender_name
from .state import _poke_cooldown, _summary_last

_CALLS_HER = re.compile(r"芙芙|芙宁娜|水神|芙卡洛斯")

# NapCat 新版用无 CQ: 前缀的现代格式 [at:qq=X]，旧版是 [CQ:at,qq=X]，都要兼容
_AT_RE = re.compile(r"\[(?:CQ:)?at[:,]qq=(\d+)\]")
_REPLY_RE = re.compile(r"\[(?:CQ:)?reply[,:]id=(\d+)\]")

# 与搜图插件 google_image_search._check_trigger 完全一致的触发词（唯一触发：引用图 + 艾特 + “搜图”）。
# 旧版这里用了宽得多的“自然语言搜图意图”（出处/来源/哪来的/这是什么东西…）：聊天插件因此放弃回复，
# 但搜图插件并不会接手，结果“@芙芙 这是什么东西”（引用一张图）这类问题机器人完全不回。
_SEARCH_PATTERN = re.compile(r"(搜|找|识|以图).{0,4}图")
_URL_RE = re.compile(r"https?://\S+")
_MEDIA_TAGS = ("[CQ:image", "[CQ:face", "[CQ:json", "[CQ:share", "[CQ:video")


def _is_media_only(raw_msg: str, text: str) -> bool:
    """判断消息是否只有媒体内容（图片/表情包/小程序/链接），没有实质文字。"""
    t = (text or "").strip()
    # 去掉纯 URL 后如果没有文字，说明只有链接
    t_no_url = _URL_RE.sub("", t).strip()
    if not t_no_url:
        # 没有文字，检查是否有媒体内容
        has_media = any(tag in raw_msg for tag in _MEDIA_TAGS)
        if has_media or t:  # t 不为空说明只有 URL
            return True
    return False


async def handle_summary(event: GroupMessageEvent, matcher) -> None:
    """即时总结最近的群聊内容"""
    bind_bot(event)
    group_id = event.group_id
    user_id = str(event.user_id)
    bot = bot_for(event)

    # 每群限频：旧版没有任何限制，连刷“总结”会反复消耗大量模型额度
    now = time.time()
    wait = config.SUMMARY_COOLDOWN - (now - _summary_last.get(group_id, 0))
    if config.SUMMARY_COOLDOWN > 0 and wait > 0:
        await matcher.finish(f"刚刚才总结过呢，{int(wait) + 1} 秒后再来吧~")
        return
    _summary_last[group_id] = now
    daily_limits.track("summary", group_id)

    await matcher.send(Message([
        MessageSegment.at(user_id),
        MessageSegment.text(roles.flavor(" 好的，芙芙这就帮你总结一下最近的聊天~", " 好的，{name}这就帮你总结一下最近的聊天~")),
    ]))

    try:
        # 获取最近200条群消息
        ret = await bot.call_api(
            "get_group_msg_history",
            group_id=str(group_id),
            count=200,
            reverse_order=False,
        )
        msgs = (ret or {}).get("messages") or []
    except Exception as e:
        logger.warning(f"[总结] 获取群历史消息失败: {e}")
        await matcher.finish("❌ 获取聊天记录失败了，请稍后再试~")
        return

    if not msgs:
        await matcher.finish("最近没有聊天记录可以总结呢~")
        return

    # 提取消息文本，过滤机器人自己的消息和空消息
    lines = []
    for m in msgs:
        uid = str(m.get("user_id") or "")
        if uid == config.BOT_QQ:
            continue
        sender = m.get("sender") or {}
        if uid == config.GROUP_OWNER_QQ:
            name = "群主"
        elif isinstance(sender, dict):
            name = sender.get("card") or sender.get("nickname") or "群友"
        else:
            name = getattr(sender, "card", "") or getattr(sender, "nickname", "") or "群友"
        text = _history_segment_text(m.get("message"))
        if text:
            lines.append(f"{name}：{text[:80]}")

    if not lines:
        await matcher.finish("最近大家都没怎么说话呢，没什么好总结的~")
        return

    # 限制输入长度
    user_content = "\n".join(lines)
    if len(user_content) > 12000:
        user_content = user_content[-12000:]

    # 调用 LLM 总结
    messages = [
        {
            "role": "system",
            "content": (
                "你是群聊总结助手。把下面的群聊记录总结成简洁的要点，3~8条，"
                "每条一行、不超过40字，只记真实发生的事，不要编造。"
                f"用{roles.display_name()}的口吻，活泼一点，但不要太啰嗦。"
            ),
        },
        {"role": "user", "content": user_content},
    ]
    try:
        summary_text = await llm.chat(messages)
    except Exception as e:
        logger.error(f"[总结] LLM 总结失败: {e}")
        await matcher.finish("❌ 总结失败了，请稍后再试~")
        return

    summary_text = humanize.prepare_message((summary_text or "").strip())
    if not summary_text:
        await matcher.finish(roles.flavor("嗯...芙芙看了半天，感觉没什么特别的事发生呢~", "嗯...{name}看了半天，感觉没什么特别的事发生呢~"))
        return

    # 发送总结
    await matcher.finish(Message([
        MessageSegment.at(user_id),
        MessageSegment.text(f"\n📋 最近聊天总结：\n{summary_text}"),
    ]))


def _turn_check(turn, seen):
    """发送前检查：生成期间 TA 又补了话 → True（重来）；否则立刻结束合并会话，
    之后 TA 再发的消息按新消息正常处理，不会被并进一条已经在发送的回复里。"""
    def check() -> bool:
        if seen is not None and len(turn.extras) > seen:
            return True
        turns.close(turn)
        return False
    return check


async def _forced_chat(event, matcher, turn, group_id, user_id, nickname, raw, store, current_text,
                       has_image, has_at_bot, quote, vision_images, peer=(bot_dialog.HUMAN, 0)) -> None:
    """被艾特后的处理：限额 → 记忆意图 → 指令 → 情绪/好感 → 合并连发 → 回复。
    peer=(状态, 第几轮)：对方是其他 AI 机器人时由 bot_dialog 决定是否收拢/收尾（真人为 HUMAN，行为不变）。"""
    _user_limit = config.get_user_chat_limit(group_id, user_id)
    if str(user_id) not in config.UNLIMITED_CHAT_QQ and not daily_limits.consume("chat", group_id, user_id, _user_limit):
        logger.info(f"[AI聊天] 群{group_id} 用户{user_id} 今日艾特聊天次数已达上限（{_user_limit}次），忽略")
        return
    daily_limits.track("chat", group_id)
    if peer[0] == bot_dialog.HUMAN:  # 谁用得多（面板众筹参考）：只记真人
        daily_limits.track_user("chat", group_id, user_id, name=nickname)

    # 词条/设定/外号增删查：先让 AI 理解自然语言意图，命中就直接执行并返回
    # （问机器人喜不喜欢/好感属于情感闲聊，分类器会自行判 none 放行给大模型）
    try:
        handled = await _handle_memory_intent(event, group_id, user_id, nickname, current_text, has_at_bot, raw)
        if handled:
            return
    except Exception as e:
        logger.warning(f"[AI聊天] 记忆意图处理失败: {e}")

    # ===== 指令解析：艾特某人 / 发图发表情 / 设为精华 =====
    cmd = _parse_command(current_text, raw)
    if cmd:
        logger.info(f"[AI聊天] 群{group_id} 用户{user_id} 触发指令: {cmd}")
        executed = await _execute_command(event, group_id, cmd)
        if executed:
            return

    # 仅通过权限/限额并确实进入聊天的消息影响关系；记忆/工具指令不刷分。
    # ===== 情绪观察：夸奖/坏话影响心情值 =====
    try:
        emo_msg = emotion.observe_message(group_id, user_id, nickname, current_text, has_at_bot)
        if emo_msg:
            logger.info(f"[AI聊天] 情绪变化: {emo_msg}")
    except Exception as e:
        logger.warning(f"[AI聊天] 情绪观察失败: {e}")

    # ===== 好感度养成：常来聊天/夸奖会涨、说坏话会掉（群主和特别关照的人锁定满值） =====
    try:
        favor.observe_message(group_id, user_id, nickname, current_text, has_at_bot,
                              message_id=getattr(event, "message_id", None),
                              bot_qq=event.self_id)
    except Exception as e:
        logger.warning(f"[AI聊天] 好感度更新失败: {e}")


    # 只是「谢谢 / 好的 / 晚安 / 哈哈」这类收尾话：有时只贴个表情，不再回一句（真人也常这样）
    if peer[0] == bot_dialog.HUMAN and quote is None and not has_image:
        try:
            if await reactions.react_instead(bot_for(event), event, current_text):
                mid = getattr(event, "message_id", None)
                emo_name = reactions.last_emoji_name(group_id, mid) or "表情"
                if current_text:  # 先把对方这句记进上下文，再标上「你给这条贴了表情」
                    memory.add_message(group_id, "user", f"{nickname}：{current_text}",
                                       meta=transcript.event_meta(event, nickname))
                    memory.annotate(group_id, mid, "reacted", emo_name)
                memory.add_message(group_id, "assistant", f"（给这条消息贴了个「{emo_name}」表情，没说话）", meta={"to": mid})
                memory.mark_replied(group_id, [mid], f"（贴了个「{emo_name}」表情）")
                logger.info(f"[AI聊天] 群{group_id} {nickname} 只是在收尾，贴个表情代替回复")
                return
        except Exception as e:
            logger.warning(f"[AI聊天] 贴表情代替回复失败: {e}")

    logger.info(f"[AI聊天] 群{group_id} 用户{user_id} 艾特机器人，触发自动回复")
    peer_state, peer_round = peer
    peer_opts = bot_dialog.options(peer_state, peer_round, nickname)
    if peer_state == bot_dialog.WRAPUP:
        logger.info(f"[AI聊天] 群{group_id} 和机器人 {nickname} 已聊 {peer_round} 轮，这句自然收尾（不艾特、不引用）")
    elif peer_state == bot_dialog.NORMAL:
        logger.info(f"[AI聊天] 群{group_id} 和机器人 {nickname} 互聊第 {peer_round} 轮")
    if turn is None:
        result = await _reply(event, group_id, nickname, store, current_text, has_image, quote,
                              forced=True, vision_images=vision_images, **peer_opts)
        if result is True:
            await matcher.skip()
        return
    # 等 TA 把话说完（几秒内接着发的都并进来）；生成期间又补了话就丢掉旧稿重来一次
    # 话像没说完（「然后」「等一下」「那个」、逗号结尾）会多等一会儿；等满了 TA 还是没说完，就像真人那样「啥？」「然后呢」
    unfinished = await turns.wait_quiet(turn, first=current_text)
    turn_extra = turns.unfinished_note(current_text, turn) if unfinished else ""
    result = None
    for attempt in range(2):
        seen = len(turn.extras)
        imgs = (list(vision_images or []) + turn.images())[:max(1, config.VISION_MAX_IMAGES)]
        result = await _reply(
            event, group_id, nickname, store, turns.combined_text(current_text, turn),
            has_image or turn.has_image(), quote, forced=True, vision_images=imgs,
            extra_mids=turn.mids(),
            abort_check=_turn_check(turn, seen if attempt == 0 else None),
            turn_extra=turn_extra,
            **peer_opts,
        )
        if result != "RETRY":
            break
    if result is True:
        # AI回复包含搜图意图，跳过当前matcher，让搜图插件处理
        await matcher.skip()
    return


async def _hear_voice(event, group_id, user_id) -> str:
    """把这条语音转成文字。只在她可能会接话时才听（省资源）：正在这个群聊着、群主、常聊的人，或者三成随机。"""
    if not config.HEAR_VOICE:
        return ""
    want = (attention.linger_probability(group_id) > 0
            or (config.GROUP_OWNER_QQ and str(user_id) == str(config.GROUP_OWNER_QQ))
            or str(user_id) in presence.frequent(group_id)
            or random.random() < 0.3)
    if not want:
        return ""
    data = next((d for t, d in _iter_segments(event.message) if t == "record"), None)
    if not data:
        return ""
    try:
        from ...services import voice_asr
        return (await voice_asr.hear_segment(bot_for(event), data)).strip()[:200]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[听语音] 失败，当没听清: {e}")
        return ""


_GROUP_QUESTION_RE = re.compile(r"[?？]\s*$|有没有人|有人[^，。]{0,12}[吗么嘛]|谁(?:知道|会|懂|有)|求助|求推荐|怎么办|咋办")


def random_reply_chance(group_id, user_id, nickname: str, text: str, hist: list, from_bot: bool) -> tuple[float, str]:
    """随机接话的命中线和原因。平时就是「随机接话概率」（AI_CHAT_PROBABILITY）；下面几种情况会提到各自设置里的数
    （每种都能在设置里改，填 0 = 这种情况不加）。随机接话概率设成 0 = 完全不随机接话，这些也都不生效。"""
    p, why = float(config.REPLY_PROBABILITY), "平时"
    if p <= 0:
        return 0.0, "随机接话已关"
    lifts = []
    # 当前说话人刚刚和她对过话（活跃多轮追问窗口）
    if context.is_speaker_in_active_bot_thread(hist, nickname):
        lifts.append((config.THREAD_PROBABILITY, "刚和她聊着"))
    # TA 之前提过的事（考试/面试…）已经过去、还没问过：好顺口关心一句
    if config.LIFE_EVENTS and not from_bot and life_events.pending(group_id, user_id):
        lifts.append((config.LIFE_FOLLOWUP_PROBABILITY, "有近况可问"))
    if not from_bot:
        # 话多一点：群里有人抛问题时更想接
        if config.CHATTY and _GROUP_QUESTION_RE.search(text or ""):
            lifts.append((config.CHATTY_QUESTION_PROBABILITY, "有人提问"))
        # 常聊的人隔了很久回来了（尤其她惦记过 TA）
        lifts.append((presence.welcome_back_probability(group_id, user_id), "常聊的人回来了"))
        # 刚在这个群聊过、还「留着」（聊太久会自己收尾，见 attention）
        lifts.append((attention.linger_probability(group_id), "刚聊完还留着"))
        # 群主说话：她会多看两眼
        if config.GROUP_OWNER_QQ and str(user_id) == str(config.GROUP_OWNER_QQ):
            lifts.append((config.OWNER_WAKE_PROBABILITY, "群主说话"))
    for q, reason in lifts:
        q = max(0.0, min(1.0, float(q or 0)))
        if q > p:
            p, why = q, reason
    return p, why


async def _sim2_pre_at(event, group_id, user_id, nickname, raw, current_text, has_at_bot) -> bool:
    """二代仿真被艾特：词条增删查 / 指令照旧先处理（处理了返回 True），再记情绪和好感。"""
    if kernel.away_info(getattr(event, "self_id", "")) and not kernel._is_owner(user_id):
        return False  # 她去刷 B 站了：词条 / 指令也等她回来再说（消息交给二代仿真攒着）
    try:
        done = await _handle_memory_intent(event, group_id, user_id, nickname, current_text, has_at_bot, raw)
        if done:
            # 词条是旁路直接办的：也告诉二代仿真（不叫醒她），免得下次被问起她完全不知道自己刚做了什么
            kernel.note_handled(event, group_id, user_id, nickname, current_text, done if isinstance(done, str) else "")
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AI聊天] 记忆意图处理失败: {e}")
    cmd = _parse_command(current_text, raw)
    if cmd and await _execute_command(event, group_id, cmd):
        kernel.note_handled(event, group_id, user_id, nickname, current_text, "", done_what="按这条消息里的指令办了")
        return True
    try:
        emotion.observe_message(group_id, user_id, nickname, current_text, has_at_bot)
        favor.observe_message(group_id, user_id, nickname, current_text, has_at_bot,
                              message_id=getattr(event, "message_id", None), bot_qq=event.self_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AI聊天] 情绪/好感更新失败: {e}")
    return False


async def handle_chat(event: GroupMessageEvent, matcher) -> None:
    bind_bot(event)  # 多号同时在线：本次处理期间的角色/自称/“艾特我”都按收到消息的这个号
    group_id = event.group_id
    user_id = str(event.user_id)
    nickname = _sender_name(event)
    raw = event.raw_message
    # 兼容"@芙宁娜/@芙芙"这类纯文本艾特（部分客户端不生成 [CQ:at] 段）
    at_ids = set(_AT_RE.findall(raw))
    self_ids = {str(getattr(event, "self_id", "")), str(config.BOT_QQ)} - {""}
    has_at_bot = bool(at_ids & self_ids) or roles.is_bot_at(event)

    current_text = _extract_text(event.message)
    if has_at_bot and current_text.startswith(("@", "＠")):
        current_text = roles.strip_bot_at(current_text, str(getattr(event, "self_id", ""))) or current_text
    has_image = _has_image(event.message)
    has_reply = bool(_REPLY_RE.search(raw))
    # 群友发语音：她「听」一下（借整合包的语音识别，资源紧张 / 没开就当没听清）；话里叫了她的名字就当在跟她说
    if "[语音]" in current_text:
        heard = await _hear_voice(event, group_id, user_id)
        if heard:
            current_text = current_text.replace("[语音]", f"[语音] {heard}", 1)
            if not has_at_bot and any(a and a in heard for a in roles.aliases()):
                has_at_bot = True

    # 群友直接发的合并转发 / 聊天记录：展开成「谁: 说了什么」，不然她只看到一条空消息
    if "[合并转发]" in current_text:
        try:
            from .quotes import forward_text

            expanded = await forward_text(event.message, bot_for(event))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[AI聊天] 展开合并转发失败: {e}")
            expanded = ""
        if expanded:
            current_text = current_text.replace("[合并转发]", expanded, 1)

    # Reserve upstream-configured namespaces so AI does not answer game commands.
    # No local game parser, query, or rendering code is loaded here.
    from ...services.gscore import reserved_for_core
    if reserved_for_core(current_text):
        return

    # 图片与引用只在真正会用到时才读取：旧版对每条群消息都下载全部图片、对每条带引用的消息都
    # 调 get_msg，而绝大多数消息既没艾特机器人也不会触发随机接话（默认 2%），白白消耗带宽和
    # NapCat 调用。艾特机器人 / 开启了后台图片描述时照旧提前读取；随机接话命中后再补读。
    vision_images, first_image_entry = [], None
    if has_image and (has_at_bot or config.CHAT_IMAGE_DESC):
        vision_images, first_image_entry = await _collect_message_images(event, group_id)

    # 读取引用内容（含合并转发）
    quote = None
    if has_reply and has_at_bot:
        quote = await _fetch_quote(event)

    # 记忆里只存干净的「昵称：内容」。引用/艾特这些元信息只在本轮 prompt
    # （full_user）里告诉模型，绝不写进记忆，否则模型会把「（艾特了…）」当
    # 群友原话照抄进回复，还会把不同人的话混在一起。
    store = current_text.strip()
    if has_image:
        store = ("[图片] " + store).strip() if store else "[图片]"
    store = store[:300]

    # 记下本条图片的引用（不下载），之后有人问“这谁/这是啥”时可以补看
    if has_image:
        try:
            memory.remember_images(group_id, nickname, [
                (str(sdata.get("file") or ""), str(sdata.get("url") or ""))
                for stype, sdata in _iter_segments(event.message) if stype == "image"
            ])
        except Exception as e:
            logger.warning(f"[AI聊天] 记录最近图片失败: {e}")

    # 先把消息记入群上下文（未回复的消息也保留，供后续接话参考）
    meta = transcript.event_meta(event, nickname)
    if store:
        stored_line = f"{nickname}：{store}"
        memory.add_message(group_id, "user", stored_line, meta=meta)
        # 后台生成图片描述写进上下文，方便以后"联系上下文"提到这张图
        if config.CHAT_IMAGE_DESC and first_image_entry is not None and _desc_allowed(group_id):
            _schedule_image_desc(group_id, stored_line, first_image_entry)

    # ===== 自主学习：捕捉外号、风格要求（词条增删查已由记忆意图识别先行处理） =====
    try:
        learned_msgs = learning.observe_message(
            group_id, user_id, nickname, current_text, has_at_bot
        )
        for lm in learned_msgs:
            logger.info(f"[AI聊天] 自主学习: {lm}")
    except Exception as e:
        logger.warning(f"[AI聊天] 自主学习失败: {e}")
    # ===== 群梗：好几个人都在说的短句记下来，聊天时她也会用 =====
    if config.GROUP_MEMES and not has_at_bot:
        try:
            slang.observe(group_id, user_id, current_text)
        except Exception as e:
            logger.warning(f"[AI聊天] 群梗记录失败: {e}")
    # ===== 每日纪要素材收集（供晚上自动生成纪要） =====
    try:
        digest.record_message(group_id, user_id, nickname, current_text, has_image)
    except Exception as e:
        logger.warning(f"[AI聊天] 纪要收集失败: {e}")
    # ===== 群友近况：「明天考试」「周五面试」「我感冒了」记下来，过后顺口问一句 =====
    _from_bot = bot_dialog.is_peer(user_id, str(getattr(event, "self_id", "")))
    if not _from_bot and permissions.allowed(group_id, "chat", True):
        try:
            life_events.record(group_id, user_id, nickname, current_text)
        except Exception as e:
            logger.warning(f"[AI聊天] 记录群友近况失败: {e}")
    # ===== 时间感：记下每个人上次在群里说话的时间（隔了很久才回来，她会知道） =====
    if not _from_bot:
        try:
            presence.seen(group_id, user_id, nickname, current_text)
        except Exception as e:
            logger.warning(f"[AI聊天] 记录群友出现时间失败: {e}")
        # 每个人一本账（跨群）：最后出现、常出现的钟点、「等我洗个澡」这类记在心里
        try:
            people.observe(group_id, user_id, nickname, current_text)
        except Exception as e:
            logger.warning(f"[AI聊天] 记录群友账本失败: {e}")

    # ===== 边界感：被冷落的人艾特她或叫她名字求情认错 → 攒够诚意就原谅（一次比一次难）；有点心软时回一句 =====
    _plea = ""
    if not _from_bot and (has_at_bot or _CALLS_HER.search(current_text or "")):
        try:
            _plea = boundary.plead(user_id, nickname, current_text)
            if _plea in ("forgiven", "softening", "noted"):
                logger.info(f"[AI聊天] 群{group_id} {nickname}（{user_id}）求情认错：" + {
                    "forgiven": "原谅 TA 了", "softening": "有点心软，回一句但还没原谅", "noted": "诚意还不够，继续不理"}[_plea])
        except Exception as e:
            logger.warning(f"[AI聊天] 认错判断失败: {e}")
    # ===== 边界感：警告过还越界的人，冷落期间说什么都不回（消息照样记进上下文） =====
    if not _from_bot and _plea != "softening" and boundary.ignoring(user_id):
        if has_at_bot:
            logger.info(f"[AI聊天] 群{group_id} {nickname}（{user_id}）还在冷落中（剩 {boundary.cold_left_hours(user_id):.1f} 小时），不回")
        return

    # ===== 模式（面板「模式与内核」）：二代仿真交给 DSH 内核；聊天 / Agent 只回艾特 =====
    if has_image:
        stickerlib.note_images(group_id, event.message, meta.get("mid"), user_id, nickname)
    mode = kernel.effective_mode(group_id)
    _is_search = bool(_SEARCH_PATTERN.search(raw)) or "搜图" in raw
    _is_upscale = "强化" in raw or "放大" in raw or "高清化" in raw
    _handoff = has_at_bot and quote is not None and (quote.images or quote.failed) and (_is_search or _is_upscale)
    if mode == "agent" and has_at_bot and not _handoff and brain.agent_enabled() \
            and str(user_id) == str(config.GROUP_OWNER_QQ or "") and permissions.allowed(group_id, "chat", True):
        await kernel.run_agent(bot_for(event), user_id, current_text, group_id=group_id)
        return
    if mode == "sim2" and not _handoff:
        if has_at_bot:
            if memory.replied_text(group_id, meta.get("mid")) is not None:
                return
            if permissions.allowed(group_id, "chat", True) and await _sim2_pre_at(
                    event, group_id, user_id, nickname, raw, current_text, has_at_bot):
                return
        async def _fallback(ev=event, gid=group_id, uid=user_id, nick=nickname, st=store, txt=current_text,
                            img=has_image, q=quote, vi=vision_images):
            await _reply(ev, gid, nick, st, txt, img, q, forced=True, vision_images=vi)
        if await kernel.observe(event, group_id, user_id, nickname, current_text, has_at_bot, has_image, _from_bot,
                                fallback=_fallback):
            return

    # ===== 艾特了机器人 =====
    if has_at_bot:
        if _handoff:
            # 引用里可点开的图（或读取失败）+ 输入了搜图/强化相关词 → 不聊天，交给搜图/图片强化插件
            _trigger_type = "搜图" if _is_search else "强化"
            logger.info(f"[AI聊天] 群{group_id} 引用有图+艾特+{_trigger_type}（自然语言意图），不触发聊天，交给对应插件")
            # 刚才顺手记进上下文的这条"搜图指令"不属于聊天，撤掉，免得污染记忆
            if store:
                plain = f"{nickname}：{store}"
                hist = memory.get_history(group_id)
                if hist and memory.same(hist[-1], "user", plain):
                    memory.pop_last(group_id)
            await matcher.skip()
            return

        # 群主艾特聊天不设上限，其他人受每日次数限制（特定用户有自定义限额），
        # 超限的艾特消息一律不再处理，避免词条意图识别继续消耗模型额度或绕过限流
        if not permissions.allowed(group_id, "chat", True):
            logger.info(f"[AI聊天] 群{group_id} 已关闭艾特回复权限")
            return
        # 同一条消息（NapCat 重复上报等）已经回过了，不再回第二次
        if memory.replied_text(group_id, meta.get("mid")) is not None:
            logger.info(f"[AI聊天] 群{group_id} 消息 {meta.get('mid')} 已回复过，忽略重复事件")
            return
        # TA 上一句还在等回复：这条并进去一起回，不单独再回一次
        if turns.append(group_id, user_id, current_text, has_image, meta.get("mid"), vision_images):
            logger.info(f"[AI聊天] 群{group_id} {nickname} 连发消息，并入上一轮一起回复")
            return
        # 对方是群里另一个 AI 机器人：聊几轮自然收尾，收尾后冷却期内不再接 TA 的话（见 bot_dialog）
        peer = bot_dialog.begin(group_id, str(getattr(event, "self_id", "")), user_id)
        if peer[0] == bot_dialog.SILENT:
            logger.info(f"[AI聊天] 群{group_id} 和机器人 {nickname} 的闲聊已收尾，冷却期内不再接话")
            return
        # 连发合并默认关闭（AI_CHAT_MERGE_SECONDS=0）：不开合并会话，收到就正常回
        turn = turns.open_turn(group_id, user_id) if config.MERGE_SECONDS > 0 else None
        try:
            await _forced_chat(event, matcher, turn, group_id, user_id, nickname, raw, store, current_text,
                               has_image, has_at_bot, quote, vision_images, peer=peer)
        finally:
            turns.close(turn)
        return

    # ===== 未艾特：TA 刚艾特过机器人、还在等回复时，接着说的话并进那一轮 =====
    if turns.is_open(group_id, user_id):
        if has_image and not vision_images:
            vision_images, _ = await _collect_message_images(event, group_id)
        if turns.append(group_id, user_id, current_text, has_image, meta.get("mid"), vision_images):
            logger.info(f"[AI聊天] 群{group_id} {nickname} 补充了一句，并入正在进行的回复")
            return
    # ===== 未艾特：智能场景判断 + 按概率随机接话（融合 Qhunliv13/qq-bot 与 MaiBot 实践） =====
    # 聊天 / Agent 模式只回艾特；随机接话、顺手贴表情只在一代仿真里有
    if mode != "sim1":
        return
    if not permissions.allowed(group_id, "auto", True):
        return
    # 顺手给群消息贴个表情（低概率、每群有冷却）；贴了就不再插话
    if not _from_bot:
        try:
            if await reactions.maybe_react(bot_for(event), event, current_text, has_image):
                return
        except Exception as e:
            logger.warning(f"[AI聊天] 顺手贴表情失败: {e}")
    if _is_media_only(raw, current_text):
        return
    # 1) 消息明确 @ 了其他群友（非机器人）：属于点名找别人说话，不强行插嘴
    other_at_ids = {qid for qid in at_ids if qid and qid != config.BOT_QQ and qid != "0"}
    if other_at_ids:
        return
    # 和这个机器人的闲聊刚收过尾：随机接话也不去接 TA，免得又聊起来
    if bot_dialog.is_silent(group_id, str(getattr(event, "self_id", "")), user_id):
        return
    # 2) 检测最近 4 条消息是否为另外两名群友一问一答交替私聊（A->B->A->B）：若是则安静避让
    hist_now = memory.get_history(group_id)
    two_talking, _talkers = context.detect_two_others_talking(hist_now)
    if two_talking:
        logger.debug(f"[AI聊天] 群{group_id} 检测到双人交替对话 {_talkers}，跳过随机插话")
        return
    # 3) 随机接话的命中线（见 random_reply_chance）
    eff_prob, why = random_reply_chance(group_id, user_id, nickname, current_text, hist_now, _from_bot)
    # 只有「[语音]」没听清：不知道说了啥就别接
    if (current_text or "").strip() == "[语音]":
        return
    # 时间感：太久以前的消息（刚醒 / 补看）不随机接
    _age = time.time() - float(getattr(event, "time", 0) or time.time())
    if _age >= max(1, config.STALE_SILENT_MINUTES) * 60:
        return
    if random.random() >= eff_prob:
        return
    # 随机接话命中后才读取本条图片和引用（见上方说明）
    if has_image and not vision_images:
        vision_images, _ = await _collect_message_images(event, group_id)
    if has_reply and quote is None:
        quote = await _fetch_quote(event)
    if memory.replied_text(group_id, meta.get("mid")) is not None:
        return
    logger.info(f"[AI聊天] 群{group_id} 用户{user_id} 触发随机接话（命中线 {eff_prob * 100:.0f}%：{why}）")
    daily_limits.track("auto", group_id)
    await _reply(event, group_id, nickname, store, current_text, has_image, quote, forced=False, vision_images=vision_images)


# ===== 被戳一戳：有人戳机器人时触发一次带艾特的自动回复 =====
# 被戳一戳的回复方式随机抽一种，保证每次格式都不一样、不落固定句式
POKE_STYLES = [
    "被戳了，你有点无语，随口吐槽一句，越短越好",
    "被戳了，你一脸问号，反问对方一句",
    "被戳了，你软软地撒娇抱怨一句",
    "被戳了，你假装要去告状/喊人帮忙，玩笑口吻",
    "被戳了，你装傻装困，敷衍一句",
    "被戳了，你反过来开个玩笑逗对方，但别打比方",
    "被戳了，你压根不当回事，随便岔开说别的",
    "被戳了，你嘴上嫌弃但其实是闹着玩",
]
POKE_REPLY_NOTE = """【戳一戳专属规则（优先级最高）】
群友戳了你一下，就像微信里朋友随手戳你一样，用一句话自然回应，别当成什么大事。
一句话，越短越随意越好，别抖机灵、别打比方、别写小作文；
不要用「你这戳…」「手别乱摸」「仪态」「审判钟」「广场舞」这类用过很多次的句式，也别每条都拿「～」结尾。"""



async def handle_poke(event: PokeNotifyEvent):
    """群友戳一戳芙宁娜时，触发一次带艾特的自动回复。"""
    if not isinstance(event, PokeNotifyEvent):
        return
    bind_bot(event)
    if str(event.target_id) != config.BOT_QQ:
        return
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return
    # 被戳自动 AI 回复总开关（AI_CHAT_POKE_REPLY=false 时直接忽略戳一戳）
    if not config.POKE_REPLY:
        return
    poker_id = event.user_id
    if str(poker_id) == config.BOT_QQ:
        return
    if boundary.ignoring(poker_id):
        logger.info(f"[AI聊天] 群{group_id} {poker_id} 戳了她，但还在冷落中，不理")
        return

    key = (int(group_id), int(poker_id))
    now = time.time()
    if now - _poke_cooldown.get(key, 0) < config.POKE_COOLDOWN:
        return
    if not permissions.allowed(group_id, "poke", True):
        return
    nickname = _sender_name(event)
    if str(poker_id) not in config.UNLIMITED_CHAT_QQ and not daily_limits.consume("poke", group_id, poker_id, config.DAILY_POKE_LIMIT_PER_USER):
        logger.info(f"[AI聊天] 群{group_id} {nickname}（{poker_id}）今日戳一戳次数已达上限，忽略")
        return
    daily_limits.track("poke", group_id)
    daily_limits.track_user("poke", group_id, poker_id, name=nickname)
    _poke_cooldown[key] = now

    logger.info(f"[AI聊天] 群{group_id} {nickname}（{poker_id}）戳了戳机器人，触发回复")
    # 被戳心情 +2
    try:
        poke_msg = emotion.on_poke(group_id, poker_id)
        if poke_msg:
            logger.info(f"[AI聊天] 情绪变化: {poke_msg}")
    except Exception as e:
        logger.warning(f"[AI聊天] 戳戳情绪更新失败: {e}")
    # 被戳好感度微涨（锁定用户内部自动跳过）
    try:
        favor.on_poke(poker_id, bot_qq=event.self_id)
    except Exception as e:
        logger.warning(f"[AI聊天] 戳戳好感度更新失败: {e}")

    poke_group_id = group_id
    class _PokeCtx:
        user_id = poker_id
        group_id = poke_group_id
        self_id = event.self_id
        message = Message()
        raw_message = ""
        reply = None

    ctx = _PokeCtx()

    async def _poke_send(message):
        bot = bot_for(event)
        await bot.send_group_msg(group_id=group_id, message=message)

    await _reply(
        ctx, group_id, nickname, f"（{nickname} 戳了戳你）", "戳了戳你", False, None, True,
        vision_images=[], send_func=_poke_send,
        context_note=POKE_REPLY_NOTE + random.choice(POKE_STYLES),
        allow_actions=False,
    )


# ===== 新成员入群：自动艾特并欢迎 =====
# 芙宁娜角色用下面这组；其他角色用 WELCOME_STYLES_GENERIC（{me} = 当前角色名）
WELCOME_STYLES = [
    "欢迎{name}进群！本导演带你玩～",
    "哟，{name}来啦？欢迎欢迎，芙芙罩着你！",
    "欢迎{name}～群里有啥不懂的尽管问，本大明星好说话得很。",
    "新朋友{name}入群啦！先自我介绍一下呗？",
    "欢迎{name}！以后就是一家人了，别客气。",
]
WELCOME_STYLES_GENERIC = [
    "欢迎{name}进群！{me}带你玩～",
    "哟，{name}来啦？欢迎欢迎！",
    "欢迎{name}～群里有啥不懂的尽管问，{me}好说话得很。",
    "新朋友{name}入群啦！先自我介绍一下呗？",
    "欢迎{name}！以后就是一家人了，别客气。",
]


WELCOME_PROMPT = """【有新人进群了】{name} 刚加进这个群。你在群里看到了，想随口跟 TA 打个招呼。
{group}
像群里的老群友那样说一句就行，可以调皮一点：坏笑着调侃 TA 的昵称、给个俏皮的下马威、开个小玩笑，或者问一句 TA 从哪来的；
别用「欢迎加入大家庭」「有问题尽管问」这种客服腔，别做自我介绍式的长段；
只写一行、不超过二十个字；不要写 TA 的名字（会自动艾特 TA），不要用括号写动作，不要输出任何 [标记]。"""


async def _compose_welcome(group_id, name: str) -> str:
    """用她自己的话欢迎新人（每次都不一样）；生成不了就退回固定那几句。"""
    if humanize.skip_if_limited(group_id, "入群欢迎"):
        return ""
    try:
        from . import proactive
        from .cleanup import drop_periods
        recent = [str(e.get("content") or "")[:40] for e in memory.get_history(int(group_id))[-6:] if e.get("role") == "user"]
        group = attention.group_note(group_id)
        if recent:
            group = (group + "\n" if group else "") + "群里刚才在聊：" + " / ".join(recent[-4:])
        raw = await llm.chat([{"role": "system", "content": proactive.base_system(str(group_id), str(config.BOT_QQ or ""))},
                              {"role": "user", "content": WELCOME_PROMPT.format(name=name, group=group)}])
        text = proactive._clean(raw, 1)
        if config.NO_PERIOD:
            text = drop_periods(text)
        return text if 2 <= len(text) <= 40 else ""
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AI聊天] 群{group_id} 生成欢迎语失败，用固定的: {e}")
        return ""


async def handle_group_increase(event: GroupIncreaseNoticeEvent):
    """新成员加群时，自动艾特新成员并欢迎。"""
    if not isinstance(event, GroupIncreaseNoticeEvent):
        return
    bind_bot(event)
    group_id = getattr(event, "group_id", None)
    if group_id is None or not permissions.allowed(str(group_id), "chat", True):
        return
    if str(event.user_id) == config.BOT_QQ:
        return
    if not config.WELCOME_NEW_MEMBER:
        _note_join(event, group_id, "", "")
        return
    bot = None
    try:
        bot = bot_for(event)
        info = await bot.get_group_member_info(
            group_id=int(group_id), user_id=int(event.user_id)
        )
        name = info.get("card") or info.get("nickname") or "新朋友"
    except Exception as e:
        logger.warning(f"[AI聊天] 群{group_id} 获取新成员信息失败，用默认称呼: {e}")
        name = "新朋友"
    if bot is None:
        _note_join(event, group_id, "", "")
        return
    text = await _compose_welcome(group_id, name)
    if not text:
        if roles.is_furina():
            text = random.choice(WELCOME_STYLES).format(name=name)
        else:
            text = random.choice(WELCOME_STYLES_GENERIC).format(name=name, me=roles.display_name())
    msg = Message([
        MessageSegment.at(event.user_id),
        MessageSegment.text(f" {text}"),
    ])
    try:
        await bot.send_group_msg(group_id=group_id, message=humanize.prepare_message(msg))
        logger.info(f"[AI聊天] 群{group_id} 新成员{name}（{event.user_id}）入群，已欢迎")
        memory.add_message(int(group_id), "assistant", text)
    except Exception as e:
        logger.warning(f"[AI聊天] 群{group_id} 欢迎新成员失败: {e}")
        text = ""
    _note_join(event, group_id, "" if name == "新朋友" else name, text)


def _note_join(event, group_id, name: str, welcomed: str) -> None:
    """告诉二代仿真有人进群了（以及已经替她欢迎过、说了什么），免得她再欢迎一遍或者找不到人。"""
    try:
        from . import kernel

        kernel.note_join(getattr(event, "self_id", ""), group_id, event.user_id, name, welcomed)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[二代仿真] 记录入群失败: {e}")

