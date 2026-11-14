# Escalation: the caregiver joins the call

On **ESCALATE** or **UNCLEAR**, Onset says and shows:

> I am adding your caregiver to this call now. Please call 911.

Then it brings the caregiver into the live call and texts them a summary of what it noticed.
Calling 911 stays the instruction on every screen and in every line. The caregiver joining is
an extra help, never a replacement.

## What Chert allows, and why it works this way

Read on 14 Nov 2026 from https://app.trychert.com/skills.md, docs.trychert.com (Group calls,
Limits, Capabilities, Bring Your Own Room) and the CLI `@trychert/facetime@0.1.0-beta.20/.21`:

| Idea | Possible? |
|---|---|
| Add a person to the FaceTime call in progress (add, merge, transfer) | **No.** Chert's API "exposes no add, remove, mute, or moderation controls"; a second incoming call is not transferred into the active one. |
| FaceTime the caregiver from the same line during the call | **No.** A line carries one active call at a time. With a second line, that call would carry Onset's picture and voice, not the caller's. |
| Group FaceTime (`recipients: [...]` on `POST /api/v1/calls`) | Only as a **new** outbound call that Chert enables per line. It is no use mid-emergency, because the caller would have to answer again. |
| LiveKit SIP dial-out to the caregiver's phone | Needs a SIP trunk, audio only, and a second audio publisher ends the call. |
| **Caregiver joins our LiveKit room** | **Yes, subscribe-only.** "Silent auxiliary participants may be present". But "two participants publishing audio ends the call" (`several_audio`). `chert dev` rooms have `maxParticipants: 2`, which counts the bridge and one caregiver; the agent does not count. |

So the caregiver **watches** the call's room and **talks** through a side room. Onset relays
the side room into its own single audio track, and draws their camera picture-in-picture.

```
 caller's iPhone ── FaceTime ── Chert Mac ── chert-bridge-<call>  ┐
                                                                   │  room <call room>
 Onset agent: ONE audio track (its voice, then the caregiver's) ──┤  (caregiver: subscribe only)
              ONE camera track (caller + captions + caregiver PiP)│
 caregiver's browser ── watch token ───────────────────────────────┘
 caregiver's browser ── talk token ── room <call room>-cg ── Onset listens (hidden) and relays
```

## The flow

1. `onset/flow.py` decides (rules only), says the closing line, and calls the agent's escalation
   hook in the background.
2. `agent.py` `Escalation.run`:
   - sets its participant attributes `onset.outcome`, `onset.summary`, `onset.relay`, `onset.patient`;
   - mints the caregiver's tokens (`onset/caregiver.py`): the **watch** token is the call's room,
     subscribe only, about 15 minutes; the **talk** token is the side room, publish microphone and
     camera only;
   - joins the side room hidden (`onset/relay.py`) **before** the link exists;
   - texts the caregiver the summary plus the link, through the safety gate (below);
   - writes the link to `runs/live/caregiver_link.txt` (0600; it is never put in an event or an
     archive), so the local dashboard can open the caregiver view in a demo.
3. The caregiver opens the link: `ONSET_CAREGIVER_PAGE#c/<LiveKit host>/<key length>/<watch key>`.
   The key is in the `#fragment`, so the static host never sees it. The link is about 500
   characters and has no `&`, `=` or `%` (issue #30: the earlier link was about 1,100 characters
   with the talk key last, it arrived cut short, and the caregiver could watch but not talk).
   The key's length comes first, so a cut link is refused with "this link was cut short" instead
   of half-working. **The talk key is not in the link**: Onset hands it to the caregiver inside
   the call, on data topic `onset`, as soon as they join.
   - **Joined**: Onset says "Your caregiver has joined the call. Please still call 911", and sends
     the summary on data topic `onset`.
   - **One tap.** "Tap to join the call" asks for the microphone and camera inside that tap and
     starts talking as soon as the talk key arrives; there is no second button to find. Leave
     goes back to watching only. If the microphone is blocked or missing the page says so, and
     tells Onset (topic `onset-cg` in the side room), so the dashboard and the log show why
     talking has not started ("the caregiver's microphone is blocked in their browser").
   - **Talking**: their microphone reaches Onset in the side room.
     Onset says "You can talk to your caregiver now. Please still call 911", then plays their
     voice through its own audio track, and the caller sees them picture-in-picture.
     `onset.relay` turns `on`, so the page stops playing Onset's audio, which would otherwise
     echo the caregiver's own voice back to them. The caregiver hears the caller directly from
     the bridge's audio.
