"""Piece 1: smile asymmetry from the caller's video (MediaPipe Face Landmarker).

Owner: the `face` session. See ROADMAP.md, piece 1, and notes/face.md.

How it works
------------
For every frame MediaPipe gives 478 face landmarks. From them we take the two eye centers,
the two mouth corners and the nose tip, and express the mouth corners in a *face frame*:

* origin at the midpoint between the eyes,
* x axis along the eye line (so head tilt / roll is rotated away),
* unit length = inter-ocular distance (so distance to the camera does not matter).

In that frame each corner has an *outward* coordinate (away from the face midline) and an
*up* coordinate (towards the eye line). The neutral position of each corner is the median
over the neutral frames. During the smile, each corner's displacement from its neutral
position is projected on the up-and-outward diagonal; that is the corner's *lift*. The lift
per side is the median over the frames where the smile is at its fullest, and

    asymmetry = 1 - weaker_lift / stronger_lift      (0 = symmetric, 1 = one side did not move)

Sides are the person's own: the image is not mirrored, so the person's left is image right
(MediaPipe's landmark 263/291 side).

Everything that does geometry or decides quality is a pure function on landmark arrays
(`face_geometry`, `corner_lifts`, `asymmetry_index`, `evaluate`), so it is unit-tested without
images or MediaPipe. `FaceMeter` only turns frames into `FaceObs` and calls `evaluate`.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from onset.types import FaceResult, Frame, Quality

# --------------------------------------------------------------------------- landmarks
# MediaPipe Face Mesh indices. "R_" is the person's right = image left (non-mirrored image).
R_EYE = (33, 133)  # outer, inner corner
L_EYE = (263, 362)
R_MOUTH = 61
L_MOUTH = 291
NOSE_TIP = 1
KEY_POINTS = (*R_EYE, *L_EYE, R_MOUTH, L_MOUTH, NOSE_TIP)

# Contours used only for drawing.
LIPS_OUTER = (61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291,
              375, 321, 405, 314, 17, 84, 181, 91, 146, 61)
LIPS_INNER = (78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308,
              324, 318, 402, 317, 14, 87, 178, 88, 95, 78)
R_EYE_LOOP = (33, 246, 161, 160, 159, 158, 157, 173, 133, 155, 154, 153, 145, 144, 163, 7, 33)
L_EYE_LOOP = (263, 466, 388, 387, 386, 385, 384, 398, 362, 382, 381, 380, 374, 373, 390, 249, 263)

# --------------------------------------------------------------------------- thresholds
MIN_FACE_FRACTION = 0.5  # face must be found in at least half of all frames, else NO_SUBJECT
MIN_NEUTRAL_FRAMES = 5  # usable frames needed in each phase, else PARTIAL
MIN_SMILE_FRAMES = 8
MAX_YAW_DEG = 20.0  # frames with the head turned further are not used
EDGE_MARGIN = 0.02  # key points must be this far (fraction of the image) inside the frame
MIN_LUMA = 40.0  # median brightness (0-255) of the face region (whole frame if no face)
MAX_MOTION = 1.0  # median head speed, inter-ocular distances per second
MIN_SMILE_LIFT = 0.03  # stronger corner must lift at least this much (IOD units)
MIN_ASYM_FOR_SIDE = 0.05  # below this, weaker_side is None (within measurement noise)
PEAK_FRACTION = 0.5  # smile frames with total lift >= this x the 90th percentile are "peak"
MIN_PEAK_FRAMES = 5
NOSE_DEPTH = 0.64  # nose tip depth in front of the eye centers, IOD units (canonical mesh)

DEFAULT_MODEL = "models/face_landmarker.task"


# --------------------------------------------------------------------------- pure geometry


@dataclass(frozen=True)
class FaceGeom:
    """One frame's face, reduced to what the smile check needs."""

    center: np.ndarray  # (2,) eye midpoint, pixels
    x_axis: np.ndarray  # (2,) unit vector along the eye line, towards the person's left
    iod: float  # inter-ocular distance, pixels
    roll_deg: float  # head tilt; positive = eye line rotated clockwise in the image
    yaw_deg: float  # head turn proxy from the nose tip; positive = towards the person's left
    corners: np.ndarray  # (2, 2): rows [left, right] (person's), cols [outward, up], IOD units

    @property
    def y_axis(self) -> np.ndarray:
        """Unit vector pointing down the face (perpendicular to the eye line)."""
        return np.array([-self.x_axis[1], self.x_axis[0]])

    def to_face(self, p: np.ndarray) -> np.ndarray:
        """Image pixels -> face frame (x along eye line, y down the face), IOD units."""
        d = np.asarray(p, dtype=float)[..., :2] - self.center
        return np.stack([d @ self.x_axis, d @ self.y_axis], axis=-1) / self.iod

    def to_image(self, q: np.ndarray) -> np.ndarray:
        """Face frame (IOD units) -> image pixels."""
        q = np.asarray(q, dtype=float)
        return self.center + self.iod * (q[..., :1] * self.x_axis + q[..., 1:2] * self.y_axis)

    def corner_to_image(self, side: int, out_up: np.ndarray) -> np.ndarray:
        """A corner position given as (outward, up) for side 0=left, 1=right -> pixels."""
        sign = 1.0 if side == 0 else -1.0
        return self.to_image(np.array([sign * out_up[0], -out_up[1]]))


