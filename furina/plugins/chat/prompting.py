"""聊天提示词组装：读图、拼当前消息、取历史、拼系统提示词、组成发给模型的 messages。

由 chat._reply 按顺序调用；每一步只做一件事，方便单独测试和调整提示词。
"""
import re
import time

from nonebot import logger

from . import config, context, digest, emotion, favor, huoren, learning, memory, stickers, upkeep, vision
from .cleanup import _scrub_fixed_template
from .quotes import _iter_segments, _sender_name, at_label
from .role_persona import build_persona, build_shared_lore
from ...core import roles
from ...core.botref import bot_for



# “这谁 / 这是啥 / 哪个角色”这类指向刚才那张图的追问
_ASK_IMAGE_RE = re.compile(
    r"(这|那|图里|图上|左边|右边|中间)(个|张|位)?(是)?(谁|啥|什么|哪个|哪位)"
    r"|是谁|谁啊|谁呀|啥角色|什么角色|哪个角色|哪部|出处|认不出|叫啥|叫什么"
)


def _asks_about_recent_image(text: str) -> int:
    """返回应回看的秒数：0 表示不是在问图。"""
    t = (text or "").strip()
    if not t or re.search(r"你是谁|我是谁|你叫|你是啥", t):
        return 0
    if _ASK_IMAGE_RE.search(t):
        return config.RECENT_IMAGE_SECONDS
    # 很短的问句（如“三月七？”“流萤吗”）通常是在猜刚发的那张图，只回看 2 分钟
    if len(t) <= 12 and t.endswith(("?", "？", "吗", "吧", "么")) and not re.search(r"[你我]", t):
        return min(120, config.RECENT_IMAGE_SECONDS)
    return 0


def _gap_label(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} 分钟后"
    hours = minutes // 60
    return f"{hours} 小时后" if hours < 24 else "隔了很久后"


def _history_with_gaps(history: list) -> list:
    """把历史转成发给模型的消息，相邻间隔较大处给后一条加“【N 分钟后，可能是新话题】”标记。"""
    out, prev_ts = [], None
    for m in history:
        role = m.get("role", "user")
        limit = config.HISTORY_SEND_CHARS_SELF if role == "assistant" else config.HISTORY_SEND_CHARS
        content = (m.get("content") or "")[:limit]
        ts = m.get("ts")
        try:
            ts = float(ts) if ts else None
        except (TypeError, ValueError):
            ts = None
        if ts and prev_ts and ts - prev_ts >= config.HISTORY_GAP_SECONDS:
            content = f"【{_gap_label(ts - prev_ts)}，可能是新话题】" + content
        if ts:
            prev_ts = ts
        out.append({"role": role, "content": content})
    return out


async def gather_images(event, group_id, nickname, current_text, has_image, quote, forced, vision_images):
    """返回 (附加给模型的图片字节, 其中来自引用的张数, 补看最近图片的说明)。"""
    # 本条消息自己发的图作为视觉输入
    attach_images = list(vision_images or [])
    # 艾特机器人时，引用消息里的图也喂给模型（群友引用图片问问题时能看懂）
    # 普通随机接话时不喂引用图，避免模型脑补成搜图/强化指令
    quote_image_count = 0
    if forced and quote is not None and quote.image_segments:
        try:
            bot = bot_for(event)
            for file_id, url in quote.image_segments:
                if len(attach_images) >= config.VISION_MAX_IMAGES:
                    break
                data = await vision.fetch_image_bytes(bot, file_id, url)
                if data:
                    attach_images.append(data)
                    quote_image_count += 1
            if quote_image_count:
                logger.info(f"[AI聊天] 群{group_id} 附加引用消息中的 {quote_image_count} 张图片给模型看")
        except Exception as e:
            logger.warning(f"[AI聊天] 引用图片下载失败: {e}")

    # 本条没带图、也没有引用图，但在问“这谁/这是啥”：把群里最近几分钟的图补给模型看
    recent_image_note = ""
    if (config.CHAT_VISION and not attach_images and not has_image
            and not (quote is not None and quote.image_segments)
            and (_look_back := _asks_about_recent_image(current_text))):
        try:
            bot = bot_for(event)
            recent = memory.recent_images(group_id, _look_back)
            picked = []
            for item in recent:
                if len(attach_images) >= config.VISION_MAX_IMAGES:
                    break
                data = await vision.fetch_image_bytes(bot, item["file"], item["url"])
                if data:
                    attach_images.append(data)
                    picked.append(item)
            if picked:
                ago = int(time.time() - picked[0]["ts"])
                ago_txt = f"{ago} 秒前" if ago < 60 else f"{ago // 60} 分钟前"
                recent_image_note = (
                    f"群里 {ago_txt} {picked[0]['sender']} 发的图你现在能看到（共 {len(picked)} 张，最新的在前）。"
                    f"{nickname} 这句多半是在问这张图：以你亲眼看到的图片内容为准回答，"
                    "别拿更早聊天里提到过的角色名去猜；认不出就直说认不出。"
                )
                logger.info(f"[AI聊天] 群{group_id} 追问最近图片，补看 {len(picked)} 张（{ago_txt}）")
        except Exception as e:
            logger.warning(f"[AI聊天] 补看最近图片失败: {e}")
    return attach_images, quote_image_count, recent_image_note


