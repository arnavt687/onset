"""Piece 4: baselines and the decision rules.

Owner: the `decide` session. See ROADMAP.md, piece 4, and docs/thresholds.md.

The decision is rules, not a model: any measured change beyond its threshold gives
ESCALATE, and any step whose quality is not OK (or a missing baseline) gives UNCLEAR.
Both mean "call 911 now". NO_CHANGE_DETECTED is only possible when all three steps were
measured well and every number is within its threshold of the person's own baseline.
A few arm rules are absolute (they escalate with or without a baseline).

`decide()` never raises: a step whose results cannot be read becomes UNCLEAR.

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

from onset.speech import normalize_words
from onset.types import Baseline, CheckResults, Decision, Outcome, Quality

log = logging.getLogger(__name__)

BASELINE_DIR = Path("baselines")  # gitignored; files are owner-only (0600), directory 0700
# 2 added the optional per-side, pause and confidence fields. Version 1 files still load.
BASELINE_FORMAT_VERSION = 2
_READABLE_VERSIONS = (1, 2)
# Fields every baseline file has had since version 1; the rest are optional.
_REQUIRED_FIELDS = frozenset({
    "patient_id", "recorded_at", "face_asymmetry", "arm_drift_difference",
    "speech_word_error_rate", "speech_words_per_minute", "sentence", "samples",
})

# A patient id becomes a file name, so it must not be able to leave BASELINE_DIR.
# Letters, digits, "_", "+", "-" and "." (not first, so no "." / ".." / hidden files).
_PATIENT_ID_RE = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9_+.-]{0,127}$")

# Float slack so a value exactly at a threshold reliably counts as a change.
_EPS = 1e-9
# WER is reported to 3 decimals, so wer * n_words can be off by up to ~0.01 words.
_WORD_SLACK = 0.05


# --------------------------------------------------------------------------- thresholds


@dataclass(frozen=True)
class Thresholds:
    """Every number the decision depends on, in one place.

    Prototype placeholders chosen to err toward ESCALATE; they need clinical validation.
    docs/thresholds.md explains each one, where it came from, and how to tune it from
    recorded check-ins. A value exactly at its threshold counts as a change.
    """

    # Smile: asymmetry index above baseline. face.py defines it as
    # 1 - weaker_lift / stronger_lift (0 = symmetric, 1 = one side did not move).
    face_asymmetry_increase: float = 0.10
    # Smile: asymmetry below this is measurement noise (face.py MIN_ASYM_FOR_SIDE). A weaker
    # side that switched from the baseline's, with asymmetry at or above this, is a change.
    face_side_noise: float = 0.05
    # Arms: |left - right| wrist drift (shoulder widths over the hold) above baseline.
    arm_drift_difference_increase: float = 0.06
    # Arms: side difference below this is noise (arms.py SIDE_NOISE); a switched weaker side
    # above it is a change.
    arm_side_noise: float = 0.03
    # Arms, absolute (with or without a baseline): this much more drift on one side ...
    arm_drift_difference_absolute: float = 0.20
    # ... or either wrist drifting down this far (an arm fell). An early one-sided drop
    # (arms detail["early_drop"]) also escalates.
    arm_single_drift_absolute: float = 0.50
    # Arms: both arms must have been up at least this long for the check to count.
    arm_min_hold_s: float = 8.0
    # Baseline: a check-in can only become a baseline with the full 10 s hold the flow times
    # (arms.HOLD_SECONDS), less a quarter second of frame timing.
    baseline_min_hold_s: float = 9.75
    # Sentence: word error rate above baseline (0 = exact; can exceed 1 with extra words).
    speech_wer_increase: float = 0.10
    # Sentence: this many more wrong, missing or added words than the whole number of errors
    # the baseline averages escalates, whatever the sentence length.
    speech_word_errors_increase: float = 1.0
    # Sentence: speaking rate this fraction or more below baseline words per minute.
    speech_rate_drop_fraction: float = 0.20
    # Sentence: longest pause >= max(floor, baseline pause + increase) is a change.
    speech_pause_floor_s: float = 1.0
    speech_pause_increase_s: float = 0.7
    # Sentence: absolute pause limit used when the baseline has no pause stored (older
    # baseline files) or there is no baseline.
    speech_longest_pause_s: float = 1.5
    # Sentence: mean word confidence (Scribe logprob) this far below baseline -> UNCLEAR.
    speech_logprob_drop: float = 0.15


DEFAULT_THRESHOLDS = Thresholds()


# ---------------------------------------------------------------------------- baselines


class BaselineError(ValueError):
    """A baseline (or the results it would be built from) cannot be trusted."""


def _check_patient_id(patient_id: object) -> str:
    if not isinstance(patient_id, str) or not _PATIENT_ID_RE.fullmatch(patient_id):
        raise BaselineError("patient id must be 1-128 letters, digits, '_', '+', '-' or '.'")
    return patient_id


def _finite(name: str, value: object) -> float:
    # bool is an int subclass; a JSON true/false is never a measurement.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaselineError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise BaselineError(f"{name} must be a finite number")
    return value


def _finite_nonneg(name: str, value: object) -> float:
    value = _finite(name, value)
    if value < 0:
        raise BaselineError(f"{name} must be a finite number >= 0")
    return value


def _validate_baseline(b: Baseline) -> Baseline:
    """Raise BaselineError unless every field is usable for comparison."""
    if not isinstance(b, Baseline):
        raise BaselineError("not a Baseline")
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
    # Optional fields (None = not recorded, e.g. an older baseline file).
    for name in ("face_weaker_side", "arm_weaker_side"):
        if getattr(b, name) not in (None, "left", "right"):
            raise BaselineError(f"{name} must be 'left', 'right' or empty")
    for name in ("face_lift_left", "face_lift_right", "arm_drift_left", "arm_drift_right"):
        if getattr(b, name) is not None:
            _finite(name, getattr(b, name))
    if b.speech_longest_pause_s is not None:
        _finite_nonneg("speech_longest_pause_s", b.speech_longest_pause_s)
    if b.speech_mean_logprob is not None and _finite("speech_mean_logprob", b.speech_mean_logprob) > 0:
        raise BaselineError("speech_mean_logprob must be <= 0")
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
    baseline, which makes the check UNCLEAR. It is never treated as a pass. Files written
    before the optional per-side / pause / confidence fields existed still load; those
    fields are then None and the rules that need them fall back (see docs/thresholds.md).
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
        version = payload.pop("version", None)
        if isinstance(version, bool) or version not in _READABLE_VERSIONS:
            raise BaselineError("unknown format version")
        allowed = {f.name for f in fields(Baseline)}
        if not (_REQUIRED_FIELDS <= set(payload) <= allowed):
            raise BaselineError("unexpected or missing fields")
        baseline = _validate_baseline(Baseline(**payload))
        if baseline.patient_id != patient_id:
            raise BaselineError("file belongs to a different patient id")
        return baseline
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        # json.JSONDecodeError and BaselineError are ValueErrors. Do not log the contents.
        log.warning("baseline %s is corrupt (%s); treating as no baseline", path.name, exc)
        return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _side_of(signed: float, noise: float) -> str | None:
    """Positive signed values mean the person's left is the weaker side."""
    if signed >= noise - _EPS:
        return "left"
    if signed <= -noise + _EPS:
        return "right"
    return None


