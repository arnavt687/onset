"""Build the Onset logo SVGs (web/brand/). The wordmark text is outlined from Inter Bold
(onset/assets/fonts, OFL), so the SVGs need no font.

    ~/onset/.venv/bin/python web/brand/build_logo.py

Writes logo-mark.svg (square app icon), logo-glyph.svg (the mark alone, transparent),
logo.svg (horizontal wordmark on dark, transparent background) and logo-light.svg (wordmark
for light backgrounds). PNGs are rendered from these with Chrome (see README.md here).

The mark: an open ring (a one-minute check-in, the circle of the 90 days) with a coral point
in the gap, the moment a change begins.
"""

from __future__ import annotations

import math
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
FONT = HERE.parent.parent / "onset" / "assets" / "fonts" / "Inter-Bold.ttf"

TEAL, TEAL2, CORAL = "#67E0D2", "#3FB6E8", "#FF6B57"
INK, INK2, TEXT = "#0A1322", "#13233A", "#EEF4F8"


def ring(cx: float, cy: float, r: float, w: float, uid: str) -> str:
    """The open ring and its dot, drawn around (cx, cy)."""
    a0, a1, ad = -25.0, 265.0, -60.0  # arc from -25 deg clockwise to 265 deg; dot at -60 deg

    def pt(a: float, rad: float = r) -> tuple[float, float]:
        return cx + rad * math.cos(math.radians(a)), cy + rad * math.sin(math.radians(a))

    (x0, y0), (x1, y1), (xd, yd) = pt(a0), pt(a1), pt(ad)
    return (
        f'<defs><linearGradient id="g{uid}" x1="0" y1="1" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{TEAL2}"/><stop offset="1" stop-color="{TEAL}"/></linearGradient></defs>'
        f'<path d="M{x0:.2f} {y0:.2f}A{r} {r} 0 1 1 {x1:.2f} {y1:.2f}" fill="none" '
        f'stroke="url(#g{uid})" stroke-width="{w}" stroke-linecap="round"/>'
        f'<circle cx="{xd:.2f}" cy="{yd:.2f}" r="{w * 0.56:.2f}" fill="{CORAL}"/>'
    )


def text_path(s: str, x: float, baseline: float, size: float, tracking: float = -0.02) -> tuple[str, float]:
    font = TTFont(FONT)
    gs, cmap, upm = font.getGlyphSet(), font.getBestCmap(), font["head"].unitsPerEm
    k = size / upm
    out, pen_x = [], 0.0
    for ch in s:
        g = cmap[ord(ch)]
        sp = SVGPathPen(gs)
        gs[g].draw(TransformPen(sp, (k, 0, 0, -k, x + pen_x * k, baseline)))
        out.append(sp.getCommands())
        pen_x += gs[g].width + tracking * upm
    return " ".join(out), (pen_x - tracking * upm) * k


def svg(w: float, h: float, body: str, title: str = "Onset") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" width="{w:g}" height="{h:g}" '
            f'role="img" aria-label="{title}"><title>{title}</title>{body}</svg>\n')


def main() -> None:
    tile = (f'<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{INK2}"/>'
            f'<stop offset="1" stop-color="{INK}"/></linearGradient></defs>'
            f'<rect width="512" height="512" rx="116" fill="url(#bg)"/>')
    (HERE / "logo-mark.svg").write_text(svg(512, 512, tile + ring(256, 256, 148, 54, "m")))
    (HERE / "logo-glyph.svg").write_text(svg(512, 512, ring(256, 256, 176, 64, "c")))

    # Wordmark: glyph + "Onset", cap height matched to the ring.
    size = 300
    d, tw = text_path("Onset", 0, 0, size)
    H = 360
    gx, gr, gw = 180, 128, 47
    tx = gx + gr + gw / 2 + 70
    base = H / 2 + 0.727 * size / 2  # Inter cap height ~0.727 em
    d, tw = text_path("Onset", tx, base, size)
    W = tx + tw + 30
    for name, fill in (("logo.svg", TEXT), ("logo-light.svg", INK)):
        (HERE / name).write_text(svg(round(W), H, ring(gx, H / 2, gr, gw, "w") + f'<path d="{d}" fill="{fill}"/>'))
    print(f"wordmark {W:.0f}x{H}")


if __name__ == "__main__":
    main()