def build_user_content(event, nickname, current_text, has_image, quote, forced,
                       attach_images, quote_image_count, recent_image_note) -> str:
    """本轮发给模型的「昵称：内容」（含场景说明、引用、艾特、补看图片说明）。"""
    # 组装正文：图片已经作为视觉输入附加时，不再写 [图片] 占位文字，
    # 否则模型会把"图片"两个字复读进回复里（句尾冒出"图片"的原因）
    # 注意：这些是给模型的指令说明，用句号不用括号，避免模型模仿括号风格
    parts = []
    if forced:
        # 明确当前说话人，避免模型把历史消息或引用对象当成"现在对我说话的人"
        parts.append(f"当前场景：群聊被艾特回复。说话人：{nickname}。只回应 TA 一个人。")
    else:
        parts.append(f"当前场景：群聊随机接话。说话人：{nickname}。对方没有特意找你，只是在群里说话，你在旁边自然插一句即可。")
    if quote is not None and quote.text and getattr(quote, "forward", False):
        # 引用的是聊天记录 / 推送的帖子（哪怕是她这个号发出去的，也不是她说的话）
        qtext = quote.text.replace("[图片]", "[一张图]")[:400]
        src = "你这个号之前推送到群里的" if context.quote_from_bot(quote, config.BOT_QQ) else f"{quote.sender_name or '别人'} 发的"
        parts.append(f"{nickname} 回复的是{src}一份聊天记录/帖子，内容大概是：\n{qtext}\n"
                     f"那不是你说的话。{nickname} 这句是在对里面的内容说的：像「妈妈来了」「老婆来了」「这谁」「好怪」"
                     f"多半是在调侃里面的角色或人，按这个意思接，别当成 TA 在说自己。")
    elif quote is not None and not quote.text and not quote_image_count:
        parts.append(f"{nickname} 是回复了一条你打不开的消息（多半是聊天记录或视频），这句话是在说那条消息里的东西，"
                     f"不是在说 TA 自己；猜不出就直接问是啥，别瞎接。")
    elif quote is not None and quote.text:
        who = quote.sender_name or "别人"
        if context.quote_from_bot(quote, config.BOT_QQ):
            # 引用的是芙芙自己说的话：这是同一话题的延续，顺着回答
            qtext = quote.text.replace("[图片]", "[一张图]")
            _mine_extra = "，引用的图你也能看到" if quote_image_count > 0 else ""
            parts.append(f"{nickname} 引用了你之前说的「{qtext[:120]}」来追问/接话{_mine_extra}，这是同一话题的延续，直接顺着你的话回答，别当成新话题。")
        elif quote_image_count > 0:
            # 引用的图已经作为视觉输入传给模型了，直接说明
            qtext = quote.text.replace("[图片]", "").strip()
            if forced:
                parts.append(f"{nickname} 引用了 {who} 的一条消息，其中的图片你已经能看到了，引用文字内容：\n{qtext}\n请结合图片和文字回答 {nickname} 的问题。")
            else:
                parts.append(f"{nickname} 引用了 {who} 的一条消息，其中的图片你已经能看到了，引用文字内容：\n{qtext}\n你可以围绕它自然接一句。")
        else:
            # 引用图只是背景，用中性的"一张图"占位，避免模型看到"图片"字样就跑去脑补图片功能
            qtext = quote.text.replace("[图片]", "[一张图]")
            if forced:
                parts.append(f"{nickname} 引用了 {who} 的一条消息，引用内容：\n{qtext}\n引用只是背景，不是对你说的，别替 {who} 回答。")
            else:
                parts.append(f"{nickname} 引用了 {who} 的一条消息，引用内容：\n{qtext}\n你可以围绕它自然接一句，但不要装作对方在跟你说话，也不能没头没尾。")
    # 本条消息还艾特了谁（不含机器人自己）；被艾特时只回应说话人，随机接话时在旁边自然插一句
    at_names = []
    for stype, sdata in _iter_segments(event.message):
        if stype == "at":
            qq = str(sdata.get("qq") or "")
            label = at_label(qq) if qq and qq != config.BOT_QQ and qq != "0" else ""
            if label:
                at_names.append(str(sdata.get("name") or "") or label.lstrip("@"))
    if at_names:
        if forced:
            parts.append(f"这条消息还艾特了 {'、'.join(at_names)}（TA 话里的「@…」就是这个人）。你回的是 {nickname}；"
                         f"如果 {nickname} 是在说这个人的事（比如「叫@某某，不是我」），就按这个人理解，别安到 {nickname} 或别人头上。")
        else:
            parts.append(f"这条消息还艾特了 {'、'.join(at_names)}，你可以在旁边自然插一句，别代替他们回答，也别突然自言自语。")
    if recent_image_note:
        parts.append(recent_image_note)
    if has_image and not attach_images:
        parts.append("[图片]")
    if current_text:
        parts.append(current_text)
    user_content = "\n".join(parts) if parts else "（没有说话，只是发了点东西）"
    full_user = f"{nickname}：{user_content}"
    return full_user


