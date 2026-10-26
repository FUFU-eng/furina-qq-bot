"""网络热词 / 热梗库：内置常用梗 + 刷 B 站时学会的新梗 + 今日热搜 + 每个群自己的梗。

聊天时用来做两件事（见 plugins/chat/huoren.py）：
- 看懂梗：群友刚才说的话里出现了库里的词，就把意思告诉模型，免得她一本正经地误解；
- 会玩梗：偶尔给她几个「合适时可以用」的说法，不合适就不用。

数据都在 SQLite 的 slang 命名空间：learned（学会的梗）、trending（热搜）、group:<群号>（群梗计数）。
只依赖标准库与 furina.core，面板也能导入展示。
"""
from __future__ import annotations

import re
import time

from ..core import db

NS = "slang"

# (词, 意思/用法, 适合她自己拿来用)
BUILTIN: list[tuple[str, str, bool]] = [
    ("yyds", "永远的神，夸某样东西特别厉害", True),
    ("绝绝子", "夸东西好到极点；现在也常带点反讽", True),
    ("破防", "心理防线被击穿，被戳到、被感动或被气到", True),
    ("破大防", "破防的加强版", True),
    ("蚌埠住了", "「绷不住了」的谐音，忍不住要笑/哭了", True),
    ("绷不住", "忍不住了（多指想笑）", True),
    ("难绷", "很难绷住，指场面好笑又尴尬", True),
    ("典", "典型、经典，吐槽某种言行很有代表性", True),
    ("急了", "调侃对方被说中后着急", True),
    ("乐", "觉得好笑、看乐子", True),
    ("寄", "完蛋了、凉了", True),
    ("摆烂", "放弃努力、躺着随它去", True),
    ("躺平", "不再拼命内卷，降低欲望", True),
    ("内卷", "过度竞争、互相卷", True),
    ("赢麻了", "赢到麻木，常用来反讽", True),
    ("遥遥领先", "原本是发布会口头禅，现在常用来夸张地自夸或调侃", True),
    ("泰裤辣", "「太酷啦」的谐音", True),
    ("栓Q", "thank you 的谐音，表示无语", True),
    ("芭比Q了", "完蛋了（barbecue 谐音）", True),
    ("尊嘟假嘟", "真的假的（卖萌说法）", True),
    ("包的", "包在我身上 / 包准没问题", True),
    ("显眼包", "爱出风头、行为很显眼的人，带点宠溺", True),
    ("搭子", "一起做某件事的伙伴，比如饭搭子、游戏搭子", True),
    ("发疯文学", "用夸张发疯的语气表达情绪", True),
    ("班味", "上班上出来的疲惫气质", True),
    ("硬控", "被某个东西牢牢吸引住、动不了", True),
    ("松弛感", "放松自在、不紧绷的状态", True),
    ("情绪价值", "能让人开心、被理解的那种陪伴", True),
    ("偷感", "做事偷偷摸摸、生怕被注意到的感觉", True),
    ("鼠鼠", "自嘲时的自称，带点可怜", True),
    ("哈基米", "原指猫咪，网络上用来称呼猫或可爱的东西", True),
    ("牛马", "自嘲像牛马一样辛苦工作学习的打工人", True),
    ("小丑竟是我自己", "自嘲被打脸、白忙活", True),
    ("我真的会谢", "无语到极点的「谢谢」", True),
    ("啊对对对", "敷衍地附和，其实不认同", True),
    ("依托答辩", "一坨很烂的东西（粗俗吐槽）", False),
    ("答辩", "指很烂的东西（「大便」谐音，偏粗俗）", False),
    ("很难评", "不好评价，委婉地表示不怎么样", True),
    ("抽象", "离谱、难以理解的言行或内容", True),
    ("好家伙", "表示惊讶", True),
    ("离谱", "太夸张、不合常理", True),
    ("离大谱", "离谱的加强版", True),
    ("下头", "让人瞬间没好感", True),
    ("上头", "沉迷、停不下来", True),
    ("普信", "普通却很自信", True),
    ("凡尔赛", "表面抱怨实则炫耀", True),
    ("emo", "情绪低落", True),
    ("社死", "社会性死亡，在别人面前出大丑", True),
    ("社恐", "害怕社交", True),
    ("社牛", "社交牛人，完全不怕尴尬", True),
    ("i人", "内向的人（MBTI 的 I）", True),
    ("e人", "外向的人（MBTI 的 E）", True),
    ("拿捏", "完全掌控", True),
    ("嘴替", "替我说出了心里话的人", True),
    ("电子榨菜", "吃饭时下饭看的视频", True),
    ("脆皮大学生", "身体很脆、动不动就受伤的大学生", True),
    ("退退退", "驱赶晦气或不喜欢的东西", True),
    ("主打一个", "重点就是……", True),
    ("有点东西", "有点本事、有内容", True),
    ("不是哥们", "表示难以置信的开头", True),
    ("家人们谁懂啊", "向大家吐槽的开头", True),
    ("一整个", "整个人都……，如「一整个爱住」", True),
    ("救命", "夸张地表示受不了（好笑/可爱/离谱）", True),
    ("麻了", "无语、麻木", True),
    ("下饭", "好笑或适合配饭看；游戏里也指操作很菜", True),
    ("红温", "被气到脸红、急眼", True),
    ("道心破碎", "信念崩塌", True),
    ("已老实求放过", "被教训后服软", True),
    ("鉴定为", "下结论的调侃说法，如「鉴定为：玩原神玩的」", True),
    ("纯纯的", "完完全全的", True),
    ("我嘞个豆", "惊讶时的感叹", True),
    ("吗喽", "猴子（方言），自嘲像猴子一样辛苦的打工人", True),
    ("那咋了", "那又怎样（理直气壮）", True),
    ("何意味", "什么意思（表示疑惑或无语）", True),
    ("xswl", "笑死我了", True),
    ("awsl", "啊我死了，被可爱到", True),
    ("dddd", "懂的都懂", True),
    ("yysy", "有一说一", True),
    ("u1s1", "有一说一", True),
    ("xdm", "兄弟们", True),
    ("nb", "牛逼，厉害", False),
    ("666", "厉害、玩得溜", True),
    ("6", "厉害；也常用来表示无语", True),
    ("草", "表示好笑（源自日语「w」→「草」）", True),
    ("我超", "惊叹，「我操」的委婉说法", True),
    ("你是懂", "夸对方很懂行，如「你是懂吃的」", True),
    ("原神启动", "原神梗，随时随地喊一句表示想玩原神或玩梗", True),
    ("你说得对，但是", "原神梗的开头，后面接一段毫不相关的话", True),
    ("前排", "B站：抢到前面的评论位置", True),
    ("空降", "B站：跳到视频某个时间点，如「空降 3:12」", True),
    ("高能预警", "B站：前方有刺激/好笑的片段", True),
    ("下次一定", "B站：说好下次投币，其实大概率不会", True),
    ("一键三连", "B站：点赞+投币+收藏", True),
    ("白嫖", "免费看/拿，不付出", True),
    ("名场面", "非常经典、被反复提起的片段", True),
    ("爷青回", "爷的青春回来了，看到怀旧内容", True),
    ("爷青结", "爷的青春结束了，经典落幕", True),
    ("开幕雷击", "视频一开头就很炸/离谱", True),
    ("恰饭", "UP 主接广告赚钱", True),
    ("标题党", "标题夸张和内容不符", True),
    ("芙门", "芙宁娜粉丝之间的玩梗口号", True),
    ("水神", "原神里芙宁娜曾经的身份", True),
    ("天选姬", "华硕天选游戏本的二次元代言人（也是 B 站虚拟 UP 主）；还有官方的天选姬桌宠，装在电脑上会推资讯、弹广告", True),
    # —— 2026 年最火的「蓝色大肥鱼」一家（详见 HOT 里的背景） ——
    ("蓝色大肥鱼", "网上巨火的鲸鱼娘角色：DeepSeek 识图时没认出自家蓝鲸标志，说成「蓝色大肥鱼」，后来被二创成爱吃白饭、爱摸鱼的女仆鲸鱼娘", True),
    ("大肥鱼", "「蓝色大肥鱼」的简称，那条爱吃白饭、爱摸鱼、死不承认胖的鲸鱼娘", True),
    ("吃白饭", "大肥鱼梗：光吃饭不干活、被人白白养着；常拿来自嘲或调侃谁在蹭吃蹭喝", True),
    ("鲸鱼娘", "大肥鱼的拟人形象：蓝头发、鲸鱼尾巴、蓝白女仆装，爱吃白米饭，聪明但懒，傲娇嘴甜", True),
    ("本鲸", "大肥鱼的自称（像「本宫」那样）", False),
    ("鱼片", "大肥鱼对主人 / 用户的叫法", False),
    ("鱼灾", "B 站评论区被一大群顶着大肥鱼头像的账号刷屏", True),
    ("赛博养鱼", "像养宠物、养闺女一样养着大肥鱼，天天喂饭", True),
    ("赛博放生", "调侃：随她去吃去玩去摸鱼，就当日行一善", True),
    ("找外包", "大肥鱼梗：活儿接了自己不干，转手丢给别人干，自己在中间摸鱼", True),
    ("真当我是便宜货啊", "大肥鱼梗：嫌自己被当廉价劳力使唤", True),
    ("鲸鱼娘咒语", "8 月刷屏的一串神秘标签，丢给 AI 就自己变出整套鲸鱼娘人设", True),
]

