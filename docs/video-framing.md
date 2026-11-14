# Video framing: why Onset sends an upright picture

Issue [#27](https://github.com/arnavt687/onset/issues/27). On a real FaceTime call the caller saw
Onset's picture zoomed in, with the captions cut off at both sides (`this is On` for
`Hello, this is Onset, ...`), and the video ran one to two seconds behind.

## The crop

Every Chert line has a `video_framing` setting, `fit` or `fill`. The caller's FaceTime window is
upright. With `fit` the whole picture is shown with bars around it. With `fill` Chert scales the
picture until it covers the upright window and throws the overflow away. For a wide 16:9 picture
that leaves only the middle third, which is where the caption text was.

The setting lives in the Chert account. Nothing in the CLI or the documented API changes it, and a
new line may default to either value, so Onset no longer depends on it:

- **The picture is upright by default: 540x960.** `ONSET_FRAME=portrait` (the default) turns
  whatever `OUT_WIDTH` and `OUT_HEIGHT` say upright and widens it to at least 540, so an old
  `.env.local` with `640` and `360` becomes 540x960 and `1280` and `720` becomes 720x1280. An
  upright picture loses nothing at the sides under `fill`.
- **Text keeps to a safe area.** If the caller's window is squarer than the picture (3:4 against
  9:16), `fill` trims the top and bottom strips instead. Captions, the step pill and the countdown
  bar all stay inside the centred 3:4 region (`overlay.safe_box`); the caption panel's ink runs on
  to the frame edge so there is no gap under it.
- **The doctor checks it.** `tools/demo.sh --check` has a `video framing` row: PASS for `fit`, PASS
  for `fill` with an upright picture, FAIL for `fill` with `ONSET_FRAME=landscape`.

`ONSET_FRAME=landscape` keeps the old wide picture, for a line you know is set to `fit`.

## The delay

Measured on the render path alone (caller frame in, finished frame out), per frame:

| Picture | Before | After |
|---|---|---|
| 640x360 | 9.5 ms | 2.9 ms |
| 1280x720 | 47.8 ms | 12.9 ms |
| 540x960 (new default) | | 6.9 ms |
| 720x1280 | | 9.5 ms |

At 15 frames a second there are 66 ms per frame for everything: rendering, the landmark drawing,
the dashboard JPEG, the encoder and MediaPipe. At 1280x720 rendering alone took 48 of them. What
changed:

- **Captions are blended only where they are.** Each layer used to be blended over the whole frame
  on every tick; now only the rows and columns it covers (`overlay.composite`).
- **Old caller frames are dropped, not queued.** The caller's video stream had an unbounded queue,
  so every frame Onset was too busy to take waited behind the last one and the picture fell
  further and further behind. It now keeps the newest frame only, and converts it off the event
  loop.
- **One encoding instead of three.** Simulcast encoded the picture at three sizes for subscribers
  that never asked for the small ones. It is off (`ONSET_SIMULCAST=1` turns it back on), with a
  bitrate cap sized to the picture.
- **No bursts.** When a tick runs late the loop skips it instead of sending several frames at once.

The agent logs `sending 540x960 video @ 15 fps ...` at the start of a call and
`video fell behind on N of the last M ticks` whenever it cannot keep up; if that line appears
often, use a smaller picture.

FaceTime, Chert's Mac and LiveKit each add their own delay in both directions. That part is
outside Onset; expect a fraction of a second even when the agent keeps up.

## Checking a fix on a real call

The full greeting, the full `Please look at the camera, and relax your face.` and the whole
`Step 1 of 3 · Smile` pill readable from both an iPhone and a Mac. `tools/fake_call.py --frames DIR`
saves what Onset sends, to compare against what the caller sees.
