"""芙宁娜的声音：调用本机 GPT-SoVITS（api.py 接口）把一句话合成 wav。

- 引擎目录、音色权重、参考音频全部来自 .env（FURINA_TTS_*），代码里不写死任何路径；
- 引擎在后台常开（无窗口）：内存够就一直开着，随叫随到；内存/显存紧张或在打游戏时主动关掉，宽裕了再自动开回来；
- 只依赖标准库 + httpx，面板/测试也能导入（导入时不会启动任何东西）。

.env 键：
  FURINA_TTS_ENABLED       总开关（默认关）
  FURINA_TTS_ENGINE_DIR    GPT-SoVITS 整合包目录（里面有 runtime/python.exe 和 api.py）
  FURINA_TTS_GPT           GPT 权重 .ckpt
  FURINA_TTS_SOVITS        SoVITS 权重 .pth
  FURINA_TTS_REF_WAV       参考音频（3~10 秒，路径用纯英文）
  FURINA_TTS_REF_TEXT      参考音频里说的原话
  FURINA_TTS_PORT          引擎端口（默认 9881）
  FURINA_TTS_AUTOSTART     引擎没开时是否自动拉起（默认开）
  FURINA_TTS_KEEP_ALIVE    常开：内存够就一直开着，关掉了也会在宽裕时自动开回来（默认开）
  FURINA_TTS_IDLE_MINUTES  自己拉起的引擎闲置多久后关掉（默认 0 = 不因闲置关）
  FURINA_TTS_SPEED         语速（默认 1.0）
  FURINA_TTS_SAMPLE_STEPS  v3/v4 模型的采样步数（4/8/16/32，默认 32；越小越快、越省显存，音质略降）
  FURINA_TTS_SOFTEN        压一压气声 / 齿音（0~1，默认 0.35；0 = 原声）
  FURINA_TTS_TAIL_PAD_MS   句尾补多少毫秒静音（默认 450）：QQ 转码时会截掉最后一小段，不补就「吞尾字」

多种语气（同一个模型，换参考音频就换语气，不多占内存）：
  FURINA_TTS_EMOTIONS              按每句话的情绪挑参考音频（默认开）
  FURINA_TTS_REF_WAV / _TEXT       平静（默认那段）
  FURINA_TTS_REF_PROUD_WAV / _TEXT 神气（得意、宣布、表演）
  FURINA_TTS_REF_DAILY_WAV / _TEXT 日常（随口闲聊）
  FURINA_TTS_REF_TSUN_WAV / _TEXT  傲娇（嘴硬、哼）
  没配的语气自动退回平静那段。

省内存（16G 内存也能放心打游戏、跑 AI）：
  FURINA_TTS_MIN_FREE_RAM_GB     可用内存低于这么多就不开引擎（默认 4：引擎加载要吃 3G 左右，0 = 不检查）
  FURINA_TTS_MIN_FREE_COMMIT_GB  可用「提交内存」低于这么多就不开引擎（默认 10：引擎启动要预留约 8~9G）
  FURINA_TTS_MIN_FREE_VRAM_GB    显存剩余低于这么多就不开引擎（默认 2.5，0 = 不检查）
  FURINA_TTS_PAUSE_FOR_GAMES     检测到在玩游戏时不开引擎，已开着的也关掉（默认开）
  FURINA_TTS_GAME_PROCESSES      哪些进程算游戏（逗号分隔的 exe 名，有默认清单）
  开着时每 30 秒查一次，任一项低于下面的「让路线」就主动关掉（引擎自己占着内存，所以这几条比开机门槛低）：
  FURINA_TTS_STOP_FREE_RAM_GB     可用内存低于这么多就关（默认 1）
  FURINA_TTS_STOP_FREE_COMMIT_GB  可用提交内存低于这么多就关（默认 1）
  FURINA_TTS_STOP_FREE_VRAM_GB    显存剩余低于这么多就关（默认 0.8）
  条件不满足时这条就发文字，不影响聊天。
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time

import httpx

from ..core import env, paths

try:  # 机器人进程里用 NoneBot 的日志；面板/测试里退回标准 logging
    from nonebot import logger as _log
except Exception:  # pragma: no cover
    _log = logging.getLogger("furina.voice")

_proc: subprocess.Popen | None = None
_lock: asyncio.Lock | None = None
_state = {"last_use": 0.0, "last_fail": 0.0, "started_at": 0.0, "yielded_at": 0.0, "keep_task": None}
FAIL_BACKOFF = 300.0  # 拉起失败后这么久内不再重试，免得每条消息都去拉一次
# 只在句末切分：按逗号切会把一句话拆成好几段分别合成，每段结尾都容易被「吞」掉、语气也断
CUT_PUNC = "。？！；;?!…"
_END_PUNC = "。？！?!…"

# 语气：key -> (中文名, .env 里参考音频键, 原话键)
EMOTIONS = {
    "calm": ("平静", "FURINA_TTS_REF_WAV", "FURINA_TTS_REF_TEXT"),
    "proud": ("神气", "FURINA_TTS_REF_PROUD_WAV", "FURINA_TTS_REF_PROUD_TEXT"),
    "daily": ("日常", "FURINA_TTS_REF_DAILY_WAV", "FURINA_TTS_REF_DAILY_TEXT"),
    "tsun": ("傲娇", "FURINA_TTS_REF_TSUN_WAV", "FURINA_TTS_REF_TSUN_TEXT"),
}
_EMO_ALIASES = {
    "平静": "calm", "官方": "calm", "认真": "calm", "温柔": "calm", "安慰": "calm", "calm": "calm",
    "神气": "proud", "得意": "proud", "骄傲": "proud", "表演": "proud", "兴奋": "proud", "proud": "proud",
    "日常": "daily", "闲聊": "daily", "随意": "daily", "开心": "daily", "daily": "daily",
    "傲娇": "tsun", "嘴硬": "tsun", "哼": "tsun", "生气": "tsun", "tsun": "tsun",
}
DEFAULT_GAMES = ("YuanShen.exe,GenshinImpact.exe,StarRail.exe,ZenlessZoneZero.exe,BH3.exe,Wuthering Waves.exe,"
                 "Client-Win64-Shipping.exe,League of Legends.exe,VALORANT-Win64-Shipping.exe,cs2.exe,dota2.exe,"
                 "PUBG.exe,TslGame.exe,NarakaBladepoint.exe,Overwatch.exe,eldenring.exe,MonsterHunterWilds.exe,"
                 "GTA5.exe,Cyberpunk2077.exe,BlackMythWukong.exe,b1-Win64-Shipping.exe,DeltaForceClient-Win64-Shipping.exe")


def emotion_key(word: str | None) -> str | None:
    w = (word or "").strip().lower()
    return _EMO_ALIASES.get(w) or (w if w in EMOTIONS else None)


def emotion_label(key: str | None) -> str:
    return EMOTIONS.get(key or "", ("平静",))[0] if key else "默认"


def settings() -> dict:
    return {
        "enabled": env.get_bool("FURINA_TTS_ENABLED", False),
        "engine_dir": env.get("FURINA_TTS_ENGINE_DIR", "").strip().strip('"'),
        "gpt": env.get("FURINA_TTS_GPT", "").strip().strip('"'),
        "sovits": env.get("FURINA_TTS_SOVITS", "").strip().strip('"'),
        "ref_wav": env.get("FURINA_TTS_REF_WAV", "").strip().strip('"'),
        "ref_text": env.get("FURINA_TTS_REF_TEXT", "").strip(),
        "port": env.get_int("FURINA_TTS_PORT", 9881),
        "autostart": env.get_bool("FURINA_TTS_AUTOSTART", True),
        "idle_minutes": env.get_int("FURINA_TTS_IDLE_MINUTES", 0),
        "keep_alive": env.get_bool("FURINA_TTS_KEEP_ALIVE", True),
        "stop_ram": env.get_float("FURINA_TTS_STOP_FREE_RAM_GB", 1.0),
        "stop_commit": env.get_float("FURINA_TTS_STOP_FREE_COMMIT_GB", 1.0),
        "stop_vram": env.get_float("FURINA_TTS_STOP_FREE_VRAM_GB", 0.8),
        "speed": env.get_float("FURINA_TTS_SPEED", 1.0),
        "sample_steps": env.get_int("FURINA_TTS_SAMPLE_STEPS", 32),
        "soften": env.get_float("FURINA_TTS_SOFTEN", 0.35),
        "tail_pad_ms": env.get_int("FURINA_TTS_TAIL_PAD_MS", 450),
        "emotions": env.get_bool("FURINA_TTS_EMOTIONS", True),
        "min_ram": env.get_float("FURINA_TTS_MIN_FREE_RAM_GB", 4.0),
        "min_commit": env.get_float("FURINA_TTS_MIN_FREE_COMMIT_GB", 10.0),
        "min_vram": env.get_float("FURINA_TTS_MIN_FREE_VRAM_GB", 2.5),
        "pause_games": env.get_bool("FURINA_TTS_PAUSE_FOR_GAMES", True),
        "games": env.get("FURINA_TTS_GAME_PROCESSES", "") or DEFAULT_GAMES,
    }


def refs() -> dict[str, tuple[str, str]]:
    """配好了的语气参考音频：{key: (wav, 原话)}；文件不存在或没写原话的语气不算。"""
    out = {}
    for key, (_name, wav_key, text_key) in EMOTIONS.items():
        wav = env.get(wav_key, "").strip().strip('"')
        text = env.get(text_key, "").strip()
        if wav and text and os.path.isfile(wav):
            out[key] = (wav, text)
    return out


def pick_ref(emotion: str | None, s: dict | None = None) -> tuple[str, str, str]:
    """返回 (wav, 原话, 实际用的语气 key)；要的语气没配就退回平静（默认参考音频）。"""
    s = s or settings()
    table = refs() if s.get("emotions", True) else {}
    if emotion and emotion in table:
        wav, text = table[emotion]
        return wav, text, emotion
    return s["ref_wav"], s["ref_text"], "calm"


# ---------------- 省内存：内存 / 显存不够或正在打游戏时不开引擎 ----------------
_res_cache: dict = {"ts": 0.0, "data": None}


def _mem_gb() -> tuple[float, float]:
    """(可用物理内存 GB, 可用提交内存 GB)；非 Windows 或取不到返回 (-1, -1)。"""
    if sys.platform != "win32":
        return -1.0, -1.0
    import ctypes

    class _MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    ms = _MS()
    ms.dwLength = ctypes.sizeof(_MS)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return -1.0, -1.0
    except Exception:
        return -1.0, -1.0
    g = 1024 ** 3
    return ms.ullAvailPhys / g, ms.ullAvailPageFile / g


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
    except Exception:
        return ""


def _vram_free_gb() -> float:
    out = _run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"])
    try:
        return max(float(x) for x in out.split()) / 1024 if out.strip() else -1.0
    except ValueError:
        return -1.0


def _running_game(games: str) -> str:
    wanted = {g.strip().lower() for g in (games or "").split(",") if g.strip()}
    if not wanted or sys.platform != "win32":
        return ""
    out = _run(["tasklist", "/FO", "CSV", "/NH"])
    for line in out.splitlines():
        name = line.split('","', 1)[0].strip('"').strip()
        if name.lower() in wanted:
            return name
    return ""


def resources(max_age: float = 20.0) -> dict:
    """当前资源情况（20 秒内用缓存）：可用内存 / 提交内存 / 显存（GB，-1 = 取不到）、正在运行的游戏。"""
    now = time.time()
    if _res_cache["data"] is not None and now - _res_cache["ts"] < max_age:
        return _res_cache["data"]
    ram, commit = _mem_gb()
    data = {"ram": round(ram, 1), "commit": round(commit, 1), "vram": round(_vram_free_gb(), 1),
            "game": _running_game(settings()["games"]), "ts": now}
    _res_cache.update(ts=now, data=data)
    return data


def can_start(s: dict | None = None, res: dict | None = None) -> tuple[bool, str]:
    """现在适不适合开引擎：返回 (行不行, 不行的原因)。取不到的数据当作没问题。"""
    s = s or settings()
    r = res or resources()
    if s["pause_games"] and r.get("game"):
        return False, f"正在玩游戏（{r['game']}）"
    if s["min_ram"] > 0 and 0 <= r.get("ram", -1) < s["min_ram"]:
        return False, f"可用内存只剩 {r['ram']}G（设置的下限 {s['min_ram']:g}G）"
    if s["min_commit"] > 0 and 0 <= r.get("commit", -1) < s["min_commit"]:
        return False, f"可用提交内存只剩 {r['commit']}G（引擎启动要预留约 8~9G，下限 {s['min_commit']:g}G）"
    if s["min_vram"] > 0 and 0 <= r.get("vram", -1) < s["min_vram"]:
        return False, f"显存只剩 {r['vram']}G（下限 {s['min_vram']:g}G）"
    return True, ""


def must_yield(s: dict | None = None, res: dict | None = None) -> str:
    """引擎开着时，资源是不是紧张到该让路了：返回原因，空串 = 不用关。取不到的数据当作没问题。"""
    s = s or settings()
    r = res or resources()
    if s["pause_games"] and r.get("game"):
        return f"检测到在玩游戏（{r['game']}）"
    if s["stop_ram"] > 0 and 0 <= r.get("ram", -1) < s["stop_ram"]:
        return f"可用内存只剩 {r['ram']}G"
    if s["stop_commit"] > 0 and 0 <= r.get("commit", -1) < s["stop_commit"]:
        return f"可用提交内存只剩 {r['commit']}G"
    if s["stop_vram"] > 0 and 0 <= r.get("vram", -1) < s["stop_vram"]:
        return f"显存只剩 {r['vram']}G"
    return ""


_last_block = {"ts": 0.0, "why": ""}


def _blocked() -> str:
    ok, why = can_start()
    if ok:
        return ""
    if why != _last_block["why"] or time.time() - _last_block["ts"] > 600:
        _log.info(f"[语音] {why}，先不开语音引擎，这条发文字")
        _last_block.update(ts=time.time(), why=why)
    return why


def enabled() -> bool:
    """总开关打开且参考音频配好了（引擎可以是别处已经开着的）。"""
    s = settings()
    return bool(s["enabled"] and s["ref_wav"] and s["ref_text"] and os.path.isfile(s["ref_wav"]))


def api_url() -> str:
    return f"http://127.0.0.1:{settings()['port']}"


def _engine_cmd(s: dict) -> list[str] | None:
    py = os.path.join(s["engine_dir"], "runtime", "python.exe")
    api = os.path.join(s["engine_dir"], "api.py")
    if not (s["engine_dir"] and os.path.isfile(py) and os.path.isfile(api)):
        return None
    cmd = [py, "-X", "utf8", "api.py", "-a", "127.0.0.1", "-p", str(s["port"]), "-d", "cuda"]
    if s["sovits"] and os.path.isfile(s["sovits"]):
        cmd += ["-s", s["sovits"]]
    if s["gpt"] and os.path.isfile(s["gpt"]):
        cmd += ["-g", s["gpt"]]
    cmd += ["-dr", s["ref_wav"], "-dt", s["ref_text"], "-dl", "zh"]  # 默认参考；每句话请求时可换别的语气
    return cmd


async def is_online(timeout: float = 1.5) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            r = await c.get(api_url() + "/control")
            r.json()
        return True
    except Exception:
        return False


def _start_engine() -> bool:
    global _proc
    s = settings()
    cmd = _engine_cmd(s)
    if cmd is None:
        _log.warning("[语音] 找不到 GPT-SoVITS 引擎（检查 FURINA_TTS_ENGINE_DIR）")
        return False
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(paths.LOG_DIR / "tts_engine.log", "a", encoding="utf-8", errors="replace")
    log_file.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 机器人拉起语音引擎 =====\n")
    log_file.flush()
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        _proc = subprocess.Popen(cmd, cwd=s["engine_dir"], stdout=log_file, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as e:
        _log.warning(f"[语音] 拉起引擎失败: {e}")
        return False
    _state["started_at"] = time.time()
    _log.info(f"[语音] 已在后台拉起 GPT-SoVITS 引擎（端口 {s['port']}，pid {_proc.pid}）")
    return True


async def ensure_engine(wait: float = 120.0) -> bool:
    """引擎在线返回 True；没开且允许自动拉起时拉起并最多等 wait 秒（wait=0 只拉起不等）。"""
    global _lock
    if await is_online():
        return True
    s = settings()
    if not s["autostart"]:
        return False
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if await is_online():
            return True
        running = _proc is not None and _proc.poll() is None
        if not running:
            if time.time() - _state["last_fail"] < FAIL_BACKOFF:
                return False
            if _blocked():  # 内存/显存不够或在打游戏：这次不开
                return False
            if not _start_engine():
                _state["last_fail"] = time.time()
                return False
        deadline = time.time() + max(0.0, wait)
        while time.time() < deadline:
            await asyncio.sleep(1.0)
            if await is_online():
                return True
            if _proc is not None and _proc.poll() is not None:
                _log.warning("[语音] 引擎进程启动后退出了，详见 logs/tts_engine.log")
                _state["last_fail"] = time.time()
                return False
        return False


def _looks_like_wav(data: bytes) -> bool:
    return len(data) > 1000 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def speakable(text: str) -> str:
    """把聊天文字整理成适合念出来的句子：去掉颜文字、表情符号、网址和标记。"""
    import re

    t = text or ""
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\[[^\]]{0,30}\]|<<[^>]{0,20}>>|\{[^}]{0,20}\}", "", t)
    t = re.sub(r"[（(][^）)]{0,20}[）)]", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    t = re.sub(r"[~～]+", "，", t)
    t = re.sub(r"(?i)\b(?:www+|hhh+|233+)\b", "哈哈哈", t)
    t = re.sub(r"[“”\"'`*#^_=|\\/<>]", "", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "，", t)  # 聊天里用空格隔开的两句，念的时候停一下
    t = re.sub(r"\s*([，。！？、,.!?])\s*", r"\1", t)
    t = re.sub(r"[，,]{2,}", "，", t)
    t = re.sub(r"\s+", " ", t).strip(" ，,")
    return t


def _steps(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 32
    return min((4, 8, 16, 32), key=lambda x: abs(x - n))


def finish_text(words: str) -> str:
    """句末补个句号：没有结尾标点时模型常常把最后一个字念一半就停。"""
    w = (words or "").rstrip("，,、；;：: ")
    return w if not w or w[-1] in _END_PUNC else w + "。"


def polish_wav(data: bytes, soften: float = 0.35, tail_pad_ms: int = 450) -> bytes:
    """合成后的小修整（纯标准库）：
    - 轻微低通混合，压一压气声和齿音（soften 0~1）；
    - 开头 15ms 淡入、结尾 40ms 淡出，去掉爆音；
    - 峰值归一到 -1dB；
    - 句尾补一段静音：QQ 把 wav 转成 silk 时常吞掉最后一两百毫秒，这就是「吞尾字」的主要来源。
    解析失败就原样返回。"""
    import array
    import io
    import math
    import wave

    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if width != 2 or ch != 1 or n < sr // 10:
            return data
        x = array.array("h", raw)
        if sys.byteorder != "little":
            x.byteswap()
        k = max(0.0, min(1.0, float(soften or 0)))
        fx = [float(v) for v in x]
        if k > 0:
            a = 1 - math.exp(-2 * math.pi * 5500.0 / sr)
            lp = 0.0
            for i, v in enumerate(fx):
                lp += a * (v - lp)
                fx[i] = v - k * (v - lp)
        fin, fout = int(sr * 0.015), int(sr * 0.04)
        for i in range(min(fin, len(fx))):
            fx[i] *= i / fin
        for i in range(min(fout, len(fx))):
            fx[-1 - i] *= i / fout
        peak = max(1.0, max(abs(v) for v in fx))
        gain = min(3.0, 32767 * 0.89 / peak)
        out = array.array("h", (int(max(-32768, min(32767, v * gain))) for v in fx))
        out.extend([0] * int(sr * max(0, int(tail_pad_ms or 0)) / 1000))
        if sys.byteorder != "little":
            out.byteswap()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(out.tobytes())
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        _log.info(f"[语音] 修整音频失败，发原声：{type(e).__name__}: {e}")
        return data


async def synthesize(text: str, *, wait: float = 120.0, emotion: str | None = None) -> bytes | None:
    """合成一句话，返回 wav 字节；没开、引擎不在、合成失败都返回 None（调用方退回发文字）。
    emotion：calm / proud / daily / tsun，换对应的参考音频（同一个模型，不多占内存）。"""
    if not enabled():
        return None
    words = finish_text(speakable(text))
    if len(words) < 2:
        return None
    if not await ensure_engine(wait):
        return None
    s = settings()
    ref_wav, ref_text, _used = pick_ref(emotion, s)
    body = {
        "text": words, "text_language": "zh",
        "refer_wav_path": ref_wav, "prompt_text": ref_text, "prompt_language": "zh",
        "speed": max(0.6, min(1.6, float(s["speed"] or 1.0))), "cut_punc": CUT_PUNC,
        "sample_steps": _steps(s["sample_steps"]),
    }
    _state["last_use"] = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0), trust_env=False) as c:
            r = await c.post(api_url() + "/", json=body)
    except Exception as e:
        _log.warning(f"[语音] 合成请求失败: {type(e).__name__}: {e}")
        return None
    if r.status_code != 200 or not _looks_like_wav(r.content):
        _log.warning(f"[语音] 引擎返回异常（HTTP {r.status_code}，{len(r.content)} 字节）")
        return None
    _state["last_use"] = time.time()
    return polish_wav(r.content, s["soften"], s["tail_pad_ms"])


async def warm_up() -> None:
    """后台把引擎拉起来（不等），下一次就能直接用。"""
    if enabled():
        await ensure_engine(wait=0)


YIELD_COOLDOWN = 300  # 因为资源紧张关掉后，至少等 5 分钟再自动开回来，免得反复开关


def idle_check() -> None:
    """定时调用（每 30 秒）：资源紧张就关掉自己拉起的引擎；常开模式下宽裕了再后台开回来。"""
    global _proc
    if _proc is not None and _proc.poll() is not None:
        _proc = None
    s = settings()
    if _proc is not None:
        why = must_yield(s, resources(max_age=10))
        if why:
            stop()
            _state["yielded_at"] = time.time()
            _log.info(f"[语音] {why}，已主动关掉语音引擎把内存和显存让出来（宽裕了自动开回来，这期间只发文字）")
            return
        minutes = s["idle_minutes"]
        if minutes > 0 and not s["keep_alive"]:
            last = max(_state["last_use"], _state["started_at"])
            if time.time() - last >= minutes * 60:
                stop()
                _log.info(f"[语音] 引擎闲置超过 {minutes} 分钟，已关闭释放显存（下次要用时自动再开）")
        return
    if want_keep_alive(s):
        _schedule_keep_alive()


def want_keep_alive(s: dict | None = None) -> bool:
    """常开模式下，现在该不该把引擎开回来（不在冷却期、资源够、没被别人占着端口由调用方再查）。"""
    s = s or settings()
    if not (s["keep_alive"] and s["autostart"] and enabled()):
        return False
    if time.time() - _state.get("yielded_at", 0.0) < YIELD_COOLDOWN:
        return False
    if time.time() - _state["last_fail"] < FAIL_BACKOFF:
        return False
    r = resources(max_age=10)
    ok, _ = can_start(s, r)
    if not ok:
        return False
    # 自动开回来要留够余量：引擎自己要吃约 1.5G 内存、8~9G 提交内存、2G 左右显存，
    # 开完以后还得高于「让路线」，否则刚开就被关、反复横跳
    for key, line, need in (("ram", "stop_ram", 2.5), ("commit", "stop_commit", 9.5), ("vram", "stop_vram", 2.5)):
        if s[line] > 0 and 0 <= r.get(key, -1) < s[line] + need:
            return False
    return True


def _schedule_keep_alive() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _state.get("keep_task") is not None and not _state["keep_task"].done():
        return

    async def _go():
        if not await is_online():  # 端口上已经有引擎（别人开的也算）就不重复开
            _log.info("[语音] 常开模式：内存够，后台把语音引擎开起来")
            await ensure_engine(wait=0)

    _state["keep_task"] = loop.create_task(_go())


def stop() -> None:
    """只关自己拉起的引擎；别人开着的引擎不碰。"""
    global _proc
    if _proc is None:
        return
    try:
        if _proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(_proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
            else:
                _proc.terminate()
    except Exception:
        pass
    _proc = None


def status() -> dict:
    s = settings()
    return {
        "enabled": s["enabled"], "configured": enabled(), "port": s["port"],
        "owned_running": _proc is not None and _proc.poll() is None,
        "last_use": _state["last_use"],
        "emotions": sorted(refs()) if s["emotions"] else [],
    }
