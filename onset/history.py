"""Longitudinal check-in history: every archived run for one person, and its trends.

This module only reads what the call flow already archives after each check-in
(`onset/events.py`, `EventSink.close`):

    runs/<run_id>/events.jsonl      run_started, result (per step), decision, ... (see events.py)
    runs/<run_id>/frame.jpg         the last annotated agent frame
    runs/<run_id>/audio/*.wav       the agent's spoken lines
    recordings/<run_id>/            the caller's own video and audio, only with ONSET_RECORD=1

It never writes, never changes the call flow, and never raises on a bad archive: a run that
cannot be read, or that never reached an outcome, is skipped and counted.

`load_history(patient_id)` gives a `History`:

- one `CheckIn` per finished check-in, oldest first: when, the outcome, the key numbers
  (smile asymmetry, arm drift left / right / difference, word error rate, words per minute,
  longest pause) and quality flags. A number from a step whose quality was not OK is left
  out, exactly as `onset/decide.py` never uses it;
- per-metric trends over a rolling window (default 90 days, starting at the first
  check-in): least-squares slope, change against the baseline, the last 7 days against the
  7 days before, and how many check-ins reached the change threshold;
- counts of each outcome, and a "gradual decline" flag for a metric that moves steadily
  toward its `decide.py` threshold although no single check-in reached it;
- a plain-language summary for the care team. It describes measurements only; it never
  names a condition or says the person is well.

The trend settings are explained in docs/thresholds.md ("Longitudinal trends").
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from onset.decide import _PATIENT_ID_RE, DEFAULT_THRESHOLDS, Thresholds, load_baseline
from onset.types import Baseline

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS = Path(os.environ.get("ONSET_RUNS_DIR", REPO_ROOT / "runs"))
DEFAULT_BASELINES = REPO_ROOT / "baselines"
DEFAULT_RECORDINGS = Path(os.environ.get("ONSET_RECORD_DIR") or REPO_ROOT / "recordings")

# runs/live is a working copy of whichever run is in progress (or finished last); its
# archive is runs/<run_id>, so reading both would count one check-in twice.
_NOT_RUNS = frozenset({"live"})
_MAX_EVENTS_BYTES = 32 * 1024 * 1024
_EPS = 1e-9  # a value exactly at a threshold counts as reaching it, as in decide.py

OUTCOMES = ("escalate", "unclear", "no_change_detected", "baseline_recorded")
OUTCOME_LABELS = {
    "escalate": "Change detected",
    "unclear": "Unclear (not measured clearly)",
    "no_change_detected": "No change detected",
    "baseline_recorded": "Baseline recorded",
}
QUALITY_LABELS = {
    "no_subject": "not in view",
    "partial": "only partly in view",
    "too_dark": "too dark",
    "motion": "too much movement",
    "no_response": "step not done",
    "error": "could not be completed",
}
_STEP_LABELS = {"face": "smile", "arms": "arms", "speech": "sentence"}


@dataclass(frozen=True)
class TrendSettings:
    """How the trends are computed. Prototype values, like the decision thresholds."""

    window_days: float = 90.0  # rolling window; starts at the first check-in
    compare_days: float = 7.0  # "last 7 days vs the 7 days before"
    # Gradual decline: at least this many measured check-ins in the window ...
    min_points: int = 5
    # ... a least-squares line whose correlation with time is at least this strong ...
    min_correlation: float = 0.6
    # ... whose fitted value at the latest check-in has covered at least this fraction of the
    # distance from the baseline to the threshold, in the worse direction ...
    min_progress: float = 0.5
    # ... while no check-in in the window reached the threshold.


DEFAULT_SETTINGS = TrendSettings()


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    worse: str  # "up": larger is worse; "down": smaller is worse
    digits: int


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("face_asymmetry", "Smile asymmetry", "index, 0 = even", "up", 2),
    MetricSpec("arm_drift_left", "Left arm drift", "shoulder widths", "up", 2),
    MetricSpec("arm_drift_right", "Right arm drift", "shoulder widths", "up", 2),
    MetricSpec("arm_drift_difference", "Arm drift difference", "shoulder widths", "up", 2),
    MetricSpec("speech_wer", "Word error rate", "fraction of words", "up", 2),
    MetricSpec("words_per_minute", "Speaking rate", "words per minute", "down", 0),
    MetricSpec("longest_pause_s", "Longest pause", "seconds", "up", 2),
)
METRIC_KEYS = tuple(m.key for m in METRICS)
_SPECS = {m.key: m for m in METRICS}


# --------------------------------------------------------------------------- helpers


def _num(value: object) -> float | None:
    """A finite float, or None (None, bool, str, NaN, inf, ...)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _parse_time(value: object) -> datetime | None:
    """ISO 8601 text or epoch seconds -> an aware UTC datetime."""
    try:
        if isinstance(value, str) and value.strip():
            t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        elif _num(value) is not None:
            t = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds") if t else None


