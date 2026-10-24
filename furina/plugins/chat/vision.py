"""看图：取图 / 编码 / 一句话描述（实现在 furina.core.images 与 furina.core.llm）。"""
from ...core import llm as _llm
from ...core.images import encode_data_url, encode_data_url_async, fetch_image_bytes, image_part, image_parts  # noqa: F401


async def describe_image(data: bytes, max_len: int = 40) -> str:
    return await _llm.describe_image(await encode_data_url_async(data, max_side=896), max_len=max_len)
