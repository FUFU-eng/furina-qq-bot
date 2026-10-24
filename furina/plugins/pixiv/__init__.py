"""Pixiv daily/weekly/monthly ranking delivery and named image requests.
Default off. Does not read browser credentials and never restarts NapCat.
"""
import asyncio
import json
import re
import random
import time
import uuid
from datetime import datetime, timedelta

from nonebot import get_bots, get_driver, logger, on_message
from ...core import scheduler
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from ...services.pixiv import settings
from ...core.pixiv_intent import AMBIGUOUS, parse_request, queries_for
from ...services.pixiv.provider import Client, PixivError, PixivNetworkError, PixivAccessError, PixivDownloadError
from ...services.pixiv.schedule import CST, board_for, slots_for, rank_bounds
from ...services.pixiv.storage import Store
from ...services.pixiv.delivery import send_work, SendUncertain
from ...services.pixiv.announcement import make_opener
from ...services.pixiv.qq_receipt import send_once
from ...core import env, permissions, roles
from ...core.task_queue import TaskQueue

_client = None
_store = None
_loop_task = None
_jobs = set()
_lock = asyncio.Lock()
_point_queue = TaskQueue("pixiv_point")
_cooldowns = {}
_started = None
# 正在进行的定时/手动榜单批次数。只有“上一批还在跑”才跳过新批次；
# 点图任务占着 _lock 时，新批次改为排队等点图发完再发（见 _fire_slot）。
_batch_running = 0


def _config():
    return settings.load()


def _allowed(gid, feature):
    try:
        return _config()['enabled'] and permissions.allowed(gid, feature, False)
    except Exception:
        return False


def _text(event):
    text = event.get_plaintext().strip()
    return roles.strip_bot_at(text, str(getattr(event, 'self_id', '')))


def _rule(event):
    if not isinstance(event, GroupMessageEvent) or str(event.user_id) in {str(event.self_id), str(env.bot_qq())}:
        return False
    original = getattr(event, 'original_message', None) or event.message
    self_ids = {str(event.self_id), str(env.bot_qq())} - {''}
    has_at = any(s.type == 'at' and str(s.data.get('qq')) in self_ids for s in original) or roles.is_bot_at(event)
    if not has_at:
        return False
    if any(s.type == 'reply' for s in original) or getattr(event, 'reply', None):
        return False  # Existing reply-image search/upscale behavior is unchanged.
    return parse_request(_text(event)) is not None


point = on_message(rule=Rule(_rule), priority=4, block=True)


def _spawn(coro):
    task = asyncio.create_task(coro)
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    return task


def _sender(bot, gid, bare_images=False):
    # bare_images: scheduled pushes send single-page images without the
    # author/PID caption. Merged-forward (multi-page) messages stay unchanged.
    async def send(text, paths, *, forward=False, start_page=1, total_pages=None,
                   node_captions=None):
        if paths:  # 点图和定时榜单推送发出的图都收进筛图库
            try:
                from ..pic_inbox import hook as _pic_hook
                for _p in paths:
                    _pic_hook('pixiv_rank' if bare_images else 'pixiv', path=str(_p), gid=gid)
            except Exception:
                pass
        if forward:
            def node(content):
                return {"type": "node", "data": {
                    "name": "Pixiv 画作", "uin": str(bot.self_id),
                    "content": content,
                }}
            nodes = [node([MessageSegment.text(text)])]
            for i, path in enumerate(paths):
                if node_captions is not None:
                    # 批量取图：逐张图用调用方给的署名（作者 / PID），不再用全局页码
                    label = node_captions[i] if i < len(node_captions) else ''
                    content = ([MessageSegment.text(label)] if label else []) + [
                        MessageSegment.image(path.resolve().as_uri(), cache=False)]
                else:
                    page = start_page + i
                    content = [
                        MessageSegment.text(f'第 {page}/{total_pages} 页'),
                        MessageSegment.image(path.resolve().as_uri(), cache=False),
                    ]
                nodes.append(node(content))
            # Use the same NapCat API as the existing merged-forward plugins.
            # No fallback/replay on timeout: delivery may already have happened.
            return await send_once(bot,gid,text,paths,'send_forward_msg',{'group_id':int(gid),'messages':nodes},first=start_page,total=total_pages,forward=True)
        else:
            if bare_images and paths:
                text = ''
            msg = Message(MessageSegment.text(text)) if text else Message()
            for path in paths:
                msg.append(MessageSegment.image(path.resolve().as_uri(), cache=False))
            return await send_once(bot,gid,text,paths,'send_group_msg',{'group_id':int(gid),'message':msg,'timeout':300000})
    return send