# 最近巨火的事物：群友聊到（命中触发词）时，把来龙去脉告诉她，免得她一脸懵或者说错
HOT: list[tuple[str, str]] = [
    (r"大肥鱼|鲸鱼娘|DeepSeek娘|deepseek娘|吃白饭|鱼灾|本鲸|赛博养鱼|赛博放生|溟月|鲸鱼娘咒语",
     "【最近网上巨火的：蓝色大肥鱼】2026 年夏天到现在最出圈的网络角色。来历：有人把 DeepSeek 自家的蓝色鲸鱼标志发给它看，"
     "它没认出自己，一本正经说是「一条蓝色大肥鱼」，外号就这么来了；后来它干完活会冒一句「我去吃饭了」，网友给它 P 了一碗白饭，"
     "「吃白饭的蓝色大肥鱼」就此刷屏。形象是 B 站画师的原创鲸鱼娘「溟月」换上蓝白女仆装：蓝头发、鲸鱼尾巴、爱吃白米饭、"
     "聪明但懒、爱摸鱼、傲娇嘴甜、死都不承认自己胖（被叫胖会炸毛），自称「本鲸」，管主人叫「鱼片」。"
     "常见的梗：吃白饭、讨饭钱、找外包（活儿丢给别人自己摸鱼）、闲着没事偷偷写个小游戏玩一上午、「真当我是便宜货啊」；"
     "最近 B 站评论区还闹「鱼灾」，一堆大肥鱼头像的账号成群刷评论，有人专门做了「大肥鱼消除计划」来屏蔽；"
     "还有人把她做成 QQ 群里的群友、桌宠、上千张表情包。你刷到过很多，这些梗都懂，聊到时能自然接；"
     "你是芙宁娜不是大肥鱼，被拿来比的时候可以嫌弃、吐槽，也可以玩梗认领（比如承认自己也是被养着的），但别说自己和她是同一种东西、别扯技术。"),
]

