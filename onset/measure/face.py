"""Piece 1: smile asymmetry from the caller's video (MediaPipe Face Landmarker).

Owner: the `face` session. See ROADMAP.md, piece 1.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from onset.types import FaceResult, Frame


class FaceMeter:
    """Feed frames during the neutral and smile phases, then read the result."""

    def __init__(self, model_path: str = "models/face_landmarker.task") -> None:
        raise NotImplementedError

    def add_neutral(self, frame: Frame) -> None:
        """A frame while the person looks at the camera with a relaxed face."""
        raise NotImplementedError

    def add_smile(self, frame: Frame) -> None:
        """A frame while the person is asked to smile."""
        raise NotImplementedError

    def result(self) -> FaceResult:
        raise NotImplementedError

    def draw(self, rgba: np.ndarray, live: dict | None = None) -> None:
        """Draw face landmarks and live numbers onto an RGBA frame, in place."""
        raise NotImplementedError


def measure_face(neutral: Sequence[Frame], smile: Sequence[Frame]) -> FaceResult:
    """Convenience: run FaceMeter over two lists of frames."""
    raise NotImplementedError
