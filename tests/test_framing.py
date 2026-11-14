"""Framing retries: the camera cannot see the arms (or the face), so Onset says how to fix the
picture and runs the step again, at most Timing.max_reframes times, then falls back to UNCLEAR.

Three layers:
* the pure framing functions on synthetic landmark tracks (arms) and observations (face);
* the flow with scripted stub meters (exact control over when framing is lost);
* the flow with the real ArmMeter maths fed synthetic pose samples (no MediaPipe).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from onset import decide, overlay, script
from onset.flow import CheckFlow, Timing
from onset.measure import arms as A
from onset.measure import face as F
from onset.types import ArmResult, FaceResult, Quality
from tests.test_arms import FPS as ARM_FPS
from tests.test_arms import H, W, make_samples
from tests.test_face import obs_series
from tests.test_flow import SENTENCE, assert_safe, baseline, evaluate, speech, steps

FPS = 15


# ============================================================================================
# Arms: frame_problem / framing_of on synthetic tracks


def _with(samples, fn):
    """Copy samples with fn(pts) applied to each pose's 6x3 array (person-side rows)."""
    out = []
    for s in samples:
        if s.pts is None:
            out.append(s)
            continue
        p = s.pts.copy()
        fn(p, s.t)
        out.append(A.PoseSample(s.t, p, s.size, s.luma))
    return out


def _hide(row, after=0.0, before=1e9):
    def fn(p, t):
        if after <= t < before:
            p[row, 2] = 0.1

    return fn


def test_good_hold_is_framed():
    s = make_samples()
    for k in (5, 40, 100):
        assert A.frame_problem(s[k]) is None
    assert A.framing_of(s[:100]).ok
    assert A.framing_of(s[:100], during_hold=True).ok


def test_lowered_hands_below_the_frame_are_not_a_framing_problem():
    # Before the raise the wrists hang below the bottom edge: an arm not raised yet, not framing.
    s = make_samples(raise_arms=False)
    assert s[30].pts[A.LW, 1] > H
    assert A.framing_of(s[:60]).ok


def test_no_body_and_too_dark():
    assert A.framing_of(make_samples(pose=False)[:30]).problem == "no_body"
    assert A.framing_of(make_samples(pose=False, luma=20.0)[:30]).problem == "too_dark"


def test_both_wrists_hidden_is_both_hands():
    f = A.framing_of(make_samples(wrist_vis=0.2)[:60])
    assert f.problem == "both_hands" and f.seconds > 1.5


@pytest.mark.parametrize("row, problem", [(A.LW, "left_hand"), (A.RW, "right_hand")])
def test_one_wrist_hidden_names_the_persons_side(row, problem):
    s = _with(make_samples(), _hide(row))
    f = A.framing_of(s[:60])
    assert f.problem == problem
    assert f.seconds > 1.5  # lost since the arms came up (~1 s in), sample 59 is at ~3.9 s


def test_arms_overhead_out_of_the_top_is_hands_high():
    f = A.framing_of(make_samples(up_level=-1.3)[:60])
    assert f.problem == "hands_high"


def test_shoulders_cut_off():
    def down(p, t):
        p[:, 1] += 500  # the picture shows only the head: shoulders below the bottom edge

    def up(p, t):
        p[:, 1] -= 400  # phone pointing at the chest: shoulders above the top edge

    def side(p, t):
        p[:, 0] += 900  # person off to the right

    assert A.framing_of(_with(make_samples(), down)[:30]).problem == "shoulders_low"
    assert A.framing_of(_with(make_samples(), up)[:30]).problem == "shoulders_high"
    assert A.framing_of(_with(make_samples(), side)[:30]).problem == "shoulders_side"


def test_seconds_counts_only_the_trailing_run():
    s = _with(make_samples(), _hide(A.LW, after=4.0))
    f = A.framing_of(s[: int(5.0 * ARM_FPS) + 1])
    assert f.problem == "left_hand"
    assert 0.8 <= f.seconds <= 1.1
    s2 = _with(make_samples(), _hide(A.LW, after=4.0, before=4.5))
    assert A.framing_of(s2[: int(6.0 * ARM_FPS)]).ok  # back in view: problem over