def valid_patient_id(patient_id: object) -> bool:
    """The same rule as decide.py's baseline file names."""
    return isinstance(patient_id, str) and bool(_PATIENT_ID_RE.fullmatch(patient_id))


def _baseline_dict(value: object) -> dict | None:
    """A Baseline (or the dict run_started carries) -> {field: number or side}, or None."""
    if isinstance(value, Baseline):
        value = asdict(value)
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("face_asymmetry", "arm_drift_difference", "speech_word_error_rate", "speech_words_per_minute",
                "face_lift_left", "face_lift_right", "arm_drift_left", "arm_drift_right",
                "speech_longest_pause_s", "samples"):
        out[key] = _num(value.get(key))
    recorded = _parse_time(value.get("recorded_at"))
    out["recorded_at"] = _iso(recorded)
    # A baseline without its four core numbers cannot anchor anything.
    core = ("face_asymmetry", "arm_drift_difference", "speech_word_error_rate", "speech_words_per_minute")
    if all(out[k] is None for k in core):
        return None
    return out


def reference_and_limit(key: str, baseline: dict | None, t: Thresholds = DEFAULT_THRESHOLDS
                        ) -> tuple[float | None, float | None, str]:
    """(baseline value, the value at which decide.py counts a change, the rule in words).

    The limit follows decide.py's main per-metric rule. Without a baseline only the
    absolute rules (arm drift, the pause fallback) give a limit.
    """
    b = baseline or {}
    if key == "face_asymmetry":
        ref = _num(b.get("face_asymmetry"))
        inc = t.face_asymmetry_increase
        return ref, (ref + inc if ref is not None else None), f"baseline + {inc:g}"
    if key == "arm_drift_difference":
        ref = _num(b.get("arm_drift_difference"))
        inc, cap = t.arm_drift_difference_increase, t.arm_drift_difference_absolute
        if ref is None:
            return None, cap, f"{cap:g}, absolute"
        return ref, min(ref + inc, cap), f"baseline + {inc:g}, at most {cap:g}"
    if key in ("arm_drift_left", "arm_drift_right"):
        ref = _num(b.get(key))
        cap = t.arm_single_drift_absolute
        return ref, cap, f"{cap:g}, absolute: an arm fell"
    if key == "speech_wer":
        ref = _num(b.get("speech_word_error_rate"))
        inc = t.speech_wer_increase
        return ref, (min(1.0, ref + inc) if ref is not None else None), f"baseline + {inc:g}"
    if key == "words_per_minute":
        ref = _num(b.get("speech_words_per_minute"))
        frac = t.speech_rate_drop_fraction
        keep = 1.0 - frac
        return ref, (ref * keep if ref is not None else None), f"{keep:.0%} of baseline"
    if key == "longest_pause_s":
        ref = _num(b.get("speech_longest_pause_s"))
        if ref is None:
            lim = t.speech_longest_pause_s
            return None, lim, f"{lim:g} s, absolute"
        lim = max(t.speech_pause_floor_s, ref + t.speech_pause_increase_s)
        return ref, lim, f"max({t.speech_pause_floor_s:g}, baseline + {t.speech_pause_increase_s:g}) s"
    return None, None, ""