def baseline_from(
    results: list[CheckResults],
    patient_id: str,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Baseline:
    """Build a baseline from one or more check-ins recorded while the person was well.

    Each number is the mean over the check-ins; the weaker sides come from the mean signed
    (left minus right) values. Raises BaselineError (a ValueError) if any check-in:
    - has a step whose quality is not OK, or a missing value;
    - had an arm come down before the other (`early_drop` in the arm detail);
    - held the arms up for less than the full hold (`baseline_min_hold_s`);
    - would itself have escalated or been unclear on the rules that need no baseline
      (a large one-sided arm drift, an arm falling, a long pause, words hard to make out);
    or if the check-ins used different sentences. A baseline is only as good as its worst
    sample, and a bad one would hide a real change later.
    """
    t = thresholds
    _check_patient_id(patient_id)
    if not results:
        raise BaselineError("need at least one check-in to build a baseline")
    results = list(results)
    face, arms, wer, wpm, pause, sentences = [], [], [], [], [], set()
    face_signed, arm_signed, lifts, drifts, logprobs = [], [], [], [], []
    for i, r in enumerate(results, start=1):
        for step, q in (("smile", r.face.quality), ("arms", r.arms.quality), ("sentence", r.speech.quality)):
            if q is not Quality.OK:
                value = q.value if isinstance(q, Quality) else repr(q)
                raise BaselineError(f"check-in {i}: {step} quality is {value}, not ok")
        values = {
            "face asymmetry": r.face.asymmetry,
            "arm drift difference": r.arms.drift_difference,
            "word error rate": r.speech.word_error_rate,
            "words per minute": r.speech.words_per_minute,
            "longest pause": r.speech.longest_pause_s,
        }
        for name, v in values.items():
            if v is None:
                raise BaselineError(f"check-in {i}: {name} is missing")
            _finite_nonneg(f"check-in {i}: {name}", v)
        if not isinstance(r.speech.expected, str) or not r.speech.expected.strip():
            raise BaselineError(f"check-in {i}: the sentence is missing")
        arm_detail = _detail(r.arms)
        if arm_detail.get("early_drop"):
            raise BaselineError(f"check-in {i}: one arm came down before the other (early_drop)")
        hold = _num(r.arms.hold_seconds)
        if hold is None or hold < t.baseline_min_hold_s - _EPS:
            raise BaselineError(
                f"check-in {i}: arms held up {hold if hold is not None else 'unknown'} s; "
                f"a baseline needs the full hold (at least {t.baseline_min_hold_s} s)"
            )
        changes, unclear = _findings(r, None, t)
        unclear = [u for u in unclear if u != _NO_BASELINE_LINE]
        if changes or unclear:
            raise BaselineError(f"check-in {i} would itself need attention: " + "; ".join(changes + unclear))

        face.append(float(r.face.asymmetry))
        arms.append(float(r.arms.drift_difference))
        wer.append(float(r.speech.word_error_rate))
        wpm.append(float(r.speech.words_per_minute))
        pause.append(float(r.speech.longest_pause_s))
        sentences.add(r.speech.expected.strip())

        fd = _detail(r.face)
        ll, lr = _num(fd.get("lift_left")), _num(fd.get("lift_right"))
        lifts.append((ll, lr) if ll is not None and lr is not None else None)
        s = _signed_from_lifts(ll, lr)
        if s is None:
            s = _signed_from_side(r.face.asymmetry, r.face.weaker_side)
        face_signed.append(s if s is not None else 0.0)  # side None = within noise

        dl, dr = _num(r.arms.left_drift), _num(r.arms.right_drift)
        drifts.append((dl, dr) if dl is not None and dr is not None else None)
        s = dl - dr if dl is not None and dr is not None else _signed_from_side(r.arms.drift_difference, r.arms.weaker_side)
        arm_signed.append(s if s is not None else 0.0)

        logprobs.append(_num(_detail(r.speech).get("mean_logprob")))
    if len(sentences) != 1:
        raise BaselineError("all baseline check-ins must use the same sentence")
    have_lifts = all(x is not None for x in lifts)
    have_drifts = all(x is not None for x in drifts)
    have_lp = all(x is not None for x in logprobs)
    return _validate_baseline(
        Baseline(
            patient_id=patient_id,
            recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            face_asymmetry=_mean(face),
            arm_drift_difference=_mean(arms),
            speech_word_error_rate=_mean(wer),
            speech_words_per_minute=_mean(wpm),
            sentence=sentences.pop(),
            samples=len(results),
            face_weaker_side=_side_of(_mean(face_signed), t.face_side_noise),
            face_lift_left=_mean([x[0] for x in lifts]) if have_lifts else None,
            face_lift_right=_mean([x[1] for x in lifts]) if have_lifts else None,
            arm_drift_left=_mean([x[0] for x in drifts]) if have_drifts else None,
            arm_drift_right=_mean([x[1] for x in drifts]) if have_drifts else None,
            arm_weaker_side=_side_of(_mean(arm_signed), t.arm_side_noise),
            speech_longest_pause_s=_mean(pause),
            speech_mean_logprob=min(0.0, _mean(logprobs)) if have_lp else None,
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
# PARTIAL means something different for audio than for video.
_PARTIAL_LINES = {"speech": "The sentence could not be heard clearly enough to check"}
_NO_BASELINE_LINE = "There is no saved baseline to compare with yet"
_LOW_CONFIDENCE_LINE = "Some words were hard to make out"
_OTHER_SENTENCE_LINE = "A different sentence was used than at the usual check-in"
_UNKNOWN_SENTENCE_LINE = "The sentence used could not be confirmed"


def _num(value: object) -> float | None:
    """A finite float, or None for anything else (None, bool, str, NaN, inf, ...)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _usable(value: object) -> bool:
    v = _num(value)
    return v is not None and v >= 0


def _detail(result: object) -> dict:
    d = getattr(result, "detail", None)
    return d if isinstance(d, dict) else {}


def _quality_line(step: str, quality: Quality, detail: dict | None = None) -> str:
    if quality is Quality.NO_RESPONSE:
        line = _NO_RESPONSE_LINES[step]
    elif quality is Quality.PARTIAL and step in _PARTIAL_LINES:
        line = _PARTIAL_LINES[step]
    else:
        line = _QUALITY_LINES.get(quality, "The {step} could not be completed").format(step=_STEP_NAMES[step])
    # The flow asked the person to fix the picture and re-ran the step (onset/flow.py).
    retries = _num((detail or {}).get("framing_retries"))
    if retries is not None and retries >= 1:
        n = int(retries)
        line += f", even after {n} {'try' if n == 1 else 'tries'} to fix the camera view"
    return line


def _side(weaker_side: object) -> str | None:
    return weaker_side if weaker_side in ("left", "right") else None


def _signed_from_lifts(lift_left: object, lift_right: object) -> float | None:
    """Smile asymmetry with a sign: positive = the person's left lifted less."""
    ll, lr = _num(lift_left), _num(lift_right)
    if ll is None or lr is None:
        return None
    strong = max(ll, lr)
    if strong <= 0:
        return None
    a = min(1.0, max(0.0, 1.0 - max(min(ll, lr), 0.0) / strong))
    return a if ll < lr else (-a if lr < ll else 0.0)


def _signed_from_side(value: object, weaker_side: object) -> float | None:
    v, side = _num(value), _side(weaker_side)
    if v is None or side is None:
        return None
    return v if side == "left" else -v


def _moved_toward_weaker(today: float | None, base: float | None, threshold: float) -> tuple[bool, str | None]:
    """Did a signed measure (positive = left weaker) move toward today's weaker side by at
    least `threshold`? A move back toward even is an improvement and never counts."""
    if today is None or base is None or today == 0:
        return False, None
    move = today - base if today > 0 else base - today
    return move >= threshold - _EPS, ("left" if today > 0 else "right")


def _face_findings(face, baseline: Baseline | None, t: Thresholds, changes: list, unclear: list) -> None:
    if face.quality is not Quality.OK:
        unclear.append(_quality_line("face", face.quality, _detail(face)))
        return
    if not _usable(face.asymmetry):
        unclear.append("The smile check did not give a usable measurement")
        return
    if baseline is None:
        return
    asym = float(face.asymmetry)
    side = _side(face.weaker_side)
    changed = asym - baseline.face_asymmetry >= t.face_asymmetry_increase - _EPS
    # The weaker side switched from the baseline's, above the noise level.
    b_side = _side(baseline.face_weaker_side)
    if not changed and b_side and side and side != b_side and asym >= t.face_side_noise - _EPS:
        changed = True
    # Signed (left minus right) asymmetry moved toward one side by the threshold.
    if not changed:
        fd = _detail(face)
        today = _signed_from_lifts(fd.get("lift_left"), fd.get("lift_right"))
        if today is None:
            today = _signed_from_side(asym, side)
        base = _signed_from_lifts(baseline.face_lift_left, baseline.face_lift_right)
        if base is None:
            base = _signed_from_side(baseline.face_asymmetry, b_side)
        changed, moved_side = _moved_toward_weaker(today, base, t.face_asymmetry_increase)
        side = side or moved_side
    if changed:
        changes.append(
            f"The {side} side of the smile lifted less than usual" if side
            else "One side of the smile lifted less than usual"
        )


def _arm_findings(arms, baseline: Baseline | None, t: Thresholds, changes: list, unclear: list) -> None:
    if arms.quality is not Quality.OK:
        unclear.append(_quality_line("arms", arms.quality, _detail(arms)))
        return
    if not _usable(arms.drift_difference):
        unclear.append("The arm check did not give a usable measurement")
        return
    diff = float(arms.drift_difference)
    side = _side(arms.weaker_side)
    left, right = _num(arms.left_drift), _num(arms.right_drift)
    early = _detail(arms).get("early_drop")

    # Absolute rules (notes/arms.md): with or without a baseline.
    absolute = None
    if early:
        early_side = _side(early)
        absolute = (f"The {early_side} arm came down before the other" if early_side
                    else "One arm came down before the other")
    elif max((d for d in (left, right) if d is not None), default=-math.inf) >= t.arm_single_drift_absolute - _EPS:
        fell = "left" if (left if left is not None else -math.inf) >= (right if right is not None else -math.inf) else "right"
        absolute = f"The {fell} arm dropped while it was held up"
    elif diff >= t.arm_drift_difference_absolute - _EPS:
        absolute = (f"The {side} arm drifted down much more than the other" if side
                    else "One arm drifted down much more than the other")
    if absolute:
        changes.append(absolute)
    elif baseline is not None:
        changed = diff - baseline.arm_drift_difference >= t.arm_drift_difference_increase - _EPS
        b_side = _side(baseline.arm_weaker_side)
        if not changed and b_side and side and side != b_side and diff >= t.arm_side_noise - _EPS:
            changed = True
        if not changed:
            today = left - right if left is not None and right is not None else _signed_from_side(diff, side)
            if baseline.arm_drift_left is not None and baseline.arm_drift_right is not None:
                base = baseline.arm_drift_left - baseline.arm_drift_right
            else:
                base = _signed_from_side(baseline.arm_drift_difference, b_side)
            changed, moved_side = _moved_toward_weaker(today, base, t.arm_drift_difference_increase)
            side = side or moved_side
        if changed:
            changes.append(
                f"The {side} arm drifted down more than usual" if side
                else "One arm drifted down more than usual"
            )
    if not (_usable(arms.hold_seconds) and arms.hold_seconds >= t.arm_min_hold_s - _EPS):
        unclear.append("Both arms were not held up for the full time")


def _speech_findings(sp, baseline: Baseline | None, t: Thresholds, changes: list, unclear: list) -> None:
    sd = _detail(sp)
    low_confidence = bool(sd.get("low_confidence"))
    if sp.quality is not Quality.OK:
        unclear.append(_LOW_CONFIDENCE_LINE if low_confidence else _quality_line("speech", sp.quality))
        return
    if not (_usable(sp.word_error_rate) and _usable(sp.words_per_minute)):
        unclear.append("The sentence check did not give a usable measurement")
        return
    if low_confidence:
        unclear.append(_LOW_CONFIDENCE_LINE)
    words = normalize_words(sp.expected) if isinstance(sp.expected, str) else None
    if baseline is not None:
        if not words:
            unclear.append(_UNKNOWN_SENTENCE_LINE)
            same_sentence = False
        else:
            same_sentence = words == normalize_words(baseline.sentence)
            if not same_sentence:
                unclear.append(_OTHER_SENTENCE_LINE)
        wer = float(sp.word_error_rate)
        changed = wer - baseline.speech_word_error_rate >= t.speech_wer_increase - _EPS
        if not changed and words:
            # Word-count rule: one more wrong, missing or added word than the whole number of
            # errors the baseline averages escalates, whatever the sentence length.
            n = len(words)
            usual = math.floor(baseline.speech_word_error_rate * n + 1e-6)
            changed = wer * n >= usual + t.speech_word_errors_increase - _WORD_SLACK
        if changed:
            changes.append("The sentence came out less clearly than usual")
        if same_sentence:  # rate and confidence depend on the sentence
            slowest_ok = baseline.speech_words_per_minute * (1.0 - t.speech_rate_drop_fraction)
            if sp.words_per_minute <= slowest_ok + _EPS:
                changes.append("Speech was slower than usual")
            lp, base_lp = _num(sd.get("mean_logprob")), _num(baseline.speech_mean_logprob)
            if (not low_confidence and lp is not None and base_lp is not None
                    and lp <= base_lp - t.speech_logprob_drop + _EPS):
                unclear.append(_LOW_CONFIDENCE_LINE)
    pause = _num(sp.longest_pause_s)
    if pause is None or pause < 0:
        unclear.append("The pauses in the sentence could not be measured")
        return
    base_pause = _num(baseline.speech_longest_pause_s) if baseline is not None else None
    if base_pause is not None:
        limit = max(t.speech_pause_floor_s, base_pause + t.speech_pause_increase_s)
    else:  # no baseline, or an older baseline file without a pause value
        limit = t.speech_longest_pause_s
    if pause >= limit - _EPS:
        changes.append("There was a long pause while saying the sentence")


def _findings(results: CheckResults, baseline: Baseline | None, t: Thresholds) -> tuple[list[str], list[str]]:
    """(changes, unclear): plain-language lines, in step order. Never raises: a step whose
    results cannot be read is reported as not completed (UNCLEAR)."""
    changes: list[str] = []
    unclear: list[str] = []
    if baseline is None:
        unclear.append(_NO_BASELINE_LINE)
    for step, attr, check in (("face", "face", _face_findings), ("arms", "arms", _arm_findings),
                              ("speech", "speech", _speech_findings)):
        step_changes: list[str] = []
        step_unclear: list[str] = []
        try:
            check(getattr(results, attr), baseline, t, step_changes, step_unclear)
        except Exception as exc:  # odd inputs: never raise, never pass
            log.warning("%s results could not be read (%s); treating as unclear", step, type(exc).__name__)
            step_changes, step_unclear = [], [_quality_line(step, Quality.ERROR)]
        changes += [c for c in step_changes if c not in changes]
        unclear += [u for u in step_unclear if u not in unclear]
    return changes, unclear


def decide(
    results: CheckResults,
    baseline: Baseline | None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Decision:
    """Compare one check-in with the person's baseline. Rules only. Never raises.

    ESCALATE if any measurement moved past its threshold (or an absolute arm rule fired);
    otherwise UNCLEAR if any step's quality is not OK, a value (including the longest
    pause) is missing, words were hard to make out, the sentence differs from the
    baseline's, the arms were not up for `arm_min_hold_s`, or there is no (valid) baseline;
    otherwise NO_CHANGE_DETECTED. No baseline, or any unclear step, is never
    NO_CHANGE_DETECTED. `reasons` lists every change first, then every unclear item.
    """
    if baseline is not None:
        try:
            _validate_baseline(baseline)
        except Exception as exc:  # BaselineError, or something that is not a Baseline at all
            log.warning("baseline rejected (%s); treating as no baseline", exc)
            baseline = None
    if not isinstance(thresholds, Thresholds):
        thresholds = DEFAULT_THRESHOLDS
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