def test_one_odd_frame_does_not_change_the_advice():
    s = _with(make_samples(), _hide(A.LW, after=3.0))
    s = list(s[:75])
    k = 70
    p = s[k].pts.copy()
    p[A.RW, 2] = 0.1  # one frame where both are lost
    s[k] = A.PoseSample(s[k].t, p, s[k].size, s[k].luma)
    assert A.framing_of(s).problem == "left_hand"


def test_wrist_lost_while_moving_down_is_a_drop_not_framing():
    # The left arm drifts down, then vanishes (e.g. behind the table): a possible drop.
    s = _with(make_samples(left=lambda tau: 0.1 * tau), _hide(A.LW, after=6.0))
    upto = s[: int(7.0 * ARM_FPS)]
    held = A.framing_of(upto, during_hold=True)
    assert held.ok and held.dropped == "left"
    # Before the hold (no drop guard) the same picture is a framing problem.
    assert A.framing_of(upto).problem == "left_hand"


def test_wrist_lost_while_up_is_framing_even_during_the_hold():
    s = _with(make_samples(), _hide(A.RW, after=6.0))
    f = A.framing_of(s[: int(7.5 * ARM_FPS)], during_hold=True)
    assert f.problem == "right_hand" and f.dropped is None and f.seconds >= 1.0


def test_meter_framing_and_live():
    m = A.ArmMeter(model_path="", landmarker=object())
    assert m.framing().ok  # nothing seen yet: no advice
    for s in _with(make_samples(), _hide(A.LW))[:60]:
        m.add_sample(s)
    assert m.framing().problem == "left_hand"
    assert m.live()["framing"] == "left_hand"
    m.reset()
    assert m.framing().ok


def test_meter_framing_never_raises():
    m = A.ArmMeter(model_path="", landmarker=object())
    m.samples.append(A.PoseSample(0.0, np.zeros((2, 2)), (W, H)))  # malformed
    assert m.framing().ok


# ============================================================================================
# Face: framing_of on synthetic observations


def test_face_framing():
    good = obs_series(20)
    assert F.framing_of(good).ok and F.face_problem(good[0]) is None
    none = obs_series(20, face_every=10**6)  # only frame 0 has a face
    f = F.framing_of(none)
    assert f.problem == "no_face" and f.seconds == pytest.approx(18 / 15, abs=0.01)
    assert F.framing_of(obs_series(20, luma=20.0)).problem == "too_dark"
    assert F.framing_of(obs_series(20, yaw=40.0)).problem == "turned"
    assert F.framing_of(obs_series(20, cx=5.0)).problem == "edge"
    assert F.framing_of([]).ok
    assert F.framing_of(obs_series(10, face_every=10**6)[1:] + good[:1]).ok  # back in view


# ============================================================================================
# Flow with scripted stub meters


class ScriptedArms:
    """attempts[i] (i = number of reset() calls so far):
    up_after: step seconds until arms_up() (None = never)
    lost: (from, to, problem) step seconds during which framing() reports the problem
    dropped: side reported as dropped by framing(during_hold=True)
    result: the ArmResult for that attempt"""

    def __init__(self, attempts):
        self.attempts, self.i, self.t, self.resets = attempts, 0, 0.0, 0
        self.calls = []

    @property
    def a(self):
        return self.attempts[min(self.i, len(self.attempts) - 1)]

    def add(self, f):
        self.t = f.t

    def reset(self):
        self.i += 1
        self.resets += 1
        self.t = 0.0

    def arms_up(self):
        ua = self.a.get("up_after")
        return ua is not None and self.t >= ua

    def hold_start(self):
        return self.a.get("up_after") if self.arms_up() else None

    def framing(self, during_hold=False):
        self.calls.append(during_hold)
        lost = self.a.get("lost")
        if lost and lost[0] <= self.t < lost[1]:
            return A.Framing(lost[2], self.t - lost[0])
        return A.Framing(None, 0.0, self.a.get("dropped") if during_hold else None)

    def live(self):
        return {"arms_up": self.arms_up(), "hold_elapsed": 0.0, "framing": None}

    def result(self):
        return self.a["result"]

    def draw(self, rgba, live=None):
        pass

    def close(self):
        pass


