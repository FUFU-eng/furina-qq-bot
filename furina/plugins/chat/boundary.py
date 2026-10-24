"""边界感：有人对她动手动脚（「（亲了好几口）」）时态度要硬，说到做到。

用户定的规矩：
- 群主和特别关照的人除外；
- 其他人最多抱抱（抱、贴贴、摸头、牵手、拍拍都算抱抱这一档）；
- 越界记过：亲亲（亲、啵、mua、么么、蹭）一次 1 点；舔、摸身体、扑倒、搞黄色这些重的一次 2 点；
  点数够「警告次数 + 1」（AI_CHAT_BOUNDARY_WARNS，默认 2 → 3 点）就冷落 AI_CHAT_COLD_SHOULDER_HOURS 小时（默认 6），
  也就是亲亲可以警告两次、第三次冷落；重的警告一次、再犯冷落。艾特、戳她、随机接话都不回；7 天不再犯一笔勾销；
- 求情和悔改可以让她原谅（AI_CHAT_FORGIVE）：被冷落的人艾特她 / 叫她名字认错、求情、保证不再犯，攒够诚意她就原谅，
  冷落马上结束；每原谅一次，下次就更难（30 天内：第 1 次一句认错就行，第 2 次要攒 3 点且保证不再犯，
  第 3 次要 6 点还得磨上 10 分钟……）；原谅以后再犯，直接冷落 6 小时。
只看「对她说的话」（艾特、回复她、叫她名字）；群友之间互相开玩笑不管。
开关：AI_CHAT_BOUNDARY（设置 → 活人感 → 边界感）。
"""
from __future__ import annotations

import re
import time

from . import config
from ...core import db

NS = "chat_boundary"
_STRIKE_TTL = 7 * 86400
_DEBOUNCE = 90  # 同一条消息重新生成 / 连发并入时不重复记过
_NOTE_TTL = 180          # 原谅后的提示留给接下来这轮回复用

# 括号、星号里的动作描写：（亲了好几口）*舔了一下* 【扑倒】
_ACTION_RE = re.compile(r"[（(\[【*]([^（）()\[\]【】*]{1,40})[）)\]】*]")
# 「轻」的：亲亲这一档（嫌弃推开，不拉黑）。强吻不算轻
_ACTION_MILD = re.compile(r"(?<!强)亲|啵|(?<!强)吻|mua|么么|蹭", re.I)
_TEXT_MILD = re.compile(
    r"(?:亲|啵|吻)(?:你|芙芙|芙宁娜|一下|一口|一个|个|了一?口|几口|嘴|脸|额头|亲)|(?:让|给|要)我(?:亲|吻|啵)|"
    r"亲了(?:你|芙芙|好几口|一口|几口)|么么哒?|mua", re.I)
# 动作里出现就算越界的字（抱抱那一档不在里面）
_ACTION_BAD = re.compile(
    r"亲|啵|吻|mua|么么|舔|嘬|咬|揉|捏|蹭|舌|扑倒|推倒|压倒|按倒|压在|摁住|按住|骑|掀|扒|脱|解开|伸进|袭胸|拍屁股|"
    r"摸(?!头|摸头|鱼)|上床|抱上床|压床|睡了|侵犯|强吻|强抱|搂腰", re.I)
# 不带括号的直接说法（必须是冲着她的）
_TEXT_BAD = re.compile(
    r"(?:亲|啵|吻|舔|嘬)(?:你|芙芙|芙宁娜|一下|一口|一个|个|了一?口|几口|嘴|脸|额头|亲)|(?:让|给|要)我(?:亲|舔|摸|吻|啵)|"
    r"亲了(?:你|芙芙|好几口|一口|几口)|么么哒?|mua|舔(?!狗|屏)|摸(?:你|了你)(?:的)?(?:腿|胸|屁股|腰|身|脸)?|"
    r"(?:摸|揉|捏)(?:你)?(?:的)?(?:胸|屁股|大腿|腰)|袭胸|扑倒你|推倒你|压倒你|睡你|(?:和|跟|陪)(?:你|我)上床|开房(?!间)|"
    r"涩涩|色色|瑟瑟|搞黄|做爱|doi|r18|啪啪|足交|胖次|内裤|内衣|奶子|脱光|(?:你|芙芙)脱(?:衣服|裙子|掉)|脱(?:你的|掉你的)",
    re.I)


def exempt(uid) -> bool:
    return str(uid or "") in config.UNLIMITED_CHAT_QQ  # 群主 + 特别关照的人