def face_geometry(pts: np.ndarray) -> FaceGeom | None:
    """Reduce one frame's landmarks (N x 2+ array in pixels, MediaPipe indexing) to a FaceGeom.

    Returns None when the eyes coincide (degenerate input).
    """
    p = np.asarray(pts, dtype=float)[:, :2]
    eye_r = p[list(R_EYE)].mean(axis=0)
    eye_l = p[list(L_EYE)].mean(axis=0)
    v = eye_l - eye_r
    iod = float(np.hypot(*v))
    if not np.isfinite(iod) or iod < 1e-6:
        return None
    x_axis = v / iod
    center = (eye_l + eye_r) / 2
    y_axis = np.array([-x_axis[1], x_axis[0]])

    def local(i: int) -> np.ndarray:
        d = p[i] - center
        return np.array([d @ x_axis, d @ y_axis]) / iod

    ml, mr, nose = local(L_MOUTH), local(R_MOUTH), local(NOSE_TIP)
    corners = np.array([[ml[0], -ml[1]], [-mr[0], -mr[1]]])
    roll = math.degrees(math.atan2(x_axis[1], x_axis[0]))
    # Turning the head by yaw moves the nose tip sideways by NOSE_DEPTH * tan(yaw) IODs
    # relative to the eye midpoint (orthographic view; the IOD itself shrinks by cos(yaw)).
    yaw = math.degrees(math.atan(nose[0] / NOSE_DEPTH))
    return FaceGeom(center=center, x_axis=x_axis, iod=iod, roll_deg=roll, yaw_deg=yaw,
                    corners=corners)


def corner_lifts(neutral: np.ndarray, smile: np.ndarray) -> dict:
    """Per-side lift from neutral to smile.

    neutral: (n, 2, 2) and smile: (m, 2, 2) arrays of FaceGeom.corners.
    Returns per-side median lift and its outward / up parts, over the peak smile frames.
    """
    neutral = np.asarray(neutral, dtype=float)
    smile = np.asarray(smile, dtype=float)
    rest = np.median(neutral, axis=0)  # (2, 2)
    disp = smile - rest  # (m, 2, 2)
    lift = (disp[..., 0] + disp[..., 1]) / math.sqrt(2)  # (m, 2): up-and-outward
    total = lift.sum(axis=1)
    top = float(np.percentile(total, 90))
    peak = total >= PEAK_FRACTION * top if top > 0 else np.ones(len(total), bool)
    if peak.sum() < min(MIN_PEAK_FRAMES, len(total)):
        peak = np.zeros(len(total), bool)
        peak[np.argsort(total)[-min(MIN_PEAK_FRAMES, len(total)):]] = True
    med_lift = np.median(lift[peak], axis=0)
    med_disp = np.median(disp[peak], axis=0)
    per_frame = [asymmetry_index(a, b) for a, b in lift[peak]]
    per_frame = [x for x in per_frame if x is not None]
    return {
        "lift_left": float(med_lift[0]),
        "lift_right": float(med_lift[1]),
        "out_left": float(med_disp[0, 0]),
        "up_left": float(med_disp[0, 1]),
        "out_right": float(med_disp[1, 0]),
        "up_right": float(med_disp[1, 1]),
        "peak_frames": int(peak.sum()),
        "per_frame_asymmetry_median": float(np.median(per_frame)) if per_frame else None,
        "neutral_corners": rest.tolist(),
    }


