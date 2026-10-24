"""调用上游插件的原处理函数，并把它们的输出收集起来（不直接发出）。

上游函数签名都是 ``async def handler(bot, ev)``，内部用 ``bot.send`` / ``bot.send_option``
出图。这里用 CaptureBot 代替真正的 Bot：输出先收进列表，由 FurinaMys 统一合并、
过滤按钮后再一次性发到群里。
"""
from __future__ import annotations

import sys
import copy
from typing import Any, Dict, List, Tuple, Optional
from dataclasses import field, dataclass

from PIL import Image

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event, Message
from gsuid_core.segment import MessageSegment

PLUGIN_OF = {"gs": "GenshinUID", "sr": "StarRailUID", "zzz": "ZZZeroUID", "bbb": "BBBUID"}
DROP_TYPES = {"buttons", "template_buttons", "markdown", "template_markdown", "image_size", "log"}

_module_cache: Dict[Tuple[str, str], Any] = {}


def find_module(game: str, sub: str) -> Optional[Any]:
    """在 sys.modules 里按「插件名 + 子模块名」找到已加载的上游模块。"""
    key = (game, sub)
    mod = _module_cache.get(key)
    if mod is not None:
        return mod
    plugin = PLUGIN_OF[game]
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        parts = name.split(".")
        if parts[-1] == sub and plugin in parts:
            _module_cache[key] = module
            return module
    return None


def find_func(game: str, sub: str, func: str):
    mod = find_module(game, sub)
    return getattr(mod, func, None) if mod is not None else None


@dataclass
class Output:
    segments: List[Message] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def images(self) -> List[Message]:
        return [m for m in self.segments if m.type == "image"]

    @property
    def texts(self) -> List[str]:
        return [str(m.data).strip() for m in self.segments if m.type == "text" and str(m.data).strip()]

    @property
    def has_image(self) -> bool:
        return bool(self.images)

    @property
    def empty(self) -> bool:
        return not self.segments

    def text(self) -> str:
        return "\n".join(self.texts)


def normalize(message: Any) -> List[Message]:
    """把上游可能 send 的任意类型转换成 Message 列表，并去掉按钮类片段。"""
    out: List[Message] = []
    if message is None:
        return out
    if isinstance(message, Message):
        if message.type in DROP_TYPES or message.data is None:
            return out
        if message.type == "node":
            for item in message.data or []:
                out.extend(normalize(item))
            return out
        if message.type == "text":
            text = str(message.data)
            return [MessageSegment.text(text)] if text.strip() else out
        return [message]
    if isinstance(message, (bytes, bytearray, memoryview)):
        return [MessageSegment.image(bytes(message))]
    if isinstance(message, Image.Image):
        return [MessageSegment.image(message)]
    if isinstance(message, str):
        if message.startswith(("base64://", "file://", "http://", "https://")) and "\n" not in message:
            return [MessageSegment.image(message)] if not message.startswith("http") else [Message("image", f"link://{message}")]
        return [MessageSegment.text(message)] if message.strip() else out
    if isinstance(message, (list, tuple)):
        for item in message:
            out.extend(normalize(item))
        return out
    return [MessageSegment.text(str(message))]


class CaptureBot(Bot):
    """与真实 Bot 共用连接与事件信息，但 send 只记录不发送。"""

    def __init__(self, real: Bot, ev: Event):
        super().__init__(real.bot, ev)
        self.captured: List[Message] = []

    async def send(self, message, at_sender: bool = False, extra_metadata=None, wait_recall: bool = False):  # type: ignore[override]
        self.captured.extend(normalize(message))
        return None

    async def send_option(self, reply=None, option_list=None, *args, **kwargs):  # type: ignore[override]
        self.captured.extend(normalize(reply))
        return None

    async def receive_resp(self, reply=None, option_list=None, *args, **kwargs):  # type: ignore[override]
        self.captured.extend(normalize(reply))
        return None


def make_event(ev: Event, command: str, text: str = "") -> Event:
    """复制原事件，只替换命令与参数；去掉 @ 信息，保证查询的永远是发消息的人自己。"""
    nev = copy.copy(ev)
    nev.command = command
    nev.text = text
    nev.raw_text = f"{command}{text}"
    nev.at = None
    nev.at_list = []
    nev.regex_dict = {}
    nev.regex_group = ()
    return nev


async def call_upstream(bot: Bot, ev: Event, game: str, sub: str, func: str, command: str, text: str = "") -> Output:
    handler = find_func(game, sub, func)
    if handler is None:
        logger.warning(f"[FurinaMys] 未找到上游函数 {PLUGIN_OF[game]}.{sub}.{func}")
        return Output(error="missing")
    nev = make_event(ev, command, text)
    cap = CaptureBot(bot, nev)
    try:
        await handler(cap, nev)
    except Exception as exc:  # 上游异常不能让整条指令崩掉
        logger.exception(f"[FurinaMys] 上游 {sub}.{func} 执行异常: {exc}")
        return Output(segments=cap.captured, error=str(exc) or exc.__class__.__name__)
    return Output(segments=cap.captured)
