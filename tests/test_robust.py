"""Robustness on a real call: TTS fallbacks and the greeting deadline, hang-ups, a second call."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import numpy as np

from onset import events, script, voice
from onset.events import EventSink


# ---------------------------------------------------------------- voices: fallbacks and the chime


class FakeTTS:
    def __init__(self, name, *, fail=False, delay=0.0):
        self.name, self.fail, self.delay, self.calls = name, fail, delay, 0
        self.model = name

    def synthesize(self, text):
        outer = self

        class Stream:
            async def __aenter__(self):
                outer.calls += 1
                await asyncio.sleep(outer.delay)
                if outer.fail:
                    raise RuntimeError("down")
                return self

            async def __aexit__(self, *a):
                return False

            def __aiter__(self):
                async def gen():
                    class F:
                        sample_rate, num_channels = 24000, 1
                        data = (np.ones(2400, np.int16) * 1000).tobytes()

                    class Ev:
                        frame = F()

                    yield Ev()
                return gen()

        return Stream()


def test_speaker_falls_back_then_chimes(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE", tmp_path)
    played = []

    async def play(text, pcm, rate):
        played.append((text, len(pcm)))

    async def go(primary, fallbacks):
        sp = voice.Speaker(primary, play=play, fallbacks=fallbacks, timeout_s=0.2)
        sp.start()
        sp.say("Hello")
        await sp.aclose()

    down, slow, ok = FakeTTS("down", fail=True), FakeTTS("slow", delay=1.0), FakeTTS("ok")
    asyncio.run(go(down, [slow, ok]))
    assert played == [("Hello", 4800)] and down.calls == 1 and slow.calls == 1 and ok.calls == 1

    played.clear()
    asyncio.run(go(FakeTTS("down2", fail=True), [FakeTTS("down3", fail=True)]))
    assert played and played[0][1] == len(voice.chime())  # never silence


def test_first_line_has_a_total_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE", tmp_path)
    monkeypatch.setattr(voice, "FIRST_LINE_BUDGET_S", 0.3)
    played = []

    async def play(text, pcm, rate):
        played.append((text, len(pcm), asyncio.get_running_loop().time()))

    async def go():
        sp = voice.Speaker(FakeTTS("slow1", delay=1.0), play=play, fallbacks=[FakeTTS("slow2", delay=1.0)],
                           timeout_s=0.25)
        t0 = asyncio.get_running_loop().time()
        sp.start()
        sp.say("Hello")
        await sp.aclose()
        return t0

    t0 = asyncio.run(go())
    assert played[0][1] == len(voice.chime()) and played[0][2] - t0 < 0.6  # chime well within budget


def test_chime_is_short_and_audible():
    pcm = np.frombuffer(voice.chime(), np.int16)
    assert 0.5 < pcm.size / voice.RATE < 1.0 and np.abs(pcm).max() > 1000


# ---------------------------------------------------------------- hang-ups


def _flow(tmp_path, **kw):
    from tests.test_flow import baseline, face

    from onset import decide
    from onset.flow import CheckFlow
    from tests.test_flow import StubArms, StubFace, arms, speech

    bdir = tmp_path / "b"
    decide.save_baseline(baseline(), base_dir=bdir)
    sent = []

    async def send(p, t, d):
        sent.append((p, t, d))
        return {"status": "dry_run"}

    async def sp(clip, expected):
        return speech()

    flow = CheckFlow(patient_id="demo", patient_name="Pat", baseline_dir=bdir, caregiver_phone="",
                     caregiver_send=False, face_meter_factory=lambda: StubFace(face()),
                     arm_meter_factory=lambda: StubArms(arms(), 0.5), speech_check=sp, send_message=send, **kw)
    flow.sent = sent
    return flow


def _drive(flow, until):
    t = 0.0
    frame = np.zeros((72, 128, 3), np.uint8)
    flow.start(t)
    while t < until:
        flow.on_frame(frame, t)
        flow.tick(t)
        t += 1 / 15
    return t


def test_hangup_mid_check_still_decides_and_tells_the_caregiver(tmp_path):
    flow = _flow(tmp_path)
    t = _drive(flow, 8.0)  # in the face steps
    assert flow.state in flow.MEASURING
    assert flow.hangup(t) is True and flow.needs_evaluation and flow.caller_gone
    asyncio.run(_eval_all(flow))
    types = [e["type"] for e in flow.events]
    assert "hangup" in types
    d = [e for e in flow.events if e["type"] == "decision"][0]
    assert d["outcome"].value in ("unclear", "escalate")
    assert d["reasons"]  # the unfinished steps are listed as not completed
    assert flow.sent and flow.sent[0][2] is True  # a dry run in tests
    assert flow.outcome_line in (script.CLOSE_UNCLEAR, script.CLOSE_ESCALATE)


async def _eval_all(flow):
    await flow.evaluate()
    await flow.wait_background(5)


def test_hangup_during_greeting_or_after_close_sends_nothing(tmp_path):
    flow = _flow(tmp_path)
    _drive(flow, 1.0)
    assert flow.state == "greet" and flow.hangup(1.0) is False and not flow.needs_evaluation
    flow.abort("caller left")
    assert not flow.sent and [e for e in flow.events if e["type"] == "run_ended"][0]["reason"] == "caller left"


def test_hangup_notify_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ONSET_NOTIFY_ON_HANGUP", "0")
    flow = _flow(tmp_path)
    t = _drive(flow, 8.0)
    assert flow.hangup(t) is False and not flow.needs_evaluation


def test_hangup_while_deciding_keeps_the_pending_decision(tmp_path):
    flow = _flow(tmp_path)
    flow.needs_evaluation = True
    flow.state = "deciding"
    assert flow.hangup(50.0) is True  # agent.py then awaits evaluate()


# ---------------------------------------------------------------- a second call while one runs


def test_second_call_does_not_clobber_the_live_run(tmp_path):
    # Another process holds runs/live (a call in progress).
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "live").mkdir()
        (tmp_path / "live" / "events.jsonl").write_text('{"type": "run_started"}\n')
        (tmp_path / "live.lock").write_text(json.dumps({"pid": holder.pid, "run_id": "first", "at": __import__("time").time()}))
        s2 = EventSink("second", "call", root=tmp_path)
        assert not s2.is_live and s2.live == tmp_path / "second"
        s2.emit("run_started", run_id="second")
        s2.private_file("caregiver_link.txt", "secret")
        s2.close("done")
        assert (tmp_path / "live" / "events.jsonl").read_text() == '{"type": "run_started"}\n'  # untouched
        assert "second" in (tmp_path / "second" / "events.jsonl").read_text()
        assert not (tmp_path / "second" / "caregiver_link.txt").exists()
    finally:
        holder.kill()
        holder.wait()


def test_stale_or_own_lock_is_taken_over(tmp_path):
    (tmp_path / "live.lock").write_text(json.dumps({"pid": 999999, "run_id": "dead", "at": 0}))
    s = EventSink("a", "call", root=tmp_path)
    assert s.is_live
    s2 = EventSink("b", "call", root=tmp_path)  # same process (an unclosed earlier run): taken over
    assert s2.is_live
    s2.close()
    assert not (tmp_path / "live.lock").exists()
    s.close()  # releasing someone else's lock is a no-op


def test_live_lock_released_after_close_and_archive_has_no_link(tmp_path):
    s = EventSink("r1", "call", root=tmp_path)
    s.private_file("caregiver_link.txt", "secret")
    assert oct(os.stat(tmp_path / "live" / "caregiver_link.txt").st_mode & 0o777) == "0o600"
    s.close()
    assert not (tmp_path / "live.lock").exists()
    assert (tmp_path / "r1" / "events.jsonl").exists() and not (tmp_path / "r1" / "caregiver_link.txt").exists()
    assert events.take_live(tmp_path, "next") is True


# ---------------------------------------------------------------- steps wait for their spoken line


class PacedSay:
    """A say function that reports when a line has 'played', after `secs` of flow time."""

    def __init__(self, secs):
        self.secs, self.pending, self.lines = secs, [], []

    def __call__(self, text, on_done=None):
        self.lines.append(text)
        if on_done:
            self.pending.append((self.now + self.secs, on_done))

    now = 0.0

    def advance(self, t):
        self.now = t
        for due, cb in list(self.pending):
            if due <= t:
                self.pending.remove((due, cb))
                cb()


def _paced(tmp_path, secs, **timing):
    from onset.flow import Timing

    say = PacedSay(secs)
    flow = _flow(tmp_path, say=say, timing=Timing(**timing))
    return flow, say


def _steps(flow):
    return [(e["name"]) for e in flow.events if e["type"] == "step"]


def test_step_waits_for_its_line(tmp_path):
    flow, say = _paced(tmp_path, 8.0)  # the greeting takes 8 s to say; greet_s is 5
    t = 0.0
    flow.start(t)
    while t < 12.0:
        say.advance(t)
        flow.tick(t)
        t += 0.1
    # greet ends 5 s after its line finished (8 s), not at 5 s
    assert _steps(flow)[-1] == "greet"
    while t < 13.5:
        say.advance(t)
        flow.tick(t)
        t += 0.1
    assert "face_neutral" in _steps(flow)
    assert flow._hold_total > 7.0


def test_hold_is_capped_and_not_counted_in_the_time_limit(tmp_path):
    flow, say = _paced(tmp_path, 1000.0, speech_hold_max_s=3.0)  # the speaker never finishes
    t = 0.0
    flow.start(t)
    while t < 9.0:
        say.advance(t)
        flow.tick(t)
        t += 0.1
    assert "face_neutral" in _steps(flow)  # 3 s hold + 5 s greet


def test_sentence_go_comes_after_the_prompt_is_heard(tmp_path):
    from onset.flow import Timing

    flow, say = _paced(tmp_path, 9.0, greet_s=0.5, neutral_lead_s=0.1, neutral_measure_s=0.1,
                       smile_lead_s=0.1, smile_measure_s=0.1, sentence_lead_s=1.0, sentence_record_s=1.0)
    frame = np.zeros((72, 128, 3), np.uint8)
    t = 0.0
    flow.start(t)
    prompt_at = go_at = None
    while t < 120 and go_at is None:
        say.advance(t)
        flow.on_frame(frame, t)
        flow.tick(t)
        if prompt_at is None and any(l.startswith("Last one") for l in say.lines):
            prompt_at = t
        if any(e["type"] == "step" and str(e.get("caption", "")).startswith("Say it now") for e in flow.events):
            go_at = t
        t += 0.05
    assert prompt_at is not None and go_at is not None
    assert go_at - prompt_at >= 9.0 + 1.0 - 0.1  # the 9 s prompt, then the 1 s lead


def test_plain_say_functions_never_hold(tmp_path):
    said = []
    flow = _flow(tmp_path, say=said.append)
    assert not flow._say_reports
    flow.start(0.0)
    flow.tick(5.1)
    assert "face_neutral" in _steps(flow)