4. Each step is an event: `caregiver_join` (`invited` → `joined` → `talking` → `left`),
   `action` (the text, dry or live), and `caregiver` (the summary and its status). They appear
   on the dashboard and can arrive after `run_ended`.

Without `ONSET_CAREGIVER_PAGE` the link is LiveKit Meet (`meet.livekit.io/custom`), which
gives the caregiver a watch-only view; the caller cannot hear them. `ONSET_CAREGIVER_JOIN=off`
sends the summary text only.

If the caller hangs up after the decision, the text still goes out. The agent waits up to 45 s
for it before the job ends. If the caller hangs up before the decision, nothing is sent:
there is no decision to report.

## Safety gate

Every outbound action goes through `onset/actions.py`. It is a **dry run** unless
`ONSET_LIVE_ACTIONS=1` **and** the patient's profile has a caregiver phone, and it stays a dry
run when the kind is listed in `ONSET_LIVE_ACTIONS_DENY`. Dry runs log exactly what would have
happened as an `action` event, with the phone masked. Replays (`tools/replay_flow.py`) and tests
are always dry. For a real text, Chert also requires that the caregiver's phone has **joined the
line** (texted `join <code>` once). On the free trial Chert only lets Onset reply within 24
hours of the caregiver's last text; see [chert-messaging.md](chert-messaging.md).

## Settings

| Name | Default | |
|---|---|---|
| `ONSET_CAREGIVER_JOIN` | `link` | `link` or `off` |
| `ONSET_CAREGIVER_TALK` | `1` | `0`: watch-only, no side room |
| `ONSET_CAREGIVER_PAGE` | unset | Where `web/caregiver/` is hosted. Unset: LiveKit Meet, watch-only |
| `ONSET_CAREGIVER_LINK_TTL_S` | `900` | How long the link works |
| `ONSET_LIVE_ACTIONS` | `0` | `1` sends for real |

## Verified, and what only a real line can show

- `tools/fake_escalation_private.sh` runs the agent under a private name in a private LiveKit
  room. It checks the escalation, that the watch token **cannot** publish, that the caregiver
  receives the summary, the `invited → joined → talking` events, and that a 440 Hz tone published
  by the "caregiver" in the side room reaches the "caller" inside Onset's audio track
  (tone share 0.90 on 14 Nov 2026).
- Not yet shown on a real Chert line:
  - whether the Mac tolerates a subscribe-only caregiver in the room (documented as allowed);
  - whether caller capture (`facetime-remote-video`) is visible to a non-agent participant;
  - that the relayed voice sounds right on the iPhone.

## Testing the page itself

`tools/fake_escalation_private.sh` plays the caller and the caregiver and checks that the caller
hears the caregiver through Onset. It publishes into the side room directly, so it does not
exercise the page. To drive the real page:

```bash
tools/fake_escalation_private.sh --page 200      # plays only the caller, then waits for a real page
```

When it prints "waiting ... for a real caregiver page", open the link from
`runs/live/caregiver_link.txt` in a browser (on this computer the dashboard serves the page at
`http://localhost:8765/web/caregiver/index.html#...`), tap once, and talk. It reports whether the
status reached `talking`, how much of the agent's audio was the caregiver, and anything the page
reported about itself. Do this on a real iPhone in Safari before a demo.
