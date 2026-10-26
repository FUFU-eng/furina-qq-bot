"""筛图收件箱：群友发的图 / 搜图原图 / Pixiv 点图先收进来，人工「留下 / 丢掉」后留下的进 Eagle。

机器人（收图、强化、手机接口）和面板（电脑上的筛图页）共用这一份逻辑，状态都在 SQLite：
- ns ``pic_inbox``：每张图一条，key = 内容 md5 前 16 位
    status: pending 待筛 / keep 已留下（等强化完再导出）/ trash 丢掉（撤销窗口内还在回收区）/ out 已导出（撤销窗口内保留副本）
    enh:    no 不需要 / todo 等强化 / done 已强化 / fail 强化失败（照常导出原图）
- ns ``pic_seen``：见过的图的感知哈希（dHash），丢掉、导出后还留着，同一张图被别人再发一次不会重复收。
- ns ``pic_inbox_meta``：手机端连接密码等。

丢掉的图在撤销窗口（默认 5 分钟）过后直接删除；留下的图复制进 Eagle 的自动导入文件夹，不打标签、不碰图库。
"""
from __future__ import annotations

import hashlib
import io
import os
import secrets
import shutil
import threading
import time
from pathlib import Path

from ..core import db, env

NS = "pic_inbox"
NS_SEEN = "pic_seen"
NS_META = "pic_inbox_meta"
NS_ENH_AUDIT = "pic_enhance_audit"
ENH_AUDIT_KEEP_DAYS = 30

DEFAULT_DIR = r"D:\FurinaPicInbox"
DEFAULT_EXPORT = r"D:\Pictures\Eagle"
UNDO_SECONDS = 300
SEEN_KEEP_DAYS = 120
NEAR_DUP_BITS = 4          # dHash 汉明距离 ≤ 4 视为同一张（重新压缩/轻微缩放）
MAX_PIXELS = 80_000_000    # 超大图直接跳过，免得解码吃内存

_lock = threading.RLock()


# ---------------- 配置 ----------------
def enabled() -> bool:
    return env.get_bool("PIC_INBOX_ENABLED", True)


def root() -> Path:
    return Path(env.get("PIC_INBOX_DIR", DEFAULT_DIR))


def export_dir() -> Path:
    """Eagle 实际监听的导入文件夹。

    Eagle 4 的「自动导入」会在设置的文件夹里按图库名再建一层子文件夹（如 足迹\\足迹），
    只监听这一层；直接放在外层不会被导入。所以：外层里有和当前图库同名的子文件夹就用它，
    只有一个子文件夹也用它，否则才用设置的路径本身。
    """
    base = Path(env.get("PIC_INBOX_EXPORT_DIR", DEFAULT_EXPORT))
    lib = env.get("EAGLE_LIBRARY_DIR", "")
    name = Path(lib.rstrip("\\/")).stem if lib else ""
    try:
        if name and (base / name).is_dir():
            return base / name
        subs = [d for d in base.iterdir() if d.is_dir() and not d.name.startswith("_")]
        if len(subs) == 1:
            return subs[0]
    except OSError:
        pass
    return base


def min_side() -> int:
    return max(0, env.get_int("PIC_INBOX_MIN_SIDE", 360))


def enhance_under_bytes() -> int:
    """小于这个体积的图排队强化；0 = 不强化。"""
    return max(0, env.get_int("PIC_INBOX_ENHANCE_UNDER_KB", 1024)) * 1024


def _move(src: Path, dst: Path, tries: int = 30) -> None:
    """os.replace 带重试：Windows 上杀毒/索引/看图软件刚碰过的文件会被短暂占用（WinError 32）。"""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.1)


def _unlink(p: Path, tries: int = 20) -> None:
    for i in range(tries):
        try:
            p.unlink(missing_ok=True)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.1)


def _dir(name: str) -> Path:
    p = root() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def pending_dir() -> Path:
    return _dir("pending")


def trash_dir() -> Path:
    return _dir("trash")


def out_dir() -> Path:
    return _dir("exported")


def preview_dir() -> Path:
    return _dir("preview")


def work_dir() -> Path:
    return _dir("work")


