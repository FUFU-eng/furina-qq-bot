"""测试环境：临时项目根目录 + 临时数据库，绝不碰真实数据。必须最先导入。"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="furina-test-"))
os.environ["FURINA_ROOT"] = str(TMP)
os.environ["FURINA_DB"] = str(TMP / "data" / "test.db")
# 每轮判心情会额外调模型：老测试按关键词模式跑（会数模型调用次数），新测试里单独打开
os.environ.setdefault("AI_CHAT_MOOD_JUDGE", "false")
# 烦躁值 / B站口味会跨测试累积（同一个假用户被艾特很多次）：老测试关着跑，新测试里单独打开
os.environ.setdefault("AI_CHAT_ANNOY", "false")
os.environ.setdefault("BILI_TASTE", "false")
# 筛图收件箱：测试里收到的图只落在临时目录，绝不写进真实的待筛目录 / Eagle 导入文件夹
os.environ.setdefault("PIC_INBOX_DIR", str(TMP / "pic_inbox"))
os.environ.setdefault("PIC_INBOX_EXPORT_DIR", str(TMP / "eagle_import"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.chdir(TMP)  # NoneBot 会读取当前目录的 .env：避开真实配置

import nonebot  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init()
    from nonebot.adapters.onebot.v11 import Adapter

    nonebot.get_driver().register_adapter(Adapter)
    nonebot.load_plugin("furina.plugins.chat")


_last_mtime = [0]


def write_env(text: str) -> None:
    import time

    p = TMP / ".env"
    # 「话多一点」会给随机接话加保底概率：老测试默认关掉，免得随机插话让结果不稳定（新测试显式打开）
    if "AI_CHAT_CHATTY=" not in text:
        text = text + ("" if not text or text.endswith("\n") else "\n") + "AI_CHAT_CHATTY=0\n"
    # 「刚聊完多留一会儿」同理：会抬高随机接话概率，老测试默认关掉
    if "AI_CHAT_LINGER=" not in text:
        text = text + ("" if not text or text.endswith("\n") else "\n") + "AI_CHAT_LINGER=0\n"
    p.write_text(text, encoding="utf-8")
    st = p.stat()
    # env 按 (mtime, 大小) 判断要不要重读：同一毫秒里连写两次、大小又一样时会读到旧值（test_switch_off 偶发失败）。
    # 保证每次写完的 mtime 都比上一次大。
    mtime = max(st.st_mtime_ns + int(time.time() * 1000) % 1000 + 1_000_000, _last_mtime[0] + 1_000_000)
    _last_mtime[0] = mtime
    os.utime(p, ns=(st.st_atime_ns, mtime))
