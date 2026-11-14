"""Caption-first overlay for the video Onset sends back to the caller.

The caller may not hear Onset (see CHERT_SUPPORT_REPORT.md), so every instruction is drawn
large on the video: a caption panel, a countdown bar and a step progress pill. Text is sized
for a phone showing a 640x360 picture: caption cap height is at least MIN_CAP_PX pixels.

Look: Inter Bold on a deep ink panel with a thin teal rule, the same visual language as the
pitch deck and dashboard. Lines that tell the caller to call 911 now use the one alert colour.
A new caption rises and fades in over a fraction of a second (draw_caption); nothing flashes.
"""

from __future__ import annotations

import functools
import os
import re
import textwrap
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

MIN_CAP_PX = 28  # minimum capital-letter height of caption text, in output pixels
_ASSETS = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_FILES = [
    str(_ASSETS / "Inter-Bold.ttf"),  # bundled (SIL OFL), so every machine draws the same captions
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
PILL_FONT_FILES = [str(_ASSETS / "Inter-SemiBold.ttf"), *FONT_FILES]
MAX_LINES = 3
PORTRAIT_MAX_LINES = 5  # an upright frame is narrower, and has the height to spare
PORTRAIT_MIN_WIDTH = 540  # narrower than this and caption text is too coarse after upscaling
SAFE_ASPECT = 3 / 4  # the squarest upright picture a caller's device is assumed to crop to

# Palette (RGB), shared with web/pitch and web/dashboard.html
INK = (8, 12, 22)          # caption panel
TEXT = (248, 250, 252)
GLOW = (103, 224, 210)     # the measuring teal: rules, progress
ALERT = (255, 91, 69)      # only for "call 911 now"
ALERT_INK = (58, 10, 7)
TRACK = (34, 40, 54)
_ALERT_RE = re.compile(r"\b911 now\b", re.I)


@functools.lru_cache(maxsize=16)
def _load(files: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for f in files:
        if Path(f).is_file():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _load(tuple(FONT_FILES), size)


def pill_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _load(tuple(PILL_FONT_FILES), size)


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


def _balanced(text: str, size: int, width_px: int, n: int) -> list[str]:
    """The same number of lines, as even as possible (no lone last word)."""
    lo, hi = width_px // 3, width_px
    while hi - lo > 4:
        mid = (lo + hi) // 2
        if len(_wrap(text, size, mid)) <= n:
            hi = mid
        else:
            lo = mid
    return _wrap(text, size, hi)


def scale(w: int, h: int) -> float:
    """Text scale for a frame: 1.0 at the 640x360 reference, limited by the shorter dimension."""
    return min(h / 360, w / 640)


def safe_box(w: int, h: int) -> tuple[int, int]:
    """(top, bottom) rows that every caption, pill and bar stays inside.

    A Chert line set to video_framing "fill" scales Onset's picture to cover the caller's upright
    FaceTime window and discards the overflow. An upright frame loses nothing sideways; if the
    window is squarer than the frame (3:4 against 9:16), the top and bottom strips go. So text
    keeps to the centred 3:4 region. A wide frame has no safe region under "fill": use portrait.
    """
    if h <= w:
        return 0, h
    safe_h = min(h, round(w / SAFE_ASPECT))
    top = (h - safe_h) // 2
    return top, top + safe_h


def max_lines(w: int, h: int) -> int:
    return PORTRAIT_MAX_LINES if h > w else MAX_LINES


def output_size(env=None) -> tuple[int, int]:
    """(width, height) of the video Onset sends, from OUT_WIDTH, OUT_HEIGHT and ONSET_FRAME.

    ONSET_FRAME=portrait (the default) turns the size upright, whichever way round it was given,
    and widens it to at least PORTRAIT_MIN_WIDTH: 640x360 becomes 540x960. Upright survives a
    line whose video_framing is "fill" (see safe_box). ONSET_FRAME=landscape keeps it as given.
    """
    env = os.environ if env is None else env

    def num(name: str, default: int) -> int:
        try:
            return max(16, int(str(env.get(name) or default).strip()))
        except ValueError:
            return default

    w, h = num("OUT_WIDTH", 540), num("OUT_HEIGHT", 960)
    if str(env.get("ONSET_FRAME") or "portrait").strip().lower() == "landscape":
        return w, h
    short, long = sorted((w, h))
    if short < PORTRAIT_MIN_WIDTH:
        long = round(long * PORTRAIT_MIN_WIDTH / short)
        short = PORTRAIT_MIN_WIDTH
    return short - short % 2, long - long % 2


def is_alert(text: str) -> bool:
    """Captions that tell the caller to call 911 now get the alert treatment."""
    return bool(_ALERT_RE.search(text or ""))


@functools.lru_cache(maxsize=64)
def caption_layer(text: str, w: int, h: int, position: str = "bottom") -> tuple[np.ndarray, dict]:
    """A transparent RGBA layer with the caption panel, plus layout info (for tests).

    The font starts at the size that gives MIN_CAP_PX capitals scaled to the frame height
    (640x360 reference) and grows if there is room; it never shrinks below the minimum, it
    wraps instead (up to max_lines; beyond that the last line is elided). In an upright frame
    the words stay inside safe_box and the panel's ink runs on to the frame edge.
    """
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if not text:
        return np.asarray(layer).copy(), {"lines": [], "size": 0, "cap": 0, "box": None, "alert": False}
    k = scale(w, h)
    limit = max_lines(w, h)
    safe_top, safe_bottom = safe_box(w, h)
    min_cap = max(MIN_CAP_PX, round(MIN_CAP_PX * k))
    size = font_size_for_cap(min_cap)
    pad_x = max(10, w // 32)
    lines = _wrap(text, size, w - 2 * pad_x)
    # Grow the font a little for short captions that fit on one or two lines.
    for bigger in (int(size * 1.22), int(size * 1.1)):
        trial = _wrap(text, bigger, w - 2 * pad_x)
        if len(trial) <= 2 and len(trial) <= max(1, len(lines)):
            size, lines = bigger, trial
            break
    if 1 < len(lines) <= limit:
        lines = _balanced(text, size, w - 2 * pad_x, len(lines))
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = textwrap.shorten(lines[-1] + " …", width=max(4, len(lines[-1])), placeholder=" …")
    f = font(size)
    alert = is_alert(text)
    asc, desc = f.getmetrics()
    line_h = round((asc + desc) * 1.02)
    pad_y = max(8, round(12 * k))
    rule = max(2, round(3 * k))
    panel_h = line_h * len(lines) + 2 * pad_y + rule
    y0 = safe_top if position == "top" else safe_bottom - panel_h
    ink = ALERT_INK if alert else INK
    accent = ALERT if alert else GLOW

    # panel: solid where the words are, feathered into the picture on its inner edge
    feather = max(6, round(14 * k))
    panel = np.zeros((h, w, 4), np.uint8)
    p0, p1 = (0, y0 + panel_h) if position == "top" else (y0, h)  # the ink runs to the frame edge
    panel[p0:p1, :, :3] = ink
    panel[p0:p1, :, 3] = 226 if alert else 214
    if position == "bottom":
        ys = np.arange(max(0, y0 - feather), y0)
        panel[ys, :, :3] = ink
        panel[ys, :, 3] = (np.linspace(0, 150, len(ys))).astype(np.uint8)[:, None]
        panel[y0:y0 + rule, :, :3] = accent
        panel[y0:y0 + rule, :, 3] = 255
    else:
        ys = np.arange(y0 + panel_h, min(h, y0 + panel_h + feather))
        panel[ys, :, :3] = ink
        panel[ys, :, 3] = (np.linspace(150, 0, len(ys))).astype(np.uint8)[:, None]
        panel[y0 + panel_h - rule:y0 + panel_h, :, :3] = accent
        panel[y0 + panel_h - rule:y0 + panel_h, :, 3] = 255
    layer = Image.fromarray(panel, "RGBA")

    # words, with a soft shadow so they stay crisp after video compression
    words = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dw, ds = ImageDraw.Draw(words), ImageDraw.Draw(shadow)
    y = y0 + pad_y + (rule if position == "bottom" else 0)
    for line in lines:
        lw = f.getlength(line)
        x = (w - lw) / 2
        ds.text((x, y + max(1, round(k))), line, font=f, fill=(0, 0, 0, 170))
        dw.text((x, y), line, font=f, fill=(*TEXT, 255))
        y += line_h
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, k)))
    layer = Image.alpha_composite(Image.alpha_composite(layer, shadow), words)
    info = {"lines": lines, "size": size, "cap": cap_height(size), "box": (0, y0, w, y0 + panel_h), "alert": alert,
            "safe": (safe_top, safe_bottom)}
    return np.asarray(layer).copy(), info


