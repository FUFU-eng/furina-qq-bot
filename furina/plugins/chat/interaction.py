"""Conservative local interaction signals, not a general sentiment classifier.

Ambiguous teasing, quotations, negation and third-person reports are neutral.
No LLM call, no permissions derived from relationship scores.
"""
import hashlib
import re
import unicodedata

from ...core import roles


def clean_text(text):
    t = unicodedata.normalize('NFKC', str(text or ''))[:2000]
    t = re.sub(r'\[(?:CQ:)?(?:at|reply|image|face)[^\]]*\]', '', t)
    t = re.sub(r'https?://\S+', '', t)
    return t.strip()


def fingerprint(text):
    t = ''.join(c.lower() for c in clean_text(text) if c.isalnum())
    t = re.sub(r'(.)\1{2,}', r'\1\1', t)
    return hashlib.sha256(t.encode()).hexdigest() if len(t) >= 2 else ''


def _names_alt():
    """你 / 芙芙 / 芙宁娜 + 当前角色的称呼，拼成正则分组内容。"""
    names = ['你', '芙芙', '芙宁娜']
    try:
        names += [n for n in roles.aliases() if n not in names]
    except Exception:
        pass
    return '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True))


def _call_alt():
    names = ['芙芙', '芙宁娜']
    try:
        names += [n for n in roles.aliases() if n not in names]
    except Exception:
        pass
    return '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True))


def sentiment(text):
    """Only clear statements directed at the bot earn a positive/negative signal."""
    t = clean_text(text)
    if not t or re.search(r'[?？]|[“”「」『』\"\']|(?:他说|她说|别人说|有人说|比如|例如|如果|假如|不是|不算|不觉得|并不|没有|别说|别叫|不要叫|别骂)', t):
        return 0
    t = re.sub(r'\s+', '', t).strip('，。!！~～')
    # Do not punish ordinary criticism, jokes, illness, or incidental substrings.
    you, call = _names_alt(), _call_alt()
    negative = rf'(?:{you})(?:就是|是|真是|这个|个)?(?:个|一个)?(?:废物|白痴|弱智|傻逼|垃圾)(?:[，。!！]|$)|^(?:你给我)?(?:滚开|滚远点|去死)(?:[，。!！]|$)|^讨厌你$'
    positive = rf'^(?:谢谢你?|辛苦了|抱抱|亲亲)(?:呀|啊|啦|哦|{call})*$|^(?:我)?(?:喜欢你|爱你)(?:呀|啊|啦|哦)*$|(?:{you})(?:真的|真|好|很|太|超|最|非常)*(?:可爱|厉害|棒|漂亮|好看|温柔|贴心)(?:了|呀|啊|啦|哦)*$'
    if re.search(negative, t):return -1
    if re.search(positive, t):return 1
    return 0