def take_history(group_id, nickname, store, current_text, quote, full_user):
    """取群上下文（并撤掉刚记下的本条普通版本），给当前消息补上说话人脉络提示。"""
    # 把本轮已记录的普通上下文替换成带引用信息的完整版本
    history = memory.get_history(group_id)
    if store:
        plain = f"{nickname}：{store}"
        if history and memory.same(history[-1], "user", plain):
            memory.pop_last(group_id)
            history = memory.get_history(group_id)

    # 补充 TA 自己刚才说过的话，帮模型联系上下文（历史原样不动，不混入别的话题）
    # 注意：必须放在 history 取出之后，否则 history 未定义
    _exclude_line = f"{nickname}：{store}" if store else ""
    # 结构化记录里谁接谁的话已经一目了然，不再额外拼「TA 刚才说过」「对话脉络」两段长提示
    if not config.STRUCTURED_CONTEXT:
        _recap = context.speaker_recap(history, nickname, exclude=_exclude_line)
        if _recap:
            full_user += "\n" + _recap
        _thread = context.dialogue_thread(history, nickname, exclude=_exclude_line)
        if _thread:
            full_user += "\n" + _thread
    _anchor = context.build_target_anchor(
        nickname,
        current_text,
        quote=quote,
        bot_qq=config.BOT_QQ,
        recent_replies=memory.recent_assistant_messages(group_id, 2),
    )
    if _anchor:
        full_user += "\n" + _anchor
    return history, full_user


async def fetch_stickers(event) -> list:
    # 带上收藏表情包列表，让模型按语境自己挑（拉取失败就纯文字）
    # 表情包本子开着且有带备注的表情：按备注挑（模型看到的是备注，不是一串链接）
    try:
        from . import stickerlib

        lib = stickerlib.prompt_stickers()
        if lib:
            return lib
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[表情包] 读取表情包本子失败: {e}")
    try:
        bot = bot_for(event)
        sticker_urls = await stickers.fetch_sticker_urls(bot)
    except Exception as e:
        logger.warning(f"[AI聊天] 获取表情包收藏失败: {e}")
        sticker_urls = []
    return sticker_urls


# 人设（每天不变）和后面每轮都变的提示之间的分界。结构化模式会把群聊记录塞进这里：
# 「人设 + 记录」是连续几次调用共同的前缀，能吃到 DeepSeek 的缓存价（约 1/50），每轮变的放最后。
DYN_MARK = "\n\n\x00<<动态提示>>\x00"


def split_dynamic(system_prompt: str) -> tuple[str, str]:
    """(人设部分, 每轮变的部分)；没有分界就整段当人设。"""
    if DYN_MARK in system_prompt:
        a, b = system_prompt.split(DYN_MARK, 1)
        return a, b.strip("\n")
    return system_prompt, ""