# ---------------------------------------------------------------- caption motion

_seen: dict[tuple[int, int, str], tuple[str, float]] = {}
ENTER_S = 0.35  # how long a new caption takes to settle


def draw_caption(img: np.ndarray, text: str, position: str, t: float) -> None:
    """Composite the caption onto img; a caption that just changed rises and fades in.

    `t` is the caller of render's clock in seconds. Purely visual: the words are on screen
    in full within ENTER_S seconds, and a static caption is drawn exactly like caption_layer.
    """
    h, w = img.shape[:2]
    layer, info = caption_layer(text, w, h, position)
    key = (w, h, position)
    last = _seen.get(key)
    if last is None or last[0] != text or t < last[1]:
        _seen[key] = (text, t)
        last = _seen[key]
    p = (t - last[1]) / ENTER_S
    if p >= 1 or not text:
        composite(img, layer)
        return
    e = 1 - (1 - max(0.0, p)) ** 3  # ease-out
    shift = round((1 - e) * max(6, 12 * scale(w, h))) * (1 if position == "bottom" else -1)
    moved = np.zeros_like(layer)
    if shift > 0:
        moved[shift:] = layer[:-shift]
    elif shift < 0:
        moved[:shift] = layer[-shift:]
    else:
        moved[:] = layer
    moved[..., 3] = (moved[..., 3].astype(np.float32) * (0.25 + 0.75 * e)).astype(np.uint8)
    composite(img, moved)


