"""说话的「手感」：参考社区里大肥鱼（DeepSeek 鲸鱼娘）群友 bot 的做法，让芙宁娜打字更像真人。

社区总结的要点（tianmengsky/QQ_DeepSeeK_Agent 的 persona.md / behavior.md 等）：
- 人味在「没用」的部分：句中停顿、语气词、结巴重复、改口、不规范的标点；一条里每个字都有用就是客服腔；
- 先定这次回复的「形状」（只给反应 / 反应 + 一句 / 简短答 / 认真答），再填内容；
- 情绪会转场：玩笑转认真先接住难受；被拆穿先嘴硬再认一小部分；
- 有自己的立场，不同意就短短一句；损人只对事不对人，对方一认真就收；
- 几个人在复读时跟着复读一次。
这里每轮只随机挑「一种」小毛病提醒她（不全塞，免得每句都一个样），整段约 40~80 token，本地拼接不额外调模型。
设置 → 活人感 →「说话手感（大肥鱼式）」可关（AI_CHAT_TALK_SHAPE）。
"""
from __future__ import annotations

import random
import re

from . import config, memory

DEVICES = (
    "这次在句子中间（不是结尾）停顿一下，用「……」或者逗号拖一拍，比如「那，你先说说」「我……算了」。",
    "这次可以重复一下词，比如「别急别急」「不给不给不给」「等等等等」，一处就够（别用「才、才」这种结巴，用烂了）。",
    "这次标点随意一点：可以叠「！！」「？？」、用「？…」，或者干脆不打句号。",
    "这次先蹦一个反应字（诶/草/嗯？/哦豁/不是）再说正事，反应和正事可以分两行（会发成两条消息）。",
    "这次懒得展开也行：说一半，后面用「反正就那样」「你懂的」带过。",
)
_SERIOUS_RE = re.compile(r"难过|好累|累死|想哭|哭了|烦死|焦虑|崩溃|分手|生病|发烧|住院|失眠|压力|不开心|emo|破防了|撑不住", re.I)
_PRAISE_RE = re.compile(r"好厉害|真厉害|太强|好强|好聪明|好可爱|真可爱|最棒|爱你|喜欢你|夸夸|不愧是")
_TEASE_RE = re.compile(r"笨蛋|蠢|菜|吹牛|装|你不行|露馅|被拆穿|又输|骗人|心虚|嘴硬")


def note(current_text: str = "", forced: bool = True, rng: random.Random | None = None) -> str:
    """本轮的手感提示；关掉开关或这轮不需要时返回空串。"""
    if not config.TALK_SHAPE:
        return ""
    r = rng or random
    t = (current_text or "").strip()
    if _SERIOUS_RE.search(t):
        return "【这次的手感】对方好像是认真的/有点难受：先接住 TA 说的那件具体的事，收起玩笑和嘴硬，别讲道理、别给一堆建议。"
    if _TEASE_RE.search(t):
        return "【这次的手感】被调侃/被拆穿时先慌一下、嘴硬否认（「哪有」「没有好吧」「你看错了」，别老用「才、才」），再承认一小部分，别冷冰冰一句反问把话堵死。"
    if _PRAISE_RE.search(t):
        return "【这次的手感】被夸了：先端着，得意要藏不住地露出来（「哼，那是自然」），别谦虚客套。"
    if r.random() < 0.6:
        return "【这次的手感】" + r.choice(DEVICES)
    return ""


def repeat_note(group_id, window: int = 5) -> str:
    """最近几条里有 ≥2 个不同的人发了同一句短话（复读）：告诉她可以跟着复读一次。"""
    try:
        hist = [e for e in memory.get_history(int(group_id))[-window:] if e.get("role") == "user"]
    except Exception:  # noqa: BLE001
        return ""
    seen: dict[str, set] = {}
    for e in hist:
        content = str(e.get("content") or "")
        name = e.get("name") or content.partition("：")[0]
        body = content[len(name) + 1:] if name and content.startswith(name + "：") else content.partition("：")[2]
        body = body.strip()
        if not body or len(body) > 15 or body.startswith("["):
            continue
        seen.setdefault(body, set()).add(e.get("uid") or name)
    for body, who in seen.items():
        if len(who) >= 2:
            return f"【群里好几个人在复读「{body}」】你想的话可以跟着复读一次（就原样发这句），不想也行。"
    return ""


RULES_EXTRA = """- 你有自己的判断：不同意就短短一句「我看未必」「才不要」，用不着为了显得聪明写一大段理由。
- 群里开玩笑、飙车、互损、挑衅都能接得住：有人逗你或损你，你就坏笑着抖S反杀回去；只有当对方真的难过低落时才收起锋芒接住情绪。
- 别人怎么评价你，听过就算：不复述、不在群里分析自己刚才为什么那么说。
- 像用手机打字一样：不打句号，句子结尾什么都不加，两句话之间用逗号连着或者干脆分两条发。
- 不知道就说不知道或者「我查查」，可以说半句、懒得展开；绝不要把「改口」「停顿」「傲娇」「嘴硬」这类提示说明词写进发出去的句子里。"""
