# Decision thresholds

**These numbers are prototype placeholders, not clinically validated values.** Onset is a
research prototype, not a medical device. Each threshold below was picked to err toward
ESCALATE (a false alarm costs a 911 call; a miss can cost far more), and every one of them
needs validation against recorded check-ins from real patients, with clinicians, before
anyone relies on it.

All thresholds live in one place: the `Thresholds` dataclass in
[`onset/decide.py`](../onset/decide.py) (`DEFAULT_THRESHOLDS`). `decide()` takes an optional
`thresholds=` argument, so a tuned set can be tried without editing the defaults.

## How the decision works

`decide(results, baseline)` compares one check-in with the person's own baseline (the mean of
several check-ins recorded on well days, see `baseline_from`). It is rules only:

1. **ESCALATE** if any measurement moved past its threshold in the worse direction.
2. Otherwise **UNCLEAR** if any step's quality is not `OK`, any needed value is missing or not
   a finite number, the arms were not held up long enough, or there is no valid baseline
   (missing, corrupt, or failing validation).
3. Otherwise **NO_CHANGE_DETECTED**.

ESCALATE and UNCLEAR lead to the same action: the person is told to call 911 now and the
caregiver gets a text. A value **exactly at** its threshold counts as a change. Moving in the
better direction (a more even smile, faster speech) is never a change.

Only changes relative to the person's own baseline are used, except the pause rule (below),
because people differ a lot: some smiles are naturally uneven, some people speak slowly.

## The thresholds

| Name | Default | Unit | Fires when |
|------|---------|------|-----------|
| `face_asymmetry_increase` | 0.10 | asymmetry index (0..1) | today − baseline ≥ 0.10 |
| `arm_drift_difference_increase` | 0.06 | shoulder widths | today − baseline ≥ 0.06 |
| `arm_min_hold_s` | 8.0 | seconds | hold < 8.0 s → UNCLEAR |
| `speech_wer_increase` | 0.15 | word error rate | today − baseline ≥ 0.15 |
| `speech_rate_drop_fraction` | 0.20 | fraction of baseline | words/min ≤ 80 % of baseline |
| `speech_longest_pause_s` | 1.5 | seconds (absolute) | longest pause ≥ 1.5 s |

### `face_asymmetry_increase` = 0.10

The smile check (`onset/measure/face.py`) reports `asymmetry = 1 − weaker_lift / stronger_lift`:
0 when both mouth corners lift equally, 1 when one side does not move at all. (The comment on
`FaceResult.asymmetry` in `onset/types.py` describes an older eye-distance-normalized
difference; face.py's ratio is what is actually returned, and these numbers assume it.)

Why 0.10: the face piece treats differences under 0.05 as measurement noise
(`MIN_ASYM_FOR_SIDE`), so the threshold is twice the noise floor. A clear one-sided facial
droop typically halves or removes one side's lift (an index of 0.5 or more), so 0.10 is far
below the change it is meant to catch. Expect false alarms from half-hearted smiles; that is
the intended trade-off for now.

### `arm_drift_difference_increase` = 0.06

The arm check (`onset/measure/arms.py`) measures how far each wrist moves down over the 10 s
hold, in shoulder widths, and reports `drift_difference = |left − right|`. Comparing the
*difference* cancels the drift both arms share from ordinary tiredness; a weak arm drifts
on its own.

Why 0.06: the arms piece treats side differences under 0.03 as noise (`SIDE_NOISE`), so this
is twice the noise floor, about 2–2.5 cm for a typical adult shoulder width. A clinical
pronator drift is usually a visible drop of several centimeters within 10 s.

### `arm_min_hold_s` = 8.0

The check is timed for 10 s. A drift measured over a much shorter hold underestimates the
drift, so a hold shorter than 8 s makes the arm step UNCLEAR even if its quality is `OK`. The
arms piece itself already returns NO_RESPONSE below 6 s (`MIN_HOLD_S`) unless one arm clearly
dropped first; that one-sided early drop arrives here with quality `OK`, a short hold and a
large drift difference, and is reported as ESCALATE (drift) plus the short-hold line.

### `speech_wer_increase` = 0.15

`word_error_rate` from `onset/speech.py` is a word-level edit distance after normalization:
0 is exact, 1 is nothing right, and it can go above 1 if many extra words are heard.

Why 0.15: in a typical 7–10 word sentence one wrong word is 0.10–0.14 and two are 0.20–0.29.
Speech recognition alone sometimes gets one word wrong on a good day, so the threshold sits
just above one word for most sentences (and at one word for sentences of six words or fewer).
The baseline's own average WER already absorbs a person's usual recognition errors.

### `speech_rate_drop_fraction` = 0.20

Words per minute over the spoken sentence. Fires when today's rate is 80 % or less of the
baseline rate. A person's rate on the same short sentence usually varies by about 10–15 %
between days; slowed, effortful speech is a common sign of a speech change, so a 20 % drop is
treated as a change. Speaking faster is never a change.

### `speech_longest_pause_s` = 1.5

The longest silence between words inside the sentence. This one is **absolute** (not relative
to baseline) because the `Baseline` contract does not store a pause value yet; it therefore
also applies when there is no baseline. A missing pause value (with quality `OK`) makes the
step UNCLEAR. In a short repeated sentence, a gap of 1.5 s or more is unusual even for slow
speakers. To make it relative, add `speech_longest_pause_s` to `Baseline` (a contract change,
own PR) and compare `today − baseline` instead.

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
