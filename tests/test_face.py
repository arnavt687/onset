"""Piece 1: smile asymmetry.

Most tests build synthetic landmark arrays (a 3D face posed and projected), so they need no
images and no MediaPipe. The smoke tests at the end render MediaPipe's own canonical face
mesh (read from the model file, no photo of a real person) and run the real landmarker; they
are skipped when mediapipe or the model file is missing.
"""

from __future__ import annotations

import math
import zipfile

import numpy as np
import pytest

from onset.measure import face as F
from onset.types import Frame, Quality

# Key landmarks of MediaPipe's canonical face model, cm, y up, z towards the camera.
KEY3D = {
    33: (-4.446, 2.664, 3.173), 133: (-1.856, 2.585, 3.758),
    263: (4.446, 2.664, 3.173), 362: (1.856, 2.585, 3.758),
    61: (-2.456, -4.343, 4.284), 291: (2.456, -4.343, 4.284),
    1: (0.0, -1.127, 7.476),
}
IOD_CM = 2 * 3.151
W, H = 1280, 720


def rotation(yaw=0.0, roll=0.0, pitch=0.0) -> np.ndarray:
    y, r, p = (math.radians(a) for a in (yaw, roll, pitch))
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return rz @ ry @ rx


def smile_offsets(lift_left: float, lift_right: float) -> dict[int, np.ndarray]:
    """Corner displacement (cm) for a lift in IOD units, split evenly up and outward."""
    out = {}
    for idx, lift, sign in ((F.L_MOUTH, lift_left, 1.0), (F.R_MOUTH, lift_right, -1.0)):
        d = lift * IOD_CM / math.sqrt(2)
        out[idx] = np.array([sign * d, d, 0.0])
    return out


def synth_pts(lift_left=0.0, lift_right=0.0, yaw=0.0, roll=0.0, pitch=0.0, scale=22.0,
              center=(W / 2, H / 2), jitter=0.0, rng=None) -> np.ndarray:
    """478 x 3 landmark array in pixels, MediaPipe indexing (only key points are meaningful)."""
    P = np.tile(np.array([0.0, 0.0, 4.0]), (478, 1))
    for i, v in KEY3D.items():
        P[i] = v
    for i, d in smile_offsets(lift_left, lift_right).items():
        P[i] = P[i] + d
    P = P @ rotation(yaw, roll, pitch).T
    pts = np.c_[center[0] + P[:, 0] * scale, center[1] - P[:, 1] * scale, -P[:, 2] * scale]
    if jitter and rng is not None:
        pts[:, :2] += rng.normal(0, jitter, size=(478, 2))
    return pts


def obs_series(n: int, t0: float = 0.0, fps: float = 15.0, luma: float = 120.0,
               lifts=None, face_every: int = 1, move_px: float = 0.0, seed: int = 0,
               cx: float = W / 2, **pose):
    """n FaceObs at fps. lifts: callable frame_index -> (left, right)."""
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        ll, lr = lifts(k) if lifts else (0.0, 0.0)
        c = (cx + move_px * (k % 2), H / 2)
        has = k % face_every == 0
        pts = synth_pts(ll, lr, center=c, jitter=0.8, rng=rng, **pose) if has else None
        out.append(F.FaceObs(t=t0 + k / fps, pts=pts, luma=luma, width=W, height=H))
    return out


def ramp(left: float, right: float, delay: int = 8):
    """Reaction delay of `delay` frames, then a 4-frame ramp to a held smile."""
    def f(k):
        a = min(1.0, max(0.0, (k - delay) / 4))
        return left * a, right * a
    return f


def run(left: float, right: float, neutral_n=30, smile_n=45, **kw):
    neutral = obs_series(neutral_n, seed=1, **kw)
    smile = obs_series(smile_n, lifts=ramp(left, right), seed=2, **kw)
    return F.evaluate(neutral, smile)


# --------------------------------------------------------------------------- pure geometry


def test_geometry_frontal_units_and_sides():
    g = F.face_geometry(synth_pts(scale=22.0))
    assert g.iod == pytest.approx(IOD_CM * 22.0, rel=1e-3)
    assert g.roll_deg == pytest.approx(0, abs=1e-6)
    assert g.yaw_deg == pytest.approx(0, abs=1e-6)
    # rows = [person's left, person's right]; both corners ~0.39 IOD out, ~1.1 IOD below eyes
    assert g.corners[0] == pytest.approx(g.corners[1])
    assert g.corners[0, 0] == pytest.approx(2.456 / IOD_CM, rel=1e-3)
    assert g.corners[0, 1] == pytest.approx(-(4.343 + 2.6245) / IOD_CM, rel=1e-3)
    # person's left eye/mouth corner is on the image right
    pts = synth_pts()
    assert pts[F.L_EYE[0], 0] > pts[F.R_EYE[0], 0]


