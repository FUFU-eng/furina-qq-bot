"""Real-ESRGAN ncnn-vulkan 封装：自动探测 RTX 5060 独显，异步执行 2 倍超分。"""

import asyncio
import os
import re
import subprocess
import tempfile

from nonebot import logger
from PIL import Image

from . import config

# 探测到的 NVIDIA 独显设备号（进程内缓存，避免每次重复探测）
_cached_nvidia_gpu_id: int | None = None
_probe_lock = asyncio.Lock()


async def _kill_process_tree(proc):
    """超时后先 kill 进程，再确保整个进程树退出，避免残留子进程占用 GPU/文件。"""
    try:
        proc.kill()
    except Exception:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
        return
    except Exception:
        pass
    try:
        # 放到线程里执行，避免阻塞事件循环最多 10 秒（期间所有群消息都无法处理）
        await asyncio.to_thread(
            subprocess.run,
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def _parse_gpu_devices(stderr: str):
    """从 exe 启动输出里解析设备列表：[(id, "AMD Radeon(TM) 610M"), (id, "NVIDIA GeForce RTX 5060 Laptop GPU")]"""
    devices = []
    # 形如: [0 AMD Radeon(TM) 610M]  queueC=1[2]  queueG=0[1]  queueT=2[1]
    for m in re.finditer(r"\[(\d+)\s+([^\]]+)\]\s+queueC", stderr):
        try:
            devices.append((int(m.group(1)), m.group(2).strip()))
        except Exception:
            continue
    return devices


def _nvidia_gpu_id_from_stderr(stderr: str) -> int | None:
    """在 exe 输出里找 NVIDIA/GeForce/RTX 设备号；找不到返回 None"""
    for gid, name in _parse_gpu_devices(stderr):
        low = name.lower()
        if "nvidia" in low or "geforce" in low or "rtx" in low:
            return gid
    return None


async def probe_nvidia_gpu_id() -> int | None:
    """用一张 64x64 小图预热 exe 一次，解析出 NVIDIA 独显（RTX 5060）的设备号。

    结果缓存，避免每次超分都多跑一次。失败返回 None（调用方退回配置默认设备号）。
    """
    global _cached_nvidia_gpu_id
    if _cached_nvidia_gpu_id is not None:
        return _cached_nvidia_gpu_id
    async with _probe_lock:
        if _cached_nvidia_gpu_id is not None:
            return _cached_nvidia_gpu_id
        if not os.path.exists(config.UPSCALE_EXE):
            logger.warning(f"[强化] 找不到超分引擎: {config.UPSCALE_EXE}")
            return None

        in_path, out_path = "", ""
        try:
            fd_in, in_path = tempfile.mkstemp(suffix=".png")
            os.close(fd_in)
            Image.new("RGB", (64, 64), (128, 128, 128)).save(in_path, "PNG")
            fd_out, out_path = tempfile.mkstemp(suffix=".png")
            os.close(fd_out)
            if os.path.exists(out_path):
                os.unlink(out_path)
            args = [
                config.UPSCALE_EXE,
                "-i", in_path,
                "-o", out_path,
                "-n", config.UPSCALE_MODEL,
                "-s", str(config.UPSCALE_SCALE),
                "-g", "0",  # 探测时跑哪块卡无所谓，只要拿到完整设备列表
                "-t", str(config.UPSCALE_TILE),
                "-m", config.UPSCALE_MODELS_DIR,
            ]
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.path.dirname(os.path.abspath(config.UPSCALE_EXE)),
            )
            try:
                _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60)
            except asyncio.TimeoutError:
                await _kill_process_tree(proc)
                raise
            stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")
            gid = _nvidia_gpu_id_from_stderr(stderr_text)
            if gid is not None:
                _cached_nvidia_gpu_id = gid
                logger.info(f"[强化] 探测到 NVIDIA 独显设备号: {gid}")
            else:
                logger.warning(f"[强化] 未探测到 NVIDIA 独显，将使用配置默认 GPU 设备号 {config.UPSCALE_GPU_ID}")
        except Exception as e:
            logger.warning(f"[强化] 探测 NVIDIA 独显失败: {e}")
        finally:
            for p in (in_path, out_path):
                try:
                    if p and os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass
        return _cached_nvidia_gpu_id


async def run_upscale(input_path: str, output_path: str) -> int:
    """执行 2 倍超分，返回实际使用的 GPU 设备号（用于日志确认是 RTX 5060 独显）。"""
    if not os.path.exists(config.UPSCALE_EXE):
        raise RuntimeError(f"找不到超分引擎: {config.UPSCALE_EXE}")

    gpu_id = await probe_nvidia_gpu_id()
    if gpu_id is None:
        gpu_id = config.UPSCALE_GPU_ID

    args = [
        config.UPSCALE_EXE,
        "-i", input_path,
        "-o", output_path,
        "-n", config.UPSCALE_MODEL,
        "-s", str(config.UPSCALE_SCALE),
        "-g", str(gpu_id),
        "-t", str(config.UPSCALE_TILE),
        "-m", config.UPSCALE_MODELS_DIR,
    ]
    logger.info(f"[强化] 开始超分: model={config.UPSCALE_MODEL} scale={config.UPSCALE_SCALE} gpu={gpu_id}")

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.dirname(os.path.abspath(config.UPSCALE_EXE)),
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=config.UPSCALE_TIMEOUT)
    except asyncio.TimeoutError:
        await _kill_process_tree(proc)
        logger.error(f"[强化] 超分超时（>{config.UPSCALE_TIMEOUT}秒），已终止进程")
        raise TimeoutError(f"超分超时（{config.UPSCALE_TIMEOUT}秒）")

    stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        logger.error(f"[强化] 超分进程失败 exit={proc.returncode}: {stderr_text[-500:]}")
        raise RuntimeError(f"超分引擎执行失败（exit={proc.returncode}）")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("超分未生成输出文件")

    # 确认实际使用的 GPU，避免误用核显
    used = _nvidia_gpu_id_from_stderr(stderr_text)
    if used is not None and used != gpu_id:
        logger.warning(f"[强化] 实际使用 GPU {used}（配置为 {gpu_id}），以实际为准")
    return gpu_id
