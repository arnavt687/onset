"""Piece 5: the scripted, caption-first check-in flow.

`CheckFlow` is a state machine driven by three inputs, so a live call and a replay run the
same code:

    flow.start(t)                         # bridge_ready: greet
    flow.on_frame(rgb, t)                 # every caller frame (upright RGB/RGBA); runs the meters
    flow.on_audio(pcm, sample_rate, t)    # caller audio, 16-bit mono
    flow.tick(t)                          # advance timers; call ~15 times a second
    if flow.needs_evaluation:             # after the sentence step (or the time cap)
        await flow.evaluate()             # speech check, decision, baseline, caregiver text
    img = flow.render(latest_caller_rgb, t)   # the RGBA frame to send back

`t` is seconds on one clock (monotonic for live calls, recorded time for replays). Nothing
here waits for the caller to have heard anything: every step is on screen and timed.

Steps: greet -> face_neutral -> face_smile -> arms_wait -> arms_hold -> sentence -> deciding
-> closing (+ baseline_saved on a first run). The whole check is capped at Timing.cap_s; any
step not finished by then counts as NO_RESPONSE, which makes the decision UNCLEAR.

Framing retries: when the camera cannot see what a step measures (no face, a hand out of the
picture, too dark), the flow says what to fix and runs that step again: face_reframe ->
face_neutral, or back to arms_wait (a hold that loses an arm for more than
Timing.hold_lost_grace_s restarts). At most Timing.max_reframes per step; each one emits a
`framing` event and extends the time cap by what it costs. After the last retry the step runs
as it always did, so an unreadable step still ends UNCLEAR (caregiver + 911). A wrist that was
moving down when it disappeared, an arm that came down early, no smile and arms never raised
are findings, never framing problems.

Safety: outcomes come only from onset.decide (rules). UNCLEAR and ESCALATE both tell the
person to call 911 now and bring the caregiver in (a join link plus the summary by iMessage,
onset/caregiver.py; a dry run unless ONSET_LIVE_ACTIONS=1). No line says "you're fine"
(see onset/script.py).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from onset import overlay, script
from onset.types import (
    ArmResult,
    AudioClip,
    CheckResults,
    Decision,
    FaceResult,
    Frame,
    Outcome,
    Quality,
    SpeechResult,
)

log = logging.getLogger("onset.flow")

SpeechCheck = Callable[[AudioClip, str], Awaitable[SpeechResult]]
SayFn = Callable[[str], None]


@dataclass(frozen=True)
class Timing:
    greet_s: float = 5.0  # greeting on screen before the first step
    neutral_lead_s: float = 2.5  # read the instruction, settle
    neutral_measure_s: float = 2.0
    smile_lead_s: float = 1.5
    smile_measure_s: float = 3.0
    arms_wait_timeout_s: float = 15.0
    arms_hold_s: float = 10.0  # ArmMeter.HOLD_SECONDS
    arms_hold_margin_s: float = 0.5
    sentence_lead_s: float = 6.0  # "lower your arms" + hear/read the sentence
    sentence_record_s: float = 7.0
    speech_timeout_s: float = 15.0
    cap_s: float = 90.0  # from start(), not counting speech holds; everything unfinished is NO_RESPONSE
    # A step's timer waits while its spoken instruction is still playing (so "Say it now" comes
    # after the caller has heard the sentence), for at most this long per step.
    speech_hold_max_s: float = 12.0
    live_interval_s: float = 0.2  # live metric events, ~5 per second
    # Framing retries (docs/thresholds.md, "Framing retries"): the camera cannot see what a
    # step measures, so say how to fix it and run the step again, at most max_reframes times
    # per step. After that the step carries on as before, and an unreadable step is UNCLEAR.
    max_reframes: int = 2
    reframe_after_s: float = 4.0  # arms_wait: time to read the line and raise the arms first
    reframe_retry_s: float = 8.0  # ... and after a warning: time to move the phone and raise again
    reframe_persist_s: float = 1.5  # arms_wait: the problem must have lasted this long
    hold_lost_grace_s: float = 1.0  # arms_hold: an arm out of the picture this long restarts
    face_reframe_s: float = 4.0  # the fix-it caption on screen before the face step re-runs
    face_lost_fraction: float = 0.75  # face missing for this much of the neutral window: re-run now


# Steps whose timer waits for their spoken instruction to finish (arms_hold is timed by the
# arm meter from when the arms came up, so it never waits).
SPEECH_HOLD_STEPS = ("greet", "face_reframe", "face_neutral", "face_smile", "arms_wait", "sentence")

STEP_ORDER = ["greet", "face_neutral", "face_smile", "arms_wait", "arms_hold", "sentence",
              "deciding", "closing"]

# Step qualities that mean "the camera could not see it", worth one more try after the person
# fixes the picture. NO_RESPONSE (no smile, arms not raised) is a finding and is never retried;
# neither are MOTION and ERROR.
REFRAMEABLE = (Quality.NO_SUBJECT, Quality.PARTIAL, Quality.TOO_DARK)

_ARM_REASON_PROBLEM = {"body_not_found": "no_body", "wrists_not_visible": "both_hands",
                       "wrists_lost_during_hold": "both_hands", "too_far_or_side_on": "shoulders_side",
                       "very_dark": "too_dark", "no_frames": "no_body"}
_FACE_REASON_PROBLEM = {"head turned away": "turned", "face cut off by the frame edge": "edge"}


def _problem_from_result(check: str, res: Any) -> str:
    """A framing problem name from a finished step's result (when no live framing is known)."""
    if res.quality is Quality.TOO_DARK:
        return "too_dark"
    reason = str((res.detail or {}).get("reason", ""))
    if check == "face":
        return _FACE_REASON_PROBLEM.get(reason, "no_face")
    return _ARM_REASON_PROBLEM.get(reason, "both_hands")