@functools.lru_cache(maxsize=32)
def pill_layer(text: str, w: int, h: int, position: str = "top") -> np.ndarray:
    """Step progress: "Step 2 of 3 · Arms" with three dots, the done ones in teal."""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if not text:
        return np.asarray(layer).copy()
    k = scale(w, h)
    safe_top, safe_bottom = safe_box(w, h)
    size = max(14, round(17 * k))
    f = pill_font(size)
    m = re.match(r"Step (\d+) of (\d+)", text)
    step, total = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    pad = max(7, round(10 * k))
    dot_r, dot_gap = max(3, round(3.5 * k)), max(9, round(11 * k))
    dots_w = (total * dot_gap + pad) if total else 0
    tw = f.getlength(text)
    asc, desc = f.getmetrics()
    bh = asc + desc + pad
    bw = int(tw + 2 * pad + dots_w)
    margin = max(8, round(10 * k))
    x0 = w - bw - margin
    y0 = safe_top + margin if position == "top" else safe_bottom - bh - margin
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=bh // 2, fill=(*INK, 205))
    d.text((x0 + pad, y0 + pad // 2), text, font=f, fill=(*TEXT, 255))
    cy = y0 + bh / 2
    for i in range(total):
        cx = x0 + pad + tw + pad + i * dot_gap + dot_r
        fill = (*GLOW, 255) if i < step else (255, 255, 255, 80)
        d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=fill)
    return np.asarray(layer).copy()


def composite(base: np.ndarray, layer: np.ndarray) -> None:
    """Alpha-blend an RGBA layer onto an RGBA base in place.

    Only the rows and columns the layer covers are blended: a caption or a pill is a fraction
    of the frame, and blending the whole frame on every tick was most of the render time.
    """
    alpha = layer[..., 3]
    rows = np.flatnonzero(alpha.any(axis=1))
    if rows.size == 0:
        return
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    cols = np.flatnonzero(alpha[y0:y1].any(axis=0))
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    a = alpha[y0:y1, x0:x1, None].astype(np.uint16)
    dst = base[y0:y1, x0:x1, :3]
    dst[:] = ((dst.astype(np.uint16) * (255 - a) + layer[y0:y1, x0:x1, :3].astype(np.uint16) * a) // 255).astype(np.uint8)
    base[..., 3] = 255


def countdown_bar(img: np.ndarray, fraction_left: float, position: str = "top") -> None:
    """A full-width bar that empties as time runs out, with a bright leading edge."""
    h, w = img.shape[:2]
    bh = max(6, round(8 * scale(w, h)))
    safe_top, safe_bottom = safe_box(w, h)
    y0 = safe_top if position == "top" else safe_bottom - bh
    frac = float(np.clip(fraction_left, 0.0, 1.0))
    x1 = int(w * frac)
    cv2.rectangle(img, (0, y0), (w, y0 + bh - 1), (*TRACK, 255), -1)
    if x1 > 0:
        cv2.rectangle(img, (0, y0), (x1, y0 + bh - 1), (*GLOW, 255), -1)
        edge = max(2, bh // 3)
        cv2.rectangle(img, (max(0, x1 - edge), y0), (x1, y0 + bh - 1), (215, 255, 249, 255), -1)


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


@functools.lru_cache(maxsize=4)
def _blank(w: int, h: int) -> np.ndarray:
    """Deep ink with a soft teal glow: the picture before the caller's video arrives."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w * 0.5) / (w * 0.75)) ** 2 + ((yy - h * 0.42) / (h * 0.9)) ** 2)
    glow = np.clip(1 - r, 0, 1) ** 2
    img = np.zeros((h, w, 4), dtype=np.uint8)
    for c, (base, tint) in enumerate(zip((10, 15, 26), (24, 52, 58))):
        img[..., c] = (base + (tint - base) * glow).astype(np.uint8)
    img[..., 3] = 255
    return img


def blank(w: int, h: int) -> np.ndarray:
    return _blank(w, h).copy()
