"""tools/fake_call.py --assert: the pass/fail logic, offline (no LiveKit, no keys, no env)."""

from __future__ import annotations

import copy
import importlib
import sys

import pytest

SENTENCE = "You can't teach an old dog new tricks."


def fake_call():
    return importlib.import_module("tools.fake_call")


def good_events(transcript: str = "You cannot teach an old dog new tricks.") -> list[dict]:
    return [
        {"t": 0.0, "type": "run_started", "sentence": SENTENCE, "has_baseline": False},
        {"t": 0.0, "type": "step", "name": "greet", "caption": "Hi"},
        {"t": 31.0, "type": "step", "name": "sentence", "caption": f"Say it now: “{SENTENCE}”"},
        {"t": 34.4, "type": "vad", "enabled": True, "reason": "speech_end", "started_at": 0.5,
         "ended_at": 2.5, "speech_s": 2.0, "record_s": 3.4, "floor_db": -120.0},
        {"t": 34.4, "type": "step", "name": "deciding", "caption": "Thank you."},
        {"t": 35.5, "type": "result", "check": "speech", "quality": "ok",
         "data": {"quality": "ok", "expected": SENTENCE, "transcript": transcript,
                  "word_error_rate": 0.0, "detail": {}}},
        {"t": 36.0, "type": "run_ended", "reason": "complete"},
    ]


def good_audio() -> dict:
    return {"agent_joined": 1.0, "ready_at": 5.0, "first_audible": 6.2, "audible_s": 40.0, "peak": -12.0}


def test_import_needs_no_env(monkeypatch):
    for k in ("AGENT_NAME", "LIVEKIT_URL", "LIVEKIT_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    sys.modules.pop("tools.fake_call", None)
    fc = fake_call()
    assert callable(fc.check_run)
    assert fc.parse_args(["--assert"]).assert_ is True


def test_good_run_passes():
    fc = fake_call()
    assert fc.check_run(good_events(), good_audio(), sentence=SENTENCE) == []
    out = fc.summary(good_events(), good_audio(), [], sentence=SENTENCE)
    assert out.startswith("PASS") and "speech_end" in out and "1.20s after bridge_ready" in out


def test_expected_falls_back_to_the_event():
    fc = fake_call()
    assert fc.check_run(good_events(), good_audio()) == []


@pytest.mark.parametrize("audio, needle", [
    ({"first_audible": None}, "no audible agent audio"),
    ({"first_audible": 15.5}, "10.50s after bridge_ready"),
    ({"first_audible": 3.0}, "BEFORE bridge_ready"),
    ({"ready_at": None}, "bridge_ready was never set"),
    ({"agent_joined": None}, "agent never joined"),
])
def test_agent_audio_failures(audio, needle):
    fc = fake_call()
    a = {**good_audio(), **audio}
    failures = fc.check_run(good_events(), a, sentence=SENTENCE)
    assert any(needle in f for f in failures), failures
    assert fc.summary(good_events(), a, failures).startswith("FAIL")


def test_greet_exactly_at_deadline_passes():
    fc = fake_call()
    assert fc.check_run(good_events(), {**good_audio(), "first_audible": 15.0}, sentence=SENTENCE) == []


def _speech(events, **changes):
    ev = copy.deepcopy(events)
    res = next(e for e in ev if e["type"] == "result")
    for k, v in changes.items():
        if k == "quality":
            res["quality"] = v
            res["data"]["quality"] = v
        else:
            res["data"][k] = v
    return ev


@pytest.mark.parametrize("changes, needle", [
    ({"quality": "no_response", "detail": {"reason": "silence"}}, "speech quality 'no_response' (silence)"),
    ({"quality": "partial", "detail": {"reason": "low word confidence"}}, "speech quality 'partial'"),
    ({"transcript": None}, "no transcript"),
    ({"transcript": "You can teach an old dog new tricks"}, "WER"),  # one word of 8 wrong: 0.125
    ({"word_error_rate": 0.4}, "WER 0.40"),  # the event's own WER counts too
])
def test_speech_failures(changes, needle):
    fc = fake_call()
    failures = fc.check_run(_speech(good_events(), **changes), good_audio(), sentence=SENTENCE)
    assert any(needle in f for f in failures), failures


def test_missing_speech_result_and_run_ended():
    fc = fake_call()
    ev = [e for e in good_events() if e["type"] not in ("result", "run_ended")]
    failures = fc.check_run(ev, good_audio(), sentence=SENTENCE)
    assert any("no speech result" in f for f in failures)
    assert any("no run_ended" in f for f in failures)


def test_small_wer_within_limit_passes():
    fc = fake_call()
    long = "the quick brown fox jumps over the lazy dog near the river bank"
    ev = good_events(transcript="the quick brown fox jumps over the lazy dog near a river bank")
    ev = _speech(ev, expected=long, word_error_rate=None)
    assert fc.check_run(ev, good_audio(), sentence=long) == []  # 1 of 13 words: 0.077


@pytest.mark.parametrize("vad, needle", [
    (None, "no vad event"),
    ({"enabled": False, "reason": "timer"}, "voice activity was off"),
    ({"enabled": True, "reason": "timer", "started_at": None, "record_s": 7.0}, "'timer', not speech_end"),
])
def test_vad_failures(vad, needle):
    fc = fake_call()
    ev = [e for e in good_events() if e["type"] != "vad"]
    if vad is not None:
        ev.insert(3, {"type": "vad", **vad})
    failures = fc.check_run(ev, good_audio(), sentence=SENTENCE)
    assert any(needle in f for f in failures), failures
    # with voice activity off in the agent, the timer is expected
    assert fc.check_run(ev, good_audio(), sentence=SENTENCE, expect_vad=False) == []


def test_read_events_tolerates_missing_and_bad_files(tmp_path):
    fc = fake_call()
    assert fc.read_events(tmp_path / "nope.jsonl") == []
    p = tmp_path / "e.jsonl"
    p.write_text('{"type": "run_started"}\n\n{"type": "run_ended"}\n')
    assert [e["type"] for e in fc.read_events(p)] == ["run_started", "run_ended"]
    assert fc.run_sentence([{"type": "run_started", "sentence": "Hi there."}]) == "Hi there."