def test_geometry_scale_and_tilt_invariant():
    base = F.face_geometry(synth_pts(0.1, 0.05, scale=22.0)).corners
    small = F.face_geometry(synth_pts(0.1, 0.05, scale=9.0)).corners
    tilted = F.face_geometry(synth_pts(0.1, 0.05, roll=20.0)).corners
    np.testing.assert_allclose(small, base, atol=1e-9)
    np.testing.assert_allclose(tilted, base, atol=1e-9)
    assert F.face_geometry(synth_pts(roll=20.0)).roll_deg == pytest.approx(-20.0, abs=1e-6)


def test_yaw_proxy_tracks_head_turn():
    for yaw in (-30, -10, 0, 15, 30):
        assert F.face_geometry(synth_pts(yaw=yaw)).yaw_deg == pytest.approx(yaw, abs=1.0)


def test_face_frame_round_trip():
    g = F.face_geometry(synth_pts(roll=12.0, center=(500, 300)))
    p = np.array([[530.0, 410.0], [100.0, 50.0]])
    np.testing.assert_allclose(g.to_image(g.to_face(p)), p, atol=1e-9)
    pts = synth_pts(roll=12.0, center=(500, 300))
    np.testing.assert_allclose(g.corner_to_image(0, g.corners[0]), pts[F.L_MOUTH, :2], atol=1e-9)
    np.testing.assert_allclose(g.corner_to_image(1, g.corners[1]), pts[F.R_MOUTH, :2], atol=1e-9)


def test_asymmetry_index():
    assert F.asymmetry_index(0.0, 0.0) is None
    assert F.asymmetry_index(0.1, 0.1) == 0.0
    assert F.asymmetry_index(0.1, 0.05) == pytest.approx(0.5)
    assert F.asymmetry_index(0.05, 0.1) == pytest.approx(0.5)
    assert F.asymmetry_index(0.1, -0.05) == 1.0  # weak side moved down/in: fully asymmetric


def test_corner_lifts_uses_peak_frames_not_reaction_delay():
    neutral = np.array([F.face_geometry(synth_pts()).corners] * 10)
    late = [F.face_geometry(synth_pts(*ramp(0.12, 0.12, delay=25)(k))).corners for k in range(45)]
    r = F.corner_lifts(neutral, np.array(late))
    assert r["lift_left"] == pytest.approx(0.12, abs=0.005)
    assert r["lift_right"] == pytest.approx(0.12, abs=0.005)


# --------------------------------------------------------------------------- evaluate


def test_symmetric_smile():
    r = run(0.12, 0.12)
    assert r.quality is Quality.OK
    assert r.asymmetry < 0.05
    assert r.weaker_side is None
    assert r.detail["lift_left"] == pytest.approx(0.12, abs=0.01)
    assert r.frames_used == 75


@pytest.mark.parametrize("left,right,side", [(0.04, 0.12, "left"), (0.12, 0.04, "right")])
def test_one_sided_droop(left, right, side):
    r = run(left, right)
    assert r.quality is Quality.OK
    assert r.asymmetry == pytest.approx(1 - 0.04 / 0.12, abs=0.05)
    assert r.weaker_side == side


def test_droop_where_weak_side_does_not_move():
    r = run(0.0, 0.12)
    assert r.quality is Quality.OK
    assert r.asymmetry > 0.85
    assert r.weaker_side == "left"


def test_head_tilt_gives_same_result():
    flat = run(0.05, 0.12)
    tilted = run(0.05, 0.12, roll=18.0)
    assert tilted.quality is Quality.OK
    assert tilted.asymmetry == pytest.approx(flat.asymmetry, abs=0.03)
    assert tilted.weaker_side == "left"
    assert abs(tilted.detail["roll_deg_median"]) == pytest.approx(18.0, abs=1.0)


def test_small_head_turn_still_measured():
    r = run(0.12, 0.12, yaw=12.0)
    assert r.quality is Quality.OK
    assert r.asymmetry < 0.08


def test_turned_head_is_partial():
    r = run(0.12, 0.04, yaw=35.0)
    assert r.quality is Quality.PARTIAL
    assert r.asymmetry is None
    assert r.detail["reason"] == "head turned away"


def test_no_smile_is_no_response():
    r = run(0.0, 0.0)
    assert r.quality is Quality.NO_RESPONSE
    assert r.asymmetry is None and r.weaker_side is None