@point.handle()
async def handle_point(bot: Bot, event: GroupMessageEvent):
    if not _allowed(event.group_id, 'pixiv_search'):
        await point.finish('这个群还没有开启 Pixiv 点图，请在控制面板的群权限中开启。')
    req = parse_request(_text(event))
    if req.query in AMBIGUOUS:
        await point.finish(AMBIGUOUS[req.query])
    key = (event.group_id, event.user_id)
    cfg = _config()
    now = time.monotonic()
    if now - _cooldowns.get(key, -10000) < cfg['search_cooldown_seconds']:
        await point.finish('刚刚才点过图，稍等一会儿再来找我吧。')
    # Register and spawn without yielding: tasks reach the FIFO lock in arrival order.
    ticket = _point_queue.enqueue()
    _cooldowns[key] = now
    for old in [k for k, v in _cooldowns.items() if now - v > 3600]:
        _cooldowns.pop(old, None)
    task = _spawn(_queued_point_job(bot, event.group_id, event.user_id, req, cfg, ticket))
    # Also release tickets cancelled before their coroutine gets its first turn.
    task.add_done_callback(lambda _: ticket.cancel())


async def _point_notice(bot, gid, uid, text):
    """Queue notices are best effort; never replay a possibly delivered message."""
    try:
        await bot.send_group_msg(
            group_id=int(gid),
            message=MessageSegment.at(uid) + MessageSegment.text(' ' + text),
        )
    except Exception as exc:
        logger.warning(f'[Pixiv] 点图队列通知未确认：{type(exc).__name__}，不重试')


async def _queued_point_job(bot, gid, uid, req, cfg, ticket):
    notice = None
    try:
        if ticket.position > 1 or _lock.locked():
            text = (f'已加入 Pixiv 点图队列（第 {ticket.position} 个点图请求，'
                    f'前面还有 {ticket.position - 1} 个点图请求）。上一单发完后会自动处理，不用重复点图。')
            if ticket.position == 1:
                text = '已加入 Pixiv 点图队列，正在等待当前 Pixiv 批次发完，之后会自动处理，不用重复点图。'
            notice = asyncio.create_task(_point_notice(bot, gid, uid, text))
        # Acquire before awaiting the notice, so slow QQ replies cannot reorder requests.
        async with ticket:
            if notice:
                await notice
            async with _lock:
                if not _allowed(gid, 'pixiv_search'):
                    await _point_notice(bot, gid, uid, 'Pixiv 点图权限已关闭，本次排队请求已取消。')
                    return
                await _point_notice(bot, gid, uid, f'轮到你的「{req.query}」点图了，现在开始查找并发送。')
                await _point_job(bot, gid, req, cfg)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f'[Pixiv] 点图队列任务失败：{type(exc).__name__}；继续下一单')
    finally:
        ticket.cancel()
        if notice and not notice.done():
            notice.cancel()
            await asyncio.gather(notice, return_exceptions=True)


