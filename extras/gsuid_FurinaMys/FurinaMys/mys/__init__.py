"""芙宁娜米游社助手：所有指令都要 @机器人。

@芙芙 绑定账号 / 我的账号 / 指令帮助 / 更新展柜 / 绑定设备 / 解除绑定
@芙芙 <前缀>每日 | <前缀>深渊 | <前缀>今日素材 | <前缀><角色>攻略 | <前缀>更新展柜 | <前缀><角色>
前缀：原神 ys / 星铁 bt 崩铁 / 绝区零 zzz / 崩坏3 bbb 崩三
"""
from __future__ import annotations

import re
import json
import asyncio
from typing import Dict, List, Tuple, Optional

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event, Message
from gsuid_core.segment import MessageSegment
from gsuid_core.aps import scheduler
from gsuid_core.server import on_core_start

from . import bind as _bind
from . import verify as _verify
from . import botswitch as _botswitch
from .bind import (
    UNBOUND_TEXT, bound_uid, has_cookie, has_stoken, needs_stoken, start_bind, bound_roles, sender_name,
    any_binding, heal_device, bind_device, unbind_all, device_bound, refresh_cookie, migrate_shared_devices,
)
from .extras import trim_resin_card, mys_guide, clean_name
from .cards import GAME_META, GAME_ORDER, AccountCard, render_help_card, render_account_card
from .bridge import Output, find_module, call_upstream
from .config import cfg, bot_name

sv_game = SV("芙芙米游社查询", priority=0)
sv_account = SV("芙芙米游社账号", priority=0)

GAME_PREFIX: List[Tuple[str, str]] = sorted(
    [("原神", "gs"), ("ys", "gs"), ("星铁", "sr"), ("崩铁", "sr"), ("bt", "sr"),
     ("绝区零", "zzz"), ("zzz", "zzz"), ("崩坏3", "bbb"), ("崩三", "bbb"), ("bbb", "bbb")],
    key=lambda x: -len(x[0]),
)
GAME_LABEL = {"gs": "原神", "sr": "星铁", "zzz": "绝区零", "bbb": "崩坏3"}

DAILY_WORDS = {"每日", "体力", "便笺", "便签", "实时便笺", "mr", "树脂", "开拓力", "电量"}
ABYSS_WORDS = {"深渊", "战绩", "深渊战绩", "全部深渊", "终局", "高难"}
MATERIAL_WORDS = {"今日素材", "今日材料", "每日素材", "每日材料", "素材", "材料"}
REFRESH_WORDS = {"更新展柜", "刷新展柜", "更新面板", "刷新面板", "强制刷新", "刷新", "更新"}
OVERVIEW_WORDS = {"查询", "面板", "总览", "女武神", "我的女武神"}

