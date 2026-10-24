"""Process integration only. All game behavior belongs to unmodified upstream projects."""
from __future__ import annotations
import contextlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from furina.core import paths

CORE = paths.ROOT / "integrations" / "gsuid_core"
CORE_PYTHON = CORE / ".venv" / "Scripts" / "python.exe"
BOT_PYTHON = paths.ROOT / "integrations" / "nonebot-env" / "Scripts" / "python.exe"
CORE_URL = "http://127.0.0.1:8765"
CONSOLE_URL = CORE_URL + "/app"


def processes() -> list[int]:
    from furina.core import winproc
    if not winproc.IS_WIN:
        return []
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq "
              + winproc.ps_quote(str(CORE_PYTHON))
              + " -and ($_.CommandLine -like '*-m gsuid_core.core*' -or $_.CommandLine -like "
              + winproc.ps_quote("*" + str(CORE / "gsuid_core" / "core.py") + "*")
              + ") } | ForEach-Object { $_.ProcessId }")
    return [int(x) for x in winproc.powershell(script).split() if x.isdigit()]


def snapshot() -> dict:
    from furina.core import winproc
    pids = processes()
    installed = CORE_PYTHON.is_file() and BOT_PYTHON.is_file()
    port = winproc.port_open(8765)
    healthy = False
    if pids and port:
        try:
            opener = build_opener(ProxyHandler({}))
            with opener.open(CORE_URL + "/api/auth/pubkey", timeout=2) as response:
                healthy = response.status == 200
        except Exception:
            pass
    return {"engine": "GsCore", "installed": installed, "running": bool(pids),
            "ready": healthy, "port_conflict": port and not pids,
            "console_url": CONSOLE_URL, "pids": pids,
            "plugins": ["GenshinUID", "StarRailUID", "ZZZeroUID", "BBBUID"],
            "integration": "官方 NoneBot 适配器 → 独立 GsCore → 上游游戏插件。账号、签到、查询和图片均由上游处理。"}


@contextlib.contextmanager
def _start_lock():
    import msvcrt
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (paths.LOG_DIR / "gscore-start.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def ensure_running() -> dict:
    from furina.core import winproc
    if not CORE_PYTHON.is_file():
        raise RuntimeError("GsCore 独立环境尚未安装完成，请查看 logs/gscore-install.log")
    with _start_lock():
        state = snapshot()
        if state["port_conflict"]:
            raise RuntimeError("8765 端口由其他进程占用；未停止或替换该进程。")
        if not state["running"]:
            paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                       PLAYWRIGHT_BROWSERS_PATH=str(paths.ROOT / "integrations" / "playwright"))
            env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY", ""), "localhost", "127.0.0.1", "::1"]))
            with (paths.LOG_DIR / "gscore-console.log").open("ab") as output:
                subprocess.Popen([str(CORE_PYTHON), "-m", "gsuid_core.core", "--host", "127.0.0.1", "--port", "8765"],
                                 cwd=CORE, env=env, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=subprocess.STDOUT, creationflags=winproc.NO_WINDOW)
        for _ in range(45):
            if winproc.port_open(8765):
                break
            time.sleep(1)
    return snapshot()


def local_registration_code() -> str:
    """Only for an explicit desktop UI click; never include in status/API/log output."""
    data = json.loads((CORE / "data" / "config.json").read_text(encoding="utf-8"))
    return str(data.get("REGISTER_CODE") or "")


# Exact @-commands handled by the FurinaMys GsCore plugin (no game prefix).
CORE_EXACT = frozenset({
    "绑定账号", "扫码绑定", "绑定米游社", "重新绑定", "我的账号", "绑定信息", "账号信息",
    "指令帮助", "帮助", "菜单", "功能", "指令", "更新展柜", "刷新展柜", "更新面板", "刷新面板",
    "解除绑定", "解绑账号", "取消绑定", "删除账号", "解绑",
})
# Prefix @-commands handled by FurinaMys (the device JSON follows the keyword).
CORE_PREFIX = ("绑定设备", "设备绑定", "设备登录")

# 「@芙芙 绝区零开播了，你也去看看直播」这种是聊天，不是游戏指令：前缀后面像一句话（有标点/空格、
# 语气词结尾、带人称或闲聊词、太长）就不当指令。chat 插件（furina/services/gscore.py）和
# FurinaMys（gsuid_core/plugins/FurinaMys）各有一份同样的判断，改一处要同步另一处。
_CHAT_WORDS = re.compile(
    r"[，。！？,.!?、~～…\s]|你|我|他|她|它|大家|开播|直播|启动|好玩|怎么|什么|为什么|为啥|咋|没有|不是|真的|一起|"
    r"新版本|版本|更新了|活动|卡池|池子|出了|上线|玩家|剧情|好看|厉害|喜欢|讨厌")
_PARTICLE_END = re.compile(r"[了吗呢吧啊呀嘛哦么啦哈耶诶咯噢嗷喔]$")


def looks_like_chat(rest: str) -> bool:
    """游戏前缀后面那段像闲聊（不是「每日」「芙宁娜攻略」这类指令）。"""
    rest = (rest or "").strip()
    if not rest:
        return False
    return len(rest) > 16 or bool(_CHAT_WORDS.search(rest)) or bool(_PARTICLE_END.search(rest))


# A namespace reservation for coexistence with the existing AI chat only.
# The upstream router still decides whether a command exists and how it behaves.
_namespace_cache: tuple[float, tuple[str, ...], tuple[str, ...]] = (0.0, (), ())


def reserved_for_core(text: str) -> bool:
    global _namespace_cache
    now = time.monotonic()
    stamp, prefixes, starts = _namespace_cache
    if now - stamp > 5:
        found: set[str] = set()
        try:
            config = json.loads((CORE / "data/config.json").read_text(encoding="utf-8"))
            starts = tuple(x for x in config.get("command_start", []) if isinstance(x, str) and x)
        except (OSError, ValueError):
            starts = ()
        for name in ("GenshinUID", "StarRailUID", "ZZZeroUID", "BBBUID", "core_command", "FurinaSign", "FurinaMys"):
            try:
                data = json.loads((CORE / "data/plugins_configs" / (name + ".json")).read_text(encoding="utf-8"))
                keys = ("prefix",) if data.get("disable_force_prefix") else ("prefix", "force_prefix")
                for key in keys:
                    values = data.get(key) or []
                    if isinstance(values, str): values = [values]
                    found.update(x for x in values if isinstance(x, str) and x)
            except (OSError, ValueError):
                pass
        prefixes = tuple(sorted(found, key=len, reverse=True))
        _namespace_cache = now, prefixes, starts
    text = str(text).strip()
    for start in sorted(starts, key=len, reverse=True):
        if text.startswith(start):
            text = text[len(start):].lstrip(); break
    if text in CORE_EXACT or text.startswith(CORE_PREFIX):
        return True
    for prefix in prefixes:
        if text.startswith(prefix):
            return not looks_like_chat(text[len(prefix):])  # 前缀后面是一句闲聊：交给她聊
    return False
