"""One short natural opener per group batch; bookkeeping remains in the ledger."""
import asyncio
import random
import re

_FALLBACKS = ('刚看到几张不错的，发来一起看看。', '来，给你们看看这几张。', '挑了些图，看看有没有你喜欢的。')

def clean_opener(value):
    if not isinstance(value, str):
        return ''
    value = value.strip().strip('"“”')
    if not 6 <= len(value) <= 42 or '\n' in value or '\r' in value:
        return ''
    if any(s in value for s in ('确认', '未完成', '断点', '续发', '重发', '画展', '舞台', '盛宴', '作为AI', '画了', '我画', '自己画', '我创作', '我的作品', 'http', '[CQ:', '@')):
        return ''
    if re.search(r'\d', value):
        return ''
    return value

async def make_opener():
    from ...core import llm
    try:
        if not llm.api_key():
            return random.choice(_FALLBACKS)
        async with asyncio.timeout(7):
            content = await llm.chat([
                {'role':'system','content':'为QQ群分享插画写一句自然的开场白，图片是其他作者的作品，绝对不能自称画了这些图。像熟悉的群友随手分享，轻松但不浮夸。只输出一句8至25字的话。不要报数量、排名、日期、处理进度；不要说未完成、确认、续发、画展、舞台、盛宴；不要自我介绍、不要艾特人、不要链接。'},
                {'role':'user','content':'准备发一批插画，请写一句开场白。'},
            ], timeout=6, max_tokens=100, temperature=.9)
            text = clean_opener(content)
            if text:
                return text
    except asyncio.CancelledError:
        raise
    except Exception:
        pass
    return random.choice(_FALLBACKS)
