"""米游社官号帖子推送插件。

后台轮询米游社各游戏官方论坛，按账号白名单发现新帖后，
把帖子按原排版拆成「文字图 / 图片（含 GIF）/ 视频」块，
合并成一条转发消息推到配置的 QQ 群，避免刷屏。
"""

import asyncio
import json
import os
import tempfile
import time
from io import BytesIO
from pathlib import Path

from nonebot import get_bot, get_driver, logger, on_command
from ...core import scheduler
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed, NetworkError
from nonebot.permission import SUPERUSER
from PIL import Image

from . import api, config, render
from ...core import docfile, permissions
from ...core.live_groups import get_live_groups

# 当前实际推送目标群：机器人「实时在的群」∩「开启了米游社推送权限」的群。
# 每轮轮询开头刷新一次，替代写死的 _ACTIVE_GROUPS，这样换号/加群/退群都能自动同步，
# 也不会再往机器人已经不在的群发送而失败刷红。
_ACTIVE_GROUPS: list = []


async def _refresh_active_groups() -> list:
    """刷新本轮推送目标群（int 列表，按群号排序，结果稳定）。"""
    global _ACTIVE_GROUPS
    live = await get_live_groups()
    ids = sorted(
        int(g) for g in live
        if str(g).isdigit() and permissions.allowed(str(g), "miyoushe", True)
    )
    if ids != _ACTIVE_GROUPS:
        logger.info(f"[米游社推送] 本轮目标群更新为: {ids}")
    _ACTIVE_GROUPS = ids
    return ids

_seen_lock = asyncio.Lock()
# 已读记录结构：{forum_id: {post_id: {"ts": 创建时间戳, "groups": [已推送群号]}}}
# 帖子推送到全部目标群后，视为"完成"；完成记录永久保留，防止旧帖被重新推送。
_seen: dict = {}
_watch_task = None  # scheduler.Job
# 本次进程启动后尚未建立"启动基线"的论坛：建立基线前只把现有帖标记为已读、不推送，
# 避免机器人停机一段时间后重启时，把停机期间的历史资讯当成新帖一次性补推（刷屏）。
_startup_pending: set = set()
# 已读记录落盘节流：有改动时最多 60 秒写一次，正常退出时强制写
_seen_dirty = False
_last_seen_save = 0.0

# 已初始化的群（新群加入时建立基线，不补推历史帖）
_known_groups: set = set()
_KNOWN_GROUPS_FILE = os.path.join(os.path.dirname(config.SEEN_FILE), "miyoushe_known_groups.json")

# 各群启用米游社推送的基线时间戳（秒）：{gid: ts}
# 帖子发布时间早于基线的一律视为历史资讯，不推给该群；控制面板重新开启推送时写入
_group_baselines: dict = {}
_BASELINES_FILE = os.path.join(os.path.dirname(config.SEEN_FILE), "miyoushe_baselines.json")

# 发送用的临时文件（文字图/图片/视频），发送完成后统一清理
_pending_files = []

# 推送失败的群进入冷却：冷却期内不再为它重复下载 / 上传大图，避免风控类永久错误
# （如 retcode=1200 伪造合并转发被拒）每轮狂试，占满网络与事件循环、拖慢群消息回复。
_group_cooldown: dict = {}     # gid -> 下次允许尝试的 unix 时间戳
_FATAL_RETCODES = {1200}       # 风控 / 接口拒绝类，短时间内重试无意义
_FATAL_COOLDOWN = 30 * 60      # 风控类冷却 30 分钟，等风控缓解后自动恢复
_FAIL_COOLDOWN = 90            # 普通失败退避 90 秒，避免每轮重复下载大图

# 合并转发上传的"有效吞吐"估计（字节/秒，已含每文件开销），用于让标题在转发快完成时发出
_upload_rate_est = {"rate": 8.0 * 1024 * 1024, "n": 0}

# 机器人在群里的昵称缓存（合并转发节点显示用）
_bot_nick_cache = {"ts": 0.0, "name": ""}


# ============ 已读记录持久化 ============
def _load_seen():
    global _seen, _seen_dirty, _last_seen_save
    _seen = {}
    _seen_dirty = False
    _last_seen_save = 0.0
    try:
        if docfile.exists(config.SEEN_FILE):
            raw = docfile.load(config.SEEN_FILE)
            if isinstance(raw, dict):
                _seen = {int(k): v for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"[米游社推送] 读取已读记录失败: {e}")