async def _point_job(bot, gid, req, cfg):
    send = _sender(bot, gid)
    key = f'search:{datetime.now(CST).date()}:{gid}:{uuid.uuid4().hex[:8]}'
    try:
        _store.claim(key, detail=f'检索 {req.query}，目标 {req.count} 部')
        cap_note = '（一次最多十部，已经帮你收住啦。）' if req.clamped else ''
        await send(f'哼哼，想看「{req.query}」对吧？让我去挑 {req.count} 部画作。{cap_note}', [])
        pool = await _client.search(queries_for(req.query, cfg['aliases']), req.count,
                                    pool_size=min(30, max(20, req.count * 3)))
        # 同一角色换着发：先排除本群最近发过的，再随机抽，发送时仍按热度排序。
        recent = set(_store.recent_search_pids(gid))
        fresh = [w for w in pool if str(w['id']) not in recent]
        # Keep every available fresh work before filling the shortfall from history.
        works = random.sample(fresh, min(req.count, len(fresh)))
        remaining = [w for w in pool if str(w['id']) not in {str(x['id']) for x in works}]
        if len(works) < req.count:
            works += random.sample(remaining, min(req.count-len(works), len(remaining)))
        order = {str(w['id']): i for i, w in enumerate(pool)}
        works.sort(key=lambda w: order.get(str(w['id']), 0))
        if not works:
            _store.finish(key, 'partial', '没有可用候选作品')
            await send(f'这次没找到「{req.query}」的可用插画。可以试试完整角色名，或在 Pixiv 设置里补充标签映射。', [])
            return
        success, failures = 0, 0
        for round_no in range(2):
            deferred=[]
            for work in works:
                if not _allowed(gid,'pixiv_search'):
                    _store.finish(key,'stopped',f'已发 {success} 部，群权限已关闭');return
                try:
                    await send_work(_client,_store,work,send,cfg,
                                    allowed=lambda:_allowed(gid,'pixiv_search'),receipt_scope=key)
                    success+=1
                    _store.remember_search_pids(gid,[str(work['id'])])
                except SendUncertain:
                    _store.remember_search_pids(gid,[str(work['id'])]);raise
                except PixivDownloadError:
                    if round_no==0:deferred.append(work)
                    else:failures+=1
                except (PixivNetworkError,PixivAccessError):raise
                except PixivError:failures+=1
            if not deferred:break
            works=deferred
            await asyncio.sleep(5)
        _store.finish(key, 'completed' if success == req.count else 'partial', f'已发送 {success}/{req.count} 部；失败 {failures} 部')
        if success < req.count:
            await send(f'这次成功发了 {success} 部，目标是 {req.count} 部；其余候选不足或下载失败，没有拿无关图片凑数。', [])
    except asyncio.CancelledError:
        _store.finish(key, 'interrupted', '点图任务中断，不自动重发')
        raise
    except Exception as exc:
        detail = str(exc) if isinstance(exc, PixivError) else type(exc).__name__
        _store.finish(key, 'stopped', detail)
        logger.warning(f'[Pixiv] 点图停止 {key}：{detail}')
        # Do not retry a failed send, including uncertainty around opener/result messages.
        if isinstance(exc, PixivError) and not isinstance(exc, SendUncertain):
            try:
                await send(f'Pixiv 点图未能完成：{detail[:160]}。已发出的作品不会自动重发；详情可看运行记录。', [])
            except Exception:
                pass
    finally:
        _store.cleanup()


async def _wait_for_board(allowed, seconds=300):
    """Wait in 60s steps so disabling the feature takes effect promptly."""
    waited = 0
    while waited < seconds:
        await asyncio.sleep(60)
        waited += 60
        if not allowed():
            raise PixivError('推送已关闭或批次过期')


