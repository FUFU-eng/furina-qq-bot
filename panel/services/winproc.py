"""兼容旧导入：实现已移到 furina/core/winproc.py（机器人侧的 services 不能依赖面板）。

这里把本模块直接替换成核心模块，``panel.services.winproc`` 与 ``furina.core.winproc``
是同一个对象，打补丁、读常量都一致。
"""
import sys

from furina.core import winproc as _impl

sys.modules[__name__] = _impl
