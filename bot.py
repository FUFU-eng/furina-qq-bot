"""芙宁娜 QQ 机器人 v2 启动入口：python bot.py"""
from __future__ import annotations

import os
import pkgutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

# Keep the official connector dependencies isolated from the desktop/global Python.
if __name__ == "__main__":
    _bot_python = os.path.join(ROOT, "integrations", "nonebot-env", "Scripts", "python.exe")
    if os.path.isfile(_bot_python) and os.path.normcase(sys.executable) != os.path.normcase(_bot_python):
        os.execv(_bot_python, [_bot_python, os.path.join(ROOT, "bot.py"), *sys.argv[1:]])

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter  # noqa: E402

from furina.core import db, http, images, llm, paths  # noqa: E402
from furina.core.logging import setup_file_logging  # noqa: E402


def load_all_plugins() -> list[str]:
    """加载 furina/plugins 下所有插件，返回加载失败的插件名（NoneBot 只记日志不抛错）。"""
    import furina.plugins as pkg

    disabled = {x.strip() for x in os.environ.get("FURINA_DISABLED_PLUGINS", "").split(",") if x.strip()}
    failed = []
    for mod in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod.ispkg and not mod.name.startswith("_") and mod.name not in disabled:
            if nonebot.load_plugin(f"furina.plugins.{mod.name}") is None:
                failed.append(mod.name)
    return failed


PLUGIN_STATE = os.path.join(ROOT, "data", "plugin_load.json")


def _save_plugin_state(failed: list[str]) -> None:
    """正式启动时记下哪些插件本来就加载不了（缺依赖等），面板「重启前试加载」只拦新冒出来的失败。"""
    import json
    import time

    try:
        os.makedirs(os.path.dirname(PLUGIN_STATE), exist_ok=True)
        with open(PLUGIN_STATE, "w", encoding="utf-8") as f:
            json.dump({"failed": failed, "ts": time.time()}, f, ensure_ascii=False)
    except OSError:
        pass


def create_app():
    paths.ensure_dirs()
    nonebot.init(onebot_api_timeout=180, gsuid_core_host="127.0.0.1", gsuid_core_repeat=True)
    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    @driver.on_shutdown
    async def _close_shared_clients():
        await llm.aclose()
        await images.aclose()
        await http.aclose_all()
        db.close()

    failed = load_all_plugins()
    if os.environ.get("FURINA_TRIAL_LOAD") == "1":
        print("FURINA_TRIAL_FAILED=" + ",".join(failed))
    else:
        _save_plugin_state(failed)
    return driver


if __name__ == "__main__":
    setup_file_logging()
    create_app()
    nonebot.run()
