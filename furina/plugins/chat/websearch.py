"""聊天里的联网搜索：她不知道就自己查，查完用自己的话说（设置 → 联网搜索 可整体关掉，按群在「功能权限」里开关）。

两种触发：
- 群友明说「搜一下 / 查一下 / 百度一下 XXX」：先搜好再回，只调用一次模型（多约 300~600 token）；
- 她自己觉得不知道、拿不准、要最新消息：整条回复只写 <<SEARCH:关键词>>，程序替她搜，
  再让她看着结果重新回一次（多一次模型调用；系统提示前缀相同，DeepSeek 等有缓存的接口这部分很便宜）。
默认只在被艾特时能搜；随机接话要搜得在设置里打开。搜不到就照实说，不编。
"""
from __future__ import annotations

import random
import re

from nonebot import logger

from . import config
from ...core import permissions, usage as daily_limits
from ...services import websearch as ws

FEATURE = "websearch"

_MARK_RE = re.compile(
    r"<<\s*(?:SEARCH|search|Search|搜索|搜)\s*[:：]\s*([^<>\n]{1,60}?)\s*>>"
    r"|[\[【]\s*(?:SEARCH|search|Search|搜索)\s*[:：]\s*([^\]】\n]{1,60}?)\s*[\]】]"
)
# 群友明说要搜：搜一下XX / 帮我查查XX / 百度一下XX / 你上网查下XX
_ASK_RE = re.compile(
    r"(?:^|[，,。\s]|帮我|给我|你|去|快)(?:上网|联网)?(?:搜|查|百度|谷歌|必应|google)"
    r"(?:一下|一搜|一查|查|搜|下|看看|看)\s*[:：，,]?\s*(?P<q>[^?？!！。]{2,40})",
    re.I,
)
_FILLER_RE = re.compile(r"^(?:看|一下|下|吧|呗|呀|啊|嘛|，|,|\s)+|(?:吧|呗|呀|啊|嘛|好吗|可以吗|行吗|谢谢|[~～])+$")

NOTE = ("【不知道可以先偷偷看一眼】遇到你不知道、拿不准、或者要最新消息的事实问题（新闻、比赛、游戏版本/卡池、价格、天气、"
        "某个人/作品/梗到底是什么……），别瞎编，也别一句「不知道」打发：这次整条回复只写 <<SEARCH:关键词>>"
        "（关键词写成「主语 + 要问的点」，比如 <<SEARCH:绝区零 最新版本>>，别加日期），程序会悄悄把资料给你，"
        "你看完像本来就知道一样回。闲聊、情绪、玩梗、你本来就清楚的事都不用看。")

# 去看资料前偶尔先说一句（不提「搜」，像真人想一下 / 翻一下手机）
WAIT_LINES = ("等等", "唔…我想想", "让我看看", "嗯…稍等", "诶我记得是…等下", "我瞅一眼")
# 回复里露出「我搜了」的痕迹（对方没叫她搜的时候要改掉）
LEAK_RE = re.compile(r"搜(?:出来|到|了|索|一下|了一下)|查(?:到|了一下|了下)|搜索结果|网上(?:说|写)|资料(?:显示|上说)|根据.{0,6}(?:结果|资料)")
LEAK_FIX = ("【改一下】刚才那句露出了你在「搜/查资料」的痕迹，像个机器人。重新回一次：把信息当成你本来就知道的，"
            "不提搜、查、搜索结果、网上、资料；不确定就说不太确定。直接给出改好的回复。")


def enabled(group_id, forced: bool) -> bool:
    """这一轮能不能搜：总开关 + 群权限 + （随机接话要另开）。"""
    try:
        if not config.WEB_SEARCH:
            return False
        if not forced and not config.WEB_SEARCH_RANDOM:
            return False
        return permissions.allowed(str(group_id), FEATURE, True)
    except Exception:  # noqa: BLE001
        return False


def extract(reply: str) -> tuple[str, str | None]:
    """剥掉 <<SEARCH:...>> 标记，返回 (剩下的文字, 关键词或 None)。"""
    if not reply:
        return reply, None
    m = _MARK_RE.search(reply)
    if not m:
        return reply, None
    query = (m.group(1) or m.group(2) or "").strip()
    cleaned = _MARK_RE.sub("", reply).strip()
    return cleaned, (query or None)


def explicit_query(text: str) -> str | None:
    """群友明说「搜一下 XX」时取出 XX；不是在叫她搜就返回 None。"""
    t = re.sub(r"\[CQ:[^\]]*\]|@\S+", " ", text or "").strip()
    m = _ASK_RE.search(t)
    if not m:
        return None
    q = _FILLER_RE.sub("", m.group("q").strip()).strip()
    if len(q) < 2 or re.fullmatch(r"(?:图|这张图|这个图|一下|下|看)", q):
        return None  # 「搜一下这张图」是以图搜图，交给搜图插件
    return q[:40]


def _consume(group_id) -> bool:
    cap = int(config.WEB_SEARCH_DAILY_MAX or 0)
    try:
        if not daily_limits.consume(FEATURE, group_id, "", cap):
            logger.info(f"[联网搜索] 群{group_id} 今天已搜满 {cap} 次")
            return False
        daily_limits.track(FEATURE, group_id)
    except Exception:  # noqa: BLE001  计数出错不影响搜索
        pass
    return True


async def lookup(query: str, group_id) -> str | None:
    """搜一次并返回给模型看的结果说明；今天额度用完返回 None。"""
    if not _consume(group_id):
        return None
    results = await ws.search(query, n=config.WEB_SEARCH_RESULTS)
    logger.info(f"[联网搜索] 群{group_id} 搜「{query}」→ {len(results)} 条")
    return ws.format_results(query, results)


def leaks(reply: str, user_text: str = "") -> bool:
    """回复里露了「我搜了 / 搜索结果」的痕迹，而对方并没有叫她去搜。"""
    if not reply or re.search(r"搜|查|百度|谷歌|google", user_text or "", re.I):
        return False
    return bool(LEAK_RE.search(reply))


def wait_line(rng: random.Random | None = None) -> str | None:
    """要搜的时候先随口说一句「等下我查查」（按设置的概率），更像真人。"""
    r = rng or random
    if r.random() < max(0.0, float(config.WEB_SEARCH_SAY_WAIT or 0)):
        return r.choice(WAIT_LINES)
    return None


def add_to_system(messages: list, note: str) -> None:
    """把搜索结果接在系统提示后面（不改 user 消息结构，带图片的消息也不受影响）。"""
    if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
        messages[0] = {**messages[0], "content": f"{messages[0]['content']}\n\n{note}"}
    elif messages and messages[0].get("role") == "system":
        messages.insert(1, {"role": "system", "content": note})
    else:
        messages.insert(0, {"role": "system", "content": note})