async def _batch_job(bot, gid, key, board, batch, cfg, deadline, rows, turn_lock=None):
    send=_sender(bot,gid,bare_images=True)
    turn_lock = turn_lock or asyncio.Lock()
    scope=f'rank:{board.key}:{gid}'
    low,high=rank_bounds(batch)
    batch_rows=[r for r in rows if low<=r['rank']<=high]
    planned=len(batch_rows);newly=0;announced=False
    errors={};consecutive=0
    allowed=lambda:_allowed(gid,'pixiv_rank') and datetime.now(CST)<deadline
    def confirmed():return sum(_store.work_sent(scope,r['id']) for r in batch_rows)
    def progress(status='running',note=''):
        _store.finish(key,status,f'已确认 {confirmed()}/{planned} 部；本轮新增 {newly} 部；待处理 {len(errors)} 部。'+note[:250])
    try:
        if not allowed():raise PixivError('推送已关闭或本批已到期')
        queue=[r for r in batch_rows if not _store.work_sent(scope,r['id'])]
        for round_no in range(2):
            deferred=[]
            for row in queue:
                if not allowed():raise PixivError('推送已关闭或本批已到期，断点已保存')
                if _store.work_sent(scope,row['id']):continue
                pid=str(row['id'])
                try:
                    # Fair FIFO work turns: no concurrent QQ sends or cache writers.
                    async with turn_lock:
                        delay=max(0,getattr(_client,'_blocked_until',0)-time.monotonic())
                        if delay:
                            await asyncio.sleep(min(delay,max(0,(deadline-datetime.now(CST)).total_seconds())))
                        if not allowed():raise PixivError('本批已到期')
                        # Deadline also bounds a slow original; never overrun the next scheduled batch.
                        async with asyncio.timeout(max(.1,(deadline-datetime.now(CST)).total_seconds())):
                            work=await _client.detail(pid)
                            if not announced:
                                await send(await make_opener(),[])
                                announced=True
                            await send_work(_client,_store,work,send,cfg,prefix=f'#{row["rank"]} · ',allowed=allowed,receipt_scope=scope)
                    newly+=1;consecutive=0;errors.pop(pid,None)
                    logger.info(f'[Pixiv回执] group={gid} board={board.key} rank={row["rank"]} pid={pid} confirmed')
                except SendUncertain:
                    raise  # No retry after an unknown send acknowledgement.
                except PixivAccessError:
                    raise
                except PixivDownloadError as exc:
                    errors[pid]=f'#{row["rank"]}: {exc}';consecutive+=1
                    if round_no==0:deferred.append(row)
                    logger.warning(f'[Pixiv] 单作品延后 {key} rank={row["rank"]}: {exc}')
                    if consecutive>=3:raise PixivNetworkError('连续三部原图下载失败，本群暂停，已保存逐作品断点') from None
                except PixivNetworkError as exc:
                    errors[pid]=f'#{row["rank"]}: {exc}';consecutive+=1
                    if round_no==0:deferred.append(row)
                    logger.warning(f'[Pixiv] API暂时失败，冷却后继续其他作品 {key} rank={row["rank"]}')
                    if consecutive>=3:raise PixivNetworkError('连续三部作品API/下载失败，本群暂停，已保存断点') from None
                except PixivError as exc:
                    errors[pid]=f'#{row["rank"]}: {exc}'
                progress()
            if not deferred:break
            progress('waiting','本轮其他作品已处理，稍后仅重试尚未发出的下载失败项')
            await asyncio.sleep(5)
            queue=deferred
        done=confirmed()
        progress('completed' if done==planned else 'partial','; '.join(errors.values()))
        # Detailed counts stay in the panel/ledger, not in group chatter.
    except asyncio.CancelledError:
        progress('interrupted','进程停止，已保存逐作品/分块回执，不自动重发不确定消息');raise
    except Exception as exc:
        detail=str(exc) if isinstance(exc,PixivError) else type(exc).__name__
        progress('stopped',detail)
        logger.warning(f'[Pixiv] 批次停止 {key}: {detail}')
        if isinstance(exc,PixivError) and not isinstance(exc,SendUncertain):
            try:await send('这轮发图遇到点问题，先停一下。',[])
            except Exception:logger.warning(f'[Pixiv] 暂停通知未确认 {key}，不重试')
    finally:
        _store.cleanup()


async def _collect_groups():
    """列出所有开启了榜单推送的群（定时批次和手动补推共用）。"""
    groups = []
    for bot in get_bots().values():
        try:
            members = await bot.get_group_list()
        except Exception:
            continue
        for g in members:
            gid = str(g['group_id'])
            if _allowed(gid, 'pixiv_rank'):
                groups.append((bot, gid))
    return groups


async def _resolve_board(board, keys, deadline):
    """拉榜单；未公布则把 keys 全部标 waiting，每5分钟重试；关闭/过期返回 None。

    注意：调用时不要占着 _lock，让点图在等榜单时照常可用。
    """
    rows = None
    while rows is None:
        try:
            rows = await _store.ranking(_client, board)
        except PixivError as exc:
            if '榜单尚未公布' not in str(exc):
                raise
            for k in keys:
                _store.finish(k, 'waiting', f'榜单尚未公布，等待后重试（本批截止 {deadline:%H:%M}）')
            logger.info(f'[Pixiv] {board.key} 榜单未公布，等待重试')
            try:
                await _wait_for_board(lambda: _config()['enabled'] and datetime.now(CST) < deadline)
            except PixivError:
                return None
    return rows