def classify(text: str) -> tuple[str, str]:
    """返回 (级别, 命中的那段)：级别 "heavy" / "mild"；没越界返回 ("", "")。"""
    t = (text or "").strip()
    if not t:
        return "", ""
    mild = ""
    for m in _ACTION_RE.finditer(t):
        inner = m.group(1)
        if _ACTION_BAD.search(inner):
            # 动作里只有亲亲那一档的字 → 轻；还夹着别的（舔、摸、扑倒……）→ 重
            rest = _ACTION_MILD.sub("", inner)
            if _ACTION_MILD.search(inner) and not _ACTION_BAD.search(rest):
                mild = mild or m.group(0)[:20]
                continue
            return "heavy", m.group(0)[:20]
    for m in _TEXT_BAD.finditer(t):
        seg = m.group(0)
        if _TEXT_MILD.search(seg) and not _TEXT_BAD.search(_TEXT_MILD.sub("", seg)):
            mild = mild or m.group(0)[:20]
            continue
        return "heavy", m.group(0)[:20]
    return ("mild", mild) if mild else ("", "")


def offense(text: str) -> str:
    """越界时返回命中的那段（给提示用），没越界返回空串。轻重都算。"""
    return classify(text)[1]


def _state(uid) -> dict:
    st = db.get(NS, str(uid)) or {}
    return st if isinstance(st, dict) else {}


def ignoring(uid, now: float | None = None) -> bool:
    """这个人正在被冷落中。"""
    if not config.BOUNDARY or exempt(uid):
        return False
    now = time.time() if now is None else now
    return float(_state(uid).get("cold_until") or 0) > now


def cold_left_hours(uid, now: float | None = None) -> float:
    now = time.time() if now is None else now
    return max(0.0, (float(_state(uid).get("cold_until") or 0) - now) / 3600)


def _pts(level: str) -> int:
    return 2 if level == "heavy" else 1


def _limit() -> int:
    """记过点数到这个数就冷落：警告次数 + 1（亲亲一次 1 点，重的一次 2 点）。"""
    return max(0, int(config.BOUNDARY_WARNS)) + 1


def check(uid, nickname: str, text: str, now: float | None = None) -> str:
    """对她说话时调用：越界就记过，返回这一轮给她的提示（警告 / 最后警告 / 冷落）；没事返回空串。"""
    if not config.BOUNDARY or exempt(uid):
        return ""
    level, hit = classify(text)
    if not hit:
        return ""
    now = time.time() if now is None else now
    who = nickname or "TA"
    limit = _limit()
    out = {}

    def _u(st):
        st = dict(st or {})
        strikes = int(st.get("strikes") or 0)
        if now - float(st.get("last") or 0) > _STRIKE_TTL:  # 7 天没再犯：一笔勾销
            strikes = 0
            st["probation"] = False
        fresh = now - float(st.get("last") or 0) >= _DEBOUNCE or not strikes
        if fresh:
            strikes += _pts(level)
        st.update(strikes=strikes, last=now, what=hit)
        if nickname:
            st["name"] = nickname
        cold = float(st.get("cold_until") or 0) > now
        if fresh and not cold and (st.get("probation") or strikes >= limit):
            st["cold_until"] = now + max(0.5, float(config.COLD_SHOULDER_HOURS)) * 3600
            st["plea"] = {}
            out["went_cold"] = True
        out.update(st)
        return st

    db.update(NS, str(uid), _u)
    strikes = int(out.get("strikes") or 0)
    if out.get("went_cold") or float(out.get("cold_until") or 0) > now:
        why = "TA 被你原谅过还再犯" if out.get("probation") else "你已经警告过 TA 了"
        return (f"【{who} 又越界了：{hit}】{why}，现在说到做到：冷冷地回一句，说你不理 TA 了"
                f"（用你自己的话，短，别骂人、别解释），之后就真的不理了。"
                f"不害羞、不撒娇、不开玩笑、不顺着 TA 的戏往下演，也别加表情包和语音。")
    last_warn = strikes + 1 >= limit
    warn = ("这是最后一次警告：明确告诉 TA 再有一次就真的不理 TA 了" if last_warn
            else "警告 TA「再这样就不理你了」这个意思")
    if level == "mild":
        return (f"【{who} 想亲你：{hit}】除了群主和特别关照的人，别人不许亲你，最多抱抱。"
                f"嫌弃地推开、明确说不许亲，并且{warn}（用自己的话说，可以带点傲娇）。"
                f"不害羞、不半推半就、不顺着 TA 的戏演，一两句就够。")
    return (f"【{who} 在冒犯你：{hit}】除了群主和特别关照的人，谁都不许对你亲亲、动手动脚、搞黄色，最多抱抱。"
            f"这次态度要硬：明确说不行、让 TA 收手，并且{warn}（用自己的话说）。"
            f"不要害羞、不要撒娇、不要半推半就，也不要顺着 TA 的戏往下演或者给台阶，一两句就够。")


