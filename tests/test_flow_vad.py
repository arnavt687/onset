"""The sentence step with voice activity: early end on speech end, timer fallback, VAD off, pre-roll."""

from __future__ import annotations

import numpy as np
import pytest

from onset.flow import CheckFlow, Timing
from tests.test_flow import FPS, SENTENCE, StubArms, StubFace, arms, evaluate, face, speech
from tests.test_vad import noise, pcm, speechlike

RATE = 16000
CHUNK_S = 0.01  # the agent's AudioStream hands over 10 ms frames


class Caller:
    """Room noise everywhere, plus `speech_s` of speech-like audio starting `delay` seconds after
    "Say it now" (negative: before it, while the sentence is still being read out)."""

    def __init__(self, delay: float | None, speech_s: float = 2.0):
        self.delay, self.speech_s = delay, speech_s
        self.voice = speechlike(speech_s, RATE, dbfs=-22.0)
        self.room = noise(200.0, RATE, dbfs=-58.0, seed=7)
        self.window_start: float | None = None  # when "Say it now" will appear
        self.window: float | None = None  # when it did

    def chunk(self, t: float) -> bytes:
        i0, n = int(round(t * RATE)), int(round(CHUNK_S * RATE))
        x = self.room[i0:i0 + n].copy()
        if self.delay is not None and self.window_start is not None:
            s0 = int(round((self.window_start + self.delay) * RATE))
            j = np.arange(n) + i0 - s0
            m = (j >= 0) & (j < len(self.voice))
            x[m] += self.voice[j[m]]
        return pcm(x).tobytes()


def drive(tmp_path, caller: Caller, timing: Timing = Timing()):
    clips = []

    async def fake_speech(clip, expected):
        clips.append(clip)
        return speech()

    flow = CheckFlow(patient_id="demo", patient_name="Pat", sentence=SENTENCE, timing=timing,
                     face_meter_factory=lambda: StubFace(face()), arm_meter_factory=lambda: StubArms(arms()),
                     speech_check=fake_speech, baseline_dir=tmp_path / "b", caregiver_phone="",
                     caregiver_send=False)
    frame = np.zeros((36, 64, 3), np.uint8)
    t, audio_t = 0.0, 0.0
    flow.start(t)
    while t < 120 and not flow.done:
        if flow.state == "sentence" and caller.window_start is None:
            # the first tick at or after the lead is when "Say it now" appears
            lead_end = flow._step_t + timing.sentence_lead_s
            caller.window_start = np.ceil(round(lead_end * FPS, 6)) / FPS
        flow.on_frame(frame, t)
        while audio_t + CHUNK_S <= t + 1e-9:  # audio arrives continuously, 10 ms at a time
            flow.on_audio(caller.chunk(audio_t), RATE, audio_t)
            audio_t = round(audio_t + CHUNK_S, 6)
        flow.tick(t)
        if flow.state == "sentence" and flow._measure_t is not None and caller.window is None:
            caller.window = flow._measure_t  # when "Say it now" actually appeared (t0 is 0)
        if flow.needs_evaluation:
            evaluate(flow)
        t += 1 / FPS
    vad = [e for e in flow.events if e["type"] == "vad"]
    go = next(e for e in flow.events if e["type"] == "step" and e["caption"].startswith("Say it now"))
    return flow, vad, clips, go


def test_speech_end_ends_recording_early(tmp_path):
    caller = Caller(delay=0.4, speech_s=2.0)
    flow, vad, clips, _ = drive(tmp_path, caller)
    assert len(vad) == 1
    v = vad[0]
    assert v["enabled"] is True and v["reason"] == "speech_end"
    assert v["started_at"] == pytest.approx(0.4, abs=0.12)
    assert v["ended_at"] == pytest.approx(2.4, abs=0.15)
    assert v["speech_s"] == pytest.approx(2.0, abs=0.2)
    assert v["record_s"] == pytest.approx(2.4 + 0.9, abs=0.2) and v["record_s"] < Timing().sentence_record_s
    # the clip holds the whole attempt and starts at "Say it now" (no pre-roll needed)
    clip = clips[0]
    assert clip.t_start == pytest.approx(caller.window, abs=0.02)
    assert len(clip.pcm) / 2 / RATE == pytest.approx(v["record_s"], abs=0.12)
    names = [e.get("name", e["type"]) for e in flow.events if e["type"] in ("step", "vad")]
    assert names.index("vad") == names.index("deciding") - 1 and flow.done


