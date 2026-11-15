# What we hit building on Chert, and what we propose

Onset is a FaceTime check-in for the 90 days after a stroke, built on Chert during Emory Hacks
2026 (13 to 15 Nov 2026). This is our feedback for the Chert team: five things that cost us
time, what we did about each on our side, and the change we would ask for. Everything here was
seen on two free-trial projects with the CLI `@trychert/facetime@0.1.0-beta.20/.21`.

Chert's code is not public, so these are proposals, not patches. Where we could work around a
limit in our own code, that code is linked and can serve as a reference.

| # | What we hit | Our workaround | What we'd ask for |
|---|---|---|---|
| 1 | No way to bring a second person into a FaceTime call | The caregiver joins through Onset's own audio and video, then Onset FaceTimes them afterwards | Add a participant to a live call |
| 2 | Nobody can see which phones have joined; a failed join gets no reply | A tool that reads what the owner's sign-in can see | List joined phones; reply to a bad join |
| 3 | `video_framing: fill` cropped our captions, with no warning | Upright video with a safe area | Warn in `chert dev`; make it settable |
| 4 | `chert dev` waited for `my-agent` with no LiveKit keys | A wrapper that passes `--agent` and `--env-file` | Read `.env.local` as the help says |
| 5 | A line's Mac reported `audio_ready: false` and calls were silent | None possible on our side | Say so in `chert doctor` and `chert dev` |

## 1. A second person cannot join the call

**What we needed.** When a check-in finds a change, the caregiver should be in the call with the
patient, on FaceTime, straight away.

**What Chert allows** (skills.md, docs.trychert.com: Group calls, Limits, Bring Your Own Room):

- no add, merge or transfer on a call in progress;
- one active call per line, so the line cannot ring the caregiver while the patient is on it;
- Group FaceTime only as a new outbound call (`recipients: [...]` on `POST /api/v1/calls`),
  enabled per line, so the patient would have to answer again;
- in our LiveKit room, exactly one participant may publish audio: a second audio publisher ends
  the call (`several_audio`).

**What we built instead.**

- The caregiver opens a link and joins the call's room **subscribe-only**. Their microphone and
  camera go to a side room; the Onset agent listens there and plays their voice through its own
  single audio track, and draws their camera picture-in-picture on its own video. The patient
  hears and sees the caregiver inside the same FaceTime call.
  Code: [`onset/relay.py`](../onset/relay.py), [`onset/caregiver.py`](../onset/caregiver.py),
  [`web/caregiver/`](../web/caregiver/), `Escalation` in [`agent.py`](../agent.py).
- On Chert's advice we also use a **group call**: Onset tells the patient it is calling straight
  back, hangs up, and starts one group FaceTime call to the patient and the caregiver
  (`recipients: [...]`), says what it found, and leaves them talking. It works on FaceTime
  itself, but the patient has to be hung up on and answer again, in the middle of an alert.
- Or, once the patient's call ends, Onset places a second call to the caregiver alone through
  `POST /api/v1/calls` (queued if the line is still busy) and briefs them by voice and on screen.
  Code: [`tools/caregiver_call.py`](../tools/caregiver_call.py), [`onset/brief.py`](../onset/brief.py).

**What we'd ask for.** Either of these would let a product like ours drop the relay:

1. `POST /api/v1/calls/{id}/participants` with `{"destination": "+1..."}`: turn the active call
   into a Group FaceTime, as the FaceTime app's own "Add People" does. The new participant's
   audio and video would arrive in the room as another bridge participant.
2. A per-call flag that lifts the one-audio-publisher rule and has the bridge mix what the room
   publishes, so a customer's room can carry the agent and one more human.

## 2. Joined phones are invisible, and a failed join is silent

**What happened.** The caregiver's text was refused with `recipient_not_verified` every time.
He had texted `join <code>` from the right phone, in a blue bubble, and got no reply. We could
not tell whether his phone had joined.

