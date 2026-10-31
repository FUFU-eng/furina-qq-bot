"""聊天核心：按步骤组装上下文 → 调用 LLM → 清洗与防重复 → 解析动作 → 发送 → 执行动作。

提示词的拼装在 prompting.py，动作标记在 actions.py，这里只负责编排。
"""

import asyncio
import random
import re
import time

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from . import actions, config, deai, humanize, learning, life_events, llm, memory, memory_intent, planner, prompting, selfacts, stickers, talkshape, transcript, tts, vision, voice
from . import attention, banned, boundary, diary, emotion, gallery, kouchan, people, presence, stickerlib, websearch
from .cleanup import (
    _is_awkward_sentence,
    _scrub_fixed_template,
    _should_send_sticker,
    _split_reply_parts,
    burst_parts,
    _strip_action_parentheses,
    _strip_context_markers,
    _tame_dashes,
    _too_similar,
    _trim_reply,
    drop_periods,
)
from .matchers import _chat
from .persona import DAILY_RULES, LORE, LORE_FULL, PERSONA, _asks_about_identity  # noqa: F401  兼容旧导入
from .prompting import _asks_about_recent_image, _gap_label, _history_with_gaps  # noqa: F401  兼容旧导入
from .quotes import _iter_segments  # noqa: F401
from ...core import brain, roles  # noqa: F401  roles 兼容旧导入
from ...core.botref import bot_for

# 后台任务保活：asyncio 只对任务保持弱引用，不保存引用的任务可能在执行中途被回收
_bg_tasks: set = set()


def _spawn_bg(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task

async def _retry_reply(messages: list, prev_reply: str, instruction: str) -> str:
    """对不满意的回复，追加一句提示让模型重新生成一次；失败或为空时沿用原回复。"""
    # 防御性清洗：确保每条消息的 content 都是非空字符串，避免某些模型报 unsupported input format
    retry_messages = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = m.get("content")
        if isinstance(content, list):
            # 多模态 content：提取所有 text 部分拼成字符串（重试时不带图，纯文本更稳）
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "") or ""))
            content = "".join(text_parts).strip()
        if not isinstance(content, str):
            content = str(content or "")
        content = content.strip()
        if not content:
            continue  # 跳过空内容消息，避免 400 错误
        retry_messages.append({"role": role, "content": content})
    if not prev_reply or not isinstance(prev_reply, str):
        return prev_reply or ""
    retry_messages.append({"role": "assistant", "content": prev_reply.strip()})
    retry_messages.append({"role": "user", "content": instruction.strip()})
    try:
        reply2 = await llm.chat(retry_messages)
        reply2 = (reply2 or "").strip()
        reply2 = _strip_context_markers(reply2)
        reply2 = _scrub_fixed_template(reply2)
        reply2 = _trim_reply(reply2, config.AI_CHAT_MAX_REPLY_CHARS)
        return reply2 or prev_reply
    except llm.LLMError as e:
        logger.warning(f"[AI聊天] 重试失败（{e}），沿用原回复。重试消息数={len(retry_messages)}，总字符≈{sum(len(m['content']) for m in retry_messages)}")
        return prev_reply


async def _collect_message_images(event, group_id, extra_image_segments=None) -> tuple[list, dict | None]:
    """下载本条消息里的图片（可附加转发节点里的引用），返回图片字节与第一条缓存条目。"""
    out: list = []
    first_entry = None
    if not config.CHAT_VISION:
        return out, None
    try:
        bot = bot_for(event)
    except Exception:
        return out, None
    image_segments = [
        (str(sdata.get("file") or ""), str(sdata.get("url") or ""))
        for stype, sdata in _iter_segments(event.message) if stype == "image"
    ]
    image_segments.extend(
        (str(file_id or ""), str(url or ""))
        for file_id, url in (extra_image_segments or [])
    )
    for file_id, url in image_segments:
        data = await vision.fetch_image_bytes(bot, file_id, url)
        if not data:
            continue
        # 旧版还会把每张图的字节塞进 _group_recent_images（每群 8 张），但全项目没有任何读取方，
        # 只是白白常驻内存，这里不再保留。
        entry = {"ts": time.time(), "data": data, "desc": ""}
        if first_entry is None:
            first_entry = entry
        out.append(data)
        if len(out) >= config.VISION_MAX_IMAGES:
            break
    if out:
        logger.info(f"[AI聊天] 群{group_id} 已读取 {len(out)} 张图片")
    return out, first_entry


def _schedule_image_desc(group_id: int, stored_line: str, entry: dict):
    """后台生成图片的一句话描述，写进上下文记忆，方便之后"联系上下文"提到这张图。"""

    async def _run():
        try:
            desc = await vision.describe_image(entry["data"])
        except Exception as e:
            logger.warning(f"[AI聊天] 群{group_id} 后台图片描述失败: {e}")
            return
        if not desc:
            return
        entry["desc"] = desc
        new_line = stored_line.replace("[图片]", f"[图片：{desc}]", 1)
        if memory.replace_content(group_id, stored_line, new_line):
            logger.info(f"[AI聊天] 群{group_id} 已把图片描述写入上下文: {desc}")

    _spawn_bg(_run())


def _last_user_text(messages: list) -> str:
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):  # 带图的多模态消息
                c = " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
            return str(c or "")
    return ""


