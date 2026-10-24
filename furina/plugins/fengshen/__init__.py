"""群聊封神榜插件：每周三和周日评选群里最有梗/最活跃的群友，第1名封中二神称号。

功能：
- 监听群消息，统计每个群友的发言数、被回复数、表情包数、图片数、艾特别人数
- 每周三、周日 21:30 自动评选 Top N，第1名封中二神称号
- 榜单发到群里，历史记录持久化
"""

import json
import os
import threading
import time

from nonebot import get_bot, get_driver, logger, on_message
from ...core import scheduler
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from . import config
from ...core import db, docfile, llm as ai_llm, roles
from ...core.live_groups import cached as _live_cached

_lock = threading.Lock()
_task = None  # scheduler.Job


# ============ 数据持久化 ============

def _load_data() -> dict:
    """各群统计与历史榜单；存 SQLite（files/fengshen_data.json），单行事务写入不会写坏。"""
    try:
        data = docfile.load(config.FENGSHEN_DATA_PATH)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"[封神榜] 读取数据失败: {e}")
    return {}


def _save_data(data: dict):
    try:
        docfile.save(config.FENGSHEN_DATA_PATH, data)
    except Exception as e:
        logger.warning(f"[封神榜] 保存数据失败: {e}")


def _get_group_data(data: dict, group_id: str) -> dict:
    gd = data.setdefault(group_id, {})
    gd.setdefault("stats", {})
    gd.setdefault("history", [])
    gd.setdefault("last_fengshen", "")
    return gd


def _get_user_stat(gd: dict, user_id: str, nickname: str = "") -> dict:
    stats = gd.setdefault("stats", {})
    us = stats.setdefault(user_id, {
        "nickname": nickname or "",
        "messages": 0,
        "replied": 0,
        "stickers": 0,
        "images": 0,
        "at_others": 0,
    })
    if nickname and nickname != us.get("nickname"):
        us["nickname"] = nickname
    return us


# ============ 消息统计 ============

def _is_fengshen_group(event: GroupMessageEvent) -> bool:
    if not config.FENGSHEN_ENABLED:
        return False
    return str(event.group_id) in config.FENGSHEN_GROUPS


_stats_matcher = on_message(rule=Rule(_is_fengshen_group), priority=50, block=False)


@_stats_matcher.handle()
async def _record_message(event: GroupMessageEvent):
    """统计每条群消息的数据。"""
    gid = str(event.group_id)
    uid = str(event.user_id)
    nickname = event.sender.card or event.sender.nickname or uid

    data = _load_data()
    gd = _get_group_data(data, gid)

    with _lock:
        us = _get_user_stat(gd, uid, nickname)
        us["messages"] += 1

        # 检测消息内容
        has_sticker = False
        has_image = False
        has_at = False
        reply_target = None

        for seg in event.message:
            stype = getattr(seg, "type", "")
            sdata = getattr(seg, "data", {}) or {}
            if stype == "image":
                # sub_type=1 是表情包，0 是普通图片
                if str(sdata.get("sub_type", "")) == "1":
                    has_sticker = True
                else:
                    has_image = True
            elif stype == "at":
                has_at = True
            elif stype == "reply":
                reply_target = str(sdata.get("user_id", "") or "")

        # OneBot 的 reply 段只有消息 id，没有 user_id；而且适配器会把 reply 段从 message 里
        # 摘到 event.reply。旧逻辑因此永远统计不到“被回复”（评分权重最高的一项恒为 0）。
        reply = getattr(event, "reply", None)
        if reply is not None and not reply_target:
            sender = getattr(reply, "sender", None)
            reply_target = str(getattr(sender, "user_id", "") or "")

        if has_sticker:
            us["stickers"] += 1
        if has_image:
            us["images"] += 1
        if has_at:
            us["at_others"] += 1
        # 回复机器人自己的消息不计入（否则机器人会以“被回复”高分出现在封神榜上）
        if reply_target and reply_target != uid and reply_target != str(event.self_id):
            target = _get_user_stat(gd, reply_target)
            target["replied"] += 1

        _save_data(data)