**What we found** (request ids `da5bfb81-393d-4122-8236-d415a8ac3ae7`,
`72f63fc0-3a55-405b-b6a9-1abfce7306ac`, `5114e1bc-2c67-4f35-b2d0-8728ef07fb9b`):

- There is no endpoint that lists joined phones, for a project key or for the owner's CLI
  sign-in, and the console does not show them. `GET /api/v1/shared-messages` lists only what the
  agent sent, so it reads as "nobody joined" when it cannot show a join at all.
- `GET /api/v1/shared-join-code` answers an owner's sign-in but refuses a project key
  (`join_code_member_required`).
- The refusal's message is generic: "Check the recipient verification, allocation, credit and
  usage limits."
- A join that does not register (an old code, a text from an Apple ID e-mail, the wrong number
  after a trial line was released) gets no reply.

**Our workaround.** [`tools/chert_join.py`](../tools/chert_join.py) reads what the owner can see
(the phrase in force and when it changed, whether joining is on, the number to text, the plan,
whether the trial line was released) and compares it with what the person typed.
[`onset/chert_api.py`](../onset/chert_api.py) `explain()` turns the code into a sentence.

**What we'd ask for.**

1. `GET /api/v1/shared-recipients` for owners: the phones verified for this project, masked.
2. A reply to a join that fails: "That code is not valid", "Text this from your phone number, not
   an e-mail address".
3. A specific message with `recipient_not_verified`: "This phone has not joined project X."

## 3. `video_framing: fill` cropped the captions

**What happened.** On a new line, callers saw our picture zoomed in and cut off at both sides:
`this is On` for "Hello, this is Onset, ...". The line's `video_framing` was `fill`; the line we
had developed on was `fit`. Nothing in `chert dev` or `chert doctor` mentions the setting, and it
cannot be changed from the CLI or the documented API.

**Our workaround.** Onset sends an upright 540x960 picture and keeps every caption inside a
centred 3:4 safe area ([`onset/overlay.py`](../onset/overlay.py), `safe_box`), and our own doctor
fails when a `fill` line would crop a wide picture. Details: [video-framing.md](video-framing.md).

**What we'd ask for.** Print the line's `video_framing` in `chert dev` when the agent's track is
wide and the line fills; let the owner set it per line; document the target picture's shape.

## 4. `chert dev` did not find the agent

**What happened.** `chert dev --json`, run in the agent's folder with `AGENT_NAME` in
`.env.local`, reported `agent_not_warm: "my-agent" is NOT in the waiting room` and declined every
call. The command's help says the default is "AGENT_NAME in .env.local". With `--agent` alone the
name was right but it never went warm, because it also had no LiveKit keys. With `--agent NAME
--env-file .env.local` it was warm in two seconds.

**Our workaround.** [`tools/chert.sh`](../tools/chert.sh) adds both flags for `dev`.

**What we'd ask for.** Read `.env.local` from the working directory, as the help text says, or
say "no agent name and no LiveKit keys found; pass --agent and --env-file".

## 5. A line whose Mac had no audio

**What happened.** On our first line every call connected and was silent in both directions:
the caller's audio reached our agent as digital silence (RMS -120 dBFS) and the agent's voice
never reached the caller. `GET /api/v1/lines` showed `session_health.readiness.audio_ready:
false` on every read; `chert doctor` passed. Details: [chert-audio.md](chert-audio.md).

A second line reports `audio_ready: false` too and its audio works, so the flag is not a
reliable signal either way.

**What we'd ask for.** Have `chert doctor` and `chert dev` report the line's readiness, and make
`audio_ready` mean what it says.

## Questions the documents did not answer

1. Can a free-trial project place outbound calls through `POST /api/v1/calls`, or only Shared?
2. Is the trial's "replies only, within 24 hours" rule reported with its own error code?
3. Can an outbound call be placed while `chert dev` holds the line for inbound calls?
