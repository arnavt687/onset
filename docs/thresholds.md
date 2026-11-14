# Decision thresholds

**These numbers are prototype placeholders, not clinically validated values.** Onset is a
research prototype, not a medical device. Each threshold below was picked to err toward
ESCALATE (a false alarm costs a 911 call; a miss can cost far more), and every one of them
needs validation against recorded check-ins from real patients, with clinicians, before
anyone relies on it.

All thresholds live in one place: the `Thresholds` dataclass in
[`onset/decide.py`](../onset/decide.py) (`DEFAULT_THRESHOLDS`). `decide()` takes an optional
`thresholds=` argument, so a tuned set can be tried without editing the defaults. The one
number that lives in a meter instead is the speech confidence cut-off
`LOW_CONFIDENCE_MIN_LOGPROB` in `onset/speech.py` (it decides the speech step's quality).

## How the decision works

`decide(results, baseline)` compares one check-in with the person's own baseline (the mean of
several check-ins recorded on well days, see `baseline_from`). It is rules only:

1. **ESCALATE** if any measurement moved past its threshold in the worse direction, or an
   absolute arm rule fired (these apply with or without a baseline).
2. Otherwise **UNCLEAR** if any step's quality is not `OK`, any needed value is missing or not
   a finite number, words were hard to make out (low Scribe confidence), the sentence was not
   the baseline's sentence, the arms were not held up long enough, or there is no valid
   baseline (missing, corrupt, or failing validation).
3. Otherwise **NO_CHANGE_DETECTED**.

ESCALATE and UNCLEAR lead to the same action: the person is told to call 911 now and the
caregiver gets a text. A value **exactly at** its threshold counts as a change. Moving in the
better direction (a more even smile, faster speech) is never a change. `decide()` never
raises: results it cannot read make that step UNCLEAR.

Most rules compare with the person's own baseline because people differ a lot: some smiles
are naturally uneven, some people speak slowly. The exceptions are the absolute arm rules
and the pause fallback, below.

## Side by side with the measurement notes

The measurement pieces each suggested thresholds in their notes (`notes/face.md`,
`notes/arms.md`, `notes/speech.md`). Where decide was looser than a note it now follows the
note; where decide was already stricter it keeps its own value.

| Measure | decide threshold | Measurement notes | Direction |
|---|---|---|---|
| Smile asymmetry increase | `face_asymmetry_increase` ≥ +0.10 | face: +0.15 | decide stricter, kept |
| Smile weaker side switched | other side and asymmetry ≥ `face_side_noise` 0.05, or signed move ≥ 0.10 | face: side flip below 0.15 is noise | decide stricter (review fix) |
| Arm drift difference increase | `arm_drift_difference_increase` ≥ +0.06 sw | arms: +0.12 | decide stricter, kept |
| Arm weaker side switched | other side and difference ≥ `arm_side_noise` 0.03, or signed move ≥ 0.06 | arms: none | new (review fix) |
| Arm drift difference, absolute | `arm_drift_difference_absolute` ≥ 0.20 sw | arms: ≥ 0.20 | aligned (was missing) |
| One arm fell, absolute | `arm_single_drift_absolute` ≥ 0.50 sw on either side | arms: ≥ 0.5 | aligned (was missing) |
| Early one-sided drop | `detail["early_drop"]` set | arms: escalate | aligned (was missing) |
| Arm hold too short | `arm_min_hold_s` < 8.0 s → UNCLEAR | arms: < 6 s is NO_RESPONSE | decide stricter, kept |
| Baseline hold | `baseline_min_hold_s` ≥ 9.75 s | arms: 10 s hold | new (review fix) |
| Word error rate increase | `speech_wer_increase` ≥ +0.10 | speech: +0.10 | aligned (decide was 0.15, looser) |
| Wrong words, any length | `speech_word_errors_increase` 1 more word than the baseline's whole errors | speech: "one word adds 0.125–0.17" | new (review fix), stricter |
| Speaking rate drop | `speech_rate_drop_fraction` ≤ 0.80 × baseline | speech: < 0.70 × | decide stricter, kept |
| Longest pause | ≥ max(`speech_pause_floor_s` 1.0, baseline + `speech_pause_increase_s` 0.7) | speech: > max(1.0, baseline + 0.7) | aligned (decide was absolute 1.5); decide counts "at" as a change |
| Longest pause, no stored pause | `speech_longest_pause_s` ≥ 1.5 s | speech: none | fallback for older baselines / no baseline |
| Word confidence, absolute | min logprob < `LOW_CONFIDENCE_MIN_LOGPROB` −0.5 → PARTIAL → UNCLEAR | speech: min logprob < −0.5 → UNCLEAR | aligned (was missing) |
| Word confidence vs baseline | mean logprob ≤ baseline − `speech_logprob_drop` 0.15 → UNCLEAR | speech: drop > 0.15 → UNCLEAR | aligned (was missing); decide counts "at" |

