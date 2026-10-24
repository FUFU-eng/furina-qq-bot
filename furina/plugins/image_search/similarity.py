"""图片相似度：dHash、裁剪宽容对比、图片魔数校验。"""

from io import BytesIO

from PIL import Image


def _image_dhash_bytes(data: bytes) -> int:
    """计算图片的 dHash（差异哈希，256位，16×16），用于相似度对比"""
    return _image_dhash_from_pil(Image.open(BytesIO(data)))


def _image_dhash_from_pil(img) -> int:
    """对 PIL Image 计算 dHash（256位，16×16 马赛克）。
    比原 64 位（8×8）区分度高很多：无关小图会掉到 30~35 分，真图仍接近 64 分"""
    img = img.convert("RGBA").convert("L").resize((17, 16), Image.LANCZOS)
    px = list(img.getdata())
    h = 0
    for row in range(16):
        for col in range(16):
            h = (h << 1) | (1 if px[row * 17 + col] > px[row * 17 + col + 1] else 0)
    return h


def _image_similarity(a: bytes, b: bytes) -> int:
    """两张图片的相似度分数（0-64，越大越相似）"""
    try:
        ha = _image_dhash_bytes(a)
        hb = _image_dhash_bytes(b)
        # 256 位哈希：汉明距离 0~256，归一化回 0~64 分
        return (256 - bin(ha ^ hb).count("1")) // 4
    except Exception:
        return 0


def _image_crop_variants(img):
    """把查询图切成几个常见的裁剪变体：原图、上半、中间、下半、中心正方形。
    用于和"被网站裁剪过的缩略图"做对比，取其中最高的相似度"""
    variants = [img]
    try:
        w, h = img.size
        variants.append(img.crop((0, 0, w, h // 2)))            # 上半
        variants.append(img.crop((0, h // 4, w, h * 3 // 4)))   # 中间
        variants.append(img.crop((0, h // 2, w, h)))            # 下半
        variants.append(img.crop((0, 0, w, h // 3)))            # 上三分之一
        variants.append(img.crop((0, h // 3, w, h * 2 // 3)))   # 中三分之一
        variants.append(img.crop((0, h * 2 // 3, w, h)))        # 下三分之一
        s = min(w, h)
        left = (w - s) // 2
        top = (h - s) // 2
        variants.append(img.crop((left, top, left + s, top + s)))  # 中心正方形
    except Exception:
        pass
    return variants


def _image_similarity_crop_tolerant(a: bytes, b: bytes) -> int:
    """对裁剪过的缩略图更宽容的相似度（0-64）：
    查询图用 原图/上半/中间/下半/中心正方形 变体分别与缩略图算 dHash，取最高分"""
    try:
        query = Image.open(BytesIO(a))
        thumb = Image.open(BytesIO(b))
        thumb_hash = _image_dhash_from_pil(thumb)
        best = -1
        for variant in _image_crop_variants(query):
            vh = _image_dhash_from_pil(variant)
            score = (256 - bin(vh ^ thumb_hash).count("1")) // 4
            if score > best:
                best = score
        return max(best, 0)
    except Exception:
        return 0


def _looks_like_image(data: bytes) -> bool:
    """判断字节流是否是常见图片格式（防止把 Cloudflare 验证页/错误页的 HTML 当成图片）"""
    if not data:
        return False
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):                 # JPEG
        return True
    if head.startswith(b"\x89PNG\r\n\x1a\n"):            # PNG
        return True
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):  # GIF
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":    # WebP
        return True
    if head.startswith(b"BM"):                           # BMP
        return True
    return False


def _image_pixel_area(data: bytes) -> int:
    """图片像素面积，解析失败返回 0"""
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as img:
            return (img.width or 0) * (img.height or 0)
    except Exception:
        return 0


def _is_placeholder_small(data: bytes, min_area: int = 40000) -> bool:
    """判断图片是否小到疑似 CDN 占位图（默认按 200x200=40000 像素过滤）"""
    area = _image_pixel_area(data)
    return 0 < area < min_area

def _crop_ui_query_bytes(data: bytes) -> bytes:
    """竖屏截图类查询图（手机短视频截图等）裁剪掉顶部状态栏/底部播放器控件，
    只保留画面主体，用于内部相似度与视觉打分，避免 UI 元素拉低分数。
    非竖屏或处理失败时原样返回。"""
    if not data:
        return data
    try:
        img = Image.open(BytesIO(data))
        w, h = img.size
        if h < w * 1.4:
            return data  # 横图/接近方图：不裁剪，避免误伤正常图
        # 越高的竖屏截图（手机短视频）UI 占比越大，裁剪多一点
        if h >= w * 2.0:
            top_ratio, bottom_ratio = 0.06, 0.22
        else:
            top_ratio, bottom_ratio = 0.05, 0.14
        top = int(h * top_ratio)
        bottom = int(h * bottom_ratio)
        crop = img.crop((0, top, w, h - bottom))
        if crop.width < 64 or crop.height < 64:
            return data
        buf = BytesIO()
        fmt = (img.format or "").upper()
        if fmt == "PNG":
            crop.save(buf, "PNG")
        else:
            crop.convert("RGB").save(buf, "JPEG", quality=92)
        return buf.getvalue()
    except Exception:
        return data
