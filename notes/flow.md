# Piece 5: scripted call flow, caption-first overlay (`flow`)

Branch `piece/flow`, issue arnavt687/onset#6.

## What's here

| File | What |
|---|---|
| `onset/script.py` | Every spoken line and on-screen caption, in one place. No line says "fine"/"normal"/"healthy"/"stroke"; every closing mentions 911. |
| `onset/flow.py` | `CheckFlow`: source-agnostic state machine (frames, caller audio, a clock). Same code for live calls and replays. |
| `onset/overlay.py` | Caption panel (DejaVu Sans Bold, capitals ≥ 28 px at 640x360, wraps to ≤ 3 lines), countdown bar, step pill, letterbox. |
| `onset/voice.py` | `Speaker`: TTS once per line, disk cache `runs/tts-cache/`, writes each line's WAV to the run dir, emits `say`, plays into the call. |
| `agent.py` | Live agent: CheckFlow on Chert's `bridge_ready`; caller video → meters in a worker thread (frames dropped while busy); caller mic → `on_audio` (16 kHz); steady 15 fps render/send loop; optional whole-call recording (`ONSET_RECORD=1`, step `call`). |
| `tools/replay_flow.py` | Runs a recorded call (step `call`) through a fresh CheckFlow, writing `runs/live/` like a live call. `--fast`, `--no-voice`, `--patient`. |
| `tools/fake_call.py` | Private-room end-to-end test: `--video FILE` as the caller's camera, `--speak` (the fake caller says the sentence when the agent shows "Say it now"), `--frames DIR` saves the agent's video, prints the agent's event timeline. |
| `tools/fake_call_private.sh` | Runs this checkout's agent under `AGENT_NAME=onset-flowtest` on health port 8091, then a fake call, then stops it: never touches the Chert line. |
| `tools/render_captions.py` | Renders every caption at 640x360 to `runs/caption-samples/` and prints font/cap sizes. |
| `tests/test_flow.py` | 34 tests: every path, the event sequence, banned words, caption sizes, record → replay. |

## The flow (default `Timing`)

greet (5 s) → face_neutral (2.5 s lead + 2 s measured) → face_smile (1.5 s lead + 3 s measured)
→ arms_wait (until `ArmMeter.arms_up()`, timeout 15 s → "That's all right. Let's move on.")
→ arms_hold (10 s from the meter's hold start, + 0.5 s) → sentence (6 s lead: "lower your arms"
+ the prompt; then "Say it now" and 7 s of caller audio) → deciding → closing.
Hard cap 90 s from start: unfinished checks become NO_RESPONSE → UNCLEAR.

- **First run (no baseline):** the baseline is saved only if all three checks are `ok`
  (`step baseline_saved`, then the "your usual check-in is recorded" closing). Otherwise the
  "let's try again later; if you feel unwell, call 911" closing. No `decision` event, no text.
- **With a baseline:** `decide()` → `decision` event. ESCALATE/UNCLEAR: the 911 closing is shown
  and spoken first, then the caregiver text runs in the background (it can take ~50 s) and
  reports through the `caregiver` event; `run_ended` follows it. Dry run unless
  `ONSET_CAREGIVER_SEND=1` and `ONSET_CAREGIVER_PHONE` are set (dry runs without a phone use a
  placeholder number and say so in `detail`).
- With a baseline the flow always uses the baseline's sentence (decide() treats another as UNCLEAR).
- Env: `ONSET_PATIENT_ID` (default `demo`), `ONSET_PATIENT_NAME`, `ONSET_CAREGIVER_PHONE`,
  `ONSET_CAREGIVER_SEND`, `ONSET_FACE_EVERY` (process every Nth face frame), `ONSET_RECORD`,
  `ONSET_HEALTH_PORT` (run a second agent beside the live one).
- Caption placement: top during the face steps (one short line, so it does not cover the face),
  bottom otherwise; countdown bar and step pill on the opposite edge.

## One full fake call (synthetic camera, fake caller speaks the sentence)

```
 7.00 run_started  patient flowtest, no baseline, "You can't teach an old dog new tricks."
 7.00 step greet           "Hello, this is Onset. A one-minute check-in."
10.12 say  greeting (audio/001.wav)        first audible agent audio 3.9 s after bridge_ready (cold TTS cache)
12.01 step face_neutral    "Look here. Relax your face."
16.54 step face_smile      "Big smile! Hold it."
21.08 result face  no_subject (synthetic camera: no face)
21.08 step arms_wait       "Raise both arms straight out. Keep them up."
36.14 result arms  no_subject (15 s timeout)
36.15 step sentence        "Get ready to say: “...”"
42.21 step sentence        "Say it now: “...”"      (fake caller speaks)
49.28 step deciding
49.63 result speech ok     (Scribe heard the fake caller's sentence)
49.64 step closing         "Let's try again later. If you feel unwell, call 911."  (baseline not saved: face not ok)
49.77 run_ended
50.75 say  closing line (audio/008.wav)
```

Agent video: 640x360, ~15 fps (705 frames). 20 s of audible agent speech, peak -5 dBFS.

## Known / left

- `say` events can arrive after `run_ended` (the speaker is asynchronous): the dashboard should keep
  playing audio that arrives after the run ends.
- First call after a voice/text change pays one TTS synthesis per line (~0.3–1 s each); the cache
  warms at job start, so the greeting normally starts instantly.
- Face and arm meters have only been exercised end to end on synthetic frames here: a real
  recorded call (`ONSET_RECORD=1`) is needed to check them on a person, then `tools/replay_flow.py`.
- `recordings/<id>/call/` is one continuous step; `tools/replay.py`'s per-step meter runner does not
  read it (use `tools/replay_flow.py`).
