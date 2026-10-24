# ============ 图片强化配置区（从环境变量读取，可在 .env 中配置） ============
import os

from ...core import env

_config = env.config_ns  # 旧键名，改读 .env

# 项目根目录（D:\old-qq-bot），由插件所在路径推导，避免硬编码路径。
# 插件位于 <项目根>/bot/plugins/image_upscale/，其父目录上推 3 层即项目根。
PROJECT_ROOT = env.assets_root()

# Real-ESRGAN ncnn-vulkan 预编译便携版（无需 CUDA/PyTorch，自带模型）
UPSCALE_EXE = getattr(_config, "upscale_exe", os.path.join(PROJECT_ROOT, "upscale", "realesrgan-ncnn-vulkan.exe"))
UPSCALE_MODELS_DIR = getattr(_config, "upscale_models_dir", os.path.join(PROJECT_ROOT, "upscale", "models"))

# 使用的模型与放大倍数：对应手机 RealSR 的 real-esrganv3-anime-x2（animevideo v3 模型 2 倍输出）
UPSCALE_MODEL = getattr(_config, "upscale_model", "realesr-animevideov3")
UPSCALE_SCALE = int(getattr(_config, "upscale_scale", 2))

# 默认 GPU 设备号（1 = RTX 5060 独显）；插件启动时会自动探测 NVIDIA 独显并优先使用，避免误用核显
UPSCALE_GPU_ID = int(getattr(_config, "upscale_gpu_id", 1))

# 单次超分总超时（秒）：大图在 GPU 上通常几十秒内完成，300 秒兜底防卡死
UPSCALE_TIMEOUT = int(getattr(_config, "upscale_timeout", 300))
# 分块大小（0=自动）
UPSCALE_TILE = int(getattr(_config, "upscale_tile", 0))

# 机器人QQ号（用于判断艾特）

# 机器人常见昵称：有些客户端/用户发的是纯文本"@芙宁娜"，不会生成 [CQ:at] 段，
# 这类文本艾特也视为艾特机器人（真正的 [CQ:at] 仍是首选判定方式）
BOT_ALIASES = getattr(_config, "bot_aliases", "芙宁娜,芙芙,水神大人").split(",")

# 大群（主群）和小群（分群）群号：大群强化的图片自动转发到小群
MAIN_GROUP_ID = str(getattr(_config, "main_group_id", ""))
SUB_GROUP_ID = str(getattr(_config, "sub_group_id", ""))


def __getattr__(name):
    if name == "BOT_QQ":
        return env.bot_qq()
    raise AttributeError(name)
