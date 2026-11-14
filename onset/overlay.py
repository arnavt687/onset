"""Caption-first overlay for the video Onset sends back to the caller.

The caller may not hear Onset (see CHERT_SUPPORT_REPORT.md), so every instruction is drawn
large on the video: a caption panel, a countdown bar and a step progress pill. Text is sized
for a phone showing a 640x360 picture: caption cap height is at least MIN_CAP_PX pixels.
"""

from __future__ import annotations

import functools
import textwrap
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

MIN_CAP_PX = 28  # minimum capital-letter height of caption text, in output pixels
FONT_FILES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
MAX_LINES = 3


@functools.lru_cache(maxsize=16)
def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for f in FONT_FILES:
        if Path(f).is_file():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def cap_height(size: int) -> int:
    """Pixel height of a capital letter at this font size."""
    box = font(size).getbbox("H")
    return box[3] - box[1]


def font_size_for_cap(px: int) -> int:
    size = px
    while cap_height(size) < px and size < 200:
        size += 1
    return size


def _wrap(text: str, size: int, width_px: int) -> list[str]:
    f = font(size)
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if f.getlength(trial) <= width_px or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


@functools.lru_cache(maxsize=64)
def caption_layer(text: str, w: int, h: int, position: str = "bottom") -> tuple[np.ndarray, dict]:
    """A transparent RGBA layer with the caption panel, plus layout info (for tests).

    The font starts at the size that gives MIN_CAP_PX capitals scaled to the frame height
    (360 px reference) and grows if there is room; it never shrinks below the minimum, it
    wraps instead (up to MAX_LINES lines; beyond that the last line is elided).
    """
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if not text:
        return np.asarray(layer).copy(), {"lines": [], "size": 0, "cap": 0, "box": None}
    min_cap = max(MIN_CAP_PX, round(MIN_CAP_PX * h / 360))
    size = font_size_for_cap(min_cap)
    pad = max(8, w // 40)
    lines = _wrap(text, size, w - 2 * pad)
    # Grow the font a little for short captions that fit on one or two lines.
    for bigger in (int(size * 1.25), int(size * 1.12)):
        trial = _wrap(text, bigger, w - 2 * pad)
        if len(trial) <= 2 and len(trial) <= max(1, len(lines)):
            size, lines = bigger, trial
            break
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES]
        lines[-1] = textwrap.shorten(lines[-1] + " …", width=max(4, len(lines[-1])), placeholder=" …")
    f = font(size)
    asc, desc = f.getmetrics()
    line_h = asc + desc + max(2, size // 10)
    panel_h = line_h * len(lines) + 2 * pad
    y0 = 0 if position == "top" else h - panel_h
    d = ImageDraw.Draw(layer)
    d.rectangle([0, y0, w, y0 + panel_h], fill=(10, 14, 28, 215))
    d.rectangle([0, y0 if position == "bottom" else y0 + panel_h - 4, w,
                 (y0 + 4) if position == "bottom" else y0 + panel_h], fill=(90, 200, 255, 255))
    y = y0 + pad
    for line in lines:
        lw = f.getlength(line)
        d.text(((w - lw) / 2, y), line, font=f, fill=(255, 255, 255, 255))
        y += line_h
    info = {"lines": lines, "size": size, "cap": cap_height(size), "box": (0, y0, w, y0 + panel_h)}
    return np.asarray(layer).copy(), info


@functools.lru_cache(maxsize=32)
def pill_layer(text: str, w: int, h: int, position: str = "top") -> np.ndarray:
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if not text:
        return np.asarray(layer).copy()
    size = font_size_for_cap(max(14, round(16 * h / 360)))
    f = font(size)
    pad = max(6, size // 3)
    tw = f.getlength(text)
    asc, desc = f.getmetrics()
    bw, bh = int(tw + 2 * pad), asc + desc + pad
    x0 = w - bw - pad
    y0 = pad if position == "top" else h - bh - pad
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=bh // 2, fill=(10, 14, 28, 200))
    d.text((x0 + pad, y0 + pad // 2), text, font=f, fill=(255, 255, 255, 255))
    return np.asarray(layer).copy()


def composite(base: np.ndarray, layer: np.ndarray) -> None:
    """Alpha-blend an RGBA layer onto an RGBA base in place."""
    a = layer[..., 3:4].astype(np.uint16)
    if not a.any():
        return
    base[..., :3] = ((base[..., :3].astype(np.uint16) * (255 - a) + layer[..., :3].astype(np.uint16) * a) // 255).astype(np.uint8)
    base[..., 3] = 255


def countdown_bar(img: np.ndarray, fraction_left: float, position: str = "top") -> None:
    """A full-width bar that empties as time runs out."""
    h, w = img.shape[:2]
    bh = max(6, h // 45)
    y0 = 0 if position == "top" else h - bh
    frac = float(np.clip(fraction_left, 0.0, 1.0))
    cv2.rectangle(img, (0, y0), (w, y0 + bh), (40, 40, 50, 255), -1)
    cv2.rectangle(img, (0, y0), (int(w * frac), y0 + bh), (90, 220, 120, 255), -1)


def letterbox(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Fit an RGB/RGBA image into w x h RGBA without distortion, black bars around it."""
    if img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2RGBA)
    ih, iw = img.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    canvas = np.zeros((h, w, 4), dtype=np.uint8)
    canvas[..., 3] = 255
    x, y = (w - nw) // 2, (h - nh) // 2
    canvas[y:y + nh, x:x + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return canvas


def blank(w: int, h: int) -> np.ndarray:
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[..., :3] = (22, 26, 40)
    img[..., 3] = 255
    return img