def build_system_prompt(event, group_id, current_text, quote, context_note, sticker_urls, extra_notes: str = "") -> str:
    _self_qq = str(getattr(event, "self_id", "") or "")
    # 「你的饭钱」一句放人设末尾：不随轮次变，照样吃缓存
    system_prompt = build_persona(_self_qq) + upkeep.static_line() + DYN_MARK
    # 共享设定：只注入“当前角色已关联、且触发词命中本条消息”的故事/背景设定，平时不带
    _lore_query = (current_text or "") + "\n" + (getattr(quote, "text", "") or "")
    _shared_lore = build_shared_lore(_self_qq, _lore_query)
    if _shared_lore:
        system_prompt += "\n\n" + _shared_lore
    # 场景专属补充规则（如戳一戳时要求自由发挥、不许重复），有则追加
    if context_note:
        system_prompt += "\n\n" + context_note
    # 注入自主学习记住的群友信息（外号/风格要求/小档案）——词条按这句话的相关度挑，称呼只带最近在场的人
    learned_summary = learning.get_group_summary(group_id, query=_lore_query)
    if learned_summary:
        system_prompt += "\n\n【你的长期记忆】" + learned_summary
    # 情绪状态注入（心情影响语气，纯本地拼接零 token）
    try:
        if config.EMOTION_ENABLED:
            # 判心情模式：真要回的这一轮才「说」，同一次变化只说一次（鱼的心情的四道闸）
            emo_note = emotion.get_emotion_note(group_id, getattr(event, "user_id", None), consume=True)
            if emo_note:
                system_prompt += "\n\n" + emo_note
            # 当前对话对象的情绪上下文（偶尔主动提及谁夸过/骂过你）
            user_emo = emotion.get_emotion_for_user(event.user_id)
            if user_emo:
                system_prompt += "\n\n" + user_emo
    except (OSError, ValueError, TypeError):
        logger.warning("[AI聊天] 心情数据异常，暂不注入；原数据保留")
    # 烦躁值：被找烦了是会攒着的（纯本地，零 token）
    try:
        from ...services import annoy

        _annoy_note = annoy.note([getattr(event, "user_id", "")])
        if _annoy_note:
            system_prompt += "\n\n" + _annoy_note
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[AI聊天] 烦躁值提示失败: {e}")
    # 当前对象的真实好感程度 + 问好感时由模型自行理解并自然作答（不报数字）
    _favor_note = favor.favor_note(event.user_id, getattr(event, "self_id", None))
    if _favor_note:
        system_prompt += "\n\n" + _favor_note
    # 冒充群主检测：昵称像群主但 QQ 号不对，提示 AI 调侃反驳
    if str(event.user_id) != config.GROUP_OWNER_QQ:
        sender_nick = _sender_name(event)
        if any(kw in (sender_nick or "") for kw in ("我超爱芙啦", "太有芙啦", "有芙啦")):
            system_prompt += (
                "\n\n【注意】现在跟你说话的人在冒充群主，你要先调侃怼他一句"
                "（比如'你也配叫这名？''盗号狗退散''胆子不小啊敢冒充群主'之类，"
                "自由发挥，语气带点调侃不要太凶），然后再正常回应他说的内容。"
                "绝对不要提 QQ 号、不要说'你不是真群主'这种揭穿逻辑，"
                "就自然地怼一句就行，别让人看出来你在执行什么程序。"
            )
    # 最近几天纪要 + 长期事实注入（模糊记忆）
    if config.DIGEST_ENABLED:
        digest_note = digest.get_digest_note(group_id)
        if digest_note:
            system_prompt += "\n\n" + digest_note
    _notes = getattr(sticker_urls, "notes", None)
    if sticker_urls and _notes:
        lines = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(_notes))
        system_prompt += (
            "\n\n你可以发自己收藏的表情包（后面是你给它写的备注），编号从1开始：\n"
            f"{lines}\n"
            "语境合适时，在回复里加上 <<STICKER:编号>> 就能带一张（例如「晚安~ <<STICKER:5>>」），"
            "按备注挑最贴的那张；尖括号方向必须是两个左尖括号在前、两个右尖括号在后；"
            "平均每三次回复最多用一次，大多数时候纯文字就好，别硬塞。"
        )
    elif sticker_urls:
        lines = "\n".join(f"{i + 1}. {u}" for i, u in enumerate(sticker_urls[: config.STICKER_MAX_COUNT]))
        system_prompt += (
            "\n\n你可以发自己的 QQ 收藏表情包，编号从1开始：\n"
            f"{lines}\n"
            "语境合适时，在回复里加上 <<STICKER:编号>> 就能带一张（例如「晚安~ <<STICKER:5>>」），"
            "注意尖括号方向必须是两个左尖括号在前、两个右尖括号在后，别写反；"
            "平均每三次回复最多用一次，大多数时候纯文字就好，别硬塞。"
        )
    recent_replies = memory.recent_assistant_messages(group_id, 3)
    if recent_replies:
        lines_r = "\n".join(
            f"- {_scrub_fixed_template(r)[:120].replace('[图片]', '').strip()}" for r in recent_replies
        )
        system_prompt += (
            "\n\n你最近几次说过的话（这些都是你亲口说的：立场、玩笑、人设必须前后一致，"
            "绝不能自己打自己的脸——比如你刚认的关系，转头就不许翻脸不认；"
            "但也不要重复原句原话，更别模仿它们的腔调和句式，尤其是老用比喻、"
            + roles.flavor("老拿「～」或破折号「——」结尾、老自称本导演这些毛病）：\n",
                           "老拿「～」或破折号「——」结尾、老用同一个自称这些毛病）：\n")
            + f"{lines_r}\n"
        )
    # 活人感：真人聊天规则 + 此刻在干嘛 + B 站经历 + 群友语感 + 热梗/群梗/热搜 + 芙宁娜口吻（本地拼接，不额外调模型）
    try:
        huoren_note = huoren.build_note(group_id, context_text=_lore_query)
    except Exception as e:  # noqa: BLE001  活人感提示出错不影响正常回复
        logger.warning(f"[AI聊天] 活人感提示拼接失败: {e}")
        huoren_note = ""
    if huoren_note:
        system_prompt += "\n\n" + huoren_note
    try:
        from . import banned

        system_prompt += "\n" + banned.prompt_line()
    except Exception:  # noqa: BLE001
        pass
    # 群里正聊到钱 / token / 框架：告诉她这就是她自己的饭钱，带上真实账目（不调模型）
    try:
        upkeep_note = upkeep.note(group_id, _lore_query)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[AI聊天] 饭钱提示失败: {e}")
        upkeep_note = ""
    if upkeep_note:
        system_prompt += "\n\n" + upkeep_note
    # 本轮说话方式 / 最近口头禅 / 可以顺口关心的近况 / 只发表情包（chat._turn_notes 组装），放最后离生成最近
    if extra_notes:
        system_prompt += "\n\n" + extra_notes
    # 提示词里的"当前日期"每次按当天自动生成，避免写死日期过期
    system_prompt = system_prompt.replace("{CURRENT_DATE}", time.strftime("%Y-%m-%d"))
    # 群主 / 特别关照的人的 QQ 号从 .env（AI_CHAT_GROUP_OWNER_QQ / AI_CHAT_SPECIAL_QQ）读取，不写死在提示词里
    system_prompt = system_prompt.replace("{GROUP_OWNER_QQ}", config.GROUP_OWNER_QQ).replace("{SPECIAL_QQ}", config.SPECIAL_QQ)
    return system_prompt