class ScriptedFace:
    """attempts[i]: result (FaceResult), neutral_problem (framing() during the neutral phase)."""

    def __init__(self, attempts):
        self.attempts, self.i, self.resets = attempts, 0, 0
        self._live = {}

    @property
    def a(self):
        return self.attempts[min(self.i, len(self.attempts) - 1)]

    def add_neutral(self, f):
        pass

    def add_smile(self, f):
        pass

    def reset(self):
        self.i += 1
        self.resets += 1

    def framing(self):
        p = self.a.get("neutral_problem")
        return F.FaceFraming(p, 5.0) if p else F.FaceFraming(None)

    def result(self):
        return self.a["result"]

    def draw(self, rgba, live=None):
        pass

    def close(self):
        pass


def ok_arms(diff=0.02, **detail):
    return ArmResult(Quality.OK, 0.01, 0.01 + diff, diff, None, 10.0, 150, dict(detail))


def bad_arms(q=Quality.PARTIAL, reason="wrists_lost_during_hold", **detail):
    return ArmResult(q, None, None, None, None, 0.0, 0, {"reason": reason, **detail})


def ok_face(asym=0.05):
    return FaceResult(Quality.OK, asym, None, 40, {})


def bad_face(q=Quality.NO_SUBJECT, reason="no face in most frames"):
    return FaceResult(q, None, None, 0, {"reason": reason})


def drive(tmp_path, *, arms=None, face=None, base=True, timing=Timing(), sink=None, until=400.0):
    bdir = tmp_path / "baselines"
    if base:
        decide.save_baseline(baseline(), base_dir=bdir)
    arms = arms or ScriptedArms([{"up_after": 1.0, "result": ok_arms()}])
    face = face or ScriptedFace([{"result": ok_face()}])

    async def fake_speech(clip, expected):
        return speech()

    async def fake_send(phone, text, dry):
        return {"status": "dry_run", "accepted": False, "detail": "test"}

    flow = CheckFlow(sink=sink, patient_id="demo", patient_name="Pat", sentence=SENTENCE, timing=timing,
                     face_meter_factory=lambda: face, arm_meter_factory=lambda: arms,
                     speech_check=fake_speech, send_message=fake_send, baseline_dir=bdir,
                     caregiver_phone="", caregiver_send=False)
    frame = np.zeros((72, 128, 3), np.uint8)
    chunk = (np.ones(320, np.int16) * 2000).tobytes()
    clock = [0.0]
    emit = flow._emit

    def timed_emit(type_, **fields):  # note the fake-clock time of every event, for timing tests
        emit(type_, **fields)
        flow.events[-1]["_t"] = clock[0]

    flow._emit = timed_emit
    t = 0.0
    flow.start(t)
    while t < until and not flow.done:
        clock[0] = t
        flow.on_frame(frame, t)
        flow.on_audio(chunk, 16000, t)
        flow.tick(t)
        if flow.needs_evaluation:
            evaluate(flow)
        t += 1 / FPS
    flow.end_t = t
    flow.arms_meter, flow.face_meter = arms, face
    return flow


def framing_events(flow, check=None):
    return [e for e in flow.events if e["type"] == "framing" and (check is None or e["check"] == check)]


def outcome(flow):
    d = [e for e in flow.events if e["type"] == "decision"]
    return d[0]["outcome"].value if d else None


def said(flow, line):
    return any(e["type"] == "say" and e["text"] == line.say for e in flow.events)


def arms_result(flow):
    return next(e for e in flow.events if e["type"] == "result" and e["check"] == "arms")["data"]