# Where the caption panel goes, so it does not cover what is being measured.
CAPTION_POS = {"face_neutral": "top", "face_smile": "top"}


def _no_response(check: str, reason: str) -> Any:
    detail = {"reason": reason}
    if check == "face":
        return FaceResult(Quality.NO_RESPONSE, None, None, 0, detail)
    if check == "arms":
        return ArmResult(Quality.NO_RESPONSE, None, None, None, None, 0.0, 0, detail)
    return SpeechResult(Quality.NO_RESPONSE, "", None, None, None, None, detail)


def _error(check: str, reason: str) -> Any:
    detail = {"reason": reason}
    if check == "face":
        return FaceResult(Quality.ERROR, None, None, 0, detail)
    if check == "arms":
        return ArmResult(Quality.ERROR, None, None, None, None, 0.0, 0, detail)
    return SpeechResult(Quality.ERROR, "", None, None, None, None, detail)


def _default_face_meter():
    from onset.measure.face import FaceMeter

    return FaceMeter(process_every=int(os.environ.get("ONSET_FACE_EVERY", "1")))


def _default_arm_meter():
    from onset.measure.arms import ArmMeter

    return ArmMeter()


async def _default_speech_check(clip: AudioClip, expected: str) -> SpeechResult:
    from onset.speech import check_sentence

    return await check_sentence(clip, expected)


async def _default_send(recipient: str, text: str, dry_run: bool) -> dict:
    from onset.chert_api import send_imessage

    return await send_imessage(recipient, text, dry_run=dry_run)