"sw" = shoulder widths.

## The thresholds

| Name | Default | Unit | Fires when |
|------|---------|------|-----------|
| `face_asymmetry_increase` | 0.10 | asymmetry index (0..1) | today − baseline ≥ 0.10, or signed move toward one side ≥ 0.10 |
| `face_side_noise` | 0.05 | asymmetry index | weaker side ≠ baseline's and asymmetry ≥ 0.05 |
| `arm_drift_difference_increase` | 0.06 | shoulder widths | today − baseline ≥ 0.06, or signed move toward one side ≥ 0.06 |
| `arm_side_noise` | 0.03 | shoulder widths | weaker side ≠ baseline's and difference ≥ 0.03 |
| `arm_drift_difference_absolute` | 0.20 | shoulder widths | difference ≥ 0.20 (any baseline) |
| `arm_single_drift_absolute` | 0.50 | shoulder widths | either wrist drift ≥ 0.50 (any baseline) |
| `arm_min_hold_s` | 8.0 | seconds | hold < 8.0 s → UNCLEAR |
| `baseline_min_hold_s` | 9.75 | seconds | hold < 9.75 s → refused as a baseline |
| `speech_wer_increase` | 0.10 | word error rate | today − baseline ≥ 0.10 |
| `speech_word_errors_increase` | 1 | words | wrong words ≥ floor(baseline errors) + 1 |
| `speech_rate_drop_fraction` | 0.20 | fraction of baseline | words/min ≤ 80 % of baseline |
| `speech_pause_floor_s` | 1.0 | seconds | pause ≥ max(1.0, baseline + 0.7) |
| `speech_pause_increase_s` | 0.7 | seconds | (same rule) |
| `speech_longest_pause_s` | 1.5 | seconds (absolute) | pause ≥ 1.5 s when no baseline pause is stored |
| `speech_logprob_drop` | 0.15 | Scribe logprob | mean logprob ≤ baseline − 0.15 → UNCLEAR |

### `face_asymmetry_increase` = 0.10 and `face_side_noise` = 0.05

The smile check (`onset/measure/face.py`) reports `asymmetry = 1 − weaker_lift / stronger_lift`:
0 when both mouth corners lift equally, 1 when one side does not move at all, with the
per-side lifts in `detail["lift_left"]` / `detail["lift_right"]`.

Why 0.10: the face piece treats differences under 0.05 as measurement noise
(`MIN_ASYM_FOR_SIDE`, mirrored here as `face_side_noise`), so the threshold is twice the noise
floor. A clear one-sided droop typically halves or removes one side's lift (an index of 0.5
or more), so 0.10 is far below the change it is meant to catch. The face notes suggested
0.15; decide keeps the stricter 0.10. Expect false alarms from half-hearted smiles; that is
the intended trade-off for now.

**Side switching.** The unsigned index cannot see a person whose usually weaker right side is
now the stronger one and whose left is now weaker by the same amount. So the baseline stores
the weaker side and the mean per-side lifts, and decide also escalates when (a) today's weaker
side differs from the baseline's and today's asymmetry is at least `face_side_noise`, or
(b) the signed asymmetry (positive = left weaker) moved toward today's weaker side by at least
`face_asymmetry_increase`. A move back toward an even smile never counts. Baselines saved
before these fields existed have no side, so only the unsigned rule applies to them.

### `arm_drift_difference_increase` = 0.06 and `arm_side_noise` = 0.03

The arm check (`onset/measure/arms.py`) measures how far each wrist moves down over the 10 s
hold, in shoulder widths, and reports `drift_difference = |left − right|`. Comparing the
*difference* cancels the drift both arms share from ordinary tiredness; a weak arm drifts
on its own.

Why 0.06: the arms piece treats side differences under 0.03 as noise (`SIDE_NOISE`, mirrored
as `arm_side_noise`), so this is twice the noise floor, about 2–2.5 cm for a typical adult
shoulder width. The arms notes suggested 0.12; decide keeps the stricter 0.06. Side switching
works as for the smile, with the signed difference left − right drift and the baseline's mean
per-side drifts.