def test_too_dark():
    r = run(0.12, 0.12, luma=20.0)
    assert r.quality is Quality.TOO_DARK


def test_no_face_in_most_frames():
    r = run(0.12, 0.12, face_every=3)
    assert r.quality is Quality.NO_SUBJECT


def test_no_frames():
    assert F.evaluate([], []).quality is Quality.NO_SUBJECT


def test_too_much_motion():
    r = run(0.12, 0.12, move_px=40.0)  # ~40 px jumps at 15 fps, IOD ~139 px: ~4 IOD/s
    assert r.quality is Quality.MOTION


def test_face_cut_off_by_edge_is_partial():
    r = run(0.12, 0.12, cx=60.0)
    assert r.quality is Quality.PARTIAL
    assert "edge" in r.detail["reason"]


def test_never_reports_a_reassuring_word():
    for r in (run(0.12, 0.12), run(0.0, 0.0), run(0.04, 0.12)):
        text = repr(r).lower()
        assert not any(w in text for w in ("fine", "normal", "healthy"))


# --------------------------------------------------------------------------- MediaPipe smoke


def _canonical_mesh():
    """MediaPipe's canonical face mesh (468 vertices, cm; triangles) from the model file."""
    path = F.find_model()
    data = zipfile.ZipFile(path).read("geometry_pipeline_metadata_landmarks.binarypb")

    def varint(b, i):
        r = s = 0
        while True:
            c = b[i]
            i += 1
            r |= (c & 0x7F) << s
            s += 7
            if c < 0x80:
                return r, i

    def fields(b):
        i = 0
        while i < len(b):
            k, i = varint(b, i)
            f, w = k >> 3, k & 7
            if w == 0:
                v, i = varint(b, i)
            elif w == 1:
                v, i = b[i:i + 8], i + 8
            elif w == 5:
                v, i = b[i:i + 4], i + 4
            elif w == 2:
                n, i = varint(b, i)
                v, i = b[i:i + n], i + n
            else:
                raise ValueError(w)
            yield f, w, v

    mesh = next(v for f, w, v in fields(data) if f == 1)
    floats, idx = [], []
    for f, w, v in fields(mesh):
        if f == 3 and w == 5:
            floats.append(np.frombuffer(v, "<f4")[0])
        elif f == 3 and w == 2:
            floats.extend(np.frombuffer(v, "<f4"))
        elif f == 4 and w == 0:
            idx.append(v)
        elif f == 4 and w == 2:
            i = 0
            while i < len(v):
                x, i = varint(v, i)
                idx.append(x)
    verts = np.array(floats, dtype=float).reshape(-1, 5)[:, :3]
    return verts, np.array(idx).reshape(-1, 3)


LIP_SET = set(F.LIPS_OUTER) | set(F.LIPS_INNER)


def _render(verts, tris, yaw=0.0, roll=0.0, scale=24.0, center=(W / 2, H / 2), bright=1.0):
    """Flat-shaded render of the mesh: skin-coloured face, red lips, on a plain background."""
    import cv2

    P = verts @ rotation(yaw, roll).T
    img = np.empty((H, W, 3), np.uint8)
    img[:] = (int(90 * bright), int(110 * bright), int(130 * bright))
    xy = np.c_[center[0] + P[:, 0] * scale, center[1] - P[:, 1] * scale]
    tri = P[tris]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
    light = np.array([0.3, 0.4, 1.0]) / np.linalg.norm([0.3, 0.4, 1.0])
    for t in np.argsort(P[tris, 2].mean(axis=1)):
        base = np.array([190, 90, 90]) if all(i in LIP_SET for i in tris[t]) else np.array([225, 180, 150])
        col = np.clip(base * (0.25 + 0.75 * abs(n[t] @ light)) * bright, 0, 255)
        cv2.fillConvexPoly(img, xy[tris[t]].astype(np.int32), tuple(int(c) for c in col), cv2.LINE_AA)
    return img


def _smile_mesh(verts, lift_left, lift_right, sigma=1.0):
    """Pull the skin around each mouth corner up and outward (Gaussian falloff)."""
    v = verts.copy()
    for idx, d in smile_offsets(lift_left, lift_right).items():
        w = np.exp(-np.sum((verts - verts[idx]) ** 2, axis=1) / (2 * sigma**2))
        v += w[:, None] * d
    return v


@pytest.fixture(scope="module")
def mesh():
    pytest.importorskip("mediapipe")
    pytest.importorskip("cv2")
    try:
        return _canonical_mesh()
    except FileNotFoundError:
        pytest.skip("face_landmarker.task not available")