# ---------------- 图片工具 ----------------
def _open(data: bytes):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    return Image.open(io.BytesIO(data))


def probe(data: bytes) -> dict | None:
    """读格式/尺寸/是否动图；不是图片或读不了返回 None。"""
    try:
        im = _open(data)
        fmt = (im.format or "").upper()
        w, h = im.size
        animated = bool(getattr(im, "is_animated", False)) or int(getattr(im, "n_frames", 1) or 1) > 1
        return {"fmt": fmt, "w": int(w), "h": int(h), "animated": animated}
    except Exception:
        return None


def dhash(data: bytes) -> str:
    """64 位差值哈希（9x8 灰度），十六进制。"""
    from PIL import Image

    im = _open(data)
    try:
        im.seek(0)
    except Exception:
        pass
    g = im.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "BMP": "bmp", "GIF": "gif", "TIFF": "tif"}


def looks_like_sticker(info: dict, size: int) -> bool:
    """表情包启发式：动图、GIF、太小的图，或者「小尺寸 + 小体积」的图。"""
    if info["animated"] or info["fmt"] == "GIF":
        return True
    short, long_ = min(info["w"], info["h"]), max(info["w"], info["h"])
    if short < min_side():
        return True
    if long_ < 600 and size < 60 * 1024:
        return True
    return False


# ---------------- 去重 ----------------
def _seen_match(md5: str, dh: str) -> str | None:
    seen = db.items(NS_SEEN)
    if dh in seen:
        return dh
    for k, v in seen.items():
        if isinstance(v, dict) and v.get("md5") == md5:
            return k
        if hamming(k, dh) <= NEAR_DUP_BITS:
            return k
    return None


def _mark_seen(dh: str, md5: str, how: str) -> None:
    if dh:
        db.put(NS_SEEN, dh, {"t": time.time(), "md5": md5, "how": how})


# ---------------- 收图 ----------------
def add_bytes(data: bytes, src: str, gid: str | int = "", uid: str | int = "", uname: str = "",
              strict: bool = True) -> tuple[str | None, str]:
    """收一张图。返回 (id, 原因)；id 为 None 表示没收（表情包/重复/不是图）。

    strict=False 用于搜图原图、Pixiv 点图：只挡动图，不做尺寸上的表情包判断。
    """
    if not data:
        return None, "empty"
    info = probe(data)
    if not info:
        return None, "not_image"
    if info["w"] * info["h"] > MAX_PIXELS:
        return None, "too_big"
    if info["animated"] or info["fmt"] == "GIF":
        return None, "animated"
    if strict and looks_like_sticker(info, len(data)):
        return None, "sticker"
    md5 = hashlib.md5(data).hexdigest()
    iid = md5[:16]
    try:
        dh = dhash(data)
    except Exception:
        return None, "not_image"
    with _lock:
        if db.get(NS, iid) is not None:
            return None, "dup"
        if _seen_match(md5, dh):
            return None, "seen"
        ext = _EXT.get(info["fmt"], "jpg")
        name = f"{iid}.{ext}"
        path = pending_dir() / name
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        under = enhance_under_bytes()
        item = {
            "id": iid, "file": name, "src": src, "gid": str(gid or ""), "uid": str(uid or ""),
            "uname": str(uname or "")[:40], "ts": time.time(), "size": len(data), "w": info["w"], "h": info["h"],
            "osize": len(data), "ow": info["w"], "oh": info["h"], "dh": dh, "md5": md5,
            "status": "pending", "st_ts": 0, "enh": "todo" if (under and len(data) < under) else "no",
        }
        db.put(NS, iid, item)
        _mark_seen(dh, md5, "in")
    return iid, "ok"


def add_file(path: str | os.PathLike, src: str, gid="", uid="", uname="", strict: bool = False):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None, "unreadable"
    return add_bytes(data, src, gid, uid, uname, strict=strict)


# ---------------- 查询 ----------------
def get(iid: str) -> dict | None:
    it = db.get(NS, str(iid))
    return it if isinstance(it, dict) else None


