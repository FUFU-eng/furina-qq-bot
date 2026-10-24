"""经典原生面板（PySide6，米哈游启动器式布局）：旧版控制面板移植到 v2，数据走 v2 的 SQLite。"""


def main() -> int:
    from furina.core import paths

    paths.ensure_dirs()
    from .window import main as _main

    return _main()