def _frames(mesh, lifts, n, bright=1.0, yaw=0.0):
    verts, tris = mesh
    out = []
    for k in range(n):
        ll, lr = lifts(k)
        img = _render(_smile_mesh(verts, ll, lr), tris, yaw=yaw, bright=bright,
                      center=(W / 2 + (k % 3), H / 2))
        out.append(Frame(rgb=img, t=k / 15))
    return out


def test_mediapipe_finds_rendered_face_with_expected_sides(mesh):
    verts, tris = mesh
    meter = F.FaceMeter()
    try:
        meter.add_neutral(Frame(rgb=_render(verts, tris), t=0.0))
        obs = meter.neutral[0]
    finally:
        meter.close()
    assert obs.pts is not None and obs.pts.shape[0] >= 468
    assert obs.pts[F.L_EYE[0], 0] > obs.pts[F.R_EYE[0], 0]  # person's left = image right
    assert abs(obs.geom.roll_deg) < 3 and abs(obs.geom.yaw_deg) < 10


@pytest.mark.parametrize("left,right,side", [(0.0, 0.15, "left"), (0.15, 0.0, "right")])
def test_mediapipe_end_to_end_one_sided_smile(mesh, left, right, side):
    neutral = _frames(mesh, lambda k: (0.0, 0.0), 20)
    smile = _frames(mesh, ramp(left, right, delay=4), 30)
    r = F.measure_face(neutral, smile)
    print("one-sided", side, r.quality, r.asymmetry, r.weaker_side,
          {k: r.detail.get(k) for k in ("lift_left", "lift_right", "ms_per_frame")})
    assert r.quality is Quality.OK, r.detail
    assert r.weaker_side == side
    assert r.asymmetry > 0.5


def test_mediapipe_end_to_end_symmetric_smile(mesh):
    neutral = _frames(mesh, lambda k: (0.0, 0.0), 20)
    smile = _frames(mesh, ramp(0.15, 0.15, delay=4), 30)
    r = F.measure_face(neutral, smile)
    print("symmetric", r.quality, r.asymmetry,
          {k: r.detail.get(k) for k in ("lift_left", "lift_right", "ms_per_frame")})
    assert r.quality is Quality.OK, r.detail
    assert r.asymmetry < 0.25


def test_mediapipe_dark_and_empty_frames(mesh):
    dark = _frames(mesh, lambda k: (0.0, 0.0), 10, bright=0.12)
    assert F.measure_face(dark[:5], dark[5:]).quality is Quality.TOO_DARK
    empty = [Frame(rgb=np.full((H, W, 3), 128, np.uint8), t=k / 15) for k in range(10)]
    assert F.measure_face(empty[:5], empty[5:]).quality is Quality.NO_SUBJECT


def _recorded_smile_steps():
    """Recorded calls with a smile step (recordings/ is gitignored; see tools/replay.py)."""
    import os
    from pathlib import Path

    root = Path(os.environ.get("ONSET_RECORD_DIR", Path(__file__).resolve().parents[1] / "recordings"))
    return sorted(p.parent for p in root.glob("*/smile/meta.json")) if root.is_dir() else []


@pytest.mark.parametrize("call_dir", _recorded_smile_steps() or [None])
def test_recorded_calls_give_a_result(mesh, call_dir):
    """On any locally recorded call: the meter returns a well-formed result without raising.

    Skipped when nothing is recorded (always in CI). Recorded people are not labelled, so this
    checks plumbing and quality gates, not the asymmetry value.
    """
    if call_dir is None:
        pytest.skip("no recorded calls with a smile step under recordings/")
    replay = pytest.importorskip("tools.replay")
    step = replay.load(str(call_dir))["smile"]
    mark = step.mark("smile")
    if mark is None:
        pytest.skip("recording has no smile mark")
    r = F.measure_face(list(step.frames(None, mark)), list(step.frames(mark, None)))
    print(call_dir.name, r.quality.value, r.asymmetry, r.weaker_side, r.detail.get("reason"))
    assert isinstance(r.quality, Quality)
    assert (r.asymmetry is None) == (r.quality is not Quality.OK)


def test_draw_on_letterboxed_rgba(mesh):
    neutral = _frames(mesh, lambda k: (0.0, 0.0), 6)
    smile = _frames(mesh, ramp(0.0, 0.15, delay=0), 6)
    with F.FaceMeter() as meter:
        for f in neutral:
            meter.add_neutral(f)
        for f in smile:
            meter.add_smile(f)
        out = np.zeros((720, 960, 4), np.uint8)
        out[..., 3] = 255
        meter.draw(out, live={"step": "smile"})
        assert out[..., :3].any()
        assert meter._live.get("lift_right") is not None
