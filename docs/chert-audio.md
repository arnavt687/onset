# Chert FaceTime calls: no audio in either direction

**Conclusion (14 Nov 2026, 05:00 UTC):** the audio problem is on Chert's Mac, not in Onset. Audio
fails in **both** directions on every call: the caller hears nothing from the agent, and the
caller's voice reaches the agent as digital silence. Video works both ways. Chert's own starter
agent and Chert's hosted demo agent are silent in the same way. The Mac's own readiness report says
`audio_ready: false`. Nothing in our agent can fix this. The line needs Chert to repair the Mac's
audio, or a different line.

Not ElevenLabs, and not "TTS vs LLM": the lab agent's 330 Hz tone is generated in our own code with no
TTS at all, and the caller still heard nothing. Chert's hosted demo (Chert's own voice stack) was
silent too. And no voice provider can affect the caller-to-agent direction, which is also silent.

## Evidence from Chert's API (no calls needed)

`GET /api/v1/lines` (CLI sign-in, read-only), line `cda385e6-…` "Kenji Naka", worker `8fa251d8-…`,
Mac build `262acc83…`, runtime started 2026-11-11 04:51 UTC:

| Field | Value |
| --- | --- |
| `session_health.readiness.audio_ready` | **false** (every read, 04:17–05:00 UTC) |
| `session_health.readiness.video_ready` | true |
| readiness check `system_audio_capture` | `at_call_start` (never `passed`) |
| checks `audio_devices`, `playback_renderer`, `facetime_selections` | passed |
| `video_framing` / `connecting_screen_dismissal` | `fit` / `audio` (line defaults, same on every call) |
| `status` | `available`, except about 04:31 UTC: `offline`, with check `control_plane_poll` `failed` |

`GET /api/v1/hosted-agent` answers 404 `hosted_agent_unavailable`, so no Chert-hosted agent is available as another control.
`/api/v1/lines/<id>` is refused to a CLI sign-in (`cli_scope_denied`); line settings cannot be read or
changed from the CLI, and there is no per-line audio setting in anything the CLI or docs expose.

## Hypotheses and tests

| # | Hypothesis | Test | Result |
| --- | --- | --- | --- |
| H1 | Our Python agent publishes audio wrongly | `tools/fake_call.py`: plain LiveKit client in a test room | Ruled out: agent audio audible, peak -5 dBFS, 0.4 s after bridge_ready |
| H2 | Something in our agent code (Python, AgentSession) | **Chert's own video starter** (`chert new --video`), Node, Chert's room handling unchanged; only change: fixed ElevenLabs lines instead of OpenAI realtime (no key). Call **5959db32-fd95-4ff3-abaa-ce41aa7147c6**, FaceTime Video | Ruled out: caller saw the starter's face and heard **nothing**. Mac logged `agent_connected` 1 s after `bridge_ready`; agent spoke 7 lines |
| H3 | ElevenLabs / TTS output (format, level) | Lab phases 1–4: locally generated tone, no TTS involved. Chert's hosted demo (call c26d040f, Chert's own stack) | Ruled out: tone inaudible, demo silent too |
| H4 | Audio must be flowing before FaceTime connects | Lab phase 1: track published at warm-room job start (0.16 s), continuous tone from then on, before the bridge joined | Ruled out: silent (calls bcd803c5, 07343699) |
| H5 | Too quiet / DTX cuts low levels | Lab phase 2: tone -9 dBFS + speech at full scale; phase 3: DTX and RED off | Ruled out: silent |
| H6 | Sample rate or channel count (48 kHz, stereo, 24 kHz) | Lab phase 1 (48k mono), 3 (48k stereo, name `agent-audio`), 4 (24k mono, `SOURCE_UNKNOWN`, name `audio`) | Ruled out: silent in all. Side effect: in both lab calls the bridge left 0.1 s after subscribing to the phase 4 track, and Chert marked the calls `failed` / `unknown`. So republishing a second replacement audio track (or one with `SOURCE_UNKNOWN`) crashes the bridge: don't do it in Onset |
| H7 | Only FaceTime Video calls are broken ("Tested with audio only") | Lab on a FaceTime **Audio** call, **07343699-6820-429b-97fe-d8b256b6ab90** | Ruled out: answered, `bridge_ready` and `agent_connected` logged, caller heard nothing |
| H8 | Caller side (iPhone volume, Bluetooth, mute) | The same iPhone hears friends on FaceTime; and the agent-side silence of H9 cannot come from the iPhone's speaker | Ruled out as far as we can test |
| H9 | Audio broken in the Mac's own capture/playback path | Lab logs the caller's audio as the agent receives it. Caller said "hello, one two three" | **Confirmed.** Caller audio from the bridge is digital silence for the whole call on both lab calls: RMS -120 dBFS, peak -90 dBFS (1 LSB), every second. Together with `audio_ready: false`, the Mac is sending and playing silence: its FaceTime audio capture and its playback into FaceTime are both broken |
| H10 | A different shared line / Mac | Read `chert onboard` code and docs | Not tried. `chert onboard --status`: session `complete`, the trial line "stays yours until your trial ends". `--restart` only starts a new onboarding intent: phone verification again, a new demo call (uses minutes), and very likely the same line. It cannot pick another Mac. Only Chert can move the project to another line |

