# Piece 2: arm drift (10 s hold)

Closes #3. Only `onset/measure/arms.py`, `tests/test_arms.py`, `notes/arms.md` and
`notes/run.sh` changed. No new packages, no change to `onset/types.py`.

## Status

- Done: `ArmMeter` / `measure_arms` with MediaPipe Pose Landmarker (full model, VIDEO mode),
  hold detection, drift per side, quality gates, `draw()` overlay, 22 tests passing
  (`pytest tests/test_arms.py`, about 8 s).
- Not done: calibration on real people. Every threshold below comes from synthetic tracks and
  a drawn figure, not from recorded calls. Once piece 7 recordings exist, replay a few holds
  from healthy volunteers (steady, deliberately drifting one arm, phone in the hand vs propped)
  and adjust the thresholds.

## How it measures

1. Each frame, MediaPipe gives shoulders, elbows and wrists. The frame is downscaled to 640 px
   wide first (same accuracy, steadier timing). Sides are the person's own. The image is not
   mirrored, so the person's left is on the image right. If MediaPipe swaps the skeleton's
   labels on a frontal view, the frame is re-labelled so that the "left" shoulder is always
   the one on the image right.
2. **Body frame.** Each wrist is expressed relative to the shoulders: the origin is the
   shoulder midpoint, the x axis runs along the shoulder line, y points down perpendicular to
   it, and the unit is the shoulder width ("sw"). The phone moving, tilting (roll) or coming
   closer moves the shoulders and wrists together, so it cancels. In a test, a hand-held
   wobble of 30 px, 3° and 4% zoom gives a raw left/right difference of over 0.1 sw. In the
   body frame that drops to under 0.05 sw.
3. **Wrist states.** A wrist counts as *in frame* when its visibility is at least 0.5. It is
   *below* when it is predicted past the bottom edge: a dropped arm, with the value clamped to
   the edge, which underestimates the drop. It is *above* when predicted past the top edge
   (up, but not measurable). Otherwise it is *missing*.
4. **`arms_up()`.** Both wrists are at most 0.25 sw below the shoulder line (or above the
   frame), and have stayed there, moving less than 0.2 sw, for 0.5 s. That covers arms forward
   at shoulder height and arms out to the sides. `hold_start()` is that moment, and the flow
   times 10 s from it.
5. **Hold end.** The earliest of: hold start + 10 s, the last frame, or both wrists down
   (≥ 0.75 sw below the line, or below the frame) for 0.3 s. One arm coming down does **not**
   end the hold. When the hold ends because both arms came down, the last 0.5 s (the lowering
   movement) is left out of the drift window.
6. **Drift per side** (positive = down, in sw). `end` = median of the last 1.5 s minus median
   of the first 1.5 s. `peak` = the largest 0.5 s rolling median minus the start level. This
   catches a drop that was corrected before the end. `drift = max(end, peak)`.
   `drift_difference = |left − right|`. `weaker_side` is the side that drifted more, or None
   when the difference is ≤ 0.03 sw.
7. **`detail`** has `hold_start`, `hold_end`, per-side `*_end`, `*_peak`, `*_start_level`,
   `first_down` (when each arm came down, if it did), `early_drop` (`"left"`, `"right"` or
   None), the motion numbers, `luma`, `fps`, `shoulder_width_px`, and `reason`.

## Quality gates (checked in this order)

| Quality | When |
|---|---|
| NO_SUBJECT | no frames, or both shoulders found in < 25% of frames (TOO_DARK instead if median luma < 45) |
| TOO_DARK | median luma < 20, whatever the tracker says |
| PARTIAL | arms never seen up and wrist positions unknown in ≥ 70% of frames (TOO_DARK if dim) |
| NO_RESPONSE | arms never raised; or the hold lasted < 6 s and both arms came down together |
| PARTIAL | < 60% of hold frames with both wrists measurable (e.g. arms raised out of the top of the frame), a 1.5 s window too empty, or shoulders < 6% of the frame width (too far, or side-on) |
| MOTION | after compensation: 90th-percentile shoulder-midpoint speed > 1.5 sw/s, or median residual wrist jitter > 0.06 sw/frame, or shoulder width p90/p10 > 1.35 (person turning, big zoom change) |
| OK | otherwise |

The exception to NO_RESPONSE: if one arm came down at least 1 s before the other, the hold
can be under 6 s and the result is still OK, with `early_drop` set. One arm falling is
the finding, not a non-response. Drift numbers are filled in whenever they can be computed,
even when quality is not OK (for logs). Piece 4 must ignore them unless quality is OK.