def _reached(value: float, limit: float, worse: str) -> bool:
    return value >= limit - _EPS if worse == "up" else value <= limit + _EPS


def _linfit(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    """Least squares y = slope * x + intercept, and Pearson r (0 when y is constant)."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0:
        return None
    slope = sxy / sxx
    r = sxy / math.sqrt(sxx * syy) if syy > 0 else 0.0
    return slope, my - slope * mx, max(-1.0, min(1.0, r))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def fmt_value(key: str, value: float | None, extra_digits: int = 0) -> str:
    """A metric value as the report and summary show it."""
    if value is None:
        return "–"
    digits = (_SPECS[key].digits if key in _SPECS else 2) + extra_digits
    return f"{value:.{digits}f}"


# --------------------------------------------------------------------------- one run


@dataclass
class CheckIn:
    """One finished check-in, as read back from its archive."""

    run_id: str
    started_at: datetime
    time_source: str  # "event" (run_started carried a time) | "file" (archive mtime)
    source: str | None  # "call" | "replay" | ...
    outcome: str  # one of OUTCOMES
    reasons: list[str]
    metrics: dict[str, float | None]
    quality: dict[str, str | None]  # per step: "ok", "partial", ..., or None if not measured
    flags: list[str]  # quality problems in plain words
    baseline: dict | None  # the baseline numbers this run was compared with
    artifacts: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": _iso(self.started_at),
            "date": self.started_at.date().isoformat(),
            "time_source": self.time_source,
            "source": self.source,
            "outcome": self.outcome,
            "outcome_label": OUTCOME_LABELS.get(self.outcome, self.outcome),
            "reasons": list(self.reasons),
            "metrics": {k: _round(v) for k, v in self.metrics.items()},
            "quality": dict(self.quality),
            "flags": list(self.flags),
            "artifacts": dict(self.artifacts),
        }


class SkipRun(Exception):
    """This archive cannot be used; the message says why. `patient_id` is set when known."""

    def __init__(self, reason: str, patient_id: str | None = None) -> None:
        super().__init__(reason)
        self.patient_id = patient_id


def _scan_events(path: Path) -> dict:
    """The events that matter for history, from one events.jsonl."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SkipRun(f"events.jsonl unreadable ({type(exc).__name__})") from None
    if size > _MAX_EVENTS_BYTES:
        raise SkipRun("events.jsonl too large")
    found: dict[str, Any] = {"run_started": None, "results": {}, "decision": None, "baseline_saved": False,
                             "run_ended": None, "max_t": 0.0, "bad_lines": 0}
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                line = raw.decode("utf-8", "replace").strip()
                if not line or '"type": "live"' in line:  # meter samples: many, and not needed
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    found["bad_lines"] += 1
                    continue
                if not isinstance(ev, dict):
                    found["bad_lines"] += 1
                    continue
                t = _num(ev.get("t"))
                if t is not None:
                    found["max_t"] = max(found["max_t"], t)
                kind = ev.get("type")
                if kind == "run_started" and found["run_started"] is None:
                    found["run_started"] = ev
                elif kind == "result" and ev.get("check") in ("face", "arms", "speech"):
                    found["results"][ev["check"]] = ev  # the last one for a step wins
                elif kind == "decision":
                    found["decision"] = ev
                elif kind == "step" and ev.get("name") == "baseline_saved":
                    found["baseline_saved"] = True
                elif kind == "run_ended" and found["run_ended"] is None:
                    found["run_ended"] = ev
    except OSError as exc:
        raise SkipRun(f"events.jsonl unreadable ({type(exc).__name__})") from None
    return found


def _step(found: dict, check: str) -> tuple[str | None, dict]:
    ev = found["results"].get(check)
    if not isinstance(ev, dict):
        return None, {}
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    quality = ev.get("quality", data.get("quality"))
    return (quality if isinstance(quality, str) else "error"), data


def _metrics_and_flags(found: dict, t: Thresholds) -> tuple[dict, dict, list[str]]:
    metrics: dict[str, float | None] = {k: None for k in METRIC_KEYS}
    quality: dict[str, str | None] = {}
    flags: list[str] = []
    for check in ("face", "arms", "speech"):
        q, data = _step(found, check)
        quality[check] = q
        detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
        if q is None:
            flags.append(f"{_STEP_LABELS[check]}: no result")
            continue
        if q != "ok":
            words = "not heard clearly enough" if (check, q) == ("speech", "partial") else QUALITY_LABELS.get(q, q)
            flags.append(f"{_STEP_LABELS[check]}: {words}")
            if check == "speech" and detail.get("low_confidence"):
                flags.append("sentence: some words hard to make out")
            continue  # decide.py never uses numbers from a step that was not OK
        if check == "face":
            metrics["face_asymmetry"] = _num(data.get("asymmetry"))
        elif check == "arms":
            metrics["arm_drift_left"] = _num(data.get("left_drift"))
            metrics["arm_drift_right"] = _num(data.get("right_drift"))
            metrics["arm_drift_difference"] = _num(data.get("drift_difference"))
            hold = _num(data.get("hold_seconds"))
            if hold is None or hold < t.arm_min_hold_s - _EPS:
                flags.append("arms: not held up for the full time")
            if detail.get("early_drop"):
                flags.append("arms: one arm came down early")
        else:
            metrics["speech_wer"] = _num(data.get("word_error_rate"))
            metrics["words_per_minute"] = _num(data.get("words_per_minute"))
            metrics["longest_pause_s"] = _num(data.get("longest_pause_s"))
            if detail.get("low_confidence"):
                flags.append("sentence: some words hard to make out")
    return metrics, quality, flags


def read_run(run_dir: Path, *, thresholds: Thresholds = DEFAULT_THRESHOLDS,
             recordings_dir: Path | None = None) -> tuple[str | None, CheckIn]:
    """(patient_id, CheckIn) for one archived run. Raises SkipRun (only) if it is unusable."""
    events = run_dir / "events.jsonl"
    if not events.is_file():
        raise SkipRun("no events.jsonl")
    found = _scan_events(events)
    started = found["run_started"]
    if not isinstance(started, dict):
        raise SkipRun("no run_started event")
    patient = started.get("patient_id")
    patient = patient if isinstance(patient, str) else None

    decision = found["decision"] if isinstance(found["decision"], dict) else None
    if decision is not None:
        outcome = decision.get("outcome")
        if outcome not in ("escalate", "unclear", "no_change_detected"):
            raise SkipRun("unrecognised decision outcome", patient)
        raw_reasons = decision.get("reasons")
        reasons = [r for r in raw_reasons if isinstance(r, str)] if isinstance(raw_reasons, list) else []
    elif found["baseline_saved"]:
        outcome, reasons = "baseline_recorded", []
    else:
        raise SkipRun("no outcome (the check-in did not finish)", patient)

    when = None
    for key in ("started_at", "wall_time", "ts"):
        when = _parse_time(started.get(key))
        if when is not None:
            break
    time_source = "event"
    if when is None:  # events only carry seconds since start: use the archive's own time
        try:
            mtime = events.stat().st_mtime
        except OSError:
            raise SkipRun("no time for the run", patient) from None
        when = datetime.fromtimestamp(mtime - found["max_t"], tz=timezone.utc)
        time_source = "file"

    metrics, quality, flags = _metrics_and_flags(found, thresholds)
    if found["bad_lines"]:
        flags.append(f"archive: {found['bad_lines']} unreadable line(s) ignored")

    recordings = recordings_dir if recordings_dir is not None else DEFAULT_RECORDINGS
    try:
        audio = sum(1 for p in (run_dir / "audio").glob("*.wav")) if (run_dir / "audio").is_dir() else 0
        artifacts = {
            "events": True,
            "frame": (run_dir / "frame.jpg").is_file(),
            "agent_audio_clips": audio,
            "caller_recording": (Path(recordings) / run_dir.name).is_dir(),
        }
    except OSError:
        artifacts = {"events": True}

    run_id = started.get("run_id") if isinstance(started.get("run_id"), str) and started.get("run_id") else run_dir.name
    source = started.get("source") if isinstance(started.get("source"), str) else None
    return patient, CheckIn(run_id=run_id, started_at=when, time_source=time_source, source=source,
                            outcome=outcome, reasons=reasons, metrics=metrics, quality=quality, flags=flags,
                            baseline=_baseline_dict(started.get("baseline")), artifacts=artifacts)


# --------------------------------------------------------------------------- trends


def _metric_trend(spec: MetricSpec, window: list[CheckIn], start: datetime, end: datetime,
                  baseline: dict | None, t: Thresholds, s: TrendSettings) -> dict:
    ref, limit, rule = reference_and_limit(spec.key, baseline, t)
    pts = [(c.started_at, c.metrics.get(spec.key), c.outcome) for c in window]
    pts = [(when, v, o) for when, v, o in pts if v is not None]
    xs = [(when - start).total_seconds() / 86400.0 for when, _, _ in pts]
    ys = [v for _, v, _ in pts]
    out: dict[str, Any] = {
        "key": spec.key, "label": spec.label, "unit": spec.unit, "worse": spec.worse,
        "n": len(ys), "baseline": _round(ref), "limit": _round(limit), "limit_rule": rule,
        "points": [{"t": _iso(when), "v": _round(v), "outcome": o} for when, v, o in pts],
        "first": None, "latest": None, "min": None, "max": None, "mean": None,
        "change_vs_baseline": None, "slope_per_day": None, "slope_per_week": None, "r": None,
        "fitted_start": None, "fitted_latest": None, "direction": "no data",
        "last7": {"n": 0, "mean": None}, "prior7": {"n": 0, "mean": None}, "last7_minus_prior7": None,
        "reached_limit": 0, "progress": None, "days_to_limit": None, "gradual_decline": False,
    }
    if not ys:
        return out
    sign = 1.0 if spec.worse == "up" else -1.0
    out.update(first=_round(ys[0]), latest=_round(ys[-1]), min=_round(min(ys)), max=_round(max(ys)),
               mean=_round(_mean(ys)))
    if ref is not None:
        out["change_vs_baseline"] = _round(ys[-1] - ref)
    if limit is not None:
        out["reached_limit"] = sum(1 for v in ys if _reached(v, limit, spec.worse))

    cut = end - timedelta(days=s.compare_days)
    cut2 = cut - timedelta(days=s.compare_days)
    last = [v for (when, v, _) in pts if cut < when <= end]
    prior = [v for (when, v, _) in pts if cut2 < when <= cut]
    out["last7"] = {"n": len(last), "mean": _round(_mean(last))}
    out["prior7"] = {"n": len(prior), "mean": _round(_mean(prior))}
    if last and prior:
        out["last7_minus_prior7"] = _round(_mean(last) - _mean(prior))

    fit = _linfit(xs, ys)
    if fit is None:
        out["direction"] = "too few check-ins"
        return out
    slope, intercept, r = fit
    fitted_start, fitted_latest = intercept + slope * xs[0], intercept + slope * xs[-1]
    out.update(slope_per_day=_round(slope, 6), slope_per_week=_round(slope * 7, 5), r=_round(r, 3),
               fitted_start=_round(fitted_start), fitted_latest=_round(fitted_latest))
    adverse = slope * sign
    steady = abs(r) >= s.min_correlation
    if abs(slope) < 1e-12 or not steady:
        out["direction"] = "no steady trend"
    else:
        out["direction"] = "toward threshold" if adverse > 0 else "away from threshold"

    if limit is not None:
        anchor = ref if ref is not None else fitted_start
        margin = (limit - anchor) * sign
        if margin > _EPS:
            out["progress"] = _round((fitted_latest - anchor) * sign / margin, 3)
        if adverse > 0:
            remaining = (limit - fitted_latest) * sign
            out["days_to_limit"] = _round(max(0.0, remaining) / adverse, 1)
        out["gradual_decline"] = bool(
            len(ys) >= s.min_points
            and adverse > 0
            and steady
            and out["reached_limit"] == 0
            and out["progress"] is not None
            and out["progress"] >= s.min_progress - _EPS
        )
    return out


# --------------------------------------------------------------------------- history


@dataclass
class History:
    patient_id: str
    generated_at: datetime
    settings: TrendSettings
    window_start: datetime | None = None
    window_end: datetime | None = None
    checkins: list[CheckIn] = field(default_factory=list)  # in the window, oldest first
    total_checkins: int = 0  # for this person, in or out of the window
    baseline: dict | None = None
    baseline_source: str | None = None  # "latest run" | "baseline file" | "given" | None
    counts: dict[str, int] = field(default_factory=dict)
    skipped: list[dict] = field(default_factory=list)
    trends: dict[str, dict] = field(default_factory=dict)
    flags: list[dict] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "generated_at": _iso(self.generated_at),
            "window": {"days": self.settings.window_days, "start": _iso(self.window_start),
                       "end": _iso(self.window_end), "compare_days": self.settings.compare_days},
            "settings": asdict(self.settings),
            "baseline": self.baseline,
            "baseline_source": self.baseline_source,
            "total_checkins": self.total_checkins,
            "counts": dict(self.counts),
            "checkins": [c.to_dict() for c in self.checkins],
            "skipped": list(self.skipped),
            "trends": self.trends,
            "flags": list(self.flags),
            "summary": list(self.summary),
            "disclaimer": DISCLAIMER,
        }


