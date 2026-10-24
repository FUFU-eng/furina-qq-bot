"""Self-only persistent reminders, with explicit confirmation for model-parsed requests."""
import asyncio
from datetime import datetime, timedelta
import json
import math
import re
import time

from nonebot import get_bots, get_driver, logger, on_message
from ...core import scheduler
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from ...services.reminder import settings
from ...services.reminder.domain import CST, Clarify, Proposal, make_proposal
from ...services.reminder.parser import (NeedPeriod, PERIOD_WORDS, add_period, candidate, management, local_create,
                                         model_messages, normalize, parse_model_reply)
from ...services.reminder.store import Store
from ...services.reminder.engine import tick
from ...core import env, llm, permissions, roles

_store=None
_task=None
_pending={}
_busy=set()
_cooldowns={}
_model_gate=asyncio.Semaphore(2)
_members={}
# 她问过「早上还是晚上」、或者刚定好的提醒：消息号 -> {gid, uid(提醒谁), text(原话), until, rid}
# 有人回复这条消息补一句「晚上啊」（哪怕是别人替 TA 答），或者 TA 本人接着说「晚上」，就按补充的改
_followups={}
_last_ask={}
_YES=re.compile(r'(?:对|对的|对对|是|是的|嗯|嗯嗯|好|好的|好滴|可以|行|没错|确认|确定|就这个|就这样|ok|OK|Ok)[啊呀的吧嘛!！~～。.]*')

HELP='''可以这样说：
• 20分钟后提醒我收菜
• 明天晚上8点叫我打周本
• 每天上午9点提醒我喝水
• 每周日下午3点提醒我整理照片
• 每月15号上午9点提醒我交账单（没有该日的月份跳过）
• 我的提醒 / 我的提醒 第2页
• 取消提醒 12 / 取消刚才那个提醒
只在本群提醒你本人。听得明白的我直接定好；不太确定的会先问你一句，回个「对」就行。
统一北京时间；离线错过不超过30分钟补发，超过跳过，重复任务保留下一次。'''


def _now():return datetime.now(CST)


def _allowed(gid):
    try:return settings.load()['enabled'] and permissions.allowed(gid,'reminder',False)
    except Exception:return False


def _original(event):
    return getattr(event,'original_message',None) or event.message


def _mentioned(event):
    ids={str(s.data.get('qq')) for s in _original(event) if s.type=='at'}
    ids.update(re.findall(r'\[(?:CQ:)?at[:,]qq=(\d+|all)\]',event.raw_message))
    return ids


def _sweep():
    now=time.monotonic()
    for key in [k for k,v in _pending.items() if v[0]<now]:_pending.pop(key,None)
    for key in [k for k,v in _cooldowns.items() if now-v>600]:_cooldowns.pop(key,None)


def _reply_id(event):
    try:
        if event.reply is not None:return str(event.reply.message_id)
    except Exception:
        pass
    m=re.search(r'\[(?:CQ:)?reply[:,]id=(-?\d+)',event.raw_message or '')
    return m[1] if m else ''


def _period(text):
    m=re.search(PERIOD_WORDS,normalize(text))
    return m[0] if m else ''


def _followup(event,text):
    """这条是不是在补「早上/晚上」：回复了她的追问/刚定的提醒，或者问过的那个人接着答。"""
    now=time.monotonic()
    for k in [k for k,v in _followups.items() if v['until']<now]:_followups.pop(k,None)
    if not _period(text) or len(normalize(text))>20:return None
    f=_followups.get(_reply_id(event))
    if f is None:
        mid=_last_ask.get((event.group_id,str(event.user_id)))
        f=_followups.get(mid) if mid else None
        if f is not None and f.get('rid'):return None  # 定好之后的改时间必须回复那条
    if f is None or f['gid']!=event.group_id:return None
    return f