def file_path(it: dict) -> Path:
    base = {"pending": pending_dir, "keep": pending_dir, "trash": trash_dir, "out": out_dir}.get(it.get("status"), pending_dir)
    return base() / it["file"]


def _all() -> list[dict]:
    return [v for v in db.items(NS).values() if isinstance(v, dict) and v.get("id")]


def pending(limit: int = 60, newest_first: bool = False, offset: int = 0) -> list[dict]:
    rows = [it for it in _all() if it.get("status") == "pending"]
    rows.sort(key=lambda it: it.get("ts", 0), reverse=newest_first)
    off = max(0, int(offset))
    return rows[off: off + max(1, int(limit))]


def stats() -> dict:
    rows = _all()
    st = {"pending": 0, "keep_wait": 0, "enh_todo": 0, "undo": 0}
    for it in rows:
        s = it.get("status")
        if s == "pending":
            st["pending"] += 1
        elif s == "keep":
            st["keep_wait"] += 1
        elif s in ("trash", "out"):
            st["undo"] += 1
        if s in ("pending", "keep") and it.get("enh") in ("todo", "run"):
            st["enh_todo"] += 1
    day = time.strftime("%Y-%m-%d")
    d = db.get(NS_META, "day:" + day) or {}
    st["kept_today"] = int(d.get("keep", 0))
    st["dropped_today"] = int(d.get("drop", 0))
    return st


def _count(kind: str, n: int = 1) -> None:
    day = time.strftime("%Y-%m-%d")
    db.update(NS_META, "day:" + day, lambda d: {**(d or {}), kind: int((d or {}).get(kind, 0)) + n}, default={})


def public(it: dict) -> dict:
    """给手机端/面板展示的字段。"""
    return {k: it.get(k) for k in ("id", "src", "gid", "uid", "uname", "ts", "size", "w", "h", "osize", "ow", "oh", "enh", "status")}


# ---------------- 筛选 ----------------
def decide(iid: str, keep: bool) -> dict:
    with _lock:
        it = get(iid)
        if not it or it.get("status") != "pending":
            return {"ok": False, "reason": "gone"}
        src = file_path(it)
        if keep:
            it.update(status="keep", st_ts=time.time())
            db.put(NS, iid, it)
            _count("keep")
        else:
            try:
                if src.exists():
                    _move(src, trash_dir() / it["file"])
            except OSError:
                return {"ok": False, "reason": "busy"}  # 文件被占用：保持待筛，下次再丢
            it.update(status="trash", st_ts=time.time())
            db.put(NS, iid, it)
            _mark_seen(it.get("dh", ""), it.get("md5", ""), "drop")
            _count("drop")
    if keep:
        export_ready(only=iid)
    return {"ok": True}


def decide_many(ids: list[str], keep: bool) -> dict:
    """手机端多选后一次提交：逐张处理，返回成功/失败的 id。"""
    done, failed, reasons = [], [], {}
    for iid in dict.fromkeys(str(i) for i in ids if i):
        # 断网重试：同一个决定已经落地就算成功，不重复计数/导出。
        it = get(iid)
        already = it and (it.get("status") in ("keep", "out") if keep else it.get("status") == "trash")
        res = {"ok": True} if already else decide(iid, keep)
        (done if res.get("ok") else failed).append(iid)
        if not res.get("ok"):
            reasons[iid] = res.get("reason", "gone")
    return {"ok": True, "done": done, "failed": failed, "reasons": reasons}


def undo_many(ids: list[str]) -> dict:
    done, failed = [], []
    for iid in dict.fromkeys(str(i) for i in ids if i):
        (done if undo(iid).get("ok") else failed).append(iid)
    return {"ok": True, "done": done, "failed": failed}


