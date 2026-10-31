"""合并转发：把节点里「只有 id、没有 content」的内层转发补成内联 content。

NapCat 的 get_forward_msg 会把内层合并转发直接内联在 forward 段的 data.content 里；
SnowLuma 只给内层转发的 id。这里按 id 再拉一次，补成 NapCat 那种结构，下游的展开逻辑
（文字、看图、强化取图）就不用分两套写。拉取次数和嵌套层数都有上限，异常输入不会无限递归。
"""
from __future__ import annotations

import asyncio

from nonebot import logger

FORWARD_TYPES = ("forward", "longmsg")


def forward_nodes(res) -> list:
    """get_forward_msg 的返回 → 节点列表（兼容适配器已解包的 {messages} 和原始信封 {data: {messages}}）。"""
    if not isinstance(res, dict):
        return []
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    nodes = res.get("messages") or data.get("messages") or []
    return nodes if isinstance(nodes, list) else []


def _node_message(node: dict):
    payload = node.get("data") if node.get("type") == "node" else node
    payload = payload if isinstance(payload, dict) else node
    message = payload.get("message")
    if message is None:
        message = payload.get("content")
    if isinstance(message, dict):
        message = [message]
    return message if isinstance(message, list) else []


async def inline_nested(bot, nodes, *, max_depth: int = 2, budget: int = 6, timeout: float = 8.0) -> list:
    """原地补全 nodes 里内层转发的 content 并返回 nodes；最多再拉 budget 次、往下 max_depth 层。"""
    left = [max(0, int(budget))]

    async def walk(current, depth: int) -> None:
        if depth >= max_depth or not isinstance(current, list):
            return
        for node in current:
            if not isinstance(node, dict):
                continue
            for seg in _node_message(node):
                if not isinstance(seg, dict) or seg.get("type") not in FORWARD_TYPES:
                    continue
                data = seg.get("data")
                if not isinstance(data, dict):
                    continue
                inner = data.get("content")
                if not (isinstance(inner, list) and inner):
                    fid = str(data.get("id") or data.get("res_id") or data.get("forward_id") or "")
                    if not fid or fid == "0" or left[0] <= 0 or bot is None:
                        continue
                    left[0] -= 1
                    try:
                        res = await asyncio.wait_for(bot.get_forward_msg(id=fid), timeout)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[合并转发] 读取内层转发 {fid} 失败: {e}")
                        continue
                    inner = forward_nodes(res)
                    if not inner:
                        continue
                    data["content"] = inner
                await walk(inner, depth + 1)

    await walk(nodes, 0)
    return nodes