def test_arms_not_in_frame_before_the_hold_warns_and_retries(tmp_path):
    arms = ScriptedArms([
        {"up_after": None, "lost": (0.0, 1e9, "left_hand"), "result": bad_arms()},
        {"up_after": 1.0, "result": ok_arms()},
    ])
    flow = drive(tmp_path, arms=arms)
    ev = framing_events(flow)
    assert [(e["check"], e["phase"], e["problem"], e["action"], e["retry"]) for e in ev] == \
        [("arms", "wait", "left_hand", "retry", 1)]
    assert ev[0]["caption"] == script.FRAME_LEFT_HAND.caption and ev[0]["max_retries"] == 2
    s = steps(flow)
    assert s.count("arms_wait") == 2 and s.count("arms_hold") == 1
    i = flow.events.index(ev[0])
    nxt = next(e for e in flow.events[i:] if e["type"] == "step")
    assert nxt["name"] == "arms_wait" and nxt["caption"] == script.FRAME_LEFT_HAND.caption
    assert said(flow, script.FRAME_LEFT_HAND)
    assert arms.resets == 1
    assert arms_result(flow).quality is Quality.OK
    assert arms_result(flow).detail["framing_retries"] == 1
    assert outcome(flow) == "no_change_detected"
    assert flow.outcome_line == script.CLOSE_NO_CHANGE
    assert_safe(flow)


def test_wait_warning_waits_for_the_person_to_read_and_raise(tmp_path):
    arms = ScriptedArms([
        {"up_after": None, "lost": (0.0, 1e9, "both_hands"), "result": bad_arms()},
        {"up_after": 1.0, "result": ok_arms()},
    ])
    flow = drive(tmp_path, arms=arms)
    wait = next(e for e in flow.events if e["type"] == "step" and e["name"] == "arms_wait")
    warn = framing_events(flow)[0]
    assert Timing().reframe_after_s <= warn["_t"] - wait["_t"] <= Timing().reframe_after_s + 0.2


def test_repeat_warning_gives_time_to_move_the_phone(tmp_path):
    arms = ScriptedArms([{"up_after": None, "lost": (0.0, 1e9, "both_hands"), "result": bad_arms()}])
    flow = drive(tmp_path, arms=arms)
    w1, w2 = framing_events(flow)[:2]
    assert Timing().reframe_retry_s <= w2["_t"] - w1["_t"] <= Timing().reframe_retry_s + 0.2


def test_short_glitch_in_wait_does_not_warn(tmp_path):
    arms = ScriptedArms([{"up_after": 6.0, "lost": (4.0, 5.0, "left_hand"), "result": ok_arms()}])
    flow = drive(tmp_path, arms=arms)
    assert not framing_events(flow) and arms.resets == 0
    assert outcome(flow) == "no_change_detected"


def test_arm_leaving_the_frame_during_the_hold_restarts_it(tmp_path):
    arms = ScriptedArms([
        {"up_after": 1.0, "lost": (5.0, 1e9, "right_hand"), "result": ok_arms()},
        {"up_after": 1.0, "result": ok_arms()},
    ])
    flow = drive(tmp_path, arms=arms)
    ev = framing_events(flow)
    assert [(e["phase"], e["problem"], e["action"]) for e in ev] == [("hold", "right_hand", "retry")]
    assert said(flow, script.ARMS_RESTART) and said(flow, script.FRAME_RIGHT_HAND)
    s = steps(flow)
    assert s[s.index("arms_wait"):s.index("sentence")] == ["arms_wait", "arms_hold", "arms_wait", "arms_hold"]
    assert True in arms.calls  # the hold asked with the drop guard on
    assert outcome(flow) == "no_change_detected"


def test_restarted_hold_is_timed_from_its_own_start(tmp_path):
    arms = ScriptedArms([
        {"up_after": 1.0, "lost": (5.0, 1e9, "right_hand"), "result": ok_arms()},
        {"up_after": 2.0, "result": ok_arms()},
    ])
    flow = drive(tmp_path, arms=arms)
    holds = [e for e in flow.events if e["type"] == "step" and e["name"] == "arms_hold"]
    assert len(holds) == 2 and all(h["seconds"] == Timing().arms_hold_s for h in holds)
    # The second hold ran its full 10 s from when its arms came up (up_after = 2 s of its frames).
    sentence = next(e for e in flow.events if e["type"] == "step" and e["name"] == "sentence")
    restart = framing_events(flow)[0]
    tm = Timing()
    assert sentence["_t"] - restart["_t"] == pytest.approx(2.0 + tm.arms_hold_s + tm.arms_hold_margin_s, abs=0.2)