def undo(iid: str) -> dict:
    """撤销上一次决定：丢掉的从回收区拿回来；已导出的若 Eagle 还没取走就撤回。"""
    with _lock:
        it = get(iid)
        if not it:
            return {"ok": False, "reason": "gone"}
        st = it.get("status")
        if st == "pending":
            return {"ok": True}
        if st == "trash":
            p = trash_dir() / it["file"]
            if not p.exists():
                return {"ok": False, "reason": "gone"}
            _move(p, pending_dir() / it["file"])
            _count("drop", -1)
        elif st == "keep":
            _count("keep", -1)
        elif st == "out":
            dst = Path(it.get("out_path") or "")
            if dst and dst.exists():
                try:
                    _unlink(dst)
                except OSError:
                    return {"ok": False, "reason": "imported"}
            elif dst:
                return {"ok": False, "reason": "imported"}  # Eagle 已经导入走了
            p = out_dir() / it["file"]
            if it.get("stale") and (pending_dir() / it["file"]).exists():
                _unlink(p, 3)
            elif not p.exists():
                return {"ok": False, "reason": "gone"}
            else:
                _move(p, pending_dir() / it["file"])
            it.pop("stale", None)
            _count("keep", -1)
        it.update(status="pending", st_ts=0)
        it.pop("out_path", None)
        db.put(NS, iid, it)
    return {"ok": True}


def _export_one(it: dict) -> bool:
    src = pending_dir() / it["file"]
    if not src.exists():
        db.delete(NS, it["id"])
        return False
    dest_dir = export_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        if shutil.disk_usage(dest_dir).free < 512 * 1024 * 1024:
            return False  # 导出盘快满了：先留着，不硬塞
    except OSError:
        return False
    tag = time.strftime("%Y%m%d_%H%M%S", time.localtime(it.get("ts") or time.time()))
    final = dest_dir / f"furina_{tag}_{it['id'][:8]}{Path(it['file']).suffix}"
    # 先写到导入文件夹外面的同盘临时目录，再一次性改名进去：Eagle 不会读到半截文件
    stage = dest_dir.parent / "_furina_stage"
    stage.mkdir(parents=True, exist_ok=True)
    tmp = stage / final.name
    shutil.copyfile(src, tmp)
    _move(tmp, final)
    try:
        _move(src, out_dir() / it["file"])
    except OSError:
        shutil.copyfile(src, out_dir() / it["file"])  # 原文件被占用：留个副本供撤销，原件稍后清理
        it["stale"] = it["file"]
    it.update(status="out", st_ts=time.time(), out_path=str(final))
    db.put(NS, it["id"], it)
    _mark_seen(it.get("dh", ""), it.get("md5", ""), "keep")
    return True


def export_ready(only: str | None = None) -> int:
    """把「已留下且不用再等强化」的图复制进 Eagle 自动导入文件夹。"""
    n = 0
    with _lock:
        rows = [get(only)] if only else _all()
        for it in rows:
            if not it or it.get("status") != "keep" or it.get("enh") in ("todo", "run"):
                continue
            try:
                n += 1 if _export_one(it) else 0
            except OSError:
                continue
    return n


def purge(now: float | None = None) -> int:
    """撤销窗口过了：丢掉的真删，已导出的删本地副本；预览缓存和过期的见过记录一并清理。"""
    now = now or time.time()
    n = 0
    with _lock:
        for it in _all():
            st = it.get("status")
            if st in ("trash", "out") and now - float(it.get("st_ts") or 0) >= UNDO_SECONDS:
                p = (trash_dir() if st == "trash" else out_dir()) / it["file"]
                try:
                    _unlink(p, 3)
                    if it.get("stale"):
                        _unlink(pending_dir() / it["stale"], 3)
                except OSError:
                    continue
                for q in preview_dir().glob(it["id"] + "_*.jpg"):
                    q.unlink(missing_ok=True)
                db.delete(NS, it["id"])
                n += 1
        cutoff = now - SEEN_KEEP_DAYS * 86400
        for k, v in db.items(NS_SEEN).items():
            if not isinstance(v, dict) or float(v.get("t") or 0) < cutoff:
                db.delete(NS_SEEN, k)
        for k, v in db.items(NS_ENH_AUDIT).items():
            if not isinstance(v, dict) or float(v.get("ts") or 0) < now - ENH_AUDIT_KEEP_DAYS * 86400:
                db.delete(NS_ENH_AUDIT, k)
    return n