def asymmetry_index(lift_left: float, lift_right: float) -> float | None:
    """1 - weaker / stronger lift, in [0, 1]. None if neither side lifted."""
    strong = max(lift_left, lift_right)
    if strong <= 0:
        return None
    weak = max(min(lift_left, lift_right), 0.0)
    return float(min(1.0, max(0.0, 1.0 - weak / strong)))


@dataclass
class FaceObs:
    """What one processed frame contributed. pts is None when no face was found."""

    t: float  # seconds, Frame.t
    pts: np.ndarray | None  # (N, 2+) landmarks in pixels
    luma: float  # mean brightness of the face region (whole frame if no face), 0-255
    width: int
    height: int
    geom: FaceGeom | None = field(default=None)

    def __post_init__(self) -> None:
        if self.geom is None and self.pts is not None:
            self.geom = face_geometry(self.pts)

    def usable(self) -> tuple[bool, str]:
        """(usable, reason) for the smile measurement."""
        if self.pts is None or self.geom is None:
            return False, "no_face"
        key = np.asarray(self.pts, dtype=float)[list(KEY_POINTS), :2]
        mx, my = EDGE_MARGIN * self.width, EDGE_MARGIN * self.height
        inside = ((key[:, 0] >= mx) & (key[:, 0] <= self.width - mx)
                  & (key[:, 1] >= my) & (key[:, 1] <= self.height - my))
        if not inside.all():
            return False, "edge"
        if abs(self.geom.yaw_deg) > MAX_YAW_DEG:
            return False, "turned"
        return True, "ok"


def _motion(obs: Sequence[FaceObs]) -> float | None:
    """Median head speed in IOD per second over consecutive usable frames of one phase."""
    speeds = []
    prev = None
    for o in obs:
        if not o.usable()[0]:
            prev = None
            continue
        if prev is not None and o.t > prev.t:
            g0, g1 = prev.geom, o.geom
            dt = o.t - prev.t
            move = np.hypot(*(g1.center - g0.center)) / g1.iod
            zoom = abs(g1.iod - g0.iod) / g1.iod
            speeds.append(math.hypot(move, zoom) / dt)
        prev = o
    return float(np.median(speeds)) if speeds else None


def _result(quality: Quality, frames_used: int, detail: dict, asym=None, side=None) -> FaceResult:
    return FaceResult(quality=quality, asymmetry=asym, weaker_side=side,
                      frames_used=frames_used, detail=detail)