class CheckFlow:
    def __init__(
        self,
        *,
        sink=None,  # onset.events.EventSink, or None
        say: SayFn | None = None,
        patient_id: str | None = None,
        patient_name: str | None = None,
        sentence: str | None = None,
        timing: Timing = Timing(),
        out_size: tuple[int, int] = (640, 360),
        face_meter_factory: Callable[[], Any] | None = None,
        arm_meter_factory: Callable[[], Any] | None = None,
        speech_check: SpeechCheck | None = None,
        send_message: Callable[[str, str, bool], Awaitable[dict]] | None = None,
        baseline_dir: Path | None = None,
        caregiver_phone: str | None = None,
        caregiver_send: bool | None = None,
        caregiver_name: str | None = None,
        escalate: Callable[[Decision, str], Awaitable[dict]] | None = None,
        save_baseline: bool = True,
        recorder=None,  # tools.record.Recorder (whole call), or None
        source: str = "call",
        run_id: str = "",
    ) -> None:
        from onset import decide as _decide  # imported here so tests can monkeypatch onset.decide
        from onset.speech import SENTENCES

        self._decide = _decide
        self.sink = sink
        self._say_fn = say
        self.patient_id = patient_id or os.environ.get("ONSET_PATIENT_ID", "demo")
        self.patient_name = patient_name or os.environ.get("ONSET_PATIENT_NAME", "Demo patient")
        self.timing = timing
        self.out_w, self.out_h = out_size
        self._face_factory = face_meter_factory or _default_face_meter
        self._arm_factory = arm_meter_factory or _default_arm_meter
        self._speech_check = speech_check or _default_speech_check
        self._send = send_message or _default_send
        self.baseline_dir = baseline_dir
        self.caregiver_phone = caregiver_phone if caregiver_phone is not None else os.environ.get("ONSET_CAREGIVER_PHONE", "")
        self.caregiver_name = caregiver_name if caregiver_name is not None else os.environ.get("ONSET_CAREGIVER_NAME", "")
        # Whether a real text may be sent is decided by the safety gate (onset.actions:
        # ONSET_LIVE_ACTIONS=1 plus a caregiver phone). caregiver_send=False forces a dry run.
        self._force_dry = caregiver_send is False
        import inspect

        try:
            self._say_reports = say is not None and "on_done" in inspect.signature(say).parameters
        except (TypeError, ValueError):
            self._say_reports = False
        self._escalate_fn = escalate  # agent.py: caregiver link + relay + text; None: text only
        self.save_baseline = save_baseline  # False for an unidentified caller
        self.caregiver_status = ""  # "" | invited | joined | talking | left
        self.caller_gone = False  # set by hangup(): no one to invite the caregiver to
        self._cg_frame: tuple[float, np.ndarray] | None = None  # (t, rgb) caregiver camera for PiP
        self.recorder = recorder
        self.source, self.run_id = source, run_id

        try:
            self.baseline = self._decide.load_baseline(self.patient_id, base_dir=baseline_dir)
        except Exception:  # noqa: BLE001 - a broken baseline is no baseline
            log.exception("[onset] could not load baseline")
            self.baseline = None
        # With a baseline, always its sentence: decide() treats a different sentence as UNCLEAR.
        self.sentence = self.baseline.sentence if self.baseline else (sentence or SENTENCES[0])
        self._background: set[asyncio.Task] = set()

        self._lock = threading.RLock()
        self.state = "idle"
        self.caption = ""
        self.countdown: tuple[float, float] | None = None  # (t_end, seconds)
        self.t0: float | None = None
        self._step_t = 0.0  # when the current step began (moved on while its line is playing)
        self._enter_t = 0.0  # when the current step really began
        self._step_n = 0
        self._awaiting_line = False  # the current step's instruction is still playing
        self._hold_total = 0.0  # seconds steps stood still for speech (not counted in cap_s)
        self._last_tick: float | None = None
        self._measure_t: float | None = None  # when measuring began within the step
        self._arms_t0 = 0.0
        self._last_live = -1e9
        self.face: FaceResult | None = None
        self.arms: ArmResult | None = None
        self.speech: SpeechResult | None = None
        self.decision: Decision | None = None
        self.outcome_line: script.Line | None = None
        self.baseline_saved = False
        self.needs_evaluation = False
        self.done = False
        self._audio: list[tuple[float, bytes, int]] = []  # (t, pcm, rate) during the sentence window
        self._rec_window: tuple[float, float] | None = None
        self._face_meter = None
        self._arm_meter = None
        self.reframes = {"face": 0, "arms": 0}  # framing retries used per step
        self._framing_last: dict[str, str | None] = {"face": None, "arms": None}
        self._framing_gave_up: set[str] = set()
        self._cap_bonus = 0.0  # each retry extends the time cap by what it costs
        self._hold_dropped: str | None = None  # side that vanished while moving down, this hold
        self.events: list[dict] = []  # a copy of everything emitted (tests, logs)

    # ------------------------------------------------------------------ output helpers

    def _emit(self, type_: str, **fields: Any) -> None:
        self.events.append({"type": type_, **fields})
        if self.sink is not None:
            try:
                self.sink.emit(type_, **fields)
            except Exception:  # noqa: BLE001
                log.exception("[onset] event sink failed")

    def _speak(self, text: str, on_done: Callable[[], None] | None = None) -> None:
        """Say a line. `on_done` runs once it has finished playing (at once if it cannot be
        played or the say function cannot report that)."""
        if not text:
            if on_done:
                on_done()
            return
        if self._say_fn is None:
            self._emit("say", text=text, audio=None)
            if on_done:
                on_done()
            return
        try:
            if on_done is not None and self._say_reports:
                self._say_fn(text, on_done=on_done)  # onset.voice.Speaker.say
            else:
                self._say_fn(text)  # the speaker emits the `say` event with its audio file
                if on_done:
                    on_done()
        except Exception:  # noqa: BLE001 - never break the flow over audio
            log.exception("[onset] say failed")
            self._emit("say", text=text, audio=None)
            if on_done:
                on_done()

    def _line_done(self, step_n: int) -> Callable[[], None]:
        def done() -> None:
            with self._lock:
                if self._step_n == step_n:
                    self._awaiting_line = False
        return done

    def _enter(self, name: str, t: float, line: script.Line | None = None, *,
               seconds: float | None = None, caption: str | None = None, speak: bool = True) -> None:
        self.state = name
        self._step_t = t
        self._enter_t = t
        self._step_n += 1
        self._awaiting_line = False
        self._measure_t = None
        self.caption = caption if caption is not None else (line.caption if line else "")
        self.countdown = (t + seconds, seconds) if seconds else None
        rel = t - (self.t0 if self.t0 is not None else t)
        log.info("[onset] step %s (t=%.1f)", name, rel)
        self._emit("step", name=name, caption=self.caption, seconds=seconds)
        if self.recorder is not None:
            try:
                self.recorder.mark(name, rel)
            except Exception:  # noqa: BLE001
                pass
        if speak and line is not None:
            if name in SPEECH_HOLD_STEPS and line.say:
                self._awaiting_line = True
                self._speak(line.say, on_done=self._line_done(self._step_n))
            else:
                self._speak(line.say)

    def _hold_for_speech(self, t: float) -> bool:
        """While the step's instruction is still playing, its clock (and countdown) stands
        still. Returns True while holding."""
        last, self._last_tick = self._last_tick, t
        if not self._awaiting_line or self.state not in SPEECH_HOLD_STEPS:
            return False
        if t - self._enter_t >= self.timing.speech_hold_max_s:
            self._awaiting_line = False  # never stall a step on a stuck speaker
            return False
        dt = max(0.0, t - (last if last is not None else t))
        self._step_t += dt
        self._hold_total += dt
        if self._measure_t is not None:
            self._measure_t += dt
        if self.countdown:
            self.countdown = (self.countdown[0] + dt, self.countdown[1])
        return True

    # ------------------------------------------------------------------ inputs

    def start(self, t: float) -> None:
        with self._lock:
            if self.state != "idle":
                return
            self.t0 = t
            # The baseline's numbers go with the run so the dashboard can draw "usual" markers
            # without reading the baseline file (events.py turns the dataclass into a plain dict).
            self._emit("run_started", run_id=self.run_id, source=self.source, patient_id=self.patient_id,
                       has_baseline=self.baseline is not None, sentence=self.sentence,
                       baseline=self.baseline)
            self._enter("greet", t, script.GREETING, seconds=self.timing.greet_s)
            for name, attr, factory in (("face", "_face_meter", self._face_factory),
                                        ("arms", "_arm_meter", self._arm_factory)):
                try:
                    meter = factory()
                except Exception as e:  # noqa: BLE001 - e.g. model file missing
                    log.exception("[onset] %s meter failed to start", name)
                    meter = None
                    setattr(self, name, _error(name, f"meter failed to start: {type(e).__name__}"))
                setattr(self, attr, meter)

    def on_frame(self, rgb: np.ndarray, t: float) -> None:
        """One upright caller frame. Runs MediaPipe for the active step: call it from a worker thread."""
        with self._lock:
            state = self.state
            if self.recorder is not None and self.t0 is not None:
                try:
                    self.recorder.add_frame(rgb, t - self.t0)
                except Exception:  # noqa: BLE001
                    pass
            try:
                if state in ("face_neutral", "face_smile") and self._measure_t is not None and self._face_meter is not None:
                    f = Frame(rgb=np.ascontiguousarray(rgb[..., :3]), t=t - self._measure_t)
                    (self._face_meter.add_neutral if state == "face_neutral" else self._face_meter.add_smile)(f)
                    if t - self._last_live >= self.timing.live_interval_s:
                        self._last_live = t
                        live = dict(getattr(self._face_meter, "_live", {}) or {})
                        self._emit("live", step=state, metrics=_small(live))
                elif state in ("arms_wait", "arms_hold") and self._arm_meter is not None:
                    self._arm_meter.add(Frame(rgb=np.ascontiguousarray(rgb[..., :3]), t=t - self._arms_t0))
                    if t - self._last_live >= self.timing.live_interval_s:
                        self._last_live = t
                        self._emit("live", step=state, metrics=_small(self._arm_meter.live()))
            except Exception:  # noqa: BLE001 - one bad frame never breaks the call
                log.exception("[onset] meter failed on a frame")

    def on_audio(self, pcm: bytes, sample_rate: int, t: float) -> None:
        with self._lock:
            if self.recorder is not None and self.t0 is not None:
                try:
                    self.recorder.add_audio(pcm, sample_rate, t - self.t0)
                except Exception:  # noqa: BLE001
                    pass
            w = self._rec_window
            if w is not None and w[0] <= t <= w[1] + 0.5:
                self._audio.append((t, bytes(pcm), int(sample_rate)))

    def tick(self, t: float) -> None:
        with self._lock:
            try:
                self._tick(t)
            except Exception:  # noqa: BLE001
                log.exception("[onset] flow tick failed; finishing as unclear")
                self._finish_measuring(t, "internal error")

    # ------------------------------------------------------------------ the state machine

    def _tick(self, t: float) -> None:
        tm, s = self.timing, self.state
        if s in ("idle", "deciding", "closing") or self.needs_evaluation:
            return
        if self.t0 is not None and t - self.t0 - self._hold_total >= tm.cap_s + self._cap_bonus:
            self._finish_measuring(t, "time limit")
            return
        if self._hold_for_speech(t):
            return
        el = t - self._step_t
        if s == "greet" and el >= tm.greet_s:
            self._enter("face_neutral", t, script.FACE_NEUTRAL, seconds=tm.neutral_lead_s + tm.neutral_measure_s)
        elif s == "face_reframe" and el >= tm.face_reframe_s:
            self._enter("face_neutral", t, script.FACE_NEUTRAL, seconds=tm.neutral_lead_s + tm.neutral_measure_s)
        elif s == "face_neutral":
            if self._measure_t is None and el >= tm.neutral_lead_s:
                self._measure_t = t
            elif el >= tm.neutral_lead_s + tm.neutral_measure_s:
                # Face missing for most of the neutral window: say so now rather than after the smile.
                fr = self._meter_framing(self._face_meter)
                if (fr is not None and not fr.ok and fr.seconds >= tm.face_lost_fraction * tm.neutral_measure_s
                        and self._reframe("face", fr.problem, t, "neutral")):
                    return
                self._enter("face_smile", t, script.FACE_SMILE, seconds=tm.smile_lead_s + tm.smile_measure_s)
        elif s == "face_smile":
            if self._measure_t is None and el >= tm.smile_lead_s:
                self._measure_t = t
            elif el >= tm.smile_lead_s + tm.smile_measure_s:
                res = self._read_face()
                if res.quality in REFRAMEABLE and self._face_meter is not None:
                    fr = self._meter_framing(self._face_meter)
                    problem = fr.problem if fr is not None and fr.problem else _problem_from_result("face", res)
                    if self._reframe("face", problem, t, "smile"):
                        return
                self.face = self._with_framing("face", res)
                self._emit("result", check="face", quality=self.face.quality, data=self.face)
                self._arms_t0 = t
                self._enter("arms_wait", t, script.ARMS_RAISE)
        elif s == "arms_wait":
            m = self._arm_meter
            fr = self._meter_framing(m)
            if m is not None and m.arms_up():
                self._enter("arms_hold", t, script.ARMS_HOLD, seconds=tm.arms_hold_s)
                hs = m.hold_start()
                if hs is not None:  # time the bar from when the meter saw the arms come up
                    self.countdown = (self._arms_t0 + hs + tm.arms_hold_s, tm.arms_hold_s)
            elif (fr is not None and not fr.ok and fr.seconds >= tm.reframe_persist_s
                  and el >= (tm.reframe_retry_s if self.reframes["arms"] else tm.reframe_after_s)
                  and self._reframe("arms", fr.problem, t, "wait")):
                return
            elif m is None or el >= tm.arms_wait_timeout_s:
                self._speak(script.ARMS_TIMEOUT.say)
                self._end_arms(t)
        elif s == "arms_hold":
            m = self._arm_meter
            fr = self._meter_framing(m, during_hold=True)
            if fr is not None and getattr(fr, "dropped", None):
                self._hold_dropped = fr.dropped  # an arm vanished while moving down: a finding
            # Once an arm has dropped out of view this hold is evidence: never restart it.
            if (fr is not None and not fr.ok and fr.seconds >= tm.hold_lost_grace_s and not self._hold_dropped
                    and self._reframe("arms", fr.problem, t, "hold")):
                return
            end = self.countdown[0] if self.countdown else self._step_t + tm.arms_hold_s
            if t >= end + tm.arms_hold_margin_s:
                res = self._read_arms()
                # Arms lost often enough that the hold cannot be read: one more try after
                # fixing the picture. Never when an arm came down early or vanished while
                # moving down: those are findings, and they go straight to the decision.
                if self._hold_dropped and isinstance(getattr(res, "detail", None), dict):
                    res.detail["dropped_out_of_view"] = self._hold_dropped
                if (res.quality in REFRAMEABLE and not (res.detail or {}).get("early_drop")
                        and not self._hold_dropped and m is not None):
                    fr = self._meter_framing(m, during_hold=True)
                    problem = fr.problem if fr is not None and fr.problem else _problem_from_result("arms", res)
                    if self._reframe("arms", problem, t, "hold"):
                        return
                self.arms = res
                self._speak(script.ARMS_DONE.say)
                self._end_arms(t)
        elif s == "sentence":
            if self._measure_t is None and el >= tm.sentence_lead_s:
                self._measure_t = t
                self._rec_window = (t, t + tm.sentence_record_s)
                go = script.sentence_go(self.sentence)
                self.caption = go.caption
                self.countdown = (t + tm.sentence_record_s, tm.sentence_record_s)
                self._emit("step", name="sentence", caption=go.caption, seconds=tm.sentence_record_s)
            elif self._measure_t is not None and t >= self._measure_t + tm.sentence_record_s:
                self._rec_window = None
                self._to_deciding(t)

    def _read_face(self) -> FaceResult:
        if self.face is not None:  # already an error from start()
            return self.face
        try:
            return self._face_meter.result()
        except Exception as e:  # noqa: BLE001
            log.exception("[onset] face result failed")
            return _error("face", f"{type(e).__name__}")

    def _read_arms(self) -> ArmResult:
        if self.arms is not None:  # already an error from start()
            return self.arms
        try:
            return self._arm_meter.result() if self._arm_meter is not None else _error("arms", "no meter")
        except Exception as e:  # noqa: BLE001
            log.exception("[onset] arm result failed")
            return _error("arms", type(e).__name__)

    # ------------------------------------------------------------------ framing retries

    @staticmethod
    def _meter_framing(meter: Any, **kw: Any) -> Any:
        """The meter's live framing, or None if it has none (stubs, replays) or it fails."""
        fn = getattr(meter, "framing", None) if meter is not None else None
        if fn is None:
            return None
        try:
            return fn(**kw)
        except Exception:  # noqa: BLE001 - framing is advice; never break the call over it
            log.debug("framing failed", exc_info=True)
            return None

    def _reframe(self, check: str, problem: str | None, t: float, phase: str) -> bool:
        """Tell the person what to fix and run the step again. False once the retries for this
        step are used up: the step then carries on exactly as it did before retries existed,
        so an unreadable step still ends as UNCLEAR (caregiver + 911)."""
        tm, n = self.timing, self.reframes[check]
        if n >= tm.max_reframes:
            if check not in self._framing_gave_up:
                self._framing_gave_up.add(check)
                log.info("[onset] %s framing (%s): no retries left", check, problem)
                self._emit("framing", check=check, phase=phase, problem=problem, action="give_up",
                           retry=n, max_retries=tm.max_reframes, caption=self.caption)
            return False
        self.reframes[check] = n + 1
        self._framing_last[check] = problem
        line = script.framing_line(check, problem)
        log.info("[onset] %s framing (%s, %s): retry %d of %d", check, phase, problem, n + 1, tm.max_reframes)
        self._emit("framing", check=check, phase=phase, problem=problem, action="retry",
                   retry=n + 1, max_retries=tm.max_reframes, caption=line.caption)
        if check == "arms":
            self._cap_bonus += tm.arms_wait_timeout_s + tm.arms_hold_s
            self._reset_meter(self._arm_meter)
            self._arms_t0 = t  # Frame.t restarts for the new attempt
            self._hold_dropped = None
            if phase == "hold":
                self._speak(script.ARMS_RESTART.say)
            self._enter("arms_wait", t, line)
        else:
            self._cap_bonus += (tm.face_reframe_s + tm.neutral_lead_s + tm.neutral_measure_s
                                + tm.smile_lead_s + tm.smile_measure_s)
            self._reset_meter(self._face_meter)
            self._enter("face_reframe", t, line, seconds=tm.face_reframe_s)
        return True

    @staticmethod
    def _reset_meter(meter: Any) -> None:
        if meter is not None and hasattr(meter, "reset"):
            try:
                meter.reset()
            except Exception:  # noqa: BLE001
                log.exception("[onset] meter reset failed")

    def _with_framing(self, check: str, res: Any) -> Any:
        """Note the retries in the result's detail, for decide's reasons and the logs."""
        n = self.reframes[check]
        if n and isinstance(getattr(res, "detail", None), dict):
            res.detail["framing_retries"] = n
            res.detail["framing_problem"] = self._framing_last[check]
        return res

    def _end_arms(self, t: float) -> None:
        if self.arms is None:
            self.arms = self._read_arms()
        self.arms = self._with_framing("arms", self.arms)
        self._emit("result", check="arms", quality=self.arms.quality, data=self.arms)
        self._enter("sentence", t, script.sentence_prompt(self.sentence), seconds=self.timing.sentence_lead_s)

    def _to_deciding(self, t: float) -> None:
        self._enter("deciding", t, script.DECIDING)
        self.needs_evaluation = True

    def _finish_measuring(self, t: float, reason: str) -> None:
        """Time cap or internal error: every unfinished check is NO_RESPONSE, then decide."""
        if self.face is None:
            self.face = _no_response("face", reason)
            self._emit("result", check="face", quality=self.face.quality, data=self.face)
        if self.arms is None:
            self.arms = _no_response("arms", reason)
            self._emit("result", check="arms", quality=self.arms.quality, data=self.arms)
        if self.speech is None and (self._measure_t is None or self.state != "sentence" or self.caller_gone):
            self.speech = _no_response("speech", reason)
        self._rec_window = None
        self._to_deciding(t)

    # ------------------------------------------------------------------ evaluation

    def _clip(self) -> AudioClip | None:
        if not self._audio:
            return None
        rate = self._audio[0][2]
        pcm = b"".join(p for _, p, r in self._audio if r == rate)
        return AudioClip(pcm=pcm, sample_rate=rate, t_start=self._audio[0][0] - (self.t0 or 0.0))

    async def evaluate(self) -> Decision | None:
        """Speech check, decision, baseline, caregiver text, closing line. Never raises."""
        with self._lock:
            if not self.needs_evaluation:
                return self.decision
            self.needs_evaluation = False
            clip = self._clip()
        try:
            if self.speech is None:
                if clip is None:
                    self.speech = SpeechResult(Quality.NO_RESPONSE, self.sentence, None, None, None, None,
                                               {"reason": "no caller audio"})
                else:
                    try:
                        self.speech = await asyncio.wait_for(self._speech_check(clip, self.sentence),
                                                             self.timing.speech_timeout_s)
                    except asyncio.TimeoutError:
                        self.speech = _error("speech", "speech check timed out")
                    except Exception as e:  # noqa: BLE001
                        log.exception("[onset] speech check failed")
                        self.speech = _error("speech", type(e).__name__)
            self._emit("result", check="speech", quality=self.speech.quality, data=self.speech)
            results = CheckResults(face=self.face, arms=self.arms, speech=self.speech,
                                   started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            if self.baseline is None and self.save_baseline:
                await self._first_run(results)
            else:
                await self._compare(results)
        except Exception:  # noqa: BLE001 - last line of defence: tell them to call 911
            log.exception("[onset] evaluation failed; closing as unclear")
            with self._lock:
                self._close(script.CLOSE_UNCLEAR)
        finally:
            self._close_meters()
            if not self._background:
                self._end_run("complete")
        return self.decision

    def _end_run(self, reason: str) -> None:
        if not getattr(self, "_ended", False):
            self._ended = True
            self._emit("run_ended", reason=reason)

    async def _first_run(self, results: CheckResults) -> None:
        try:
            baseline = self._decide.baseline_from([results], self.patient_id)
            self._decide.save_baseline(baseline, base_dir=self.baseline_dir)
            self.baseline_saved = True
        except Exception as e:  # noqa: BLE001 - BaselineError: some step not OK
            log.info("[onset] baseline not saved: %s", e)
            self.baseline_saved = False
        with self._lock:
            if self.baseline_saved:
                self._enter("baseline_saved", self._step_t, None, caption=script.CLOSE_BASELINE_SAVED.caption, speak=False)
                self._close(script.CLOSE_BASELINE_SAVED)
            else:
                self._close(script.CLOSE_BASELINE_FAILED)

    async def _compare(self, results: CheckResults) -> None:
        decision = self._decide.decide(results, self.baseline)
        self.decision = decision
        self._emit("decision", outcome=decision.outcome, reasons=list(decision.reasons),
                   baseline_used=self.baseline is not None)
        if decision.outcome in (Outcome.ESCALATE, Outcome.UNCLEAR):
            # The 911 line ("I am adding your caregiver to this call now") is shown and said
            # first; inviting the caregiver and the text can take ~50 s (retries, re-reads),
            # so they run in the background and report through their own events.
            with self._lock:
                self._close(script.CLOSE_ESCALATE if decision.outcome is Outcome.ESCALATE else script.CLOSE_UNCLEAR)
            task = asyncio.create_task(self._escalate(decision), name="onset-caregiver")
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        else:
            with self._lock:
                self._close(script.CLOSE_NO_CHANGE)

    async def _escalate(self, decision: Decision) -> None:
        """Bring the caregiver in (agent.py's hook: link, relay, text) or just text them."""
        try:
            text = self._decide.caregiver_summary(decision, self.patient_name)
        except Exception as e:  # noqa: BLE001
            self._emit("caregiver", text="", status="failed", detail=f"summary failed: {type(e).__name__}")
            self._end_run("complete")
            return
        try:
            if self._escalate_fn is not None:
                res = await self._escalate_fn(decision, text)
            else:
                from onset.caregiver import notify_caregiver

                res = await notify_caregiver(sink=self, summary=text, caregiver_phone=self.caregiver_phone,
                                             caregiver_name=self.caregiver_name, patient_name=self.patient_name,
                                             send=self._send, force_dry=self._force_dry)
            status, detail = res.get("status", "failed"), res.get("detail", "")
        except Exception as e:  # noqa: BLE001
            log.exception("[onset] escalation failed")
            status, detail = "failed", type(e).__name__
        self._emit("caregiver", text=text, status=status, detail=detail)
        self._end_run("complete")

    # A sink-like face for onset.actions.record / notify_caregiver: events go through _emit.
    def emit(self, type_: str, **fields: Any) -> None:
        self._emit(type_, **fields)

    # ------------------------------------------------------------------ caregiver presence

    def caregiver_update(self, status: str, detail: str = "") -> None:
        """The caregiver was invited / joined / started talking / left (from agent.py)."""
        rank = {"": 0, "failed": 0, "invited": 1, "joined": 2, "talking": 3, "left": 1}
        with self._lock:
            if status == self.caregiver_status:
                return
            if status == "invited" and rank[self.caregiver_status] >= rank["invited"]:
                return  # the caregiver was quicker than the invitation's bookkeeping
            prev, self.caregiver_status = self.caregiver_status, status
            self._emit("caregiver_join", status=status, detail=detail)
            if status == "joined" and prev in ("", "invited"):
                self.caption = script.CAREGIVER_JOINED.caption
                self._speak(script.CAREGIVER_JOINED.say)
            elif status == "talking":
                self.caption = script.CAREGIVER_TALKING.caption
                self._speak(script.CAREGIVER_TALKING.say)
            elif status == "left" and self.outcome_line is not None:
                self.caption = self.outcome_line.caption
                self._cg_frame = None

    def caregiver_frame(self, rgb: np.ndarray, t: float) -> None:
        """The caregiver's latest camera frame, drawn picture-in-picture for the caller."""
        self._cg_frame = (t, rgb)

    async def wait_background(self, timeout: float = 90.0) -> None:
        """Wait for background work (the caregiver text). Live calls need not; replays and tests do."""
        if self._background:
            await asyncio.wait(list(self._background), timeout=timeout)

    def _close(self, line: script.Line) -> None:
        self.outcome_line = line
        self._enter("closing", self._step_t, line)
        self.countdown = None
        self.done = True

    def _close_meters(self) -> None:
        for name in ("_face_meter", "_arm_meter"):
            m = getattr(self, name)
            setattr(self, name, None)
            if m is not None and hasattr(m, "close"):
                try:
                    m.close()
                except Exception:  # noqa: BLE001
                    pass

    MEASURING = ("face_reframe", "face_neutral", "face_smile", "arms_wait", "arms_hold", "sentence")

    def hangup(self, t: float) -> bool:
        """The caller hung up. Mid-check (after the greeting, before the decision), the rest
        of the check counts as not done and Onset still decides, which is then UNCLEAR at
        least, so the caregiver hears that the check-in ended early (ONSET_NOTIFY_ON_HANGUP=0
        turns this off). Returns True when a decision is now due (call `evaluate()`)."""
        with self._lock:
            self.caller_gone = True
            if self.done or self.needs_evaluation or self.state not in self.MEASURING:
                return self.needs_evaluation
            if os.environ.get("ONSET_NOTIFY_ON_HANGUP", "1").strip() == "0":
                return False
            log.info("[onset] the caller hung up during %s: deciding with what was measured", self.state)
            self._emit("hangup", step=self.state)
            self._finish_measuring(t, "the call ended before the check finished")
            return True

    def abort(self, reason: str) -> None:
        """The caller left mid-check."""
        with self._lock:
            self.done = True
            self.needs_evaluation = False
            self._end_run(reason)
            self._close_meters()

    # ------------------------------------------------------------------ rendering

    def render(self, caller: np.ndarray | None, t: float) -> np.ndarray:
        """The RGBA frame to send back: the caller's video with the meters' landmarks, the
        caption panel, countdown bar and step progress."""
        w, h = self.out_w, self.out_h
        with self._lock:
            state, caption, countdown = self.state, self.caption, self.countdown
            if caller is not None:
                src = caller if caller.shape[2] == 4 else np.dstack([caller, np.full(caller.shape[:2], 255, np.uint8)])
                src = np.array(src, copy=True)
                try:
                    if state in ("face_neutral", "face_smile") and self._face_meter is not None:
                        self._face_meter.draw(src)
                    elif state in ("arms_wait", "arms_hold") and self._arm_meter is not None:
                        live = {}
                        if state == "arms_hold" and countdown:
                            live["hold_elapsed"] = max(0.0, self.timing.arms_hold_s - (countdown[0] - t))
                        self._arm_meter.draw(src, live or None)
                except Exception:  # noqa: BLE001
                    log.debug("meter draw failed", exc_info=True)
        img = overlay.letterbox(src, w, h) if caller is not None else overlay.blank(w, h)
        pos = CAPTION_POS.get(state, "bottom")
        other = "bottom" if pos == "top" else "top"
        overlay.draw_caption(img, caption, pos, t)
        if countdown:
            t_end, secs = countdown
            overlay.countdown_bar(img, (t_end - t) / secs if secs else 0.0, other)
        prog = script.PROGRESS.get(state)
        if prog:
            overlay.composite(img, overlay.pill_layer(f"Step {prog[0]} of 3 · {prog[1]}", w, h, other))
        if self.caregiver_status in ("invited", "joined", "talking"):
            self._draw_caregiver(img, t, other)
        if self.sink is not None:
            self.sink.frame(img)
        return img


    def _draw_caregiver(self, img: np.ndarray, t: float, pill_pos: str) -> None:
        """Caregiver status pill, and their camera picture-in-picture while it is fresh."""
        try:
            label = {"invited": "Calling your caregiver…", "joined": "Caregiver joined",
                     "talking": "Caregiver on the call"}[self.caregiver_status]
            overlay.composite(img, overlay.pill_layer(label, self.out_w, self.out_h, pill_pos))
            cg = self._cg_frame
            if cg is None or t - cg[0] > 1.5:
                return
            import cv2

            h, w = img.shape[:2]
            pw = max(64, w // 4)
            src = cg[1]
            ph = int(round(pw * src.shape[0] / max(1, src.shape[1])))
            ph = min(ph, h // 2)
            pip = cv2.resize(np.ascontiguousarray(src[..., :3]), (pw, ph), interpolation=cv2.INTER_AREA)
            x0, y0 = w - pw - 12, (h - ph) // 2
            img[y0 - 2:y0 + ph + 2, x0 - 2:x0 + pw + 2, :3] = 255
            img[y0:y0 + ph, x0:x0 + pw, :3] = pip
            if img.shape[2] == 4:
                img[y0 - 2:y0 + ph + 2, x0 - 2:x0 + pw + 2, 3] = 255
        except Exception:  # noqa: BLE001 - the picture is optional
            log.debug("caregiver draw failed", exc_info=True)


def _small(d: dict) -> dict:
    """Keep live metrics small and JSON-friendly."""
    out = {}
    for k, v in d.items():
        if isinstance(v, (bool, str)) or v is None:
            out[k] = v
        elif isinstance(v, (int, float, np.floating, np.integer)):
            out[k] = round(float(v), 4)
    return out
