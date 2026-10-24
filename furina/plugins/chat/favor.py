"""自然关系成长：跨群统一计分、有效互动冷却、每日总上限，保留历史数据。

模糊情绪不扣分，不因离线衰减；权限与亲疏无关。旧 users/favor 字段与面板兼容。
"""
import copy
from datetime import date, datetime, timedelta, timezone
import math
import threading
import time
from nonebot import logger
from . import config
from ...core.botdata import favor_ns
from ...core.store import NsCache
from .interaction import clean_text, fingerprint, sentiment

_stores: dict[str, NsCache] = {}  # 每个机器人 QQ 一份（见 core.botdata.favor_ns）
CST = timezone(timedelta(hours=8))
DEFAULT_FAVOR = 30
MIN_F, MAX_F = 0, 100
CHAT_GAIN, DAILY_CHAT_CAP = 0.3, 2.4
STREAK_BONUS = 0.1  # 一天只奖励首次有效聊天，已计入聊天上限
PRAISE_GAIN, DAILY_PRAISE_CAP = 0.3, 0.9
INSULT_LOSS, DAILY_INSULT_CAP = 1.5, 4.5
POKE_GAIN, DAILY_POKE_CAP = 0.1, 0.3
DAILY_TOTAL_CAP = 3.0
CHAT_COOLDOWN, PRAISE_COOLDOWN, INSULT_COOLDOWN, POKE_COOLDOWN = 120, 600, 300, 600
_LEVELS = [(0,"戒备"),(15,"初识"),(30,"认识"),(45,"朋友"),(60,"要好"),
           (75,"很喜欢"),(90,"格外偏爱"),(99,"满好感")]
_LEVEL_ORDER = [n for _, n in _LEVELS]
_lock = threading.Lock()
_datas: dict[str, dict] = {}


def _today():return datetime.now(CST).date().isoformat()
def _yesterday():return (datetime.now(CST).date()-timedelta(days=1)).isoformat()
def _days_between(d1, d0):
    try:return (date.fromisoformat(d1)-date.fromisoformat(d0)).days
    except (ValueError, TypeError):return 9999


def _number(value, default=0.0):
    try:
        n=float(value)
        return n if math.isfinite(n) else float(default)
    except (TypeError, ValueError, OverflowError):return float(default)


def is_locked(uid):return str(uid) in config.FAVOR_LOCKED_QQ


def _store(bot_qq=None) -> NsCache:
    ns=favor_ns(bot_qq)
    st=_stores.get(ns)
    if st is None:st=_stores[ns]=NsCache(ns)
    return st


def _load(bot_qq=None):
    """{"users": {qq: 记录}}；每人一行存 SQLite（主号命名空间 favor，其他号 favor@QQ）。"""
    st=_store(bot_qq)
    users=st.load()
    data=_datas.get(st.ns)
    if data is None or data.get("users") is not users:data=_datas[st.ns]={"users":users}
    return data
def _save(uid=None,bot_qq=None):
    _store(bot_qq).save([uid] if uid is not None else None)
def _record(uid,bot_qq=None):
    users=_load(bot_qq)["users"]
    rec=users.get(uid)
    if rec is not None and not isinstance(rec,dict):
        raise ValueError("好感用户记录无效，保留原文件")
    if rec is None:rec={};users[uid]=rec
    defaults={"favor":DEFAULT_FAVOR,"first":_today(),"last":_today(),"day":_today(),
              "day_gain":0.0,"day_praise":0.0,"day_insult":0.0,"day_poke":0.0,
              "chats":0,"praise":0,"insult":0,"name":"","streak":0,
              "last_chat_day":"","level":"","close_since":"","active_days":0,"recent":[]}
    for k,v in defaults.items():rec.setdefault(k,v)
    rec["favor"]=max(MIN_F,min(MAX_F,_number(rec["favor"],DEFAULT_FAVOR)))
    for k in ("day_gain","day_praise","day_insult","day_poke","chats","praise","insult","streak","active_days"):
        rec[k]=max(0,_number(rec[k]))
    rec.setdefault("day_total",sum(rec[k] for k in ("day_gain","day_praise","day_poke")))
    rec["day_total"]=max(0,_number(rec["day_total"]))
    if not isinstance(rec["recent"],list):rec["recent"]=[]
    if rec["day"]!=_today():
        rec["day"]=_today()
        for k in ("day_gain","day_praise","day_insult","day_poke","day_total"):rec[k]=0.0
    return rec