# ============ 封神评选 ============

def _collect_recent_digest(group_id: str, days: int = 5) -> str:
    """从每日纪要读取最近几天的群聊纪要，作为评选参考。"""
    try:
        digest_data = db.items("digest")  # 与 AI 聊天每日纪要同一份数据
        parts = []
        for d in sorted(digest_data.keys())[-days:]:
            rec = (digest_data.get(d) or {}).get(group_id) or {}
            text = rec.get("digest") or ""
            if text:
                parts.append(f"[{d}] {text[:800]}")
        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"[封神榜] 读取纪要失败: {e}")
        return ""


def _build_stats_summary(gd: dict, top_n: int = 15) -> str:
    """把统计数据整理成文本，取发言最多的 top_n 人。"""
    stats = gd.get("stats", {})
    if not stats:
        return ""
    # 按综合分排序：发言数*1 + 被回复*3 + 表情包*2 + 图片*1.5 + 艾特*1
    scored = []
    for uid, us in stats.items():
        score = (
            us.get("messages", 0) * 1
            + us.get("replied", 0) * 3
            + us.get("stickers", 0) * 2
            + us.get("images", 0) * 1.5
            + us.get("at_others", 0) * 1
        )
        scored.append((uid, us, score))
    scored.sort(key=lambda x: x[2], reverse=True)
    lines = []
    for uid, us, score in scored[:top_n]:
        lines.append(
            f"- {us.get('nickname', uid)}（QQ:{uid}）：发言{us.get('messages',0)}条，"
            f"被回复{us.get('replied',0)}次，表情包{us.get('stickers',0)}个，"
            f"图片{us.get('images',0)}张，艾特{us.get('at_others',0)}次，综合分{score:.0f}"
        )
    return "\n".join(lines)


async def _generate_ranking(group_id: str) -> list[dict] | None:
    """用 AI 评选封神榜，返回排名列表。"""
    data = _load_data()
    gd = _get_group_data(data, group_id)

    stats_text = _build_stats_summary(gd)
    if not stats_text:
        logger.info(f"[封神榜] 群{group_id} 没有统计数据，跳过")
        return None

    digest_text = _collect_recent_digest(group_id)

    system_prompt = (
        "你是群聊封神榜评委。根据下面的群友活跃度数据和最近群聊纪要，"
        f"评选出最有梗、最有趣、最活跃的 {config.FENGSHEN_TOP_N} 个人，按封神程度从高到低排序。\n"
        "评选标准：不只是看发言多，更要看谁造梗多、谁带动气氛、谁被大家回复讨论得多、谁有趣。\n"
        "第1名要封一个特别中二的神称号（比如「梗界创世神」「暗夜话痨之神」「表情包至高神」"
        "「水群无上至尊」「群聊氛围之神」「潜水归来之神」之类，要够中二够霸气，每次不一样）。\n"
        "输出严格遵循 JSON 数组格式，不要输出其他文字：\n"
        '[{"rank":1,"user_id":"QQ号","nickname":"昵称","title":"中二神称号","reason":"一句话评选理由"},'
        '{"rank":2,...}]\n'
        "reason 要简短幽默，不超过20字。"
    )

    user_content = f"【群友活跃度数据】\n{stats_text}"
    if digest_text:
        user_content += f"\n\n【最近群聊纪要】\n{digest_text[:3000]}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        reply = await ai_llm.chat(messages)
        reply = (reply or "").strip()
        # 提取 JSON 部分
        start = reply.find("[")
        end = reply.rfind("]")
        if start >= 0 and end > start:
            rankings = json.loads(reply[start : end + 1])
            if isinstance(rankings, list) and rankings:
                return rankings[: config.FENGSHEN_TOP_N]
        logger.warning(f"[封神榜] AI 输出解析失败: {reply[:200]}")
        return None
    except Exception as e:
        logger.error(f"[封神榜] 评选失败: {e}")
        return None


