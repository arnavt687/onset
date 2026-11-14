# Outbound check-in calls (research)

Piece 8 (issue #9). Question: can Onset FaceTime the person at check-in time, instead of
waiting for them to call, through `POST /api/v1/calls`, on the free trial or on Shared?

Sources, read on 2026-11-13: <https://app.trychert.com/skills.md> ("HTTP API", "Limits on
Shared"); docs.trychert.com pages *Outbound*, *Calls API*, *Bring Your Own Room* (token
requirements), *Limits and readiness*, *Feature availability*, *Errors*, *Give a personal
agent a Chert identity*; the CLI's bundled `DEVELOPER_GUIDE.md`, `CLI_GUIDE.md`,
`docs/CLI_ONBOARDING.md` and `dist/src` (`@trychert/facetime@0.1.0-beta.20`). No call has
been placed and no key exists, so everything below is from documentation, not a test.

## Findings

1. **The HTTP API documents outbound calls; the CLI does not do them.** The CLI guide's
   "not yet available" list (`DEVELOPER_GUIDE.md` §10) includes outbound calls, and
   `chert dev` is an incoming-call connector only ("it does not ... start an outbound
   call"). That is a CLI limit. The HTTP endpoint is documented in full (*Outbound*), listed
   in skills.md, and exposed in the JS SDK as `ChertControlPlane.createCall`. Onset calls the
   HTTP API directly, so the CLI limit does not block it.
2. **Shared: documented, with limits.** skills.md lists `POST /api/v1/calls` with
   `"destination": "<verified phone>"`, and a busy-line queue (`"when_busy": "queue"`)
   "for a busy Shared line", which only makes sense if Shared lines place outbound calls.
   `mode: "managed"` (a Chert-hosted assistant) needs "a line your project owns", so on a
   Shared line Onset must use **`mode: "customer_managed"`** (our own LiveKit room and
   agent). The destination must be a phone verified or joined for the project (see
   [chert-messaging.md](chert-messaging.md): the person texts `join <code>` once). Limits:
   50 FaceTime minutes a day, **5 minutes a call**, one call at a time on the line.
3. **Free trial: not stated.** Nothing says outbound calls are allowed or blocked on the
   trial. The trial's onboarding demo is an outbound FaceTime, but Chert places it itself
   (`POST /api/v1/onboarding/:id/demo`), not through `/calls`. The trial's iMessage rule
   ("the agent only replies, within 24 hours") may or may not extend to calls. Treat it as
   unknown until one approved test or an answer from Chert support.
4. **Video needs a line enabled for outbound video** "with fresh audio and video
   readiness"; inbound or audio readiness alone does not show it. Onset's checks need the
   caller's camera, so ask Chert whether the shared line can place outbound video. Caller
   video is also a separate opt-in ("caller capture").
5. **Line availability.** One active call per line. The trial/Shared line is also what
   `chert dev` holds for inbound calls; whether an outbound call can be placed while the
   inbound connector is connected is not documented (presumably yes, subject to the
   one-call rule).
6. **Base URL.** skills.md says `https://app.trychert.com/api/v1`; the docs site's FaceTime
   pages say `https://facetime.trychert.com/api`. Onset uses only `app.trychert.com` (the
   rule for this project: the key goes nowhere else).

**Verdict:** implementable on Shared per the docs, unverified on the trial. Implemented in
`onset/chert_api.py` as far as the docs go, dry run by default; a real call needs the
user's approval, a project API key, and the room side described below (main session).

## The documented contract

1. **Prepare the room first.** Create a LiveKit room; put the agent in it with exactly one
   unmuted audio publication (and, for video, its video track) but **silent**. Mint a
   separate bridge token for Chert: its own identity (`sub`, distinct from the agent's),
   `roomJoin` + `canPublish` + `canSubscribe` for exactly that room, integer `nbf`/`exp`
   with `exp - nbf <= 7200` s and **more than 90 s left** when the call is allocated.
   `wss://` URL on port 443, no credentials or fragment.
2. **Create the call.**

   ```http
   POST https://app.trychert.com/api/v1/calls
   Authorization: Bearer <ftk_ project key, facetime:write>
   X-Chert-Project-Id: <project UUID>
   Idempotency-Key: <8-200 chars, unique per call; reuse only to retry this call>

   {"direction": "outbound", "mode": "customer_managed", "line_id": "<line UUID>",
    "destination": "+15551234567", "destination_type": "phone",
    "media": {"audio": true, "video": true},
    "livekit_url": "wss://<project>.livekit.cloud", "participant_token": "<bridge JWT>",
    "remote_participant_identity": "<agent identity>"}
   ```

   - **201**: allocated (not dialed, not answered). `data.id` is the call id.
   - **202** with `queued: true` (only with `when_busy: "queue"`): waiting up to 2 min for a
     busy Shared line. Poll `GET /api/v1/calls/queue/{queue_id}`; on `ready`, repeat the
     same request with the **same key** and a valid token within 20 s.
     `DELETE /api/v1/calls/queue/{queue_id}` gives up.
   - **409**: the key was reused with different call fields.
   - A lost answer: retry with the same key (Chert returns the original call). Never switch
     to a new key until you know no call was created.
3. **Greet only on `bridge_ready`.** The bridge joins before the person answers. Poll
   `GET /api/v1/calls/{id}` (every 500 ms is what the CLI does) until `status` is `active`
   and `facetime_call_events` contains `bridge_ready` from `source: "worker"`. Greet once.
   Stop on `ended`, `failed` or `rejected`.
4. **End and clean up.** `POST /api/v1/calls/{id}/end` with its own idempotency key and
   body `{}` (HTTP 202), keep reading until terminal, then delete our room and dispatch.

## What is implemented (`onset/chert_api.py`)

| Function | Does |
| --- | --- |
| `mint_bridge_token(room, api_key=, api_secret=, ttl_s=1800)` | Offline: signs the bridge JWT with the grants above (identity `chert-bridge-<room>`, 120..7200 s) |
| `check_bridge_token(token, agent_identity)` | Offline: Chert's token rules, so a bad token fails before the API call |
| `place_check_in_call(destination, dry_run=True, livekit_url=, participant_token=, remote_participant_identity=, video=True, when_busy="fail")` | Validates, then (real mode) `POST /api/v1/calls`. Returns `dry_run`, `allocated` (`call_id`), `queued` (`queue_id`), `unknown` (answer lost; same key kept) or `error` |
| `get_call(call_id)` | `GET /api/v1/calls/{id}`: `call_status`, `bridge_ready`, `terminal` |

Not implemented: polling the busy-line queue, `POST /calls/{id}/end`, and the LiveKit side
(room creation and agent dispatch), which belongs to the main session's files
(`agent.py`, `onset/flow.py`). Tests: `tests/test_chert_api.py` (mocked).

## Wiring it into Onset (for the main session)

```python
from livekit import api
from onset import chert_api

room = f"onset-checkin-{uuid4()}"
async with api.LiveKitAPI() as lk:                       # LIVEKIT_URL/KEY/SECRET from .env.local
    await lk.room.create_room(api.CreateRoomRequest(name=room, empty_timeout=300, max_participants=3))
    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room, metadata='{"outbound": true}'))
    # wait until the agent has joined and published its (silent) audio and video tracks
    token = chert_api.mint_bridge_token(room, api_key=..., api_secret=..., ttl_s=900)
    r = await chert_api.place_check_in_call(person_phone, dry_run=False, livekit_url=LIVEKIT_URL,
                                            participant_token=token,
                                            remote_participant_identity=agent_identity)
    # poll chert_api.get_call(r["call_id"]) every 0.5 s; on bridge_ready set the room metadata
    # agent.py already waits for: {"chert_call": id, "chert_bridge_ready": true, "at": iso}
```

Points to watch:

- `agent.py` greets on the room metadata `chert_bridge_ready`, which `chert dev` writes for
  inbound calls. Nothing writes it for an outbound call, so the code that places the call
  must write it (as above) or the agent must poll `get_call` itself. Never greet on join.
- The agent's identity under `livekit-agents` is assigned at dispatch (`agent-<job id>`);
  Chert's current worker picks the sole eligible audio publisher anyway, but send the real
  identity.
- 5 minutes a call on Shared/trial: the 60-second check fits; keep the token to ~15 minutes.
- Consent: call only people who agreed, at a time they chose, and only a phone that joined.

## To find out with one approved test (or Chert support)

1. Does `POST /api/v1/calls` work on this trial project, or only on Shared?
2. Can the shared line place outbound **video**, and is caller capture on for it?
3. Can it place a call while `chert dev` is connected for inbound?
4. Response shapes: `data.status` values after 201, the queue receipt fields.

The test needs: a project API key in `.env.local` (user creates it), a phone that joined the
line and agreed to be called, the main session's go-ahead (it holds the line), then one
`place_check_in_call(..., dry_run=False)` followed by `get_call` until terminal.