def test_brief_loss_within_the_grace_period_does_not_restart(tmp_path):
    arms = ScriptedArms([{"up_after": 1.0, "lost": (5.0, 5.8, "right_hand"), "result": ok_arms()}])
    flow = drive(tmp_path, arms=arms)
    assert not framing_events(flow) and arms.resets == 0
    assert steps(flow).count("arms_hold") == 1


def test_retry_cap_then_falls_back_to_unclear_and_911(tmp_path):
    arms = ScriptedArms([{"up_after": None, "lost": (0.0, 1e9, "both_hands"),
                          "result": bad_arms(reason="wrists_not_visible")}])
    flow = drive(tmp_path, arms=arms)
    ev = framing_events(flow, "arms")
    assert [(e["action"], e["retry"]) for e in ev] == [("retry", 1), ("retry", 2), ("give_up", 2)]
    assert arms.resets == 2
    assert said(flow, script.ARMS_TIMEOUT)  # then today's behaviour: time out, move on
    r = arms_result(flow)
    assert r.quality is Quality.PARTIAL and r.detail["framing_retries"] == 2
    assert r.detail["framing_problem"] == "both_hands"
    d = next(e for e in flow.events if e["type"] == "decision")
    assert d["outcome"].value == "unclear"
    assert any("even after 2 tries" in x for x in d["reasons"]), d["reasons"]
    assert flow.outcome_line == script.CLOSE_UNCLEAR and "911" in flow.outcome_line.caption
    assert any(e["type"] == "caregiver" for e in flow.events)
    assert "sentence" in steps(flow)  # the call still reaches the sentence step
    assert next(e for e in flow.events if e["type"] == "result" and e["check"] == "speech")["quality"] is Quality.OK
    assert_safe(flow)


def test_retry_cap_during_the_hold(tmp_path):
    arms = ScriptedArms([{"up_after": 1.0, "lost": (5.0, 1e9, "left_hand"), "result": bad_arms()}])
    flow = drive(tmp_path, arms=arms)
    ev = framing_events(flow, "arms")
    assert [e["action"] for e in ev] == ["retry", "retry", "give_up"]
    assert steps(flow).count("arms_hold") == 3  # the last hold runs to the end as before
    assert said(flow, script.ARMS_DONE)
    assert outcome(flow) == "unclear" and flow.outcome_line == script.CLOSE_UNCLEAR


def test_max_reframes_zero_is_todays_behaviour(tmp_path):
    arms = ScriptedArms([{"up_after": None, "lost": (0.0, 1e9, "both_hands"), "result": bad_arms()}])
    flow = drive(tmp_path, arms=arms, timing=Timing(max_reframes=0))
    assert [e["action"] for e in framing_events(flow)] == ["give_up"]
    assert arms.resets == 0 and steps(flow).count("arms_wait") == 1
    assert outcome(flow) == "unclear"
    assert "framing_retries" not in arms_result(flow).detail


def test_arm_that_dropped_out_of_view_is_never_retried(tmp_path):
    # framing() says the left arm vanished while moving down: the hold is evidence.
    arms = ScriptedArms([
        {"up_after": 1.0, "dropped": "left", "lost": (8.0, 1e9, "left_hand"), "result": bad_arms()},
        {"up_after": 1.0, "result": ok_arms()},
    ])
    flow = drive(tmp_path, arms=arms)
    assert not framing_events(flow) and arms.resets == 0
    r = arms_result(flow)
    assert r.quality is Quality.PARTIAL and r.detail["dropped_out_of_view"] == "left"
    assert outcome(flow) == "unclear" and flow.outcome_line == script.CLOSE_UNCLEAR


def test_unreadable_hold_result_gets_one_more_try(tmp_path):
    # Live framing never stayed bad for a full second, but the hold could not be read.
    arms = ScriptedArms([{"up_after": 1.0, "result": bad_arms()}, {"up_after": 1.0, "result": ok_arms()}])
    flow = drive(tmp_path, arms=arms)
    ev = framing_events(flow)
    assert [(e["phase"], e["problem"]) for e in ev] == [("hold", "both_hands")]
    assert arms_result(flow).quality is Quality.OK
    assert outcome(flow) == "no_change_detected"