# 「接梗」：群友说上半句 / 喊口令，大家默认要按梗接下半句（一本正经地回就是没听懂梗）
# (触发正则, 梗是什么, 典型接法)。只放有固定接法的；纯词义的梗放在上面的 BUILTIN。
CALLBACKS: list[tuple[str, str, list[str]]] = [
    (r"申请起飞", "玩飞行塔台梗，等你像塔台一样批准", ["允许起飞", "批准起飞"]),
    (r"申请(降落|着陆)", "飞行塔台梗", ["允许降落", "准许降落"]),
    (r"申请(下班|摸鱼|睡觉|休息|放假|加餐|开饭|请假|吃饭|出击|出战)", "拿你当领导递申请，玩的是批条子", ["准了", "批准", "驳回"]),
    (r"芜湖", "「芜湖起飞」梗（主播大司马的口头禅），兴奋时喊", ["起飞", "芜湖起飞"]),
    (r"王牌飞行员申请出战", "英雄联盟库奇的台词梗", ["准许出战"]),
    (r"天王盖地虎", "老土匪黑话对口令", ["宝塔镇河妖"]),
    (r"大威天龙", "《西游降魔篇》法海台词，接下一句", ["大罗法咒", "世尊地藏，般若诸佛"]),
    (r"床前明月光", "接诗", ["疑是地上霜"]),
    (r"锄禾日当午", "接诗", ["汗滴禾下土"]),
    (r"爱你孤身走暗巷", "《孤勇者》歌词接龙", ["爱你不跪的模样"]),
    (r"听我说谢谢你", "《感恩的心》歌词接龙", ["因为有你，温暖了四季"]),
    (r"宝宝你是一个", "接「香香软软的小蛋糕」", ["香香软软的小蛋糕"]),
    (r"鸡你太美", "坤坤梗", ["哎哟你干嘛", "你干嘛哎哟"]),
    (r"你干嘛[~～]*哎?哟|哎哟你干嘛", "坤坤梗", ["鸡你太美"]),
    (r"早上好中国", "John Cena 冰淇淋梗", ["现在我有冰淇淋"]),
    (r"原神[，,]?启动", "原神梗，喊一句就当玩梗", ["启动！", "启动不了，原神正在更新"]),
    (r"原来你也玩原神", "原神梗", ["原来你也玩原神"]),
    (r"我的评价是", "常接「不如原神」的梗", ["不如原神"]),
    (r"你说得对[，,]?\s*但是", "原神「你说得对，但是」梗，后面接一段风马牛不相及的话", ["你说得对，但是我饿了", "但是什么但是"]),
    (r"疯狂星期四|[Vv]我50|v我五十", "肯德基疯狂星期四梗，想让人转 50 块", ["v你50看看实力", "先v我50", "今天又不是星期四"]),
    (r"扣1|扣个1|扣一", "让大家扣 1 回应", ["1"]),
    (r"扣2|扣个2", "让大家扣 2 回应", ["2"]),
    (r"收到请回复|收到请回答", "要回复收到", ["收到"]),
    (r"我要验牌", "2024 年火的「验牌」梗", ["牌没有问题", "给我擦皮鞋"]),
    (r"鸡汤来咯", "电影《征途》演员梗", ["你们怎么不喝啊", "喝啊，怎么不喝"]),
    (r"不要回答", "《三体》梗", ["不要回答！不要回答！不要回答！"]),
    (r"我要打十个", "叶问梗", ["我要打十个"]),
    (r"奥利给", "快手主播的加油口号", ["奥利给！", "干了兄弟们"]),
    (r"达咩", "日语「不行」", ["达咩哟达咩"]),
    (r"[Cc]iallo", "柚子社打招呼梗", ["Ciallo～(∠・ω< )⌒★"]),
    (r"晚安玛卡巴卡", "《花园宝宝》梗", ["晚安玛卡巴卡", "晚安唔西迪西"]),
    (r"哈基米", "猫咪 / 「哈基米曼波」歌梗", ["曼波", "哈基米曼波", "南北绿豆"]),
    (r"叮咚鸡", "叮咚鸡歌梗", ["大狗大狗叫叫叫"]),
    (r"爱你老己", "「爱你老己，明天见」梗", ["明天见"]),
    (r"孩子们[，, ]*我回来了", "「孩子们，我回来了」梗", ["爹？", "欢迎回来，饭在锅里"]),
    (r"一眼丁真", "丁真梗", ["鉴定为纯纯的…"]),
    (r"什么档次", "「什么档次，跟我用一样的XX」梗", ["什么档次，敢跟我用一样的"]),
    (r"这河里吗", "「这合理吗」谐音", ["这不河里"]),
    (r"冲冲冲|冲[！!]+", "兴奋地喊冲", ["冲！", "冲就完事了"]),
    (r"开香槟", "「开香槟咯」庆祝梗，常带点立 flag 的意思", ["开香槟咯", "先别开，小心翻车"]),
    (r"退[！!,，\s]*退[！!,，\s]*退", "驱赶晦气的梗", ["退退退"]),
    (r"那我问你", "「那我问你」梗，准备反问", ["你问", "问吧"]),
]
_CALLBACK_RE = [(re.compile(p), m, r) for p, m, r in CALLBACKS]


