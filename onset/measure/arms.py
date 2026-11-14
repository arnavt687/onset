"""Piece 2: arm drift from the caller's video (MediaPipe Pose Landmarker).

Owner: the `arms` session. See ROADMAP.md, piece 2 and notes/arms.md.

The test is a camera version of the pronator drift test: the person raises both arms (in
front of them or out to the sides; FaceTime framing varies) and holds them for 10 s. A weak
arm drifts or drops. We measure how far each wrist moves *down* over the hold.

Layout of this module
---------------------
* `PoseSample` is one frame's worth of landmarks (shoulders, elbows, wrists, in pixels) plus
  frame brightness. `sample_from_landmarks` makes one from MediaPipe output; tests build them
  directly, so all the maths below runs without MediaPipe.
* Pure functions on a list of samples: `body_track` (camera-motion-compensated wrist tracks),
  `find_hold_start`, `find_hold_end`, `analyse` (-> ArmResult).
* `ArmMeter` wraps MediaPipe for live use: `add(frame)`, `arms_up()`, `result()`, `draw()`.

Coordinates
-----------
Every wrist is expressed in a *body frame* rebuilt each video frame from the two shoulders:
origin at the shoulder midpoint, x along the shoulder line, y perpendicular to it pointing
down, unit = shoulder width. The phone moving, tilting, or coming closer moves the shoulders
and the wrists together, so it cancels out. What is left is the arms moving relative to the
body. Drift is the increase of a wrist's body-frame y over the hold (positive = down).

Left/right are the person's own. The caller's image is not mirrored, so the person's left is
on the image right. MediaPipe labels landmarks anatomically but sometimes swaps the whole
skeleton on frontal views; `sample_from_landmarks` re-labels each frame so that the "left"
shoulder is always the one on the image right.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from onset.types import ArmResult, Frame, Quality

# ---------------------------------------------------------------------------------------------
# Tunables. Units: "sw" = shoulder widths, seconds, 0..255 luma.

HOLD_SECONDS = 10.0  # the flow times this long from arms_up()
MIN_HOLD_S = 6.0  # a hold shorter than this is NO_RESPONSE (unless one arm clearly dropped first)
STABLE_S = 0.5  # both wrists up and steady this long -> arms_up()
STABLE_RANGE = 0.20  # sw: max wrist movement inside the stable window
UP_LEVEL = 0.25  # sw: wrist at most this far below the shoulder line counts as "up"
DOWN_LEVEL = 0.75  # sw: wrist at least this far below counts as "down" (hysteresis with UP)
DOWN_SUSTAIN_S = 0.3  # a wrist must stay down this long to count as having come down
EARLY_DROP_GAP_S = 1.0  # one arm down this long before the other = a real one-sided drop
WINDOW_S = 1.5  # first/last window for the robust start/end level
LOWER_TRIM_S = 0.5  # when both arms came down, ignore this much before that moment
SMOOTH_S = 0.5  # rolling median for the sudden-drop (peak) measure
MIN_WINDOW_FILL = 0.4  # each window needs this fraction of its expected frames
MIN_COVERAGE = 0.6  # fraction of hold frames where both wrists must be measurable
MIN_SUBJECT = 0.25  # fraction of all frames with both shoulders found
VIS_MIN = 0.5  # MediaPipe visibility needed to trust a landmark
EDGE = 0.01  # fraction of the frame treated as the edge
MIN_SW_FRAC = 0.06  # shoulder width under this fraction of frame width: too far / side-on
CAM_SPEED_MAX = 1.5  # sw/s, 90th percentile shoulder-midpoint speed: phone moving too much
JITTER_MAX = 0.06  # sw/frame, median residual wrist jitter after compensation
SW_RATIO_MAX = 1.35  # p90/p10 shoulder width over the hold: person turning / big zoom change
DARK_LUMA = 45.0  # dim: blame darkness when the body is not found
VERY_DARK_LUMA = 20.0  # too dark whatever the tracker says
SIDE_NOISE = 0.03  # sw: below this difference, weaker_side is None

# Wrist states.
OK_PT, BELOW, ABOVE, MISSING = 0, 1, 2, 3

# Landmark rows in PoseSample.pts (person's own sides).
LS, RS, LE, RE, LW, RW = range(6)
_MP_INDEX = (11, 12, 13, 14, 15, 16)  # MediaPipe Pose indices for the rows above
_SWAP = (RS, LS, RE, LE, RW, LW)


@dataclass(frozen=True)
class PoseSample:
    """One frame: landmarks in pixels of the source frame, or no pose at all."""

    t: float
    pts: np.ndarray | None  # 6 x 3: x_px, y_px, visibility for LS, RS, LE, RE, LW, RW
    size: tuple[int, int]  # (width, height) of the source frame
    luma: float = 128.0  # mean brightness 0..255


def frame_luma(rgb: np.ndarray) -> float:
    """Mean brightness (Rec. 601 luma) on a 1-in-8 subsample; cheap enough per frame."""
    s = rgb[::8, ::8].astype(np.float32)
    return float((0.299 * s[..., 0] + 0.587 * s[..., 1] + 0.114 * s[..., 2]).mean())


def sample_from_landmarks(t: float, landmarks, size: tuple[int, int], luma: float) -> PoseSample:
    """Build a PoseSample from one MediaPipe pose (list of NormalizedLandmark) or None."""
    if not landmarks:
        return PoseSample(t, None, size, luma)
    w, h = size
    pts = np.array(
        [[landmarks[i].x * w, landmarks[i].y * h, landmarks[i].visibility or 0.0] for i in _MP_INDEX],
        dtype=np.float64,
    )
    return PoseSample(t, canonical_sides(pts), size, luma)


def canonical_sides(pts: np.ndarray) -> np.ndarray:
    """Person's left shoulder must be on the image right (unmirrored, facing the camera)."""
    if pts[LS, 0] < pts[RS, 0]:
        return pts[list(_SWAP)]
    return pts