# (子模块, 函数, 命令字)
DAILY = {
    "gs": ("genshinuid_resin", "send_daily_info_pic", "每日"),
    "sr": ("starrailuid_stamina", "send_daily_info_pic", "每日"),
    "zzz": ("zzzerouid_stamina", "send_daily_info_pic", "每日"),
    "bbb": ("bbb_data", "send_note_info", "便笺"),
}
PANEL = {
    "gs": ("genshinuid_enka", "send_char_info", "查询"),
    "sr": ("starrailuid_charinfo", "send_char_info", "查询"),
    "zzz": ("zzzerouid_char_detail", "send_char_detail_msg", "查询"),
    "bbb": ("bbb_data", "send_index_info", "查询"),
}
REFRESH = {
    "gs": ("genshinuid_enka", "send_card_info", "强制刷新"),
    "sr": ("starrailuid_charinfo", "send_card_info", "强制刷新"),
    "zzz": ("zzzerouid_char_detail", "send_refresh_char_detail_msg", "刷新面板"),
    "bbb": ("bbb_data", "send_refresh_panel", "刷新面板"),
}
GUIDE = {
    "gs": ("genshinuid_guide", "send_guide_pic", "攻略"),
    "sr": ("starrailuid_wiki", "send_role_guide_pic", "角色攻略"),
    "zzz": ("zzzerouid_wiki", "send_role_guide_pic", "攻略"),
    "bbb": ("bbb_elysian", "send_elysian_strategy", "乐土攻略"),
}
MATERIAL = ("genshinuid_dailycost", "send_collection_info", "今日素材")
# 米游社战绩页上能看到的终局玩法，全套依次出图：(子模块, 函数, 命令字, 标题)
ABYSS = {
    "gs": [
        ("genshinuid_abyss", "send_abyss_info", "深渊", "深境螺旋"),
        ("genshinuid_poetry_abyss", "send_poetry_abyss_info", "幻想真境剧诗", "幻想真境剧诗"),
        ("genshinuid_hard_challenge", "send_hard_abyss_info", "幽境危战", "幽境危战"),
    ],
    "sr": [
        ("starrailuid_abyss", "send_srabyss_info", "深渊", "忘却之庭"),
        ("starrailuid_abyss_story", "send_srabyss_info", "虚构叙事", "虚构叙事"),
        ("starrailuid_abyss_boss", "send_srabyss_info", "末日幻影", "末日幻影"),
        ("starrailuid_abyss_peak", "send_srabyss_info", "异相仲裁", "异相仲裁"),
        ("starrailuid_grid_fight", "send_srabyss_info", "货币战争", "货币战争"),
    ],
    "zzz": [
        ("zzzerouid_challenge", "send_abyss_info", "深渊", "式舆防卫战"),
        ("zzzerouid_mem", "send_mem_info", "危局强袭战", "危局强袭战"),
        ("zzzerouid_void", "send_mem_info", "临界推演", "临界推演"),
        ("zzzerouid_abyss", "send_abyss_info", "零号空洞", "零号空洞"),
    ],
    "bbb": [
        ("bbb_data", "send_abyss_info", "深渊", "超弦空间 / 量子流形"),
        ("bbb_data", "send_battlefield_info", "战场", "记忆战场"),
        ("bbb_data", "send_godwar_info", "往世乐土", "往世乐土"),
    ],
}
# 这些玩法没打 / 没数据时不在结果里列「暂无数据」那一行（群友不需要看到）
ABYSS_QUIET = {"临界推演"}
# 上游「没数据 / 没绑定」类提示：命中后换成芙芙自己的说法
UPSTREAM_BIND_HINTS = ("绑定uid", "绑定UID", "绑定Cookie", "绑定CK", "扫码登", "未绑定", "没有绑定", "暂未绑定", "绑定ck")


# 「@芙芙 绝区零开播了，你也去看看直播」这种是聊天，不是游戏指令：前缀后面像一句话（有标点/空格、
# 语气词结尾、带人称或闲聊词、太长）就不当指令。这里和 chat 插件
# （D:/furina-qq-bot/furina/services/gscore.py）各有一份同样的判断，改一处要同步另一处。
_CHAT_WORDS = re.compile(
    r"[，。！？,.!?、~～…\s]|你|我|他|她|它|大家|开播|直播|启动|好玩|怎么|什么|为什么|为啥|咋|没有|不是|真的|一起|"
    r"新版本|版本|更新了|活动|卡池|池子|出了|上线|玩家|剧情|好看|厉害|喜欢|讨厌")
_PARTICLE_END = re.compile(r"[了吗呢吧啊呀嘛哦么啦哈耶诶咯噢嗷喔]$")


def looks_like_chat(rest: str) -> bool:
    """游戏前缀后面那段像闲聊（不是「每日」「芙宁娜攻略」这类指令）。"""
    rest = (rest or "").strip()
    if not rest:
        return False
    return len(rest) > 16 or bool(_CHAT_WORDS.search(rest)) or bool(_PARTICLE_END.search(rest))


def split_game(raw: str) -> Tuple[Optional[str], str]:
    text = raw.strip().lstrip("#/").strip()
    for prefix, game in GAME_PREFIX:
        if text.lower().startswith(prefix.lower()):
            return game, text[len(prefix):].strip()
    return None, text


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strip_panel_words(name: str) -> str:
    name = re.sub(r"^(查询|角色面板|查看)", "", name)
    name = re.sub(r"(的?面板|的?练度)$", "", name)
    return name.strip()


def _core_name(name: str) -> str:
    """去掉上游支持的修饰（六命、换武器……），只留角色名用于校验。"""
    name = re.sub(r"^([零一二三四五六0-6]\s*[命魂影]|满命|满魂)", "", name)
    name = re.sub(r"(换|拿|带).*$", "", name)
    return name.strip()


