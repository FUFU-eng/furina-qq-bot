"""连发合并：同一个人在群里连着发几条时，等 TA 说完再一起回（不一条条追着回）。

被艾特后先开一个「会话」：TA 接下来几秒内发的消息都并进来；
生成回复期间 TA 又补了话，handlers 会丢掉旧稿、带上新话重新生成一次。
"""
import asyncio
import re
import time
from dataclasses import dataclass, field

from . import config


@dataclass
class Turn:
    group_id: int
    user_id: str
    started: float = field(default_factory=time.monotonic)
    last: float = field(default_factory=time.monotonic)
    extras: list = field(default_factory=list)  # [{"text", "has_image", "mid", "images"}]

    def texts(self) -> list[str]:
        return [x["text"] for x in self.extras if x.get("text")]

    def mids(self) -> list[str]:
        return [str(x["mid"]) for x in self.extras if x.get("mid")]

    def images(self) -> list:
        out = []
        for x in self.extras:
            out.extend(x.get("images") or [])
        return out

    def has_image(self) -> bool:
        return any(x.get("has_image") for x in self.extras)


_open: dict[tuple, Turn] = {}


def _key(group_id, user_id) -> tuple:
    return int(group_id), str(user_id)


def is_open(group_id, user_id) -> bool:
    return _key(group_id, user_id) in _open


def open_turn(group_id, user_id) -> Turn | None:
    """开一个合并会话；同一人已有会话在进行时返回 None。"""
    k = _key(group_id, user_id)
    if k in _open:
        return None
    t = Turn(int(group_id), str(user_id))
    _open[k] = t
    return t


def append(group_id, user_id, text: str, has_image: bool = False, mid=None, images=None) -> bool:
    """有进行中的会话就把这条并进去并返回 True。"""
    t = _open.get(_key(group_id, user_id))
    if t is None:
        return False
    t.extras.append({"text": (text or "").strip(), "has_image": bool(has_image), "mid": mid, "images": list(images or [])})
    t.last = time.monotonic()
    return True


def close(turn: Turn | None) -> None:
    if turn is not None and _open.get(_key(turn.group_id, turn.user_id)) is turn:
        del _open[_key(turn.group_id, turn.user_id)]


# 话说一半的结尾（参考 qq-bridge v2-wait.js）：等 TA 说完再回
UNFINISHED_TAIL = re.compile(r"(?:你知道|等一下|等下|我跟你讲|我跟你说|其实吧|但是|所以说|然后|那个|就是|我想说|对了|等我|等等|"
                             r"我看看|还有|主要是|毕竟|因为|所以|但是吧|话说|听我说|你听我说)$")
UNFINISHED_PUNCT = re.compile(r"[，、；：,;:]$")
FINISHED_TAIL = re.compile(r"[。！？!?…～~]+$")
PING = re.compile(r"^(?:在吗|在不在|在么|在嘛|人呢|你在吗|出来|醒醒|喂)[?？!！。~～]*$")
NUDGES = ("啥", "然后呢", "你说啊", "嗯？")


def looks_unfinished(text: str) -> bool:
    s = (text or "").strip()
    if not s or FINISHED_TAIL.search(s):
        return False
    return bool(UNFINISHED_TAIL.search(s) or UNFINISHED_PUNCT.search(s))


def _last_text(first: str, turn: Turn) -> str:
    texts = turn.texts()
    return texts[-1] if texts else (first or "")


def quiet_window(first: str, turn: Turn) -> tuple[float, float]:
    """这次该等多久：(安静秒数, 最长总等待)。
    - 只是「在吗」这种点名：1.5 秒就回（先应一声，别让人以为你不在）；
    - 话明显没说完：安静 12 秒才算完，最长等 AI_CHAT_UNFINISHED_WAIT_SECONDS（默认 90）；
    - 15 秒内连发了 3 条以上：安静 12 秒；
    - 其余：AI_CHAT_MERGE_SECONDS（默认 6）。"""
    base, cap = config.MERGE_SECONDS, config.MERGE_MAX_SECONDS
    if base <= 0:
        return 0.0, 0.0
    last = _last_text(first, turn).strip()
    if not turn.extras and PING.match(last):
        return min(base, 1.5), min(cap, 3.0)
    if looks_unfinished(last):
        return max(base, 12.0), max(cap, config.UNFINISHED_WAIT_SECONDS)
    if len(turn.extras) >= 2 and time.monotonic() - turn.started <= 15:
        return max(base, 12.0), max(cap, 30.0)
    return base, cap


async def wait_quiet(turn: Turn, window: float | None = None, max_total: float | None = None, first: str = "") -> bool:
    """等到对方 window 秒没再发消息（最长 max_total 秒）。不传 window 时按 quiet_window 自适应。
    返回 True 表示等到头了 TA 的话还是像没说完（可以催一句「啥」）。"""
    adaptive = window is None and max_total is None
    if not adaptive:
        window = config.MERGE_SECONDS if window is None else window
        max_total = config.MERGE_MAX_SECONDS if max_total is None else max_total
    while True:
        if adaptive:
            window, max_total = quiet_window(first, turn)
        if window <= 0:
            return False
        now = time.monotonic()
        quiet_left = window - (now - turn.last)
        total_left = max_total - (now - turn.started)
        if quiet_left <= 0:
            return False
        if total_left <= 0:
            return adaptive and looks_unfinished(_last_text(first, turn))
        await asyncio.sleep(min(quiet_left, total_left, 0.5))


UNFINISHED_NOTE = ("【TA 话没说完】TA 最后一句明显没说完（停在「{tail}」），你等了一会儿 TA 也没补。"
                   "这次就像真人一样催一句，一两个字就够（比如「{nudge}」），别替 TA 把话说完、别长篇回应前面的内容。")


def unfinished_note(first: str, turn: Turn | None, rng=None) -> str:
    import random as _r
    r = rng or _r
    last = _last_text(first, turn) if turn else (first or "")
    return UNFINISHED_NOTE.format(tail=last.strip()[-8:], nudge=r.choice(NUDGES))


def combined_text(first: str, turn: Turn | None) -> str:
    parts = [p for p in [(first or "").strip()] + (turn.texts() if turn else []) if p]
    return "\n".join(parts)