def wrist_state(x: float, y: float, vis: float, size: tuple[int, int]) -> int:
    """OK_PT if trustworthy and in frame; BELOW/ABOVE if predicted off the bottom/top edge."""
    w, h = size
    if not np.isfinite(y):
        return MISSING
    if y > h * (1 - EDGE):
        return BELOW
    if y < h * EDGE:
        return ABOVE
    if vis >= VIS_MIN and -w * EDGE <= x <= w * (1 + EDGE):
        return OK_PT
    return MISSING


def shoulders_ok(s: PoseSample) -> bool:
    if s.pts is None:
        return False
    w, h = s.size
    for row in (LS, RS):
        x, y, v = s.pts[row]
        if v < VIS_MIN or not (0 <= x <= w and 0 <= y <= h):
            return False
    return float(np.hypot(*(s.pts[LS, :2] - s.pts[RS, :2]))) > 1.0


# ---------------------------------------------------------------------------------------------
# Pure track maths


@dataclass(frozen=True)
class BodyTrack:
    """Per-frame arrays, all length N (N = number of samples)."""

    t: np.ndarray
    ok: np.ndarray  # bool: both shoulders found
    sw: np.ndarray  # shoulder width, px (nan when not ok)
    mid: np.ndarray  # N x 2 shoulder midpoint, px
    rel: np.ndarray  # N x 2 x 2: [frame, side(0=left,1=right), (x, y)] in body frame, sw units
    state: np.ndarray  # N x 2 wrist state per side
    luma: np.ndarray
    width: int

    def up(self) -> np.ndarray:
        """N x 2 bool: wrist up (body-frame y small, or predicted above the frame top)."""
        y = self.rel[:, :, 1]
        with np.errstate(invalid="ignore"):
            u = (self.state == OK_PT) & (y <= UP_LEVEL)
        u |= self.state == ABOVE
        return u & self.ok[:, None]

    def down(self) -> np.ndarray:
        y = self.rel[:, :, 1]
        with np.errstate(invalid="ignore"):
            d = (self.state == OK_PT) & (y >= DOWN_LEVEL)
        d |= self.state == BELOW
        return d & self.ok[:, None]

    def measurable(self) -> np.ndarray:
        """N x 2 bool: we have a y value for that wrist (in frame, or clamped at the bottom)."""
        return np.isfinite(self.rel[:, :, 1]) & self.ok[:, None]