async def valid_char(game: str, name: str) -> bool:
    name = _core_name(name)
    if not name:
        return False
    try:
        if game == "gs":
            mod = find_module("gs", "name_covert")
            if mod is None:
                return True
            result = await mod.alias_to_char_name(name)
            return result in getattr(mod, "alias_data", {})
        if game == "sr":
            mod = find_module("sr", "name_covert")
            return True if mod is None else bool(await mod.alias_to_char_id(name))
        if game == "zzz":
            mod = find_module("zzz", "name_convert")
            return True if mod is None else bool(mod.char_name_to_char_id(name))
        if game == "bbb":
            mod = find_module("bbb", "name_convert")
            return True if mod is None else bool(mod.alias_to_char_name(name))
    except Exception as exc:
        logger.warning(f"[FurinaMys] 角色名校验异常 {game} {name}: {exc}")
        return True
    return False


RISK_TEXT = "米游社弹了人机验证，芙芙这次没查到"
# 设备重新登记后还被拦：这是米游社给「芙芙这台电脑 + 这个账号」挂的验证，群友手机上不会弹，
# 由主人在本机验证页拖一次滑块解除（见 verify.py），恢复后芙芙会在群里通知。
RISK_FIX = "过一会儿再试试吧～"
# 按早柚核心官方文档的做法：让群友把「自己常用手机的设备信息」绑定给芙芙（mys设备登录 / 绑定设备），
# 之后查询带的是他自己手机的设备身份，米游社就很少再弹验证。已经绑过的，再弹就去米游社 App 里过一次。
RISK_FIX_REAL = "打开米游社 App →「我的」→「我的角色」点进战绩页，弹出验证就拖一下，然后再查一次～"


def risk_fix_bind() -> str:
    return f"想以后少弹验证：@{bot_name()} 绑定设备（按提示做一次就行，建议私聊{bot_name()}）"


async def risk_fix(ev: Event, game: str) -> str:
    """被米游社验证拦住时给群友的下一步：没绑手机设备 → 引导绑定；绑过 → 去米游社 App 过一次验证。"""
    try:
        uid = await bound_uid(ev, game)
        if uid and await device_bound(uid, game):
            return RISK_FIX_REAL
    except Exception as exc:
        logger.warning(f"[FurinaMys] 判断设备绑定状态失败: {exc}")
    return risk_fix_bind()


RISK_WORDS = ("验证码", "1034", "解除风险", "10035", "5003", "10041", "风控")


def is_risky(out: Output) -> bool:
    return (not out.has_image) and any(w in t for t in out.texts for w in RISK_WORDS)


async def call_safe(bot: Bot, ev: Event, game: str, sub: str, func: str, command: str, text: str = "") -> Output:
    """调用上游；遇到验证码时按上游做法自动重新做一次设备登录，然后重试一次。"""
    out = await call_upstream(bot, ev, game, sub, func, command, text=text)
    if is_risky(out):
        uid = await bound_uid(ev, game)
        if uid and await heal_device(uid, game):
            await asyncio.sleep(1.5)
            out = await call_upstream(bot, ev, game, sub, func, command, text=text)
        if uid and is_risky(out):
            await _verify.report(bot, ev, game, uid)
        if is_risky(out):
            out.hint = await risk_fix(ev, game)  # type: ignore[attr-defined]
    return out


def friendly(text: str) -> str:
    """把上游的提示换成群友看得懂的话。"""
    text = (text or "").strip()
    if not text or "UID存在错误" in text:
        return ""
    if any(w in text for w in RISK_WORDS):
        return RISK_TEXT
    if "请求错误" in text or "联系Bot主人" in text:
        return "米游社接口暂时出错了，稍后再试～"
    if any(h in text for h in UPSTREAM_BIND_HINTS):
        return f"{UNBOUND_TEXT}，@{bot_name()} 绑定账号 扫个码就好～"
    text = re.sub(r"^\[[^\]]{1,8}\]\s*", "", text)
    return text


async def _send_output(bot: Bot, out: Output, fallback: str) -> None:
    if out.empty:
        await bot.send(fallback)
        return
    if not out.has_image:
        logger.info(f"[FurinaMys] 上游无图输出: {out.text()[:200]}")
        lines = []
        for t in out.texts:
            f = friendly(t)
            if f and f not in lines:
                lines.append(f)
        if RISK_TEXT in lines:
            lines.append(getattr(out, "hint", None) or RISK_FIX)
        await bot.send("\n".join(lines) or fallback)
        return
    await bot.send(out.segments)