async def build_messages(system_prompt, history, full_user, attach_images, group_id,
                         transcript_text: str | None = None, plan_note: str = "") -> list:
    if config.STRUCTURED_CONTEXT and transcript_text is not None:
        return await _build_structured(system_prompt, history, full_user, attach_images, group_id,
                                       transcript_text, plan_note)
    return await _build_classic(system_prompt, history, full_user, attach_images, group_id)


async def _build_structured(system_prompt, history, full_user, attach_images, group_id,
                            transcript_text: str, plan_note: str) -> list:
    """带编号的群聊记录 + 现在要回复的消息（MaiBot 式），不再把每个群友拆成一条 user。

    顺序为了省钱：system =「人设 → 群聊记录 → 本轮才有的提示」，user =「现在要回复的消息」。
    人设一天不变、记录只往后追加（transcript 锚定窗口），所以连续几次调用前面一大截都按缓存命中计费。
    """
    persona, dynamic = split_dynamic(system_prompt)
    sys_parts = [persona]
    if transcript_text:
        sys_parts.append("【最近的群聊记录（从旧到新；#是编号，“你”是你自己说的，↩#n 表示引用/回复了第 n 条）】\n"
                         + transcript_text)
    if dynamic:
        sys_parts.append(dynamic)
    system_prompt = "\n\n".join(sys_parts)
    if config.PROMPT_SIZE_LOG:
        logger.info(f"[提示词] 群{group_id} 人设{len(persona)} 记录{len(transcript_text or '')} "
                    f"本轮提示{len(dynamic)} 目标{len(full_user)} 字")
    blocks = []
    try:
        _last_ts = float(history[-1].get("ts") or 0) if history else 0
    except (TypeError, ValueError, AttributeError):
        _last_ts = 0
    head = "【现在要你回复的消息】"
    if _last_ts and time.time() - _last_ts >= config.HISTORY_GAP_SECONDS:
        head += f"（距离上面最后一条已过 {_gap_label(time.time() - _last_ts).replace('后', '')}，很可能是新话题）"
    blocks.append(head + "\n" + full_user)
    if plan_note:
        blocks.append(plan_note)
    blocks.append("群聊记录只是背景：别把别人的话安到说话人头上，别从无关的旧消息里挑词拼句；"
                  "回复里不要出现编号、时间或「↩」这些记录格式。")
    prompt_user = "\n\n".join(blocks)
    messages = [{"role": "system", "content": system_prompt}]
    image_parts = await vision.image_parts(attach_images)
    if image_parts:
        messages.append({"role": "user", "content": image_parts + [{"type": "text", "text": prompt_user}]})
        logger.info(f"[AI聊天] 群{group_id} 附加 {len(image_parts)} 张图片给模型看")
    else:
        messages.append({"role": "user", "content": prompt_user})
    return messages


