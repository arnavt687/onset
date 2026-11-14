# decide (piece 4): baselines and decision rules

Closes #5. Status: done (ROADMAP "done" column met), then updated by the review fixes
(branch `fix/review`, 2026-11-13; see "Review fixes" below).

## What is in this piece
- `onset/decide.py`
  - `save_baseline(b, *, base_dir=None)`: one JSON per patient at `baselines/<patient_id>.json`,
    validated before writing, temp file + fsync + `os.replace` (atomic), file 0600, directory
    created or tightened to 0700. Patient ids are checked so they cannot leave the directory
    (letters, digits, `_ + - .`, not starting with `.`; phone numbers like `+15551234567` work).
    Writes format version 2.
  - `load_baseline(id, *, base_dir=None)`: `None` if missing. A corrupt file (bad JSON, missing
    required or unknown fields, wrong version, NaN/negative/bool/string numbers, zero speaking
    rate, other patient's id, symlink, directory, oversize) is logged as a warning and returns
    `None`, which `decide` turns into UNCLEAR. Version 1 files (without the optional fields
    below) still load. A group/other-readable file is tightened to 0600 and logged.
  - `baseline_from(results, id, thresholds=DEFAULT_THRESHOLDS)`: mean of each value,
    `samples = len(results)`, plus the weaker sides (from mean signed left − right values), the
    per-side lifts and drifts, the mean longest pause and the mean Scribe confidence. Raises
    `BaselineError` (a `ValueError`) if any step quality is not OK, a value is missing, the
    sentences differ, an arm came down early (`early_drop`), the hold was under 9.75 s, or the
    check-in would itself have escalated or been unclear on the no-baseline rules.
  - `decide(results, baseline, thresholds=DEFAULT_THRESHOLDS)`: never raises. ESCALATE on any
    change past a threshold or an absolute arm rule; otherwise UNCLEAR on any non-OK step,
    missing value (pause included), low speech confidence, a different sentence, hold under
    8 s, or no valid baseline; otherwise NO_CHANGE_DETECTED. Value at threshold = change.
    `reasons` = changes first, then unclear items, plain language, no medical terms.
  - `caregiver_summary(decision, name, *, tz=None)`: < 500 chars, who/when/checks/what changed or
    was unclear/"was told to call 911 now"/"research prototype and cannot diagnose". The 911
    line always survives; extra reasons collapse to "and N more". Name is whitespace-collapsed
    and capped at 40 chars.
  - `Thresholds` dataclass: all numbers in one place.
- `docs/thresholds.md`: each threshold, why, a side-by-side table against the measurement
  notes, prototype disclaimer, how to tune from recordings.
- `tests/test_decide.py`: table-driven outcomes and boundaries, every quality failure per step,
  missing baseline, corrupt files, permissions, atomic write, path safety, summary length,
  banned words ("fine", "normal", "healthy", "stroke") in every output and string literal, and
  one regression test per review fix.

## Review fixes (2026-11-13)
1. Low speech confidence (`detail["low_confidence"]`, min logprob < −0.5 in speech.py) is
   UNCLEAR: "Some words were hard to make out". Mean logprob 0.15 below the baseline's too.
2. WER: +0.10 (was 0.15) plus a word-count rule, so one wrong word escalates at any sentence
   length (one word in the 8-word dog sentence is 0.125).
3. `Baseline` stores weaker sides and per-side values; a weaker side that switched, or a signed
   left − right move past the threshold, escalates.
4. Absolute arm rules: difference ≥ 0.20, either drift ≥ 0.5, or `early_drop`, with or without
   a baseline.
5. A different sentence from the baseline's is UNCLEAR, no longer a log warning.
6. Pause: `≥ max(1.0, baseline_pause + 0.7)`; baselines without a pause keep the 1.5 s rule.
7. `baseline_from` refuses early drops, short holds and check-ins that would themselves escalate.
8. Thresholds aligned with the measurement notes where decide was looser (table in docs).
10. `decide` never raises on odd inputs (`expected=None`, strings for numbers, missing steps).

## Alignment with the measurement pieces
- face: asymmetry is `1 - weaker/stronger` (ratio, as the `FaceResult.asymmetry` comment in
  `onset/types.py` now says), noise floor `MIN_ASYM_FOR_SIDE = 0.05` -> threshold 0.10.
- arms: drift in shoulder widths, noise `SIDE_NOISE = 0.03` -> 0.06; arms returns NO_RESPONSE
  under `MIN_HOLD_S = 6`; decide additionally makes holds under 8 s UNCLEAR.
- speech: WER can exceed 1.0 (handled; still ESCALATE); pause = largest gap between timed
  words; thresholds from `notes/speech.md` are in the side-by-side table.

## For the main session (integration)
- `BASELINE_DIR` is relative (`baselines/`), so run the agent from the repo root or pass
  `base_dir=`.
- `baseline_from` raising means "do not save this baseline, try again another day".
- No new packages needed.

## Not changed (and why)
- Baseline `arm_drift_difference` is still the mean, not the max the arms notes suggest:
  changing it would move every existing baseline's threshold; worth doing with recorded data.
- No absolute smile rule without a baseline (face notes mention 0.30 only "for wording"):
  without a baseline the outcome is already UNCLEAR, which leads to the same action.
- The −0.5 confidence cut-off does not catch the TTS mild slurs (−0.13 to −0.36); lowering it
  needs real-caller recordings first.

## Next to try
- Tune thresholds from recorded well-day check-ins (see docs/thresholds.md) once piece 7 lands.