def _add_capped(rec,bucket,cap,amount):
    add=min(max(0,amount),max(0,cap-_number(rec.get(bucket))))
    if bucket!="day_insult":add=min(add,max(0,DAILY_TOTAL_CAP-rec["day_total"]))
    rec[bucket]=round(_number(rec.get(bucket))+add,4)
    if bucket!="day_insult":rec["day_total"]=round(rec["day_total"]+add,4)
    return add


def _check_level_up(uid,rec,nickname):
    lv=level_name(int(round(rec["favor"])));old=rec.get("level")
    rec["level"]=lv
    if old in _LEVEL_ORDER and old!=lv:
        if _LEVEL_ORDER.index(lv)>_LEVEL_ORDER.index(old):rec["close_since"]=_today()
        else:rec["close_since"]=""
        logger.info(f"[好感] {nickname or uid}：{old}→{lv}")


def _commit(uid,rec,before,delta,bot_qq=None):
    previous=rec["favor"]
    rec["favor"]=round(max(MIN_F,min(MAX_F,previous+delta)),4)
    rec["last"]=_today()
    _check_level_up(uid,rec,rec.get("name",""))
    try:_save(uid,bot_qq)
    except Exception:
        users=_load(bot_qq)["users"]
        if before is None:users.pop(uid,None)
        else:users[uid]=before
        raise
    return round(rec["favor"]-previous,4)


def observe_message(group_id,user_id,nickname,text,has_at_bot,message_id=None,bot_qq=None):
    if not config.FAVOR_ENABLED or not has_at_bot or is_locked(user_id):return 0.0
    text=clean_text(text);fp=fingerprint(text)
    if not fp:return 0.0
    uid=str(user_id);now=time.time()
    event=f"{group_id}:{message_id}" if message_id is not None else ""
    with _lock:
        before=copy.deepcopy(_load(bot_qq)["users"].get(uid));rec=_record(uid,bot_qq)
        recent=[r for r in rec["recent"] if isinstance(r,dict) and now-_number(r.get("ts"))<86400]
        if any(r.get("fp")==fp or (event and r.get("event")==event) for r in recent):return 0.0
        if now-_number(rec.get("last_interaction"),-1e15)<CHAT_COOLDOWN:return 0.0
        sign=sentiment(text)
        rec["name"]=str(nickname or rec["name"])[:100]
        rec["last_interaction"]=now
        rec["recent"]=(recent+[{"fp":fp,"event":event,"ts":now}])[-128:]
        delta=0.0
        if sign<0:
            # Insults do NOT also earn ordinary chat points.
            if now-_number(rec.get("last_insult"),-1e15)>=INSULT_COOLDOWN:
                delta=-_add_capped(rec,"day_insult",DAILY_INSULT_CAP,INSULT_LOSS)
                rec["last_insult"]=now;rec["insult"]+=1
        else:
            first_today=rec["last_chat_day"]!=_today()
            if first_today:
                rec["streak"]=int(rec["streak"])+1 if rec["last_chat_day"]==_yesterday() else 1
                rec["last_chat_day"]=_today();rec["active_days"]+=1
            bonus=STREAK_BONUS if first_today and rec["streak"]>=2 else 0
            delta=_add_capped(rec,"day_gain",DAILY_CHAT_CAP,CHAT_GAIN+bonus)
            rec["chats"]+=1
            if sign>0 and now-_number(rec.get("last_praise"),-1e15)>=PRAISE_COOLDOWN:
                delta+=_add_capped(rec,"day_praise",DAILY_PRAISE_CAP,PRAISE_GAIN)
                rec["last_praise"]=now;rec["praise"]+=1
        return _commit(uid,rec,before,delta,bot_qq)


