"""onset.vad: energy-based voice activity on synthetic audio (speech-like tones, noise, hum)."""

from __future__ import annotations

import numpy as np
import pytest

from onset.vad import VoiceActivity, band_levels


def speechlike(seconds: float, rate: int = 16000, dbfs: float = -20.0, seed: int = 0) -> np.ndarray:
    """A voiced, syllabic signal: 120-160 Hz harmonics with a ~4 Hz syllable envelope (float64)."""
    rng = np.random.default_rng(seed)
    n = int(round(seconds * rate))
    t = np.arange(n) / rate
    f0 = 140 + 20 * np.sin(2 * np.pi * 0.7 * t + rng.uniform(0, 6))
    phase = 2 * np.pi * np.cumsum(f0) / rate
    sig = sum(np.sin(k * phase) / k for k in range(1, 25) if 160 * k < rate / 2)
    env = 0.25 + 0.75 * np.abs(np.sin(np.pi * 4.0 * t))  # syllables, never fully silent
    sig = sig * env
    rms = np.sqrt(np.mean(sig**2)) or 1.0
    return sig / rms * 32768 * 10 ** (dbfs / 20)


def noise(seconds: float, rate: int = 16000, dbfs: float = -55.0, seed: int = 1) -> np.ndarray:
    n = int(round(seconds * rate))
    return np.random.default_rng(seed).normal(0, 32768 * 10 ** (dbfs / 20), n)


def hum(seconds: float, rate: int = 16000, amplitude: float = 0.3, freq: float = 60.0) -> np.ndarray:
    t = np.arange(int(round(seconds * rate))) / rate
    return 32767 * amplitude * (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * 2 * freq * t)
                                + 0.1 * np.sin(2 * np.pi * 3 * freq * t)) / 1.4


def pcm(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x), -32768, 32767).astype("<i2")


def run(samples: np.ndarray, rate: int = 16000, chunk: int = 160, t0: float = 0.0, vad=None, until=None):
    """Feed in chunks; returns the VAD and the first clock time `ended` became true (or None)."""
    vad = vad or VoiceActivity()
    s = pcm(samples)
    ended_seen = None
    for i in range(0, len(s), chunk):
        t = t0 + i / rate
        vad.feed(s[i:i + chunk].tobytes(), rate, t)
        if vad.ended and ended_seen is None:
            ended_seen = t + len(s[i:i + chunk]) / rate
    return vad, ended_seen


def test_speech_in_noise_start_and_end():
    x = np.concatenate([noise(1.5), noise(2.0, seed=2) + speechlike(2.0), noise(2.5, seed=3)])
    vad, ended_seen = run(x)
    assert vad.ended
    assert vad.started_at == pytest.approx(1.5, abs=0.1)
    assert vad.ended_at == pytest.approx(3.5, abs=0.15)
    assert vad.speech_s == pytest.approx(2.0, abs=0.2)
    # ended is reported ~0.9 s after the last loud frame, not before
    assert ended_seen == pytest.approx(3.5 + 0.9, abs=0.15)
    assert vad.floor_db < -50


def test_speech_not_ended_while_talking_or_without_trailing_silence():
    x = np.concatenate([noise(1.0), noise(3.0, seed=2) + speechlike(3.0)])
    vad, _ = run(x)
    assert vad.in_speech and vad.active and not vad.ended
    x = np.concatenate([noise(1.0), speechlike(2.0) + noise(2.0, seed=4), noise(0.5, seed=5)])
    vad, _ = run(x)  # only 0.5 s of silence after speech
    assert vad.in_speech and not vad.ended


@pytest.mark.parametrize("seconds", [5.0])
def test_digital_silence_is_no_speech(seconds):
    vad, _ = run(np.zeros(int(seconds * 16000)))
    assert not vad.active and not vad.ended and vad.started_at is None
    assert vad.floor_db == -120.0 and vad.level_db == -120.0
    assert band_levels(np.zeros(320), 16000, 320)[0] == -120.0


def test_near_silence_and_quiet_room_are_no_speech():
    rng = np.random.default_rng(0)
    dither = rng.integers(-1, 2, 16000 * 4).astype(np.float64)  # +-1 LSB, about -95 dBFS
    for x in (dither, noise(4.0, dbfs=-62.0)):
        vad, _ = run(x)
        assert not vad.active and vad.started_at is None