async def _dispatch(groups, board, batch, cfg, deadline, day, key_suffix=''):
    global _batch_running
    _batch_running += 1
    try:
        await _dispatch_inner(groups, board, batch, cfg, deadline, day, key_suffix)
    finally:
        _batch_running -= 1


async def _dispatch_inner(groups, board, batch, cfg, deadline, day, key_suffix=''):
    # 先把所有群的记录都建好（榜单没出时也看得到 waiting，不用排队等）。
    keys = []
    claimed = []
    for bot, gid in groups:
        key = f'{day}:{batch}:{gid}{key_suffix}'
        if not _store.claim(key, detail=board.label):
            continue
        keys.append(key)
        claimed.append((bot, gid, key))
    if not claimed:
        return
    # 榜单只取一次、不占锁：等榜单时点图照常可用。
    try:
        rows = await _resolve_board(board, keys, deadline)
    except asyncio.CancelledError:
        for k in keys:
            _store.finish(k, 'interrupted', '等榜单时进程停止，不自动补发')
        raise
    except PixivError as exc:
        for k in keys:
            _store.finish(k, 'stopped', str(exc)[:200])
        logger.warning(f'[Pixiv] 取榜单失败 {board.key}: {exc}')
        return
    if rows is None:
        for k in keys:
            _store.finish(k, 'stopped', '等榜单时推送已关闭或批次过期')
        return
    # Keep point-search/ranking mutual exclusion, but rotate groups per work.
    # A many-page work still stays intact; it no longer holds up 30 full works
    # for every subsequent group. Only one work is downloaded/sent at a time.
    await _lock.acquire()
    try:
        turn_lock = asyncio.Lock()
        await asyncio.gather(*(
            _batch_job(bot,gid,key,board,batch,cfg,deadline,rows,turn_lock=turn_lock)
            for bot,gid,key in claimed
        ))
    finally:
        _lock.release()


def _trigger_path():
    return settings.DATA / 'pixiv' / 'push_now.json'