@pytest.mark.parametrize("res", [
    bad_arms(early_drop="left"),  # one arm came down early: a finding
    bad_arms(Quality.NO_RESPONSE, "hold_too_short"),  # arms not held: a finding
    bad_arms(Quality.MOTION, "camera_or_body_moving"),
    bad_arms(Quality.ERROR, "landmarker_failed"),
])
def test_findings_and_other_failures_are_not_retried(tmp_path, res):
    arms = ScriptedArms([{"up_after": 1.0, "result": res}, {"up_after": 1.0, "result": ok_arms()}])
    flow = drive(tmp_path, arms=arms)
    assert not framing_events(flow) and arms.resets == 0
    assert outcome(flow) in ("unclear", "escalate")


def test_arms_never_raised_without_framing_problem_is_not_retried(tmp_path):
    arms = ScriptedArms([{"up_after": None, "result": bad_arms(Quality.NO_RESPONSE, "arms_not_raised")}])
    flow = drive(tmp_path, arms=arms)
    assert not framing_events(flow) and arms.resets == 0
    assert said(flow, script.ARMS_TIMEOUT) and outcome(flow) == "unclear"


def test_escalation_still_happens_after_a_retry(tmp_path):
    arms = ScriptedArms([
        {"up_after": None, "lost": (0.0, 1e9, "hands_high"), "result": bad_arms()},
        {"up_after": 1.0, "result": ok_arms(diff=0.5)},
    ])
    flow = drive(tmp_path, arms=arms)
    assert framing_events(flow)[0]["caption"] == script.FRAME_HANDS_HIGH.caption
    assert outcome(flow) == "escalate" and flow.outcome_line == script.CLOSE_ESCALATE


def test_retries_extend_the_time_cap(tmp_path):
    # Two arm retries cost more than the plain 90 s cap allows; the sentence must still run.
    arms = ScriptedArms([
        {"up_after": 1.0, "lost": (9.0, 1e9, "left_hand"), "result": ok_arms()},
        {"up_after": 1.0, "lost": (9.0, 1e9, "left_hand"), "result": ok_arms()},
        {"up_after": 1.0, "result": ok_arms()},
    ])
    face = ScriptedFace([{"result": bad_face()}, {"result": bad_face()}, {"result": ok_face()}])
    flow = drive(tmp_path, arms=arms, face=face, timing=Timing(cap_s=60.0))
    r = {e["check"]: e["quality"] for e in flow.events if e["type"] == "result"}
    assert r == {"face": Quality.OK, "arms": Quality.OK, "speech": Quality.OK}
    assert flow.end_t > 60.0
    assert outcome(flow) == "no_change_detected"


def test_hard_cap_still_ends_the_call(tmp_path):
    arms = ScriptedArms([{"up_after": None, "lost": (0.0, 1e9, "no_body"), "result": bad_arms()}])
    t = Timing(cap_s=20.0, arms_wait_timeout_s=60.0)
    flow = drive(tmp_path, arms=arms, timing=t)
    bonus = 2 * (t.arms_wait_timeout_s + t.arms_hold_s)
    assert flow.end_t < t.cap_s + bonus + 2.0
    assert outcome(flow) == "unclear"


# ---- face


def test_face_not_visible_warns_and_reruns_the_face_step(tmp_path):
    face = ScriptedFace([{"result": bad_face()}, {"result": ok_face()}])
    flow = drive(tmp_path, face=face)
    ev = framing_events(flow, "face")
    assert [(e["phase"], e["problem"], e["action"]) for e in ev] == [("smile", "no_face", "retry")]
    assert ev[0]["caption"] == script.FACE_NOT_VISIBLE.caption
    assert said(flow, script.FACE_NOT_VISIBLE)
    s = steps(flow)
    assert s[:7] == ["greet", "face_neutral", "face_smile", "face_reframe", "face_neutral", "face_smile", "arms_wait"]
    assert face.resets == 1
    res = [e for e in flow.events if e["type"] == "result" and e["check"] == "face"]
    assert len(res) == 1 and res[0]["quality"] is Quality.OK  # one result, from the good attempt
    assert res[0]["data"].detail["framing_retries"] == 1
    assert outcome(flow) == "no_change_detected"


