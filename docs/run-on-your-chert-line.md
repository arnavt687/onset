# Run Onset on your own Chert line

From a fresh clone to a FaceTime check-in on your line, with no code changes. About 20 minutes,
most of it creating keys. Everything below runs in a terminal on Linux, WSL Ubuntu or macOS.

> Onset is a research prototype, not a medical device. Use it with people who know that.

## What you need first

| | Why | Where |
|---|---|---|
| Linux, WSL 2 Ubuntu, or macOS | The agent and the Chert CLI run here | On Windows, do everything inside Ubuntu (WSL) |
| Python 3.12 | The agent (`livekit-agents` 1.8) | `python3.12`, or [uv](https://docs.astral.sh/uv/) (setup uses it to fetch 3.12) |
| Node 20 or newer (24 recommended) | The Chert CLI (`@trychert/facetime`, run through `npx`) | [nvm](https://github.com/nvm-sh/nvm): `nvm install 24` |
| A LiveKit Cloud project | Carries audio and video between Chert and the agent | [cloud.livekit.io](https://cloud.livekit.io), free tier is enough |
| An ElevenLabs API key | Onset's voice, and Scribe speech-to-text for the sentence step | [elevenlabs.io](https://elevenlabs.io) > Developers > API keys |
| A Chert account with a line | Your FaceTime number; a Mac on Chert's side bridges the call | [trychert.com](https://trychert.com), `tools/chert.sh onboard` |
| An iPhone or Mac with FaceTime | To call the line | |

**The line's Mac should report `audio_ready: true`.** On one line, `false` meant calls connected
but were silent in both directions, and nothing on your side could fix it (see
[chert-audio.md](chert-audio.md)); another line reports `false` and works. `tools/demo.sh --check`
reads it for you (the `line readiness` row, a WARN): make a test call to be sure.

## 1. Clone and set up

```bash
git clone https://github.com/arnavt687/onset.git && cd onset
tools/setup.sh             # add --profiles to also create config/patients.json
```

`tools/setup.sh` is safe to run again; it skips whatever is already done. It:

- creates `.venv` with Python 3.12 and installs `requirements.txt`;
- downloads the two MediaPipe models into `models/` (and on Linux tells you if the EGL/GLES
  libraries MediaPipe needs are missing: `sudo apt-get install -y libegl1 libgles2 libgl1`);
- checks Node and that `tools/chert.sh --version` runs;
- copies `.env.example` to `.env.local` (owner-only, gitignored) if you have none.

`tools/setup.sh --dry-run` prints the steps without doing them.

## 2. Fill in `.env.local`

Open `.env.local` in an editor. Never commit it or paste its values anywhere; every tool here
prints names only.

| Name | Required | What it is, and where to get it |
|---|---|---|
| `LIVEKIT_URL` | yes | `wss://<project>.livekit.cloud`: LiveKit Cloud > your project > Settings |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | yes | LiveKit Cloud > Settings > API keys > Create key (same project as the URL). Alternative: `tools/chert.sh livekit` writes Chert's own LiveKit keys into `.env.local` |
| `AGENT_NAME` | yes | Any name unique to you, e.g. `onset-ab`. The agent registers under it and `chert dev` sends your calls to it |
| `ELEVENLABS_API_KEY` | yes | ElevenLabs > Developers > API keys. Needs Text to Speech and Speech to Text. Without it the sentence step can never be checked and every call ends "unclear" |
| `ELEVEN_VOICE_ID` | no | A different voice (default Sarah) |
| `GOOGLE_API_KEY`, `OPENAI_API_KEY` | no | Fallback voices, used only when there is no ElevenLabs key |
| `CHERT_API_KEY` | for texts | A project API key (`ftk_...`) from [app.trychert.com/developer](https://app.trychert.com/developer). Needed to text the caregiver; the doctor also uses it to read `audio_ready` |
| `CHERT_PROJECT_ID` | for texts | Your project's id (shown with the key, and by `tools/chert.sh whoami`) |
| `CHERT_LINE_ID` | no | Pin one line when the project has several |
| `CHERT_CLI_VERSION` | no | Pin another Chert CLI version for `tools/chert.sh` |
| `ONSET_PATIENTS_FILE` | no | Profiles file (default `config/patients.json`), see step 4 |
| `ONSET_PATIENT_ID`, `ONSET_PATIENT_NAME` | no | The one person this line serves, when there is no profiles file (default id `demo`) |
| `ONSET_CAREGIVER_NAME`, `ONSET_CAREGIVER_PHONE` | no | Their caregiver (phone in E.164: `+15551234567`), when there is no profiles file |
| `ONSET_CAREGIVER_JOIN` | no | `link` (default) or `off`; see [Escalation](#escalation-the-caregiver-joins-the-call) |
| `ONSET_CAREGIVER_TALK` | no | `1` (default): with the caregiver page, the caregiver can talk to the caller |
| `ONSET_CAREGIVER_PAGE` | no | Where you host `web/caregiver/` (e.g. GitHub Pages). Unset: a watch-only LiveKit Meet link |
| `ONSET_CAREGIVER_LINK_TTL_S` | no | How long the caregiver's link works (default 900 s) |
| `ONSET_LINK_SHORTENER` | no | `isgd` (is.gd) or `dagd` (da.gd): send a short link (about 20 characters) instead of the 450-character one. The shortener stores the link with its watch key, so keep it for demos. Default `off` |
| `ONSET_CAREGIVER_CALL` | no | `group`: Onset hangs up and rings the person and the caregiver together in one group FaceTime call (Chert must enable group calling on the line). `after`: once the person's call ends, Onset FaceTimes the caregiver alone. `off` (default). See [escalation.md](escalation.md) |
| `ONSET_PATIENT_PHONE` | for `group` | The person's own phone (E.164), so Onset can call them back |
| `ONSET_LIVE_ACTIONS` | no | `0` (default): dry run, nothing is sent. `1`: real iMessages; see [Going live](#going-live) |
| `ONSET_LIVE_ACTIONS_DENY` | no | Keep some kinds dry even when live: `imessage`, `call`, `invite` |
| `ONSET_SPEECH_VAD` | no | `1` (default) stops recording the sentence when the caller stops talking |
| `OUT_WIDTH`, `OUT_HEIGHT` | no | Size of the video Onset sends back (540x960, upright). 720x1280 is sharper and slower |
| `ONSET_FRAME` | no | `portrait` (default) turns the size upright so a line set to `fill` cannot crop the captions; `landscape` keeps it wide. See [video-framing.md](video-framing.md) |
| `ONSET_SIMULCAST` | no | `1` sends the video at three sizes again (default `0`: one, less work) |
| `ONSET_RECORD` | no | `1` records calls to `recordings/` (a face and a voice: keep private) |
| `ONSET_MODELS` | no | MediaPipe models somewhere other than `models/` |

## 3. Sign in to Chert

```bash
tools/chert.sh login       # signs this computer in to your Chert project (opens a browser)
tools/chert.sh doctor      # Chert's own check: session, clock, line, worker, minutes
```

No line yet? `tools/chert.sh onboard` sets up the account, verifies your phone and gives the
project a line. The free trial has a limited number of FaceTime minutes and calls of at most a
few minutes each; `tools/demo.sh --check` shows what is left (`chert minutes`).

## 4. Who the line serves (patient profile)

**One patient per line for now.** Chert does not yet tell the agent the caller's phone number,
so Onset cannot tell callers apart: every call uses one profile, its baseline and its caregiver.

The simplest setup is in `.env.local`:

```bash
ONSET_PATIENT_ID=alex            # names the baseline file baselines/alex.json
ONSET_PATIENT_NAME=Alex
ONSET_CAREGIVER_NAME=Sam
ONSET_CAREGIVER_PHONE=+15551230002
```

Or a profiles file (`tools/setup.sh --profiles` copies the example; it is gitignored because it
holds phone numbers). Set `"default"` to the person this line serves; the other entries are kept
for when Chert passes the caller's handle. See `config/patients.example.json` and
`onset/patients.py` for the format.

## 5. Clock

Chert declines calls when your computer's clock is more than 0.25 s ahead of Chert's. The doctor
measures the offset (`clock (ntp)`) and Chert's own reading (`chert clock`). To fix it:

- WSL: `sudo hwclock -s`, or from Windows `wsl --shutdown` and reopen Ubuntu. WSL's clock
  drifts after the laptop sleeps, so check again on the day.
- Windows: Settings > Time & language > Date & time > Sync now.
- Linux: `sudo timedatectl set-ntp true`. macOS: `sudo sntp -sS time.apple.com`.

## 6. Check everything

```bash
tools/demo.sh --check      # runs tools/doctor.py; starts nothing; exit 1 if anything FAILs
```

Each row is PASS, WARN (works, but read it), FAIL (fix before calling) or SKIP (not checked).
`tools/doctor.py --offline` skips the network rows; `--json` prints the same as JSON.

| Row | What it checks | If it fails |
|---|---|---|
| `env file` | `.env.local` exists and only you can read it | `tools/setup.sh`, `chmod 600 .env.local` |
| `livekit settings`, `agent name`, `voice key`, `scribe key` | Required names are set and not the example placeholders | Fill them in (step 2) |
| `chert api settings` | `CHERT_API_KEY` (`ftk_...`) and `CHERT_PROJECT_ID` | WARN only: without them nobody can be texted |
| `python packages` | The agent's packages import, on Python 3.12 | `tools/setup.sh` |
| `patient profiles`, `caregiver phone` | The profile calls will use, and that it has a caregiver phone | Step 4 |
| `safety gate` | Dry run or LIVE | See [Going live](#going-live) |
| `escalation` | `ONSET_CAREGIVER_JOIN`, the caregiver page and link lifetime | See [Escalation](#escalation-the-caregiver-joins-the-call) |
| `dashboard port` | Port 8765 is free | `pkill -f tools/dashboard.py`, or `tools/demo.sh --port N` |
| `mediapipe models` | Both models exist and load | Delete `models/` and run `tools/setup.sh` |
| `voice (tts)` | Synthesizes the greeting once and caches it, so Onset greets within Chert's 10 s window | The key, or the voice settings |
| `chert session`, `chert line`, `chert worker`, `chert line_free`, `chert minutes`, `chert clock` | From `tools/chert.sh doctor`: signed in, the line and its Mac are up, no other session holds the line, minutes left | `tools/chert.sh login`; wait for another session to end |
| `line readiness` | The line's Mac reports `audio_ready` and `video_ready` (Chert API, with your project key or the CLI sign-in). WARN only: the flag has been wrong | `audio_ready false`: make a test call; if it is silent, only Chert can fix it ([chert-audio.md](chert-audio.md)) |
| `video framing` | The line's `video_framing` against the picture Onset sends: `fill` crops a wide picture to its middle third | Remove `ONSET_FRAME=landscape` ([video-framing.md](video-framing.md)) |
| `clock (ntp)` | Offset from an NTP server; FAIL over 0.25 s, WARN over 0.1 s | Step 5 |
| `livekit reachable` | The LiveKit keys work (lists rooms) | Wrong key, secret or URL, or keys from another project |
| `elevenlabs key` | The key is valid and the Scribe model the sentence step uses exists (free endpoint, no transcription) | A new key with Text to Speech and Speech to Text |
| `caregiver page` | `ONSET_CAREGIVER_PAGE` answers 200 | Publish `web/caregiver/` |

## 7. Run it

```bash
tools/demo.sh              # add --open on WSL to open the dashboard in the Windows browser
```

It runs the doctor with `--warm` (every scripted line goes into the voice cache, a few seconds
the first time), stops on any FAIL (`--force` starts anyway), then starts the agent, connects the
line (`chert dev --agent <AGENT_NAME> --env-file .env.local`), starts the dashboard and prints the
number to call. Ctrl+C stops all three and releases the line. Logs are in `runs/demo-logs/`.

By hand, in three terminals: `.venv/bin/python agent.py dev`, `.venv/bin/python tools/dashboard.py`
and `tools/chert.sh dev` (it adds `--agent` and `--env-file .env.local` for you). Wait for
`agent_warm` before calling.

## 8. FaceTime the line

Call the number with **FaceTime Video** (not FaceTime Audio) from the iPhone or Mac.

- Onset answers and greets you, then shows each instruction as a caption on its video: relax your
  face, a big smile, both arms straight out until the bar fills, then repeat a sentence.
  The captions carry every instruction, so it works with the sound off.
- The dashboard at http://localhost:8765 (click once to enable sound) shows the caller's video,
  each step, the measurements and the result as they happen.
- **The first call records the baseline**: the person's own well-day numbers, saved to
  `baselines/<patient-id>.json`. Do it on a good day. Onset closes with "I've recorded your usual
  check-in".
- **Later calls compare** with that baseline: "No change from your usual check-in", or a change
  or unclear result, which tells the caller to call 911 and brings in the caregiver.

## Escalation: the caregiver joins the call

Short version; see [docs/escalation.md](escalation.md) for the details.

On ESCALATE or UNCLEAR, Onset tells the caller "I am adding your caregiver to this call now.
Please call 911." With `ONSET_CAREGIVER_JOIN=link` (the default), the caregiver gets an
iMessage with a short summary of what changed or could not be checked, plus a private link
(valid `ONSET_CAREGIVER_LINK_TTL_S`, 15 minutes by default) that opens this same call in their
browser: they see the caller and Onset's screen, with the summary as a caption.

- With the caregiver page (`ONSET_CAREGIVER_PAGE`, a static copy of `web/caregiver/`, e.g. on
  GitHub Pages) and `ONSET_CAREGIVER_TALK=1`, they can also talk: Onset relays their voice and
  camera to the caller inside its own audio and video (a FaceTime call through Chert carries one
  publisher, so the caregiver never joins the room directly).
- Without the page, the link opens LiveKit Meet, watch-only.
- `ONSET_CAREGIVER_JOIN=off` sends the summary iMessage only.
- FaceTiming the caregiver into the call is not possible: Chert cannot add anyone to a live
  FaceTime call.

The summary iMessage is always sent (in a dry run, it is shown on the dashboard instead).

## Going live

Until `ONSET_LIVE_ACTIONS=1`, every action is a dry run: logged and shown on the dashboard as an
`action` event with what would have been sent, and nothing leaves the computer. To send for real:

1. Set `CHERT_API_KEY` and `CHERT_PROJECT_ID` and the caregiver's phone (step 4).
2. The caregiver texts `join <code>` to the line's number once, over iMessage (the code is on the
   Messages page of the Chert console). Chert only lets a line text people who have texted it; on the free trial
   the window is narrower still. See [chert-messaging.md](chert-messaging.md).
3. Set `ONSET_LIVE_ACTIONS=1` in `.env.local`. The doctor's `safety gate` row turns into a WARN
   that says LIVE, and FAILs if the Chert API settings are missing.
4. Every action, dry or live, appears on the dashboard as an action event (kind, live or dry,
   status, masked phone).

## Troubleshooting

| What happens | Likely cause | Fix |
|---|---|---|
| The call connects but is silent both ways; the agent hears silence | The line's Mac has `audio_ready: false` | `line readiness` row; only Chert can fix it ([chert-audio.md](chert-audio.md)) |
| Calls are declined or end at once | Clock more than 0.25 s ahead | `clock (ntp)` row; step 5 |
| The call connects but Onset never greets | `chert dev` sends calls to a different `AGENT_NAME` than the running agent, or the agent is not running | Start both with `tools/demo.sh` from the same checkout |
| `chert dev` says `agent_not_warm`, `"my-agent" is NOT in the waiting room` | The CLI was started without `--agent` and `--env-file` | Use `tools/chert.sh dev` from this checkout: it adds both |
| `agent_not_warm` and the agent log says `worker is at full capacity` | An old checkout: LiveKit's `start` mode refuses work above 0.7 CPU load | `git pull` (the worker now never refuses a call: `ONSET_LOAD_THRESHOLD`, `ONSET_IDLE_PROCESSES` in `.env.example`) |
| The greeting comes late, then the call drops | The greeting was not cached, so it was synthesized during Chert's 10 s window | `tools/doctor.py --warm` (`tools/demo.sh` does it) |
| Onset sees no video, everything ends "unclear" | The call was FaceTime Audio, or `video_ready` is false | Call with FaceTime Video; `line readiness` row |
| Every sentence step is "unclear" | No ElevenLabs key, or Scribe rejected it | `scribe key` and `elevenlabs key` rows |
| `chert dev` says the line is in use | Another `chert dev` session holds the line | `chert line_free` row; wait, or ask its owner to stop (`tools/stop.sh` on their machine) |
| The caregiver gets nothing | Dry run, no caregiver phone, or they never texted `join <code>` | `safety gate` and `caregiver phone` rows; [Going live](#going-live) |
| The text is refused with `recipient_not_verified` | That phone has not joined the line (no "Verified" reply came back) | `tools/chert_join.py` shows the phrase in force, the number to text and what to check on the iPhone ([chert-messaging.md](chert-messaging.md)) |
| The caregiver's link only watches | No caregiver page | Host `web/caregiver/`, set `ONSET_CAREGIVER_PAGE` |
| The picture is zoomed in and the captions are cut off at the sides | The line's `video_framing` is `fill` and Onset is sending a wide picture | `git pull`; remove `ONSET_FRAME=landscape`; `video framing` row ([video-framing.md](video-framing.md)) |
| The video lags or stutters | The picture is too big for this computer (`video fell behind` in the agent log) | Remove `OUT_WIDTH`/`OUT_HEIGHT` from `.env.local` to use 540x960 |
| The dashboard does not start | Port 8765 is taken | `pkill -f tools/dashboard.py`, or `--port N` |
| MediaPipe fails to load on Linux | Missing EGL/GLES libraries | `sudo apt-get install -y libegl1 libgles2 libgl1` |

More: [rehearsal.md](rehearsal.md) for a demo-day checklist, and `tools/demo.sh --replay DIR`
to replay a recorded call on the dashboard without a line.
