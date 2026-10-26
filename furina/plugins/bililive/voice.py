"""直播用的声音：微软免费的 AI 女声（edge-tts），不用芙宁娜原声（避开角色声音侵权）。

合成失败（没网 / 没装 edge-tts）就返回 None，舞台上照样出字幕。
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path  # noqa: F401

from nonebot import logger

from ...core import env, paths

VOICES = {
    "zh-CN-XiaoyiNeural": "晓伊（活泼少女，默认）",
    "zh-CN-XiaoxiaoNeural": "晓晓（温柔）",
    "zh-CN-XiaohanNeural": "晓涵（知性）",
    "zh-CN-liaoning-XiaobeiNeural": "晓北（东北话）",
    "zh-TW-HsiaoChenNeural": "曉臻（台湾腔）",
}
_KEEP_SECONDS = 1800


def cache_dir() -> Path:
    d = paths.DATA_DIR / "bililive_audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup(d: Path) -> None:
    now = time.time()
    for f in d.glob("*.mp3"):
        try:
            if now - f.stat().st_mtime > _KEEP_SECONDS:
                f.unlink()
        except OSError:
            pass


async def synth(text: str) -> tuple[str, float] | None:
    """合成一句：返回 (文件名, 秒数)；失败返回 None。"""
    try:
        import edge_tts
    except ImportError:
        logger.warning("[B站直播] 没装 edge-tts，只出字幕不出声")
        return None
    voice = env.get("BILI_LIVE_VOICE", "zh-CN-XiaoyiNeural") or "zh-CN-XiaoyiNeural"
    rate = env.get("BILI_LIVE_RATE", "+5%") or "+0%"
    pitch = env.get("BILI_LIVE_PITCH", "+6Hz") or "+0Hz"
    d = cache_dir()
    _cleanup(d)
    name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.mp3"
    audio, end = bytearray(), 0
    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        async for ch in comm.stream():
            if ch["type"] == "audio":
                audio += ch["data"]
            elif "offset" in ch:
                end = max(end, ch["offset"] + ch.get("duration", 0))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[B站直播] 合成语音失败（只出字幕）：{e}")
        return None
    if not audio:
        return None
    (d / name).write_bytes(bytes(audio))
    secs = end / 1e7 if end else max(1.5, len(text) * 0.22)
    return name, secs + 0.3