async def _build_classic(system_prompt, history, full_user, attach_images, group_id) -> list:
    system_prompt = system_prompt.replace(DYN_MARK, "")
    # 历史消息与当前消息强分隔：让模型明白历史只是背景，下面这条才是现在要回复的话，
    # 避免它把历史里的人名/称呼/梗（如 D大哥）脑补进回复
    prompt_user = full_user
    if history:
        prompt_user = (
            "【以下才是现在要你回复的目标消息。\n"
            "• 同人同话题自然续接：如果当前说话人是在延续 TA 自己刚才的话、或者追问/接你上一句的回复，请顺着上方对话脉络自然接话，保持前后逻辑连贯；\n"
            "• 旁人噪音与旧话题隔离：与当前说话人无关的其他群友闲聊、以及标着【N 分钟后，可能是新话题】之前的旧话题只作背景，不要把别人的话或旧话题张冠李戴套到当前说话人身上，也不要从无关历史里挑词拼句子；\n"
            "• 立场一致：如果你之前对同一件事表过态，这次要和自己说过的保持一致，发现说错了就大方承认。】\n"
            + full_user
        )
    messages = [{"role": "system", "content": system_prompt}]
    # 历史只取最近一小段并截短：模型需要的是"刚才发生了什么"，不是完整聊天记录，
    # 太长反而会从历史里捡词拼句、答非所问
    send_history = history[-config.HISTORY_SEND_LIMIT:] if config.HISTORY_SEND_LIMIT > 0 else history
    send_history = _history_with_gaps(send_history)
    try:
        _last_ts = float(history[-1].get("ts") or 0) if history else 0
    except (TypeError, ValueError, AttributeError):
        _last_ts = 0
    if _last_ts and time.time() - _last_ts >= config.HISTORY_GAP_SECONDS:
        prompt_user = f"【距离上面最后一条已过 {_gap_label(time.time() - _last_ts).replace('后', '')}，这很可能是新话题】\n" + prompt_user
    messages.extend(send_history)
    # 编码失败的图片直接丢弃，不能把空 url 发给模型（必然 400）
    image_parts = await vision.image_parts(attach_images)
    if image_parts:
        content = image_parts + [{"type": "text", "text": prompt_user}]
        messages.append({"role": "user", "content": content})
        logger.info(f"[AI聊天] 群{group_id} 附加 {len(image_parts)} 张图片给模型看")
    else:
        messages.append({"role": "user", "content": prompt_user})
    return messages