def test_constant_hum_is_no_speech_but_speech_over_hum_is():
    vad, _ = run(hum(5.0, amplitude=0.5))  # loud 60 Hz mains hum with harmonics
    assert not vad.active and vad.started_at is None
    vad, _ = run(hum(5.0, amplitude=0.3, freq=50.0) + noise(5.0, dbfs=-60))
    assert not vad.active
    h = hum(6.0, amplitude=0.3)
    x = h + np.concatenate([np.zeros(16000), speechlike(2.0, dbfs=-25), np.zeros(3 * 16000)])
    vad, _ = run(x)
    assert vad.ended and vad.started_at == pytest.approx(1.0, abs=0.1)


def test_constant_in_band_drone_becomes_the_floor():
    t = np.arange(16000 * 6) / 16000
    drone = 32767 * 0.05 * np.sin(2 * np.pi * 500 * t)  # a constant 500 Hz whine
    vad, _ = run(drone)
    # the first frame sets the floor; a constant level never rises above it
    assert not vad.active and vad.started_at is None


def test_dc_offset_is_no_speech():
    vad, _ = run(np.full(16000 * 3, 2000.0))
    assert not vad.active


def test_short_blip_is_not_speech_end_then_real_speech_is():
    x = np.concatenate([noise(1.0), noise(0.3, seed=2) + speechlike(0.3), noise(1.5, seed=3)])
    vad, _ = run(x)
    assert not vad.ended and vad.state == "idle"  # a cough: dropped
    x = np.concatenate([x, noise(1.5, seed=4) + speechlike(1.5, seed=5), noise(1.2, seed=6)])
    vad, _ = run(x)
    assert vad.ended and vad.started_at == pytest.approx(2.8, abs=0.1)


def test_tunable_end_silence():
    x = np.concatenate([noise(1.0), noise(1.5, seed=2) + speechlike(1.5), noise(0.6, seed=3)])
    vad, _ = run(x, vad=VoiceActivity(end_silence_s=0.5))
    assert vad.ended and vad.ended_at == pytest.approx(2.5, abs=0.15)
    vad, _ = run(x)  # default 0.9 s: not yet
    assert not vad.ended


def test_any_rate_and_odd_chunk_sizes():
    for rate in (8000, 16000, 24000, 48000):
        x = np.concatenate([noise(1.0, rate), noise(2.0, rate, seed=2) + speechlike(2.0, rate), noise(1.5, rate, seed=3)])
        vad, _ = run(x, rate=rate, chunk=441)
        assert vad.ended, rate
        assert vad.started_at == pytest.approx(1.0, abs=0.1), rate
        assert vad.ended_at == pytest.approx(3.0, abs=0.15), rate


def test_chunks_are_placed_at_their_own_time():
    """A sender whose clock runs ahead of the samples (gaps between chunks) still times correctly."""
    s = pcm(np.concatenate([noise(1.0), speechlike(2.0) + noise(2.0, seed=2), noise(2.0, seed=3)]))
    vad, t, chunk = VoiceActivity(), 100.0, 320
    for i in range(0, len(s), chunk):
        vad.feed(s[i:i + chunk].tobytes(), 16000, t)
        t += 0.022  # 20 ms of audio every 22 ms
    assert vad.ended
    assert vad.started_at == pytest.approx(100.0 + 1.0 * 1.1, abs=0.12)


def test_talking_from_the_first_frame_then_reset_keeps_floor():
    """The first frame sets the floor (never a false start in a noisy room); talk that was already
    going on is not trusted, and the floor learns the room in the next pause."""
    x = np.concatenate([speechlike(1.0) + noise(1.0, seed=2), noise(0.4, seed=3),
                        speechlike(1.5, seed=4) + noise(1.5, seed=5), noise(1.2, seed=6)])
    vad, _ = run(x)
    assert vad.ended and vad.started_at == pytest.approx(1.4, abs=0.1)
    assert vad.ended_at == pytest.approx(2.9, abs=0.15)
    floor = vad.floor_db
    vad.reset()
    assert vad.state == "idle" and vad.started_at is None and vad.floor_db == floor


def test_summary_is_plain():
    vad, _ = run(np.concatenate([noise(1.0), speechlike(1.0) + noise(1.0, seed=2), noise(1.0, seed=3)]))
    s = vad.summary()
    assert s["state"] == "ended" and isinstance(s["speech_s"], float) and isinstance(s["floor_db"], float)