async def _polish(messages: list, reply: str, group_id) -> str:
    """清洗模型输出；与最近回复雷同、铸币梗重复或句子绕口时重试一次（最多一次）。"""
    reply = (reply or "").strip()
    # 统一在末尾和发送闸门处理句号，不能rstrip误删表达无语的连续句号。
    # 去掉模型从上下文学来的 [图片] 尾巴
    reply = _strip_context_markers(reply)
    # 兜底清掉「宝~ 你怕不是个铸币哦～」这类固定开场模板
    reply = _scrub_fixed_template(reply)
    # 超长回复在标点处截断，防止越说越长、刷屏
    reply = _trim_reply(reply, config.AI_CHAT_MAX_REPLY_CHARS)

    # 防重复：和最近几次回复太像时，重试一次要求换种说法。
    # 最多只重试一次（retried 标记），避免连环重试让模型越改越跑题、越说越没逻辑。
    recent_replies = memory.recent_assistant_messages(group_id, 8)
    # 先剥掉 <<STICKER:n>> 再比相似度，否则表情标记会让原本重复的句子被当成"新话"
    reply_for_check = stickers.extract_sticker_marker(reply)[0] or reply
    retried = False
    # 主人点名禁止的句式（「这X我服」等）：先让她重说一次，最后发出前还会兜底删掉那半句
    _ban = banned.hit(reply_for_check)
    if _ban:
        logger.info(f"[AI聊天] 回复用了禁用句式「{_ban}」，重新生成一次: {reply_for_check[:40]}")
        reply = await _retry_reply(messages, reply, banned.RETRY_HINT.format(name=_ban))
        reply_for_check = stickers.extract_sticker_marker(reply)[0] or reply
        retried = True
    # 去 AI 味：客服腔结尾、书面连接词直接删；自称 AI、markdown 标题加粗这类让模型重说一次
    if config.DEAI:
        reply = deai.soft_clean(reply)
        _ai_why = deai.hard_problem(reply) or deai.structure_problem(reply)
        if _ai_why and not retried:
            logger.info(f"[AI聊天] 回复有 AI 味（{_ai_why}），重新生成一次")
            reply = await _retry_reply(messages, reply, deai.RETRY_HINT.format(why=_ai_why))
            reply = deai.strip_markdown(deai.soft_clean(reply)).strip()
            reply_for_check = stickers.extract_sticker_marker(reply)[0] or reply
            retried = True
    # 句式模板 / 口癖：「挺好看的，就是有点晃眼」这种先夸后挑刺的对仗句、评论家腔、最近说烂了的口头禅 → 重说一次
    if config.DEAI and not retried:
        _topic = _last_user_text(messages)
        _why = kouchan.hard_template(reply_for_check) or "、".join(kouchan.reply_hits(reply_for_check, _topic)[:3])
        if _why:
            logger.info(f"[AI聊天] 回复套了老句式（{_why}），重新生成一次: {reply_for_check[:40]}")
            reply = await _retry_reply(messages, reply, kouchan.RETRY_HINT.format(why=_why))
            if kouchan.hard_template(reply):  # 还是先夸后挑刺：只留前半句的反应
                reply = kouchan.trim_template(reply)
            reply_for_check = stickers.extract_sticker_marker(reply)[0] or reply
            retried = True
    if not retried and recent_replies and any(_too_similar(reply_for_check, r) for r in recent_replies):
        logger.info("[AI聊天] 检测到与最近回复雷同，重新生成一次")
        reply = await _retry_reply(
            messages, reply,
            "你刚才的回复和最近说过的话太像了，请用另一种说法回复同一个意思，"
            "别跑题、别换话题、别加无关内容。",
        )
        retried = True
    # 铸币防重复：只有这次回复跟最近某条「铸币」回复确实雷同（整句或后半句高度接近）才重试，
    # 避免「铸币」一词一出现就重试、把逻辑带偏
    if not retried and "铸币" in reply_for_check and any(
        r and "铸币" in r and _too_similar(reply_for_check, r, 0.55) for r in recent_replies
    ):
        logger.info("[AI聊天] 最近已用过铸币梗且说法雷同，要求换个新说法")
        reply = await _retry_reply(
            messages, reply,
            "你刚才这句和最近用过的铸币说法太像了，请用另一种全新的说法回复同一个意思"
            "（或者干脆换个不带铸币的吐槽方式），别跑题、别换话题、别加无关内容。",
        )
        retried = True

    # 逻辑兜底：模型偶尔会输出「得看毛茸茸度够不够才算好福瑞」这类绕口病句，
    # 检测到就重试一次，要求换成普通直白的人话
    if not retried and _is_awkward_sentence(reply):
        logger.info("[AI聊天] 回复句式绕口/生造词，重新生成一次")
        reply = await _retry_reply(
            messages, reply,
            "你刚才这句话绕口、生造词，中文逻辑不通顺；请用最普通直白的一句人话重新说，"
            "别用「得看XX度够不够才算好XX」这种句式，也不要说自己都不懂的词。",
        )
        retried = True

    # 【已停用】防扯无关的人/称呼：容易误判且重试可能导致模型池挂掉，暂时取消
    # allowed_text = full_user
    # if quote is not None and quote.text:
    #     allowed_text += "\n" + quote.text
    # unrelated = _find_unrelated_names(reply, allowed_text, group_id, history)
    # if not retried and unrelated:
    #     logger.info(f"[AI聊天] 回复提到无关内容（{'、'.join(unrelated)}），重新生成一次")
    #     reply = await _retry_reply(
    #         messages, reply,
    #         (
    #             f"你刚才的回复提到了「{'」「'.join(unrelated)}」，但对方这句话根本没提这些内容。"
    #             "把无关的人名/称呼/话题全部删掉，只针对对方这句话本身重新说一句自然的话。",
    #         ),
    #     )

    # 去掉结尾多余的波浪号（AI 腔重灾区），避免每条都「～」收尾
    reply = re.sub(r"[~～]+$", "", reply).strip()

    # 压住 AI 腔破折号：句尾的删掉，句中最多留一个，其余换逗号
    reply = _tame_dashes(reply)
    # 自称刷屏兜底：最近几条已经频繁「本XX / XX大人」，这次换回「我」
    guarded = voice.guard_self_titles(reply, recent_replies)
    if guarded != reply:
        logger.info(f"[AI聊天] 群{group_id} 最近自称用得太多，这次换成「我」: {reply[:60]} -> {guarded[:60]}")
        reply = guarded
    # 不打句号（真人聊天几乎不用）：句末删掉、句中换逗号
    if config.NO_PERIOD:
        reply = drop_periods(reply)
    if banned.hit(reply):  # 重说完还用：直接删掉那半句（删光了就不发）
        logger.info(f"[AI聊天] 群{group_id} 重说后仍用禁用句式「{banned.hit(reply)}」，删掉那半句")
        reply = banned.strip(reply)
    return reply


