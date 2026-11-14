"""Render every caption as the caller would see it (640x360), for a legibility check.

    .venv/bin/python tools/render_captions.py [out_dir]     # default runs/caption-samples
Prints each caption's font size, capital height (px) and line count.
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


def scene() -> np.ndarray:
    """A stand-in camera picture: gradient with a head-and-shoulders silhouette."""
    img = np.zeros((720, 1280, 3), np.uint8)
    img[:] = np.linspace(70, 140, 1280, dtype=np.uint8)[None, :, None]
    cv2.ellipse(img, (640, 300), (120, 150), 0, 0, 360, (180, 150, 130), -1)
    cv2.ellipse(img, (640, 720), (330, 230), 0, 180, 360, (60, 70, 110), -1)
    return img


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "runs" / "caption-samples"
    out.mkdir(parents=True, exist_ok=True)
    s = SENTENCES[0]
    cases = [
        ("greet", script.GREETING, "bottom", None, 0.6),
        ("face_neutral", script.FACE_NEUTRAL, "top", (1, "Smile"), 0.5),
        ("face_smile", script.FACE_SMILE, "top", (1, "Smile"), 0.3),
        ("arms_wait", script.ARMS_RAISE, "bottom", (2, "Arms"), None),
        ("arms_hold", script.ARMS_HOLD, "bottom", (2, "Arms"), 0.7),
        ("sentence_lead", script.sentence_prompt(s), "bottom", (3, "Sentence"), 0.5),
        ("sentence_go", script.sentence_go(s), "bottom", (3, "Sentence"), 0.8),
        ("close_escalate", script.CLOSE_ESCALATE, "bottom", None, None),
        ("close_unclear", script.CLOSE_UNCLEAR, "bottom", None, None),
        ("close_no_change", script.CLOSE_NO_CHANGE, "bottom", None, None),
        ("close_baseline", script.CLOSE_BASELINE_SAVED, "bottom", None, None),
        ("arms_restart", script.ARMS_RESTART, "bottom", (2, "Arms"), None),
    ]
    # Framing warnings (the camera cannot see the step), shown in the step's own layout.
    for problem, line in script.ARMS_FRAMING.items():
        cases.append((f"framing_arms_{problem}", line, "bottom", (2, "Arms"), None))
    for problem, line in script.FACE_FRAMING.items():
        cases.append((f"framing_face_{problem}", line, "bottom", (1, "Smile"), 0.5))
    for name, line, pos, prog, frac in cases:
        img = overlay.letterbox(scene(), W, H)
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
