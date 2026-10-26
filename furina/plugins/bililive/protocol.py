"""B 站直播间弹幕连接的收发包格式（纯函数，方便测试）。

包头 16 字节：总长(4) 头长(2)=16 协议版本(2) 操作码(4) 序号(4)，后面是正文。
操作码：2 心跳 / 3 心跳回应（人气）/ 5 消息 / 7 进房认证 / 8 认证回应。
协议版本：0/1 明文 JSON；2 zlib 压缩（里面还套着若干个包）；3 brotli（我们只申请 2，不会收到 3）。
"""
from __future__ import annotations

import json
import struct
import zlib

HEADER = struct.Struct(">IHHII")
OP_HEARTBEAT, OP_HEARTBEAT_REPLY, OP_MESSAGE, OP_AUTH, OP_AUTH_REPLY = 2, 3, 5, 7, 8


def pack(op: int, body: bytes | str | dict, ver: int = 1, seq: int = 1) -> bytes:
    if isinstance(body, dict):
        body = json.dumps(body, separators=(",", ":"))
    if isinstance(body, str):
        body = body.encode("utf-8")
    return HEADER.pack(HEADER.size + len(body), HEADER.size, ver, op, seq) + body


def auth_packet(room_id: int, uid: int, token: str, buvid: str = "") -> bytes:
    return pack(OP_AUTH, {"uid": int(uid or 0), "roomid": int(room_id), "protover": 2, "buvid": buvid,
                          "platform": "web", "type": 2, "key": token or ""})


def heartbeat_packet() -> bytes:
    return pack(OP_HEARTBEAT, "[object Object]")


def unpack(data: bytes) -> list[tuple[int, object]]:
    """拆一个 websocket 帧：返回 [(操作码, 正文)]；消息正文是 dict，心跳回应是人气数。"""
    out, off = [], 0
    while off + HEADER.size <= len(data):
        total, hlen, ver, op, _seq = HEADER.unpack_from(data, off)
        if total < hlen or off + total > len(data):
            break
        body = data[off + hlen: off + total]
        off += total
        if op == OP_MESSAGE and ver == 2:
            try:
                out.extend(unpack(zlib.decompress(body)))
            except zlib.error:
                pass
            continue
        if op == OP_HEARTBEAT_REPLY:
            out.append((op, int.from_bytes(body[:4], "big") if len(body) >= 4 else 0))
        elif op in (OP_MESSAGE, OP_AUTH_REPLY):
            try:
                out.append((op, json.loads(body.decode("utf-8", "ignore") or "{}")))
            except ValueError:
                pass
    return out


def _name_from_danmu(info: list) -> tuple[str, str]:
    uid, name = "", ""
    try:
        uid, name = str(info[2][0] or ""), str(info[2][1] or "")
    except (IndexError, TypeError):
        pass
    try:  # 新版把完整昵称放在 info[0][15].user.base.name（info[2][1] 可能是打码的）
        base = info[0][15]["user"]["base"]
        if base.get("name"):
            name = str(base["name"])
    except (IndexError, KeyError, TypeError):
        pass
    return uid, name


def _pb_fields(raw: bytes) -> dict[int, object]:
    """极简 protobuf 顶层解析（只要整数和字符串字段；嵌套的不管）。"""
    out: dict[int, object] = {}
    i, n = 0, len(raw)

    def varint() -> int:
        nonlocal i
        shift = val = 0
        while i < n:
            b = raw[i]
            i += 1
            val |= (b & 0x7F) << shift
            if not b & 0x80:
                return val
            shift += 7
        raise ValueError("truncated")

    try:
        while i < n:
            key = varint()
            field, wt = key >> 3, key & 7
            if wt == 0:
                out.setdefault(field, varint())
            elif wt == 2:
                ln = varint()
                out.setdefault(field, raw[i:i + ln])
                i += ln
            elif wt == 1:
                i += 8
            elif wt == 5:
                i += 4
            else:
                break
    except (ValueError, IndexError):
        pass
    return out


def _interact_v2(d: dict) -> dict | None:
    import base64

    try:
        f = _pb_fields(base64.b64decode(d.get("pb") or ""))
    except (ValueError, TypeError):
        return None
    mt = f.get(5) if isinstance(f.get(5), int) else 0
    if mt not in (1, 2):
        return None
    name = f.get(2)
    name = name.decode("utf-8", "replace") if isinstance(name, bytes) else ""
    uid = f.get(1) if isinstance(f.get(1), int) else 0
    return {"kind": "enter" if mt == 1 else "follow", "uid": str(uid or ""), "name": name}


def parse(msg: dict) -> dict | None:
    """把一条直播间消息变成 {kind, uid, name, text, ...}；不关心的消息返回 None。"""
    cmd = str(msg.get("cmd") or "").split(":")[0]
    d = msg.get("data") or {}
    if cmd == "DANMU_MSG":
        info = msg.get("info") or []
        uid, name = _name_from_danmu(info)
        text = str(info[1]) if len(info) > 1 else ""
        emoticon = False
        try:
            emoticon = bool(info[0][12])  # 表情弹幕
        except (IndexError, TypeError):
            pass
        return {"kind": "danmaku", "uid": uid, "name": name, "text": text.strip(), "emoticon": emoticon}
    if cmd == "SUPER_CHAT_MESSAGE":
        u = d.get("user_info") or {}
        return {"kind": "sc", "uid": str(d.get("uid") or ""), "name": str(u.get("uname") or ""),
                "text": str(d.get("message") or "").strip(), "price": d.get("price") or 0}
    if cmd == "INTERACT_WORD_V2":
        return _interact_v2(d)
    if cmd in ("SEND_GIFT", "SEND_GIFT_V2") and d.get("giftName"):
        return {"kind": "gift", "uid": str(d.get("uid") or ""), "name": str(d.get("uname") or ""),
                "gift": str(d.get("giftName") or ""), "num": int(d.get("num") or 1),
                "coin": str(d.get("coin_type") or ""), "price": int(d.get("price") or 0)}
    if cmd == "GUARD_BUY":
        return {"kind": "guard", "uid": str(d.get("uid") or ""), "name": str(d.get("username") or ""),
                "gift": str(d.get("gift_name") or "舰长")}
    if cmd == "INTERACT_WORD":
        mt = int(d.get("msg_type") or 0)
        if mt in (1, 2):
            return {"kind": "enter" if mt == 1 else "follow", "uid": str(d.get("uid") or ""),
                    "name": str(d.get("uname") or "")}
        return None
    if cmd == "WATCHED_CHANGE":
        return {"kind": "watched", "num": int(d.get("num") or 0)}
    if cmd in ("LIVE", "PREPARING"):
        return {"kind": "live" if cmd == "LIVE" else "offline"}
    return None
