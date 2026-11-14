# Chert support report: no audio in either direction on line "Kenji Naka" (the Mac reports audio_ready: false)

**Project:** 38e8b4b9-5811-456e-9834-cac1d71bab63 (free trial, shared line "Kenji Naka", +1 646-391-6614)
**Line / worker:** cda385e6-cd61-48ab-b446-438343d28885 / 8fa251d8-0bf9-4d72-bd8d-8478d13badc7 (Mac build 262acc83…, runtime started 2026-11-11 04:51 UTC)
**CLI:** @trychert/facetime 0.1.0-beta.20, WSL2 Ubuntu, Node 24.22.3
**Caller:** the project's verified iPhone

## Summary

On every call, FaceTime Video or FaceTime Audio, there is no audio in **either** direction. Video works both ways.

- The caller hears nothing from the agent. This includes **your own video starter** (`chert new --video`) and your hosted demo agent on the onboarding call.
- The caller's voice reaches the agent as **digital silence**: the bridge participant's audio track measures -120 dBFS RMS, peak -90 dBFS (1 LSB), every second of the call, while the caller is talking.
- The line's own readiness, from `GET /api/v1/lines`, has reported this the whole time we have checked (04:17–05:00 UTC, 14 Nov):
  - `session_health.readiness.audio_ready: false`
  - `video_ready: true`
  - automatic check `system_audio_capture`: `at_call_start`. It never shows `passed`, while `audio_devices`, `playback_renderer` and `facetime_selections` pass.

We think the Mac's FaceTime audio capture and its playback into FaceTime are not working. Could you check the audio on this Mac, or move the project to a line whose Mac reports `audio_ready: true`?

## What works

- Calls are answered from the warm room in 0.0–0.1 s. `bridge_ready` arrives, and the room signal is set.
- Video both ways: the caller's camera reaches the agent (1280x720, ~15 fps), and the agent's camera is shown on the phone.
- `chert dev` prints `call_media state ok` ("Video and audio are coming from agent-AJ_…").
- The worker logs `agent_connected` within about 1 s of `bridge_ready` on every call, and never ends a call with `remote_audio_timeout`. So it appears to receive our audio.
- The bridge subscribes to the agent's audio track within 0.1 s of each publish.
- The same agent audio is clearly audible to an ordinary LiveKit client in a test room (peak about -5 dBFS).

## Calls (UTC, 14 Nov 2026 unless stated)

| Call ID | Type | Agent | Result |
| --- | --- | --- | --- |
| c26d040f-e29d-4d4c-a4ba-01ebc0a111f4 (13 Nov 23:27) | Video, outbound | Chert hosted demo | Silent to caller |
| 5ae80193-9447-4318-aef6-cc7f3ff90b05, de87a125-7f5b-4b0b-a9b6-34ade2e53b89, 1984bdaf-2946-49d8-baec-789f51d8f7ed, c5eb991a-4117-4f7e-98a7-6f11524ab40b, dce98a9b-f024-480e-800f-594bc2df0c23 | Video | Our Python agent (livekit-agents 1.8.12) | Silent to caller, `remote_ended` |
| **5959db32-fd95-4ff3-abaa-ce41aa7147c6** (04:27) | Video | **Your video starter**, unchanged except fixed ElevenLabs lines instead of OpenAI realtime | Silent to caller; 7 lines spoken; `agent_connected` 04:27:20 |
| **bcd803c5-d56e-4a3f-82a8-22baa61245b5** (04:55) | Video | Diagnostic agent: audio published before the bridge joined, continuous tone, loud and quiet levels, 48 kHz mono and stereo, 24 kHz, DTX off | Silent to caller in every variant; **caller audio -120 dBFS throughout** |
| **07343699-6820-429b-97fe-d8b256b6ab90** (04:57) | **Audio** | Same diagnostic agent | Silent to caller; **caller audio -120 dBFS throughout** |

The two diagnostic calls ended as `failed` / `unknown`. That happened when our agent unpublished its audio track and published a second replacement track (SOURCE_UNKNOWN, 24 kHz): the bridge left 0.1 s later. That was our test, not normal use, but you may want to know that a track swap ends the call this way.

## Questions for Chert

1. Why does this Mac report `audio_ready: false`, and why does `system_audio_capture` never pass? Can it be fixed, or the project moved to another line?
2. Do the worker logs for 5959db32, bcd803c5 and 07343699 show audio being played into FaceTime, and audio being captured from FaceTime?
3. Is there any per-line audio setting a customer must set? We found none in the CLI, the docs or `GET /api/v1/lines`.

## Separate issue: relay drops and a brief outage

`chert dev` loses its relay connection often: `closed_by_relay`, then "no heartbeat from the relay for 5 s". There were outages of 2.0 s, 18.6 s, 7.9 s and 20.9 s between 04:25 and 05:00 UTC, while plain HTTPS to app.trychert.com from the same machine answered in 0.2–0.6 s.

Around 04:30 UTC the line showed `offline` (check `control_plane_poll` failed), and every CLI request failed with `backend_unreachable` for about two minutes.

Some calls were lost because of this:
- 8540d757-de18-4fab-8bf9-27cdfc823495 (03:23): failed within 2 s.
- 7a288a4e-eaf7-4f63-a7b9-7e42d03d219b (03:37, FaceTime Audio): `webhook_timeout_declined`.
- A FaceTime Audio call at about 04:28: the iPhone showed "call unavailable", and there is no Chert record. It came during an 18.6 s outage, right after the previous call's cleanup.

## Separate issue: Windows

On Windows 11 (PowerShell 5.1, standard user) every command fails with `windows_private_storage_required`. The ACL script's `Set-Acl` succeeds the first time on a folder. After that it throws `PrivilegeNotHeldException` (SeSecurityPrivilege) on any folder it has already protected, and `~/.chert` is protected again on each run.
