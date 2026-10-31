"""Strictly verified OneBot/NapCat delivery receipts.

When ``send_group_msg`` or ``send_forward_msg`` times out at the HTTP/WS API
layer, QQ may still have delivered the message. We never blindly resend; we
inspect recent group history and only mark a chunk sent when our own message
after ``since`` matches the PID caption, local image MD5s, AND has a unique
server-assigned ``real_seq`` (NTQQ keeps failed outgoing messages locally with
the preceding message's ``real_seq`` or ``"0"``).
"""
import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
try:
    from nonebot import logger
except ImportError:
    logger=logging.getLogger(__name__)

SEND_TIMEOUT=300.0
SINGLE_SEND_TIMEOUT=30.0
CONFIRM_DELAYS=(1.0,2.5)
HISTORY_COUNT=100
MD5_RE=re.compile(r'([0-9a-fA-F]{32})')


def file_md5(path):
    h=hashlib.md5()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(256*1024),b''):h.update(chunk)
    return h.hexdigest().lower()


def _segments(message):
    if isinstance(message,list):return [s for s in message if isinstance(s,dict)]
    return []


def _texts(obj):
    out=[]
    if isinstance(obj,str):out.append(obj)
    elif isinstance(obj,dict):
        for k in ('raw_message','text','content','summary','prompt'):
            if isinstance(obj.get(k),str):out.append(obj[k])
        for k in ('message','content','messages','data'):
            if k in obj:out.extend(_texts(obj[k]))
    elif isinstance(obj,list):
        for item in obj:out.extend(_texts(item))
    return out


def _image_md5s(obj):
    found=[]
    if isinstance(obj,dict):
        if obj.get('type')=='image':
            data=obj.get('data') or {}
            blob=' '.join(str(data.get(k,'')) for k in ('file','url','path','file_id','filename'))
            found.extend(m.lower() for m in MD5_RE.findall(blob))
        for k in ('message','content','messages','data'):
            if k in obj:found.extend(_image_md5s(obj[k]))
    elif isinstance(obj,list):
        for item in obj:found.extend(_image_md5s(item))
    elif isinstance(obj,str):
        for cq in re.findall(r'\[CQ:image[^\]]+\]',obj):
            found.extend(m.lower() for m in MD5_RE.findall(cq))
    return found


def _forward_ids(msg):
    ids=[]
    for seg in _segments(msg.get('message')):
        if seg.get('type') in ('forward','longmsg'):
            fid=str((seg.get('data') or {}).get('id') or '')
            if fid:ids.append(fid)
    raw=str(msg.get('raw_message') or '')
    ids.extend(re.findall(r'\[CQ:forward,[^\]]*?id=([^,\]]+)',raw))
    return list(dict.fromkeys(ids))


async def _expand_forward(bot,msg):
    expanded=[msg]
    for fid in _forward_ids(msg):
        for key in (fid,int(fid)) if fid.isdigit() else (fid,):
            try:
                res=await bot.call_api('get_forward_msg',message_id=key,id=key)
                if isinstance(res,dict):expanded.append(res);break
            except Exception:
                continue
    return expanded


def _matches_page_markers(text_blob,first,total,count,forward):
    if not forward or not total or total<=1:return True
    last=first+count-1
    return f'第 {first}/{total} 页' in text_blob and f'第 {last}/{total} 页' in text_blob


def _is_unaccepted_local_send(m, messages):
    """Return True if ``m`` in ``get_group_msg_history`` is a local failed/unsequenced send.

    When NTQQ starts sending an outgoing group message, it immediately inserts the
    message into the local database with ``real_seq`` set to ``"0"``/``""`` or copied
    from the group's previous message. If Tencent's server rejects the send
    (``KSEND_STATUS_FAILED``), ``real_seq`` is never updated to a new sequence number,
    so it duplicates another message's ``real_seq`` in ``messages``.
    """
    if not isinstance(m, dict) or 'real_seq' not in m:
        return False
    seq = str(m.get('real_seq') or '').strip()
    if not seq or seq == '0':
        return True
    mid = m.get('message_id')
    for other in messages:
        if not isinstance(other, dict) or other is m:
            continue
        if mid is not None and other.get('message_id') == mid:
            continue
        other_seq = str(other.get('real_seq') or '').strip()
        if other_seq and other_seq == seq:
            return True
    return False