## FaceTime Audio calls "never registered"

They do reach Chert. The first one, **7a288a4e-eaf7-4f63-a7b9-7e42d03d219b** (03:37 UTC), was
`rejected` / `webhook_timeout_declined`: `chert dev` did not answer within 10 s (most likely during a
relay drop). The one at about 04:28 UTC ("call unavailable" on the iPhone) has no Chert record at all.
It came while `chert dev` was reconnecting after an 18.6 s relay outage and right after the previous
call's cleanup (04:27:59). The next FaceTime Audio call (07343699, 04:57 UTC) was answered in 0.0 s.
FaceTime Audio works for signalling; it fails when Chert's side is mid-cleanup or the relay is down.

## Relay instability (Chert side, affects answering)

`chert dev` loses the relay repeatedly: `closed_by_relay`, then "no heartbeat from the relay for 5 s".
Outages of 2.0 s, 18.6 s, 7.9 s and 20.9 s within 35 minutes (04:25–05:00 UTC). Plain HTTPS to
app.trychert.com from the same WSL machine answered in 0.2–0.6 s at the same times. Once at about
04:30 UTC the CLI's API calls failed outright (`backend_unreachable`, doctor "Could not reach
Chert") while the line showed `offline`; that cleared within two minutes.

## Calls used (14 Nov 2026 UTC)

| Call | Type | Agent | Result |
| --- | --- | --- | --- |
| 5959db32-fd95-4ff3-abaa-ce41aa7147c6 | Video | Chert starter (Node) | Video OK, no audio heard; `remote_ended`, 45 s |
| (no record) about 04:28 | Audio | Chert starter | iPhone: "call unavailable"; never reached Chert |
| bcd803c5-d56e-4a3f-82a8-22baa61245b5 | Video | audio lab | Phases shown, nothing heard; caller audio -120 dBFS; `failed`/`unknown` after phase 4 swap |
| 07343699-6820-429b-97fe-d8b256b6ab90 | Audio | audio lab | Answered, nothing heard; caller audio -120 dBFS; `failed`/`unknown` after phase 4 swap |

About 3.5 FaceTime minutes used; 43 left on the trial afterwards.

## What Onset should do

- Nothing in `agent.py` changes: its audio is correct (H1, H2). Keep one audio track for the whole call and
  never republish it (H6 side effect).
- Send Chert the updated `CHERT_SUPPORT_REPORT.md`. Ask them to fix the Mac's audio (`audio_ready`,
  `system_audio_capture`) or to move the project to a line whose Mac reports `audio_ready: true`.
- Before any demo, check the line first:
  `node tools/chert_line_health.mjs <project-id>` prints `audio_ready` and the readiness checks.
- Until audio works, the demo can't use speech in either direction. The sentence-repeat step also needs the caller's
  audio, which arrives silent today.

## Tools added

- `tools/audio_lab.py`: diagnostic agent that steps through audio variants in one call and logs the
  caller's audio level. `LAB_AGENT_NAME` (default `onset-lab-38e8b4b9`). Don't run it on a real check-in.
- `tools/chert_line_health.mjs`: read-only line readiness (`audio_ready`, checks, heartbeat), using the CLI sign-in.