# ---------------- 强化 ----------------
def next_to_enhance() -> dict | None:
    rows = [it for it in _all() if it.get("status") in ("pending", "keep") and it.get("enh") == "todo"]
    if not rows:
        return None
    # 已经点了「留下」的优先（它们在等强化完才能进 Eagle），其余按收图顺序
    rows.sort(key=lambda it: (it.get("status") != "keep", it.get("ts", 0)))
    return rows[0]


def reset_running() -> int:
    """机器人上次停在「强化中」的图退回排队。"""
    n = 0
    for it in _all():
        if it.get("enh") == "run":
            set_enh(it["id"], "todo")
            n += 1
    return n


def set_enh(iid: str, state: str) -> None:
    def _u(it):
        if isinstance(it, dict):
            it["enh"] = state
        return it
    with _lock:
        if get(iid):
            db.update(NS, iid, _u)


def enhance_audit(iid: str) -> dict | None:
    """只留尺寸/体积/内容哈希，主队列清理后仍可核验；不存图片、昵称或账号。"""
    row = db.get(NS_ENH_AUDIT, str(iid))
    return row if isinstance(row, dict) else None


def record_enhance_event(it: dict, state: str, *, model: str = "", expected_scale: int = 0,
                         reason: str = "", **facts) -> dict:
    row = {"id": it["id"], "ts": time.time(), "state": state, "model": str(model)[:100],
           "expected_scale": int(expected_scale), "reason": str(reason)[:120],
           "input_w": it.get("w", 0), "input_h": it.get("h", 0),
           "input_size": it.get("size", 0), "input_md5": it.get("md5", ""), **facts}
    db.put(NS_ENH_AUDIT, it["id"], row)
    return row


def apply_enhanced(iid: str, upscaled: Path, *, model: str = "", expected_scale: int = 0) -> bool:
    """把超分结果换进来：有透明通道存 PNG，否则存 JPEG q95（省 E 盘空间）。"""
    from PIL import Image

    with _lock:
        it = get(iid)
        if not it or it.get("status") not in ("pending", "keep"):
            return False
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        old = pending_dir() / it["file"]
        original_bytes = old.read_bytes()
        with Image.open(io.BytesIO(original_bytes)) as original:
            input_w, input_h = original.size
        with Image.open(upscaled) as im:
            w, h = im.size
            if w * h > MAX_PIXELS:
                raise ValueError("强化输出尺寸过大")
            if w <= input_w or h <= input_h:
                raise ValueError(f"强化输出没有实际放大: {input_w}x{input_h} -> {w}x{h}")
            if expected_scale > 1 and (w, h) != (input_w * expected_scale, input_h * expected_scale):
                raise ValueError(f"强化输出倍率不符: 期望 {expected_scale}x, 实际 {w}x{h}")
            im.load()
            alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            buf = io.BytesIO()
            if alpha:
                im.save(buf, "PNG", optimize=True)
                ext = "png"
            else:
                im.convert("RGB").save(buf, "JPEG", quality=95, subsampling=0, optimize=True)
                ext = "jpg"
            w, h = im.size
        encoded = buf.getvalue()
        name = f"{iid}.{ext}"
        tmp = pending_dir() / (name + ".part")
        tmp.write_bytes(encoded)
        final = pending_dir() / name
        _move(tmp, final)
        output_md5 = hashlib.md5(encoded).hexdigest()
        if hashlib.md5(final.read_bytes()).hexdigest() != output_md5:
            raise OSError("强化文件写回校验失败")
        if old.name != name:
            try:
                _unlink(old)
            except OSError:
                pass
        for q in preview_dir().glob(iid + "_*.jpg"):
            try:
                _unlink(q, 3)
            except OSError:
                pass  # 正在发给手机的旧缓存可稍后清理；不能把成功写回误报失败
        before = dict(it)
        it.update(file=name, size=len(encoded), w=w, h=h, enh="done")
        db.put(NS, iid, it)
        record_enhance_event(before, "done", model=model, expected_scale=expected_scale,
                             input_w=input_w, input_h=input_h, input_size=len(original_bytes),
                             input_md5=hashlib.md5(original_bytes).hexdigest(),
                             output_w=w, output_h=h, output_size=len(encoded), output_md5=output_md5)
    return True


