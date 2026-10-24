"""Named image requests, deliberately separate from unqualified Eagle requests."""
import random
import re
import unicodedata
from dataclasses import dataclass

ALIASES = {
    '神里绫华': ['神里綾華', '神里绫华', 'KamisatoAyaka'],
    '绫华': ['神里綾華', '神里绫华', 'KamisatoAyaka'],
    '原神': ['原神', 'GenshinImpact'],
    '芙宁娜': ['フリーナ', '芙宁娜', 'Furina'],
    '芙芙': ['フリーナ', '芙宁娜', 'Furina'],
    '雷电将军': ['雷電将軍', '雷电将军', 'RaidenShogun'],
    '纳西妲': ['ナヒーダ', '纳西妲', 'Nahida'],
    '胡桃': ['胡桃(原神)', '胡桃', 'HuTao'],
    '甘雨': ['甘雨(原神)', '甘雨', 'Ganyu'],
    '刻晴': ['刻晴', 'Keqing'],
    '八重神子': ['八重神子', 'YaeMiko'],
    '珊瑚宫心海': ['珊瑚宮心海', '珊瑚宫心海'],
    '宵宫': ['宵宮', '宵宫', 'Yoimiya'],
    '妮露': ['ニィロウ', '妮露', 'Nilou'],
    '崩坏星穹铁道': ['崩壊スターレイル', '崩坏星穹铁道', 'HonkaiStarRail'],
    '崩坏：星穹铁道': ['崩壊スターレイル', '崩坏星穹铁道', 'HonkaiStarRail'],
    '星穹铁道': ['崩壊スターレイル', '崩坏星穹铁道', 'HonkaiStarRail'],
    '崩坏3': ['崩壊3rd', '崩坏3', 'HonkaiImpact3rd'],
    '绝区零': ['ゼンレスゾーンゼロ', '绝区零', 'ZenlessZoneZero'],
    '明日方舟': ['アークナイツ', '明日方舟', 'Arknights'],
    '碧蓝档案': ['ブルーアーカイブ', '碧蓝档案', 'BlueArchive'],
    '鸣潮': ['鳴潮', '鸣潮', 'WutheringWaves'],
}
AMBIGUOUS = {'雷神': '你指的是原神的「雷电将军」吗？请用完整角色名再点一次图。'}
GENERIC = {'', '张', '幅', '部', '点', '好看', '好看的', '美', '漂亮', '漂亮的', '随机', '二次元', '涩', '色', '高清'}
NUM = r'[0-9零〇一二两三四五六七八九十百几些]+'


@dataclass(frozen=True)
class Request:
    query: str
    count: int
    clamped: bool = False


def number(text):
    if not text or text in ('几', '些'):
        return random.randint(1, 3)
    if text.isdigit():
        return int(text)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    digits['两'] = 2
    if text in digits:
        return digits[text]
    if '百' in text:
        return 100
    if '十' in text:
        a, b = text.split('十', 1)
        return digits.get(a, 1) * 10 + digits.get(b, 0)
    return 1


def parse_request(text):
    text = unicodedata.normalize('NFKC', text).strip()
    text = re.sub(r'\[(?:CQ:)?(?:at|reply)[^\]]*\]', '', text).strip()
    text = re.sub(r'^(?:请|麻烦|帮我|给我|请给我|能不能|可以|你)?\s*', '', text)
    m = re.fullmatch(rf'(?:来|发|给|找|搜|要|求)(?:给我|我)?\s*(?P<n>{NUM})?\s*(?:张|幅|部)?\s*(?P<q>.+?)\s*(?:的)?(?:图片|插画|壁纸|图)\s*(?:吧|呗|呀|啊|谢谢)?[!！。~～?？]*', text)
    if not m:
        return None
    query = m['q'].strip().removesuffix('的').strip('「」"“” ')
    if query in GENERIC or len(query) > 80 or '\n' in query:
        return None
    count = number(m['n']) if m['n'] else (1 if re.match(r'(?:来|发|给|找|搜|要|求)(?:给我|我)?\s*(?:张|幅|部)', text) else random.randint(1, 3))
    return Request(query, max(1, min(10, count)), count > 10 or count < 1)


def queries_for(query, custom=None):
    if custom and query in custom:
        return list(dict.fromkeys(custom[query] + [query]))[:4]
    return list(dict.fromkeys(ALIASES.get(query, [query])))[:4]