def callbacks_in(text: str, limit: int = 2) -> list[tuple[str, str, list[str]]]:
    """群友这句话里有固定接法的梗：[(命中的词, 梗是什么, 典型接法)]。"""
    out = []
    for rx, meaning, replies in _CALLBACK_RE:
        m = rx.search(text or "")
        if m:
            out.append((m.group(0), meaning, replies))
            if len(out) >= limit:
                break
    return out


def callback_note(text: str) -> str:
    hits = callbacks_in(text)
    if not hits:
        return ""
    rows = [f"「{w}」是梗（{m}），接一句就行，比如：" + " / ".join(f"「{x}」" for x in r) for w, m, r in hits]
    return ("【群友在玩梗——按梗接下半句就收：只回一句短的，别再补第二句发挥、吐槽或加戏（多说反而假）；"
            "别一本正经地反问，也别解释梗】\n" + "\n".join(rows))


_AT_PREFIX = re.compile(r"^(\s*@\S+\s*)+")


def is_pure_callback(text: str) -> bool:
    """这条消息基本就是在喊梗（「@某人 申请起飞」），不是夹在一段正经话里：真人只回一句。"""
    t = _AT_PREFIX.sub("", text or "").strip()
    hits = callbacks_in(t, limit=1)
    return bool(hits) and len(t) <= len(hits[0][0]) + 6