def test_face_lost_during_neutral_warns_before_the_smile(tmp_path):
    face = ScriptedFace([{"neutral_problem": "turned", "result": bad_face(Quality.PARTIAL, "head turned away")},
                         {"result": ok_face()}])
    flow = drive(tmp_path, face=face)
    ev = framing_events(flow, "face")
    assert [(e["phase"], e["problem"]) for e in ev] == [("neutral", "turned")]
    assert steps(flow)[:4] == ["greet", "face_neutral", "face_reframe", "face_neutral"]
    assert said(flow, script.FACE_TURNED)


@pytest.mark.parametrize("res, problem, line", [
    (bad_face(Quality.TOO_DARK, "too dark"), "too_dark", script.FRAME_DARK),
    (bad_face(Quality.PARTIAL, "face cut off by the frame edge"), "edge", script.FACE_OFF_CENTER),
    (bad_face(Quality.PARTIAL, "head turned away"), "turned", script.FACE_TURNED),
])
def test_face_hint_follows_the_result(tmp_path, res, problem, line):
    face = ScriptedFace([{"result": res}, {"result": ok_face()}])
    flow = drive(tmp_path, face=face)
    ev = framing_events(flow, "face")
    assert ev[0]["problem"] == problem and ev[0]["caption"] == line.caption


def test_face_retry_cap_falls_back_to_unclear(tmp_path):
    face = ScriptedFace([{"result": bad_face()}])
    flow = drive(tmp_path, face=face)
    assert [e["action"] for e in framing_events(flow, "face")] == ["retry", "retry", "give_up"]
    assert steps(flow).count("face_reframe") == 2
    d = next(e for e in flow.events if e["type"] == "decision")
    assert d["outcome"].value == "unclear"
    assert any("even after 2 tries" in x for x in d["reasons"]), d["reasons"]
    assert flow.outcome_line == script.CLOSE_UNCLEAR
    assert_safe(flow)


@pytest.mark.parametrize("q", [Quality.NO_RESPONSE, Quality.MOTION, Quality.ERROR])
def test_no_smile_and_other_face_failures_are_not_retried(tmp_path, q):
    face = ScriptedFace([{"result": bad_face(q, "x")}, {"result": ok_face()}])
    flow = drive(tmp_path, face=face)
    assert not framing_events(flow) and face.resets == 0
    assert outcome(flow) == "unclear"


def test_first_run_baseline_after_a_retry(tmp_path):
    face = ScriptedFace([{"result": bad_face()}, {"result": ok_face()}])
    flow = drive(tmp_path, face=face, base=False)
    assert flow.baseline_saved and flow.outcome_line == script.CLOSE_BASELINE_SAVED


# ---- events, captions


def test_framing_events_reach_the_sink_as_json(tmp_path):
    from onset.events import EventSink

    sink = EventSink("f1", "replay", root=tmp_path / "runs")
    arms = ScriptedArms([{"up_after": None, "lost": (0.0, 1e9, "left_hand"), "result": bad_arms()},
                         {"up_after": 1.0, "result": ok_arms()}])
    drive(tmp_path, arms=arms, sink=sink)
    sink.close("test")
    lines = [json.loads(x) for x in (tmp_path / "runs" / "f1" / "events.jsonl").read_text().splitlines()]
    fr = [x for x in lines if x["type"] == "framing"]
    assert fr == [{"t": fr[0]["t"], "type": "framing", "check": "arms", "phase": "wait", "problem": "left_hand",
                   "action": "retry", "retry": 1, "max_retries": 2, "caption": script.FRAME_LEFT_HAND.caption}]
    res = next(x for x in lines if x["type"] == "result" and x["check"] == "arms")
    assert res["data"]["detail"]["framing_retries"] == 1


