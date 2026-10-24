"""项目路径（唯一来源）。所有运行期数据都在 <ROOT>/data 下。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("FURINA_ROOT") or Path(__file__).resolve().parents[2])
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
ENV_PATH = ROOT / ".env"
DB_PATH = Path(os.environ.get("FURINA_DB") or DATA_DIR / "furina.db")


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
