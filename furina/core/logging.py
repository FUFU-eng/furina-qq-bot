"""日志：控制台 + logs/bot.log（UTF-8，5MB 轮转，保留 5 份）。面板读取同一个文件。"""
from __future__ import annotations

import sys

from . import paths

LOG_FILE = paths.LOG_DIR / "bot.log"


def setup_file_logging() -> None:
    paths.ensure_dirs()
    try:
        from loguru import logger

        logger.add(
            LOG_FILE,
            encoding="utf-8",
            rotation="5 MB",
            retention=5,
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} [{level}] {name} | {message}",
            enqueue=True,
        )
    except Exception as e:  # pragma: no cover
        sys.stderr.write(f"[警告] 初始化日志文件失败 ({LOG_FILE}): {e}\n")