### Absolute arm rules: `arm_drift_difference_absolute` = 0.20, `arm_single_drift_absolute` = 0.50, early drop

From `notes/arms.md`, these escalate with or without a baseline, and whatever the baseline
says: a difference of 0.20 sw (about 7–8° more drop on one side), either wrist drifting down
0.5 sw or more (an arm fell; this also catches both arms falling, which the difference
misses), or `detail["early_drop"]` set by the arm meter (one arm came down at least 1 s
before the other). They apply only when the arm step's quality is `OK`; numbers from a step
that was not `OK` are never used.

### `arm_min_hold_s` = 8.0 and `baseline_min_hold_s` = 9.75

The check is timed for 10 s (`arms.HOLD_SECONDS`). A drift measured over a much shorter hold
underestimates the drift, so a hold shorter than 8 s makes the arm step UNCLEAR even if its
quality is `OK`. The arms piece itself already returns NO_RESPONSE below 6 s (`MIN_HOLD_S`)
unless one arm clearly dropped first; that early drop arrives with quality `OK` and a short
hold and is ESCALATE (early drop) plus the short-hold line.

A baseline must come from the full hold: `baseline_from` refuses a check-in whose hold was
under 9.75 s (10 s less a quarter second for frame timing).

### `speech_wer_increase` = 0.10 and `speech_word_errors_increase` = 1

`word_error_rate` from `onset/speech.py` is a word-level edit distance after normalization:
0 is exact, 1 is nothing right, and it can go above 1 if many extra words are heard.

Why: one wrong, missing or added word in the 8-word "You can't teach an old dog new tricks"
is 0.125, which the old 0.15 threshold let through. A single word change in a short repeated
sentence is exactly the kind of change the check exists to catch, so the rule is now
word-count based: escalate when today's wrong words reach the whole number of errors the
baseline averages plus one (`wer × n_words ≥ floor(baseline_wer × n_words) + 1`, with a
0.05-word slack for WER rounding), whatever the sentence length. The fraction rule
(+0.10, as `notes/speech.md` suggests) is kept alongside it. A person whose baseline takes
always had one recognition error needs a second wrong word; one whose takes had an error only
sometimes (a fractional mean) escalates on any wrong word. That errs toward false alarms.

### `speech_rate_drop_fraction` = 0.20

Words per minute over the spoken sentence. Fires when today's rate is 80 % or less of the
baseline rate. A person's rate on the same short sentence usually varies by about 10–15 %
between days; slowed, effortful speech is a common sign of a speech change, so a 20 % drop is
treated as a change. The speech notes suggested 0.7×; decide keeps the stricter 0.8×.
Speaking faster is never a change. Only compared when the sentence is the baseline's.

### `speech_pause_floor_s` = 1.0, `speech_pause_increase_s` = 0.7, `speech_longest_pause_s` = 1.5

The longest silence between words inside the sentence. With a stored baseline pause the rule
is the speech notes' `pause ≥ max(1.0, baseline_pause + 0.7)` (decide counts a value exactly
at the limit as a change; the notes used ">"). Clear speech paused at most 0.1 s in the speech
tests and injected breaks measured 1.96 s. Baselines saved before the pause was stored, and
check-ins with no baseline, use the absolute 1.5 s rule. A missing pause value (with quality
`OK`) makes the step UNCLEAR.

### Speech confidence: `LOW_CONFIDENCE_MIN_LOGPROB` = −0.5 (speech.py) and `speech_logprob_drop` = 0.15

Scribe rewrites mildly slurred words into the right ones ("teash ... twicks" came back as the
exact sentence, WER 0), so WER alone misses mild slurring. Its only trace is lower word
confidence. `check_sentence` sets quality `PARTIAL` and `detail["low_confidence"]` when any
content word's logprob is below −0.5 (garbled speech measured about −0.85), and also returns
`PARTIAL` when Scribe gave no confidence at all; decide reports the flag as UNCLEAR with the
reason "Some words were hard to make out". With a stored baseline confidence, a mean logprob
at least 0.15 below it is also UNCLEAR. The mild slurs in the speech tests only reached
−0.13 to −0.36, so −0.5 will not catch them on its own; lowering the cut-off needs recordings
of real callers first, because real phone speech is likely noisier than TTS.