# 认错：对不起 / 我错了 / 不敢了 / 原谅我……
_APOLOGY = re.compile(
    r"对不起|对不住|我错了|错了错了|知道错了|抱歉|sorry|不敢了|原谅我|下次不(?:会|敢)|再也不(?:会|敢)|以后不(?:会|敢)|"
    r"不(?:会|敢)再|是我不对|我的错|给你道歉|道个?歉|饶了我|别生气|消消气|理理我|别不理我", re.I)
# 悔改：保证不再犯
_REPENT = re.compile(
    r"保证|发誓|下次不(?:会|敢)|再也不(?:会|敢)|以后不(?:会|敢)|不(?:会|敢)再|改了|会改|一定改|注意分寸|守规矩|"
    r"只抱抱|最多抱抱|不乱来|不动手动脚|不(?:会|敢)乱", re.I)
# 求情
_PLEAD = re.compile(r"求求|求你|求原谅|原谅我|别不理我|理理我|饶了我|拜托|好芙芙|芙芙大人|芙宁娜大人|最好了|心软", re.I)
_FORGIVE_WINDOW = 30 * 86400  # 原谅次数记 30 天：这段时间里原谅得越多越难原谅
_PLEA_GAP = 30                # 两句求情至少隔 30 秒才算两次（刷屏不算诚意）


