"""经典面板已拆成多个文件：同步断言要在整个 panel/classic 包里找。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def classic_panel_source() -> str:
    pkg = ROOT / "panel" / "classic"
    files = [pkg / "window.py"] + sorted(p for p in pkg.rglob("*.py") if p.name != "window.py")
    return "\n".join(p.read_text(encoding="utf-8") for p in files)
