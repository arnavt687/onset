"""Shared test fixtures and markers for every Onset piece.

Fixtures
    make_frames(n=30, fps=15.0, width=1280, height=720, seed=0) -> list[Frame]
        synthetic caller video: a moving circle and bar on a grey background; the frame
        number is drawn as a brightness code in the top-left 8 blocks (see frame_number()).
    synthetic_frames      30 frames from make_frames(), 1280x720 at 15 fps
    make_tone(seconds=1.0, freq=440.0, sample_rate=16000, amplitude=0.3, t_start=0.0) -> AudioClip
    make_silence(seconds=1.0, sample_rate=16000, t_start=0.0) -> AudioClip
    tone_clip, silence_clip   one second of each at 16 kHz
    models_dir            the MediaPipe models directory (skips the test if the models are missing)
    face_model_path, pose_model_path

Markers
    @pytest.mark.models   needs the MediaPipe models (ONSET_MODELS, ./models or ~/onset/models);
                          skipped when they are not there.
    @pytest.mark.network  talks to an outside service; skipped unless ONSET_NETWORK_TESTS=1.
                          CI never sets it, and has no keys.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from onset.types import AudioClip, Frame

REPO_ROOT = Path(__file__).resolve().parent.parent
FACE_MODEL = "face_landmarker.task"
POSE_MODEL = "pose_landmarker_full.task"
CODE_BLOCKS = 8  # bits of the frame number drawn into the top-left corner
CODE_SIZE = 32  # pixels per code block


def find_models_dir() -> Path | None:
    for p in (os.environ.get("ONSET_MODELS"), REPO_ROOT / "models", Path.home() / "onset" / "models"):
        if p and (Path(p) / FACE_MODEL).is_file() and (Path(p) / POSE_MODEL).is_file():
            return Path(p)
    return None


def network_enabled() -> bool:
    return os.environ.get("ONSET_NETWORK_TESTS", "").strip() == "1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "models: needs the MediaPipe models (skipped when missing)")
    config.addinivalue_line("markers", "network: needs the network and keys (runs only with ONSET_NETWORK_TESTS=1)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    no_models = pytest.mark.skip(reason="MediaPipe models not found (set ONSET_MODELS or run tools/setup-shared.sh)")
    no_network = pytest.mark.skip(reason="network test: set ONSET_NETWORK_TESTS=1 to run")
    have_models = find_models_dir() is not None
    for item in items:
        if "models" in item.keywords and not have_models:
            item.add_marker(no_models)
        if "network" in item.keywords and not network_enabled():
            item.add_marker(no_network)


# --------------------------------------------------------------------------- video


def draw_frame(i: int, width: int = 1280, height: int = 720, fps: float = 15.0, seed: int = 0) -> np.ndarray:
    """Frame i of the synthetic clip, H x W x 3 uint8 RGB."""
    import cv2

    rng = np.random.default_rng(seed)
    base = rng.integers(70, 110, size=3)
    img = np.empty((height, width, 3), dtype=np.uint8)
    img[:] = base
    t = i / fps
    # A circle moving on an ellipse and a vertical bar sweeping left to right.
    cx = int(width / 2 + width * 0.3 * math.cos(t * 1.3))
    cy = int(height / 2 + height * 0.3 * math.sin(t * 1.3))
    cv2.circle(img, (cx, cy), max(8, height // 10), (230, 80, 60), -1)
    bx = int((t * width / 4) % width)
    cv2.rectangle(img, (bx, height // 4), (bx + width // 32, 3 * height // 4), (60, 200, 90), -1)
    # The frame number as black/white blocks, robust to lossy video compression.
    for b in range(CODE_BLOCKS):
        v = 255 if (i >> b) & 1 else 0
        img[0:CODE_SIZE, b * CODE_SIZE:(b + 1) * CODE_SIZE] = v
    return img


def frame_number(rgb: np.ndarray) -> int:
    """Read back the frame number draw_frame() put in the corner."""
    n = 0
    for b in range(CODE_BLOCKS):
        block = rgb[4:CODE_SIZE - 4, b * CODE_SIZE + 4:(b + 1) * CODE_SIZE - 4]
        if block.mean() > 127:
            n |= 1 << b
    return n


@pytest.fixture
def make_frames():
    def make(n: int = 30, fps: float = 15.0, width: int = 1280, height: int = 720, seed: int = 0) -> list[Frame]:
        return [Frame(rgb=draw_frame(i, width, height, fps, seed), t=i / fps) for i in range(n)]

    return make


@pytest.fixture
def synthetic_frames(make_frames) -> list[Frame]:
    return make_frames()


# --------------------------------------------------------------------------- audio


def tone_pcm(seconds: float, freq: float, sample_rate: int, amplitude: float) -> bytes:
    n = int(round(seconds * sample_rate))
    t = np.arange(n) / sample_rate
    return (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype("<i2").tobytes()


@pytest.fixture
def make_tone():
    def make(seconds: float = 1.0, freq: float = 440.0, sample_rate: int = 16000,
             amplitude: float = 0.3, t_start: float = 0.0) -> AudioClip:
        return AudioClip(pcm=tone_pcm(seconds, freq, sample_rate, amplitude), sample_rate=sample_rate, t_start=t_start)

    return make


@pytest.fixture
def make_silence():
    def make(seconds: float = 1.0, sample_rate: int = 16000, t_start: float = 0.0) -> AudioClip:
        return AudioClip(pcm=b"\0\0" * int(round(seconds * sample_rate)), sample_rate=sample_rate, t_start=t_start)

    return make


@pytest.fixture
def tone_clip(make_tone) -> AudioClip:
    return make_tone()


@pytest.fixture
def silence_clip(make_silence) -> AudioClip:
    return make_silence()


# --------------------------------------------------------------------------- models


@pytest.fixture
def models_dir() -> Path:
    d = find_models_dir()
    if d is None:
        pytest.skip("MediaPipe models not found (set ONSET_MODELS or run tools/setup-shared.sh)")
    return d


@pytest.fixture
def face_model_path(models_dir) -> str:
    return str(models_dir / FACE_MODEL)


@pytest.fixture
def pose_model_path(models_dir) -> str:
    return str(models_dir / POSE_MODEL)