def body_track(samples: Sequence[PoseSample]) -> BodyTrack:
    """Express both wrists in the shoulder-based body frame (see module docstring)."""
    n = len(samples)
    t = np.array([s.t for s in samples], dtype=np.float64)
    ok = np.zeros(n, bool)
    sw = np.full(n, np.nan)
    mid = np.full((n, 2), np.nan)
    rel = np.full((n, 2, 2), np.nan)
    state = np.full((n, 2), MISSING, dtype=np.int8)
    luma = np.array([s.luma for s in samples], dtype=np.float64)
    width = samples[0].size[0] if samples else 0
    for i, s in enumerate(samples):
        if not shoulders_ok(s):
            continue
        ls, rs = s.pts[LS, :2], s.pts[RS, :2]
        u = ls - rs
        width_px = float(np.hypot(*u))
        u = u / width_px  # unit vector along the shoulders, image-left to image-right
        d = np.array([-u[1], u[0]])  # perpendicular, pointing down the image
        m = (ls + rs) / 2
        ok[i], sw[i], mid[i] = True, width_px, m
        for side, row in ((0, LW), (1, RW)):
            x, y, v = s.pts[row]
            st = wrist_state(x, y, v, s.size)
            state[i, side] = st
            if st == OK_PT:
                p = np.array([x, y])
            elif st == BELOW:  # off the bottom edge: it is at least as low as the edge
                p = np.array([x, max(y, float(s.size[1]))])
            else:
                continue
            rel[i, side] = ((p - m) @ u / width_px, (p - m) @ d / width_px)
    return BodyTrack(t, ok, sw, mid, rel, state, luma, width)


def _fps(t: np.ndarray) -> float:
    if len(t) < 2:
        return 15.0
    dt = np.diff(t)
    dt = dt[dt > 0]
    return float(1.0 / np.median(dt)) if len(dt) else 15.0


def find_hold_start(track: BodyTrack) -> float | None:
    """Time at which both wrists have been up and steady for STABLE_S, else None."""
    up = track.up().all(axis=1)
    t, y = track.t, track.rel[:, :, 1]
    need = max(3, int(0.6 * STABLE_S * _fps(t)))
    for i in range(len(t)):
        if not up[i] or t[i] - t[0] < STABLE_S * 0.9:
            continue
        win = (t >= t[i] - STABLE_S) & (t <= t[i]) & track.ok
        if win.sum() < need or not up[win].all():
            continue
        yw = y[win]
        rng = [np.ptp(c[np.isfinite(c)]) if np.isfinite(c).sum() else 0.0 for c in yw.T]
        if max(rng) <= STABLE_RANGE:
            return float(t[i])
    return None


def _sustained(flag: np.ndarray, skip: np.ndarray, t: np.ndarray, start: int) -> float | None:
    """First time from index `start` where `flag` holds for DOWN_SUSTAIN_S. Frames marked
    `skip` (no data) neither extend nor break a run."""
    run_start = None
    for i in range(start, len(t)):
        if skip[i]:
            continue
        if flag[i]:
            if run_start is None:
                run_start = i
            if t[i] - t[run_start] >= DOWN_SUSTAIN_S:
                return float(t[run_start])
        else:
            run_start = None
    return None


def find_hold_end(track: BodyTrack, t0: float) -> tuple[float, dict, bool]:
    """End of the hold: t0 + HOLD_SECONDS, the last frame, or when both arms came down.
    Also returns when each arm first came down (None if it stayed up until the end) and
    whether the hold ended because both arms came down."""
    t = track.t
    i0 = int(np.searchsorted(t, t0))
    down = track.down()
    nodata = (track.state == MISSING) | ~track.ok[:, None]
    tb = _sustained(down.all(axis=1), nodata.any(axis=1), t, i0)
    end = min(t0 + HOLD_SECONDS, float(t[-1]))
    by_drop = tb is not None and tb < end
    if by_drop:
        end = tb
    first_down = {}
    for side, name in ((0, "left"), (1, "right")):
        td = _sustained(down[:, side], nodata[:, side], t, i0)
        first_down[name] = round(td, 2) if td is not None and td <= end else None
    return end, first_down, by_drop


