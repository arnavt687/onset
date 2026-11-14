# Piece 7: recorder, replay and CI (`harness`)

Branch `piece/harness`, issue arnavt687/onset#8. Files: `tools/record.py`, `tools/replay.py`,
`tests/conftest.py`, `tests/test_harness.py`, `.github/workflows/ci.yml`, this note.

## Status

- Works: opt-in recorder (MP4 or JPEG frames + timestamp sidecar, WAV audio, `meta.json`),
  replay loader yielding `Frame`s / `AudioClip`, replay CLI that runs whatever meters exist,
  shared fixtures and markers, CI workflow. `pytest -q tests`: 23 passed in WSL.
- Left for the main session: wire `Recorder` into `agent.py` (sketch below). No new packages needed
  (uses numpy, opencv and the standard library).
- Found on `main`: plain `pytest -q` from the repo root also collects `tools/tts_test.py`
  (pytest matches `*_test.py`), which calls `agent.make_tts()` at import: with keys present it
  spends TTS characters; without keys collection fails. CI therefore runs `pytest -q tests`.
  Suggested fix (outside this piece): add `pytest.ini` with `testpaths = tests`, or rename the script.

## Privacy rules the recorder keeps

- Off unless `ONSET_RECORD=1` (exactly `1`). Disabled recorders write nothing, not even a directory.
- Writes only under `recordings/` (gitignored; `ONSET_RECORD_DIR` moves it). Directories 0700, files 0600.
- Logs a WARNING line `[onset] RECORDING step '<step>' to <dir> ...` when a step's recording starts.
- Never slows the call: `add_frame` / `add_audio` only `put_nowait` onto bounded queues (32 frames,
  ~2 s); a background thread encodes and writes. When the writer is behind, frames are dropped and
  counted (`meta.json` → `frames.dropped`). A disk error stops recording, never the call.

## Recording a test call

On disk, per call: `recordings/<call-id>/<step>/` with `video_000.mp4` (a new segment when the frame
size changes), `frames.json` (one timestamp per written frame), `audio.wav` (16-bit mono), `meta.json`.
`ONSET_RECORD_FORMAT=jpg` writes `frames_000/000000.jpg ...` instead of MP4 (bigger, no codec needed).

Wiring for `agent.py` (main session; this piece does not edit it):

```python
from tools.record import Recorder, new_call_id, STEP_SMILE, STEP_ARMS, STEP_SENTENCE

call_id = new_call_id()                      # or the Chert call id
rec = Recorder.for_call(call_id, STEP_ARMS)  # no-op unless ONSET_RECORD=1
# in CallVideo.read_caller, per caller frame (img is upright RGBA; RGB also accepted):
rec.add_frame(img, time.monotonic() - step_t0)          # Frame.t: seconds since the step started
# per caller audio frame from rtc.AudioStream(track, sample_rate=16000, num_channels=1):
rec.add_audio(ev.frame.data, ev.frame.sample_rate, time.monotonic() - call_t0)  # AudioClip.t_start: since call start
rec.mark("smile", t)                         # smile step: when the "now smile" prompt ends
rec.set_meta(expected=sentence)              # sentence step: what the person was asked to repeat
await asyncio.to_thread(rec.close)           # at the end of the step: flushes, writes meta.json
```

Step names the replay CLI understands: `smile` (with a `smile` mark, or a separate `neutral` step),
`arms`, `sentence` (with `meta["expected"]`). Then run a call with `ONSET_RECORD=1` set in the
environment of `agent.py start` (main session only).

## Replaying

```bash
cd ~/onset-wt/harness
~/onset/.venv/bin/python tools/replay.py recordings/<call-id>             # all steps
~/onset/.venv/bin/python tools/replay.py recordings/<call-id> --step arms # one step
~/onset/.venv/bin/python tools/replay.py recordings/<call-id> --offline   # skip speech (network)
~/onset/.venv/bin/python tools/replay.py recordings/<call-id> --json      # machine-readable
```

It prints each step's frame count, size, duration, fps, dropped frames, audio length and marks,
then the meter's status: `ok` with the result fields, `not implemented yet` for a stub,
`not importable`, or `error (...)`. Models come from `--models`, `$ONSET_MODELS`, `./models` or
`~/onset/models`.

From Python (for piece tests on recorded calls):

```python
from tools.replay import load
rec = load("recordings/<call-id>")
frames = list(rec["arms"].frames())               # onset.types.Frame
neutral = rec["smile"].frames(None, rec["smile"].mark("smile"))
clip = rec["sentence"].audio()                    # onset.types.AudioClip
```

## Shared test fixtures (`tests/conftest.py`)

`make_frames(n, fps, width, height)` / `synthetic_frames` (1280x720 moving shapes; the frame number
is coded in the top-left blocks, read with `tests.conftest.frame_number`), `make_tone`, `make_silence`,
`tone_clip`, `silence_clip`, `models_dir`, `face_model_path`, `pose_model_path`.
Markers: `@pytest.mark.models` (skipped without the MediaPipe models), `@pytest.mark.network`
(skipped unless `ONSET_NETWORK_TESTS=1`; CI never sets it).

## CI

`.github/workflows/ci.yml`: on every push and pull request, Python 3.12, `pip install -r requirements.txt`
(pip cache), downloads the two MediaPipe models (URLs from `tools/setup-shared.sh`, cached), runs
`pytest -q tests`. No secrets.

## Next to try

- Once a real call is recorded: replay it through face/arms as they land, and keep a short
  consented clip outside git for regression checks (never commit recordings).
- Frame drop rate under load: check `meta.json` `frames.dropped` after the first recorded real call.