def _when(proposal):
    """「今天12:00」「明天20:30」「每天09:00」：像人说话那样报时间。"""
    if proposal.kind!='once':
        return {'daily':'每天','weekly':'每周'+'、'.join('一二三四五六日'[d] for d in proposal.weekdays),
                'monthly':f'每月{proposal.monthday}号'}[proposal.kind]+' '+proposal.clock
    at=datetime.fromtimestamp(proposal.next_at,CST);days=(at.date()-_now().date()).days
    day={0:'今天',1:'明天',2:'后天'}.get(days,at.strftime('%m月%d号').lstrip('0'))
    if at.hour==0 and at.minute==0 and days==1:return '今晚十二点'
    return f'{day}{at.strftime("%H:%M")}'


# 明确的“让机器人提醒我”请求（与只是聊到“定时/闹钟/提醒”的普通对话区分）
_EXPLICIT_REQUEST=re.compile(r'(?:提醒|通知|叫|喊|催)(?:一下)?我|(?:设|定|订|开)(?:个|一个)?(?:闹钟|定时)|别让我忘')


def _rule(event):
    if not isinstance(event,GroupMessageEvent) or str(event.user_id) in {str(event.self_id),str(env.bot_qq())}:return False
    self_ids={str(event.self_id),str(env.bot_qq())}-{''}
    if not (_mentioned(event)&self_ids) and not roles.is_bot_at(event):return False
    _sweep()
    text=roles.strip_bot_at(event.get_plaintext(),str(getattr(event,'self_id','')))
    if _followup(event,text) is not None:return _allowed(event.group_id)
    if not candidate(text,(event.group_id,event.user_id) in _pending):return False
    if management(text) or _allowed(event.group_id):return True
    # 提醒功能默认关闭。关闭时只接管管理操作和明确的“提醒我/叫我…”请求（回复“尚未开启”，
    # 避免 AI 聊天口头答应却不会真的提醒）；“@芙芙 定时炸弹是什么”“闹钟坏了怎么办”这类
    # 只是提到关键词的普通聊天交给 AI 聊天，不再一律被拦下回复“本群提醒功能尚未开启”。
    return bool(_EXPLICIT_REQUEST.search(normalize(text)))


reminder=on_message(rule=Rule(_rule),priority=3,block=True)


async def _reply(bot,event,text,uid=None):
    msg=Message(MessageSegment.at(uid or event.user_id))+MessageSegment.text(' '+text)
    res=await bot.send_group_msg(group_id=event.group_id,message=msg)
    try:return str(res.get('message_id')) if isinstance(res,dict) else str(getattr(res,'message_id','') or '')
    except Exception:return ''


STATUS={'created':'已创建','sent':'已提醒','sent_late':'已延迟补发','skipped_late':'已错过，未补发',
        'cancelled':'已取消','unknown_delivery':'发送结果不确定，未重发',
        'unknown_after_restart':'中断前发送结果不确定，未重发','unknown_interrupted':'发送中断，未重发',
        'skipped_disabled':'权限关闭，已跳过本次'}


def _list_text(gid,uid,page=1):
    rows=_store.list_own(gid,uid)
    if not rows:return '你在本群还没有提醒。\n'+HELP
    pages=max(1,math.ceil(len(rows)/10));page=max(1,min(pages,page))
    out=[f'你在本群的提醒（第{page}/{pages}页）：']
    for row in rows[(page-1)*10:page*10]:
        at=datetime.fromtimestamp(row['next_at'],CST).strftime('%m-%d %H:%M')
        state=('下次 '+at) if row['state']=='active' else ('发送中' if row['state']=='sending' else '已结束')
        cadence={'once':'一次性','daily':'每天','weekly':'每周','monthly':'每月'}[json.loads(row['schedule'])['kind']]
        text=row['text'][:80]+('…' if len(row['text'])>80 else '')
        out.append(f'#{row["id"]} [{cadence}] {state}\n{text}\n状态：{STATUS.get(row["last_status"],row["last_status"])}')
    if pages>1:out.append('翻页：我的提醒 第2页（替换页码）')
    out.append('取消：取消提醒 编号。时间均为北京时间。')
    return '\n\n'.join(out)