def _median_in(t: np.ndarray, y: np.ndarray, a: float, b: float, fps: float) -> float | None:
    m = (t >= a) & (t <= b) & np.isfinite(y)
    if m.sum() < max(2, MIN_WINDOW_FILL * (b - a) * fps):
        return None
    return float(np.median(y[m]))


def side_drift(t: np.ndarray, y: np.ndarray, t0: float, t1: float, fps: float) -> dict:
    """Downward movement of one wrist over [t0, t1], in shoulder widths.

    end = median of the last WINDOW_S minus median of the first WINDOW_S.
    peak = largest SMOOTH_S rolling median minus the start level (catches a drop that was
    corrected before the end). drift = max(end, peak).
    """
    start = _median_in(t, y, t0, t0 + WINDOW_S, fps)
    end_level = _median_in(t, y, t1 - WINDOW_S, t1, fps)
    if start is None or end_level is None:
        return {"drift": None, "end": None, "peak": None, "start_level": start}
    end = end_level - start
    peak = end
    for i in np.nonzero((t >= t0) & (t <= t1))[0]:
        lvl = _median_in(t, y, t[i] - SMOOTH_S, t[i], fps)
        if lvl is not None and lvl - start > peak:
            peak = lvl - start
    return {"drift": max(end, peak), "end": end, "peak": peak, "start_level": start}


def motion_stats(track: BodyTrack, m: np.ndarray) -> dict:
    """Camera / body motion over the frames selected by mask m (after compensation)."""
    t, idx = track.t, np.nonzero(m & track.ok)[0]
    out = {"cam_speed_p90": 0.0, "jitter": 0.0, "sw_ratio": 1.0}
    if len(idx) < 3:
        return out
    sw = track.sw[idx]
    dt = np.diff(t[idx])
    dt[dt <= 0] = 1e-3
    speed = np.hypot(*np.diff(track.mid[idx], axis=0).T) / sw[1:] / dt
    out["cam_speed_p90"] = float(np.percentile(speed, 90))
    jit = []
    for side in (0, 1):
        dy = np.abs(np.diff(track.rel[idx, side, 1]))
        dy = dy[np.isfinite(dy)]
        if len(dy):
            jit.append(float(np.median(dy)))
    out["jitter"] = max(jit) if jit else 0.0
    out["sw_ratio"] = float(np.percentile(sw, 90) / max(np.percentile(sw, 10), 1e-6))
    return out