def evaluate(neutral: Sequence[FaceObs], smile: Sequence[FaceObs]) -> FaceResult:
    """Quality gates, then the smile measurement. Pure: no images, no MediaPipe."""
    allobs = list(neutral) + list(smile)
    detail: dict = {
        "frames_neutral": len(neutral),
        "frames_smile": len(smile),
        "thresholds": {
            "min_face_fraction": MIN_FACE_FRACTION, "max_yaw_deg": MAX_YAW_DEG,
            "min_luma": MIN_LUMA, "max_motion_iod_per_s": MAX_MOTION,
            "min_smile_lift": MIN_SMILE_LIFT,
        },
    }
    if not allobs:
        detail["reason"] = "no frames"
        return _result(Quality.NO_SUBJECT, 0, detail)

    luma = float(np.median([o.luma for o in allobs]))
    found = sum(o.pts is not None for o in allobs) / len(allobs)
    detail.update(median_luma=round(luma, 1), face_found_fraction=round(found, 3))
    if luma < MIN_LUMA:
        detail["reason"] = "too dark"
        return _result(Quality.TOO_DARK, 0, detail)
    if found < MIN_FACE_FRACTION:
        detail["reason"] = "no face in most frames"
        return _result(Quality.NO_SUBJECT, 0, detail)

    use_n = [o for o in neutral if o.usable()[0]]
    use_s = [o for o in smile if o.usable()[0]]
    reasons: dict[str, int] = {}
    for o in allobs:
        r = o.usable()[1]
        reasons[r] = reasons.get(r, 0) + 1
    geoms = [o.geom for o in use_n + use_s]
    detail.update(
        usable_neutral=len(use_n), usable_smile=len(use_s), frame_status=reasons,
        yaw_deg_median=round(float(np.median([g.yaw_deg for g in geoms])), 1) if geoms else None,
        roll_deg_median=round(float(np.median([g.roll_deg for g in geoms])), 1) if geoms else None,
        iod_px_median=round(float(np.median([g.iod for g in geoms])), 1) if geoms else None,
    )
    frames_used = len(use_n) + len(use_s)
    if len(use_n) < MIN_NEUTRAL_FRAMES or len(use_s) < MIN_SMILE_FRAMES:
        worst = max((k for k in reasons if k != "ok"), key=reasons.get, default="few frames")
        detail["reason"] = {"turned": "head turned away", "edge": "face cut off by the frame edge",
                            "no_face": "face lost in too many frames"}.get(worst, worst)
        return _result(Quality.PARTIAL, frames_used, detail)

    speeds = [s for s in (_motion(neutral), _motion(smile)) if s is not None]
    motion = max(speeds) if speeds else 0.0
    detail["motion_iod_per_s"] = round(motion, 3)
    if motion > MAX_MOTION:
        detail["reason"] = "too much movement"
        return _result(Quality.MOTION, frames_used, detail)

    lifts = corner_lifts(np.array([g.corners for g in (o.geom for o in use_n)]),
                         np.array([g.corners for g in (o.geom for o in use_s)]))
    detail.update({k: (round(v, 4) if isinstance(v, float) else v) for k, v in lifts.items()})
    strong = max(lifts["lift_left"], lifts["lift_right"])
    if strong < MIN_SMILE_LIFT:
        detail["reason"] = "no smile seen"
        return _result(Quality.NO_RESPONSE, frames_used, detail)

    asym = asymmetry_index(lifts["lift_left"], lifts["lift_right"])
    side = None
    if asym is not None and asym >= MIN_ASYM_FOR_SIDE:
        side = "left" if lifts["lift_left"] < lifts["lift_right"] else "right"
    return _result(Quality.OK, frames_used, detail, asym=round(asym, 4), side=side)


# --------------------------------------------------------------------------- MediaPipe


def find_model(path: str | os.PathLike | None = None) -> Path:
    """Locate face_landmarker.task: $ONSET_FACE_MODEL, $ONSET_MODELS (CI), the given path,
    the repo, or ~/onset."""
    rel = Path(path or DEFAULT_MODEL)
    repo = Path(__file__).resolve().parents[2]
    cands = [Path(os.environ["ONSET_FACE_MODEL"])] if os.environ.get("ONSET_FACE_MODEL") else []
    if os.environ.get("ONSET_MODELS"):
        cands.append(Path(os.environ["ONSET_MODELS"]) / Path(DEFAULT_MODEL).name)
    cands += [rel, repo / rel, Path.home() / "onset" / rel,
              Path.home() / "onset" / DEFAULT_MODEL]
    for c in cands:
        if c.is_file():
            return c
    raise FileNotFoundError(f"face landmarker model not found; tried {[str(c) for c in cands]}")


def _luma(rgb: np.ndarray, pts: np.ndarray | None) -> float:
    """Mean brightness of the face box (or the whole frame), on a 4x subsample."""
    h, w = rgb.shape[:2]
    if pts is not None:
        x0, y0 = np.clip(pts[:, :2].min(axis=0), 0, [w - 1, h - 1]).astype(int)
        x1, y1 = np.clip(pts[:, :2].max(axis=0), 0, [w - 1, h - 1]).astype(int)
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            rgb = rgb[y0:y1 + 1, x0:x1 + 1]
    s = rgb[::4, ::4].astype(np.float32)
    return float((s[..., 0] * 0.299 + s[..., 1] * 0.587 + s[..., 2] * 0.114).mean())


