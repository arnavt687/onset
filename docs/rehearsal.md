# Demo-day rehearsal checklist

Onset on stage: the presenter FaceTimes the Onset line from a phone on a stand; the judges watch the
laptop (projected) running the dashboard. Everything below runs inside WSL Ubuntu in `~/onset`.

```bash
tools/demo.sh --check        # keys present, clock offset, line free, minutes left; starts nothing
tools/demo.sh --open         # agent + chert dev + dashboard; prints the line number; Ctrl+C stops all three
tools/demo.sh --replay recordings/<call-id>   # fallback: dashboard + a recorded call, no line needed
```

The dashboard is `http://localhost:8765`. Click once on the page to enable sound (the laptop speaks
the agent's lines, because callers cannot hear the agent over FaceTime yet).

## The day before

- [ ] **Laptop power and sleep.** Plugged in. Windows: Settings > System > Power > Screen and sleep:
      "Never" for both when plugged in. Turn off the screen saver. Close Teams/Slack/Outlook so no
      notification pops up on the projector; turn on Do Not Disturb (Focus).
- [ ] **WSL stays awake.** WSL shuts down idle distros; keep one Ubuntu terminal open the whole time.
- [ ] **Clock.** `tools/demo.sh --check` must show `clock pass`. Chert declines every call when this
      computer is more than 0.25 s ahead. Fix: Windows Settings > Time & language > Sync now; then in
      PowerShell `wsl --shutdown` and reopen Ubuntu (WSL's clock drifts after sleep).
- [ ] **Network: phone hotspot, not venue Wi-Fi.** Use a second phone's hotspot for the laptop (not
      the phone making the FaceTime call). Venue Wi-Fi often blocks UDP and WebRTC. The calling phone
      can use its own cellular data. Run a whole cold start on the hotspot at least once.
- [ ] **Phone stand and light.** Phone on a stand at a fixed spot, about 1.2 m (4 ft) from the
      presenter, portrait, camera at chest-to-face height so the face, shoulders and both raised arms
      fit in the frame (the arm check is unclear when the wrists leave the picture). One soft light
      in front of the presenter, nothing bright behind them (no window, no projector glare). Tape
      marks on the floor for the stand and for where the presenter stands.
- [ ] **Baseline for the demo person.** The first call for a patient id records their usual check-in
      (the dashboard shows "First check-in recorded as usual"). Make it on the stage set-up, same
      distance and light, with the presenter acting normal. See "Reset the baseline" below.
- [ ] **Cold start, twice.** From a fresh Ubuntu terminal (after `wsl --shutdown`): `tools/demo.sh
      --open`, wait for "ONSET DEMO READY", FaceTime the printed number, run the whole check, Ctrl+C.
      Do it twice in a row; the second time proves the line is released and re-taken cleanly. Note
      how long a cold start takes (first start downloads nothing new, but `npx` and MediaPipe warm up).
- [ ] **Record a fallback.** With `ONSET_RECORD=1` set, do one good "change" call and keep the
      recording in `recordings/`; check `tools/demo.sh --replay recordings/<call-id>` plays it on the
      dashboard. This is the plan B for the stage.
- [ ] **Caregiver text.** Decide: dry run (the bubble shows "Dry run: not sent") or a real send
      (`ONSET_CAREGIVER_PHONE`, `ONSET_LIVE_ACTIONS=1`, and the caregiver phone must have texted the
      line once). Do not switch on the day without a rehearsal.
- [ ] **Projector check.** Open the dashboard on the projector at the venue (or a TV) and read it from
      the back of the room. Browser full screen: F11. Zoom 100% (Ctrl+0). The layout is fixed 16:9.

## Trial minutes budget

The trial line has 50 FaceTime minutes, at most 5 minutes a call, valid for 24 hours from activation
(`tools/demo.sh --check` prints minutes left). A full check-in is about 1 minute; a call with set-up
and hang-up is about 2. Budget:

| Use | Calls | Minutes |
|---|---|---|
| Baseline for the demo person | 1–2 | 4 |
| Cold-start rehearsals (twice, day before) | 2 | 4 |
| Recording the fallback "change" call | 1–2 | 4 |
| Warm-up call 10 minutes before going on | 1 | 2 |
| The stage demo, plus one redial | 2 | 4 |
| Spare | | ~10 |

Stop rehearsing on the real line once you are under 20 minutes left: rehearse with
`tools/demo_events.py` and `--replay` instead (they use no minutes).

## 30 minutes before

- [ ] Laptop on the hotspot, plugged in, notifications off, Ubuntu terminal open.
- [ ] `tools/demo.sh --check`: keys, `clock pass`, `line_free pass`, minutes left.
- [ ] `tools/demo.sh --open`; wait for "ONSET DEMO READY" and the line number.
- [ ] Click the dashboard once ("Click to enable sound"), set the laptop volume, test with the room's
      speakers or a small speaker plugged into the laptop.
- [ ] Phone on its stand, on its marks, light on, phone charged, Do Not Disturb on the phone too (but
      FaceTime still works), the Onset number saved as a contact.

## 10 minutes before: warm-up call

- [ ] FaceTime the line, run the full check once (the agent and models are warm after this).
- [ ] Check the dashboard showed every step, the meters moved, the decision and the bubble appeared.
- [ ] Leave everything running. Do not Ctrl+C. Do not let the laptop sleep.

## If a call fails on stage

1. **Redial once.** Hang up, wait 5 seconds, FaceTime again. Say: "Live demos; let's try that again."
2. **Still failing: replay mode.** Ctrl+C the demo script (it releases the line), then
   `tools/demo.sh --replay recordings/<call-id>`: the dashboard runs the same pipeline on the
   recorded call, and the agent's voice plays in the room. The badge says "replay of a recorded
   call": say so out loud.
3. If the dashboard itself is stuck, reload the page (it rebuilds from the event log) and click once
   for sound.
4. Afterwards: `tools/stop.sh` and `pkill -f tools/dashboard.py` make sure nothing holds the line.

## Reset the baseline for the demo person

The flow uses the patient id from `ONSET_PATIENT_ID` (default `demo`) and keeps that person's usual
check-in in `baselines/<patient-id>.json` (owner-only, gitignored).

```bash
cd ~/onset
ls -l baselines/                                  # what is saved
mv baselines/demo.json baselines/demo.json.bak    # reset: the next call records a new baseline
# or use a fresh id for the presenter instead of deleting anything:
ONSET_PATIENT_ID=presenter-$(date +%d) tools/demo.sh --open
```

Order on the day: one baseline call acting normal (dashboard: "First check-in recorded as usual"),
then the demo call acting a change (a weaker smile on one side, one arm drifting down, a slow
sentence with a pause) shows "Change noticed → call 911".

## Rehearse without a call

```bash
~/onset/.venv/bin/python tools/dashboard.py &                       # http://localhost:8765
~/onset/.venv/bin/python tools/demo_events.py --outcome escalate    # or unclear | no_change | first_run
```

`demo_events.py` writes a synthetic run (a cartoon test frame, tones for the voice) through the same
event log as a real call. `--speed 4` runs it four times as fast; `--caregiver sent|failed` shows the
other bubble states. Screenshots of each outcome are in `docs/demo/screenshots/`.