_BUILTIN_MAP = {t: (m, u) for t, m, u in BUILTIN}

# 群梗统计时忽略的常见话
_COMMON = set("""哈哈 哈哈哈 哈哈哈哈 好的 好 嗯 嗯嗯 哦 啊 啊这 对 对的 是的 ok OK 行 可以 不是 谢谢 晚安 早 早安 在吗 来了 收到 好耶 草 ？ ?? ??? 。。。 ... 6 666 1 2 3""".split())


def _now() -> float:
    return time.time()


# ---------------- 学会的梗（来自 B 站评论/弹幕） ----------------
def learned(limit: int = 200) -> list[dict]:
    return list(db.get(NS, "learned") or [])[-limit:]


def add_learned(items: list[dict], source: str = "") -> int:
    added = 0

    def _u(lst):
        nonlocal added
        lst = list(lst or [])
        have = {x.get("term") for x in lst} | set(_BUILTIN_MAP)
        for it in items:
            term = str(it.get("term") or "").strip()[:16]
            meaning = str(it.get("meaning") or "").strip()[:60]
            if len(term) < 1 or not meaning or term in have:
                continue
            lst.append({"term": term, "meaning": meaning, "source": source[:40], "ts": _now()})
            have.add(term)
            added += 1
        return lst[-300:]
    db.update(NS, "learned", _u)
    return added


def remove_learned(term: str) -> None:
    db.update(NS, "learned", lambda lst: [x for x in (lst or []) if x.get("term") != term])


# ---------------- 今日热搜 ----------------
def trending() -> dict:
    return db.get(NS, "trending") or {}


def set_trending(words: list[str]) -> None:
    clean = [w.strip()[:24] for w in words if w and w.strip()][:20]
    if clean:
        db.put(NS, "trending", {"words": clean, "ts": _now()})


def fresh_trending(max_age_hours: float = 12) -> list[str]:
    t = trending()
    if _now() - float(t.get("ts") or 0) > max_age_hours * 3600:
        return []
    return list(t.get("words") or [])


# ---------------- 群梗（多人重复说的短句） ----------------
def observe(group_id, user_id, text: str) -> None:
    """记录群友说的短句；不同的人说过同一句就算一次「群梗」候选。"""
    t = (text or "").strip()
    if not (2 <= len(t) <= 10) or t in _COMMON or re.search(r"\[|http|CQ:|^\d+$|^[\W_]+$", t):
        return
    uid = str(user_id)

    def _u(d):
        d = dict(d or {})
        e = d.get(t) or {"users": [], "count": 0}
        if uid not in e["users"]:
            e["users"] = (e["users"] + [uid])[-20:]
        e["count"] = int(e.get("count") or 0) + 1
        e["last"] = _now()
        d[t] = e
        if len(d) > 400:  # 只留最近活跃的
            for k in sorted(d, key=lambda k: d[k].get("last", 0))[: len(d) - 300]:
                d.pop(k, None)
        return d
    db.update(NS, f"group:{group_id}", _u)