DISCLAIMER = ("Onset is a research prototype, not a medical device. These are measurement trends from "
              "check-in calls; they are not a diagnosis, and every threshold still needs clinical validation.")


def _iter_run_dirs(runs_dir: Path):
    try:
        entries = sorted(runs_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return
    for entry in entries:
        if entry.name in _NOT_RUNS or entry.name.startswith("."):
            continue
        try:
            if entry.is_dir() and not entry.is_symlink() and (entry / "events.jsonl").exists():
                yield entry
        except OSError:
            continue


def load_history(
    patient_id: str,
    runs_dir: Path | str | None = None,
    *,
    baselines_dir: Path | str | None = None,
    baseline: Baseline | dict | None = None,
    as_of: datetime | None = None,
    settings: TrendSettings = DEFAULT_SETTINGS,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    recordings_dir: Path | str | None = None,
) -> History:
    """Every archived check-in for `patient_id`, oldest first, with trends. Never raises.

    runs_dir        the runs directory (default: ONSET_RUNS_DIR or <repo>/runs)
    baselines_dir   where decide.py keeps baselines; used only when no run carried its baseline
    baseline        compare with this baseline instead (a Baseline or its dict)
    as_of           end of the window (default: the latest check-in)
    """
    now = datetime.now(timezone.utc)
    if not isinstance(settings, TrendSettings):
        settings = DEFAULT_SETTINGS
    if not isinstance(thresholds, Thresholds):
        thresholds = DEFAULT_THRESHOLDS
    h = History(patient_id=str(patient_id), generated_at=now, settings=settings)
    try:
        _fill(h, patient_id, runs_dir, baselines_dir, baseline, as_of, thresholds, recordings_dir)
    except Exception as exc:  # noqa: BLE001 - history is a report; it must never take anything down
        log.warning("history for a patient could not be built (%s)", type(exc).__name__)
        h.checkins, h.trends, h.flags = [], {}, []
        h.skipped.append({"run": "*", "reason": f"history could not be built ({type(exc).__name__})"})
        h.summary = summarize(h)
    return h


def _fill(h: History, patient_id: str, runs_dir, baselines_dir, baseline, as_of, t: Thresholds,
          recordings_dir) -> None:
    s = h.settings
    if not valid_patient_id(patient_id):
        h.skipped.append({"run": "*", "reason": "invalid patient id"})
        h.summary = summarize(h)
        return
    runs = Path(runs_dir) if runs_dir is not None else DEFAULT_RUNS
    rec = Path(recordings_dir) if recordings_dir is not None else None

    found: list[CheckIn] = []
    seen: set[str] = set()
    for run_dir in _iter_run_dirs(runs):
        try:
            pid, checkin = read_run(run_dir, thresholds=t, recordings_dir=rec)
        except SkipRun as exc:
            # Unfinished runs of other people are not this person's business; an archive
            # whose owner cannot be read is counted for everyone.
            if exc.patient_id in (None, patient_id):
                h.skipped.append({"run": run_dir.name, "reason": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - one bad archive never hides the others
            h.skipped.append({"run": run_dir.name, "reason": f"unreadable ({type(exc).__name__})"})
            continue
        if pid != patient_id or checkin.run_id in seen:
            continue
        seen.add(checkin.run_id)
        found.append(checkin)
    found.sort(key=lambda c: (c.started_at, c.run_id))
    h.total_checkins = len(found)

    if found:
        end = found[-1].started_at
        if isinstance(as_of, datetime):
            end = (as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        start = max(found[0].started_at, end - timedelta(days=s.window_days))
        h.window_start, h.window_end = start, end
        h.checkins = [c for c in found if start <= c.started_at <= end]

    # The baseline the trends compare with: one given, else what the latest run was compared
    # with, else the saved baseline file.
    ref = _baseline_dict(baseline)
    source = "given" if ref else None
    if ref is None:
        for c in reversed(h.checkins):
            if c.baseline:
                ref, source = c.baseline, "latest run"
                break
    if ref is None and baselines_dir is not None:
        try:
            ref = _baseline_dict(load_baseline(patient_id, base_dir=Path(baselines_dir)))
            source = "baseline file" if ref else None
        except Exception:  # noqa: BLE001
            ref = None
    h.baseline, h.baseline_source = ref, source

    h.counts = {o: sum(1 for c in h.checkins if c.outcome == o) for o in OUTCOMES}
    h.counts["checkins"] = len(h.checkins)
    h.counts["with_quality_flags"] = sum(1 for c in h.checkins if c.flags)
    h.counts["skipped"] = len(h.skipped)

    if h.checkins:
        for spec in METRICS:
            h.trends[spec.key] = _metric_trend(spec, h.checkins, h.window_start, h.window_end, ref, t, s)
    for key, tr in h.trends.items():
        if tr["gradual_decline"]:
            h.flags.append({"kind": "gradual_decline", "metric": key, "label": tr["label"],
                            "text": _decline_text(tr)})
        elif tr["reached_limit"]:
            h.flags.append({"kind": "threshold_reached", "metric": key, "label": tr["label"],
                            "text": f"{tr['label']} reached its change threshold of {fmt_value(key, tr['limit'])} "
                                    f"at {_n(tr['reached_limit'], 'check-in')} (rule: {tr['limit_rule']})."})
    h.summary = summarize(h)


# --------------------------------------------------------------------------- wording


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _date(t: datetime | None) -> str:
    return f"{t:%d %b %Y}".lstrip("0") if t else "–"


def _decline_text(tr: dict) -> str:
    key = tr["key"]
    verb = "risen" if tr["worse"] == "up" else "fallen"
    unit = f" {tr['unit']}" if tr["unit"] and not tr["unit"].startswith("index") else ""
    text = (f"{tr['label']} has {verb} steadily, from about {fmt_value(key, tr['fitted_start'])} to "
            f"{fmt_value(key, tr['fitted_latest'])}{unit} across {_n(tr['n'], 'check-in')} "
            f"(r = {abs(tr['r']):.2f}). No single check-in reached the change threshold of "
            f"{fmt_value(key, tr['limit'])} ({tr['limit_rule']})")
    if tr["days_to_limit"] is not None:
        days = tr["days_to_limit"]
        text += (f"; at this rate it would reach it in about {days:.0f} day{'' if round(days) == 1 else 's'}"
                 if days >= 1 else "; at this rate it would reach it within a day")
    return text + "."


def summarize(h: History) -> list[str]:
    """Plain-language lines for the care team. Measurements only; no diagnosis."""
    lines: list[str] = []
    if not h.checkins:
        lines.append("No finished check-ins are archived for this person yet.")
        if h.skipped:
            lines.append(f"{_n(len(h.skipped), 'archived run')} could not be used and "
                         f"{'was' if len(h.skipped) == 1 else 'were'} skipped.")
        lines.append(DISCLAIMER)
        return lines
    n = len(h.checkins)
    span = (h.window_end - h.window_start).total_seconds() / 86400.0 if h.window_end and h.window_start else 0
    lines.append(f"{_n(n, 'check-in')} between {_date(h.window_start)} and {_date(h.window_end)} "
                 f"({span:.0f} day{'' if round(span) == 1 else 's'}; window: up to {h.settings.window_days:g} days "
                 f"from the first check-in).")
    c = h.counts
    parts = []
    if c.get("escalate"):
        parts.append(f"{c['escalate']} with a change detected")
    if c.get("unclear"):
        parts.append(f"{c['unclear']} unclear (could not be measured clearly)")
    if c.get("no_change_detected"):
        parts.append(f"{c['no_change_detected']} with no change detected from baseline")
    if c.get("baseline_recorded"):
        parts.append(f"{c['baseline_recorded']} recording the baseline")
    if parts:
        lines.append("Outcomes: " + "; ".join(parts) + ". (On the call, a change and an unclear result both "
                     "tell the person to call 911.)")
    if h.baseline is None:
        lines.append("No baseline was available, so only the absolute arm and pause limits are shown.")
    declines = [f for f in h.flags if f["kind"] == "gradual_decline"]
    for f in declines:
        lines.append(f["text"])
    for f in h.flags:
        if f["kind"] == "threshold_reached":
            lines.append(f["text"])
    if not declines:
        lines.append("No measurement moved steadily toward its change threshold in this window.")
    recent = []
    for key, tr in h.trends.items():
        d = tr.get("last7_minus_prior7")
        if d is None or tr["limit"] is None or tr["baseline"] is None:
            continue
        margin = abs(tr["limit"] - tr["baseline"])
        if margin > 0 and d * (1 if tr["worse"] == "up" else -1) >= 0.25 * margin:
            recent.append(f"{tr['label'].lower()} ({'+' if d >= 0 else '−'}{fmt_value(key, abs(d))})")
    if recent:
        lines.append(f"Compared with the {h.settings.compare_days:g} days before, the last "
                     f"{h.settings.compare_days:g} days averaged worse for: " + ", ".join(recent) + ".")
    flagged = c.get("with_quality_flags", 0)
    if flagged:
        lines.append(f"{_n(flagged, 'check-in')} had at least one step that was not measured cleanly; "
                     "those numbers are left out of the trends.")
    if h.skipped:
        lines.append(f"{_n(len(h.skipped), 'archived run')} could not be used (unreadable or unfinished) and "
                     f"{'was' if len(h.skipped) == 1 else 'were'} skipped.")
    lines.append(DISCLAIMER)
    return lines
