# decide (piece 4): baselines and decision rules

Status: in progress. Draft PR open.

## What works
- `onset/decide.py`: `save_baseline` / `load_baseline` (atomic, 0600 file, 0700 dir, validated;
  corrupt = no baseline = UNCLEAR), `baseline_from` (mean of OK check-ins, sample count),
  `decide` (rules, `Thresholds` dataclass), `caregiver_summary` (< 500 chars).
- `tests/test_decide.py`.

## Left
- `docs/thresholds.md`; align defaults with notes from face/arms/speech.
