"""Write a synthetic check-in run, in real time, so the dashboard works without a call.

    ~/onset/.venv/bin/python tools/demo_events.py --outcome escalate [--runs DIR] [--speed 1]

It writes exactly what a live call writes (through onset.events.EventSink): events.jsonl
with every event type, a moving test frame at ~5 fps and a short generated tone WAV for each
spoken line. The decision and the caregiver text come from onset.decide, so the reasons on
screen are the real wording. No person appears in any frame.

Outcomes
    escalate    baseline on file; the smile, arm hold and sentence all changed
    unclear     baseline on file; the arms were only partly in view
    no_change   baseline on file; every number within its threshold of the baseline
    first_run   no baseline yet: the check-in is recorded as the person's usual one
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
import threading
import time
import wave
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from onset import decide as onset_decide  # noqa: E402
from onset.events import EventSink  # noqa: E402
from onset.types import (  # noqa: E402
    ArmResult,
    Baseline,
    CheckResults,
    FaceResult,
    Quality,
    SpeechResult,
)

SENTENCE = "The sky is blue in Cincinnati."
PATIENT = "demo-patient"
PATIENT_NAME = "Alex"
OUTCOMES = ("escalate", "unclear", "no_change", "first_run")

# The same words as onset/script.py (the flow's lines), in ASCII for the test frame.
GREET = ("Hello, this is Onset, your check-in helper. This takes about one minute. Let's begin.",
         "Hello, this is Onset. A one-minute check-in.")
NEUTRAL = ("Please look at the camera, and relax your face.", "Look at the camera. Relax your face.")
SMILE = ("Now give me a big smile, and hold it.", "Now give a big smile. Hold it.")
RAISE = ("Now raise both arms straight out in front of you, and keep them up.", "Raise both arms straight out. Keep them up.")
HOLD = ("Good. Keep both arms up until the bar is full.", "Keep both arms up.")
DONE = ("Thank you. You can lower your arms.", "You can lower your arms.")
DECIDING = ("Thank you. One moment.", "Thank you. One moment.")
CLOSE = {
    "escalate": ("Some of your results were different from your usual check-in. Please call 9 1 1 now. "
                 "I am texting your caregiver.", "Please call 911 now. Your caregiver is being texted."),
    "unclear": ("I could not check everything clearly this time. To be safe, please call 9 1 1 now. "
                "I am texting your caregiver.", "I could not check clearly. Please call 911 now."),
    "no_change": ("I didn't notice any change from your usual check-in. If you feel unwell at any time, "
                  "call 9 1 1. Goodbye.", "No change from your usual check-in. If you feel unwell, call 911."),
    "first_run": ("Thank you. I've recorded your usual check-in, so I can compare next time. If you feel "
                  "unwell at any time, call 9 1 1. Goodbye.", "Your usual check-in is recorded. If you feel unwell, call 911."),
}


def asym_index(left: float, right: float) -> float:
    hi, lo = max(left, right), min(left, right)
    return 0.0 if hi <= 0 else 1.0 - max(lo, 0.0) / hi


# --------------------------------------------------------------------------- scenarios


def scenario(outcome: str) -> dict:
    """Final numbers for each check, chosen to land on the requested outcome."""
    s = {
        "baseline": Baseline(patient_id=PATIENT,
                             recorded_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds"),
                             face_asymmetry=0.06, arm_drift_difference=0.03, speech_word_error_rate=0.0,
                             speech_words_per_minute=150.0, sentence=SENTENCE, samples=1),
        "lift": (0.150, 0.158), "drift": (0.035, 0.010), "arms_quality": Quality.OK, "arms_partial": False,
        "transcript": "The sky is blue in Cincinnati", "wer": 0.0, "wpm": 148.0, "pause": 0.45,
    }
    if outcome == "escalate":
        s.update(lift=(0.085, 0.165), drift=(0.145, 0.020), transcript="the sky is... blue in sinsinnati",
                 wer=1 / 6, wpm=96.0, pause=1.8)
    elif outcome == "unclear":
        s.update(lift=(0.148, 0.160), arms_quality=Quality.PARTIAL, arms_partial=True)
    elif outcome == "first_run":
        s["baseline"] = None
    return s


def results_for(s: dict) -> CheckResults:
    ll, lr = s["lift"]
    weaker_face = "left" if ll < lr else "right"
    face = FaceResult(Quality.OK, asym_index(ll, lr), weaker_face, 42,
                      {"lift_left": ll, "lift_right": lr, "synthetic": True})
    dl, dr = s["drift"]
    if s["arms_quality"] is Quality.OK:
        arms = ArmResult(Quality.OK, dl, dr, abs(dl - dr), "left" if dl > dr else "right", 10.0, 50,
                         {"synthetic": True})
    else:
        arms = ArmResult(s["arms_quality"], None, None, None, None, 6.2, 31,
                         {"reason": "arms cut off by the frame edge", "synthetic": True})
    speech = SpeechResult(Quality.OK, SENTENCE, s["transcript"], s["wer"], s["wpm"], s["pause"], {"synthetic": True})
    return CheckResults(face=face, arms=arms, speech=speech,
                        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


# --------------------------------------------------------------------------- media


def write_tone(path: Path, text: str, rate: int = 22050) -> None:
    """A soft two-note chime, longer for longer lines (0.5 to 1.4 s)."""
    seconds = min(1.4, 0.5 + 0.008 * len(text))
    t = np.arange(int(seconds * rate)) / rate
    half = len(t) // 2
    freq = np.where(np.arange(len(t)) < half, 523.25, 659.25)
    env = np.minimum(1.0, np.minimum(t / 0.03, (seconds - t) / 0.15))
    wav = (np.sin(2 * np.pi * freq * t) * env * 0.25 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(wav.tobytes())


class FrameMaker:
    """A moving test frame: stripes, a cartoon face and arms that follow the scenario."""

    W, H = 640, 360

    def __init__(self) -> None:
        self.step = "greet"
        self.caption = ""
        self.lift = (0.0, 0.0)
        self.drift = (0.0, 0.0)
        self.arms_up = False
        self.partial = False
        self.n = 0

    def draw(self) -> np.ndarray:
        import cv2

        W, H = self.W, self.H
        self.n += 1
        x = np.arange(W)[None, :]
        y = np.arange(H)[:, None]
        stripes = ((x + y + self.n * 6) // 24) % 2
        img = np.empty((H, W, 3), np.uint8)
        img[...] = (38, 44, 58)
        img[stripes.astype(bool)] = (44, 51, 67)
        cx, cy = W // 2 + int(6 * math.sin(self.n / 9)), 150
        # body and arms
        cv2.ellipse(img, (cx, cy + 150), (110, 90), 0, 180, 360, (90, 110, 150), -1)
        if self.step.startswith("arms") and self.arms_up:
            for side, sign in ((0, 1), (1, -1)):  # the person's left is on the image right
                sx = cx + sign * 85
                drop = int(self.drift[side] * 420)
                end = (sx + sign * 150, cy + 95 + drop)
                if self.partial and side == 0:
                    end = (W + 40, cy + 95 + drop)
                cv2.line(img, (sx, cy + 105), end, (200, 175, 140), 22, cv2.LINE_AA)
        # head
        cv2.circle(img, (cx, cy), 70, (200, 175, 140), -1, cv2.LINE_AA)
        for ex in (-25, 25):
            cv2.circle(img, (cx + ex, cy - 15), 7, (40, 40, 50), -1, cv2.LINE_AA)
        # mouth: the person's left corner is on the image right
        ll, lr = self.lift
        left_c = (cx + 28, cy + 30 - int(ll * 140))
        right_c = (cx - 28, cy + 30 - int(lr * 140))
        mid = (cx, cy + 38)
        pts = np.array([right_c, mid, left_c], np.int32)
        cv2.polylines(img, [pts], False, (60, 30, 40), 4, cv2.LINE_AA)
        # labels, caption and frame counter
        cv2.putText(img, "SYNTHETIC TEST FRAME", (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (246, 166, 35), 2, cv2.LINE_AA)
        cv2.putText(img, f"frame {self.n}", (W - 120, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 210), 1, cv2.LINE_AA)
        if self.caption:
            cv2.rectangle(img, (0, H - 62), (W, H), (0, 0, 0), -1)
            text = self.caption.replace("“", '"').replace("”", '"').replace("’", "'")
            text = text.encode("ascii", "replace").decode()
            for i, line in enumerate(_wrap(text, 46)[:2]):
                cv2.putText(img, line, (14, H - 38 + i * 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        return img


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width) or [""]


# --------------------------------------------------------------------------- the run


class Run:
    def __init__(self, outcome: str, runs: Path, speed: float, caregiver: str) -> None:
        self.outcome, self.speed, self.caregiver = outcome, max(0.05, speed), caregiver
        self.s = scenario(outcome)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"demo-{outcome}-{stamp}"
        self.sink = EventSink(self.run_id, "replay", root=runs)
        self.fm = FrameMaker()
        self._stop = threading.Event()
        self._frames = threading.Thread(target=self._frame_loop, daemon=True)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds / self.speed)

    def _frame_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sink.frame(self.fm.draw())
            except Exception:  # noqa: BLE001
                pass
            # EventSink writes at most every 0.2 s of wall time; offer frames a little faster.
            self._stop.wait(0.1)

    def say(self, text: str) -> None:
        path, rel = self.sink.audio_path()
        try:
            write_tone(path, text)
        except OSError:
            rel = None
        self.sink.emit("say", text=text, audio=rel)

    def step(self, name: str, line: tuple[str, str] | None, seconds: float | None = None, caption: str | None = None) -> None:
        self.fm.step = name
        self.fm.caption = caption if caption is not None else (line[1] if line else "")
        self.sink.emit("step", name=name, caption=self.fm.caption, seconds=seconds)
        if line and line[0]:
            self.say(line[0])

    def live_ramp(self, step: str, seconds: float, make) -> None:
        n = max(1, int(seconds / 0.2))
        for i in range(n):
            frac = (i + 1) / n
            self.sink.emit("live", step=step, metrics=make(frac))
            self.wait(0.2)

    def play(self, hold_end: float) -> None:
        s, sink = self.s, self.sink
        self._frames.start()
        b = s["baseline"]
        sink.emit("run_started", run_id=self.run_id, source="replay", patient_id=PATIENT,
                  has_baseline=b is not None, sentence=SENTENCE,
                  # Not in the events.py contract (yet): the baseline numbers for the meters.
                  # Without it the dashboard asks the server for baselines/<patient_id>.json.
                  baseline=asdict(b) if b is not None else None)
        self.step("greet", GREET, seconds=5.0)
        self.wait(5.0)

        # Face: neutral, then smile with the corners lifting.
        self.step("face_neutral", NEUTRAL, seconds=4.5)
        self.wait(2.5)
        self.live_ramp("face_neutral", 2.0, lambda f: {"phase": "neutral", "face": True, "yaw": 2.1, "roll": -0.8})
        self.step("face_smile", SMILE, seconds=4.5)
        self.wait(1.5)
        ll, lr = s["lift"]
        rng = np.random.default_rng(1)

        def face_live(f: float) -> dict:
            k = min(1.0, f * 1.6)
            a, c = ll * k + rng.normal(0, 0.004), lr * k + rng.normal(0, 0.004)
            self.fm.lift = (a, c)
            return {"phase": "smile", "face": True, "lift_left": round(a, 4), "lift_right": round(c, 4),
                    "asym": round(asym_index(a, c), 4)}

        self.live_ramp("face_smile", 3.0, face_live)
        results = results_for(s)
        sink.emit("result", check="face", quality=results.face.quality, data=results.face)
        self.fm.lift = (0.0, 0.0)

        # Arms: wait for both arms, then the 10 s hold with drift growing.
        self.step("arms_wait", RAISE)
        self.live_ramp("arms_wait", 2.4, lambda f: {"arms_up": f > 0.7, "hold_elapsed": 0.0,
                                                    "left_drift": None, "right_drift": None})
        self.fm.arms_up, self.fm.partial = True, s["arms_partial"]
        self.step("arms_hold", HOLD, seconds=10.0)
        dl, dr = s["drift"]

        def arms_live(f: float) -> dict:
            el = f * 10.0
            if s["arms_partial"] and int(el * 5) % 7 in (0, 1):
                return {"arms_up": False, "hold_elapsed": round(el, 2), "left_drift": None, "right_drift": None}
            a, c = dl * f + rng.normal(0, 0.003), dr * f + rng.normal(0, 0.003)
            self.fm.drift = (a, c)
            return {"arms_up": True, "hold_elapsed": round(el, 2),
                    "left_drift": round(a, 4) if el >= 1.0 else None, "right_drift": round(c, 4) if el >= 1.0 else None}

        self.live_ramp("arms_hold", 10.5, arms_live)
        self.say(DONE[0])
        self.fm.arms_up, self.fm.drift = False, (0.0, 0.0)
        sink.emit("result", check="arms", quality=results.arms.quality, data=results.arms)

        # Sentence: the prompt, then "say it now" while the caller speaks.
        self.step("sentence", (f"Last one. Please repeat this sentence after me: {SENTENCE}",
                               f"Get ready to say: “{SENTENCE}”"), seconds=6.0)
        self.wait(6.0)
        self.step("sentence", None, seconds=7.0, caption=f"Say it now: “{SENTENCE}”")
        self.wait(7.0)
        self.step("deciding", DECIDING)
        self.wait(2.0)
        sink.emit("result", check="speech", quality=results.speech.quality, data=results.speech)

        # Decision (or, on a first call, the baseline), the closing line and the caregiver text.
        if b is None:
            self.step("baseline_saved", None, caption=CLOSE["first_run"][1])
            self.step("closing", CLOSE["first_run"])
            sink.emit("run_ended", reason="complete")
        else:
            d = onset_decide.decide(results, b)
            sink.emit("decision", outcome=d.outcome, reasons=list(d.reasons), baseline_used=True)
            key = {"escalate": "escalate", "unclear": "unclear"}.get(d.outcome.value, "no_change")
            self.step("closing", CLOSE[key])
            sink.emit("run_ended", reason="complete")
            if key != "no_change":
                self.wait(1.5)
                text = onset_decide.caregiver_summary(d, PATIENT_NAME)
                detail = {"dry_run": "dry run: no caregiver phone set (ONSET_CAREGIVER_PHONE)",
                          "sent": "accepted by Chert", "failed": "Chert returned 503: line busy"}.get(self.caregiver, "")
                sink.emit("caregiver", text=text, status=self.caregiver, detail=detail)
        self.wait(hold_end)

    def close(self, keep_archive: bool) -> None:
        self._stop.set()
        if self._frames.is_alive():
            self._frames.join(timeout=2)
        self.sink.close("caller hung up")
        if not keep_archive:
            shutil.rmtree(self.sink.archive, ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Write a synthetic Onset run for the dashboard.")
    ap.add_argument("--outcome", choices=OUTCOMES, default="escalate")
    ap.add_argument("--runs", type=Path, default=REPO_ROOT / "runs", help="runs directory (writes <runs>/live)")
    ap.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast (default: real time)")
    ap.add_argument("--caregiver", choices=("dry_run", "sent", "failed"), default="dry_run",
                    help="the caregiver status to show (nothing is ever sent)")
    ap.add_argument("--hold-end", type=float, default=6.0, help="seconds to keep the last frame live after the end")
    ap.add_argument("--keep-archive", action="store_true", help="keep the runs/<run-id> copy EventSink makes")
    args = ap.parse_args(argv)
    run = Run(args.outcome, args.runs, args.speed, args.caregiver)
    print(f"demo run {run.run_id} -> {run.sink.live}", flush=True)
    try:
        run.play(args.hold_end)
    except KeyboardInterrupt:
        pass
    finally:
        run.close(args.keep_archive)
    print("done", flush=True)


if __name__ == "__main__":
    main()