### Different sentence

If today's sentence (normalized: case, punctuation and contractions ignored) is not the
baseline's, the step is UNCLEAR ("A different sentence was used than at the usual check-in").
WER and the pause are still compared (a large error still escalates); rate and confidence are
not, because they depend on the sentence.

## Longitudinal trends

`decide()` judges one check-in at a time. `onset/history.py` reads the archived check-ins
(`runs/<run_id>/events.jsonl`) and looks across them, to catch a slow drift that no single
call flags. It changes no outcome and adds nothing to the call: it is a report for the care
team (`tools/history_report.py`, and the dashboard's Trend panel).

**Which numbers.** Only numbers from steps whose quality was `OK`, the same ones `decide()`
uses. Each run's own `run_started` event carries the baseline it was compared with; the
trends use the latest run's baseline, or `baselines/<id>.json` if no run carried one.

**The line each metric is drawn against** is the main per-metric rule above, applied to that
baseline:

| Metric | Change threshold in the trend | From |
|---|---|---|
| Smile asymmetry | baseline + `face_asymmetry_increase` | 0.10 |
| Arm drift difference | min(baseline + `arm_drift_difference_increase`, `arm_drift_difference_absolute`) | 0.06, 0.20 |
| Left / right arm drift | `arm_single_drift_absolute` | 0.50 |
| Word error rate | baseline + `speech_wer_increase` | 0.10 |
| Words per minute | baseline × (1 − `speech_rate_drop_fraction`) | 0.80 × |
| Longest pause | max(`speech_pause_floor_s`, baseline + `speech_pause_increase_s`), or `speech_longest_pause_s` with no stored pause | 1.0, 0.7, 1.5 |

The side-switch, word-count and confidence rules have no single line to draw, so the trend
does not model them; they still decide each call.

**Trend settings** (`TrendSettings` in `onset/history.py`; prototype values like everything here):

| Name | Default | Meaning |
|---|---|---|
| `window_days` | 90 | rolling window, starting at the first check-in (the first 90 days home) |
| `compare_days` | 7 | the last 7 days against the 7 days before |
| `min_points` | 5 | measured check-ins needed before a gradual decline can be flagged |
| `min_correlation` | 0.6 | \|r\| of the least-squares line against time: "steady", not noise |
| `min_progress` | 0.5 | the fitted latest value has covered half the distance from baseline to threshold |

**Gradual decline** is flagged when all of these hold for a metric in the window: at least
`min_points` measurements; the least-squares slope points toward the threshold with
\|r\| ≥ `min_correlation`; the fitted value at the latest check-in is at least
`min_progress` of the way from the baseline (or, with no baseline, from the fitted first
value) to the threshold; and no check-in reached the threshold. A metric that did reach it
is reported as "threshold reached" instead, since a call at or past it normally escalated
already (the trend uses the latest baseline, which can differ from the one a call used). The report
also gives a straight-line estimate of the days left before the threshold; it is an
extrapolation, not a forecast.

## Tuning from recorded data

Use the recorder and replay tools (piece 7: `tools/record.py`, `tools/replay.py`) to build a
labelled set offline. Never tune on live calls.

1. **Collect well-day repeats.** For each volunteer, record 5–10 check-ins on different days
   while well. Replay them through the meters and keep only results whose three steps are `OK`.
2. **Measure day-to-day spread.** For each person and each measure, compute the difference of
   each check-in from the mean of the *others* (leave-one-out baseline). Pool across people.
3. **Set a floor from that spread.** A threshold below the 95th–99th percentile of those
   well-day differences will fire on ordinary days. Start each threshold at about that
   percentile, never below the meter's noise constant (`MIN_ASYM_FOR_SIDE`, `SIDE_NOISE`).
4. **Check sensitivity with change examples.** Use recordings with simulated changes (one-sided
   smile, one arm lowered, slurred or slowed TTS clips from the speech tests) and, when
   available, clinically labelled recordings. Each threshold must catch them; if raising a
   threshold to cut false alarms starts missing them, keep the lower value.
5. **Pass a tuned set** with `decide(results, baseline, thresholds=Thresholds(...))` in the
   replay runs, compare outcome counts against the labels, and only then change the defaults
   here and in `DEFAULT_THRESHOLDS`, with the data source and date recorded in this file.

The test `tests/test_decide.py::test_thresholds_live_in_one_dataclass` fails if a threshold is
added without being documented here.