def analyse(samples: Sequence[PoseSample]) -> ArmResult:
    """The whole check, from a step's samples. Pure: no MediaPipe needed."""
    if not samples:
        return _result(Quality.NO_SUBJECT, reason="no_frames")
    tr = body_track(samples)
    fps = _fps(tr.t)
    luma = float(np.median(tr.luma))
    dim = luma < DARK_LUMA
    base = {"luma": round(luma, 1), "fps": round(fps, 1), "frames": len(samples)}

    if tr.ok.mean() < MIN_SUBJECT:
        q = Quality.TOO_DARK if dim else Quality.NO_SUBJECT
        return _result(q, reason="body_not_found", **base)
    if luma < VERY_DARK_LUMA:
        return _result(Quality.TOO_DARK, reason="very_dark", **base)

    t0 = find_hold_start(tr)
    if t0 is None:
        seen = (tr.state[tr.ok] != MISSING).all(axis=1).mean() if tr.ok.any() else 0.0
        if seen < 0.3:  # we never could see where the hands were
            q = Quality.TOO_DARK if dim else Quality.PARTIAL
            return _result(q, reason="wrists_not_visible", **base)
        return _result(Quality.NO_RESPONSE, reason="arms_not_raised", **base)

    t1, first_down, by_drop = find_hold_end(tr, t0)
    hold = t1 - t0
    # Arms lowered together at the end: leave the lowering movement out of the "last" window.
    t_meas = max(t0 + 0.5, t1 - LOWER_TRIM_S) if by_drop else t1
    in_hold = (tr.t >= t0) & (tr.t <= t1)
    meas = tr.measurable().all(axis=1) & in_hold
    sides = {}
    for side, name in ((0, "left"), (1, "right")):
        sides[name] = side_drift(tr.t, tr.rel[:, side, 1], t0, t_meas, fps)
    ld, rd = sides["left"]["drift"], sides["right"]["drift"]
    diff = abs(ld - rd) if ld is not None and rd is not None else None
    weaker = None
    if diff is not None and diff > SIDE_NOISE:
        weaker = "left" if ld > rd else "right"
    mot = motion_stats(tr, in_hold)
    sw_ref = float(np.nanmedian(tr.sw[in_hold & tr.ok])) if (in_hold & tr.ok).any() else 0.0
    detail = {
        **base,
        "hold_start": round(t0, 2),
        "hold_end": round(t1, 2),
        "first_down": first_down,
        "shoulder_width_px": round(sw_ref, 1),
        **{k: round(v, 3) for k, v in mot.items()},
        **{f"{n}_{k}": (round(v, 3) if v is not None else None) for n in sides for k, v in sides[n].items()},
    }
    # One arm clearly came down well before the other: that is the finding, not a non-response.
    fl, fr = first_down["left"], first_down["right"]
    early_drop = None
    if fl is not None and (fr is None or fr - fl >= EARLY_DROP_GAP_S):
        early_drop = "left"
    elif fr is not None and (fl is None or fl - fr >= EARLY_DROP_GAP_S):
        early_drop = "right"
    detail["early_drop"] = early_drop

    def res(q: Quality, reason: str) -> ArmResult:
        return ArmResult(
            quality=q,
            left_drift=_r(ld),
            right_drift=_r(rd),
            drift_difference=_r(diff),
            weaker_side=weaker,
            hold_seconds=round(hold, 2),
            frames_used=int(meas.sum()),
            detail={**detail, "reason": reason},
        )

    expected = max(1.0, hold * fps)
    if hold < MIN_HOLD_S and early_drop is None:
        return res(Quality.NO_RESPONSE, "hold_too_short")
    if meas.sum() / expected < MIN_COVERAGE or ld is None or rd is None:
        return res(Quality.TOO_DARK if dim else Quality.PARTIAL, "wrists_lost_during_hold")
    if sw_ref < MIN_SW_FRAC * tr.width:
        return res(Quality.PARTIAL, "too_far_or_side_on")
    if mot["cam_speed_p90"] > CAM_SPEED_MAX or mot["jitter"] > JITTER_MAX or mot["sw_ratio"] > SW_RATIO_MAX:
        return res(Quality.MOTION, "camera_or_body_moving")
    return res(Quality.OK, "ok")


def _r(v: float | None) -> float | None:
    return None if v is None else round(float(v), 4)


def _result(q: Quality, **detail) -> ArmResult:
    return ArmResult(q, None, None, None, None, 0.0, 0, detail)


# ---------------------------------------------------------------------------------------------
# MediaPipe wrapper


def resolve_model(path: str) -> str:
    """Find the model: as given, relative to the repo, $ONSET_MODELS_DIR, or ~/onset/models."""
    p = Path(path)
    repo = Path(__file__).resolve().parents[2]
    candidates = [p, repo / p]
    for d in (os.environ.get("ONSET_MODELS_DIR"), str(Path.home() / "onset" / "models")):
        if d:
            candidates.append(Path(d) / p.name)
    for c in candidates:
        if c.is_file():
            return str(c)
    raise FileNotFoundError(f"pose model not found: tried {', '.join(map(str, candidates))}")