async def _need_account(
    bot: Bot, ev: Event, game: str, need_ck: bool, after=None, need_sk: bool = False
) -> Optional[str]:
    """返回该游戏当前 UID；没有时按情况引导绑定，并返回 None。"""
    uid = await bound_uid(ev, game)
    if uid and (not need_ck or await has_cookie(uid, game)):
        if not (need_sk and needs_stoken(uid, game)) or await has_stoken(uid, game):
            return uid
        await start_bind(bot, ev, after=after, lead="你的米游社登录信息不完整（缺少 SToken，查体力要用），重新扫个码就能补上")
        return None
    if uid or await any_binding(ev):
        if not uid:
            await bot.send(
                f"你的米游社通行证下还没有{GAME_LABEL[game]}账号哦～如果刚建了角色，可以 @{bot_name()} 绑定账号 重新扫码同步"
            )
            return None
        # 绑过 UID 但 Cookie 失效：SToken 还在的话先自动换一个新 Cookie，换不了才让人重新扫码
        if await has_stoken(uid, game) and await refresh_cookie(ev) and await has_cookie(uid, game):
            if not (need_sk and needs_stoken(uid, game)) or await has_stoken(uid, game):
                return uid
        await start_bind(bot, ev, after=after, lead="你的米游社登录已经失效啦，重新扫个码就好")
        return None
    await start_bind(bot, ev, unbound=True, after=after)
    return None


# ---------------------------------------------------------------- 具体功能
async def do_daily(bot: Bot, ev: Event, game: str):
    async def run():
        out = await call_safe(bot, ev, game, *DAILY[game])
        if game == "gs":
            out = trim_resin_card(out, await bound_uid(ev, game))
        await _send_output(bot, out, f"{GAME_LABEL[game]}的实时便笺暂时拿不到，过一会儿再试试吧～")

    if await _need_account(bot, ev, game, True, after=run, need_sk=True):
        await run()


async def do_abyss(bot: Bot, ev: Event, game: str):
    async def run():
        segments: List[Message] = []
        misses: List[str] = []
        for sub, func, command, title in ABYSS[game]:
            out = await call_safe(bot, ev, game, sub, func, command)
            if out.has_image:
                segments.append(MessageSegment.text(f"【{title}】"))
                segments.extend(out.images)
            elif title not in ABYSS_QUIET:
                reason = friendly(out.texts[0]).replace("\n", " ")[:46] if out.texts else "暂无记录"
                misses.append(f"{title}：{reason}")
            # 连续请求太快更容易触发米游社风控，每项之间稍微停一下
            await asyncio.sleep(1.5)
        risky = [m.split("：", 1)[0] for m in misses if RISK_TEXT in m]
        if risky:
            misses = [m for m in misses if RISK_TEXT not in m]
            misses.append(f"{'、'.join(risky)}：{RISK_TEXT}")
            misses.append(await risk_fix(ev, game))
        if not segments:
            await bot.send(f"{GAME_LABEL[game]}这期还没有可展示的战绩哦～\n" + "\n".join(misses))
            return
        if misses:
            segments.append(MessageSegment.text("\n".join(["", "以下暂无数据："] + misses)))
        await bot.send(segments)

    if await _need_account(bot, ev, game, True, after=run):
        await bot.send(f"🏯 正在整理{GAME_LABEL[game]}的全套战绩，稍等几秒～")
        await run()


async def do_material(bot: Bot, ev: Event, game: str):
    if game != "gs":
        return await bot.send("今日素材目前只支持原神哦，发「@芙芙 ys今日素材」试试～")

    async def run():
        out = await call_upstream(bot, ev, "gs", *MATERIAL)
        await _send_output(bot, out, "今日素材图还在生成中，稍后再试试吧～")

    if cfg("PublicNeedBind") and not await _need_account(bot, ev, game, False, after=run):
        return
    await run()


def _guide_name(game: str, name: str) -> str:
    """别名转成正式角色名，方便在米游社搜索。"""
    try:
        if game == "zzz":
            mod = find_module("zzz", "name_convert")
            return (mod.alias_to_char_name(name) if mod else name) or name
        if game == "bbb":
            mod = find_module("bbb", "name_convert")
            return (mod.alias_to_char_name(name) if mod else name) or name
    except Exception:
        pass
    return name