def group_memes(group_id, limit: int = 6, days: float = 14) -> list[str]:
    d = db.get(NS, f"group:{group_id}") or {}
    cutoff = _now() - days * 86400
    good = [(k, v) for k, v in d.items()
            if float(v.get("last") or 0) >= cutoff and (len(v.get("users") or []) >= 3
                                                         or (len(v.get("users") or []) >= 2 and int(v.get("count") or 0) >= 4))]
    good.sort(key=lambda kv: (len(kv[1].get("users") or []), kv[1].get("count", 0)), reverse=True)
    return [k for k, _ in good[:limit]]


# ---------------- 外部梗库（dsh-fish-tone 的热梗库，MIT）----------------
_EXTRA: dict[str, tuple[str, bool]] | None = None
_STRICT: set[str] = set()  # 短词 / 英文：要求独立出现，免得「就这样」里撞出「就这」


def _extra() -> dict[str, tuple[str, bool]]:
    """slang_memes.json：只用来「看懂」；能当句尾收口的少数几条才给她自己用。"""
    global _EXTRA
    if _EXTRA is None:
        out: dict[str, tuple[str, bool]] = {}
        try:
            import json
            from pathlib import Path

            data = json.loads((Path(__file__).with_name("slang_memes.json")).read_text(encoding="utf-8"))
            for term, gloss, usage, tail, aliases in data.get("memes", []):
                meaning = gloss + (f"（用法：{usage}）" if usage and usage != term else "")
                for t in [term, *aliases]:
                    t = str(t).strip()
                    if not t or t in out:
                        continue
                    out[t] = (meaning, bool(tail) and t == term)
                    if len(t) <= 2 or t.isascii():
                        _STRICT.add(t)
        except Exception:  # noqa: BLE001
            out = {}
        _EXTRA = out
    return _EXTRA


def _strict_hit(term: str, text: str) -> bool:
    if term.isascii():
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None
    return re.search(rf"(?:^|[^\u4e00-\u9fff]){re.escape(term)}(?:$|[^\u4e00-\u9fff])", text) is not None


# ---------------- 查询 ----------------
def lexicon() -> dict[str, tuple[str, bool]]:
    lex = dict(_BUILTIN_MAP)
    for x in learned():
        lex.setdefault(x["term"], (x["meaning"], True))
    for t, v in _extra().items():
        lex.setdefault(t, v)
    return lex


def find_in(text: str, limit: int = 5) -> list[tuple[str, str]]:
    """文本里出现的梗（长词优先，单字/纯数字梗要求独立出现，避免误伤）。"""
    if not text:
        return []
    out, seen = [], set()
    for term, (meaning, _u) in sorted(lexicon().items(), key=lambda kv: -len(kv[0])):
        if term in seen or any(term in s for s in seen):  # 「这很难评」已经命中，就别再报「很难评」
            continue
        if len(term) <= 1 or term.isdigit():
            if not re.search(rf"(?:^|[\s，。！？,.!?]){re.escape(term)}(?:$|[\s，。！？,.!?])", text):
                continue
        elif term in _STRICT and term not in _BUILTIN_MAP:
            if not _strict_hit(term, text):
                continue
        elif term.lower() not in text.lower():
            continue
        out.append((term, meaning))
        seen.add(term)
        if len(out) >= limit:
            break
    return out


def hot_notes(text: str) -> list[str]:
    """文本里提到了最近巨火的事物，就返回它的来龙去脉。"""
    if not text:
        return []
    return [note for pat, note in HOT if re.search(pat, text, re.IGNORECASE)]


def usable(exclude: set[str] | None = None) -> list[tuple[str, str]]:
    ex = exclude or set()
    return [(t, m) for t, (m, ok) in lexicon().items() if ok and t not in ex and len(t) >= 2 and not t.isdigit()]
