"""听懂群友发的语音（小鲸鱼「她能听懂语音」）。

- 开关：AI_CHAT_HEAR_VOICE（设置 → 活人感 →「听懂语音」，默认开）；
- 借用 GPT-SoVITS 整合包自带的 FunASR（FURINA_TTS_ENGINE_DIR 里的 runtime/python.exe + tools/asr/models），
  每条语音临时起一个进程转写，转完就退出，不常驻内存；
- 省资源：在玩游戏 / 可用内存低于 AI_CHAT_HEAR_VOICE_MIN_RAM_GB（默认 1.5G）时不听；一次只听一条；
- 每天最多 AI_CHAT_HEAR_VOICE_DAILY_MAX 条（默认 200）；60 秒以上的长语音不听；
- 听不了就当没听清（返回空串），聊天照常进行，不报错不提示。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from nonebot import logger

from ..core import db, env

NS = "voice_asr"
SCRIPT = Path(__file__).with_name("asr_once.py")
_lock = asyncio.Lock()


def enabled() -> bool:
    return env.get_bool("AI_CHAT_HEAR_VOICE", True)


def _engine_dir() -> str:
    return env.get("FURINA_TTS_ENGINE_DIR", "").strip().strip('"')


def _python(engine: str) -> str:
    for rel in (("runtime", "python.exe"), ("runtime", "python"), ("runtime", "bin", "python")):
        p = os.path.join(engine, *rel)
        if os.path.isfile(p):
            return p
    return ""


def available() -> tuple[bool, str]:
    if not enabled():
        return False, "没开"
    engine = _engine_dir()
    if not engine or not os.path.isdir(os.path.join(engine, "tools", "asr", "models")):
        return False, "没找到语音识别模型（FURINA_TTS_ENGINE_DIR）"
    if not _python(engine):
        return False, "没找到整合包的 runtime/python"
    day = time.strftime("%Y-%m-%d")
    used = int((db.get(NS, "day") or {}).get(day) or 0)
    cap = env.get_int("AI_CHAT_HEAR_VOICE_DAILY_MAX", 200)
    if cap > 0 and used >= cap:
        return False, f"今天已经听了 {used} 条"
    try:
        from . import voice_tts
        res = voice_tts.resources()
        if res.get("game"):
            return False, f"正在玩游戏（{res['game']}）"
        need = env.get_float("AI_CHAT_HEAR_VOICE_MIN_RAM_GB", 1.5)
        if need > 0 and 0 <= float(res.get("ram", -1)) < need:
            return False, f"可用内存只剩 {res['ram']}G"
    except Exception:  # noqa: BLE001  取不到资源信息就当没问题
        pass
    return True, ""


def _count() -> None:
    day = time.strftime("%Y-%m-%d")
    db.update(NS, "day", lambda d: {day: int((d or {}).get(day) or 0) + 1})


def parse_output(out: str) -> str:
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and '"text"' in line:
            try:
                return str(json.loads(line).get("text") or "").strip()
            except ValueError:
                continue
    return ""


async def transcribe(path: str, timeout: float = 90.0) -> str:
    """把一个本地音频文件转成文字；听不了 / 失败返回空串。"""
    ok, why = available()
    if not ok:
        logger.info(f"[听语音] 这条没听：{why}")
        return ""
    if not path or not os.path.isfile(path):
        return ""
    engine = _engine_dir()
    async with _lock:
        start = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                _python(engine), str(SCRIPT), path, cwd=engine,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "MODELSCOPE_OFFLINE": "1"},
                creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("[听语音] 转写超时，算没听清")
                return ""
        except OSError as e:
            logger.warning(f"[听语音] 起不来识别进程: {e}")
            return ""
    _count()
    text = parse_output(out.decode("utf-8", "ignore"))
    logger.info(f"[听语音] 用了 {time.time() - start:.1f}s：{text[:60] or '（没听清）'}")
    return text


MAX_WAV_BYTES = 4 * 1024 * 1024  # wav 16k 约 60 秒以上就不听


def _local_file(value) -> str:
    """协议端给的 file/path 如果是本机现成的文件就返回路径；网址、缓存标识一律返回空。"""
    p = str(value or "").strip()
    if not p or p.lower().startswith(("http://", "https://", "base64://", "data:")):
        return ""
    if p.lower().startswith("file://"):
        from urllib.parse import unquote, urlsplit
        p = unquote(urlsplit(p).path)
        if len(p) > 2 and p[0] == "/" and p[2] == ":":
            p = p[1:]
    return p if os.path.isfile(p) else ""


def _save_wav_b64(b64: str) -> str:
    """SnowLuma 的 get_record(out_format=wav) 把转好的音频放在 base64 里：落成临时 wav；太长/坏数据返回空。"""
    import base64
    import binascii
    import tempfile

    raw = str(b64 or "").strip()
    if raw.lower().startswith("base64://"):
        raw = raw[9:]
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        return ""
    if not data:
        return ""
    if len(data) > MAX_WAV_BYTES:
        logger.info("[听语音] 语音太长，不听")
        return ""
    fd, path = tempfile.mkstemp(prefix="furina_asr_", suffix=".wav")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


async def hear_segment(bot, data: dict) -> str:
    """QQ 语音段 → 文字：拿到本地 wav 再转写。

    NapCat：语音段自带本地 path，或 get_record 返回本地 file；
    SnowLuma：语音段只有 file/url，get_record(out_format=wav) 的 file 是下载地址，转好的 wav 在 base64 字段里。
    """
    ok, why = available()
    if not ok:
        logger.info(f"[听语音] 这条没听：{why}")
        return ""
    file = str((data or {}).get("file") or "")
    path = _local_file((data or {}).get("path"))
    temp = ""
    if not path and file:
        try:
            res = await bot.call_api("get_record", file=file, out_format="wav")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[听语音] 取语音文件失败: {e}")
            return ""
        res = res if isinstance(res, dict) else {}
        if res.get("base64"):
            temp = path = await asyncio.to_thread(_save_wav_b64, res.get("base64"))
        else:
            path = _local_file(res.get("file"))
    if not path:
        return ""
    try:
        try:
            if os.path.getsize(path) > MAX_WAV_BYTES:
                logger.info("[听语音] 语音太长，不听")
                return ""
        except OSError:
            return ""
        return await transcribe(path)
    finally:
        if temp:
            try:
                os.remove(temp)
            except OSError:
                pass
