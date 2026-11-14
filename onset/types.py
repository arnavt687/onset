"""Shared contracts between Onset's modules.

Every module in the roadmap reads and returns these types, so pieces can be built in
parallel and joined later. Change a type only in its own PR, and say so in the PR title.

Safety rule baked into the types: there is no "normal" or "fine" outcome. A check either
found a change, could not tell, or detected no change *compared with the baseline*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np


@dataclass(frozen=True)
class Frame:
    """One upright caller video frame."""

    rgb: np.ndarray  # H x W x 3, uint8, RGB, already rotated upright
    t: float  # seconds since the step started (monotonic)


@dataclass(frozen=True)
class AudioClip:
    """The caller's speech for one step: 16-bit mono PCM."""

    pcm: bytes
    sample_rate: int
    t_start: float  # seconds since the call started


class Quality(str, Enum):
    """Whether a measurement can be trusted. Anything but OK makes the step unclear."""

    OK = "ok"
    NO_SUBJECT = "no_subject"  # no face / no body found
    PARTIAL = "partial"  # subject found in too few frames, or cut off by the frame edge
    TOO_DARK = "too_dark"
    MOTION = "motion"  # camera or person moving too much
    NO_RESPONSE = "no_response"  # the person did not do the step (no smile, arms not raised, silence)
    ERROR = "error"


@dataclass(frozen=True)
class FaceResult:
    """Smile check: does one side of the mouth lift less than the other?"""

    quality: Quality
    # 1 - weaker_lift / stronger_lift, in [0, 1]: 0 = both mouth corners lifted equally,
    # 1 = one side did not lift at all. Corner lift = neutral-to-smile movement of each mouth
    # corner up and outward, in inter-ocular distances (so camera distance cancels); the
    # per-side lifts are in detail["lift_left"] / detail["lift_right"].
    asymmetry: float | None
    weaker_side: str | None  # "left" | "right" (the person's own left/right) | None
    frames_used: int
    detail: dict = field(default_factory=dict)  # per-side numbers, for logs and the overlay


@dataclass(frozen=True)
class ArmResult:
    """Arm drift check: with both arms held up for 10 s, does one drift down?"""

    quality: Quality
    # Downward drift of each wrist over the hold, divided by shoulder width.
    left_drift: float | None
    right_drift: float | None
    drift_difference: float | None  # |left - right|
    weaker_side: str | None
    hold_seconds: float  # how long both arms were actually up
    frames_used: int
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SpeechResult:
    """Sentence repetition: did the person repeat the sentence clearly?"""

    quality: Quality
    expected: str
    transcript: str | None
    word_error_rate: float | None  # 0 = exact, 1 = nothing right
    words_per_minute: float | None
    longest_pause_s: float | None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResults:
    """Everything one check-in measured."""

    face: FaceResult
    arms: ArmResult
    speech: SpeechResult
    started_at: str  # ISO 8601 UTC


@dataclass(frozen=True)
class Baseline:
    """A person's own reference results, recorded when they were well."""

    patient_id: str
    recorded_at: str  # ISO 8601 UTC
    face_asymmetry: float
    arm_drift_difference: float
    speech_word_error_rate: float
    speech_words_per_minute: float
    sentence: str
    samples: int = 1  # how many check-ins were averaged into this baseline
    # Added later, all optional so older baseline files still load (None = not recorded).
    # Per-side values let decide notice the weaker side switching between check-ins.
    face_weaker_side: str | None = None  # "left" | "right" | None (within noise)
    face_lift_left: float | None = None  # mean corner lift, inter-ocular distances
    face_lift_right: float | None = None
    arm_drift_left: float | None = None  # mean wrist drift, shoulder widths (positive = down)
    arm_drift_right: float | None = None
    arm_weaker_side: str | None = None  # "left" | "right" | None (within noise)
    speech_longest_pause_s: float | None = None  # mean longest pause inside the sentence
    speech_mean_logprob: float | None = None  # mean Scribe word confidence (<= 0)


class Outcome(str, Enum):
    ESCALATE = "escalate"  # something changed: tell them to call 911 now, text the caregiver
    UNCLEAR = "unclear"  # could not measure well enough: treated exactly like ESCALATE
    NO_CHANGE_DETECTED = "no_change_detected"  # never phrased to the person as "you're fine"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reasons: list[str]  # plain-language, one per finding, safe to put in the caregiver text
    results: CheckResults
    baseline: Baseline | None
