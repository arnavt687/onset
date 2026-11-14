"""Piece 2: arm drift from the caller's video (MediaPipe Pose Landmarker).

Owner: the `arms` session. See ROADMAP.md, piece 2.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from onset.types import ArmResult, Frame


class ArmMeter:
    """Feed frames for the whole "hold both arms up" step, then read the result."""

    HOLD_SECONDS = 10.0

    def __init__(self, model_path: str = "models/pose_landmarker.task") -> None:
        raise NotImplementedError

    def add(self, frame: Frame) -> None:
        raise NotImplementedError

    def arms_up(self) -> bool:
        """True once both wrists are above the shoulders; the flow starts the 10 s timer then."""
        raise NotImplementedError

    def result(self) -> ArmResult:
        raise NotImplementedError

    def draw(self, rgba: np.ndarray, live: dict | None = None) -> None:
        """Draw shoulder-to-wrist lines and live drift numbers onto an RGBA frame, in place."""
        raise NotImplementedError


def measure_arms(frames: Sequence[Frame]) -> ArmResult:
    raise NotImplementedError
