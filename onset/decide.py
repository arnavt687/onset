"""Piece 4: baselines and the decision rules.

Owner: the `decide` session. See ROADMAP.md, piece 4, and docs/thresholds.md.

The decision is rules, not a model: any measured change beyond its threshold gives
ESCALATE, and any step whose quality is not OK (or a missing baseline) gives UNCLEAR.
Both mean "call 911 now". NO_CHANGE_DETECTED is only possible when all three steps were
measured well and every number is within its threshold of the person's own baseline.

Nothing in this module tells the person they are well or names a diagnosis.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone, tzinfo
from pathlib import Path

from onset.types import Baseline, CheckResults, Decision, Outcome, Quality

log = logging.getLogger(__name__)

BASELINE_DIR = Path("baselines")  # gitignored; files are owner-only (0600), directory 0700
BASELINE_FORMAT_VERSION = 1

# A patient id becomes a file name, so it must not be able to leave BASELINE_DIR.
# Letters, digits, "_", "+", "-" and "." (not first, so no "." / ".." / hidden files).
_PATIENT_ID_RE = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9_+.-]{0,127}$")

# Float slack so a value exactly at a threshold reliably counts as a change.
_EPS = 1e-9


# --------------------------------------------------------------------------- thresholds


@dataclass(frozen=True)
class Thresholds:
    """Every number the decision depends on, in one place.

    Prototype placeholders chosen to err toward ESCALATE; they need clinical validation.
    docs/thresholds.md explains each one and how to tune it from recorded check-ins.
    A value exactly at its threshold counts as a change.
    """

    # Smile: asymmetry index above baseline. face.py defines it as
    # 1 - weaker_lift / stronger_lift (0 = symmetric, 1 = one side did not move).
    face_asymmetry_increase: float = 0.10
    # Arms: |left - right| wrist drift (shoulder widths over the hold) above baseline.
    arm_drift_difference_increase: float = 0.06
    # Arms: both arms must have been up at least this long for the check to count.
    arm_min_hold_s: float = 8.0
    # Sentence: word error rate above baseline (0 = exact; can exceed 1 with extra words).
    speech_wer_increase: float = 0.15
    # Sentence: speaking rate this fraction or more below baseline words per minute.
    speech_rate_drop_fraction: float = 0.20
    # Sentence: longest silence inside the sentence, in seconds. Absolute, because the
    # Baseline contract does not store a pause value yet (see docs/thresholds.md).
    speech_longest_pause_s: float = 1.5


DEFAULT_THRESHOLDS = Thresholds()


# ---------------------------------------------------------------------------- baselines


class BaselineError(ValueError):
    """A baseline (or the results it would be built from) cannot be trusted."""


def _check_patient_id(patient_id: object) -> str:
    if not isinstance(patient_id, str) or not _PATIENT_ID_RE.fullmatch(patient_id):
        raise BaselineError("patient id must be 1-128 letters, digits, '_', '+', '-' or '.'")
    return patient_id


def _finite_nonneg(name: str, value: object) -> float:
    # bool is an int subclass; a JSON true/false is never a measurement.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaselineError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise BaselineError(f"{name} must be a finite number >= 0")
    return value


def _validate_baseline(b: Baseline) -> Baseline:
    """Raise BaselineError unless every field is usable for comparison."""
    _check_patient_id(b.patient_id)
    if not isinstance(b.recorded_at, str):
        raise BaselineError("recorded_at must be an ISO 8601 string")
    try:
        datetime.fromisoformat(b.recorded_at)
    except ValueError as exc:
        raise BaselineError("recorded_at must be an ISO 8601 string") from exc
    _finite_nonneg("face_asymmetry", b.face_asymmetry)
    _finite_nonneg("arm_drift_difference", b.arm_drift_difference)
    wer = _finite_nonneg("speech_word_error_rate", b.speech_word_error_rate)
    if wer > 1:
        raise BaselineError("speech_word_error_rate must be between 0 and 1")
    if _finite_nonneg("speech_words_per_minute", b.speech_words_per_minute) <= 0:
        raise BaselineError("speech_words_per_minute must be above 0")
    if not isinstance(b.sentence, str) or not b.sentence.strip():
        raise BaselineError("sentence must be a non-empty string")
    if isinstance(b.samples, bool) or not isinstance(b.samples, int) or b.samples < 1:
        raise BaselineError("samples must be a whole number >= 1")
    return b


def _baseline_path(patient_id: str, base_dir: Path | None) -> Path:
    return Path(base_dir if base_dir is not None else BASELINE_DIR) / f"{_check_patient_id(patient_id)}.json"


def save_baseline(baseline: Baseline, *, base_dir: Path | None = None) -> Path:
    """Write the baseline atomically as `<base_dir>/<patient_id>.json`, owner-only.

    The directory is created (or tightened) to 0700 and the file is 0600. A crash part
    way through leaves the previous file in place, never a half-written one.
    """
    _validate_baseline(baseline)
    path = _baseline_path(baseline.patient_id, base_dir)
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)

    payload = {"version": BASELINE_FORMAT_VERSION, **asdict(baseline)}
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")

    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")  # 0600
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    try:  # make the rename itself durable
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    return path


def load_baseline(patient_id: str, *, base_dir: Path | None = None) -> Baseline | None:
    """The saved baseline, or None if there is none or it cannot be trusted.

    A corrupt, unreadable or tampered file is logged and treated exactly like having no
    baseline, which makes the check UNCLEAR. It is never treated as a pass.
    """
    try:
        path = _baseline_path(patient_id, base_dir)
    except BaselineError:
        log.warning("baseline lookup refused: invalid patient id")
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("baseline %s unreadable (%s); treating as no baseline", path.name, type(exc).__name__)
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise BaselineError("not a regular file")
            if info.st_mode & 0o077:
                log.warning("baseline %s was readable by others; tightening to 0600", path.name)
                os.fchmod(fh.fileno(), 0o600)
            raw = fh.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise BaselineError("file too large")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise BaselineError("not a JSON object")
        if payload.pop("version", None) != BASELINE_FORMAT_VERSION:
            raise BaselineError("unknown format version")
        expected = {f.name for f in fields(Baseline)}
        if set(payload) != expected:
            raise BaselineError("unexpected or missing fields")
        baseline = _validate_baseline(Baseline(**payload))
        if baseline.patient_id != patient_id:
            raise BaselineError("file belongs to a different patient id")
        return baseline
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        # json.JSONDecodeError and BaselineError are ValueErrors. Do not log the contents.
        log.warning("baseline %s is corrupt (%s); treating as no baseline", path.name, exc)
        return None


def baseline_from(results: list[CheckResults], patient_id: str) -> Baseline:
    """Build a baseline from one or more check-ins recorded while the person was well.

    Each number is the mean over the check-ins. Raises BaselineError if any check-in has
    a step whose quality is not OK or a missing value, or if they used different
    sentences: a baseline is only as good as its worst sample.
    """
    _check_patient_id(patient_id)
    if not results:
        raise BaselineError("need at least one check-in to build a baseline")
    face, arms, wer, wpm, sentences = [], [], [], [], set()
    for i, r in enumerate(results, start=1):
        for step, q in (("smile", r.face.quality), ("arms", r.arms.quality), ("sentence", r.speech.quality)):
            if q is not Quality.OK:
                raise BaselineError(f"check-in {i}: {step} quality is {q.value}, not ok")
        values = {
            "face asymmetry": r.face.asymmetry,
            "arm drift difference": r.arms.drift_difference,
            "word error rate": r.speech.word_error_rate,
            "words per minute": r.speech.words_per_minute,
        }
        for name, v in values.items():
            if v is None:
                raise BaselineError(f"check-in {i}: {name} is missing")
            _finite_nonneg(f"check-in {i}: {name}", v)
        face.append(float(r.face.asymmetry))
        arms.append(float(r.arms.drift_difference))
        wer.append(float(r.speech.word_error_rate))
        wpm.append(float(r.speech.words_per_minute))
        sentences.add(r.speech.expected.strip())
    if len(sentences) != 1:
        raise BaselineError("all baseline check-ins must use the same sentence")
    n = len(results)
    return _validate_baseline(
        Baseline(
            patient_id=patient_id,
            recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            face_asymmetry=sum(face) / n,
            arm_drift_difference=sum(arms) / n,
            speech_word_error_rate=sum(wer) / n,
            speech_words_per_minute=sum(wpm) / n,
            sentence=sentences.pop(),
            samples=n,
        )
    )


# ----------------------------------------------------------------------------- decision

_STEP_NAMES = {"face": "smile check", "arms": "arm check", "speech": "sentence check"}

# One plain-language line per quality problem. {step} is "smile check" etc.
_QUALITY_LINES = {
    Quality.NO_SUBJECT: "The camera could not see the person during the {step}",
    Quality.PARTIAL: "The person was only partly in view during the {step}",
    Quality.TOO_DARK: "It was too dark to see the {step} clearly",
    Quality.MOTION: "There was too much movement to read the {step}",
    Quality.ERROR: "The {step} could not be completed",
}
_NO_RESPONSE_LINES = {
    "face": "No smile was seen during the smile check",
    "arms": "The arms were not seen raised during the arm check",
    "speech": "The sentence was not heard during the sentence check",
}


def _usable(value: float | None) -> bool:
    return value is not None and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _quality_line(step: str, quality: Quality) -> str:
    if quality is Quality.NO_RESPONSE:
        return _NO_RESPONSE_LINES[step]
    template = _QUALITY_LINES.get(quality, "The {step} could not be completed")
    return template.format(step=_STEP_NAMES[step])


def _side(weaker_side: str | None) -> str | None:
    return weaker_side if weaker_side in ("left", "right") else None


def _findings(results: CheckResults, baseline: Baseline | None, t: Thresholds) -> tuple[list[str], list[str]]:
    """(changes, unclear): plain-language lines, in step order."""
    changes: list[str] = []
    unclear: list[str] = []
    if baseline is None:
        unclear.append("There is no saved baseline to compare with yet")

    # Smile
    face = results.face
    if face.quality is not Quality.OK:
        unclear.append(_quality_line("face", face.quality))
    elif not _usable(face.asymmetry):
        unclear.append("The smile check did not give a usable measurement")
    elif baseline is not None and face.asymmetry - baseline.face_asymmetry >= t.face_asymmetry_increase - _EPS:
        side = _side(face.weaker_side)
        changes.append(
            f"The {side} side of the smile lifted less than usual" if side
            else "One side of the smile lifted less than usual"
        )

    # Arms
    arms = results.arms
    if arms.quality is not Quality.OK:
        unclear.append(_quality_line("arms", arms.quality))
    elif not _usable(arms.drift_difference):
        unclear.append("The arm check did not give a usable measurement")
    else:
        if baseline is not None and (
            arms.drift_difference - baseline.arm_drift_difference
            >= t.arm_drift_difference_increase - _EPS
        ):
            side = _side(arms.weaker_side)
            changes.append(
                f"The {side} arm drifted down more than usual" if side
                else "One arm drifted down more than usual"
            )
        if not (_usable(arms.hold_seconds) and arms.hold_seconds >= t.arm_min_hold_s - _EPS):
            unclear.append("Both arms were not held up for the full time")

    # Sentence
    sp = results.speech
    if sp.quality is not Quality.OK:
        unclear.append(_quality_line("speech", sp.quality))
    elif not (_usable(sp.word_error_rate) and _usable(sp.words_per_minute)):
        unclear.append("The sentence check did not give a usable measurement")
    else:
        if baseline is not None:
            if baseline.sentence.strip() != sp.expected.strip():
                log.warning("sentence differs from the baseline sentence; comparing anyway")
            if sp.word_error_rate - baseline.speech_word_error_rate >= t.speech_wer_increase - _EPS:
                changes.append("The sentence came out less clearly than usual")
            slowest_ok = baseline.speech_words_per_minute * (1.0 - t.speech_rate_drop_fraction)
            if sp.words_per_minute <= slowest_ok + _EPS:
                changes.append("Speech was slower than usual")
        # Absolute: applies with or without a baseline.
        if not _usable(sp.longest_pause_s):
            unclear.append("The pauses in the sentence could not be measured")
        elif sp.longest_pause_s >= t.speech_longest_pause_s - _EPS:
            changes.append("There was a long pause while saying the sentence")
    return changes, unclear


def decide(
    results: CheckResults,
    baseline: Baseline | None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Decision:
    """Compare one check-in with the person's baseline. Rules only.

    ESCALATE if any measurement moved past its threshold; otherwise UNCLEAR if any step's
    quality is not OK, a value (including the longest pause) is missing, the arms were not
    up for `arm_min_hold_s`, or there is no (valid) baseline; otherwise
    NO_CHANGE_DETECTED. No baseline, or any unclear step, is never NO_CHANGE_DETECTED.
    `reasons` lists every change first, then every unclear item.
    """
    if baseline is not None:
        try:
            _validate_baseline(baseline)
        except BaselineError as exc:
            log.warning("baseline rejected (%s); treating as no baseline", exc)
            baseline = None
    changes, unclear = _findings(results, baseline, thresholds)
    if changes:
        outcome = Outcome.ESCALATE
    elif unclear:
        outcome = Outcome.UNCLEAR
    else:
        outcome = Outcome.NO_CHANGE_DETECTED
    return Decision(outcome=outcome, reasons=changes + unclear, results=results, baseline=baseline)


# ------------------------------------------------------------------------ caregiver text

SUMMARY_MAX_CHARS = 480  # iMessage-friendly; the task asks for under ~500


def _clean_name(name: object) -> str:
    text = " ".join(str(name or "").split())[:40].strip()
    return text or "The person"


def _when(started_at: str, tz: tzinfo | None) -> str:
    try:
        t = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return "at an unknown time"
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(tz or timezone.utc)
    zone = t.tzname() or "UTC"
    return f"on {t:%a %d %b} at {t:%H:%M} {zone}"


def caregiver_summary(decision: Decision, patient_name: str, *, tz: tzinfo | None = None) -> str:
    """The iMessage text for the caregiver: who, when, what was checked, what changed or
    was unclear, and that the person was told to call 911. No diagnosis. Under 500 chars.
    """
    name = _clean_name(patient_name)
    head = f"Onset check-in for {name} {_when(decision.results.started_at, tz)}. Checked: smile, arm hold, repeating a sentence."
    tail_footer = "Onset is a research prototype and cannot diagnose."

    if decision.outcome is Outcome.NO_CHANGE_DETECTED:
        middle = f"No change from {name}'s saved baseline was detected in these checks."
        tail = f"If you are worried, call 911. {tail_footer}"
        return f"{head}\n{middle}\n{tail}"

    label = "What Onset noticed:" if decision.outcome is Outcome.ESCALATE else "What Onset could not check clearly:"
    tail = f"{name} was told to call 911 now. Please check on them right away. {tail_footer}"

    reasons = [r for r in decision.reasons if r] or ["Something did not look as usual"]
    budget = SUMMARY_MAX_CHARS - len(head) - len(tail) - len(label) - 3  # newlines
    lines: list[str] = []
    for i, reason in enumerate(reasons):
        line = f"- {reason}."
        remaining = len(reasons) - i - 1
        more = f"- and {remaining} more." if remaining else ""
        used = sum(len(x) + 1 for x in lines)
        if used + len(line) + 1 + (len(more) + 1 if more else 0) > budget:
            left = len(reasons) - i
            lines.append(f"- and {left} more.")
            break
        lines.append(line)
    return "\n".join([head, label, *lines, tail])
