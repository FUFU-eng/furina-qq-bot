"""界面素材（Logo、头像、主页立绘/背景）。来自 ASSETS_ROOT（v2 的 assets 目录）。"""
from __future__ import annotations

from pathlib import Path

from furina.core import paths

from . import envfile

NAMED = {
    "logo": "logo_furina.svg", "logo_dark": "logo_furina_dark.svg",
    "avatar": "avatar_round.png", "icon": "furina.ico",
}


def root() -> Path:
    return Path(envfile.get("ASSETS_ROOT", "") or paths.ROOT)


def named(name: str) -> Path | None:
    fn = NAMED.get(name)
    for base in (root(), paths.ROOT):
        if fn and (base / fn).is_file():
            return base / fn
    return None


def backgrounds() -> list[int]:
    """可用的主页背景编号：splash 与 backdrop 都存在才算。"""
    base = root() / "backgrounds"
    out = []
    for i in range(1, 10):
        if (base / "splash" / f"bg{i}.jpg").is_file() and (base / "backdrop" / f"bg{i}.jpg").is_file():
            out.append(i)
    return out


def background(kind: str, index: int) -> Path | None:
    if kind not in ("splash", "backdrop"):
        return None
    p = root() / "backgrounds" / kind / f"bg{int(index)}.jpg"
    return p if p.is_file() else None