async def do_guide(bot: Bot, ev: Event, game: str, name: str):
    name = name.strip()
    if not name:
        return await bot.send(f"要看谁的攻略呀？例如「@芙芙 {GAME_META[game][4]}{'人律' if game == 'bbb' else '芙宁娜' if game == 'gs' else '黄泉' if game == 'sr' else '星见雅'}攻略」")
    if game != "bbb" and not await valid_char(game, name):
        return await bot.send(f"{GAME_LABEL[game]}里没找到「{name}」这个角色呢，换个名字或常用别名试试～")

    async def run():
        out = await call_upstream(bot, ev, game, *GUIDE[game], text=name)
        if not out.has_image:
            # 上游攻略图库还没收录（多半是刚上线的新角色）：去米游社找社区攻略长图
            found = await mys_guide(game, _guide_name(game, name))
            if not found and clean_name(_guide_name(game, name)) != clean_name(name):
                found = await mys_guide(game, name)  # 正式名搜不到就用 TA 打的名字再搜一次
            if found:
                img, source = found
                return await bot.send([MessageSegment.text(source), MessageSegment.image(img)])
            return await bot.send(f"「{name}」的攻略图暂时还没人整理出来，过几天再来看看吧～")
        await _send_output(bot, out, f"暂时还没有「{name}」的攻略图哦～")

    if cfg("PublicNeedBind") and not await _need_account(bot, ev, game, False, after=run):
        return
    await run()


async def do_refresh(bot: Bot, ev: Event, games: List[str], announce: bool = True):
    """更新展柜：与上游「强制刷新 / 刷新面板」相同，多个游戏合并成一条消息。"""
    targets = [g for g in games if await bound_uid(ev, g)]
    if not targets:
        return await start_bind(bot, ev, unbound=True)
    if announce:
        names = "、".join(GAME_LABEL[g] for g in targets)
        await bot.send(f"🔄 正在更新 {names} 的角色展柜，稍等一会儿～")
    segments: List[Message] = []
    risky = ""
    for game in targets:
        out = await call_safe(bot, ev, game, *REFRESH[game])
        if out.has_image:
            segments.append(MessageSegment.text(f"【{GAME_LABEL[game]}】"))
            segments.extend(out.images)
        else:
            reason = friendly(out.text())[:60] or "这次没有拿到新数据"
            if not risky and reason == RISK_TEXT:
                risky = game
            segments.append(MessageSegment.text(f"【{GAME_LABEL[game]}】{reason}\n"))
        await asyncio.sleep(1)
    if risky:
        segments.append(MessageSegment.text(await risk_fix(ev, risky)))
    await bot.send(segments)


async def silent_refresh(bot: Bot, ev: Event) -> None:
    """绑定成功后在后台把各游戏面板刷一遍（米游社数据源，查面板时不用再等）。"""
    for game in GAME_ORDER:
        if await bound_uid(ev, game):
            await call_safe(bot, ev, game, *REFRESH[game])
            await asyncio.sleep(2)
    logger.info(f"[FurinaMys] {ev.user_id} 绑定后面板已在后台刷新")


_bind.ON_BOUND = silent_refresh


async def do_panel(bot: Bot, ev: Event, game: str, name: str):
    name = _strip_panel_words(name)
    if game == "bbb":
        if name and name not in OVERVIEW_WORDS and not await valid_char("bbb", name):
            return await bot.send(f"崩坏3里没找到「{name}」，可以发「@{bot_name()} bbb查询」看女武神总览～")
    elif not name:
        return await bot.send(f"想看谁的面板呀？例如「@{bot_name()} {GAME_META[game][4]}芙宁娜」，或发「@{bot_name()} 指令帮助」看看全部功能")
    elif not await valid_char(game, name):
        return await bot.send(f"芙芙没听懂「{name}」呢～角色名可以用常用别名，全部功能见「@{bot_name()} 指令帮助」")

    async def run():
        out = await call_safe(bot, ev, game, *PANEL[game], text="" if game == "bbb" else name)
        if game != "bbb" and not out.has_image:
            # 缓存里还没有这个角色：按上游方式先「强制刷新」（有登录时走米游社，全角色）再查
            refreshed = await call_safe(bot, ev, game, *REFRESH[game])
            out = await call_safe(bot, ev, game, *PANEL[game], text=name)
            if not out.has_image:
                if is_risky(refreshed):
                    return await bot.send(f"{RISK_TEXT}，{RISK_FIX}")
                return await bot.send(f"在你的账号里没找到「{name}」的面板数据哦，确认拥有该角色后可以 @{bot_name()} 更新展柜 再试")
        await _send_output(bot, out, "面板暂时拿不到，过一会儿再试试吧～")

    if await _need_account(bot, ev, game, False, after=run):
        await run()