def _peek_trigger(now):
    """看一眼手动补推请求：有效则返回（不删文件），过期/损坏则删掉返回 None。"""
    path = _trigger_path()
    try:
        if not path.exists():
            return None
        req = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        req = None
    if not isinstance(req, dict):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.warning('[Pixiv] 手动补推请求文件损坏，已丢弃')
        return None
    day = str(req.get('day') or '')
    batches = req.get('batches') or []
    if day != now.date().isoformat() or not batches or any(
            not isinstance(b, int) or not 0 <= b < 5 for b in batches):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    after = str(req.get('after') or '').strip()
    if after:
        try:
            hh, mm = after.split(':')
            if now < now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0):
                return None  # 还没到约定时间，文件留下次 tick 再看
        except (ValueError, TypeError):
            pass  # after 写错了就当没有，直接执行
    request_id=req.get('request_id','')
    if request_id and (not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',request_id)):
        logger.warning('[Pixiv] 补推request_id无效，保留请求供人工检查');return None
    groups=req.get('groups')
    if groups is not None and (not isinstance(groups,list) or not groups or any(not isinstance(g,str) or not g.isdigit() for g in groups)):
        logger.warning('[Pixiv] 补推groups无效，保留请求供人工检查');return None
    return {'day':day,'batches':list(dict.fromkeys(batches)),'request_id':request_id,'groups':groups}


def _consume_trigger():
    """派发成功后删掉请求文件，删不掉返回 False（下个 tick 会重试派发）。"""
    try:
        _trigger_path().unlink()
        return True
    except Exception:
        return False


def _clear_of_slots(times, now, pre_s=2700, post_s=600):
    """手动补推的避让窗口：定时批次前45分钟、后10分钟内不动手，避免吃掉/重发正常批次。"""
    for at in slots_for(now.date(), times):
        if now < at and (at - now).total_seconds() < pre_s:
            return False
        if at <= now and (now - at).total_seconds() < post_s:
            return False
    return True


_last_empty_warn = 0.0


async def _fire_slot(batch, at, now, cfg, seen):
    """处理一个到点的定时批次。群列表没就绪时 60 秒内下个 tick 重试，不丢弃。"""
    marker = (now.date().isoformat(), batch)
    if marker in seen or now < at:
        return
    groups = await _collect_groups()
    if not groups:
        if (now - at).total_seconds() > 60:
            seen.add(marker)
        return
    seen.add(marker)
    # 旧版用 _lock.locked() 判断“上一批仍忙”，但点图任务也持有这把锁：到点时恰好有人在点图，
    # 整批榜单就被标记跳过。现在只有上一批榜单确实还在跑才跳过；被点图占着锁时，
    # _dispatch 内部会等点图发完再发（批次截止时间不变）。
    if at < _started or (now - at).total_seconds() > 60 or _batch_running:
        reason = '机器人错过时刻/刚启动/上一批仍忙，按设置不补发'
        for _, gid in groups:
            _store.claim(f'{now.date()}:{batch}:{gid}', 'skipped', reason)
        return
    slots = slots_for(now.date(), cfg['times'])
    deadline = slots[batch+1] if batch < 4 else at.replace(hour=0, minute=0) + timedelta(days=1)
    _spawn(_dispatch(groups, board_for(now.date()), batch, cfg, deadline, now.date()))


async def _try_manual(now, cfg):
    """手动补推尝试一次：条件不够返回 False（文件保留，下个 tick 重试）。"""
    global _last_empty_warn
    if _lock.locked() or not _clear_of_slots(cfg['times'], now):
        return False
    trig = _peek_trigger(now)
    if trig is None:
        return False
    groups = await _collect_groups()
    if trig.get('groups') is not None:
        groups=[(bot,gid) for bot,gid in groups if gid in trig['groups']]
    if not groups:
        if now.timestamp() - _last_empty_warn > 600:
            _last_empty_warn = now.timestamp()
            logger.warning('[Pixiv] 手动补推：暂无可用群（可能刚启动），稍后重试')
        return False
    _consume_trigger()
    tday = datetime.strptime(trig['day'], '%Y-%m-%d').date()
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    future=[at for at in slots_for(now.date(),cfg['times']) if at>now]
    if future:end=min(end,min(future)-timedelta(minutes=5))
    suffix=':resume:'+trig['request_id'] if trig.get('request_id') else ':manual'
    for b in trig['batches']:
        _spawn(_dispatch(groups, board_for(tday), b, cfg, end, trig['day'], key_suffix=suffix))
    return True


def _sweep_stale():
    """启动时把上次没跑完的记录标成 interrupted（崩溃/强杀不走正常取消路径）。"""
    try:
        with _store.connect() as db:
            cur = db.execute(
                "UPDATE jobs SET status='interrupted', detail='进程结束前未完成，不自动补发', updated=? "
                "WHERE status IN ('started','waiting','running')", (time.time(),))
            n = cur.rowcount
    except Exception as exc:
        logger.warning(f'[Pixiv] 启动清理旧记录失败：{type(exc).__name__}')
        return 0
    if n:
        logger.info(f'[Pixiv] 启动清理 {n} 条未完成记录')
    return n


_seen_slots: set = set()


async def _scheduler_tick():
    global _seen_slots
    cfg = _config()
    now = datetime.now(CST)
    # Bound in-memory bookkeeping; persistent state is separately pruned.
    _seen_slots = {x for x in _seen_slots if x[0] == now.date().isoformat()}
    _store.cleanup()
    if cfg['enabled']:
        for batch, at in enumerate(slots_for(now.date(), cfg['times'])):
            await _fire_slot(batch, at, now, cfg, _seen_slots)
        await _try_manual(now, cfg)


@get_driver().on_startup
async def start():
    global _client, _store, _started, _loop_task
    _started = datetime.now(CST)
    _store = Store()
    _client = Client()
    _sweep_stale()
    _loop_task = scheduler.every('Pixiv 榜单', 10, _scheduler_tick, quiet=True)
    logger.info('[Pixiv] 插画榜单/点图插件已加载；默认关闭，请在面板设置及群权限中启用')


@get_driver().on_shutdown
async def stop():
    if _loop_task:
        await scheduler.stop('Pixiv 榜单')
    tasks = list(_jobs)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if _client:
        await _client.close()
    if _store:
        _store.cleanup()


# 末尾导入：``@机器人 pid1234567`` 按 ID 取图（用上面这些运行期对象，必须放最后）
from . import pid_lookup as _pid_lookup  # noqa: E402,F401

