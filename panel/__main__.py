"""python -m panel：默认打开经典原生面板；--web 打开网页版面板（其余参数同 panel.app）。"""
import sys


def _run() -> int:
    argv = sys.argv[1:]
    if "--web" in argv or "--serve" in argv or "--browser" in argv:
        from .app import main

        return main([a for a in argv if a != "--web"])
    from .app import _single_instance

    if _single_instance() is None:
        return 0
    from .classic import main

    return main()


sys.exit(_run())