async def _model_proposal(text,now):
    async def request():
        async with _model_gate:
            return await llm.chat(model_messages(text,now),timeout=25,max_tokens=500)
    response=await asyncio.wait_for(request(),timeout=35)
    return parse_model_reply(response,_now())


async def _create(bot,event,proposal,uid=None,origin=''):
    # Revalidate on confirmation: a pending time may already be in the past.
    now=_now()
    at=datetime.fromtimestamp(proposal.next_at,CST) if proposal.kind=='once' else None
    proposal=make_proposal(proposal.text,proposal.kind,now,at,proposal.clock,proposal.weekdays,proposal.monthday)
    source=f'{event.self_id}:{event.group_id}:{event.message_id}'
    uid=str(uid or event.user_id)
    row,created=_store.create(event.group_id,uid,proposal,now,source,settings.load()['max_active'])
    if not created:
        await _reply(bot,event,f'这个已经记上了（#{row["id"]}）',uid)
        return
    _cooldowns[(event.group_id,int(uid) if uid.isdigit() else uid)]=time.monotonic()
    mid=await _reply(bot,event,f'行，{_when(proposal)}叫你{proposal.text}\n（不要了就说「取消提醒 {row["id"]}」）',uid)
    if mid and origin:
        _followups[mid]={'gid':event.group_id,'uid':uid,'text':origin,'until':time.monotonic()+600,'rid':row['id']}


async def _answer_period(bot,event,f,period):
    """补了「早上/晚上」：问过的就按补充的定；已经定好的就改掉（谁替 TA 答都算，提醒的还是原来那个人）。"""
    uid=f['uid'];text=add_period(f['text'],period)
    try:
        proposal=local_create(text,_now())
        if proposal is None:raise Clarify('还是没太明白，直接说几点提醒干嘛吧')
    except Clarify as exc:
        await _reply(bot,event,str(exc),uid);return
    if f.get('rid'):
        _store.cancel(event.group_id,uid,f['rid'],_now())
    for k in [k for k,v in _followups.items() if v is f]:_followups.pop(k,None)
    _last_ask.pop((event.group_id,uid),None)
    await _create(bot,event,proposal,uid=uid,origin=text)


