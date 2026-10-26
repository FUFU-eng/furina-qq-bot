"""AI 意图识别：判断群友艾特机器人时是否在“词条/设定/外号/标签”的增删查。

单独用一次小输出预算的模型调用，让 AI 自己理解自然语言，返回 JSON；
识别不到就返回 None，主流程继续正常聊天，不阻塞回复。
"""

import json
import re

from nonebot import logger

from . import llm
from ...core import roles

_ACTIONS = {"add", "delete", "query", "none"}

_SYSTEM_PROMPT = """你是 QQ 群机器人的意图分类器。群友刚艾特了机器人，这句话可能是在要求记住、删除或查询某个人的词条、设定、外号、标签、档案。

请先理解这句话的真实意图，然后只输出一个 JSON，不要输出解释、不要加代码块外的文字。

JSON 格式：
{"action":"add" / "delete" / "query" / "none","target":"人名","value":"内容"}

判断规则：
- add：对方在给某个人添加或更新词条、设定、外号、标签、档案。value 填要记住的内容。
- delete：对方要删除某个人（或某条）词条、设定、外号、标签。value 留空表示全部删，填了表示只删这一条。
- query：对方在问某个人有什么词条、设定、外号、标签。value 留空。
- none：普通聊天、开玩笑、闲聊、问别的问题。只要不是明确的记忆操作，一律 none，不要过度解读。
  特别注意：只要句子里出现“好感度/好感/亲密度/关系值/你喜不喜欢TA/TA在你心里/你们关系”这类情感词，不管是问“我的”还是“某某的”、不管什么句式，都属于好感/情感话题，一律 none；绝对不要把“好感度”“亲密度”当成要查询的词条名，也不要对这类句子返回 query 或提取 target。

target 规则：
- 消息里艾特了谁，那个人就是最明确的目标：例如“@张三 的词条是什么”target 填“张三”，不能填“我”。
- 填对方提到的人名，必须逐字照抄原话，禁止加字、漏字、改字、换同音字，也不要带标点；
  拿不准原话里的人名时 action 返回 none。
- 只有对方明确是在说“我自己的词条”时，target 才填“我”（系统会替换成当前昵称）；
  问的是别人（即使原话里只有“词条是什么”没写名字）时不能填“我”。
- 对方说的是机器人自己（{SELF_WORDS}等）时，target 填“{SELF_NAME}”。
- 从整句话里提取真正的人名，例如“给阿白加入的设定：xxx”target 是“阿白”，“删除对象'小木屑'的所有词条”target 是“小木屑”。
- 无法确定 target 时 action 返回 none。

value 规则：
- add 时把要记住的内容单独提取出来，去掉“帮我记住”“给他加上”“设定是”这类引导语。
- 内容里带明显疑问（是什么、怎么、哪些）时说明是 query，不是 add。
- 内容不是设定而是闲聊时 action 返回 none。

结合最近的聊天：
- 下面会附上群里最近几条聊天（「机器人」是你自己刚说过的话，包括刚才执行的「记住了…」「删掉啦…」回执）。
- 原话指代不明时（「把设定去掉」「刚才那条删了」「别听他的，撤回」），按最近聊天判断指的是哪条：
  例如机器人刚回过「记住了，张三的设定：xxx」，有人说「把设定去掉」，就是 delete，target=张三，value=xxx。
- delete 时只说了要删的内容、没说是谁的（「删除xxx」），先从最近聊天里找这条内容属于谁填 target、内容填 value；
  找不到时 target 填内容本身、value 留空。"""


def _system_prompt() -> str:
    """把"机器人自己"的称呼换成当前角色的（芙宁娜角色与原文一致）。"""
    words = roles.self_words()
    shown = words[:6] if roles.is_furina() else words
    return (_SYSTEM_PROMPT.replace("{SELF_WORDS}", "、".join(shown))
            .replace("{SELF_NAME}", roles.display_name()))


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    # 去掉模型可能加的 ```json ... ``` 代码块
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    # 优先整段解析，失败再抓第一对花括号
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _clean_text(value) -> str:
    return (value or "").strip().strip("“”\"'「」『』 ，,。！？!?~～ ")


def _clean_target(raw: str, nickname: str) -> str:
    t = (raw or "").strip().strip("“”\"'「」『』 ，,。！？!?~～")
    if t in ("我", "我自己", "说话人"):
        return nickname or ""
    # 机器人自己统一成当前角色名（芙宁娜角色仍是"芙宁娜"）
    if any(k in t for k in roles.self_words()):
        return roles.display_name()
    return t


async def detect_memory_action(gid, nickname: str, text: str, mentioned: list[str] | None = None,
                               context: str = "") -> dict | None:
    """返回 {"action","target","value"}；普通聊天/识别失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    messages = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": (f"群里最近的聊天（旧→新）：\n{context}\n\n" if context else "")
            + f"当前说话人昵称：{nickname}\n本条消息里还艾特了这些人：{'、'.join(mentioned) if mentioned else '（无）'}\n对方艾特机器人说的原话：{text}\n请判断意图并只输出 JSON。",
        },
    ]
    try:
        content = await llm.chat(messages, timeout=30, max_tokens=160)
    except Exception as e:
        logger.warning(f"[记忆意图] 意图识别调用失败，按普通聊天处理: {e}")
        return None

    data = _extract_json(content)
    if not data:
        logger.info(f"[记忆意图] 模型输出无法解析，按普通聊天处理: {(content or '')[:80]!r}")
        return None

    action = str(data.get("action", "")).strip().lower()
    if action not in _ACTIONS or action == "none":
        return None

    target = _clean_target(data.get("target", ""), nickname)
    value = _clean_text(data.get("value", ""))
    if not target or len(target) > 20 or len(value) > 120:
        logger.info(f"[记忆意图] 识别结果缺少有效目标，按普通聊天处理: {data}")
        return None

    logger.info(f"[记忆意图] 群{gid} 识别为 {action}，target={target}，value={value[:40]}")
    return {"action": action, "target": target, "value": value}
