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

Safety: outcomes come only from onset.decide (rules). UNCLEAR and ESCALATE both tell the
person to call 911 now and text the caregiver. No line says "you're fine" (see onset/script.py).
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
    cap_s: float = 90.0  # from start(); everything unfinished is NO_RESPONSE
    live_interval_s: float = 0.2  # live metric events, ~5 per second


STEP_ORDER = ["greet", "face_neutral", "face_smile", "arms_wait", "arms_hold", "sentence",
              "deciding", "closing"]

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
        if caregiver_send is None:
            caregiver_send = os.environ.get("ONSET_CAREGIVER_SEND") == "1"
        self.caregiver_send = bool(caregiver_send and self.caregiver_phone)
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
        self._step_t = 0.0  # when the current step began
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
        self.events: list[dict] = []  # a copy of everything emitted (tests, logs)

    # ------------------------------------------------------------------ output helpers

    def _emit(self, type_: str, **fields: Any) -> None:
        self.events.append({"type": type_, **fields})
        if self.sink is not None:
            try:
                self.sink.emit(type_, **fields)
            except Exception:  # noqa: BLE001
                log.exception("[onset] event sink failed")

    def _speak(self, text: str) -> None:
        if not text:
            return
        if self._say_fn is None:
            self._emit("say", text=text, audio=None)
            return
        try:
            self._say_fn(text)  # the speaker emits the `say` event with its audio file
        except Exception:  # noqa: BLE001 - never break the flow over audio
            log.exception("[onset] say failed")
            self._emit("say", text=text, audio=None)

    def _enter(self, name: str, t: float, line: script.Line | None = None, *,
               seconds: float | None = None, caption: str | None = None, speak: bool = True) -> None:
        self.state = name
        self._step_t = t
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
            self._speak(line.say)

    # ------------------------------------------------------------------ inputs

    def start(self, t: float) -> None:
        with self._lock:
            if self.state != "idle":
                return
            self.t0 = t
            self._emit("run_started", run_id=self.run_id, source=self.source, patient_id=self.patient_id,
                       has_baseline=self.baseline is not None, sentence=self.sentence)
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
        if self.t0 is not None and t - self.t0 >= tm.cap_s:
            self._finish_measuring(t, "time limit")
            return
        el = t - self._step_t
        if s == "greet" and el >= tm.greet_s:
            self._enter("face_neutral", t, script.FACE_NEUTRAL, seconds=tm.neutral_lead_s + tm.neutral_measure_s)
        elif s == "face_neutral":
            if self._measure_t is None and el >= tm.neutral_lead_s:
                self._measure_t = t
            elif el >= tm.neutral_lead_s + tm.neutral_measure_s:
                self._enter("face_smile", t, script.FACE_SMILE, seconds=tm.smile_lead_s + tm.smile_measure_s)
        elif s == "face_smile":
            if self._measure_t is None and el >= tm.smile_lead_s:
                self._measure_t = t
            elif el >= tm.smile_lead_s + tm.smile_measure_s:
                self.face = self._read_face()
                self._emit("result", check="face", quality=self.face.quality, data=self.face)
                self._arms_t0 = t
                self._enter("arms_wait", t, script.ARMS_RAISE)
        elif s == "arms_wait":
            m = self._arm_meter
            if m is not None and m.arms_up():
                self._enter("arms_hold", t, script.ARMS_HOLD, seconds=tm.arms_hold_s)
                hs = m.hold_start()
                if hs is not None:  # time the bar from when the meter saw the arms come up
                    self.countdown = (self._arms_t0 + hs + tm.arms_hold_s, tm.arms_hold_s)
            elif m is None or el >= tm.arms_wait_timeout_s:
                self._speak(script.ARMS_TIMEOUT.say)
                self._end_arms(t)
        elif s == "arms_hold":
            end = self.countdown[0] if self.countdown else self._step_t + tm.arms_hold_s
            if t >= end + tm.arms_hold_margin_s:
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

    def _end_arms(self, t: float) -> None:
        if self.arms is None:
            try:
                self.arms = self._arm_meter.result() if self._arm_meter is not None else _error("arms", "no meter")
            except Exception as e:  # noqa: BLE001
                log.exception("[onset] arm result failed")
                self.arms = _error("arms", type(e).__name__)
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
        if self.speech is None and (self._measure_t is None or self.state != "sentence"):
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
            if self.baseline is None:
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
        self._emit("decision", outcome=decision.outcome, reasons=list(decision.reasons), baseline_used=True)
        if decision.outcome in (Outcome.ESCALATE, Outcome.UNCLEAR):
            # The 911 line is shown and said first; the text can take ~50 s (retries,
            # re-reads), so it runs in the background and reports through its own event.
            with self._lock:
                self._close(script.CLOSE_ESCALATE if decision.outcome is Outcome.ESCALATE else script.CLOSE_UNCLEAR)
            task = asyncio.create_task(self._text_caregiver(decision), name="onset-caregiver")
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        else:
            with self._lock:
                self._close(script.CLOSE_NO_CHANGE)

    async def _text_caregiver(self, decision: Decision) -> None:
        try:
            text = self._decide.caregiver_summary(decision, self.patient_name)
        except Exception as e:  # noqa: BLE001
            self._emit("caregiver", text="", status="failed", detail=f"summary failed: {type(e).__name__}")
            self._end_run("complete")
            return
        phone = self.caregiver_phone or "+15555550100"  # placeholder for a dry run
        dry = not self.caregiver_send
        try:
            res = await self._send(phone, text, dry)
            status = res.get("status", "failed")
            status = "dry_run" if status == "dry_run" else ("sent" if res.get("accepted") else "failed")
            detail = res.get("detail") or res.get("error") or ""
        except Exception as e:  # noqa: BLE001
            status, detail = "failed", type(e).__name__
        if dry and not self.caregiver_phone:
            detail = "dry run: no caregiver phone set (ONSET_CAREGIVER_PHONE)"
        self._emit("caregiver", text=text, status=status, detail=detail)
        self._end_run("complete")

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
        layer, _ = overlay.caption_layer(caption, w, h, pos)
        overlay.composite(img, layer)
        if countdown:
            t_end, secs = countdown
            overlay.countdown_bar(img, (t_end - t) / secs if secs else 0.0, other)
        prog = script.PROGRESS.get(state)
        if prog:
            overlay.composite(img, overlay.pill_layer(f"Step {prog[0]} of 3 · {prog[1]}", w, h, other))
        if self.sink is not None:
            self.sink.frame(img)
        return img


def _small(d: dict) -> dict:
    """Keep live metrics small and JSON-friendly."""
    out = {}
    for k, v in d.items():
        if isinstance(v, (bool, str)) or v is None:
            out[k] = v
        elif isinstance(v, (int, float, np.floating, np.integer)):
            out[k] = round(float(v), 4)
    return out