def test_every_framing_problem_has_a_line_that_fits():
    assert set(script.ARMS_FRAMING) == set(A.FRAMING_PROBLEMS)
    assert set(script.FACE_FRAMING) == set(F.FACE_FRAMING_PROBLEMS)
    lines = [*script.ARMS_FRAMING.values(), *script.FACE_FRAMING.values(), script.ARMS_RESTART]
    for line in lines:
        assert line in script.ALL_LINES, line  # warmed in the TTS cache, checked for banned words
        _, info = overlay.caption_layer(line.caption, 640, 360, "bottom")
        assert info["cap"] >= overlay.MIN_CAP_PX, (line.caption, info)
        assert len(info["lines"]) <= 2, (line.caption, info["lines"])  # short: one glance
        assert not info["lines"][-1].endswith("…")
    assert script.framing_line("arms", "nope") == script.FRAME_MOVE_BACK
    assert script.framing_line("face", None) == script.FACE_NOT_VISIBLE


def test_render_during_face_reframe(tmp_path):
    face = ScriptedFace([{"result": bad_face()}, {"result": ok_face()}])
    flow = drive(tmp_path, face=face, until=17.0)
    assert flow.state == "face_reframe"
    out = flow.render(np.full((720, 1280, 3), 90, np.uint8), 17.0)
    assert out.shape == (360, 640, 4)


# ============================================================================================
# Flow with the real ArmMeter maths on synthetic pose tracks (no MediaPipe)


class SampleMeter(A.ArmMeter):
    """A real ArmMeter whose add() looks up a precomputed pose sample per attempt."""

    def __init__(self, tracks):
        self.tracks, self.attempt, self._ready = tracks, 0, False
        super().__init__(model_path="", landmarker=object())
        self._ready = True

    def reset(self):
        if self._ready:  # ArmMeter.__init__ calls reset() too
            self.attempt = min(self.attempt + 1, len(self.tracks) - 1)
        super().reset()

    def add(self, frame):
        track = self.tracks[self.attempt]
        k = min(int(round(frame.t * ARM_FPS)), len(track) - 1)
        s = track[k]
        self.add_sample(A.PoseSample(float(frame.t), s.pts, s.size, s.luma))


def test_real_meter_left_hand_out_of_view_then_fixed(tmp_path):
    hidden = _with(make_samples(duration=40, lower_at=None), _hide(A.LW))
    good = make_samples(duration=40, lower_at=None)
    meter = SampleMeter([hidden, good])
    flow = drive(tmp_path, arms=meter)
    ev = framing_events(flow)
    assert [(e["phase"], e["problem"]) for e in ev] == [("wait", "left_hand")]
    r = arms_result(flow)
    assert r.quality is Quality.OK and r.detail["framing_retries"] == 1
    assert outcome(flow) == "no_change_detected"


def test_real_meter_hand_lost_mid_hold_restarts(tmp_path):
    lost = _with(make_samples(duration=40, lower_at=None), _hide(A.RW, after=6.0))
    good = make_samples(duration=40, lower_at=None)
    flow = drive(tmp_path, arms=SampleMeter([lost, good]))
    ev = framing_events(flow)
    assert [(e["phase"], e["problem"]) for e in ev] == [("hold", "right_hand")]
    assert arms_result(flow).quality is Quality.OK
    assert outcome(flow) == "no_change_detected"


def test_real_meter_drifting_arm_that_vanishes_is_not_retried(tmp_path):
    drop = _with(make_samples(duration=40, lower_at=None, left=lambda tau: 0.1 * tau), _hide(A.LW, after=6.0))
    good = make_samples(duration=40, lower_at=None)
    meter = SampleMeter([drop, good])
    flow = drive(tmp_path, arms=meter)
    assert not framing_events(flow)
    assert meter.attempt == 0
    assert outcome(flow) in ("unclear", "escalate")
    assert flow.outcome_line in (script.CLOSE_UNCLEAR, script.CLOSE_ESCALATE)


def test_real_meter_never_framed_falls_back_to_unclear(tmp_path):
    hidden = _with(make_samples(duration=40, lower_at=None), _hide(A.LW))
    meter = SampleMeter([hidden])
    flow = drive(tmp_path, arms=meter)
    assert [e["action"] for e in framing_events(flow)] == ["retry", "retry", "give_up"]
    r = arms_result(flow)
    assert r.quality is not Quality.OK and r.detail["framing_retries"] == 2
    assert outcome(flow) == "unclear" and flow.outcome_line == script.CLOSE_UNCLEAR