class FaceMeter:
    """Feed frames during the neutral and smile phases, then read the result.

    process_every=2 runs MediaPipe on every other frame (use it if the measured cost per
    frame is too high for live use; see notes/face.md). Not thread-safe: call from one task.
    """

    def __init__(self, model_path: str = DEFAULT_MODEL, process_every: int = 1) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(find_model(model_path))),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(opts)
        self.process_every = max(1, int(process_every))
        self.neutral: list[FaceObs] = []
        self.smile: list[FaceObs] = []
        self._phase = "neutral"
        self._seen = 0
        self._ts_base = 0
        self._last_ts = -1
        self._ms: list[float] = []
        self._errors: list[str] = []
        self._last: FaceObs | None = None
        self._rest: np.ndarray | None = None  # neutral corner median, for live numbers
        self._live: dict = {}

    # ---- feeding

    def add_neutral(self, frame: Frame) -> None:
        """A frame while the person looks at the camera with a relaxed face."""
        self._add(frame, "neutral")

    def add_smile(self, frame: Frame) -> None:
        """A frame while the person is asked to smile."""
        self._add(frame, "smile")

    def _timestamp_ms(self, t: float, phase: str) -> int:
        if phase != self._phase:  # Frame.t restarts per step: continue after the last frame
            self._phase = phase
            self._ts_base = self._last_ts + 1 - int(round(t * 1000))
        ts = self._ts_base + int(round(t * 1000))
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts
        return ts

    def _add(self, frame: Frame, phase: str) -> None:
        self._seen += 1
        ts = self._timestamp_ms(frame.t, phase)
        if (self._seen - 1) % self.process_every:
            return
        try:
            rgb = np.ascontiguousarray(frame.rgb[..., :3])
            h, w = rgb.shape[:2]
            t0 = time.perf_counter()
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
            res = self._landmarker.detect_for_video(image, ts)
            self._ms.append((time.perf_counter() - t0) * 1000)
            pts = None
            if res.face_landmarks:
                lm = res.face_landmarks[0]
                pts = np.array([(p.x * w, p.y * h, p.z * w) for p in lm], dtype=np.float32)
            obs = FaceObs(t=frame.t, pts=pts, luma=_luma(rgb, pts), width=w, height=h)
        except Exception as e:  # never let one bad frame break the call
            self._errors.append(f"{type(e).__name__}: {e}")
            return
        (self.neutral if phase == "neutral" else self.smile).append(obs)
        self._last = obs
        self._update_live(obs, phase)

    def _update_live(self, obs: FaceObs, phase: str) -> None:
        live: dict = {"phase": phase, "face": obs.geom is not None}
        if obs.geom is not None:
            live.update(yaw=obs.geom.yaw_deg, roll=obs.geom.roll_deg)
        if phase == "smile" and self._rest is None:
            good = [o.geom.corners for o in self.neutral if o.usable()[0]]
            if good:
                self._rest = np.median(np.array(good), axis=0)
        if phase == "smile" and self._rest is not None and obs.geom is not None:
            d = obs.geom.corners - self._rest
            lift = (d[:, 0] + d[:, 1]) / math.sqrt(2)
            live.update(lift_left=float(lift[0]), lift_right=float(lift[1]),
                        asym=asymmetry_index(float(lift[0]), float(lift[1])))
        self._live = live

    # ---- reading

    @property
    def ms_per_frame(self) -> float | None:
        return float(np.median(self._ms)) if self._ms else None

    def result(self) -> FaceResult:
        processed = len(self.neutral) + len(self.smile)
        if self._errors and len(self._errors) >= max(1, processed):
            return FaceResult(Quality.ERROR, None, None, 0,
                              {"reason": "landmarker failed", "errors": self._errors[-3:]})
        try:
            res = evaluate(self.neutral, self.smile)
        except Exception as e:  # pragma: no cover - defensive
            return FaceResult(Quality.ERROR, None, None, 0, {"reason": f"{type(e).__name__}: {e}"})
        res.detail.update(ms_per_frame=round(self.ms_per_frame, 1) if self._ms else None,
                          process_every=self.process_every, errors=len(self._errors))
        return res

    def draw(self, rgba: np.ndarray, live: dict | None = None) -> None:
        """Draw face landmarks and live numbers onto an RGBA frame, in place.

        rgba may be the caller's frame or a letterboxed copy of it (as agent.py sends): the
        landmarks are mapped with the same centered fit.
        """
        import cv2

        h, w = rgba.shape[:2]
        lines: list[str] = []
        obs = self._last
        if obs is not None and obs.pts is not None and obs.geom is not None:
            s = min(w / obs.width, h / obs.height)
            ox, oy = (w - obs.width * s) / 2, (h - obs.height * s) / 2

            def px(p: np.ndarray) -> tuple[int, int]:
                return int(round(p[0] * s + ox)), int(round(p[1] * s + oy))

            pts = obs.pts
            cyan, yellow, white = (0, 220, 255, 255), (255, 220, 0, 255), (255, 255, 255, 255)
            for loop, col in ((LIPS_OUTER, cyan), (LIPS_INNER, cyan),
                              (R_EYE_LOOP, yellow), (L_EYE_LOOP, yellow)):
                poly = np.array([px(pts[i]) for i in loop], np.int32)
                cv2.polylines(rgba, [poly], False, col, 1, cv2.LINE_AA)
            g = obs.geom
            e0 = g.to_image(np.array([-0.5, 0.0]))
            e1 = g.to_image(np.array([0.5, 0.0]))
            cv2.line(rgba, px(e0), px(e1), white, 1, cv2.LINE_AA)
            for side, idx in ((0, L_MOUTH), (1, R_MOUTH)):
                cur = px(pts[idx])
                cv2.circle(rgba, cur, 4, (0, 255, 0, 255), -1, cv2.LINE_AA)
                if self._rest is not None:
                    rest = px(g.corner_to_image(side, self._rest[side]))
                    cv2.circle(rgba, rest, 3, white, 1, cv2.LINE_AA)
                    cv2.line(rgba, rest, cur, (0, 255, 0, 255), 1, cv2.LINE_AA)
        lv = self._live
        if lv:
            if not lv.get("face"):
                lines.append(f"face: not found ({lv.get('phase')})")
            else:
                lines.append(f"{lv['phase']}  yaw {lv['yaw']:+.0f}  tilt {lv['roll']:+.0f} deg")
                if "lift_left" in lv:
                    a = lv.get("asym")
                    lines.append(f"lift L {lv['lift_left']:+.3f}  R {lv['lift_right']:+.3f}"
                                 f"  asym {'-' if a is None else f'{a:.2f}'}")
        for k, v in (live or {}).items():
            lines.append(f"{k}: {v:.3f}" if isinstance(v, float) else f"{k}: {v}")
        font = cv2.FONT_HERSHEY_SIMPLEX
        for i, line in enumerate(lines):
            org = (10, h - 12 - 22 * (len(lines) - 1 - i))
            (tw, th), base = cv2.getTextSize(line, font, 0.55, 1)
            cv2.rectangle(rgba, (org[0] - 4, org[1] - th - 4), (org[0] + tw + 4, org[1] + base + 2),
                          (0, 0, 0, 255), -1)
            cv2.putText(rgba, line, org, font, 0.55, (0, 255, 0, 255), 1, cv2.LINE_AA)

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> FaceMeter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def measure_face(neutral: Sequence[Frame], smile: Sequence[Frame],
                 model_path: str = DEFAULT_MODEL, process_every: int = 1) -> FaceResult:
    """Convenience: run FaceMeter over two lists of frames."""
    with FaceMeter(model_path, process_every=process_every) as meter:
        for f in neutral:
            meter.add_neutral(f)
        for f in smile:
            meter.add_smile(f)
        return meter.result()
