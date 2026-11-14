# Onset pitch deck

One HTML file, `index.html`, with pinned local copies of its libraries in `vendor/`
(three.js 0.186.1, MediaPipe Tasks Vision 1.2.0 with its SIMD wasm, and the three fonts).
If a local copy is missing it falls back to the same pinned versions on jsDelivr.

```bash
tools/pitch.sh          # serves http://localhost:8770 and copies the MediaPipe models next to the deck
```

Open it in Chrome, press `F` for full screen. A local server is needed for the camera demo
(browsers only give camera access to `localhost` or https, and wasm will not load from `file://`).
Everything else also works when the file is opened directly.

## Keys

| Key | Does |
|---|---|
| `→` `Space` `PgDn` (clicker) | next slide |
| `←` `PgUp` | previous slide |
| `3` then `Enter` | go to slide 3 |
| `N` | speaker notes, timer and next slide at the bottom |
| `P` | presenter window (notes, timer, next), kept in sync with the main window |
| `T` | reset the timer |
| `B` or `.` | black screen |
| `M` | motion on / off (also follows the system's reduce-motion setting) |
| `?` | key help |
| Slide 5: `C` `U` `X` `O` | camera run, simulated usual / acted change / out of frame |

## Slides

1 Onset (hook) · 2 The first 90 days home · 3 Compare you with yourself · 4 Two check-ins, live ·
5 Try the measurements (browser demo) · 6 A recorded call (replay) · 7 How it works ·
8 Smile · 9 Arms · 10 Speech · 11 It never says "you're fine" · 12 Safety by design ·
13 What it can't do yet · 14 Team and ask.

In `docs/demo/run-of-show.md`, "slide 5 (Safety by design)" is slide 12 here and "slide 6
(Limits)" is slide 13: type `12` `Enter`.

## The browser demo (slide 5)

Labelled on the slide: a browser demo of the measurements; the real agent runs over FaceTime.
It runs MediaPipe Face Landmarker and Pose Landmarker on the webcam, the same captions and
step order as `onset/script.py`, and simplified rules from `docs/thresholds.md`
(asymmetry +0.10, arm drift difference +0.06, hold under 8 s unclear, longest pause 1.5 s,
pace 80 % of baseline). Speech is measured from microphone loudness only (no transcription,
no word error rate). The first camera run records a baseline in this browser; later runs
compare with it (`R` forgets it). The simulated caller has a fixed baseline from simulated
well days and ends in each of Onset's outcomes: no change detected, change, unclear.
Nothing is sent anywhere.

## Replay slot (slide 6)

Put screen recordings at `web/pitch/media/call-usual.mp4` and `media/call-change.mp4`
(gitignored), or drop a file onto the slide.

## Debug parameters

`?t=5&freeze` holds every timeline 5 s in (for stills), `?demo=change` starts a simulated
run on slide 5, `?still` forces the reduced-motion version, `?presenter` is the presenter window.