class ArmMeter:
    """Feed frames for the whole "hold both arms up" step, then read the result.

    `add()` runs MediaPipe (~25 ms on this CPU at the default 640 px input, see notes/arms.md),
    so call it from a worker thread in an async agent. `stride=2` runs the model on every
    other frame if that is ever needed; the maths copes with any frame rate.
    """

    HOLD_SECONDS = HOLD_SECONDS

    def __init__(
        self,
        model_path: str = "models/pose_landmarker_full.task",
        *,
        input_width: int = 640,
        stride: int = 1,
        landmarker=None,
    ) -> None:
        self.samples: list[PoseSample] = []
        self.input_width = input_width
        self.stride = max(1, stride)
        self._n = 0
        self._last_ms = -1
        self._t0: float | None = None
        self._landmarker = landmarker
        if landmarker is None and model_path:
            from mediapipe.tasks import python as mpt
            from mediapipe.tasks.python import vision

            opts = vision.PoseLandmarkerOptions(
                base_options=mpt.BaseOptions(model_asset_path=resolve_model(model_path)),
                running_mode=vision.RunningMode.VIDEO,
                num_poses=1,
            )
            self._landmarker = vision.PoseLandmarker.create_from_options(opts)

    # -- input ---------------------------------------------------------------------------
    def add(self, frame: Frame) -> None:
        self._n += 1
        if (self._n - 1) % self.stride:
            return
        import cv2
        import mediapipe as mp

        h, w = frame.rgb.shape[:2]
        img = frame.rgb
        if self.input_width and w > self.input_width:
            img = cv2.resize(img, (self.input_width, round(h * self.input_width / w)), interpolation=cv2.INTER_AREA)
        img = np.ascontiguousarray(img)
        ms = max(int(frame.t * 1000), self._last_ms + 1)
        self._last_ms = ms
        out = self._landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=img), ms)
        lms = out.pose_landmarks[0] if out.pose_landmarks else None
        self.add_sample(sample_from_landmarks(frame.t, lms, (w, h), frame_luma(img)))

    def add_sample(self, sample: PoseSample) -> None:
        """Feed a precomputed sample (tests, replays)."""
        self.samples.append(sample)
        if self._t0 is None and len(self.samples) >= 3:
            # Only the recent past matters for the start; keep this O(window) per frame.
            recent = [s for s in self.samples if s.t >= sample.t - STABLE_S - 0.5]
            self._t0 = find_hold_start(body_track(recent))

    # -- output --------------------------------------------------------------------------
    def arms_up(self) -> bool:
        """True once both wrists are above the shoulders and steady ~0.5 s; the 10 s hold is
        timed from then (`hold_start()`)."""
        return self._t0 is not None

    def hold_start(self) -> float | None:
        return self._t0

    def result(self) -> ArmResult:
        return analyse(self.samples)

    def live(self) -> dict:
        """Numbers for the overlay: hold elapsed and drift so far."""
        out: dict = {"arms_up": self.arms_up(), "hold_elapsed": 0.0, "left_drift": None, "right_drift": None}
        if not self.samples or self._t0 is None:
            return out
        recent = [s for s in self.samples if s.t >= self._t0 - 0.1]
        tr = body_track(recent)
        t1 = min(float(tr.t[-1]), self._t0 + HOLD_SECONDS)
        out["hold_elapsed"] = max(0.0, t1 - self._t0)
        fps = _fps(tr.t)
        for side, name in ((0, "left"), (1, "right")):
            y = tr.rel[:, side, 1]
            start = _median_in(tr.t, y, self._t0, self._t0 + WINDOW_S, fps)
            now = _median_in(tr.t, y, t1 - SMOOTH_S, t1, fps)
            out[f"{name}_start_level"] = start
            if start is not None and now is not None and t1 - self._t0 >= WINDOW_S:
                out[f"{name}_drift"] = now - start
        return out

    def draw(self, rgba: np.ndarray, live: dict | None = None) -> None:
        """Draw shoulder-to-wrist lines, the hold timer and live drift numbers onto an RGBA
        frame (same orientation as the input frames), in place. `live` may override the
        numbers from `self.live()` (e.g. {"hold_elapsed": flow_clock})."""
        import cv2

        info = self.live()
        if live:
            info.update(live)
        h, w = rgba.shape[:2]
        fs = h / 720.0
        last = next((s for s in reversed(self.samples) if s.pts is not None), None)
        col = {"left": (80, 200, 255, 255), "right": (255, 170, 60, 255)}
        if last is not None:
            sx, sy = w / last.size[0], h / last.size[1]
            P = lambda row: (int(last.pts[row, 0] * sx), int(last.pts[row, 1] * sy))  # noqa: E731
            vis = lambda row: last.pts[row, 2] >= VIS_MIN  # noqa: E731
            if vis(LS) and vis(RS):
                cv2.line(rgba, P(LS), P(RS), (255, 255, 255, 255), max(1, int(2 * fs)), cv2.LINE_AA)
            for name, s_, e_, w_ in (("left", LS, LE, LW), ("right", RS, RE, RW)):
                if not vis(s_):
                    continue
                chain = [P(s_)] + ([P(e_)] if vis(e_) else []) + ([P(w_)] if vis(w_) else [])
                for a, b in zip(chain, chain[1:]):
                    cv2.line(rgba, a, b, col[name], max(2, int(4 * fs)), cv2.LINE_AA)
                if vis(w_):
                    cv2.circle(rgba, P(w_), max(3, int(8 * fs)), col[name], -1, cv2.LINE_AA)
                    lvl = info.get(f"{name}_start_level")
                    if lvl is not None and vis(LS) and vis(RS):  # tick where this wrist started
                        ls, rs = last.pts[LS, :2], last.pts[RS, :2]
                        u = (ls - rs) / np.hypot(*(ls - rs))
                        d = np.array([-u[1], u[0]])
                        swp = np.hypot(*(ls - rs))
                        px = last.pts[w_, :2]
                        rx = (px - (ls + rs) / 2) @ u / swp
                        q = (ls + rs) / 2 + swp * (rx * u + lvl * d)
                        q = (int(q[0] * sx), int(q[1] * sy))
                        half = int(25 * fs)
                        cv2.line(rgba, (q[0] - half, q[1]), (q[0] + half, q[1]), col[name], max(1, int(2 * fs)), cv2.LINE_AA)
        # Timer and numbers, top-left panel.
        font, th = cv2.FONT_HERSHEY_SIMPLEX, max(1, int(2 * fs))
        x0, y0 = int(16 * fs), int(36 * fs)
        if info.get("arms_up"):
            el = float(info.get("hold_elapsed") or 0.0)
            text = f"Hold {min(el, HOLD_SECONDS):4.1f} / {HOLD_SECONDS:.0f} s"
            bar_w = int(260 * fs)
            cv2.rectangle(rgba, (x0, y0 + int(10 * fs)), (x0 + bar_w, y0 + int(22 * fs)), (255, 255, 255, 120), -1)
            cv2.rectangle(rgba, (x0, y0 + int(10 * fs)), (x0 + int(bar_w * min(el / HOLD_SECONDS, 1)), y0 + int(22 * fs)), (90, 220, 120, 255), -1)
        else:
            text = "Raise both arms"
        cv2.putText(rgba, text, (x0, y0), font, 0.9 * fs, (255, 255, 255, 255), th, cv2.LINE_AA)
        yy = y0 + int(52 * fs)
        for name in ("left", "right"):
            v = info.get(f"{name}_drift")
            s = f"{name[0].upper()} drift " + ("--" if v is None else f"{v:+.2f}")
            cv2.putText(rgba, s, (x0, yy), font, 0.75 * fs, col[name], th, cv2.LINE_AA)
            yy += int(30 * fs)

    def close(self) -> None:
        if self._landmarker is not None and hasattr(self._landmarker, "close"):
            self._landmarker.close()


def measure_arms(frames: Sequence[Frame], model_path: str = "models/pose_landmarker_full.task") -> ArmResult:
    """Convenience: run ArmMeter over a list of frames from the whole step."""
    meter = ArmMeter(model_path)
    try:
        for f in frames:
            meter.add(f)
        return meter.result()
    finally:
        meter.close()