def on_poke(user_id,bot_qq=None):
    if not config.FAVOR_ENABLED or is_locked(user_id):return 0.0
    uid=str(user_id);now=time.time()
    with _lock:
        before=copy.deepcopy(_load(bot_qq)["users"].get(uid));rec=_record(uid,bot_qq)
        if now-_number(rec.get("last_poke"),-1e15)<POKE_COOLDOWN:return 0.0
        rec["last_poke"]=now
        return _commit(uid,rec,before,_add_capped(rec,"day_poke",DAILY_POKE_CAP,POKE_GAIN),bot_qq)


def get_favor(user_id,bot_qq=None):
    if is_locked(user_id):return MAX_F
    with _lock:
        rec=_load(bot_qq)["users"].get(str(user_id))
        n=_number(rec.get("favor"),DEFAULT_FAVOR) if isinstance(rec,dict) else DEFAULT_FAVOR
        return int(round(max(MIN_F,min(MAX_F,n))))


def level_name(score):
    name=_LEVELS[0][1]
    for floor,lv in _LEVELS:
        if score>=floor:name=lv
    return name


# 问好感时怎么自然回应的统一规则（交给模型自己判断意图，不做本地关键词匹配）
_ANSWER_RULE = (
    "你要自己读懂TA这句话（不管用什么措辞、怎么绕着问）：如果TA是在问你喜不喜欢TA、"
    "TA在你心里算什么位置、你对TA有没有好感/亲密度、你们关系怎么样，就结合上面你对TA的"
    "真实亲疏程度，用你自己的口吻自然回一两句，带情绪、有活人感——亲近就大方认、带点小得意"
    "或小傲娇，不熟就嘴硬打个太极，别假装喜欢。上面的亲疏只是给你拿捏语气的内部参考，"
    "回复里绝对不许出现任何数字、分数、百分比，也不许说“好感度”“等级”“系统”“数据”这类词，"
    "更别像在报报告；TA没问这些时就正常聊天，语气按这个亲疏程度自然有别。"
)


def favor_note(user_id, bot_qq=None) -> str:
    """统一注入：当前对象的真实亲疏程度 + 问好感时自行判断并自然作答（不报数值）。"""
    if not config.FAVOR_ENABLED:
        return ""
    try:
        score = get_favor(user_id, bot_qq)
    except (OSError, ValueError, TypeError):
        logger.warning("[好感] 数据读取失败，暂不注入亲疏判断；原文件未覆盖")
        return ""
    if is_locked(user_id) or score >= 99:
        degree = "你对TA是满好感，TA是你最偏爱、最在意的人，语气放松亲近、自然关心，但不必每句话撒娇或表白。"
    elif score >= 75:
        degree = "你挺喜欢TA、跟TA很亲近，语气热络些，愿意多聊两句，也能坦诚表达不同看法。"
    elif score >= 60:
        degree = "你对TA有好感、关系不错，自然友好，愿意接梗。"
    elif score >= 45:
        degree = "你把TA当普通朋友，不冷不热、正常客气地聊。"
    elif score >= 30:
        degree = "你跟TA还不太熟，别过分热情，保持分寸、稍微端着一点。"
    else:
        degree = "你对TA偏生分、不太待见，淡淡的、别主动贴上去，必要时毒舌岔开。"
    extra = ""
    if not is_locked(user_id):
        with _lock:
            users = _load(bot_qq).get("users", {}) or {}
            rec = dict(users.get(str(user_id)) or {})
        if isinstance(rec, dict):
            days = _days_between(_today(), str(rec.get("first") or _today()))
            if 30 <= days < 9999 and score >= 45:
                extra += f"你们已经认识 {days} 天了，是实打实的老熟人，相处可以更随意亲近一些。"
            elif 7 <= days < 9999 and score >= 30:
                extra += f"你们认识 {days} 天了，越来越熟。"
            cs = str(rec.get("close_since") or "")
            if cs and 0 <= _days_between(_today(), cs) <= 3:
                extra += "而且你们最近刚亲近了不少，正是热乎的时候。"
    return "亲疏只影响自然语气，不改变事实、权限、身份判断和边界；不因好感高而无条件附和，也不因好感低而羞辱对方。不主动宣称排他关系。" + "【你对当前这个人的真实感觉（内部参考，别外显成数字或术语）】" + degree + extra + _ANSWER_RULE
