# Chert support report: agent audio never reaches the caller on inbound FaceTime Video calls

**Project:** 38e8b4b9-5811-456e-9834-cac1d71bab63 (free trial, shared line "Kenji Naka", +1 646-391-6614)
**CLI:** @trychert/facetime 0.1.0-beta.20, run in WSL2 Ubuntu 26.04, Node 24.22.3
**Agent:** custom Python LiveKit agent (livekit-agents 1.8.12, livekit rtc 1.2.1), own LiveKit Cloud project, agent name `onset-38e8b4b9`
**Caller:** the project's verified iPhone, FaceTime Video

## Symptom

On every inbound FaceTime Video call the caller sees the agent's video (640x360, 15 fps) but hears no agent audio at all, for the whole call.

## What works

- The call is answered from a warm room in under 0.1 s.
- The caller's camera reaches the agent (1280x720, about 15 fps) and the agent's video is shown on the phone.
- `chert dev` prints `call_media state ok`: "Video and audio are coming from agent-AJ_...".
- The bridge participant subscribes to the agent's audio and video tracks (LiveKit `local_track_subscribed` fires for both, about 0.2 s after the bridge joins).
- The worker logs `agent_connected` in the same second the agent starts speaking, on every call.
- The call is not ended for `remote_audio_timeout`; each call ran until the caller hung up (`remote_ended`).
- The same agent audio is clearly audible to an ordinary LiveKit client in a test room (peak about -5 dBFS, mono, published as SOURCE_MICROPHONE by AgentSession's room output; one audio track, unmuted).

## Calls (UTC, 14 Nov 2026)

| Call ID | bridge_ready | Agent speech started | agent_connected | Length | Agent speech during call |
| --- | --- | --- | --- | --- | --- |
| 5ae80193-9447-4318-aef6-cc7f3ff90b05 | 03:22:39 | 03:22:39 | not checked | 47 s | greeting, 5 s |
| de87a125-7f5b-4b0b-a9b6-34ade2e53b89 | 03:33:42 | 03:33:47.3 | 03:33:47 | 43 s | two lines, 9 s |
| 1984bdaf-2946-49d8-baec-789f51d8f7ed | 03:37:31 | 03:37:33.1 | 03:37:33 | 53 s | two lines, 9 s |
| c5eb991a-4117-4f7e-98a7-6f11524ab40b | 03:45:01 | 03:45:02.5 | 03:45:02 | 37 s | speech every few seconds for the whole call |

**The onboarding demo call was silent too.** On call c26d040f-e29d-4d4c-a4ba-01ebc0a111f4 (outbound, Chert's hosted demo agent, 13 Nov 23:27 UTC, 25 s) the caller answered and heard nothing, although the worker logged `agent_connected`. So the missing audio is not specific to the custom agent.

That demo call logged a `remote_video_ready` event after `agent_connected`. None of the inbound calls above has that event, although the caller did see the agent's video.

## Questions for Chert

1. Does the worker log show agent audio being played into FaceTime on these calls?
2. Is agent audio on inbound FaceTime Video calls expected to work for customer-managed agents on this line? The developer guide says "Tested with audio only."
3. Is anything required of the agent's audio track beyond one unmuted SOURCE_MICROPHONE track from the participant that also publishes the camera?

## Separate issue: relay drops

`chert dev` loses its relay connection often (`closed_by_relay`, and "no heartbeat from the relay for 5 s"): 5 reconnects during call de87a125, one taking 17.9 s. One incoming call (8540d757-de18-4fab-8bf9-27cdfc823495, 03:23:38) was answered and then failed within 2 s when the relay closed the connection at that moment; the caller saw "call unavailable".

## Separate issue: Windows

On Windows 11 (PowerShell 5.1, standard user) every command fails with `windows_private_storage_required`: the ACL script's `Set-Acl` succeeds the first time on a folder and then throws `PrivilegeNotHeldException` (SeSecurityPrivilege) on any folder it has already protected, and `~/.chert` is protected again on each run.
