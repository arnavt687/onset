"""Piece 7: record -> replay round trips on synthetic data, and the recorder's safety rules."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time

import numpy as np
import pytest

from tests.conftest import frame_number
from tools import record, replay
from tools.record import Recorder

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")


@pytest.fixture
def call_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ONSET_RECORD_DIR", str(tmp_path / "recordings"))
    monkeypatch.delenv("ONSET_RECORD", raising=False)
    monkeypatch.delenv("ONSET_RECORD_FORMAT", raising=False)
    return tmp_path / "recordings" / "call-1"


def record_step(call_dir, step, frames=(), audio=None, chunk_ms=20, **kw):
    rec = Recorder(call_dir, step, enabled=True, **kw)
    for f in frames:
        assert rec.add_frame(f.rgb, f.t)
    if audio is not None:
        n = audio.sample_rate * chunk_ms // 1000 * 2  # bytes per chunk
        for k, off in enumerate(range(0, len(audio.pcm), n)):
            assert rec.add_audio(audio.pcm[off:off + n], audio.sample_rate, audio.t_start + k * chunk_ms / 1000)
    return rec


# --------------------------------------------------------------------------- round trips


@pytest.mark.parametrize("fmt", ["mp4", "jpg"])
def test_frames_round_trip(call_dir, synthetic_frames, fmt):
    rec = record_step(call_dir, "arms", synthetic_frames, fmt=fmt)
    rec.close()
    assert rec.error is None and rec.frames_dropped == 0

    step = replay.load(call_dir)["arms"]
    out = list(step.frames())
    assert len(out) == len(synthetic_frames)
    for i, (a, b) in enumerate(zip(synthetic_frames, out)):
        assert b.t == a.t  # exact timestamps from the sidecar
        assert b.rgb.shape == (720, 1280, 3) and b.rgb.dtype == np.uint8
        assert frame_number(b.rgb) == i  # same order, nothing lost or duplicated
        err = np.abs(b.rgb.astype(int) - a.rgb.astype(int)).mean()
        assert err < 6, f"frame {i}: mean error {err:.1f} after {fmt} encoding"


def test_audio_round_trip_is_exact(call_dir, make_tone):
    clip = make_tone(seconds=1.5, freq=300, sample_rate=16000, t_start=12.25)
    rec = record_step(call_dir, "sentence", audio=clip, meta={"expected": "It is a sunny day today."})
    rec.close()

    step = replay.load(call_dir)["sentence"]
    got = step.audio()
    assert got is not None
    assert got.pcm == clip.pcm
    assert got.sample_rate == 16000 and got.t_start == 12.25
    assert step.meta == {"expected": "It is a sunny day today."}
    assert step.audio_seconds == pytest.approx(1.5)
    assert step.frame_count == 0 and list(step.frames()) == []


def test_silence_and_numpy_audio(call_dir, silence_clip):
    rec = Recorder(call_dir, "sentence", enabled=True)
    rec.add_audio(np.frombuffer(silence_clip.pcm, dtype="<i2"), 16000, 0.0)
    rec.close()
    assert replay.load(call_dir)["sentence"].audio().pcm == silence_clip.pcm


def test_full_call_with_marks_and_rgba(call_dir, make_frames, tone_clip):
    frames = make_frames(n=20, width=640, height=360)
    rec = record_step(call_dir, "smile", frames[:5])
    rec.mark("smile", frames[5].t)
    for f in frames[5:]:  # RGBA, as agent.py holds caller frames
        rgba = np.dstack([f.rgb, np.full(f.rgb.shape[:2], 255, np.uint8)])
        rec.add_frame(rgba, f.t)
    rec.close()
    record_step(call_dir, "arms", make_frames(n=10)).close()
    record_step(call_dir, "sentence", audio=tone_clip, meta={"expected": "x"}).close()

    r = replay.load(call_dir)
    assert sorted(s.name for s in r) == ["arms", "sentence", "smile"]
    smile = r["smile"]
    assert smile.mark("smile") == frames[5].t
    before = list(smile.frames(None, smile.mark("smile")))
    after = list(smile.frames(smile.mark("smile")))
    assert [frame_number(f.rgb) for f in before] == list(range(5))
    assert [frame_number(f.rgb) for f in after] == list(range(5, 20))
    assert all(f.rgb.shape == (360, 640, 3) for f in after)


def test_frame_size_change_starts_new_segment(call_dir, make_frames):
    a, b = make_frames(n=4, width=1280, height=720), make_frames(n=3, width=720, height=1280)
    rec = Recorder(call_dir, "arms", enabled=True)
    for f in a:
        rec.add_frame(f.rgb, f.t)
    for f in b:
        rec.add_frame(f.rgb, 1.0 + f.t)
    rec.close()
    step = replay.Step(call_dir / "arms")
    assert [(s["width"], s["height"], s["frames"]) for s in step.index["segments"]] == [(1280, 720, 4), (720, 1280, 3)]
    shapes = [f.rgb.shape[:2] for f in step.frames()]
    assert shapes == [(720, 1280)] * 4 + [(1280, 720)] * 3


def test_meta_json(call_dir, synthetic_frames, tone_clip):
    rec = record_step(call_dir, "arms", synthetic_frames, audio=tone_clip, meta={"note": "synthetic"})
    rec.set_meta(sentence_index=2)
    rec.close()
    meta = json.loads((call_dir / "arms" / "meta.json").read_text())
    assert meta["step"] == "arms" and meta["version"] == record.FORMAT_VERSION
    assert meta["frames"]["written"] == 30 and meta["frames"]["dropped"] == 0
    assert meta["frames"]["t_first"] == 0.0 and meta["frames"]["t_last"] == pytest.approx(29 / 15)
    assert meta["audio"]["sample_rate"] == 16000 and meta["audio"]["samples"] == 16000
    assert meta["meta"] == {"note": "synthetic", "sentence_index": 2}
    assert meta["error"] is None


# --------------------------------------------------------------------------- safety rules


def test_off_unless_onset_record_is_1(call_dir, synthetic_frames, monkeypatch):
    for value in (None, "0", "true", ""):
        if value is None:
            monkeypatch.delenv("ONSET_RECORD", raising=False)
        else:
            monkeypatch.setenv("ONSET_RECORD", value)
        rec = Recorder.for_call("call-1", "arms")
        assert not rec.enabled
        assert rec.add_frame(synthetic_frames[0].rgb, 0.0) is False
        assert rec.add_audio(b"\0\0" * 160, 16000, 0.0) is False
        rec.close()
    assert not call_dir.parent.exists()  # nothing written, not even a directory

    monkeypatch.setenv("ONSET_RECORD", "1")
    rec = Recorder.for_call("call-1", "arms")
    assert rec.enabled and rec.dir == call_dir / "arms"
    rec.close()
    assert (call_dir / "arms" / "meta.json").exists()


def test_logs_when_recording_starts(call_dir, caplog):
    with caplog.at_level("WARNING", logger="onset.record"):
        Recorder(call_dir, "arms", enabled=True).close()
    assert any("RECORDING" in r.getMessage() and "arms" in r.getMessage() for r in caplog.records)


@posix_only
@pytest.mark.parametrize("fmt", ["mp4", "jpg"])
def test_owner_only_permissions(call_dir, make_frames, tone_clip, fmt):
    old = os.umask(0o022)  # a typical, permissive umask
    try:
        record_step(call_dir, "smile", make_frames(n=3), audio=tone_clip, fmt=fmt).close()
    finally:
        os.umask(old)
    root = call_dir.parent
    paths = [root, *root.rglob("*")]
    assert len(paths) > 5
    for p in paths:
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode & 0o077 == 0, f"{p} is readable by others: {oct(mode)}"


def test_gitignored():
    from pathlib import Path

    ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text().split()
    assert "recordings/" in ignore


def test_never_blocks_drops_frames_instead(call_dir, synthetic_frames, monkeypatch):
    gate = threading.Event()
    real = Recorder._write_frame

    def slow_write(self, img, t):
        gate.wait(10)  # a disk that has stalled
        real(self, img, t)

    monkeypatch.setattr(Recorder, "_write_frame", slow_write)
    rec = Recorder(call_dir, "arms", enabled=True, max_frames_queued=4)
    frame = synthetic_frames[0].rgb
    t0 = time.perf_counter()
    accepted = sum(rec.add_frame(frame, i / 15) for i in range(200))
    elapsed = time.perf_counter() - t0
    # 200 calls each copying a 1280x720 frame: well under a second when nothing blocks.
    assert elapsed < 2.0, f"add_frame blocked: {elapsed:.2f} s for 200 frames"
    assert accepted <= 6 and rec.frames_dropped == 200 - accepted
    gate.set()
    rec.close()
    meta = json.loads((call_dir / "arms" / "meta.json").read_text())
    assert meta["frames"]["written"] == accepted
    assert meta["frames"]["dropped"] == 200 - accepted
    assert replay.Step(call_dir / "arms").frame_count == accepted


def test_caller_may_reuse_its_buffer(call_dir, make_frames):
    frames = make_frames(n=5)
    buf = np.empty_like(frames[0].rgb)
    rec = Recorder(call_dir, "arms", enabled=True)
    for f in frames:
        buf[:] = f.rgb
        rec.add_frame(buf, f.t)
    rec.close()
    assert [frame_number(f.rgb) for f in replay.load(call_dir)["arms"].frames()] == list(range(5))


def test_bad_frames_are_refused_not_raised(call_dir, synthetic_frames):
    rec = Recorder(call_dir, "arms", enabled=True)
    assert rec.add_frame(np.zeros((10, 10), dtype=np.uint8), 0.0) is False
    assert rec.add_frame(None, 0.0) is False
    assert rec.add_frame(synthetic_frames[0].rgb, 0.1) is True
    rec.close()
    meta = json.loads((call_dir / "arms" / "meta.json").read_text())
    assert meta["frames"]["invalid"] == 2 and meta["frames"]["written"] == 1 and meta["error"] is None


def test_writer_error_does_not_raise_into_the_call(call_dir, synthetic_frames, monkeypatch):
    def disk_full(self, img, t):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Recorder, "_write_frame", disk_full)
    rec = Recorder(call_dir, "arms", enabled=True)
    for f in synthetic_frames:
        rec.add_frame(f.rgb, f.t)  # keeps accepting (and discarding) without raising
    rec.close()
    assert rec.error is not None and "No space left" in rec.error
    assert json.loads((call_dir / "arms" / "meta.json").read_text())["error"] == rec.error


def test_add_after_close_is_ignored(call_dir, synthetic_frames):
    rec = Recorder(call_dir, "arms", enabled=True)
    rec.close()
    assert rec.add_frame(synthetic_frames[0].rgb, 0.0) is False
    rec.close()  # twice is fine


# --------------------------------------------------------------------------- replay CLI


def test_replay_cli_tolerates_stub_meters(call_dir, make_frames, tone_clip, capsys):
    frames = make_frames(n=12, width=640, height=360)
    rec = record_step(call_dir, "smile", frames)
    rec.mark("smile", frames[6].t)
    rec.close()
    record_step(call_dir, "arms", frames).close()
    record_step(call_dir, "sentence", audio=tone_clip, meta={"expected": "It is a sunny day today."}).close()

    assert replay.main([str(call_dir), "--offline"]) == 0
    out = capsys.readouterr().out
    assert "[smile] 12 frames" in out and "[arms] 12 frames" in out and "[sentence] 0 frames" in out
    assert "check_sentence: skipped (--offline)" in out

    results = replay.run_meters(replay.load(call_dir), offline=True)
    assert set(results) == {"smile", "arms", "sentence"}
    for name in ("smile", "arms"):
        # Stubs say "not implemented yet"; once a meter lands it must still not crash the replay.
        assert results[name]["status"] in ("ok", replay.NOT_IMPLEMENTED, "error"), results[name]
        if results[name]["status"] == "error":
            assert "Traceback" not in results[name]["error"]


def test_replay_cli_json_and_single_step(call_dir, make_frames, capsys):
    record_step(call_dir, "arms", make_frames(n=3)).close()
    assert replay.main([str(call_dir / "arms"), "--json", "--offline"]) == 0
    results = json.loads(capsys.readouterr().out)
    assert list(results) == ["arms"] and results["arms"]["meter"] == "ArmMeter"


def test_replay_cli_bad_path(tmp_path, capsys):
    assert replay.main([str(tmp_path / "nope")]) == 2
    assert "cannot load" in capsys.readouterr().err


def test_replay_runs_as_a_script(call_dir, make_frames):
    import subprocess

    record_step(call_dir, "arms", make_frames(n=3)).close()
    p = subprocess.run([sys.executable, str(replay.REPO_ROOT / "tools" / "replay.py"), str(call_dir), "--offline"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "[arms] 3 frames" in p.stdout