# ---------------- 预览 ----------------
def preview(iid: str, max_side: int = 1600) -> Path | None:
    """手机/面板看的缩略图（长边 ≤ max_side 的 JPEG，缓存）；max_side=0 返回原文件。"""
    it = get(iid)
    if not it:
        return None
    src = file_path(it)
    if not src.exists():
        return None
    if max_side <= 0 or max(it.get("w", 0), it.get("h", 0)) <= max_side and src.suffix.lower() in (".jpg", ".jpeg"):
        return src
    # 缓存文件不可变：强化前后的预览用不同文件名，Windows 正在发送旧图时也不用覆盖它。
    stamp = src.stat().st_mtime_ns
    legacy = preview_dir() / f"{iid}_{int(max_side)}.jpg"
    if legacy.exists() and legacy.stat().st_mtime_ns >= stamp:
        return legacy
    out = preview_dir() / f"{iid}_{int(max_side)}_{stamp}.jpg"
    if out.exists():
        return out
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with Image.open(src) as im:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im2 = bg
        else:
            im2 = im.convert("RGB")
        tmp = out.with_name(f"{out.stem}.{threading.get_ident()}.part")  # 预热线程和请求可能同时生成同一张
        im2.save(tmp, "JPEG", quality=85)
        try:
            if not out.exists():
                os.replace(tmp, out)
        except PermissionError:
            if not out.exists():
                raise  # 同版本另一个线程先生成好则复用，不覆盖正在发送的文件
        finally:
            try:
                _unlink(tmp, 3)
            except OSError:
                pass  # 杀毒短暂占用临时文件，不耽误发图
    return out


_warm_q: list[tuple[str, int]] = []
_warm_lock = threading.Lock()
_warm_running = False
WARM_SIZES = (480, 1600)


def warm_previews(ids: list[str], sizes: tuple[int, ...] = WARM_SIZES, wait: bool = False) -> int:
    """手机端一打开就把待筛图的缩略图/大图先生成好，滑的时候不用等电脑现做。后台单线程慢慢做。"""
    global _warm_running
    jobs = []
    for iid in ids:
        it = get(iid)
        if not it:
            continue
        src = file_path(it)
        if not src.exists():
            continue
        stamp = src.stat().st_mtime_ns
        for sz in sizes:
            if max(it.get("w", 0), it.get("h", 0)) <= sz and src.suffix.lower() in (".jpg", ".jpeg"):
                continue
            legacy = preview_dir() / f"{iid}_{int(sz)}.jpg"
            cached = legacy.exists() and legacy.stat().st_mtime_ns >= stamp
            if not cached and not (preview_dir() / f"{iid}_{int(sz)}_{stamp}.jpg").exists():
                jobs.append((iid, int(sz)))
    if not jobs:
        return 0
    with _warm_lock:
        known = set(_warm_q)
        _warm_q.extend(j for j in jobs if j not in known)
        start = not _warm_running
        if start:
            _warm_running = True

    def run():
        global _warm_running
        while True:
            with _warm_lock:
                if not _warm_q:
                    _warm_running = False
                    return
                iid, sz = _warm_q.pop(0)
            try:
                preview(iid, sz)
            except Exception:  # noqa: BLE001  坏图跳过，不影响别的
                pass

    if start:
        if wait:
            run()
        else:
            threading.Thread(target=run, name="pic-warm", daemon=True).start()
    return len(jobs)


# ---------------- 手机端密码 ----------------
def token() -> str:
    t = db.get(NS_META, "token")
    if not t:
        t = secrets.token_urlsafe(12)
        db.put(NS_META, "token", t)
    return str(t)


def set_token(new: str | None = None) -> str:
    t = (new or "").strip() or secrets.token_urlsafe(12)
    db.put(NS_META, "token", t)
    return t


def require_password() -> bool:
    return env.get_bool("PIC_INBOX_REQUIRE_PASSWORD", False)


def check_token(given: str) -> bool:
    return bool(given) and secrets.compare_digest(str(given).encode(), token().encode())


def public_url() -> str:
    return env.get("PIC_INBOX_PUBLIC_URL", "").rstrip("/")


def port() -> int:
    return env.get_int("PIC_INBOX_PORT", 52190)