@reminder.handle()
async def handle(bot: Bot,event: GroupMessageEvent):
    text=event.get_plaintext().strip();key=(event.group_id,event.user_id)
    text=roles.strip_bot_at(text,str(getattr(event,'self_id',''))) or text
    f=_followup(event,text)
    if f is not None:
        await _answer_period(bot,event,f,_period(text));return
    action=management(text)
    if not action and key in _pending and _YES.fullmatch(normalize(text)):action=('confirm',None)
    if action and action[0]=='help':await _reply(bot,event,HELP);return
    if action and action[0]=='list':
        page=re.search(r'第?\s*(\d{1,3})\s*页',text)
        await _reply(bot,event,_list_text(*key,int(page[1]) if page else 1));return
    if action and action[0] in ('cancel','cancel_latest'):
        rid='latest' if action[0]=='cancel_latest' else action[1]
        if rid!='latest' and not 0<rid<2**63:
            await _reply(bot,event,'没有这个编号呀');return
        row=_store.cancel(*key,rid,_now())
        if row:
            _pending.pop(key,None)
            suffix='（这次正在发，发出去的撤不回来了）' if row['state']=='sending' else ''
            await _reply(bot,event,f'好，#{row["id"]}「{row["text"][:20]}」不提醒了{suffix}')
        else:await _reply(bot,event,'没找到这个，你说「我的提醒」我给你看看都有哪些')
        return
    if action and action[0]=='cancel_help':await _reply(bot,event,'取消哪个呀，先说「我的提醒」看看编号');return
    if action and action[0]=='discard':_pending.pop(key,None);await _reply(bot,event,'好，那就不定了');return
    if not _allowed(event.group_id):
        await _reply(bot,event,'这个群我还定不了提醒，得让群主在面板里把「定时提醒」打开');return
    if _mentioned(event)-{str(event.self_id),str(event.user_id)}:
        await _reply(bot,event,'我只能提醒你自己哦，别人要的话让 TA 自己跟我说');return
    if key in _busy:await _reply(bot,event,'等下，上一个还在弄');return
    _busy.add(key)
    try:
        if action and action[0]=='confirm':
            pending=_pending.pop(key,None)
            if not pending or pending[0]<time.monotonic():raise Clarify('刚才那个过期啦，再说一次几点干嘛')
            await _create(bot,event,pending[1]);return
        cfg=settings.load()
        if len(text)>500:raise Clarify('太长了，简单说几点干嘛就行')
        if time.monotonic()-_cooldowns.get(key,-10000)<cfg['cooldown_seconds']:
            raise Clarify('慢点慢点，过几秒再说')
        _cooldowns[key]=time.monotonic()
        _pending.pop(key,None)
        proposal=local_create(text,_now())
        if proposal is None:
            if not cfg['natural_language']:
                raise Clarify('几点呀，说个具体时间')
            try:proposal=await _model_proposal(text,_now())
            except Clarify:raise
            except Exception:
                logger.warning('[提醒] 自然语言解析暂不可用；未创建任务')
                raise Clarify('没太听明白，几点提醒你干嘛') from None
            _pending[key]=(time.monotonic()+120,proposal)  # 模型理解的：先跟 TA 对一下
            await _reply(bot,event,f'是{_when(proposal)}叫你{proposal.text}吧，对的话回我一声');return
        await _create(bot,event,proposal,origin=text)  # 本地听得明白的直接定，像人一样「行，几点叫你」
    except NeedPeriod as exc:
        mid=await _reply(bot,event,str(exc))
        if mid:
            _followups[mid]={'gid':event.group_id,'uid':str(event.user_id),'text':text,'until':time.monotonic()+600}
            _last_ask[(event.group_id,str(event.user_id))]=mid
    except Clarify as exc:
        await _reply(bot,event,str(exc))
    finally:
        _busy.discard(key)


def _delivery_sender(bot,gid):
    async def send(uid,text):
        await bot.send_group_msg(group_id=int(gid),message=Message(MessageSegment.at(uid))+MessageSegment.text(' '+text))
        await asyncio.sleep(1)  # Pace simultaneous reminders; never burst a whole queue.
    return send


async def _online_senders():
    mapping={};now=time.monotonic()
    for bot in get_bots().values():
        try:
            # Connected WS is not sufficient: QQ may be offline behind NapCat.
            state=await asyncio.wait_for(bot.get_status(),timeout=8)
            if not state.get('online',False):continue
            cache=_members.get(str(bot.self_id))
            if not cache or now-cache[0]>60:
                rows=await asyncio.wait_for(bot.get_group_list(),timeout=8)
                cache=(now,{str(r['group_id']) for r in rows});_members[str(bot.self_id)]=cache
            for gid in cache[1]:mapping.setdefault(gid,_delivery_sender(bot,gid))
        except Exception:
            continue
    return mapping


async def _tick():
    _sweep();cfg=settings.load();now=_now()
    # Don't poll QQ status when there is no pending occurrence.
    due=_store.due(now,1)
    senders=await _online_senders() if due and cfg['enabled'] else {}
    await tick(_store,_now(),senders,_allowed,cfg['grace_minutes'],clock=_now)


@get_driver().on_startup
async def start():
    global _store,_task
    _store=Store();_task=scheduler.every('定时提醒',5,_tick,quiet=True)
    logger.info('[提醒] 持久化提醒插件已加载；默认关闭，可在面板及群权限中开启')


@get_driver().on_shutdown
async def stop():
    if _task:
        await scheduler.stop('定时提醒')