def _pick_sticker(reply: str, sticker_urls: list, group_id, force: bool = False) -> tuple[str, str | None]:
    """解析 <<STICKER:n>>，按频率上限决定是否附带；模型没挑时按概率随机兜底。
    force=True（这轮是「只发表情包」）：模型挑的那张不受频率上限限制。"""
    # 解析表情包标记，并按频率上限决定是否真的附带
    reply, sticker_idx = stickers.extract_sticker_marker(reply)
    if not sticker_urls:
        return reply, None
    picked = sticker_idx is not None and 0 <= sticker_idx < len(sticker_urls)
    if picked and force:
        logger.info(f"[AI聊天] 群{group_id} 附带收藏表情 #{sticker_idx + 1}")
        stickerlib.mark_used_uri(sticker_urls[sticker_idx])
        return reply, sticker_urls[sticker_idx]
    # 模型没主动挑时按「附带表情包概率」随机配一张兜底；她挑的和兜底的记在同一本账上，
    # 整体附带比例不超过设置里的数（以前兜底不记账，实际比例会高出一截）
    fallback = not picked and random.random() < config.STICKER_PROBABILITY
    if _should_send_sticker(group_id, wanted=picked or fallback):
        idx = sticker_idx if picked else random.randrange(len(sticker_urls))
        logger.info(f"[AI聊天] 群{group_id} {'附带收藏表情' if picked else '随机兜底表情'} #{idx + 1}")
        stickerlib.mark_used_uri(sticker_urls[idx])
        return reply, sticker_urls[idx]
    if picked:
        logger.info(f"[AI聊天] 群{group_id} 表情频率已达上限，本次略过")
    return reply, None


async def _send_text(sender, event, reply: str, sticker_url, forced: bool, group_id, spent: float = 0.0,
                     mention: bool | None = None) -> None:
    """文字（可能拆两条）+ 表情包分开发；被艾特时带艾特，一半概率用引用格式。
    spent=模型已花的秒数：拟人发送时先「打字」一会儿再发（扣掉这段时间），多条之间有停顿。"""
    # 文字和表情包分成两条消息发送：先文字，后表情包
    sticker_seg = MessageSegment("image", {"file": sticker_url, "sub_type": 1}) if sticker_url else None
    # 只有被艾特触发回复（forced）时文字才带艾特；随机接话不带，避免打扰对方
    # forced 时随机选择：有时候用引用格式（MessageSegment.reply），有时候只艾特，不要每次都一样
    # mention=False：和机器人收尾时不艾特也不引用（对方机器人被点名/被引用就会接着回）
    at = forced if mention is None else bool(mention)
    use_reply = at and random.random() < config.QUOTE_PROBABILITY  # 偶尔引用，平时只艾特
    if not reply and sticker_url:
        # 只回一张表情包：被艾特时一半概率引用对方那条，不单独再发一条只有艾特的消息
        segs = []
        msg_id = getattr(event, "message_id", None)
        if use_reply and msg_id:
            segs.append(MessageSegment.reply(msg_id))
        segs.append(sticker_seg)
        await humanize.pause(humanize.first_delay("", spent))
        await sender(Message(segs))
        return
    # 小鲸鱼式分条：模型每写一行就是一条消息，太长的行在标点处再拆；不限条数，但每条都短。
    # 只有一行且不算长时，偶尔还是随机拆成两条（模拟真人分两次发）。艾特和引用只放第一条。
    parts = burst_parts(reply) if reply else []
    if len(parts) == 1:
        parts = _split_reply_parts(parts[0]) or parts
    if len(parts) > 1:
        logger.info(f"[AI聊天] 群{group_id} 分 {len(parts)} 条发送: " + " | ".join(p[:30] for p in parts))
    msg_id = getattr(event, "message_id", None)
    for idx, part in enumerate(parts):
        await humanize.pause(humanize.first_delay(part, spent) if idx == 0 else humanize.burst_gap(part))
        segs = []
        if at and idx == 0:
            if use_reply and msg_id:
                segs.append(MessageSegment.reply(msg_id))
            segs.append(MessageSegment.at(event.user_id))
        segs.append(MessageSegment.text((" " if at and idx == 0 else "") + part))
        await sender(humanize.prepare_message(Message(segs)))
    if sticker_url:
        if parts:
            await humanize.pause(humanize.gap_before(""))
        else:
            await humanize.pause(humanize.first_delay("", spent))
        await sender(Message(sticker_seg))