# ---------------------------------------------------------------- 触发器
@sv_game.on_regex(r"^(?P<rest>.*)$", block=True, to_me=True)
async def furina_mys_game(bot: Bot, ev: Event):
    game, rest = split_game(ev.raw_text)
    if game is None:
        return
    if looks_like_chat(rest):
        return  # 「绝区零开播了，你也去看看」是在跟她聊天，不是查询指令：不回，让聊天那边接
    word = _clean(rest)
    if not word:
        return await bot.send(f"想查{GAME_LABEL[game]}的什么呀？发「@{bot_name()} 指令帮助」看看全部功能～")
    if word.lower() in DAILY_WORDS:
        return await do_daily(bot, ev, game)
    if word in ABYSS_WORDS:
        return await do_abyss(bot, ev, game)
    if word in MATERIAL_WORDS:
        return await do_material(bot, ev, game)
    if word in REFRESH_WORDS:
        if not await _need_account(bot, ev, game, False):
            return
        return await do_refresh(bot, ev, [game])
    if word.endswith("攻略"):
        name = word[:-2]
        if game == "bbb":
            name = re.sub(r"乐土$", "", name)
        return await do_guide(bot, ev, game, name)
    return await do_panel(bot, ev, game, word)


@sv_account.on_fullmatch(("绑定账号", "扫码绑定", "绑定米游社", "重新绑定"), block=True, to_me=True, prefix=False)
async def furina_mys_bind(bot: Bot, ev: Event):
    await start_bind(bot, ev)


@sv_account.on_fullmatch(("我的账号", "绑定信息", "账号信息"), block=True, to_me=True, prefix=False)
async def furina_mys_accounts(bot: Bot, ev: Event):
    if not await any_binding(ev):
        return await start_bind(bot, ev, unbound=True)
    roles, mys_id = await bound_roles(ev)
    image = await render_account_card(
        AccountCard(sender_name(ev), ev.user_id, roles, mys_id, "我的米游社账号", f"{sender_name(ev)} 已绑定 {len(roles)} 个游戏账号"),
        bot_name(),
    )
    await bot.send(image)


@sv_account.on_fullmatch(("指令帮助", "帮助", "菜单", "功能", "指令"), block=True, to_me=True, prefix=False)
async def furina_mys_help(bot: Bot, ev: Event):
    await bot.send(await render_help_card(bot_name()))


@sv_account.on_fullmatch(("更新展柜", "刷新展柜", "更新面板", "刷新面板"), block=True, to_me=True, prefix=False)
async def furina_mys_refresh_all(bot: Bot, ev: Event):
    await do_refresh(bot, ev, list(GAME_ORDER))


DEVICE_HELP = (
    "📱 绑定设备（早柚核心官方防验证码方法，只需一次）\n"
    "1. 用平时登录米游社的那台安卓手机，安装 get_device_info（官方文档推荐的开源小工具）：\n"
    "   https://github.com/forchannot/get_device_info （README 里有 APK 下载链接）\n"
    "2. 打开后点按钮，复制那一整串 {...}\n"
    "3. 3 分钟内把复制的内容直接发过来；在群里发的话，绑定成功后记得撤回\n"
    "   更推荐私聊芙芙，一条消息发「绑定设备」+ 复制的内容\n"
    "苹果手机：抓包米游社请求头里的 x-rpc-device_id 和 x-rpc-device_fp，按\n"
    '{"device_id":"...","fp":"..."} 的格式发过来也可以。发「取消」放弃。'
)


def _parse_device(text: str) -> Optional[Dict]:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if "device_fp" in data and "fp" not in data:
        data["fp"] = data["device_fp"]
    if "fp" in data and ("device_id" in data or "deviceId" in data):
        return data
    if all(k in data for k in ("deviceModel", "deviceProduct", "deviceName", "deviceBoard", "oaid", "deviceFingerprint")):
        return data
    return None


