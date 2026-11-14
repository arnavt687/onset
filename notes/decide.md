# decide (piece 4): baselines and decision rules

Closes #5. Status: done (ROADMAP "done" column met). `pytest tests/test_decide.py`: 147 passed.

## What is in this PR (only files in the decide row, plus `notes/`)
- `onset/decide.py`
  - `save_baseline(b, *, base_dir=None)`: one JSON per patient at `baselines/<patient_id>.json`,
    validated before writing, temp file + fsync + `os.replace` (atomic), file 0600, directory
    created or tightened to 0700. Patient ids are checked so they cannot leave the directory
    (letters, digits, `_ + - .`, not starting with `.`; phone numbers like `+15551234567` work).
  - `load_baseline(id, *, base_dir=None)`: `None` if missing. A corrupt file (bad JSON, missing
    or extra fields, wrong version, NaN/negative/bool/string numbers, zero speaking rate, other
    patient's id, symlink, directory, oversize) is logged as a warning and returns `None`, which
    `decide` turns into UNCLEAR. A group/other-readable file is tightened to 0600 and logged.
  - `baseline_from(results, id)`: mean of each value, `samples = len(results)`; raises
    `BaselineError` if any step quality is not OK, a value is missing, or sentences differ.
  - `decide(results, baseline, thresholds=DEFAULT_THRESHOLDS)`: ESCALATE on any change past a
    threshold; otherwise UNCLEAR on any non-OK step, missing value (pause included), hold under
    8 s, or no valid baseline; otherwise NO_CHANGE_DETECTED. Value at threshold = change.
    `reasons` = changes first, then unclear items, plain language, no medical terms.
  - `caregiver_summary(decision, name, *, tz=None)`: < 500 chars, who/when/checks/what changed or
    was unclear/"was told to call 911 now"/"research prototype and cannot diagnose". The 911
    line always survives; extra reasons collapse to "and N more". Name is whitespace-collapsed
    and capped at 40 chars.
  - `Thresholds` dataclass: all numbers in one place.
- `docs/thresholds.md`: each threshold, why, prototype disclaimer, how to tune from recordings.
- `tests/test_decide.py`: table-driven outcomes and boundaries, every quality failure per step,
  missing baseline, corrupt files, permissions, atomic write, path safety, summary length, and
  banned words ("fine", "normal", "healthy", "stroke") in every output and every string literal.

## Alignment with the measurement pieces (checked their worktrees/branches 2026-11-13)
- face (`face.py`, not yet pushed): asymmetry is `1 - weaker/stronger` (ratio), noise floor
  `MIN_ASYM_FOR_SIDE = 0.05` -> `face_asymmetry_increase = 0.10`.
- arms (`arms.py`, not yet pushed): drift in shoulder widths, noise `SIDE_NOISE = 0.03` ->
  `arm_drift_difference_increase = 0.06`; arms returns NO_RESPONSE under `MIN_HOLD_S = 6`;
  decide additionally makes holds under 8 s UNCLEAR.
- speech (`piece/speech`): WER can exceed 1.0 (handled; still ESCALATE); pause = largest gap
  between timed words. No threshold numbers suggested in any notes yet; re-check before tuning.

## For the main session (integration)
- `onset/types.py`: the `FaceResult.asymmetry` comment describes an eye-distance-normalized
  difference, but face.py returns the ratio above. Worth fixing the comment (not my row).
- `Baseline` has no pause field, so the pause rule is absolute (1.5 s). Adding
  `speech_longest_pause_s: float | None = None` to `Baseline` would allow a relative rule.
- `BASELINE_DIR` is relative (`baselines/`), so run the agent from the repo root or pass
  `base_dir=`.
- If the sentence differs from the baseline's, decide compares anyway and logs a warning.
- No new packages needed.

## Next to try
- Tune thresholds from recorded well-day check-ins (see docs/thresholds.md) once piece 7 lands.
