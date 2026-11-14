"""Tests for piece 2 (arm drift). Synthetic landmark tracks exercise the maths without
MediaPipe; one smoke test runs the real model on a drawn (generated) figure."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from onset.measure import arms as A
from onset.types import Frame, Quality

W, H, FPS = 1280, 720, 15.0
CX, SY, SW = 640.0, 330.0, 300.0  # shoulder centre x, shoulder y, shoulder width (px)
REST_Y = 1.6  # sw below the shoulder line: hands at the sides, below the frame
RAISE_AT, RAISE_DUR = 1.0, 0.8
HOLD_REACHED = RAISE_AT + RAISE_DUR


def make_samples(
    duration: float = 14.0,
    lower_at: float | None = 13.0,
    left=lambda tau: 0.0,
    right=lambda tau: 0.0,
    up_level: float = -0.05,
    rel_x: float = 0.45,
    raise_arms: bool = True,
    shake=None,
    noise: float = 1.5,
    luma: float = 120.0,
    wrist_vis: float = 0.95,
    mp_swap: bool = False,
    pose: bool = True,
    seed: int = 0,
):
    """Build PoseSamples for a step. `left(tau)`/`right(tau)` give extra downward drift in
    shoulder widths, tau = seconds since the arms reached the up position. `shake(t)` returns
    (dx, dy, angle_rad, scale) camera motion applied to every landmark."""
    rng = np.random.default_rng(seed)
    out = []
    n = int(duration * FPS)
    for k in range(n):
        t = k / FPS
        if not pose:
            out.append(A.PoseSample(t, None, (W, H), luma))
            continue
        if not raise_arms or t < RAISE_AT:
            frac = 0.0
        elif t < HOLD_REACHED:
            frac = (t - RAISE_AT) / RAISE_DUR
        else:
            frac = 1.0
        if lower_at is not None and t >= lower_at:
            frac = max(0.0, 1.0 - (t - lower_at) / 0.6)
        tau = max(0.0, t - HOLD_REACHED)
        pts = {}
        # Person's left shoulder is on the image right (unmirrored).
        for name, sgn, drift in (("l", 1, left(tau) if frac == 1.0 else 0.0), ("r", -1, right(tau) if frac == 1.0 else 0.0)):
            sx = CX + sgn * SW / 2
            wy_rel = REST_Y + (up_level - REST_Y) * frac + drift
            wx_rel = sgn * (0.5 + (rel_x - 0.5) * frac)
            pts[name + "s"] = (sx, SY)
            pts[name + "w"] = (CX + wx_rel * SW, SY + wy_rel * SW)
            pts[name + "e"] = ((sx + pts[name + "w"][0]) / 2, (SY + pts[name + "w"][1]) / 2)
        rows = [pts["ls"], pts["rs"], pts["le"], pts["re"], pts["lw"], pts["rw"]]
        arr = np.array(rows, dtype=float)
        if shake is not None:
            dx, dy, ang, sc = shake(t)
            c = np.array([W / 2, H / 2])
            R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
            arr = c + sc * (arr - c) @ R.T + np.array([dx, dy])
        arr += rng.normal(0, noise, arr.shape)
        vis = np.full(6, 0.97)
        vis[4:] = wrist_vis
        for i in range(6):
            if not (0 <= arr[i, 1] <= H):
                vis[i] = 0.1
        full = np.column_stack([arr, vis])
        if mp_swap:  # MediaPipe swapped the whole skeleton's labels
            full = full[[1, 0, 3, 2, 5, 4]]
        out.append(A.PoseSample(t, A.canonical_sides(full), (W, H), luma))
    return out


def meter_from(samples):
    m = A.ArmMeter(model_path="", landmarker=object())
    for s in samples:
        m.add_sample(s)
    return m


# --------------------------------------------------------------------------------------------
# Synthetic tracks


def test_steady_hold():
    r = A.analyse(make_samples())
    assert r.quality == Quality.OK, r.detail
    assert abs(r.left_drift) < 0.05 and abs(r.right_drift) < 0.05
    assert r.drift_difference < 0.04
    assert r.weaker_side is None
    assert 9.5 <= r.hold_seconds <= 10.01
    assert r.frames_used >= 140


@pytest.mark.parametrize("side", ["left", "right"])
def test_one_arm_drifting(side):
    drift = lambda tau: 0.3 * min(tau, 10.0) / 10.0  # noqa: E731
    kw = {side: drift}
    r = A.analyse(make_samples(**kw))
    assert r.quality == Quality.OK, r.detail
    weak = r.left_drift if side == "left" else r.right_drift
    strong = r.right_drift if side == "left" else r.left_drift
    assert 0.2 < weak < 0.35
    assert abs(strong) < 0.05
    assert r.drift_difference > 0.18
    assert r.weaker_side == side


def test_swapped_mediapipe_labels_still_give_person_side():
    r = A.analyse(make_samples(left=lambda tau: 0.03 * tau, mp_swap=True))
    assert r.quality == Quality.OK
    assert r.weaker_side == "left"


def test_both_arms_drifting_equally():
    d = lambda tau: 0.25 * min(tau, 10.0) / 10.0  # noqa: E731
    r = A.analyse(make_samples(left=d, right=d))
    assert r.quality == Quality.OK
    assert r.left_drift > 0.15 and r.right_drift > 0.15
    assert r.drift_difference < 0.04
    assert r.weaker_side is None


def test_arms_out_to_the_sides():
    r = A.analyse(make_samples(rel_x=2.0, up_level=-0.3, right=lambda tau: 0.03 * tau))
    assert r.quality == Quality.OK, r.detail
    assert r.weaker_side == "right" and r.right_drift > 0.2


def test_phone_moving_is_compensated():
    # Hand-held phone: bobbing 30 px, tilting +-3 degrees, moving 4% closer/further.
    def shake(t):
        return (30 * math.sin(2 * math.pi * 0.8 * t), 25 * math.sin(2 * math.pi * 0.5 * t + 1),
                math.radians(3) * math.sin(2 * math.pi * 0.6 * t), 1 + 0.04 * math.sin(2 * math.pi * 0.3 * t))
    samples = make_samples(shake=shake)
    r = A.analyse(samples)
    assert r.quality == Quality.OK, r.detail
    assert r.drift_difference < 0.05
    assert abs(r.left_drift) < 0.07 and abs(r.right_drift) < 0.07
    # Without compensation the raw wrist heights would differ by much more than that.
    raw = np.array([[s.pts[A.LW, 1], s.pts[A.RW, 1]] for s in samples]) / SW
    assert np.ptp(raw[60:180, 0] - raw[60:180, 1]) > 2 * r.drift_difference + 0.05


def test_phone_shaking_hard_is_motion():
    def shake(t):
        return (120 * math.sin(2 * math.pi * 2.5 * t), 80 * math.sin(2 * math.pi * 2.0 * t), 0.0, 1.0)
    r = A.analyse(make_samples(shake=shake))
    assert r.quality == Quality.MOTION
    assert r.detail["cam_speed_p90"] > A.CAM_SPEED_MAX


def test_drift_is_found_even_while_phone_moves():
    def shake(t):
        return (30 * math.sin(2 * math.pi * 0.8 * t), 20 * math.sin(2 * math.pi * 0.5 * t), math.radians(3) * math.sin(t), 1.0)
    r = A.analyse(make_samples(shake=shake, left=lambda tau: 0.03 * min(tau, 10)))
    assert r.quality == Quality.OK
    assert r.weaker_side == "left" and r.drift_difference > 0.18


def test_arms_never_raised():
    samples = make_samples(raise_arms=False)
    m = meter_from(samples)
    assert not m.arms_up()
    r = m.result()
    assert r.quality == Quality.NO_RESPONSE
    assert r.left_drift is None


def test_both_arms_lowered_early_is_no_response():
    r = A.analyse(make_samples(lower_at=HOLD_REACHED + 4.0))
    assert r.quality == Quality.NO_RESPONSE
    assert r.hold_seconds < A.MIN_HOLD_S


def test_one_arm_drops_suddenly():
    # Left arm falls out of the frame 5 s into the hold; the right arm stays up.
    drop = lambda tau: 0.0 if tau < 5 else min(2.0, (tau - 5) * 4)  # noqa: E731
    r = A.analyse(make_samples(left=drop))
    assert r.quality == Quality.OK, r.detail
    assert r.left_drift > 1.0 and abs(r.right_drift) < 0.05
    assert r.weaker_side == "left"
    assert r.detail["early_drop"] == "left"


def test_one_arm_drops_then_other_early():
    # Left drops at 2 s, right lowered at 4 s: still a one-sided finding, not "no response".
    drop_l = lambda tau: 0.0 if tau < 2 else min(2.0, (tau - 2) * 4)  # noqa: E731
    drop_r = lambda tau: 0.0 if tau < 4 else min(2.0, (tau - 4) * 4)  # noqa: E731
    r = A.analyse(make_samples(left=drop_l, right=drop_r, lower_at=None))
    assert r.quality == Quality.OK, r.detail
    assert r.weaker_side == "left"
    assert r.hold_seconds < A.MIN_HOLD_S


def test_drift_then_corrected_is_caught():
    bump = lambda tau: 0.4 if 4 <= tau < 6 else 0.0  # noqa: E731
    r = A.analyse(make_samples(right=bump))
    assert r.quality == Quality.OK
    assert r.right_drift > 0.3 and r.detail["right_end"] < 0.05
    assert r.weaker_side == "right"


def test_no_body():
    assert A.analyse(make_samples(pose=False)).quality == Quality.NO_SUBJECT
    assert A.analyse([]).quality == Quality.NO_SUBJECT


def test_too_dark():
    assert A.analyse(make_samples(luma=10.0)).quality == Quality.TOO_DARK
    assert A.analyse(make_samples(pose=False, luma=30.0)).quality == Quality.TOO_DARK


def test_wrists_not_visible_is_partial():
    assert A.analyse(make_samples(wrist_vis=0.2)).quality == Quality.PARTIAL


def test_arms_above_frame_is_partial():
    # Arms raised overhead out of the top of the picture: up, but nothing to measure.
    r = A.analyse(make_samples(up_level=-1.3))
    assert r.quality == Quality.PARTIAL
    assert r.detail["hold_start"] is not None


def test_arms_up_timing():
    m = A.ArmMeter(model_path="", landmarker=object())
    became = None
    for s in make_samples():
        m.add_sample(s)
        if became is None and m.arms_up():
            became = s.t
    assert became is not None
    assert HOLD_REACHED <= became <= HOLD_REACHED + 0.8
    assert m.hold_start() == pytest.approx(became)
    assert m.result().detail["hold_start"] == pytest.approx(became, abs=0.01)


def test_live_numbers_and_draw():
    samples = make_samples(left=lambda tau: 0.03 * tau)
    m = A.ArmMeter(model_path="", landmarker=object())
    for s in samples[: int(9 * FPS)]:
        m.add_sample(s)
    live = m.live()
    assert live["arms_up"] and live["hold_elapsed"] > 5
    assert live["left_drift"] > live["right_drift"]
    rgba = np.zeros((360, 640, 4), np.uint8)  # overlay smaller than the source frame
    m.draw(rgba)
    assert rgba[..., 3].any()
    m.draw(np.zeros((H, W, 4), np.uint8), {"hold_elapsed": 3.0})
    empty = A.ArmMeter(model_path="", landmarker=object())
    empty.draw(np.zeros((H, W, 4), np.uint8))  # nothing seen yet: still draws the prompt


# --------------------------------------------------------------------------------------------
# MediaPipe smoke test on a generated figure (no images of real people are used or stored).


def draw_figure(left_deg: float, right_deg: float, w: int = W, h: int = H) -> np.ndarray:
    """A drawn person, head to waist, arms out to the sides at the given angles above
    horizontal. The person's left arm is on the image right."""
    import cv2

    img = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        img[y, :] = (200 - y // 8, 190 - y // 10, 170 - y // 12)
    cx, sh_y, half = w // 2, 330, 150
    skin, shirt, hair = (224, 172, 140), (40, 70, 150), (60, 40, 30)
    torso = np.array([[cx - half, sh_y], [cx + half, sh_y], [cx + half - 20, h], [cx - half + 20, h]], np.int32)
    cv2.fillPoly(img, [torso], shirt, cv2.LINE_AA)
    for s in (-1, 1):
        cv2.ellipse(img, (cx + s * (half - 10), sh_y + 20), (45, 40), 0, 0, 360, shirt, -1, cv2.LINE_AA)
    cv2.rectangle(img, (cx - 35, 230), (cx + 35, sh_y + 10), skin, -1)
    cv2.ellipse(img, (cx, 170), (85, 110), 0, 0, 360, skin, -1, cv2.LINE_AA)
    cv2.ellipse(img, (cx, 110), (90, 60), 0, 180, 360, hair, -1, cv2.LINE_AA)
    for dx in (-32, 32):
        cv2.ellipse(img, (cx + dx, 160), (14, 8), 0, 0, 360, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(img, (cx + dx, 160), 6, (40, 30, 20), -1, cv2.LINE_AA)
        cv2.line(img, (cx + dx - 18, 140), (cx + dx + 18, 138), hair, 5, cv2.LINE_AA)
    cv2.line(img, (cx, 165), (cx - 6, 200), (190, 130, 110), 3, cv2.LINE_AA)
    cv2.ellipse(img, (cx, 228), (30, 10), 0, 0, 180, (150, 60, 60), 4, cv2.LINE_AA)
    for s in (-1, 1):
        cv2.ellipse(img, (cx + s * 85, 170), (12, 25), 0, 0, 360, skin, -1, cv2.LINE_AA)
    for s, deg in ((1, left_deg), (-1, right_deg)):
        a = math.radians(deg)
        shx, shy = cx + s * (half - 10), sh_y + 20
        el = (int(shx + s * 150 * math.cos(a)), int(shy - 150 * math.sin(a)))
        wr = (int(el[0] + s * 140 * math.cos(a)), int(el[1] - 140 * math.sin(a)))
        cv2.line(img, (shx, shy), el, shirt, 70, cv2.LINE_AA)
        cv2.line(img, el, wr, skin, 50, cv2.LINE_AA)
        cv2.circle(img, wr, 38, skin, -1, cv2.LINE_AA)
    return cv2.GaussianBlur(img, (5, 5), 0)


@pytest.fixture(scope="module")
def model_path():
    try:
        return A.resolve_model("models/pose_landmarker_full.task")
    except FileNotFoundError:
        pytest.skip("pose model not available")


def test_mediapipe_smoke_generated_figure(model_path):
    m = A.ArmMeter(model_path)
    try:
        # Arms down 1 s, raise over 0.6 s, hold ~11 s with the person's left arm sinking.
        t, frames = 0.0, 0
        timings = []
        while t < 13.0:
            if t < 1.0:
                ang_l = ang_r = -70.0
            elif t < 1.6:
                ang_l = ang_r = -70.0 + (t - 1.0) / 0.6 * 85.0
            else:
                ang_r = 15.0
                ang_l = 15.0 - 2.5 * (t - 1.6)  # sinks ~25 degrees over the hold
            img = draw_figure(ang_l, ang_r)
            t1 = time.perf_counter()
            m.add(Frame(img, t))
            timings.append(time.perf_counter() - t1)
            t += 1 / FPS
            frames += 1
        assert m.samples[-1].pts is not None, "MediaPipe did not find the drawn figure"
        assert m.arms_up()
        r = m.result()
        print(f"\nmediapipe smoke: {1000 * np.median(timings[5:]):.1f} ms/frame median, result {r}")
        assert r.quality == Quality.OK, r.detail
        assert r.weaker_side == "left"
        assert r.left_drift > r.right_drift + 0.1
        rgba = np.zeros((H, W, 4), np.uint8)
        m.draw(rgba)
        assert rgba[..., 3].any()
    finally:
        m.close()


def test_measure_arms_without_person(model_path):
    blank = np.full((H, W, 3), 128, np.uint8)
    r = A.measure_arms([Frame(blank, i / FPS) for i in range(20)], model_path)
    assert r.quality == Quality.NO_SUBJECT


# --------------------------------------------------------------------------------------------
# Review fixes: never raise, reset()


class StubPose:
    """Stands in for MediaPipe Pose: plan(i) -> "fail" | None (no pose)."""

    def __init__(self, plan):
        self.plan = plan
        self.calls = 0
        self.timestamps: list[int] = []

    def detect_for_video(self, image, ms):
        from types import SimpleNamespace

        i = self.calls
        self.calls += 1
        self.timestamps.append(ms)
        if self.plan(i) == "fail":
            raise RuntimeError("pose landmarker crashed")
        return SimpleNamespace(pose_landmarks=[])

    def close(self):
        pass


SMALL = np.full((180, 320, 3), 120, np.uint8)


@pytest.fixture
def mp_module():
    pytest.importorskip("cv2")
    return pytest.importorskip("mediapipe")


def test_add_never_raises_when_mediapipe_fails(mp_module):
    # Fix 10: a MediaPipe error used to propagate out of add() into the call.
    m = A.ArmMeter(model_path="", landmarker=StubPose(lambda i: "fail"))
    for k in range(30):
        m.add(Frame(SMALL, k / FPS))
    r = m.result()
    assert r.quality == Quality.ERROR
    assert r.detail["errors"] == 30


def test_bad_frame_never_raises(mp_module):
    m = A.ArmMeter(model_path="", landmarker=StubPose(lambda i: None))
    m.add(Frame(None, 0.0))  # no image at all
    m.add(Frame(SMALL, float("nan")))
    assert m.result().quality == Quality.ERROR


def test_some_failures_count_as_frames_without_a_body(mp_module):
    m = A.ArmMeter(model_path="", landmarker=StubPose(lambda i: "fail" if i % 3 == 0 else None))
    for k in range(30):
        m.add(Frame(SMALL, k / FPS))
    assert len(m.samples) == 30  # failed frames are kept as "no body", not dropped
    r = m.result()
    assert r.quality == Quality.NO_SUBJECT
    assert r.detail["errors"] == 10


def test_reset_clears_the_step_and_keeps_timestamps_increasing(mp_module):
    # Fix 11: re-prompt "raise both arms" with the same meter; Frame.t restarts at 0.
    stub = StubPose(lambda i: None)
    m = A.ArmMeter(model_path="", landmarker=stub)
    for s in make_samples():
        m.add_sample(s)
    assert m.arms_up()
    for k in range(15):
        m.add(Frame(SMALL, k / FPS))
    first = list(stub.timestamps)
    m.reset()
    assert m.samples == [] and not m.arms_up() and m.hold_start() is None
    for k in range(15):
        m.add(Frame(SMALL, k / FPS))
    ts = stub.timestamps
    assert all(b > a for a, b in zip(ts, ts[1:])), "MediaPipe needs strictly increasing timestamps"
    assert ts[len(first)] > first[-1]
    assert ts[len(first) + 1] - ts[len(first)] == pytest.approx(1000 / FPS, abs=1)
    assert len(m.samples) == 15


def test_reset_after_failures_allows_a_clean_retry():
    m = A.ArmMeter(model_path="", landmarker=object())
    m._n_errors = 5  # as if MediaPipe had failed on the first attempt
    assert m.result().quality == Quality.ERROR
    m.reset()
    for s in make_samples():
        m.add_sample(s)
    assert m.result().quality == Quality.OK


def test_reset_is_documented():
    assert "reset()" in A.__doc__ and A.ArmMeter.reset.__doc__