@sv_account.on_fullmatch(("绑定设备", "设备绑定", "设备登录"), block=True, to_me=True, prefix=False)
@sv_account.on_prefix(("绑定设备", "设备绑定", "设备登录"), block=True, to_me=True, prefix=False)
async def furina_mys_device(bot: Bot, ev: Event):
    if not await any_binding(ev):
        return await start_bind(bot, ev, unbound=True)
    data = _parse_device(ev.text)
    if data is None:
        try:
            resp = await bot.receive_resp(DEVICE_HELP, timeout=180)
        except asyncio.TimeoutError:
            return await bot.send("设备信息没等到，想绑定的话再 @芙芙 绑定设备 就好～")
        if resp is None or "取消" in (resp.raw_text or ""):
            return await bot.send("好的，绑定设备已取消～")
        data = _parse_device(resp.raw_text)
        if data is None:
            return await bot.send("这串内容芙芙看不懂呢……要完整复制 get_device_info 里的 {...}，再 @芙芙 绑定设备 试一次")
    try:
        count = await bind_device(ev, data)
    except Exception as exc:
        logger.exception(f"[FurinaMys] 绑定设备失败: {exc}")
        return await bot.send("设备信息格式不对或米游社没响应，检查一下再试试～")
    if not count:
        return await bot.send(f"你还没有可用的米游社登录，先 @{bot_name()} 绑定账号 扫码，再来绑定设备～")
    await bot.send("📱 设备绑定成功！之后查询会用你自己的手机设备身份，米游社基本不会再弹验证码啦～\n（群里发过设备信息的话记得撤回）")


@sv_account.on_fullmatch(("解除绑定", "解绑账号", "取消绑定", "删除账号", "解绑"), block=True, to_me=True, prefix=False)
async def furina_mys_unbind(bot: Bot, ev: Event):
    if not await any_binding(ev):
        return await bot.send("你在芙芙这里本来就没有绑定数据哦～")
    try:
        resp = await bot.receive_resp(
            "确定要解除绑定吗？会删除你在芙芙这里的全部游戏账号、米游社登录和每日自动签到。\n30 秒内回复「确认」继续，回复别的就取消。",
            timeout=30,
        )
    except asyncio.TimeoutError:
        return await bot.send("没收到确认，解除绑定已取消～")
    if resp is None or "确认" not in (resp.raw_text or ""):
        return await bot.send("好的，账号还给你留着～")
    removed = await unbind_all(ev)
    names = "、".join(f"{GAME_LABEL[r.game]} {r.uid}" for r in removed) or "无"
    await bot.send(f"已解除绑定：{names}\n米游社登录和自动签到也一起删掉啦。想回来的话随时 @{bot_name()} 绑定账号～")


# ---------------------------------------------------------------- 后台任务
async def _migrate_later():
    await asyncio.sleep(25)  # 等上游插件和数据库都就绪
    try:
        await migrate_shared_devices()
    except Exception as exc:
        logger.warning(f"[FurinaMys] 独立设备迁移失败: {exc}")


@on_core_start
async def furina_mys_startup():
    _verify.register_routes()
    _botswitch.register_routes()
    asyncio.create_task(_migrate_later())


_verify.register_routes()
_botswitch.register_routes()


@scheduler.scheduled_job("cron", hour=9, minute=40)
async def furina_mys_refresh_bad_cookies():
    """每天自动签到（10:00）前：Cookie 被标记失效、但 SToken 还在的账号，自动换新 Cookie。"""
    from gsuid_core.utils.database.models import GsUser
    from gsuid_core.utils.cookie_manager.add_ck import get_ck_by_stoken

    try:
        users = await GsUser.get_all_user(without_error=False)
    except Exception as exc:
        return logger.warning(f"[FurinaMys] 读取账号失败: {exc}")
    done = set()
    for u in users:
        if u.status and u.stoken and (u.bot_id, u.user_id) not in done:
            done.add((u.bot_id, u.user_id))
            try:
                await get_ck_by_stoken(u.bot_id, u.user_id)
                await asyncio.sleep(2)
                await _bind.ensure_own_device(str(u.mys_id or ""))
                await asyncio.sleep(2)
            except Exception as exc:
                logger.warning(f"[FurinaMys] 自动刷新 Cookie 失败: {exc}")
    if done:
        logger.info(f"[FurinaMys] 已为 {len(done)} 位群友自动刷新失效 Cookie")