def _format_ranking_message(rankings: list[dict]) -> str:
    """把排名格式化成发群消息的文本。"""
    if not rankings:
        return ""
    lines = ["📜 群聊封神榜 📜", ""]
    for r in rankings:
        rank = r.get("rank", 0)
        nick = r.get("nickname", "?")
        title = r.get("title", "")
        reason = r.get("reason", "")
        if rank == 1:
            lines.append(f"👑 第1名：{nick}")
            if title:
                lines.append(f"   🏆 神号：{title}")
            if reason:
                lines.append(f"   📝 {reason}")
        else:
            medal = {2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")
            lines.append(f"{medal} 第{rank}名：{nick}")
            if reason:
                lines.append(f"   📝 {reason}")
        lines.append("")
    lines.append(roles.flavor("—— 本榜单由芙宁娜大人亲自册封 ——", "—— 本榜单由{name}亲自册封 ——"))
    return "\n".join(lines)


async def _do_fengshen():
    """执行一次封神：评选 + 发榜 + 记录历史 + 清空统计。"""
    data = _load_data()
    today = time.strftime("%Y-%m-%d")

    # 实时在群过滤：只往机器人当前确实在的封神群发，换号/退群后不会对着不在的群发
    _live = _live_cached()
    for gid in config.FENGSHEN_GROUPS:
        if _live and str(gid) not in _live:
            logger.info(f"[封神榜] 群{gid} 机器人当前不在该群，跳过本轮")
            continue
        gd = _get_group_data(data, gid)
        # 检查今天是否已经封神过
        if gd.get("last_fengshen") == today:
            logger.info(f"[封神榜] 群{gid} 今天已封神，跳过")
            continue

        logger.info(f"[封神榜] 群{gid} 开始评选...")
        rankings = await _generate_ranking(gid)
        if not rankings:
            logger.warning(f"[封神榜] 群{gid} 评选失败，跳过")
            continue

        # 发榜
        msg_text = _format_ranking_message(rankings)
        try:
            bot = get_bot()
            await bot.send_group_msg(
                group_id=int(gid),
                message=Message([MessageSegment.text(msg_text)]),
            )
            logger.info(f"[封神榜] 群{gid} 发榜成功")
        except Exception as e:
            logger.error(f"[封神榜] 群{gid} 发榜失败: {e}")
            continue

        # 评选/发榜期间（LLM 调用可能几十秒）群消息仍在持续写文件；这里重新读取最新数据再改，
        # 避免用开头读到的旧快照覆盖掉这段时间所有群的统计。
        data = _load_data()
        gd = _get_group_data(data, gid)
        # 记录历史
        history = gd.setdefault("history", [])
        history.append({
            "date": today,
            "rankings": rankings,
        })
        # 只保留最近20期
        if len(history) > 20:
            del history[:-20]
        gd["last_fengshen"] = today
        # 清空本周期统计，开始新周期
        gd["stats"] = {}
        _save_data(data)
        logger.info(f"[封神榜] 群{gid} 封神完成，统计已重置")


# ============ 定时任务 ============

@get_driver().on_startup
async def _start_fengshen():
    global _task
    if config.FENGSHEN_ENABLED and _task is None:
        logger.info(
            f"[封神榜] 后台任务启动：每周{config.FENGSHEN_WEEKDAYS} "
            f"{config.FENGSHEN_HOUR:02d}:{config.FENGSHEN_MINUTE:02d} 封神"
        )
        _task = scheduler.daily(
            "封神榜", _do_fengshen,
            at=lambda: (config.FENGSHEN_HOUR, config.FENGSHEN_MINUTE),
            weekdays=lambda: config.FENGSHEN_WEEKDAYS,
        )


@get_driver().on_shutdown
async def _stop_fengshen():
    global _task
    if _task is not None:
        await scheduler.stop("封神榜")
        _task = None
