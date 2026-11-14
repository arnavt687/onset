# Piece 1: smile asymmetry (`face`)

Branch `piece/face`, issue arnavt687/onset#2. Files: `onset/measure/face.py`, `tests/test_face.py`,
this note. No new packages (mediapipe, numpy, opencv only). No change to `onset/types.py`.

## Status

- Works: `FaceMeter` (`add_neutral`, `add_smile`, `result`, `draw`, `close`, context manager) and
  `measure_face(neutral, smile)`, on MediaPipe Face Landmarker in VIDEO mode.
- `pytest -q tests/test_face.py`: 26 passed, 1 skipped (20 synthetic landmark tests that need no
  images and no MediaPipe, plus 7 MediaPipe tests, one of which is the recorded-call test). The smoke tests render MediaPipe's own canonical face mesh
  (read from `face_landmarker.task`, no photo of anyone) and deform the mouth corners to make
  neutral, symmetric-smile and one-sided-smile clips. They are skipped without mediapipe or the model.
- Recorded calls: `test_recorded_calls_give_a_result` runs on every `recordings/*/smile/` call it finds
  (via `tools/replay.py` from piece 7) and is skipped when there are none, which is the case today
  and always in CI. No real call has been recorded yet, so **the thresholds below are not calibrated
  on real faces.**

## The metric

Per frame, from the landmarks (pixels):