STICKER_ONLY_NOTE = ("【这次只发表情包】对方这句很短，这次不用打字：整条回复只写一个 <<STICKER:编号>>，"
                     "从上面的表情包列表里挑一张最贴合语气的，不要任何文字。")


def _quiet_forced(forced: bool, history: list, mid) -> bool:
    """被叫到、且前面 PLANNER_QUIET_SKIP_MIN 分钟内群里没别的消息：没有对话关系要理，不必先判断。"""
    minutes = config.PLANNER_QUIET_SKIP_MIN
    if not forced or minutes <= 0:
        return False
    now = time.time()
    for e in reversed(history[-12:]):
        if not isinstance(e, dict) or (mid and str(e.get("mid") or "") == str(mid)):
            continue
        try:
            ts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts:
            return now - ts > minutes * 60
    return True


def _turn_notes(group_id, user_id, nickname, current_text, has_image, forced, context_note,
                sticker_urls, allow_actions, mention) -> tuple[str, bool, dict | None]:
    """这一轮额外的提示：说话方式、最近口头禅、可顺口关心的近况、是否只发表情包。
    返回 (提示文本, 是否只发表情包, 要关心的近况事件)。戳一戳等自带说法要求的场景不加说话方式。"""
    notes, sticker_only, followup = [], False, None
    if not context_note:
        if (sticker_urls and allow_actions and mention is not False
                and voice.sticker_only_candidate(current_text, has_image)
                and random.random() < config.STICKER_ONLY_PROBABILITY):
            sticker_only = True
            notes.append(STICKER_ONLY_NOTE)
        elif tts.available(group_id) and tts.asks_for_voice(current_text):
            notes.append(tts.voice_note(group_id))  # 群友想听她的声音：能发就写成口语，发不了就自己推掉
        elif config.STYLE_VARIETY:
            notes.append(voice.turn_note(current_text, forced))
        if config.LIFE_EVENTS:
            try:
                followup = life_events.pending(group_id, user_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[AI聊天] 读取群友近况失败: {e}")
            if followup:
                notes.append(life_events.followup_note(nickname, followup))
    if config.STYLE_VARIETY and not sticker_only:
        habit = voice.habit_note(memory.recent_assistant_messages(group_id, 6))
        if habit:
            notes.append(habit)
    if config.DEAI and not sticker_only:
        try:
            kc = kouchan.note(current_text)
        except Exception as e:  # noqa: BLE001
            kc = ""
            logger.warning(f"[AI聊天] 口癖统计失败: {e}")
        if kc:
            notes.append(kc)
    # 大肥鱼式手感：每轮随机提醒一种「人味」小毛病 / 认真时收住 / 有人复读时可以跟一次
    if not context_note and not sticker_only:
        for extra in (talkshape.note(current_text, forced), talkshape.repeat_note(group_id) if not forced else ""):
            if extra:
                notes.append(extra)
    # 群友刚发了表情包：喜欢可以收藏（表情包本子）
    if not sticker_only:
        hint = stickerlib.save_hint(group_id)
        if hint:
            notes.append(hint)
    # 她自己刚做过的小动作（贴表情 / 戳一戳）：被问到要承认是自己做的
    acts = selfacts.note(group_id, user_id)
    if acts:
        notes.append(acts)
    # 活着的痕迹：刚「睡醒」/ 有话没发出去 / 对方隔了很久才回来
    notes.extend(presence.turn_notes(group_id, user_id, nickname))
    return "\n\n".join(notes), sticker_only, followup


def _person_notes(event, group_id, nickname, current_text, forced, context_note, extra_mids, turn_extra, quote_text: str = "") -> str:
    """这个人的账本 + 察觉（没人理 / 只回一个字 / 连发 / 旧消息 / 参与度 / 收尾）+ 她的日记 + 调用方附加提示。"""
    notes = []
    uid = getattr(event, "user_id", None)
    try:
        if uid is not None:
            # 记忆按「这句话」去想：只想起跟它有关的那几条（鱼的记忆式召回），不是把最近几条全塞进来
            p = people.note(uid, nickname, group_id, query="\n".join(x for x in (current_text, quote_text) if x))
            if p:
                notes.append(p)
        if not context_note:
            notes.extend(attention.notes(group_id, uid, nickname, current_text, event_ts=float(getattr(event, "time", 0) or 0),
                                         extra_count=len(tuple(extra_mids or ())), forced=forced))
        # 她正在故意冷落的人（越界被冷落）：有人问起要认，别说「没顾上」
        for extra in (boundary.cold_note(memory.get_history(group_id)), diary.context_note(), gallery.context_note()):
            if extra:
                notes.append(extra)
    except Exception as e:  # noqa: BLE001  提示拼不出来也不影响回复
        logger.warning(f"[AI聊天] 生成人物/察觉提示失败: {e}")
    if turn_extra:
        notes.append(turn_extra)
    return "\n\n".join(n for n in notes if n)


def _after_reply(event, group_id, nickname, current_text, reply, voiced, sticker_url, context_note) -> None:
    """回完以后：记进 TA 的账本、记下她发了什么（供「没人理」察觉）、刷新「刚聊完多留一会儿」。"""
    uid = getattr(event, "user_id", None)
    try:
        if uid is not None and not context_note:
            people.note_interaction(group_id, uid, nickname, current_text, reply or "")
        if uid is not None and reply:
            emotion.spawn_judge(group_id, uid, nickname, current_text, reply)  # 后台判一次这轮的心情（鱼的心情）
        kind = "voice" if voiced else ("question" if re.search(r"[?？]\s*$", reply or "") else ("image" if sticker_url and not reply else "text"))
        attention.record_outgoing(group_id, uid, kind, reply or "")
        attention.on_reply(group_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AI聊天] 回复后记账失败: {e}")


def _action_memo(act) -> str:
    """只执行了动作、没说话时，记忆里留一句「做了什么」，免得下一轮以为自己没理人。"""
    if act.at_target:
        return f"（艾特了{act.at_target}）{act.at_content}".strip()
    if act.send_image_count:
        return f"（发了{act.send_image_count}张图）"
    if act.essence:
        return "（设了精华）"
    if act.add_target or act.delete_target:
        return "（改了词条）"
    return ""


async def _reply(event, group_id, nickname, store, current_text, has_image, quote, forced: bool, vision_images: list | None = None, send_func=None, context_note: str = "", allow_actions: bool = True,
                 extra_mids=(), abort_check=None, mention: bool | None = None, allow_poke: bool = True,
                 turn_extra: str = ""):
    """组装上下文并调用 LLM 回复（可能附带收藏表情包）。

    send_func 传入时用自定义发送函数（如被戳事件没有消息上下文，只能直接调
    send_group_msg），不传则沿用 _chat.send。
    allow_actions=False 时（如被戳一戳）回复里的动作标记只剥掉、不执行；
    随机接话（forced=False）同样不执行动作标记。
    extra_mids：连发合并时并进来的后续消息 ID（这些消息不再出现在背景记录里，回复后一并记为已回复）。
    abort_check：发送前调用，返回 True 表示对方又补了话，丢弃这一稿并返回 "RETRY"（不发送、不写记忆）。
    mention：是否艾特/引用对方，默认跟随 forced；allow_poke=False 时回复后不戳对方（见 bot_dialog）。
    turn_extra：调用方附加的这一轮提示（如「TA 话好像没说完」）。
    返回 True 表示应交给搜图插件处理。
    """
    if humanize.skip_if_limited(group_id):
        return  # 发不出去就不看图、不判断、不生成，省下模型调用
    sender = humanize.guarded(send_func or _chat.send, group_id)
    meta = transcript.event_meta(event, nickname)
    attach_images, quote_image_count, recent_image_note = await prompting.gather_images(
        event, group_id, nickname, current_text, has_image, quote, forced, vision_images)
    full_user = prompting.build_user_content(
        event, nickname, current_text, has_image, quote, forced,
        attach_images, quote_image_count, recent_image_note)
    history, full_user = prompting.take_history(group_id, nickname, store, current_text, quote, full_user)

    if not config.get_llm_key():
        logger.warning("[AI聊天] 未配置大模型 API Key，跳过 LLM 调用")
        return  # 不发「脑回路没接上」这类模板话：真人没空就是没回

    # 结构化记录 + 先判断再回复（戳一戳这类没有具体消息的场景跳过判断）
    transcript_text, plan_note = None, ""
    if config.STRUCTURED_CONTEXT:
        transcript_text, index = transcript.render(history, exclude_mids=[meta.get("mid"), *extra_mids],
                                                   anchor=str(group_id))
        if not context_note and meta.get("mid") and _quiet_forced(forced, history, meta.get("mid")):
            logger.info(f"[AI聊天] 群{group_id} 前面一阵没人说话，直接回（省一次判断）")
        elif not context_note and meta.get("mid"):
            target_line = f"{nickname}：{current_text or ('[图片]' if has_image else '')}"
            if quote is not None and getattr(quote, "text", ""):
                target_line += f"\n（TA 引用了 {getattr(quote, 'sender_name', '') or '别人'} 的「{str(quote.text)[:100]}」）"
            try:
                cold = boundary.cold_note(history)
            except Exception:  # noqa: BLE001
                cold = ""
            plan_res = await planner.plan(transcript_text, target_line, forced, facts=cold)
            if plan_res is not None and not forced and not plan_res.get("reply", True):
                logger.info(f"[AI聊天] 群{group_id} 判断这时候不适合插话，跳过随机接话")
                return
            plan_note = planner.note(plan_res, index)

    sticker_urls = await prompting.fetch_stickers(event)
    if not context_note and tts.available(group_id) and tts.asks_for_voice(current_text):
        if await tts.refresh_state(group_id) == "cold":
            await tts.voice_tts.warm_up()  # 趁她打字的工夫先把引擎开起来
    extra_notes, sticker_only, followup = _turn_notes(
        group_id, event.user_id, nickname, current_text, has_image, forced, context_note,
        sticker_urls, allow_actions, mention)
    more = _person_notes(event, group_id, nickname, current_text, forced, context_note, extra_mids, turn_extra,
                         quote_text=str(getattr(quote, "text", "") or "") if quote is not None else "")
    # 边界感：对她亲亲 / 动手动脚 / 搞黄色（群主和特别关照的人除外）→ 这轮态度要硬，不配表情包、不发语音
    stern = ""
    if forced and not context_note:
        try:
            stern = boundary.check(event.user_id, nickname, current_text) or boundary.pop_forgive_note(event.user_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[AI聊天] 边界检查失败: {e}")
    if stern:
        if any(w in stern[:40] for w in ("认错", "道歉", "求你原谅")):
            logger.info(f"[AI聊天] 群{group_id} {nickname} 求情认错，这轮按原谅/心软的口气回")
        else:
            logger.info(f"[AI聊天] 群{group_id} {nickname} 越界了，这次态度强硬" + ("（再犯，开始冷落）" if boundary.ignoring(event.user_id) else ""))
        more = (more + "\n\n" + stern).strip()
        if sticker_only:
            sticker_only = False
            extra_notes = extra_notes.replace(STICKER_ONLY_NOTE, "").strip()
    keep_ok = bool(config.GALLERY and attach_images and has_image and not context_note and not sticker_only)
    if keep_ok:
        more = (more + "\n\n" + gallery.NOTE).strip()
    if more:
        extra_notes = (extra_notes + "\n\n" + more).strip()
    if sticker_only:
        logger.info(f"[AI聊天] 群{group_id} 这次只回一张表情包")
    if followup:
        logger.info(f"[AI聊天] 群{group_id} 可以顺口关心 {nickname} 的近况：{followup.get('event')}")
    # 联网搜索：群友明说「搜一下 XX」就先搜好再回；否则告诉她不知道可以写 <<SEARCH:关键词>>
    search_on = websearch.enabled(group_id, forced) and not context_note and not sticker_only
    searched = False
    if search_on:
        extra_notes = (extra_notes + "\n\n" + websearch.NOTE).strip()
    system_prompt = prompting.build_system_prompt(event, group_id, current_text, quote, context_note, sticker_urls,
                                                  extra_notes=extra_notes)
    messages = await prompting.build_messages(system_prompt, history, full_user, attach_images, group_id,
                                              transcript_text=transcript_text, plan_note=plan_note)

    start = time.time()
    # 思考强度（面板「模式与内核 → 思考强度」，群可单独设；自动档：随机插话不想，被问问题 / 查资料时想）
    think = brain.effort_for(group_id, "at" if (forced and not context_note) else "random", current_text)
    think_search = brain.escalated_effort(group_id, think)
    ask_query = websearch.explicit_query(current_text) if search_on else None
    if ask_query:
        found = await websearch.lookup(ask_query, group_id)
        if found:
            websearch.add_to_system(messages, found + "\n（是对方叫你查的，可以自然地说「我看了下」，但别念结果、别贴链接；直接回，别再写 <<SEARCH>>。）")
            searched = True
            think = think_search
    if think != "off":
        logger.info(f"[思考] 群{group_id} 这轮思考强度：{brain.EFFORT_NAMES.get(think, think)}")
    try:
        reply = await llm.chat(messages, effort=think)
        reply, want_query = websearch.extract(reply)
        if want_query and search_on and not searched:
            wait = websearch.wait_line() if forced else None
            if wait:
                try:
                    await sender(Message(MessageSegment.text(wait)))
                except Exception:  # noqa: BLE001
                    pass
            found = await websearch.lookup(want_query, group_id)
            if found:
                websearch.add_to_system(messages, found + "\n现在直接回复对方，别再写 <<SEARCH>>。")
                reply, _again = websearch.extract(await llm.chat(messages, effort=think_search))
                searched = True
            else:
                websearch.add_to_system(messages, "【这次没法看资料】就当自己不太清楚，随口说不确定就好，别提搜索。")
                reply, _again = websearch.extract(await llm.chat(messages, effort=think))
        if searched and websearch.leaks(reply, current_text):  # 露出「我搜了」的痕迹：让她换个说法（只重来一次）
            logger.info(f"[联网搜索] 群{group_id} 回复露了搜索痕迹，重说一次：{reply[:40]}")
            fixed, _q = websearch.extract(await llm.chat(messages + [{"role": "assistant", "content": reply},
                                                                    {"role": "user", "content": websearch.LEAK_FIX}]))
            if fixed and not websearch.leaks(fixed, current_text):
                reply = fixed
        elif want_query:
            logger.info(f"[联网搜索] 群{group_id} 她想搜「{want_query}」，但这轮不能搜（开关/权限/额度），只回文字")
            if not reply:
                reply = random.choice(("这个我还真不太清楚…", "唔，这个我不知道诶", "这我真没听说过", "不知道…你问倒我了"))
    except llm.LLMError as e:
        logger.error(f"[AI聊天] LLM 调用失败（这轮不回，不发模板话）: {e}")
        return
    cost = time.time() - start

    # <<SAVE_STICKER:备注>>：群友刚发的表情包她喜欢，收进自己的表情包本子
    reply, save_note = stickerlib.extract_save(reply)
    if save_note is not None:
        try:
            stickerlib.spawn_save(bot_for(event), group_id, save_note)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[表情包] 收藏失败: {e}")
    # <<KEEP:为什么留它>>：她想把这张图存进自己的相册
    reply, keep_why = gallery.extract(reply)
    if keep_why and keep_ok:
        try:
            gallery.save(attach_images[0], keep_why, nickname, group_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[相册] 存图失败: {e}")
    # [VOICE] / [VOICE:傲娇]：模型想用语音说这句（先剥掉，免得被后面的清洗当成杂质或念出来）
    reply, voice_marked, voice_emotion = tts.extract(reply)
    reply = await _polish(messages, reply, group_id)
    reply, _marked_again, _emo_again = tts.extract(reply)
    voice_marked = voice_marked or _marked_again
    voice_emotion = voice_emotion or _emo_again
    if config.STRUCTURED_CONTEXT:
        reply = transcript.strip_tags(reply)
    reply, sticker_url = _pick_sticker(reply, sticker_urls, group_id, force=sticker_only)
    if stern:
        sticker_url, voice_marked = None, False
    reply, act = actions.parse(reply, group_id)

    # 主动艾特群友是她自己的选择，不要求先被叫到；其余动作仍只响应明确请求。
    if not (forced and allow_actions) and act.has_side_effects():
        logger.info(f"[AI聊天] 群{group_id} 主动回复保留艾特，其余动作标记忽略")
        act.drop_side_effects(keep_at=True)

    # 只回一张表情包/图片属于无效回复：没有任何文字时直接不发，
    # 表情包只能跟在文字后面一起发，绝不单独发一张图
    if not reply:
        # AI输出了搜图/强化标记但没有其他文字时，给一条简单回应，避免完全没反应
        if act.search_image:
            reply = "好的，这就帮你搜图~"
        elif act.upscale:
            reply = "好的，这就帮你强化~"
        elif sticker_only and not act.has_side_effects():
            # 这轮本来就是只发表情包：模型没挑就随便配一张
            if not sticker_url and sticker_urls:
                sticker_url = random.choice(sticker_urls)
            if not sticker_url:
                return
        elif not act.has_side_effects():
            return
        # 只写了动作标记（如整句就是 [AT:昔涟:...]，提示词里的示例正是这样）：不发文字，
        # 动作照常执行。旧版这里直接 return，艾特/发图/设精华等指令就被悄悄吞掉了。
    action_only = not reply and act.has_side_effects()

    # 后处理：删除括号中的动作/神态/心理描述，但保留网络梗括号
    if reply:
        original_reply = reply
        reply = _strip_action_parentheses(reply)
        if reply != original_reply:
            logger.info(f"[AI聊天] 群{group_id} 已删除括号动作描述: 原={(original_reply or '')[:80]} -> 新={(reply or '')[:80]}")
    # 如果删除括号后回复为空，给一个默认回复
    if not reply and not sticker_url and not action_only:
        reply = "嗯？"
    if action_only:
        sticker_url = None  # 表情包只跟着文字发，绝不单独发一张

    # 生成期间对方又补了话：这一稿作废，由调用方带上新内容重来（不发送、不写记忆）
    if abort_check is not None and abort_check():
        logger.info(f"[AI聊天] 群{group_id} {nickname} 在生成期间又补充了消息，丢弃旧稿重新生成")
        return "RETRY"

    if config.NO_PERIOD and reply:
        reply = drop_periods(reply)
    logger.info(f"[AI聊天] 群{group_id} 回复内容: {(reply or '')[:150]}")
    # 记忆里只存干净的「昵称：内容」，不存"当前场景/说话人"等系统说明，
    # 避免模型把元信息当成群友原话照抄或混淆说话人
    if store:  # 纯艾特没有文字时不往记忆里写一条空的「昵称：」
        memory.add_message(group_id, "user", f"{nickname}：{store}", meta=meta)
    memory.add_message(group_id, "assistant", reply or _action_memo(act) or "（发了张表情包）", meta={"to": meta.get("mid")})
    memory.mark_replied(group_id, [meta.get("mid"), *extra_mids], reply)
    logger.info(f"[AI聊天] 群{group_id} 回复 {nickname}，LLM 耗时 {cost:.1f}s")

    # 语音：群友想听 / 模型标了 [VOICE] / 短句随机，合成成功就用芙宁娜的声音发，失败照常发文字
    voiced = False
    if reply and not action_only and not sticker_only and not stern:
        voiced = await tts.maybe_send(sender, group_id, reply, current_text, voice_marked,
                                      trust_none=send_func is not None, emotion=voice_emotion)
    if voiced:
        if sticker_url:
            await humanize.pause(humanize.gap_before(""))
            await sender(Message(MessageSegment("image", {"file": sticker_url, "sub_type": 1})))
    elif reply or sticker_url:
        await _send_text(sender, event, reply, sticker_url, forced, group_id, spent=time.time() - min(start, float(getattr(event, "time", 0) or start)),
                         mention=mention)
    await actions.execute(act, event, group_id, sender)
    if followup:
        life_events.mark_asked(group_id, event.user_id, followup)
    _after_reply(event, group_id, nickname, current_text, reply, voiced, sticker_url, context_note)

    # 回复发出后再按配置概率（AI_CHAT_POKE_PROBABILITY，默认 15%）戳一戳对方（独立动作，不影响回复本身）
    if allow_poke and config.POKE_PROBABILITY > 0 and random.random() < config.POKE_PROBABILITY:
        try:
            bot = bot_for(event)
            await bot.call_api("group_poke", group_id=group_id, user_id=event.user_id)
            logger.info(f"[AI聊天] 群{group_id} 戳了戳 {nickname}（{event.user_id}）")
            selfacts.record(group_id, "poke", f"回完话顺手戳了戳{nickname}", target_uid=str(event.user_id))
        except Exception as e:
            logger.warning(f"[AI聊天] 戳一戳失败: {e}")

    if actions.wants_search(reply, quote):
        logger.info(f"[AI聊天] 群{group_id} AI回复包含搜图意图，交给搜图插件处理: {reply[:60]}")
        return True
    return


async def _mentioned_names(event, group_id, raw: str) -> list[str]:
    """提取本条消息里艾特了谁（不含机器人自己），返回昵称列表，供意图识别区分目标。"""
    qqs = [
        m.group(1)
        for m in re.finditer(r"\[(?:CQ:)?at[:,]qq=(\d+)", raw or "")
        if m.group(1) != str(config.BOT_QQ)
    ]
    if not qqs:
        return []
    try:
        bot = bot_for(event)
    except Exception as e:
        logger.warning(f"[AI聊天] 获取艾特对象昵称失败: {e}")
        return []

    async def _name(qq: str) -> str:
        try:
            info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(qq))
            name = (info.get("card") or info.get("nickname") or "").strip()
        except Exception:
            name = ""
        return name or f"QQ{qq}"

    return await asyncio.gather(*(_name(qq) for qq in qqs))


def _fact_values(items):
    """“A的设定：v”→ 裸值 v；格式不对的原样保留。"""
    out = []
    for item in items:
        _ft, fv = learning._fact_target(item)
        out.append(fv if fv else item)
    return out


def _alias_names(items):
    """“A 的外号: n”→ 裸名 n；格式不对的原样保留。"""
    out = []
    for item in items:
        m = re.match(r"^.*?的外号\s*[:：]\s*(.*)$", item)
        out.append(((m.group(1) if m else item) or "").strip() or item)
    return out


def _split_deleted(deleted):
    """删除回执拆成（设定值列表, 外号列表）。"""
    vals, als = [], []
    for d in deleted:
        _ft, fv = learning._fact_target(d)
        if _ft:
            vals.append(fv or d)
            continue
        m = re.match(r"^.*?的外号\s*[:：]\s*(.*)$", d)
        als.append(((m.group(1) if m else d) or "").strip() or d)
    return vals, als


# 词条意图的“粗筛”关键词：一句话里一个都没有时，不可能是词条/设定/外号的增删查，
# 直接跳过意图分类（省一次模型调用和 1~3 秒回复延迟）。这只是必要条件，
# 是否真的要增删查、目标和内容是什么，仍然完全由 memory_intent 的模型判断。
_MEMORY_HINTS = (
    "词条", "设定", "外号", "绰号", "昵称", "称呼", "标签", "档案", "人设", "资料",
    "记住", "记下", "记一下", "记着", "记得", "忘掉", "忘记", "忘了", "删", "清空", "清除",
    "去掉", "加个", "添加", "加上", "叫他", "叫她", "喊他", "喊她", "叫我", "喊我", "是谁", "查",
)


def _recent_context(group_id, n: int = 8) -> str:
    """意图识别用的最近几条聊天（她自己说的标「机器人」），让「把设定去掉」这种指代能对上。"""
    rows = []
    for m in (memory.get_history(group_id) or [])[-n:]:
        c = str(m.get("content") or "").replace("\n", " ")[:120]
        rows.append(("机器人：" + c) if m.get("role") == "assistant" else c)
    return "\n".join(rows)


async def _handle_memory_intent(event, group_id, user_id, nickname, text, has_at_bot, raw: str = ""):
    """AI 理解自然语言里的词条/设定/外号增删查意图，命中就执行并回执。"""
    if not has_at_bot or not (text or "").strip():
        return False
    if not any(h in text for h in _MEMORY_HINTS):
        return False
    if humanize.skip_if_limited(group_id):
        return False
    mentioned = await _mentioned_names(event, group_id, raw) if raw else []
    intent = await memory_intent.detect_memory_action(group_id, nickname, text, mentioned=mentioned,
                                                      context=_recent_context(group_id))
    if not intent:
        return False
    action = intent["action"]
    target = intent["target"]
    value = intent.get("value", "")
    if action == "add":
        changed, fact = learning.add_setting(group_id, target, value)
        if not fact:
            return False
        reply_text = f"记住了，{fact}" if changed else f"这条早就记过啦，{fact}"
    elif action == "delete":
        count, deleted = learning.delete_settings(group_id, target, value)
        if not count:  # 「删除ntr苦主，铸币」：只说了内容没说是谁的，按内容找
            count, deleted = learning.delete_by_value(group_id, f"{target}，{value}" if value else target)
            if count:
                target = learning._fact_target(deleted[0])[0] or target
        disp = learning.resolve_target(group_id, target, nickname) or target
        if not count:
            reply_text = f"嗯？好像没有{disp}的设定呢"
        else:
            vals, als = _split_deleted(deleted)
            segs = []
            if vals:
                segs.append(f"{disp}的设定：" + "；".join(vals))
            if als:
                segs.append(f"{disp}的外号：" + "；".join(als))
            reply_text = "好，删掉啦：" + "，".join(segs) + (f" 共{count}条" if count > 3 else "")
    elif action == "query":
        facts, aliases = learning.query_settings(group_id, target)
        disp = learning.resolve_target(group_id, target, nickname) or target
        if facts or aliases:
            segs = []
            if facts:
                segs.append(f"{disp}的设定为：" + "；".join(_fact_values(facts)))
            if aliases:
                segs.append(f"{disp}的外号为：" + "；".join(_alias_names(aliases)))
            reply_text = "，".join(segs)
        else:
            reply_text = f"{disp}暂时还没有设定"
    else:
        return False
    try:
        await _chat.send(Message([
            MessageSegment.at(user_id),
            MessageSegment.text(f" {reply_text}"),
        ]))
        logger.info(f"[AI聊天] 群{group_id} 记忆意图完成：{action} target={target} value={(value or '')[:40]}")
    except Exception as e:
        logger.warning(f"[AI聊天] 记忆意图回执发送失败: {e}")
    # 回执也记进聊天记录：她（包括二代仿真）之后知道这是自己刚做的事
    memory.add_message(group_id, "assistant", f"@{nickname} {reply_text}")
    mid = str(getattr(event, "message_id", "") or "")
    if mid:
        memory.mark_replied(group_id, [mid], reply_text)
    return reply_text
