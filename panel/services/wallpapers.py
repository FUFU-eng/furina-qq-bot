"""壁纸库：存在 SQLite（panel/wallpapers），可含图片与视频；面板上传的新壁纸放 data/wallpapers。

旧项目 backgrounds/custom 里的壁纸按原路径引用，不复制、不改动。
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
from pathlib import Path

from furina.core import db, paths

NS, KEY = "panel", "wallpapers"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")
VIDEO_EXT = (".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi")
UPLOAD_DIR = paths.DATA_DIR / "wallpapers"
THUMB_DIR = UPLOAD_DIR / ".thumbs"
MAX_UPLOAD = 512 * 1024 * 1024
_lock = threading.Lock()


def wid(path: str) -> str:
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:16]


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return "video" if ext in VIDEO_EXT else "image"


def _raw() -> dict:
    d = db.get(NS, KEY)
    return d if isinstance(d, dict) else {}


def load() -> dict:
    d = _raw()
    wps = [p for p in (d.get("wallpapers") or []) if isinstance(p, str) and os.path.exists(p)]
    pl = [p for p in (d.get("playlist") or []) if p in wps]
    try:
        interval = max(3, int(d.get("interval_seconds", 30) or 30))
    except (TypeError, ValueError):
        interval = 30
    return {"interval_seconds": interval, "wallpapers": wps, "playlist": pl or list(wps)}


def save(data: dict) -> None:
    db.put(NS, KEY, {"interval_seconds": data["interval_seconds"], "wallpapers": data["wallpapers"],
                     "playlist": data["playlist"]})


def public() -> dict:
    d = load()
    pl = set(d["playlist"])
    items = [{"id": wid(p), "name": os.path.basename(p), "kind": kind_of(p), "playing": p in pl,
              "owned": _owned(p)} for p in d["wallpapers"]]
    return {"interval_seconds": d["interval_seconds"], "items": items,
            "playlist": [wid(p) for p in d["playlist"]]}


def path_of(ident: str) -> str | None:
    for p in load()["wallpapers"]:
        if wid(p) == ident:
            return p
    return None


def _owned(p: str) -> bool:
    try:
        return Path(p).resolve().parent == UPLOAD_DIR.resolve()
    except OSError:
        return False


def add_upload(filename: str, body_iter) -> dict:
    """保存上传内容（流式写入，限制 512MB），加入壁纸库与轮播列表。"""
    name = os.path.basename(filename or "")
    ext = os.path.splitext(name)[1].lower()
    if ext not in IMAGE_EXT + VIDEO_EXT:
        raise ValueError("只支持图片（jpg/png/webp/gif/bmp）或视频（mp4/webm/mkv/mov）")
    stem = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", os.path.splitext(name)[0])[:40] or "wallpaper"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / f"{int(time.time() * 1000)}_{stem}{ext}"
    tmp = target.with_suffix(ext + ".part")
    size = 0
    try:
        with open(tmp, "wb") as f:
            for chunk in body_iter:
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise ValueError("文件太大（上限 512MB）")
                f.write(chunk)
        if size == 0:
            raise ValueError("文件为空")
        if ext in IMAGE_EXT:
            from PIL import Image

            with Image.open(tmp) as im:
                im.verify()
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    p = str(target)
    with _lock:
        d = load()
        d["wallpapers"].append(p)
        if set(d["playlist"]) == set(d["wallpapers"][:-1]):
            d["playlist"].append(p)  # 原来就是轮播全部 → 新壁纸自动加入
        save(d)
    return {"id": wid(p), "name": target.name, "kind": kind_of(p)}


def remove(ids: list[str]) -> int:
    with _lock:
        d = load()
        gone = [p for p in d["wallpapers"] if wid(p) in set(ids)]
        d["wallpapers"] = [p for p in d["wallpapers"] if p not in gone]
        d["playlist"] = [p for p in d["playlist"] if p not in gone]
        save(d)
    for p in gone:  # 只删除面板自己上传的文件；外部引用的原图不动
        if _owned(p):
            try:
                os.remove(p)
            except OSError:
                pass
        t = THUMB_DIR / f"{wid(p)}.jpg"
        if t.exists():
            t.unlink()
    return len(gone)


def set_playlist(ids: list[str]) -> list[str]:
    with _lock:
        d = load()
        by = {wid(p): p for p in d["wallpapers"]}
        pl = [by[i] for i in ids if i in by]
        d["playlist"] = pl
        save(d)
        return [wid(p) for p in pl]


def set_interval(seconds: int) -> int:
    with _lock:
        d = load()
        d["interval_seconds"] = max(3, min(3600, int(seconds)))
        save(d)
        return d["interval_seconds"]


def clear() -> None:
    with _lock:
        d = load()
        d["playlist"] = []
        d["wallpapers"] = []
        save(d)


def thumbnail(ident: str) -> bytes | None:
    """图片壁纸 → 320px JPEG 缩略图（按文件修改时间缓存）；视频返回 None，由网页取首帧。"""
    p = path_of(ident)
    if not p or kind_of(p) == "video":
        return None
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    t = THUMB_DIR / f"{ident}.jpg"
    try:
        if t.exists() and t.stat().st_mtime >= os.path.getmtime(p):
            return t.read_bytes()
        from PIL import Image, ImageOps

        with Image.open(p) as im:
            im.seek(0)
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((360, 220))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82)
        t.write_bytes(buf.getvalue())
        return buf.getvalue()
    except Exception:
        return None
