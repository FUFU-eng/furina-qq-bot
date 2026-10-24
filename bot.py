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


def load_all_plugins() -> None:
    import furina.plugins as pkg

    disabled = {x.strip() for x in os.environ.get("FURINA_DISABLED_PLUGINS", "").split(",") if x.strip()}
    for mod in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod.ispkg and not mod.name.startswith("_") and mod.name not in disabled:
            nonebot.load_plugin(f"furina.plugins.{mod.name}")


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

    load_all_plugins()
    return driver


if __name__ == "__main__":
    setup_file_logging()
    create_app()
    nonebot.run()