## Suggested thresholds for piece 4 (provisional, uncalibrated)

Scale: 1 sw is roughly 35–40 cm on an adult. A 10° drop of a straight arm held out to the
side moves the wrist about 0.25–0.3 sw. Held forward, the drop seen by the camera depends on
the camera angle, but it is of the same order.

- `quality != OK` → UNCLEAR (as the types already say).
- ESCALATE if `drift_difference ≥ 0.20` (about 7–8° more drop on one side), with or without a baseline.
- ESCALATE if `drift_difference − baseline.arm_drift_difference ≥ 0.12`.
- ESCALATE if `detail["early_drop"]` is set, or either side's drift is ≥ 0.5 (an arm fell).
- Do not escalate on both arms drifting equally (fatigue), since `drift_difference` stays
  small. Mention it in the caregiver text if both drifts are ≥ 0.4.
- Baseline: average at least 2 check-ins, and store `drift_difference` as the larger of them
  rather than the mean, so a noisy day does not become the new normal.
- Measured noise so far: in synthetic tracks with 1.5 px jitter, a steady hold reads under
  0.02 sw per side. MediaPipe on the drawn figure gave 0.03–0.04 sw on the arm that did not
  move. Real people will be noisier: re-check these numbers on recordings.

## Timing (CPU: Ryzen 5 PRO 5650U in WSL, full model, VIDEO mode)

| input width | `add()` median | p90 |
|---|---|---|
| 1280 (native) | 17.0 ms | 24.5 ms |
| **640 (default)** | **16.1 ms** | **16.9 ms** |
| 480 | 17.5 ms | 18.1 ms |

- Under the 40 ms budget, so it stays on the **full model, every frame**. `stride=2` exists if
  CPU gets tight.
- `draw()` with live numbers takes about 4 ms per call, and `result()` about 10 ms at the end
  of the step.
- Loading the model takes about 1.2 s. Create the `ArmMeter` before the arms step starts (for
  example while the agent is speaking the instructions), not on the first frame.

## For integration (piece 5)

```python
meter = ArmMeter()              # finds ~/onset/models/pose_landmarker_full.task (or $ONSET_MODELS)
# for every frame of the step (Frame.t = seconds since the step started, monotonic):
await loop.run_in_executor(one_thread_executor, meter.add, frame)   # VIDEO mode: one thread, in order
if meter.arms_up(): start the 10 s timer (meter.hold_start() is the exact time)
meter.draw(rgba)                # overlay: timer, L/R drift, shoulder-wrist lines, start-level ticks
# after hold_start + 10 s:
result = meter.result(); meter.close()
```

- Keep feeding frames until at least `hold_start() + 10 s`. Frames after that are ignored, so
  "you can lower your arms now" can be said at 10 s.
- If `arms_up()` has not happened after about 8 s, re-prompt once. A result without a hold is
  NO_RESPONSE.
- The prompt should ask for the hands to be visible: "prop the phone so I can see you down to
  your waist, then hold both arms straight out in front of you". Arms raised overhead out of
  the picture give PARTIAL.
- `draw()` assumes the overlay frame has the same orientation as the input. Mirror it after
  drawing if the overlay is shown as a selfie view. Overlay sizes other than the source size
  are scaled.

## Tests

`tests/test_arms.py` covers synthetic tracks (steady hold, one arm drifting on either side,
swapped MediaPipe labels, both drifting equally, arms out to the sides, moderate phone motion
compensated, drift found while the phone moves, heavy shaking = MOTION, never raised, both
lowered early, one arm drops suddenly, one-sided drop then both down early, drift then
corrected, no body, dark, wrists not visible, arms above the frame, `arms_up()` timing,
`live()`/`draw()`). It also has two MediaPipe tests (skipped if the model is missing): a 13 s
video of a figure **drawn with OpenCV at test time** (no image files, no real people) whose
left arm sinks about 25°, which must give OK with weaker side left; and blank frames, which must give
NO_SUBJECT.

Run: `wsl -d Ubuntu -- bash /home/arnav/onset-wt/arms/notes/run.sh` (append `-s` to see timing).

## Next ideas

- Pronation (palm turning over) is part of the clinical test but is not measured. The hand
  landmarks (index/pinky/thumb) could give a palm-rotation angle later.
- Arms forward toward the camera are foreshortened. If recordings show weak sensitivity
  there, the elbow-wrist angle could be used as a second measure.