async def confirm_history(bot,gid,caption,hashes,since,first=1,total=None,forward=False):
    self_id=str(getattr(bot,'self_id','') or '')
    expected={h.lower() for h in hashes if h}
    pid_token=next((part.strip() for part in str(caption or '').split('·') if 'PID' in part),str(caption or '').strip())
    for delay in CONFIRM_DELAYS:
        await asyncio.sleep(delay)
        try:
            hist=await bot.call_api('get_group_msg_history',group_id=int(gid),count=HISTORY_COUNT)
        except Exception as exc:
            logger.warning(f'[Pixiv回执核验] 群历史查询失败 group={gid}: {type(exc).__name__}')
            continue
        messages=(hist or {}).get('messages') if isinstance(hist,dict) else []
        for m in reversed(messages or []):
            if not isinstance(m,dict):continue
            t=int(m.get('time') or 0)
            if t and t<int(since)-5:continue
            uid=str(m.get('user_id') or (m.get('sender') or {}).get('user_id') or '')
            if self_id and uid and uid!=self_id:continue
            blobs=await _expand_forward(bot,m) if forward else [m]
            text_blob='\n'.join(_texts(blobs))
            got_md5=set(_image_md5s(blobs))
            if pid_token and pid_token not in text_blob:continue
            if not _matches_page_markers(text_blob,first,total,len(expected),forward):continue
            if expected and not expected.issubset(got_md5):continue
            mid=m.get('message_id') or m.get('real_id') or m.get('message_seq')
            if not mid:continue
            if _is_unaccepted_local_send(m, messages or []):
                logger.warning(
                    f'[Pixiv回执核验] 跳过本地未送达记录（服务端未分配新序号） '
                    f'group={gid} message_id={mid} real_seq={m.get("real_seq")}'
                )
                continue
            logger.info(f'[Pixiv回执核验] group={gid} message_id={mid} images={len(expected)} verified=history+caption+MD5+pages')
            return {'message_id':mid,'confirmed':'history'}
    return None


def _compute_send_timeout_s(paths, forward):
    if forward or len(paths) > 1:
        return SEND_TIMEOUT
    total_bytes = 0
    for p in paths:
        try:
            total_bytes += Path(p).stat().st_size
        except OSError:
            pass
    mb = total_bytes / (1024 * 1024)
    return min(90.0, max(SINGLE_SEND_TIMEOUT, 25.0 + mb * 4.0))


async def send_once(bot,gid,caption,paths,action,params,*,first=1,total=None,forward=False):
    hashes=[]
    for p in paths:
        try:hashes.append(file_md5(p))
        except OSError:hashes.append('')
    started=time.time()
    timeout_s=_compute_send_timeout_s(paths,forward)
    params=dict(params);params.setdefault('timeout',int(timeout_s*1000))
    try:
        res=await asyncio.wait_for(bot.call_api(action,**params),timeout=timeout_s+5)
        mid=(res or {}).get('message_id') if isinstance(res,dict) else None
        if mid is not None:return {'message_id':mid,'confirmed':'api'}
        raise RuntimeError('OneBot send returned no message_id')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code=getattr(exc,'retcode','')
        logger.warning(f'[Pixiv发送诊断] group={gid} action={action} images={len(paths)} elapsed={time.time()-started:.1f}s error={type(exc).__name__} retcode={code}; no resend')
        if hashes:
            result=await confirm_history(bot,gid,caption,hashes,started,first,total,forward)
            if result:return result
        raise