1. Eye centers = midpoints of the eye corners (33/133 = the person's right eye, 263/362 = left).
   Face frame: origin between the eyes, x along the eye line, unit = inter-ocular distance (IOD).
   This removes head tilt (roll), distance to the camera and position in the frame.
2. Mouth corners 291 (person's left, image right) and 61 (person's right, image left) in that frame
   as (outward from the midline, up towards the eye line).
3. Neutral position of each corner = median over the usable neutral frames.
4. Smile frames: displacement from neutral, projected on the up-and-outward diagonal = **lift** (IOD).
   Only the peak frames count: those with total lift >= 0.5 x the 90th percentile of total lift
   (at least 5). This skips the reaction delay and the relaxing at the end. Lift per side = median
   over the peak frames.
5. `asymmetry = 1 - weaker_lift / stronger_lift`, in [0, 1]: 0 = symmetric, 0.5 = the weaker side
   lifted half as much, 1 = it did not lift (or moved down / in). `weaker_side` is the person's own
   side, or None when asymmetry < 0.05.

`detail` has `lift_left`, `lift_right`, `out_*`, `up_*` (median displacement per side), `peak_frames`,
`per_frame_asymmetry_median`, `usable_neutral`, `usable_smile`, `frame_status` counts, `yaw_deg_median`,
`roll_deg_median`, `iod_px_median`, `median_luma`, `face_found_fraction`, `motion_iod_per_s`,
`ms_per_frame`, `reason` (when not OK), and the thresholds used.

Head turn: yaw is estimated from the nose tip's sideways offset from the eye midpoint
(`atan(offset / 0.64 IOD)`, exact for the canonical face in orthographic view). On the rendered face,
MediaPipe gives a proxy of -2, 7, 18, 27, 38 deg for a true 0, 10, 20, 30, 40 deg. Frames with
|yaw| > 20 deg are not used. MediaPipe's own z values were not used: on the frontal render the two
outer eye corners differed by 12 px in z, which would have meant 6 deg of yaw that was not there.

## Quality gates (in this order)

| Quality | When |
|---|---|
| `NO_SUBJECT` | no frames, or a face in < 50% of all frames |
| `TOO_DARK` | median brightness of the face box (whole frame when no face) < 40 / 255 (checked first: darkness also causes missed faces) |
| `PARTIAL` | < 5 usable neutral or < 8 usable smile frames. A frame is not usable if there is no face, the head is turned > 20 deg, or an eye/mouth/nose key point is within 2% of the frame edge. `reason` names the main cause ("head turned away", "face cut off by the frame edge", "face lost in too many frames") |
| `MOTION` | median head speed (eye-midpoint movement and zoom, in IOD per second, within each phase) > 1.0 |
| `NO_RESPONSE` | the stronger corner lifted < 0.03 IOD (no smile) |
| `ERROR` | the landmarker raised on every frame |

`result()` never raises. One bad frame is caught and counted (`detail["errors"]`).

## Numbers

Synthetic landmarks (0.8 px jitter, IOD ~139 px, 2 s neutral + 3 s smile at 15 fps): symmetric
0.12/0.12 gives asymmetry < 0.05; 0.04 vs 0.12 gives 0.67 (true 0.667); an 18 deg tilt changes it by
< 0.03; 12 deg yaw keeps a symmetric smile < 0.08.

MediaPipe on the rendered face (IOD 140 to 265 px):

| Rendered smile (lift L / R, IOD) | asymmetry | weaker side |
|---|---|---|
| 0.15 / 0.15 | 0.07 | - |
| 0.00 / 0.15 | 0.90 (measured lift 0.01 / 0.09) | left |
| 0.15 / 0.00 | 0.65 (measured lift 0.12 / 0.04) | right |
| 0.00 / 0.10 (IOD 140 px, noisy background) | 0.89 | left |
| 0.00 / 0.10 (IOD 265 px, noisy background) | 0.61 | left |

**MediaPipe pulls the two corners together**: when only one corner moves, the landmarker moves the
other one too (its mesh prior is roughly symmetric), so a completely one-sided smile reads 0.6 to 0.9
instead of 1.0. How much depends on the side and the face size. Real droop will read lower than its true size. That is one more reason to compare
with the person's own baseline instead of using an absolute cut-off.

## Timing (CPU, WSL Ubuntu, 12 threads, 1280x720 RGB)

- MediaPipe `detect_for_video`: 8 to 9.5 ms per frame (median), p95 ~10 ms, at IOD 140 and 265 px.
- Whole `add_*` per frame including brightness: ~9 to 9.5 ms. `draw()`: ~0.3 ms.
- That is well under the 40 ms budget at 15 fps, so `process_every` defaults to 1 (every frame).
  `FaceMeter(process_every=2)` halves the cost if the agent's loop gets busy.
- `add_*` is synchronous. If the flow calls it from the asyncio loop, ~10 ms blocks per frame are
  fine at 15 fps. Otherwise wrap it in `asyncio.to_thread` (not thread-safe: one meter per thread).

## Suggested thresholds for piece 4 (`decide`), to calibrate on real recordings

- Use the face result only when `quality is OK`. Anything else makes the face step UNCLEAR.
- With a baseline: **ESCALATE when `asymmetry >= baseline.face_asymmetry + 0.15`.** Healthy
  run-to-run spread is likely ~0.05 to 0.10 (render noise was 0.07). MediaPipe shrinks real
  asymmetry, so 0.15 is a visible change.
- Also ESCALATE when the weaker side lifts less than half its baseline lift. This needs per-side
  lift in `Baseline` (`detail["lift_left"/"lift_right"]` already has the numbers). That would be a
  `types.py` change, which I did not make.
- Without a baseline the roadmap rule already says never NO_CHANGE_DETECTED. If a number is needed
  for wording: asymmetry >= 0.30 is a clear asymmetry on any face.
- `weaker_side` only matters when asymmetry is over the threshold. A side flip below 0.15 is noise.

## Usage (for the flow, piece 5)

```python
from onset.measure.face import FaceMeter
meter = FaceMeter()                    # finds ~/onset/models/face_landmarker.task (or $ONSET_MODELS)
for each caller frame during "look at the camera":  meter.add_neutral(Frame(rgb, t))
for each caller frame after "please smile":         meter.add_smile(Frame(rgb, t))
meter.draw(out_rgba, live={"step": "smile"})   # on the letterboxed output frame; landmarks are re-fit
result = meter.result(); meter.close()
```

`Frame.t` may restart at 0 for the smile phase or continue from the neutral phase. Both work:
timestamps are kept increasing internally. Feed RGB (RGBA works too; the alpha is dropped).
`draw()` maps landmarks with the same centered fit as `agent.letterbox`. It draws lips, eyes,
the eye line, each mouth corner and a line from its neutral position. At the bottom left it
writes `phase yaw tilt` and live `lift L / R / asym` during the smile, plus any `live` entries.

## Next to try

- Calibrate the thresholds on recorded calls: several neutral/smile takes per person, the healthy
  spread, and a simulated one-sided smile (only one corner pulled).
- MediaPipe blendshapes `mouthSmileLeft` / `mouthSmileRight` as a second, independent signal
  (`output_face_blendshapes=True`, a few ms more per frame).
- Pitch (looking down at the phone) is not corrected. Both corners move the same way, so it lowers
  asymmetry a little. Consider gating pitch if recordings show it matters.