def test_no_speech_falls_back_to_the_timer(tmp_path):
    flow, vad, clips, _ = drive(tmp_path, Caller(delay=None))
    v = vad[0]
    assert v["enabled"] is True and v["reason"] == "timer"
    assert v["started_at"] is None and v["ended_at"] is None
    assert v["record_s"] == pytest.approx(Timing().sentence_record_s, abs=0.1)
    assert clips and len(clips[0].pcm) > 0


def test_speech_longer_than_the_window_is_capped_by_the_timer(tmp_path):
    t = Timing(sentence_record_s=4.0)
    flow, vad, _, _ = drive(tmp_path, Caller(delay=0.5, speech_s=6.0), timing=t)
    v = vad[0]
    assert v["reason"] == "timer" and v["record_s"] == pytest.approx(4.0, abs=0.1)
    assert v["started_at"] == pytest.approx(0.5, abs=0.12) and v["ended_at"] is None


def test_vad_off_uses_the_timer(tmp_path, monkeypatch):
    monkeypatch.setenv("ONSET_SPEECH_VAD", "0")
    flow, vad, clips, _ = drive(tmp_path, Caller(delay=0.4, speech_s=2.0))
    v = vad[0]
    assert v["enabled"] is False and v["reason"] == "timer"
    assert v["record_s"] == pytest.approx(Timing().sentence_record_s, abs=0.1)
    assert flow._vad is None


def test_preroll_kept_when_they_start_before_say_it_now(tmp_path):
    caller = Caller(delay=-0.6, speech_s=2.5)
    flow, vad, clips, go = drive(tmp_path, caller)
    v = vad[0]
    assert v["reason"] == "speech_end"
    assert v["started_at"] == pytest.approx(-0.6, abs=0.12)
    clip = clips[0]
    window_rel = caller.window
    # the clip starts ~1.5 s before "Say it now", so the first words are in it
    assert clip.t_start == pytest.approx(window_rel - Timing().sentence_preroll_s, abs=0.1)
    assert len(clip.pcm) / 2 / RATE == pytest.approx(Timing().sentence_preroll_s + v["record_s"], abs=0.15)


def test_no_preroll_when_quiet_at_say_it_now(tmp_path):
    caller = Caller(delay=0.5, speech_s=1.5)
    flow, vad, clips, _ = drive(tmp_path, caller)
    window_rel = caller.window
    assert clips[0].t_start == pytest.approx(window_rel, abs=0.02)


def test_talk_during_the_lead_that_stops_at_say_it_now_does_not_end_the_step(tmp_path):
    """Repeating along with the spoken sentence, finishing 0.2 s into the window: keep listening."""
    flow, vad, _, _ = drive(tmp_path, Caller(delay=-3.0, speech_s=3.2))
    v = vad[0]
    assert v["reason"] == "timer"
    assert v["record_s"] == pytest.approx(Timing().sentence_record_s, abs=0.1)


def test_vad_failure_falls_back_to_the_timer(tmp_path, monkeypatch):
    import onset.vad as V

    def boom(self, *a, **k):
        raise RuntimeError("bad audio")

    monkeypatch.setattr(V.VoiceActivity, "feed", boom)
    flow, vad, clips, _ = drive(tmp_path, Caller(delay=0.4))
    v = vad[0]
    assert v["enabled"] is False and v["reason"] == "timer" and clips
