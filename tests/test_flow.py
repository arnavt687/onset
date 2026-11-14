"""CheckFlow end to end with stub meters, a fake clock and synthetic frames/audio."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import numpy as np
import pytest

from onset import overlay, script
from onset.flow import CheckFlow, Timing
from onset.types import ArmResult, Baseline, FaceResult, Quality, SpeechResult

FPS = 15
BANNED = re.compile(r"\b(fine|normal|healthy|stroke)\b", re.I)
SENTENCE = "The sky is blue in Cincinnati."


class StubFace:
    def __init__(self, result: FaceResult):
        self._result, self.neutral, self.smile, self.closed = result, 0, 0, False
        self._live = {"phase": "neutral", "face": True}

    def add_neutral(self, f):
        self.neutral += 1

    def add_smile(self, f):
        self.smile += 1

    def result(self):
        return self._result

    def draw(self, rgba, live=None):
        rgba[:4, :4] = (255, 0, 0, 255)

    def close(self):
        self.closed = True


class StubArms:
    """arms_up() becomes true after `up_after` seconds of frames (None = never)."""

    def __init__(self, result: ArmResult, up_after: float | None = 1.0):
        self._result, self.up_after, self.t = result, up_after, 0.0

    def add(self, f):
        self.t = f.t

    def arms_up(self):
        return self.up_after is not None and self.t >= self.up_after

    def hold_start(self):
        return self.up_after if self.arms_up() else None

    def live(self):
        return {"arms_up": self.arms_up(), "hold_elapsed": 0.0, "left_drift": 0.01}

    def result(self):
        return self._result

    def draw(self, rgba, live=None):
        pass

    def close(self):
        pass


def evaluate(flow):
    async def go():
        await flow.evaluate()
        await flow.wait_background()

    asyncio.run(go())


def face(q=Quality.OK, asym=0.05):
    return FaceResult(q, asym if q is Quality.OK else None, None, 40, {})


def arms(q=Quality.OK, diff=0.02):
    ok = q is Quality.OK
    return ArmResult(q, 0.01 if ok else None, 0.03 if ok else None, diff if ok else None, None, 10.0 if ok else 0.0, 150, {})


def speech(q=Quality.OK, wer=0.0, wpm=150.0, pause=0.3):
    ok = q is Quality.OK
    return SpeechResult(q, SENTENCE, SENTENCE if ok else None, wer if ok else None, wpm if ok else None, pause if ok else None, {})


def baseline():
    return Baseline("demo", "2026-11-14T00:00:00+00:00", 0.05, 0.02, 0.0, 150.0, SENTENCE, 1)


def run(tmp_path: Path, *, f=None, a=None, s=None, up_after=1.0, base=None, audio=True,
        timing=Timing(), until=200.0, send=None) -> CheckFlow:
    """Drive a whole flow on a fake clock; returns the finished flow."""
    from onset import decide

    bdir = tmp_path / "baselines"
    if base is not None:
        decide.save_baseline(base, base_dir=bdir)
    sent = []

    async def fake_send(phone, text, dry):
        sent.append((phone, text, dry))
        return {"status": "dry_run" if dry else "sent", "accepted": not dry, "detail": "test"}

    async def fake_speech(clip, expected):
        assert clip.sample_rate == 16000 and len(clip.pcm) > 0
        return s or speech()

    flow = CheckFlow(
        patient_id="demo", patient_name="Pat", sentence=SENTENCE, timing=timing,
        face_meter_factory=lambda: StubFace(f or face()),
        arm_meter_factory=lambda: StubArms(a or arms(), up_after),
        speech_check=fake_speech, send_message=send or fake_send, baseline_dir=bdir,
        caregiver_phone="", caregiver_send=False,
    )
    flow.sent = sent
    frame = np.zeros((72, 128, 3), np.uint8)
    chunk = (np.ones(320, np.int16) * 2000).tobytes()  # 20 ms at 16 kHz
    t = 0.0
    flow.start(t)
    while t < until and not flow.done:
        flow.on_frame(frame, t)
        if audio:
            for k in range(3):
                flow.on_audio(chunk, 16000, t + k * 0.02)
        flow.tick(t)
        if flow.needs_evaluation:
            evaluate(flow)
        t += 1 / FPS
    flow.end_t = t
    return flow


def steps(flow):
    return [e["name"] for e in flow.events if e["type"] == "step"]


def texts(flow):
    out = []
    for e in flow.events:
        for k in ("caption", "text"):
            if isinstance(e.get(k), str):
                out.append(e[k])
        out += [r for r in e.get("reasons", [])]
    return out


def assert_safe(flow):
    for text in texts(flow):
        assert not BANNED.search(text), text


def test_first_run_saves_baseline(tmp_path):
    flow = run(tmp_path)
    assert flow.done and flow.baseline_saved
    assert steps(flow) == ["greet", "face_neutral", "face_smile", "arms_wait", "arms_hold",
                           "sentence", "sentence", "deciding", "baseline_saved", "closing"]
    assert flow.outcome_line == script.CLOSE_BASELINE_SAVED
    assert (tmp_path / "baselines").exists()
    assert not any(e["type"] == "decision" for e in flow.events)
    assert [e["check"] for e in flow.events if e["type"] == "result"] == ["face", "arms", "speech"]
    assert flow.events[0]["type"] == "run_started" and flow.events[-1]["type"] == "run_ended"
    assert any(e["type"] == "live" for e in flow.events)
    assert_safe(flow)


def test_first_run_with_bad_step_does_not_save(tmp_path):
    flow = run(tmp_path, f=face(Quality.TOO_DARK))
    assert not flow.baseline_saved
    assert flow.outcome_line == script.CLOSE_BASELINE_FAILED
    assert not (tmp_path / "baselines" / "demo.json").exists()
    assert_safe(flow)


def test_no_change(tmp_path):
    flow = run(tmp_path, base=baseline())
    d = [e for e in flow.events if e["type"] == "decision"]
    assert d and d[0]["outcome"].value == "no_change_detected"
    assert flow.outcome_line == script.CLOSE_NO_CHANGE
    assert not flow.sent and not any(e["type"] == "caregiver" for e in flow.events)
    assert_safe(flow)


@pytest.mark.parametrize("kw", [
    {"f": face(asym=0.6)},
    {"a": arms(diff=0.5)},
    {"s": speech(wer=0.8)},
])
def test_escalate_texts_caregiver_dry_run(tmp_path, kw):
    flow = run(tmp_path, base=baseline(), **kw)
    d = [e for e in flow.events if e["type"] == "decision"][0]
    assert d["outcome"].value == "escalate" and d["reasons"]
    assert flow.outcome_line == script.CLOSE_ESCALATE
    cg = [e for e in flow.events if e["type"] == "caregiver"]
    assert cg and cg[0]["status"] == "dry_run" and cg[0]["text"]
    assert flow.sent and flow.sent[0][2] is True  # dry run
    assert "911" in flow.outcome_line.caption
    assert_safe(flow)


@pytest.mark.parametrize("q", [Quality.NO_SUBJECT, Quality.PARTIAL, Quality.TOO_DARK, Quality.MOTION,
                               Quality.NO_RESPONSE, Quality.ERROR])
@pytest.mark.parametrize("which", ["face", "arms", "speech"])
def test_any_quality_failure_is_unclear(tmp_path, q, which):
    kw = {"f": face(q)} if which == "face" else {"a": arms(q)} if which == "arms" else {"s": speech(q)}
    flow = run(tmp_path, base=baseline(), **kw)
    d = [e for e in flow.events if e["type"] == "decision"][0]
    assert d["outcome"].value in ("unclear", "escalate")
    assert flow.outcome_line in (script.CLOSE_UNCLEAR, script.CLOSE_ESCALATE)
    assert any(e["type"] == "caregiver" for e in flow.events)
    assert_safe(flow)


def test_arms_never_raised_times_out(tmp_path):
    t = Timing(arms_wait_timeout_s=4.0)
    flow = run(tmp_path, base=baseline(), up_after=None, a=arms(Quality.NO_RESPONSE), timing=t)
    s = steps(flow)
    assert "arms_wait" in s and "arms_hold" not in s and "sentence" in s
    d = [e for e in flow.events if e["type"] == "decision"][0]
    assert d["outcome"].value == "unclear"
    assert any(e["type"] == "say" and e["text"] == script.ARMS_TIMEOUT.say for e in flow.events)


def test_time_cap(tmp_path):
    t = Timing(cap_s=12.0, arms_wait_timeout_s=60.0)
    flow = run(tmp_path, base=baseline(), up_after=None, timing=t)
    assert flow.end_t < 15.0
    r = {e["check"]: e["quality"] for e in flow.events if e["type"] == "result"}
    assert r["arms"] is Quality.NO_RESPONSE and r["speech"] is Quality.NO_RESPONSE
    d = [e for e in flow.events if e["type"] == "decision"][0]
    assert d["outcome"].value == "unclear"


def test_no_caller_audio_is_unclear(tmp_path):
    flow = run(tmp_path, base=baseline(), audio=False)
    r = {e["check"]: e["quality"] for e in flow.events if e["type"] == "result"}
    assert r["speech"] is Quality.NO_RESPONSE
    assert flow.outcome_line == script.CLOSE_UNCLEAR


def test_meter_that_fails_to_start_is_error_not_crash(tmp_path):
    from onset import decide

    decide.save_baseline(baseline(), base_dir=tmp_path / "b")

    def boom():
        raise FileNotFoundError("no model")

    async def ok(clip, expected):
        return speech()

    flow = CheckFlow(sentence=SENTENCE, face_meter_factory=boom, arm_meter_factory=boom,
                     speech_check=ok, baseline_dir=tmp_path / "b", caregiver_phone="", caregiver_send=False,
                     timing=Timing(arms_wait_timeout_s=2.0))
    t = 0.0
    flow.start(t)
    chunk = (np.ones(320, np.int16) * 2000).tobytes()
    while not flow.done and t < 120:
        flow.on_frame(np.zeros((36, 64, 3), np.uint8), t)
        flow.on_audio(chunk, 16000, t)
        flow.tick(t)
        if flow.needs_evaluation:
            evaluate(flow)
        t += 1 / FPS
    r = {e["check"]: e["quality"] for e in flow.events if e["type"] == "result"}
    assert r["face"] is Quality.ERROR and r["arms"] is Quality.ERROR
    assert flow.outcome_line == script.CLOSE_UNCLEAR


def test_speech_check_exception_is_unclear(tmp_path):
    async def broken(clip, expected):
        raise RuntimeError("network down")

    from onset import decide

    decide.save_baseline(baseline(), base_dir=tmp_path / "b")
    flow = CheckFlow(sentence=SENTENCE, face_meter_factory=lambda: StubFace(face()),
                     arm_meter_factory=lambda: StubArms(arms()), speech_check=broken,
                     baseline_dir=tmp_path / "b", caregiver_phone="", caregiver_send=False)
    t = 0.0
    flow.start(t)
    while not flow.done and t < 120:
        flow.on_frame(np.zeros((36, 64, 3), np.uint8), t)
        flow.on_audio((np.ones(320, np.int16) * 2000).tobytes(), 16000, t)
        flow.tick(t)
        if flow.needs_evaluation:
            evaluate(flow)
        t += 1 / FPS
    assert flow.outcome_line == script.CLOSE_UNCLEAR


def test_events_written_to_sink(tmp_path):
    from onset.events import EventSink

    sink = EventSink("t1", "replay", root=tmp_path / "runs")
    from onset import decide

    decide.save_baseline(baseline(), base_dir=tmp_path / "b")
    flow = CheckFlow(sink=sink, sentence=SENTENCE, face_meter_factory=lambda: StubFace(face(asym=0.9)),
                     arm_meter_factory=lambda: StubArms(arms()), speech_check=lambda c, e: _coro(speech()),
                     baseline_dir=tmp_path / "b", caregiver_phone="", caregiver_send=False,
                     send_message=lambda p, t, d: _coro({"status": "dry_run"}))
    t = 0.0
    flow.start(t)
    img = np.zeros((720, 1280, 3), np.uint8)
    while not flow.done and t < 120:
        flow.on_frame(img, t)
        flow.on_audio((np.ones(320, np.int16) * 2000).tobytes(), 16000, t)
        flow.tick(t)
        if flow.needs_evaluation:
            evaluate(flow)
        flow.render(img, t)
        t += 1 / FPS
    sink.close("test")
    lines = [json.loads(x) for x in (tmp_path / "runs" / "t1" / "events.jsonl").read_text().splitlines()]
    types = [x["type"] for x in lines]
    assert types[0] == "run_started" and "decision" in types and "caregiver" in types
    dec = next(x for x in lines if x["type"] == "decision")
    assert dec["outcome"] == "escalate"
    res = next(x for x in lines if x["type"] == "result" and x["check"] == "face")
    assert res["quality"] == "ok" and res["data"]["asymmetry"] == 0.9
    assert (tmp_path / "runs" / "t1" / "frame.jpg").exists()


async def _coro(value):
    return value


def test_render_shapes_and_caption_size(tmp_path):
    flow = run(tmp_path, until=8.0)  # stops mid-flow (in face steps)
    out = flow.render(np.full((720, 1280, 3), 90, np.uint8), 7.0)
    assert out.shape == (360, 640, 4) and out.dtype == np.uint8
    out2 = flow.render(None, 7.0)
    assert out2.shape == (360, 640, 4)
    for line in script.ALL_LINES + [script.sentence_prompt(SENTENCE), script.sentence_go(SENTENCE)]:
        _, info = overlay.caption_layer(line.caption, 640, 360, "bottom")
        assert info["cap"] >= overlay.MIN_CAP_PX, (line.caption, info)
        assert len(info["lines"]) <= overlay.MAX_LINES
        assert not info["lines"][-1].endswith("…"), f"caption too long for 3 lines: {line.caption!r}"


def test_script_never_says_banned_words():
    for line in script.ALL_LINES + [script.sentence_prompt(SENTENCE), script.sentence_go(SENTENCE)]:
        assert not BANNED.search(line.say) and not BANNED.search(line.caption), line
    for line in (script.CLOSE_ESCALATE, script.CLOSE_UNCLEAR):
        assert "911" in line.caption and "9 1 1" in line.say
    for line in (script.CLOSE_NO_CHANGE, script.CLOSE_BASELINE_SAVED, script.CLOSE_BASELINE_FAILED):
        assert "911" in line.caption
