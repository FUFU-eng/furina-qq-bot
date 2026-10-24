"""说话人近期自述、引用归属判定、双人私聊避让检测、多轮对话脉络与目标消息锚定
（融合 MaiBot / oh-my-qqbot / Qhunliv13/qq-bot 群聊接话工程实践）。"""
from __future__ import annotations

import time
from ...core import roles


def speaker_recap(history: list[dict], nickname: str, exclude: str = "", limit: int = 4) -> str:
    """提取当前说话人自己最近几条发言（不含别人、不含机器人），供联系同说话人上下文或回答"我刚才说了什么"。"""
    if not history or not nickname:
        return ""
    prefix = f"{nickname}："
    items: list[str] = []
    skipped_exclude = False
    for m in reversed(history):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = (m.get("content") or "").strip()
        if not c.startswith(prefix):
            continue
        if exclude and c == exclude and not skipped_exclude:
            skipped_exclude = True
            continue
        body = c[len(prefix):].strip()
        if body:
            items.append(body[:100])
        if len(items) >= limit:
            break
    if not items:
        return ""
    items.reverse()
    joined = " / ".join(f"「{x}」" for x in items)
    return (
        f"补充：{nickname} 刚才在本群前几句自己说过：{joined}。"
        f"如果 TA 这一句是在接着刚才自己的话题往下说、或者问你 TA 刚才说了什么，请自然顺着接话；"
        f"如果已经换了新话题，就不必硬提旧话。"
    )


def quote_from_bot(quote, bot_qq: str = "") -> bool:
    """判断被引用的消息是不是机器人自己发的（决定引用是"追问/接机器人的话"还是"顺着对方对别人的吐槽"）。"""
    if quote is None:
        return False
    sid = str(getattr(quote, "sender_id", "") or "").strip()
    bqq = str(bot_qq or "").strip()
    if sid and bqq and sid == bqq:
        return True
    sname = str(getattr(quote, "sender_name", "") or "").strip()
    bot_names = {"芙宁娜", "芙芙", "白芙", "黑芙", roles.display_name()}
    return any(n and n in sname for n in bot_names)


def _speaker_of(entry: dict) -> str:
    if not isinstance(entry, dict) or entry.get("role") != "user":
        return ""
    c = (entry.get("content") or "").strip()
    if "：" in c:
        return c.split("：", 1)[0].strip()
    return ""


def detect_two_others_talking(
    history: list[dict],
    window_seconds: float = 90.0,
    count: int = 4,
) -> tuple[bool, list[str]]:
    """检测最近群聊是否正处于「另外两名群友一问一答交替私聊」状态（借鉴 Qhunliv13/qq-bot _recent_others_talking）。
    仅当最近 count 条非机器人消息严格由 2 名不同群友交替发出（A->B->A->B）且在时间窗口内时返回 True，
    避免随机接话打断群友二人的正经对话，同时保留多人吹水时的自然插话能力。
    """
    if not history:
        return False, []
    now = time.time()
    recent_users: list[str] = []
    for m in reversed(history):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        ts = float(m.get("ts") or 0)
        if ts and (now - ts) > window_seconds:
            break
        spk = _speaker_of(m)
        if not spk:
            continue
        recent_users.append(spk)
        if len(recent_users) >= count:
            break
    if len(recent_users) < count:
        return False, []
    recent_users.reverse()
    uniq = list(dict.fromkeys(recent_users))
    if len(uniq) != 2:
        return False, []
    for i in range(1, len(recent_users)):
        if recent_users[i] == recent_users[i - 1]:
            return False, []
    return True, uniq


def is_speaker_in_active_bot_thread(
    history: list[dict],
    nickname: str,
    window_seconds: float = 90.0,
) -> bool:
    """判断当前说话人是否刚刚与机器人进行过对话（机器人在最近几条消息内刚回过该群友，且未超时）。
    用于支持群友不带 @ 的自然多轮追问接话。
    """
    if not history or not nickname or len(history) < 2:
        return False
    now = time.time()
    prefix = f"{nickname}："
    # 排除刚刚入队的当前这条 user 消息
    prior = history[:-1] if (history[-1].get("role") == "user" and (history[-1].get("content") or "").startswith(prefix)) else history
    tail = prior[-6:]
    saw_bot = False
    for m in reversed(tail):
        ts = float(m.get("ts") or 0)
        if ts and (now - ts) > window_seconds:
            break
        role = m.get("role")
        if role == "assistant":
            saw_bot = True
        elif role == "user" and (m.get("content") or "").startswith(prefix):
            if saw_bot:
                return True
    return False


