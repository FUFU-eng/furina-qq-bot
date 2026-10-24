"""把帖子文字块渲染成 PNG 图片（白底黑字，中文用微软雅黑，表情用 Segoe UI Emoji）。"""

import io
import os

from PIL import Image, ImageDraw, ImageFont

_TEXT_FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
_EMOJI_FONT_PATH = r"C:\Windows\Fonts\seguiemj.ttf"

_CANVAS_WIDTH = 720
_PAD = 24
_LINE_HEIGHT = 56
_FONT_SIZE = 36
_EMOJI_SIZE = 42

_text_font = None
_emoji_font = None


def _load_fonts():
    global _text_font, _emoji_font
    if _text_font is None:
        try:
            _text_font = ImageFont.truetype(_TEXT_FONT_PATH, _FONT_SIZE, index=0)
        except Exception:
            _text_font = ImageFont.load_default()
    if _emoji_font is None and os.path.exists(_EMOJI_FONT_PATH):
        try:
            _emoji_font = ImageFont.truetype(_EMOJI_FONT_PATH, _EMOJI_SIZE)
        except Exception:
            _emoji_font = None


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (
        (0x1F000 <= cp <= 0x1FAFF)
        or (0x2600 <= cp <= 0x27BF)
        or (0x2B00 <= cp <= 0x2BFF)
        or (0x2190 <= cp <= 0x21FF)
        or (0x2300 <= cp <= 0x23FF)
        or cp == 0xFE0F
        or cp == 0x200D
    )


def _char_font(ch: str):
    _load_fonts()
    if _emoji_font is not None and _is_emoji(ch):
        return _emoji_font
    return _text_font


def _line_width(line: str) -> float:
    return sum(_char_font(c).getlength(c) for c in line)


def _wrap_lines(text: str, max_width: int) -> list:
    lines = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            if cur and _line_width(cur + ch) > max_width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def render_text_to_png(text: str, width: int = _CANVAS_WIDTH) -> bytes:
    """把文字渲染成 PNG 字节（可指定画布宽度）。"""
    if not text.strip():
        text = ""
    _load_fonts()
    max_width = width - _PAD * 2
    lines = _wrap_lines(text, max_width)
    if not lines:
        lines = [""]
    height = _PAD * 2 + _LINE_HEIGHT * len(lines)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    y = _PAD
    for line in lines:
        x = _PAD
        for ch in line:
            font = _char_font(ch)
            draw.text((x, y), ch, font=font, fill="black")
            x += font.getlength(ch)
        y += _LINE_HEIGHT
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def merge_image_and_text(image_bytes: bytes, text: str, gap: int = 16) -> bytes:
    """把帖子里的题头/配图与它下面紧跟的文字拼接成一张 PNG。

    图片在上、文字在下，宽度取图片宽度（不小于默认画布宽度）。
    """
    _load_fonts()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width = max(img.width, _CANVAS_WIDTH)
    text_png = render_text_to_png(text, width=width)
    txt = Image.open(io.BytesIO(text_png)).convert("RGB")
    canvas = Image.new("RGB", (width, img.height + gap + txt.height), "white")
    canvas.paste(img, ((width - img.width) // 2, 0))
    canvas.paste(txt, (0, img.height + gap))
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()
