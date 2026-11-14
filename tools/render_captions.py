"""Render every caption as the caller would see it (640x360), for a legibility check and the demo video.

    .venv/bin/python tools/render_captions.py [out_dir]     # default runs/caption-samples
Prints each caption's font size, capital height (px) and line count.

The stand-in camera picture is a drawn figure doing what the caption asks: a relaxed face, a big
smile, both arms raised, speaking, and (for the reposition prompt) standing too close, so the
hands are out of the picture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from onset import overlay, script  # noqa: E402
from onset.speech import SENTENCES  # noqa: E402

W, H = 640, 360
SKIN, SKIN_DK, SHIRT, INK = (196, 160, 136), (150, 112, 92), (62, 74, 120), (52, 36, 32)
LIP, TEETH = (150, 62, 64), (246, 242, 236)
AA = cv2.LINE_AA

# Same sentence as the dashboard run (tools/demo_events.py SENTENCE).
SENTENCE = SENTENCES[1]

# Arms framing problems (onset/script.py ARMS_FRAMING) drawn with the figure too close to the phone.
ARMS_CLOSE = {"both_hands", "left_hand", "right_hand"}


def _background() -> np.ndarray:
    img = np.zeros((720, 1280, 3), np.uint8)
    img[:] = np.linspace(70, 140, 1280, dtype=np.uint8)[None, :, None]
    return img


def _face(img: np.ndarray, c: tuple[int, int], r: tuple[int, int], expr: str) -> None:
    """A head at c with radii r; expr is neutral | smile | speak."""
    cx, cy = c
    rx, ry = r
    s = rx / 120  # drawing scale against the default head
    P = lambda dx, dy: (int(cx + dx * s), int(cy + dy * s))  # noqa: E731
    R = lambda v: max(1, int(v * s))  # noqa: E731
    cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, SKIN, -1, AA)
    # ears
    for sx in (-1, 1):
        cv2.ellipse(img, P(sx * 118, 10), (R(16), R(28)), 0, 0, 360, SKIN, -1, AA)
    # hair
    cv2.ellipse(img, P(0, -60), (R(118), R(92)), 0, 180, 360, INK, -1, AA)
    # brows
    lift = -6 if expr == "smile" else 0
    for sx in (-1, 1):
        cv2.ellipse(img, P(sx * 44, -34 + lift), (R(24), R(9)), 0, 200 if sx < 0 else 200, 340, INK, R(6), AA)
    # eyes: happy arcs when smiling, open otherwise
    for sx in (-1, 1):
        if expr == "smile":
            cv2.ellipse(img, P(sx * 44, -6), (R(17), R(12)), 0, 190, 350, INK, R(6), AA)
        else:
            cv2.ellipse(img, P(sx * 44, -8), (R(15), R(10)), 0, 0, 360, (250, 250, 248), -1, AA)
            cv2.circle(img, P(sx * 44, -8), R(7), INK, -1, AA)
    # nose
    cv2.polylines(img, [np.array([P(2, 2), P(-9, 30), P(6, 32)], np.int32)], False, SKIN_DK, R(4), AA)
    # mouth
    if expr == "smile":
        cv2.ellipse(img, P(0, 52), (R(52), R(40)), 0, 0, 180, LIP, -1, AA)
        cv2.ellipse(img, P(0, 54), (R(44), R(16)), 0, 0, 180, TEETH, -1, AA)
        cv2.line(img, P(-52, 52), P(52, 52), LIP, R(5), AA)
        for sx in (-1, 1):  # cheeks
            cv2.ellipse(img, P(sx * 66, 38), (R(16), R(10)), 0, 0, 360, (214, 150, 138), -1, AA)
    elif expr == "speak":
        cv2.ellipse(img, P(0, 66), (R(22), R(26)), 0, 0, 360, LIP, -1, AA)
        cv2.ellipse(img, P(0, 72), (R(13), R(12)), 0, 0, 360, (96, 34, 40), -1, AA)
    else:
        cv2.line(img, P(-30, 66), P(30, 66), LIP, R(7), AA)


def _body(img: np.ndarray, cx: int, top: int, half: int) -> None:
    cv2.ellipse(img, (cx, top + 230), (half, 230), 0, 180, 360, SHIRT, -1, AA)


def _arms_up(img: np.ndarray, cx: int, sy: int, k: float) -> None:
    """Both arms raised in a wide V from shoulders at height sy; k scales the figure."""
    for sx in (-1, 1):
        sh = (int(cx + sx * 150 * k), int(sy))
        el = (int(cx + sx * 330 * k), int(sy - 95 * k))
        hand = (int(cx + sx * 455 * k), int(sy - 215 * k))
        cv2.line(img, sh, el, SHIRT, int(64 * k), AA)
        cv2.line(img, el, hand, SKIN, int(50 * k), AA)
        cv2.circle(img, el, int(32 * k), SHIRT, -1, AA)
        cv2.circle(img, hand, int(34 * k), SKIN, -1, AA)


def scene(kind: str = "neutral") -> np.ndarray:
    """A stand-in camera picture (1280x720 RGB) of a figure doing this step."""
    img = _background()
    if kind in ("arms", "arms_close"):
        k = 0.78 if kind == "arms" else 1.55
        cx, hy = 640, int(300 if kind == "arms" else 300)
        sy = int(hy + 175 * k)
        _body(img, cx, sy - 20, int(250 * k))
        _arms_up(img, cx, sy, k)
        cv2.rectangle(img, (int(cx - 34 * k), int(hy + 100 * k)), (int(cx + 34 * k), int(sy)), SKIN, -1)  # neck
        _face(img, (cx, hy), (int(120 * k), int(150 * k)), "neutral")
        return img
    _body(img, 640, 490, 330)
    cv2.rectangle(img, (600, 400), (680, 500), SKIN, -1)  # neck
    _face(img, (640, 300), (120, 150), {"smile": "smile", "speak": "speak"}.get(kind, "neutral"))
    if kind == "speak":
        for i, rr in enumerate((60, 95, 130)):
            for sx in (-1, 1):
                a0 = -35 if sx > 0 else 145
                cv2.ellipse(img, (640 + sx * 150, 360), (rr, rr), 0, a0, a0 + 70, (226, 238, 245), 7 - i * 2, AA)
    return img


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "runs" / "caption-samples"
    out.mkdir(parents=True, exist_ok=True)
    s = SENTENCE
    cases = [
        ("greet", script.GREETING, "bottom", None, 0.6, "neutral"),
        ("face_neutral", script.FACE_NEUTRAL, "top", (1, "Smile"), 0.5, "neutral"),
        ("face_smile", script.FACE_SMILE, "top", (1, "Smile"), 0.3, "smile"),
        ("arms_wait", script.ARMS_RAISE, "bottom", (2, "Arms"), None, "arms"),
        ("arms_hold", script.ARMS_HOLD, "bottom", (2, "Arms"), 0.7, "arms"),
        ("arms_restart", script.ARMS_RESTART, "bottom", (2, "Arms"), None, "arms"),
        ("sentence_lead", script.sentence_prompt(s), "bottom", (3, "Sentence"), 0.5, "neutral"),
        ("sentence_go", script.sentence_go(s), "bottom", (3, "Sentence"), 0.8, "speak"),
        ("close_escalate", script.CLOSE_ESCALATE, "bottom", None, None, "neutral"),
        ("close_unclear", script.CLOSE_UNCLEAR, "bottom", None, None, "neutral"),
        ("close_no_change", script.CLOSE_NO_CHANGE, "bottom", None, None, "neutral"),
        ("close_baseline", script.CLOSE_BASELINE_SAVED, "bottom", None, None, "neutral"),
        ("caregiver_joined", script.CAREGIVER_JOINED, "bottom", None, None, "neutral"),
    ]
    # Framing warnings (the camera cannot see the step), shown in the step's own layout. The
    # hands-out-of-picture ones show the figure standing too close.
    for problem, line in script.ARMS_FRAMING.items():
        kind = "arms_close" if problem in ARMS_CLOSE else "arms"
        cases.append((f"framing_arms_{problem}", line, "bottom", (2, "Arms"), None, kind))
    for problem, line in script.FACE_FRAMING.items():
        cases.append((f"framing_face_{problem}", line, "bottom", (1, "Smile"), 0.5, "neutral"))
    for name, line, pos, prog, frac, kind in cases:
        img = overlay.letterbox(scene(kind), W, H)
        layer, info = overlay.caption_layer(line.caption, W, H, pos)
        overlay.composite(img, layer)
        other = "bottom" if pos == "top" else "top"
        if frac is not None:
            overlay.countdown_bar(img, frac, other)
        if prog:
            overlay.composite(img, overlay.pill_layer(f"Step {prog[0]} of 3 · {prog[1]}", W, H, other))
        path = out / f"{name}.png"
        cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGBA2BGR))
        print(f"{name:<28} size {info['size']:>3}px cap {info['cap']:>2}px lines {len(info['lines'])}  {path}")


if __name__ == "__main__":
    main()
