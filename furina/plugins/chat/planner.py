"""先判断、再回复（参考 MaiBot 的 planner → reply(msg_id) → replyer 两步式）。

正式回复前先用一次很短的模型调用理清：这条消息在接谁的话、指代的是什么、该往哪个方向回；
随机接话时还会判断「这时候插嘴合不合适」。结果只作为提示交给正式回复，失败就当没有。
"""
import json
import re

from nonebot import logger

from . import config, llm
from ...core import roles

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_NO_RE = re.compile(r"#\d{1,3}")


def _parse(text: str) -> dict | None:
    m = _JSON_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    related = data.get("related") or []
    if isinstance(related, str):
        related = _NO_RE.findall(related)
    related = [str(x).strip() if str(x).strip().startswith("#") else f"#{str(x).strip()}" for x in related if str(x).strip()]
    topic = str(data.get("topic") or "")
    new_topic = "新" in topic and "接着" not in topic
    if new_topic:
        related = []  # 另起的话题：不挂靠到前面任何一条上，免得把两件事扯到一起
    return {
        "new_topic": new_topic,
        "reply": bool(data.get("reply", True)),
        "related": related[:4],
        "refers": str(data.get("refers") or "").strip()[:80],
        "direction": str(data.get("direction") or "").strip()[:80],
    }


async def plan(transcript_text: str, target_line: str, forced: bool, facts: str = "") -> dict | None:
    if not config.PLANNER:
        return None
    me = roles.display_name()
    if forced:
        task3 = "你要回复它。"
    elif config.CHATTY:
        # 话多一点（大肥鱼式）：不当主持人，但能接就接，别习惯性潜水
        task3 = ("判断你要不要接一句：你是群里话挺多的普通群友，话题你能插上嘴（有想法、有经历、能吐槽、能接梗）就接（reply=true）；"
                 "只有别人两个人明显在聊私事、或者你确实接不上话时才不插（reply=false）。")
    else:
        task3 = "判断你适不适合在旁边插一句：别人两个人在聊私事、话题和你无关、或者你插进去只会尬，就不插（reply=false）。"
    if not forced:
        task3 += "你看不懂这条在说什么（里面的名字或东西你不认识、前后文也推不出来），也不插（reply=false），别靠猜去接。"
    known = ""
    try:  # 新消息里有她认识的梗 / 名字（热词库）：告诉判断的人，免得把不认识的东西乱套到前面的话题上
        from ...services import slang
        hits = slang.find_in(target_line, limit=3)
        if hits:
            known = "【这些词你认识】" + "；".join(f"{t}：{m}" for t, m in hits) + "\n\n"
    except Exception:  # noqa: BLE001
        known = ""
    messages = [
        {"role": "system", "content": f"你在帮群聊角色「{me}」理清群里的对话关系。只输出一个 JSON 对象，不要任何其他文字。"},
        {"role": "user", "content": (
            f"【群聊记录（从旧到新，#是编号，“你”是{me}自己）】\n{transcript_text or '（暂无）'}\n\n"
            f"【新消息】\n{target_line}\n\n{known}{(facts.strip() + chr(10) + chr(10)) if facts else ''}"
            "请判断：\n"
            "0. 话题：群里经常几个话题同时在聊，时间挨着不等于说的是同一件事。看内容判断这条是「接着聊」前面某个话题，还是「新话题」："
            "说的是同一个东西 / 人 / 事或同一类东西（比如前面在聊桌宠，这条说另一款桌宠），或者明显在回应、追问、引用前面某句，就是接着聊；"
            "内容和前面哪条都对不上，就是新话题。别因为时间挨着，就把它硬套到你自己刚才说的话上；\n"
            "1. 这条新消息在接哪几条消息（写编号，最多 4 个；新话题就空着）；\n"
            "2. 说话人话里的「我/你/他/这个/那个」和提到的名字分别指谁或什么：你确实知道才写；不认识就写「不知道」，别把它猜成前面聊过的某样东西；\n"
            f"3. {task3}\n"
            "4. 回复方向：一句话说清该接什么、别扯到哪去，不要写回复原文；只挑一个点（别写「A或B」「既…又…」，真人一句话只回一个反应）。"
            "新话题就只围绕新消息本身；里面有你不认识的东西，就按字面接或者直接问一句是啥，别编。\n"
            '输出：{"topic": "接着聊/新话题", "reply": true, "related": ["#3"], "refers": "……", "direction": "……"}'
        )},
    ]
    try:
        raw = await llm.chat(messages, timeout=config.PLANNER_TIMEOUT, max_tokens=200, temperature=0.2)
    except Exception as e:  # noqa: BLE001  规划失败不影响正常回复
        logger.warning(f"[AI聊天] 对话关系判断失败，按原流程回复: {e}")
        return None
    result = _parse(raw)
    if result is None:
        logger.info(f"[AI聊天] 对话关系判断结果无法解析，忽略: {(raw or '')[:80]}")
    else:
        logger.info(f"[AI聊天] 对话关系: {'新话题' if result['new_topic'] else '接着聊'} reply={result['reply']} "
                    f"related={result['related']} 方向={result['direction'][:40]}")
    return result


def note(result: dict | None, index: dict) -> str:
    """把判断结果写成给正式回复看的提示。"""
    if not result:
        return ""
    lines = ["【对话关系（内部分析，只作参考：不要照抄，不要在回复里提编号）】"]
    if result.get("new_topic"):
        lines.append("这是另起的新话题：和前面聊的（包括你自己刚才那段）不是一回事，别扯到一起，也别把里面没见过的名字当成前面聊过的东西")
    rel = [f"{k} {index[k]}" for k in result.get("related") or [] if k in index]
    if rel:
        lines.append("TA 在接的消息：" + "；".join(rel))
    if result.get("refers"):
        lines.append("指代：" + result["refers"])
    if result.get("direction"):
        lines.append("回复方向：" + result["direction"])
    return "\n".join(lines) if len(lines) > 1 else ""