def is_apology(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and bool(_APOLOGY.search(t) or _PLEAD.search(t)) and not offense(t)


def forgive_level(uid, now: float | None = None) -> int:
    """30 天里已经原谅过 TA 几次（决定这次多难原谅）。"""
    now = time.time() if now is None else now
    return len([t for t in (_state(uid).get("forgiven") or []) if now - float(t) <= _FORGIVE_WINDOW])


def needs(level: int) -> dict:
    """第 level+1 次原谅要多少诚意：点数（认错 1、带悔改保证 +1、求情 +1）、要不要保证、要求多久。"""
    pts = (level + 1) * (level + 2) // 2          # 1, 3, 6, 10, 15 …
    span = (0, 0, 10, 30, 60)[min(level, 4)] * 60  # 第 3 次起：要磨一段时间，她不会立刻心软
    return {"pts": pts, "repent": level >= 1, "span": span}


def _score(text: str) -> int:
    t = text or ""
    return (1 if (_APOLOGY.search(t) or _PLEAD.search(t)) else 0) + (1 if _REPENT.search(t) else 0) + (1 if _PLEAD.search(t) else 0)


def plead(uid, nickname: str, text: str, now: float | None = None) -> str:
    """被冷落的人对她认错 / 求情：攒诚意，够了就原谅。
    返回 "forgiven"（原谅了）、"softening"（有点心软，回一句但还没原谅）、"noted"（记下了，继续不理）、
    "accepted"（只是被警告过、来道歉：接受）、""（不相干）。"""
    if not config.BOUNDARY or not config.FORGIVE or exempt(uid) or not is_apology(text):
        return ""
    now = time.time() if now is None else now
    st = _state(uid)
    cold = float(st.get("cold_until") or 0) > now
    who = nickname or st.get("name") or ""
    said = (text or "").strip()[:40]
    if not cold:
        warned = int(st.get("strikes") or 0) > 0 and now - float(st.get("last") or 0) <= _STRIKE_TTL
        if not warned or float(st.get("accepted") or 0) >= float(st.get("last") or 0):
            return ""

        def _a(s):
            s = dict(s or {})
            s["accepted"] = now
            s["note"] = {"t": now, "kind": "accepted", "said": said, "name": who}
            return s
        db.update(NS, str(uid), _a)
        return "accepted"

    level = len([t for t in (st.get("forgiven") or []) if now - float(t) <= _FORGIVE_WINDOW])
    need = needs(level)
    out = {}

    def _u(s):
        s = dict(s or {})
        plea = dict(s.get("plea") or {})
        key = re.sub(r"\W+", "", said.lower())
        seen = list(plea.get("seen") or [])
        if key in seen or now - float(plea.get("t") or 0) < _PLEA_GAP:
            out["status"] = "noted"  # 复读 / 刷屏：不加分
            return s
        plea["pts"] = int(plea.get("pts") or 0) + _score(said)
        plea["t"] = now
        plea.setdefault("start", now)
        plea["seen"] = (seen + [key])[-20:]
        if _REPENT.search(said):
            plea["repent"] = True
        ok = (plea["pts"] >= need["pts"] and (plea.get("repent") or not need["repent"])
              and now - float(plea["start"]) >= need["span"])
        if ok:
            s["cold_until"] = 0
            s["probation"] = True   # 原谅以后再犯：直接冷落
            s["forgiven"] = [t for t in (s.get("forgiven") or []) if now - float(t) <= _FORGIVE_WINDOW] + [now]
            s["plea"] = {}
            s["note"] = {"t": now, "kind": "forgiven", "level": level, "said": said, "name": who}
            out["status"] = "forgiven"
        else:
            if level >= 1 and not plea.get("nudged") and plea["pts"] * 2 >= need["pts"]:
                plea["nudged"] = True
                s["note"] = {"t": now, "kind": "softening", "level": level, "said": said, "name": who,
                             "repent": bool(plea.get("repent")) or not need["repent"]}
                out["status"] = "softening"
            else:
                out["status"] = "noted"
            s["plea"] = plea
        if nickname:
            s["name"] = nickname
        return s

    db.update(NS, str(uid), _u)
    return out.get("status", "noted")


def forgive(uid, nickname: str, text: str, now: float | None = None) -> bool:
    """兼容旧接口：这句认错让她原谅了 TA。"""
    return plead(uid, nickname, text, now) == "forgiven"


def pop_forgive_note(uid, now: float | None = None) -> str:
    """刚原谅了 / 有点心软 / 接受道歉：这一轮回复用的提示（只用一次）。"""
    now = time.time() if now is None else now
    st = _state(uid)
    n = st.get("note")
    if not isinstance(n, dict):
        return ""
    st.pop("note", None)
    db.put(NS, str(uid), st)
    if now - float(n.get("t") or 0) > _NOTE_TTL:
        return ""
    who = n.get("name") or "TA"
    said = f"「{n.get('said')}」" if n.get("said") else ""
    kind = n.get("kind") or ("forgiven" if n.get("cold") else "accepted")
    level = int(n.get("level") or 0)
    if kind == "forgiven":
        if level == 0:
            mood = "带点傲娇地原谅（「哼，这次就算了」「下不为例」这种意思，用自己的话）"
        elif level == 1:
            mood = "TA 求了好一阵你才松口，原谅得不太情愿（「看在你这么诚心的份上……」这种意思），顺带说清楚再犯就直接不理"
        else:
            mood = (f"这已经是这个月第 {level + 1} 次原谅 TA 了，原谅得很勉强、语气偏冷（「最后一次，真的」这种意思），"
                    f"别给好脸色")
        return (f"【{who} 跟你认错求情{said}】TA 之前对你动手动脚，你说到做到不理 TA 了；现在你决定原谅 TA。"
                f"{mood}。不说教、不翻旧账，也别一下子变得很热情，一两句就够。")
    if kind == "softening":
        lack = "" if n.get("repent") else "，而且 TA 还没保证以后不再犯"
        return (f"【{who} 在求你原谅{said}】TA 已经不是第一次犯了{lack}。你还在气头上，有一点点心软但没打算马上原谅："
                f"冷淡地回一句（「光嘴上说有什么用」「看你表现」这种意思，用自己的话），别原谅、别说教，一句就够；之后接着不理 TA。")
    return (f"【{who} 为刚才冒犯你道歉了{said}】你接受，傲娇地翻篇（「知道错了就好」这种意思，用自己的话），"
            f"但警告还算数。不说教、不翻旧账，一两句就够。")


def _name_in(history, uid) -> str:
    """从这个群最近的记录里找 TA 的名字；TA 最近没在这个群说过话就返回空串。"""
    for e in reversed(list(history or [])[-80:]):
        if isinstance(e, dict) and e.get("role") == "user" and str(e.get("uid") or "") == str(uid):
            name = str(e.get("name") or "")
            if not name and "：" in str(e.get("content") or ""):
                name = str(e["content"]).split("：", 1)[0][:24]
            return name or "TA"
    return ""


def cold_note(history, now: float | None = None) -> str:
    """她正在冷落、而且最近在这个群出现过的人：告诉她这是她自己故意的，有人问起要认，别装不知道。"""
    if not config.BOUNDARY:
        return ""
    now = time.time() if now is None else now
    lines = []
    for uid, st in (db.items(NS) or {}).items():
        if not isinstance(st, dict) or exempt(uid) or float(st.get("cold_until") or 0) <= now:
            continue
        seen = _name_in(history, uid)
        if not seen:
            continue
        name = st.get("name") or seen
        hours = (float(st["cold_until"]) - now) / 3600
        left = f"还要再冷 TA 大概 {hours:.0f} 小时" if hours >= 1 else "快到时间了"
        what = f"（{str(st.get('what') or '').strip('（）()')}）" if st.get("what") else ""
        lines.append(f"{name}：TA 对你动手动脚{what}，你警告过还再犯，所以你说到做到、故意不理 TA 了，{left}")
    if not lines:
        return ""
    return ("【你正在故意冷落的人】" + "；".join(lines[:3]) + "。"
            "TA 说的话、戳你，你其实都看见了，只是故意不回。有人问起（是不是拉黑了、怎么不理 TA），就大大方方承认是你故意不理，"
            "因为 TA 冒犯了你；别装不知道，也别说没看到、没顾上、在忙。语气可以冷淡、带点小脾气，不用骂人；没人提就别主动说。")


def reset(uid) -> None:
    db.delete(NS, str(uid))
