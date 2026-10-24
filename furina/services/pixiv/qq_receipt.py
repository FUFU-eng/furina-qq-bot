"""Single QQ send plus bounded read-only confirmation, NEVER a send retry."""
import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from nonebot import logger


def parts(value):
    if isinstance(value, dict):
        if value.get('type') in ('text', 'image', 'json', 'forward'):
            yield value
        for child in value.values():
            yield from parts(child)
    elif isinstance(value, list):
        for child in value:
            yield from parts(child)


def image_hash(segment):
    name = str(segment.get('data', {}).get('file', '')).replace('\\', '/').rsplit('/', 1)[-1]
    match = re.fullmatch(r'([0-9a-fA-F]{32})(?:\.[a-zA-Z0-9]+)?', name)
    return match[1].lower() if match else ''


def exact_content(value, caption, hashes, first, total, forward):
    items = list(parts(value))
    texts = [x.get('data', {}).get('text', '') for x in items if x.get('type') == 'text']
    images = [x for x in items if x.get('type') == 'image']
    if (caption and caption not in texts) or len(images) != len(hashes):
        return False
    if [image_hash(x) for x in images] != hashes:
        return False
    if forward:
        expected = [f'第 {i}/{total} 页' for i in range(first, first+len(hashes))]
        labels = [t for t in texts if re.fullmatch(r'第 \d+/\d+ 页', t)]
        if labels != expected:
            return False
    return True


def forward_id(message):
    for part in message:
        data = part.get('data', {})
        if part.get('type') == 'forward':
            return data.get('id') or data.get('res_id') or ''
        if part.get('type') == 'json':
            try:
                card = json.loads(data.get('data', '{}'))
                if card.get('app') == 'com.tencent.multimsg':
                    return card.get('meta', {}).get('detail', {}).get('resid', '')
            except (ValueError, TypeError):
                pass
    return ''


async def confirm_history(bot, gid, caption, hashes, started, first=1, total=1, forward=False, budget=120):
    end = time.monotonic()+budget
    inspected = set()
    while time.monotonic() < end:
        try:
            async with asyncio.timeout(min(20, max(.1, end-time.monotonic()))):
                data = await bot.get_group_msg_history(group_id=int(gid), count=100,
                                                       parse_mult_msg=False, _timeout=15)
                for message in data.get('messages', []):
                    mid = message.get('message_id')
                    if not mid or mid in inspected:
                        continue
                    if (str(message.get('user_id', message.get('sender', {}).get('user_id', ''))) != str(bot.self_id)
                        or str(message.get('group_id', '')) != str(gid)
                        or not started-5 <= float(message.get('time', 0)) <= time.time()+5):
                        continue
                    content = message.get('message', [])
                    if not isinstance(content, list):
                        continue
                    if forward:
                        fid = forward_id(content)
                        if not fid:
                            continue
                        nested = await bot.get_forward_msg(message_id=fid, _timeout=15)
                        content = nested.get('messages', [])
                    if exact_content(content, caption, hashes, first, total, forward):
                        logger.info(f'[Pixiv回执核验] group={gid} message_id={mid} images={len(hashes)} verified=history+caption+MD5+pages')
                        return {'message_id': mid, 'verified_by': 'history'}
                    # A forward may still be propagating: inspect it again later.
                    if not forward:
                        inspected.add(mid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f'[Pixiv回执核验] group={gid} read_error={type(exc).__name__}')
        await asyncio.sleep(min(10, max(0, end-time.monotonic())))
    return None


def _hashes(paths):
    result=[]
    for p in paths:
        h=hashlib.md5()
        with Path(p).open('rb') as f:
            for block in iter(lambda:f.read(1024*1024), b''):
                h.update(block)
        result.append(h.hexdigest())
    return result


async def send_once(bot, gid, caption, paths, action, payload, first=1, total=1, forward=False):
    hashes = await asyncio.to_thread(_hashes, paths) if paths else []
    started = time.time()
    try:
        result = await bot.call_api(action, **payload, _timeout=660)
        if isinstance(result, dict) and result.get('message_id') not in (None, 0, '0', ''):
            return result
        raise RuntimeError('QQ_SEND_ACK_WITHOUT_MESSAGE_ID')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = getattr(exc, 'retcode', '')
        logger.warning(f'[Pixiv发送诊断] group={gid} action={action} images={len(paths)} elapsed={time.time()-started:.1f}s error={type(exc).__name__} retcode={code}; no resend')
        if hashes:
            result = await confirm_history(bot, gid, caption, hashes, started, first, total, forward)
            if result:
                return result
        raise
