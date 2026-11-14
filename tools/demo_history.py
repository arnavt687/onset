"""Write about two weeks of synthetic daily check-ins, so the history report and the dashboard's
Trend panel have something to show without real calls.

    ~/onset/.venv/bin/python tools/demo_history.py [--runs DIR] [--patient demo-patient] [--days 14]

Each day becomes runs/demo-history-<patient>-<YYYYMMDD>/events.jsonl, in the same format the
call flow archives (onset/events.py), with the decision made by the real onset.decide rules.
No frames, audio, faces or voices are written. Re-running replaces the same days.

The story the numbers tell (deterministic, no randomness):
    day 1            the baseline check-in
    every day        the left arm drifts a little more, and speech slows a little, but no single
                     check-in reaches its change threshold (the "gradual decline" the report flags)
    one day          a long pause in the sentence: a change, so ESCALATE
    another day      the arms were only partly in view: UNCLEAR
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from onset import decide as onset_decide  # noqa: E402
from onset.events import RUNS, _plain  # noqa: E402
from onset.types import ArmResult, Baseline, CheckResults, FaceResult, Quality, SpeechResult  # noqa: E402

PATIENT = "demo-patient"  # the same person tools/demo_events.py plays
SENTENCE = "The sky is blue in Cincinnati."
PREFIX = "demo-history-"


def _wobble(day: int, scale: float) -> float:
    """Small, fixed day-to-day noise (no random numbers, so the demo is reproducible)."""
    return scale * math.sin(day * 2.3 + 0.7)


def day_numbers(day: int, days: int) -> dict:
    """The measured numbers for day `day` (0 = the baseline day) of `days`."""
    frac = day / max(1, days - 1)
    lift_left = 0.200 + _wobble(day, 0.004)
    asym = 0.06 + _wobble(day + 3, 0.012)  # the right corner is usually a little weaker
    return {
        "lift": (lift_left, lift_left * (1.0 - asym)),
        "drift": (0.040 + 0.046 * frac + _wobble(day, 0.003), 0.020 + _wobble(day + 5, 0.002)),
        "wpm": 150.0 - 22.0 * frac + _wobble(day + 1, 1.5),
        "pause": 0.18 + _wobble(day + 2, 0.05),
        "wer": 0.0,
        "arms_quality": Quality.OK,
    }


def results_for(n: dict, when: datetime) -> CheckResults:
    ll, lr = n["lift"]
    hi, lo = max(ll, lr), min(ll, lr)
    asym = 0.0 if hi <= 0 else 1.0 - lo / hi
    face = FaceResult(Quality.OK, round(asym, 4), "left" if ll < lr else "right", 42,
                      {"lift_left": round(ll, 4), "lift_right": round(lr, 4), "synthetic": True})
    dl, dr = n["drift"]
    if n["arms_quality"] is Quality.OK:
        arms = ArmResult(Quality.OK, round(dl, 4), round(dr, 4), round(abs(dl - dr), 4),
                         "left" if dl > dr else "right", 10.0, 120, {"synthetic": True})
    else:
        arms = ArmResult(n["arms_quality"], None, None, None, None, 6.4, 40,
                         {"reason": "arms cut off by the frame edge", "synthetic": True})
    speech = SpeechResult(Quality.OK, SENTENCE, SENTENCE.rstrip("."), n["wer"], round(n["wpm"], 1),
                          round(n["pause"], 2), {"synthetic": True})
    return CheckResults(face=face, arms=arms, speech=speech, started_at=when.isoformat(timespec="seconds"))


def baseline_from_day0(patient: str, r: CheckResults) -> Baseline:
    return Baseline(
        patient_id=patient, recorded_at=r.started_at,
        face_asymmetry=r.face.asymmetry, arm_drift_difference=r.arms.drift_difference,
        speech_word_error_rate=r.speech.word_error_rate, speech_words_per_minute=r.speech.words_per_minute,
        sentence=SENTENCE, samples=1, face_weaker_side=r.face.weaker_side,
        face_lift_left=r.face.detail["lift_left"], face_lift_right=r.face.detail["lift_right"],
        arm_drift_left=r.arms.left_drift, arm_drift_right=r.arms.right_drift, arm_weaker_side=r.arms.weaker_side,
        speech_longest_pause_s=r.speech.longest_pause_s,
    )


def _write_run(run_dir: Path, events: list[dict], end: datetime) -> None:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, mode=0o700)
    path = run_dir / "events.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(_plain(e)) + "\n")
    os.chmod(path, 0o600)
    ts = end.timestamp()
    os.utime(path, (ts, ts))  # the archive's own time matches the check-in, as for a real run


def write_demo(runs: Path = RUNS, patient: str = PATIENT, days: int = 14,
               end_day: datetime | None = None, escalate_day: int | None = None,
               unclear_day: int | None = None) -> list[Path]:
    """Write `days` daily runs ending on `end_day` (default today). Returns the run dirs."""
    days = max(3, int(days))
    escalate_day = escalate_day if escalate_day is not None else max(1, days * 3 // 7)
    unclear_day = unclear_day if unclear_day is not None else max(2, days * 9 // 14)
    last = (end_day or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last = last.replace(hour=9, minute=30, second=0, microsecond=0)
    runs = Path(runs)
    runs.mkdir(parents=True, exist_ok=True)
    baseline: Baseline | None = None
    out: list[Path] = []
    for day in range(days):
        when = last - timedelta(days=days - 1 - day) + timedelta(minutes=7 * (day % 4))
        n = day_numbers(day, days)
        if day == escalate_day:
            n["pause"] = 1.9  # one long pause mid-sentence: a change on its own
        if day == unclear_day:
            n["arms_quality"] = Quality.PARTIAL
        r = results_for(n, when)
        run_id = f"{PREFIX}{patient}-{when:%Y%m%d}"
        ev: list[dict] = [{"t": 0.0, "type": "run_started", "run_id": run_id, "source": "demo",
                           "patient_id": patient, "has_baseline": baseline is not None, "sentence": SENTENCE,
                           "baseline": asdict(baseline) if baseline else None,
                           "started_at": r.started_at, "synthetic": True},
                          {"t": 0.1, "type": "step", "name": "greet", "caption": "", "seconds": 5.0},
                          {"t": 14.0, "type": "result", "check": "face", "quality": r.face.quality, "data": r.face},
                          {"t": 31.0, "type": "result", "check": "arms", "quality": r.arms.quality, "data": r.arms},
                          {"t": 47.0, "type": "result", "check": "speech", "quality": r.speech.quality,
                           "data": r.speech}]
        if baseline is None:
            baseline = baseline_from_day0(patient, r)
            ev.append({"t": 47.5, "type": "step", "name": "baseline_saved", "caption": "", "seconds": None})
        else:
            d = onset_decide.decide(r, baseline)
            ev.append({"t": 47.5, "type": "decision", "outcome": d.outcome, "reasons": list(d.reasons),
                       "baseline_used": True})
        ev.append({"t": 49.0, "type": "run_ended", "reason": "complete"})
        run_dir = runs / run_id
        _write_run(run_dir, ev, when + timedelta(seconds=49))
        out.append(run_dir)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Write synthetic daily check-in archives for the history demo.")
    ap.add_argument("--runs", type=Path, default=RUNS, help="runs directory (default: %(default)s)")
    ap.add_argument("--patient", default=PATIENT, help="patient id (default: %(default)s)")
    ap.add_argument("--days", type=int, default=14, help="how many daily check-ins (default: %(default)s)")
    args = ap.parse_args(argv)
    if not onset_decide._PATIENT_ID_RE.fullmatch(args.patient):
        ap.error("patient id must be 1-128 letters, digits, '_', '+', '-' or '.'")
    dirs = write_demo(args.runs, args.patient, args.days)
    print(f"Wrote {len(dirs)} synthetic check-ins for {args.patient} under {Path(args.runs).resolve()}")
    print(f"Report:  python tools/history_report.py --patient {args.patient} --runs {args.runs} --out history.html")


if __name__ == "__main__":
    main()