def dialogue_thread(
    history: list[dict],
    nickname: str,
    exclude: str = "",
    limit: int = 6,
    window_seconds: float = 600.0,
) -> str:
    """提取当前说话人与机器人之间的近期连续对话脉络（借鉴 MaiBot / Qhunliv13 线程上下文重建）。
    解决此前模型把上一轮对话当成无关背景丢弃导致的「失忆/前言不搭后语」问题。
    """
    if not history or not nickname:
        return ""
    now = time.time()
    prefix = f"{nickname}："
    me_name = roles.display_name()
    lines: list[str] = []
    skipped_exclude = False
    last_was_speaker = False

    # 正序扫描最近 16 条，挑出「当前说话人的发言」以及「紧随其后的机器人回复」
    recent_slice = history[-16:]
    for idx, m in enumerate(recent_slice):
        if not isinstance(m, dict):
            continue
        ts = float(m.get("ts") or 0)
        if ts and (now - ts) > window_seconds:
            continue
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role == "user" and content.startswith(prefix):
            if exclude and content == exclude and idx == len(recent_slice) - 1 and not skipped_exclude:
                skipped_exclude = True
                continue
            lines.append(f"- {content[:100]}")
            last_was_speaker = True
        elif role == "assistant" and last_was_speaker and content:
            lines.append(f"- 你（{me_name}）当时回复：{content[:100]}")
            last_was_speaker = False
        else:
            last_was_speaker = False

    if not lines:
        return ""
    lines = lines[-limit:]
    return (
        f"【你与 {nickname} 刚才的对话脉络（若对方这句是在追问、反驳或顺着往下聊，请紧扣脉络自然接话，不要失忆）】\n"
        + "\n".join(lines)
    )


def build_target_anchor(
    nickname: str,
    current_text: str,
    quote=None,
    bot_qq: str = "",
    recent_replies: list[str] | None = None,
) -> str:
    """构造 MaiBot / oh-my-qqbot 风格的【目标消息锚定与接话判断线索】。
    1. 明确锁定本次唯一回复目标是 nickname 的当前消息；
    2. 明确人称代词归属（「我/我的」= 说话人，「你」= 机器人）；
    3. 区分「同人同话题延续」与「无关旁人闲聊隔离」；
    4. 防复读提醒。
    """
    me_name = roles.display_name()
    target_txt = (current_text or "").strip() or "[发了一张图/表情]"
    lines = [
        f"【当前回复目标与接话准则（最高优先级）】",
        f"1. 目标锁定：你现在唯一要回复的是「{nickname}」刚发的这句：「{target_txt[:140]}」。",
        f"2. 人称锚定：对方话里的「我/我的/咱」指「{nickname}」本人；「你/{me_name}」指你自己，绝不能把主客体搞混。",
        f"3. 话题延续 vs 噪音隔离：如果「{nickname}」是在延续 TA 自己刚才的话、或者追问/接你上一句说的话，必须顺着话题自然往下接，给出有内容、接得住梗的回答；但上方其他无关群友的旁人闲聊只作背景，不要把别人说的话张冠李戴算到「{nickname}」头上，也不要从无关历史里生搬硬套词句。",
    ]
    if quote is not None and getattr(quote, "text", ""):
        qtxt = str(quote.text).replace("[图片]", "[一张图]")[:100]
        if quote_from_bot(quote, bot_qq):
            lines.append(
                f"4. 引用线索：「{nickname}」正引用你之前说的「{qtxt}」进行追问或回应，请直接顺着你上一句的意思接下去，保持立场一致。"
            )
        else:
            who = getattr(quote, "sender_name", "") or "群友"
            lines.append(
                f"4. 引用线索：「{nickname}」引用了 {who} 的消息「{qtxt}」发表评论，请围绕「{nickname}」对这条消息的看法接话。"
            )
    if recent_replies:
        sample = " / ".join(f"「{r[:36]}」" for r in recent_replies[-2:] if r)
        if sample:
            lines.append(f"5. 防复读约束：你最近刚说过 {sample}，这句绝对不要重复相同的原话、比喻或句式开头。")
    return "\n".join(lines)