def _load_known_groups():
    global _known_groups
    _known_groups = set()
    try:
        if docfile.exists(_KNOWN_GROUPS_FILE):
            raw = docfile.load(_KNOWN_GROUPS_FILE)
            if isinstance(raw, list):
                _known_groups = {str(g) for g in raw}
    except Exception as e:
        logger.warning(f"[米游社推送] 读取已初始化群记录失败: {e}")


def _save_known_groups():
    try:
        docfile.save(_KNOWN_GROUPS_FILE, sorted(_known_groups), indent=None)
    except Exception as e:
        logger.warning(f"[米游社推送] 保存已初始化群记录失败: {e}")


def _load_group_baselines():
    global _group_baselines
    _group_baselines = {}
    try:
        if docfile.exists(_BASELINES_FILE):
            raw = docfile.load(_BASELINES_FILE)
            if isinstance(raw, dict):
                _group_baselines = {str(k): float(v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"[米游社推送] 读取群基线失败: {e}")


def _save_group_baselines():
    try:
        docfile.save(_BASELINES_FILE, _group_baselines, indent=1)
    except Exception as e:
        logger.warning(f"[米游社推送] 保存群基线失败: {e}")


def _baseline_new_groups():
    """检测新加入的群，把所有历史帖标记为已推送到该群（建立基线，不补推历史）。"""
    global _seen_dirty
    configured = {str(g) for g in _ACTIVE_GROUPS}
    new_groups = configured - _known_groups
    if not new_groups:
        return
    for gid in new_groups:
        count = 0
        for fid in _seen:
            for pid, rec in _seen[fid].items():
                groups = set(rec.get("groups") or [])
                if gid not in groups:
                    groups.add(gid)
                    rec["groups"] = sorted(groups)
                    count += 1
        if gid not in _group_baselines:
            _group_baselines[gid] = time.time()
        logger.info(f"[米游社推送] 新群 {gid} 建立基线：跳过 {count} 条历史帖，之后只推新帖")
    _known_groups.update(new_groups)
    _save_known_groups()
    _save_group_baselines()
    _seen_dirty = True
    _save_seen(force=True)


def _save_seen(force: bool = False):
    global _seen_dirty, _last_seen_save
    now = time.time()
    if not force and (not _seen_dirty or now - _last_seen_save < 60):
        return
    try:
        docfile.save(config.SEEN_FILE, _seen, indent=1)
        _seen_dirty = False
        _last_seen_save = now
    except Exception as e:
        logger.warning(f"[米游社推送] 保存已读记录失败: {e}")


def _prune_seen():
    """清理记录：已完成的记录永久保留（官号帖数量少，避免旧帖被重新推送），
    未完成帖（推送一直失败）保留 3 天后放弃重试。"""
    global _seen_dirty
    now = time.time()
    changed = False
    for fid in list(_seen):
        keep = {}
        for pid, rec in _seen[fid].items():
            if _is_complete(rec):
                keep[pid] = rec
            else:
                ts = rec.get("ts") or now
                if now - ts <= 3 * 86400:
                    keep[pid] = rec
        if len(keep) != len(_seen[fid]):
            changed = True
        _seen[fid] = keep
    if changed:
        _seen_dirty = True


def _is_complete(rec: dict) -> bool:
    groups_done = set(rec.get("groups") or [])
    expected = {str(g) for g in _ACTIVE_GROUPS}
    return bool(groups_done) and expected.issubset(groups_done)


# ============ 发送辅助 ============
def _cleanup_files(paths, max_retries: int = 5, retry_delay: float = 1.0) -> list:
    """删除临时文件；文件可能被 NapCat/杀软短暂占用，失败时重试，剩余交由周期清扫兜底。"""
    remaining = list(paths)
    for attempt in range(max_retries):
        if not remaining:
            return []
        still = []
        for p in remaining:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                still.append(p)
                continue
            try:
                _pending_files.remove(p)
            except ValueError:
                pass
        if still and attempt < max_retries - 1:
            time.sleep(retry_delay)
        remaining = still
    if remaining:
        logger.warning(f"[米游社推送] {len(remaining)} 个临时文件删除失败，交由周期清扫处理")
    return remaining


def _cleanup_pending_files():
    """退出时兜底：清空并删除全部待清理临时文件。"""
    while _pending_files:
        paths = _pending_files[:]
        _pending_files.clear()
        _cleanup_files(paths)


def _temp_segment(data: bytes, suffix: str):
    """把文件字节写入临时文件，返回 (MessageSegment, 临时路径)。"""
    fd, path = tempfile.mkstemp(prefix="mys_send_", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    _pending_files.append(path)
    uri = Path(path).as_uri()
    if suffix.lower() == ".mp4":
        return MessageSegment.video(uri), path
    return MessageSegment.image(uri), path


def _guess_suffix(url: str, data: bytes) -> str:
    """根据 URL 和文件内容猜测后缀，GIF 必须保真才能动图。"""
    low = (url or "").lower()
    if ".gif" in low:
        return ".gif"
    try:
        with Image.open(BytesIO(data)) as im:
            fmt = (im.format or "jpg").lower()
            return ".gif" if fmt == "gif" else (".png" if fmt == "png" else ".jpg")
    except Exception:
        return ".jpg"


def _image_aspect(data: bytes) -> float:
    """图片长宽比（宽/高）。米游社的"小标题图"一般是 604x83 这种宽条（≈7:1），
    封面/正文图通常是 690x320（≈2:1）或更高的竖图。"""
    try:
        with Image.open(BytesIO(data)) as im:
            w, h = im.size
            return (w / h) if h else 0.0
    except Exception:
        return 0.0


def _pending_total_bytes() -> int:
    total = 0
    for p in _pending_files:
        try:
            total += os.path.getsize(p)
        except Exception:
            pass
    return total


def _update_upload_rate(total_bytes: int, duration: float):
    """用一次实际发送的耗时校准有效吞吐，让下一次标题时机更准。"""
    if duration <= 0.5 or total_bytes <= 0:
        return
    measured = total_bytes / duration
    n = _upload_rate_est["n"]
    if n >= 20:
        _upload_rate_est["rate"] = measured
    else:
        _upload_rate_est["rate"] = (_upload_rate_est["rate"] * n + measured) / (n + 1)
        _upload_rate_est["n"] = n + 1
    # 限制在合理范围，避免偶发慢速把估计拖得太低、导致下次标题晚于转发
    _upload_rate_est["rate"] = max(4.0 * 1024 * 1024, min(_upload_rate_est["rate"], 16.0 * 1024 * 1024))
    _save_rate()


def _load_rate():
    try:
        if docfile.exists(config.RATE_FILE):
            raw = docfile.load(config.RATE_FILE)
            if isinstance(raw, dict):
                r = float(raw.get("rate") or 0)
                if r > 0:
                    _upload_rate_est["rate"] = r
                    _upload_rate_est["n"] = int(raw.get("n") or 0)
    except Exception as e:
        logger.warning(f"[米游社推送] 读取速率估计失败: {e}")


def _save_rate():
    try:
        docfile.save(config.RATE_FILE, _upload_rate_est)
    except Exception as e:
        logger.warning(f"[米游社推送] 保存速率估计失败: {e}")


async def _bot_nickname(bot, group_id: int) -> str:
    now = time.time()
    if _bot_nick_cache["name"] and now - _bot_nick_cache["ts"] < 300:
        return _bot_nick_cache["name"]
    name = "机器人"
    try:
        info = await bot.get_group_member_info(
            group_id=group_id, user_id=int(config.BOT_QQ or 0)
        )
        name = info.get("card") or info.get("nickname") or "机器人"
    except Exception:
        pass
    _bot_nick_cache["ts"] = now
    _bot_nick_cache["name"] = name
    return name


def _node(name: str, uin: str, content: list):
    return {"type": "node", "data": {"name": name, "uin": uin, "content": content}}


async def _build_nodes(
    bot, group_id: int, forum_info: dict, item: dict, full: dict, blocks: list,
    videos_as_cover: bool = False, pending: list | None = None,
) -> list:
    """把帖子内容块组装成合并转发的 node 列表（标题由调用方作为第一句插入）。

    规则：图片块后面如果紧跟文字块，就把图片和文字合并成一张图；
    图片后面没有文字就单独作为一张图片。
    """
    name = await _bot_nickname(bot, group_id)
    uin = config.BOT_QQ or "0"
    nodes = []

    def _mk(data: bytes, suffix: str):
        """创建临时文件，并登记到本帖子待清理列表。"""
        seg, path = _temp_segment(data, suffix)
        if pending is not None:
            pending.append(path)
        return seg
    # 视频超过该大小直接改用封面，避免下载几百 MB 导致内存/上传失败
    MAX_VIDEO_BYTES = 100 * 1024 * 1024

    # 先探测视频大小：超大的视频不下载原片，直接发封面
    if not videos_as_cover:
        video_blocks = [b for b in blocks if b["type"] == "video"]
        probe_results = await asyncio.gather(
            *(api.probe_file_size(b["url"]) for b in video_blocks),
            return_exceptions=True,
        )
        for block, probe in zip(video_blocks, probe_results):
            size = probe if isinstance(probe, int) else None
            if size is not None and size > MAX_VIDEO_BYTES:
                logger.warning(
                    f"[米游社推送] 视频 {size / 1024 / 1024:.0f}MB 超过上限 "
                    f"{MAX_VIDEO_BYTES / 1024 / 1024:.0f}MB，改用封面"
                )
                block["_fallback_cover"] = True

    # ---- 第一步：并行下载所有图片/视频（最多 8 个并发），避免 20 多张图一张张下太久 ----
    sem = asyncio.Semaphore(12)

    async def _dl(url: str, max_bytes: int | None = None):
        async with sem:
            return await api.download_file(url, max_bytes=max_bytes)

    dl_specs = []
    for block in blocks:
        btype = block["type"]
        if btype == "image":
            dl_specs.append((block, block["url"], None))
        elif btype == "video":
            if videos_as_cover or block.get("_fallback_cover"):
                if block.get("cover"):
                    dl_specs.append((block, block["cover"], None))
            else:
                dl_specs.append((block, block["url"], MAX_VIDEO_BYTES))
    dl_results = await asyncio.gather(
        *[_dl(url, max_bytes=mx) for _, url, mx in dl_specs],
        return_exceptions=True,
    )
    data_map = {}
    for (block, _url, _mx), res in zip(dl_specs, dl_results):
        data_map[id(block)] = None if isinstance(res, Exception) else res

    # ---- 第二步：按原顺序组装节点 ----
    i = 0
    while i < len(blocks):
        block = blocks[i]
        btype = block["type"]
        try:
            if btype == "image":
                data = data_map.get(id(block))
                if not data:
                    logger.warning(f"[米游社推送] 图片下载失败，跳过: {block['url'][:60]}")
                    i += 1
                    continue
                # 只有"宽条小标题图"（长宽比≥5）且后面紧跟文字时才合并成一张
                if (
                    i + 1 < len(blocks)
                    and blocks[i + 1]["type"] == "text"
                    and _image_aspect(data) >= 5.0
                ):
                    try:
                        merged = render.merge_image_and_text(data, blocks[i + 1]["text"])
                        seg = _mk(merged, ".png")
                        nodes.append(_node(name, uin, [seg]))
                        i += 2
                        continue
                    except Exception as e:
                        logger.warning(f"[米游社推送] 标题图与文字合并失败，分开发送: {e}")
                # 封面/正文图（或合并失败）：单独作为一张图片
                seg = _mk(data, _guess_suffix(block["url"], data))
                nodes.append(_node(name, uin, [seg]))
            elif btype == "text":
                png = render.render_text_to_png(block["text"])
                seg = _mk(png, ".png")
                nodes.append(_node(name, uin, [seg]))
            elif btype == "video":
                if videos_as_cover or block.get("_fallback_cover"):
                    data = data_map.get(id(block))
                    if data:
                        seg = _mk(data, _guess_suffix(block.get("cover") or "", data))
                        nodes.append(_node(name, uin, [seg]))
                    else:
                        nodes.append(_node(name, uin, [MessageSegment.text("[视频]")]))
                    i += 1
                    continue
                data = data_map.get(id(block))
                if not data:
                    logger.warning(f"[米游社推送] 视频下载失败/超限，尝试封面: {block['url'][:60]}")
                    cover_url = block.get("cover")
                    if cover_url:
                        try:
                            cover_data = await api.download_file(cover_url)
                            seg = _mk(cover_data, _guess_suffix(cover_url, cover_data))
                            nodes.append(_node(name, uin, [seg]))
                        except Exception as e:
                            logger.warning(f"[米游社推送] 视频封面下载失败，跳过: {e}")
                    i += 1
                    continue
                seg = _mk(data, ".mp4")
                nodes.append(_node(name, uin, [seg]))
        except Exception as e:
            logger.warning(f"[米游社推送] {btype} 块处理失败: {e}")
        i += 1
    return nodes


class ForwardSendUncertain(Exception):
    """发送超时，无法确认消息是否真的发出。

    超时只代表没收到确认，NapCat 可能已经把消息发出去了。此时盲目重试/下轮重推
    会让群里出现两条一模一样的推送，所以调用方应按"已推送"处理。
    """


class QQUnavailable(RuntimeError):
    """No confirmed online session: pause new work, do not treat it as delivered."""


async def _require_qq_online(bot):
    # Query the existing OneBot connection; never initiate login or change status.
    try:
        status = await bot.get_status()
    except Exception as exc:
        raise QQUnavailable("无法确认 QQ 在线，暂停推送") from exc
    if not isinstance(status, dict) or status.get("online") is not True:
        raise QQUnavailable("QQ 未在线，暂停推送，等待人工恢复登录")


async def _send_forward_with_retry(group_id: int, nodes: list):
    """One submission only: missing acknowledgement is not proof of non-delivery."""
    bot = get_bot()
    await _require_qq_online(bot)
    try:
        await bot.send_forward_msg(group_id=group_id, messages=nodes)
    except ActionFailed:
        raise
    except NetworkError as exc:
        logger.warning("[米游社推送] 合并转发未收到确认；结果不确定，不自动重发")
        raise ForwardSendUncertain from exc


async def _publish_post_to_group(group_id: int, forum_info: dict, item: dict) -> bool:
    """推送一条帖子到指定群：标题作为合并转发第一句，帖子内容跟在其后。返回是否成功。"""
    post = item.get("post", {}) or {}
    pid = str(post.get("post_id", ""))
    my_files = []
    try:
        bot = get_bot()
        await _require_qq_online(bot)
        full = await api.fetch_full_post(pid)
        blocks = api.parse_post_blocks(full)
        logger.info(
            f"[米游社推送] 帖子 {pid} 拆成 {len(blocks)} 个块: {[b['type'] for b in blocks]}"
        )
        bot = get_bot()
        # 1) 先下载/组装所有内容块（图片、视频、文字渲染），
        #    这样标题和合并转发能紧挨着发出，不会出现"标题发了、转发半天才来"
        uid = str((item.get("user") or {}).get("uid") or "")
        prefix = api.ACCOUNT_PREFIX.get(uid, forum_info["name"] + "资讯推送")
        title_text = api.build_push_title(prefix, post.get("subject"))
        nodes = await _build_nodes(bot, group_id, forum_info, item, full, blocks, pending=my_files)
        if not nodes:
            logger.warning(f"[米游社推送] 帖子 {pid} 没有可转发的内容块")
            return True
        # 标题放进合并转发的第一句
        name = await _bot_nickname(bot, group_id)
        nodes.insert(0, _node(name, config.BOT_QQ or "0", [MessageSegment.text(title_text)]))
        has_video = any(b["type"] == "video" for b in blocks)

        total_bytes = _pending_total_bytes()
        est_seconds = total_bytes / _upload_rate_est["rate"]
        logger.info(
            f"[米游社推送] 帖子 {pid} 待上传 {total_bytes / 1024 / 1024:.1f}MB，"
            f"估算转发耗时 {est_seconds:.1f}s"
        )

        async def _forward_job():
            try:
                await _send_forward_with_retry(group_id, nodes)
                return True
            except ForwardSendUncertain:
                # 超时但可能已发出：按已推送处理，避免下轮轮询重复推
                logger.warning(
                    f"[米游社推送] 帖子 {pid} 发送结果不确定（可能已发出），按已推送处理避免重复"
                )
                return True
            except ActionFailed as e:
                if has_video:
                    await _require_qq_online(bot)
                    # 视频节点可能导致转发失败：换成视频封面再试一次
                    logger.warning(
                        f"[米游社推送] 帖子 {pid} 带视频的合并转发失败，改用视频封面重试: "
                        f"{getattr(e, 'message', e)}"
                    )
                    try:
                        nodes2 = await _build_nodes(
                            bot, group_id, forum_info, item, full, blocks, videos_as_cover=True, pending=my_files
                        )
                        nodes2.insert(0, _node(name, config.BOT_QQ or "0", [MessageSegment.text(title_text)]))
                        await _send_forward_with_retry(group_id, nodes2)
                        return True
                    except ForwardSendUncertain:
                        logger.warning(
                            f"[米游社推送] 帖子 {pid} 封面重试发送结果不确定（可能已发出），按已推送处理避免重复"
                        )
                        return True
                raise

        _t_fwd_start = time.time()
        try:
            ok = await _forward_job()
        except Exception as e:
            logger.error(f"[米游社推送] 帖子 {pid} 合并转发发送失败: {e}")
            ok = False
            rc = getattr(e, "retcode", None)
            if rc in _FATAL_RETCODES:
                _group_cooldown[group_id] = time.time() + _FATAL_COOLDOWN
                logger.warning(
                    f"[米游社推送] 群 {group_id} 合并转发被拒（retcode={rc}，疑似风控），"
                    f"该群冷却 30 分钟不再重试，以免反复下载大图拖慢机器人"
                )
            else:
                _group_cooldown[group_id] = time.time() + _FAIL_COOLDOWN
        fwd_duration = time.time() - _t_fwd_start
        _update_upload_rate(total_bytes, fwd_duration)
        logger.info(f"[米游社推送] 帖子 {pid} 发送完成：转发耗时 {fwd_duration:.1f}s")
        if ok:
            _group_cooldown.pop(group_id, None)
        return ok
    except Exception as e:
        logger.error(f"[米游社推送] 帖子 {pid} 发送到群 {group_id} 失败: {e}")
        _group_cooldown[group_id] = time.time() + _FAIL_COOLDOWN
        return False
    finally:
        await asyncio.to_thread(_cleanup_files, my_files)


async def _publish_post(forum_id: int, forum_info: dict, item: dict, target_groups=None) -> list:
    """推送到指定目标群（默认全部配置群）。返回已成功推送的群号列表。"""
    post = item.get("post", {}) or {}
    pid = str(post.get("post_id", ""))
    target_groups = list(target_groups) if target_groups is not None else list(_ACTIVE_GROUPS)
    sent_groups = []
    for gid in target_groups:
        if not permissions.allowed(gid, "miyoushe", True):
            logger.info(f"[米游社推送] 群{gid} 已关闭米游社推送权限，跳过")
            continue
        if await _publish_post_to_group(gid, forum_info, item):
            sent_groups.append(gid)
            logger.info(f"[米游社推送] 已推送 [{forum_info['name']}] {pid} 到群 {gid}")
        else:
            logger.error(f"[米游社推送] 发送到群 {gid} 失败（帖子 {pid}），下轮重试剩余群")
            continue
    return sent_groups


async def _handle_new_post(forum_id: int, forum_info: dict, item: dict):
    """处理一条新官号帖：推送 + 记录进度。"""
    global _seen_dirty
    post = item.get("post", {}) or {}
    pid = str(post.get("post_id", ""))
    created = post.get("created_at") or int(time.time())

    async with _seen_lock:
        rec = _seen.setdefault(forum_id, {}).get(pid, {"ts": created, "groups": []})
        done = set(rec.get("groups") or [])
    if all(str(g) in done for g in _ACTIVE_GROUPS):
        return

    # 基线之后的帖子才推：新群/重新开启推送时写入基线，早于基线的历史资讯一律跳过；
    # 权限关闭的群也直接记为"已处理"，避免之后重新开启时补推旧帖。
    baseline_skip = set()
    disabled = set()
    for g in _ACTIVE_GROUPS:
        gs = str(g)
        if gs in done:
            continue
        if _group_baselines.get(gs, 0) > created:
            baseline_skip.add(gs)
        elif not permissions.allowed(g, "miyoushe", True):
            disabled.add(gs)
    now_ts = time.time()
    pending = [
        g for g in _ACTIVE_GROUPS
        if str(g) not in done and str(g) not in baseline_skip and str(g) not in disabled
        and _group_cooldown.get(g, 0) <= now_ts
    ]
    if not pending:
        async with _seen_lock:
            rec = _seen.setdefault(forum_id, {}).get(pid, {"ts": created, "groups": []})
            rec["ts"] = created
            rec["groups"] = sorted(done | baseline_skip | disabled)
            _seen[forum_id][pid] = rec
            _seen_dirty = True
        _save_seen(force=True)
        return

    try:
        sent_groups = await _publish_post(forum_id, forum_info, item, target_groups=pending)
    except Exception as e:
        logger.error(f"[米游社推送] 帖子 {pid} 处理失败（下轮重试）: {e}")
        sent_groups = []

    async with _seen_lock:
        _seen_dirty = True
        rec = _seen.setdefault(forum_id, {}).get(pid, {"ts": created, "groups": []})
        rec["ts"] = created
        rec["groups"] = sorted(
            set(rec.get("groups") or []) | {str(g) for g in sent_groups} | baseline_skip | disabled
        )
        _seen[forum_id][pid] = rec
    _save_seen(force=True)


# ============ 轮询 ============
async def _poll_forum(forum_id: int):
    global _seen_dirty
    _load_group_baselines()
    forum_info = api.FORUM_INFO.get(forum_id)
    if not forum_info:
        logger.warning(f"[米游社推送] 论坛 {forum_id} 未配置显示信息，跳过")
        return

    try:
        items = await api.fetch_forum_post_list(forum_id, config.PAGE_SIZE)
    except Exception as e:
        logger.warning(f"[米游社推送] 拉取 [{forum_info['name']}] 列表失败: {e}")
        return

    allowed_uids = api.FORUM_ACCOUNTS.get(forum_id)
    if allowed_uids is None:
        logger.warning(f"[米游社推送] 论坛 {forum_id} 未配置推送账号，跳过")
        return
    official = [
        it
        for it in items
        if api.is_official_post(it)
        and str((it.get("user") or {}).get("uid", "")) in allowed_uids
    ]
    official.sort(
        key=lambda it: (
            (it.get("post", {}).get("created_at") or 0),
            str(it.get("post", {}).get("post_id", "")),
        )
    )

    async with _seen_lock:
        first_run = forum_id not in _seen
        # 首次安装（无记录）或本次进程启动首轮：都只建基线、不补推历史
        baseline_only = (first_run or forum_id in _startup_pending) and not config.BACKFILL

    for item in official:
        post = item.get("post", {}) or {}
        pid = str(post.get("post_id", ""))
        if not pid:
            continue

        async with _seen_lock:
            rec = _seen.setdefault(forum_id, {}).get(pid)
            if rec and _is_complete(rec):
                continue

        # 首次启动 / 进程重启首轮：只建立基线，不推送历史帖
        if baseline_only:
            async with _seen_lock:
                _seen[forum_id][pid] = {
                    "ts": post.get("created_at") or int(time.time()),
                    "groups": [str(g) for g in _ACTIVE_GROUPS],
                }
            continue

        await _handle_new_post(forum_id, forum_info, item)

    if baseline_only:
        _startup_pending.discard(forum_id)
        logger.info(
            f"[米游社推送] [{forum_info['name']}] 启动基线：已把 {len(official)} 条现有官号帖标记为已读"
            f"（不补推历史资讯），之后只推本次启动后的新帖"
        )
        _seen_dirty = True
        _save_seen()

    _prune_seen()
    _save_seen()


_sweep_counter = 0


async def _watch_tick():
    """一轮轮询（由统一定时任务按 POLL_INTERVAL 调用）。"""
    global _sweep_counter
    try:
        # 每轮重新对账实时在群与权限，换号/加群/退群最迟一个轮询周期内自动生效
        ids = await _refresh_active_groups()
        if not ids:
            logger.info("[米游社推送] 当前没有「在群且开启权限」的目标群，本轮跳过，稍后自动重试")
        else:
            for fid in config.FORUM_IDS:
                await _poll_forum(fid)
    finally:
        _sweep_counter += 1
        # 每 30 轮（默认约 30 分钟）清扫一次发送残留的临时文件，保证推送后不残留
        if _sweep_counter >= 30:
            _sweep_counter = 0
            _sweep_stale_temp(min_age=300)


# ============ 生命周期 ============
def _sweep_stale_temp(min_age: float = 0.0):
    """删除残留的 mys_send_* 临时文件（推送完成后不保留任何资料）。

    min_age>0 时只删除超过该秒数的文件，避免误删正在上传的图片；
    正常流程发完即删，强杀进程/删除失败才会残留，这里兜底清扫。
    """
    try:
        tmp_dir = tempfile.gettempdir()
        now = time.time()
        deleted = 0
        for name in os.listdir(tmp_dir):
            if not name.startswith("mys_send_"):
                continue
            p = os.path.join(tmp_dir, name)
            try:
                if p in _pending_files:
                    continue
                if min_age > 0 and now - os.path.getmtime(p) < min_age:
                    continue
                os.unlink(p)
                deleted += 1
            except FileNotFoundError:
                continue
            except Exception:
                continue
        if deleted:
            logger.info(f"[米游社推送] 清扫残留临时文件 {deleted} 个")
    except Exception:
        pass


@get_driver().on_startup
async def _start_watcher():
    global _watch_task, _startup_pending
    _load_seen()
    _load_known_groups()
    _load_group_baselines()
    _baseline_new_groups()
    _load_rate()
    _sweep_stale_temp()
    # 每次进程启动都重新建立启动基线：第一轮只记录、不补推任何历史资讯
    _startup_pending = set(config.FORUM_IDS)
    if _watch_task is None:
        logger.info(
            f"[米游社推送] 后台任务启动：论坛={config.FORUM_IDS}，"
            f"间隔={config.POLL_INTERVAL}s，目标群=机器人实时在群 ∩ 开启推送权限"
        )
        _watch_task = scheduler.every("米游社推送", lambda: config.POLL_INTERVAL, _watch_tick)


@get_driver().on_shutdown
async def _stop_watcher():
    global _watch_task
    if _watch_task:
        await scheduler.stop("米游社推送")
        _watch_task = None
    _cleanup_pending_files()
    _save_seen(force=True)


# ============ 测试命令：把某游戏最新一条官号资讯按正式格式推到当前群 ============
# 仅群主/管理员/超级用户可用：每次都会下载整帖图片视频并发合并转发，普通群友可随意刷
push_test_cmd = on_command("推送测试", priority=5, block=True, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER)

_GAME_ALIAS = {
    "原神": 28, "ys": 28,
    "星铁": 53, "崩铁": 53, "星穹铁道": 53, "sr": 53,
    "绝区零": 58, "zzz": 58,
    "崩坏三": 6, "崩坏3": 6, "bh3": 6,
    "崩缘": 947, "因缘": 947, "hna": 947,
    "星布谷地": 949, "planet": 949,
}


@push_test_cmd.handle()
async def handle_push_test(event: GroupMessageEvent):
    """推送测试 [游戏名 或 帖子ID]：按正式格式把资讯发到当前群。

    不带参数=绝区零最新一条；支持游戏名（原神/崩铁/绝区零/崩坏三/崩缘/星布谷地）；
    也支持直接指定帖子 ID（如 推送测试 77263474）来测试任意帖子。
    """
    if not isinstance(event, GroupMessageEvent):
        await push_test_cmd.finish("这个命令只在群里可用")
    raw = event.get_plaintext().replace("推送测试", "").strip()

    # 直接指定帖子 ID 测试
    if raw.isdigit():
        pid = raw
        try:
            full = await api.fetch_full_post(pid)
        except Exception as e:
            logger.error(f"[米游社推送] 推送测试拉取帖子失败: {e}")
            await push_test_cmd.finish("拉取帖子失败，请检查帖子 ID 是否正确")
        if not full:
            await push_test_cmd.finish("帖子不存在或无法访问")
        forum_id = int(full.get("f_forum_id") or 0)
        info = api.FORUM_INFO.get(forum_id)
        if not info:
            await push_test_cmd.finish("这个帖子不在当前推送的游戏版块内")
        item = {
            "post": {
                "post_id": str(full.get("post_id")),
                "subject": full.get("subject"),
                "created_at": full.get("created_at"),
            },
            "user": {"uid": str(full.get("uid") or "")},
        }
        logger.info(f"[米游社推送] 推送测试：指定帖子 {pid}（{info['name']}）")
        if await _publish_post_to_group(event.group_id, info, item):
            logger.info("[米游社推送] 推送测试完成")
        else:
            await push_test_cmd.finish("发送失败，请查看日志")
        return

    # 按游戏名取最新一条
    forum_id = _GAME_ALIAS.get(raw, 58)  # 默认绝区零
    info = api.FORUM_INFO.get(forum_id)
    if not info:
        await push_test_cmd.finish(f"不认识这个游戏：{raw or '空'}")

    try:
        items = await api.fetch_forum_post_list(forum_id, 15)
    except Exception as e:
        logger.error(f"[米游社推送] 推送测试拉取失败: {e}")
        await push_test_cmd.finish("拉取帖子失败，请稍后再试")

    allowed = api.FORUM_ACCOUNTS.get(forum_id, set())
    official = [
        it
        for it in items
        if api.is_official_post(it)
        and str((it.get("user") or {}).get("uid", "")) in allowed
    ]
    if not official:
        await push_test_cmd.finish(f"{info['name']} 官方区暂时没有白名单账号的帖子")
    official.sort(key=lambda it: (it.get("post", {}).get("created_at") or 0), reverse=True)

    it = official[0]
    pid = str((it.get("post") or {}).get("post_id", ""))
    logger.info(f"[米游社推送] 推送测试：{info['name']} {pid}")
    if await _publish_post_to_group(event.group_id, info, it):
        logger.info("[米游社推送] 推送测试完成")
    else:
        await push_test_cmd.finish("发送失败，请查看日志")
