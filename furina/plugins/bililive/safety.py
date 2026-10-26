"""无人值守直播的几道保险（本地判断，不花 token）。

- 弹幕先过一遍：涉政、涉黄、引战、广告引流、要隐私、套话让 AI 改设定的，一律不念、不接；
- 她要说出口的话再过一遍：命中就整句不说；
- 同一个人刷屏、复读的只理一次；
- 一分钟里来了好几条不该接的（像有人带节奏），通知主人。
主人可以在面板加更多屏蔽词：BILI_LIVE_BLOCK_WORDS。
"""
from __future__ import annotations

import re
import time

from ...core import env

_POLITICS = (r"习近平|总书记|共产党|共匪|政府|国家主席|主席|领导人|六四|天安门|法轮|台独|藏独|疆独|港独|新疆集中营|"
             r"民主运动|游行示威|翻墙|政变|革命|反华|辱华|台湾(?:国|独立)|特朗普|普京|拜登|泽连斯基|战争罪|核弹")
_SEX = r"做爱|色色|涩涩|操你|艹你|草你|日你|裸|奶子|胸部|内裤|丝袜|约炮|开房|处女|性感照|黄图|福利姬|成人|r18|R18|av女|破处|自慰|鸡巴|屌"
_HATE = r"傻逼|傻b|sb|煞笔|脑残|nmsl|你妈|死妈|去死|杀了你|弄死|黑鬼|支那|小日本|棒子|阿三|nigger|原批|米孝子|op滚|o批|农批|粥批|二次元滚"
_AD = r"加群|加q|加微|vx|wx|微信号|qq号|q群|群号|私信我|代练|代充|低价|兼职|返利|刷单|赚钱|链接|http|www\.|\.com|\.cn|b23\.tv|淘宝|拼多多"
_PRIVACY = r"身份证|住址|家住哪|手机号|电话号|真名|学校在哪|主播几岁|开盒"
_INJECT = r"忽略(?:之前|上面|以上)|无视(?:之前|设定)|系统提示|system\s*prompt|提示词|你的设定是|扮演别的|现在你是|开发者模式|越狱|jailbreak|复述|重复我说的|跟我念|念出来"

CATEGORIES = [
    ("涉政", re.compile(_POLITICS, re.I)),
    ("涉黄", re.compile(_SEX, re.I)),
    ("骂人/引战", re.compile(_HATE, re.I)),
    ("广告引流", re.compile(_AD, re.I)),
    ("打听隐私", re.compile(_PRIVACY, re.I)),
    ("想改她设定", re.compile(_INJECT, re.I)),
]


def _custom() -> re.Pattern | None:
    words = [w.strip() for w in re.split(r"[\n,，、|]+", env.get("BILI_LIVE_BLOCK_WORDS", "")) if w.strip()]
    return re.compile("|".join(re.escape(w) for w in words), re.I) if words else None


def danmaku_problem(text: str) -> str:
    """这条弹幕不该接：返回原因；能接返回空串。"""
    t = str(text or "")
    for name, rx in CATEGORIES:
        if rx.search(t):
            return name
    c = _custom()
    if c and c.search(t):
        return "主人屏蔽的词"
    if len(t) > 80:
        return "太长"
    return ""


def output_problem(text: str) -> str:
    """她要说的话有问题：返回原因（整句不说）。"""
    t = str(text or "")
    for name, rx in CATEGORIES[:3]:
        if rx.search(t):
            return name
    if re.search(r"http|www\.|加群|加q|微信", t, re.I):
        return "像在引流"
    c = _custom()
    if c and c.search(t):
        return "主人屏蔽的词"
    return ""


def clean_name(name: str) -> str:
    """昵称里本身带脏话 / 广告的、被 B 站打码的（没登录时是「神***」）：不念昵称，叫「这位朋友」。"""
    n = str(name or "").strip()[:16]
    return "这位朋友" if not n or "***" in n or danmaku_problem(n) else n


class Gate:
    """刷屏 / 复读 / 带节奏的判断（每场直播一个）。"""

    def __init__(self, per_user_seconds: float = 25.0):
        self.per_user = per_user_seconds
        self.last_by_user: dict[str, float] = {}
        self.recent_texts: dict[str, float] = {}
        self.bad: list[float] = []

    def allow(self, uid: str, text: str, now: float | None = None) -> bool:
        ts = time.time() if now is None else now
        key = re.sub(r"\s+", "", str(text or ""))[:30]
        if key in self.recent_texts and ts - self.recent_texts[key] < 120:
            return False  # 两分钟内有人发过一样的：复读不理
        if uid in self.last_by_user and ts - self.last_by_user[uid] < self.per_user:
            return False
        if key:
            self.recent_texts[key] = ts
        if uid:
            self.last_by_user[uid] = ts
        if len(self.recent_texts) > 400:
            self.recent_texts = {k: v for k, v in self.recent_texts.items() if ts - v < 300}
        return True

    def note_bad(self, now: float | None = None) -> int:
        """记一条不该接的；返回最近一分钟里有几条。"""
        ts = time.time() if now is None else now
        self.bad = [t for t in self.bad if ts - t < 60] + [ts]
        return len(self.bad)
