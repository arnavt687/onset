# Onset: 3-minute live demo, run of show

Two roles. The **Presenter** talks and drives the laptop. The **Caller** holds the check-in
on the iPhone. If one person does both, skip the presenter lines marked *(over the call)*
and say only the lines between calls.

Total: 2:50 scripted, 10 s spare.

| Time | Segment | Length |
|---|---|---|
| 0:00 | Hook | 20 s |
| 0:20 | Call 1: a usual check-in (matches baseline) | 60 s |
| 1:20 | Call 2: an acted change | 60 s |
| 2:20 | Safety design and what's next | 30 s |

## What this script assumes is ready

Some of this is still on the orchestrator's list (`ORCHESTRATOR.md`, items 4 to 9). Check
each one at the rehearsal, not on the day.

- The scripted call flow with captions on the agent's video (piece 5). The agent lines
  below are the planned wording; match them to `onset/script.py` once it lands.
- The laptop dashboard: caller video with landmarks, live numbers next to the baseline, the
  decision, the caregiver text, and the agent's voice played through the laptop.
- Replay mode: the same pipeline run from a recorded call.
- Two recorded calls by the Caller: one usual, one acted change (for replay).
- A saved baseline for the Caller in `baselines/`, made from earlier well-day check-ins with
  the same sentence.
- For a real caregiver text: a Chert project API key in `.env.local`, and the caregiver's
  phone has texted the line in the last 24 hours (free-trial rule, see
  `docs/chert-messaging.md`). Without these the dashboard shows the text as a dry run.

## Stage setup

- **Phone (Caller):** iPhone on a stand at chest height, propped so it sees the Caller down
  to the waist with room above the hands. Light from the front, no window behind. Onset's
  line ready in FaceTime Recents. Do Not Disturb on, so no notifications cover the call.
- **Laptop (Presenter):** runs the agent and the dashboard, mirrored to the projector. Laptop
  audio to the room speakers: this is where the agent's voice comes from (agent audio over
  FaceTime does not reach the caller yet; Chert is investigating).
- **Caregiver phone:** a teammate holds a second iPhone, or the dashboard's caregiver panel
  shows the text.
- **Never on the projector:** any phone number (the line's, the caregiver's), API keys,
  terminal windows with `.env.local`.

## Pre-show checklist (30 minutes before)

1. In WSL: `agent.py start` and `tools/chert.sh dev` running; `chert dev` shows the line
   connected.
2. One test call end to end. Caller video appears on the dashboard; captions readable from
   the back of the room.
3. Baseline file present for the Caller; the sentence in it matches the flow's sentence.
4. Caregiver phone texts the line (any text) about 10 minutes before going on.
5. Replay mode opened once with each recording, then left on the "usual" recording, ready.
6. Phone charged, volume up, FaceTime open. Laptop on mains power.

---

## 0:00 to 0:20 — Hook (20 s)

**Laptop:** deck slide 1 (Problem). **Phone:** in the stand, FaceTime open.

**Presenter:**

> "After a stroke or a mini-stroke, people go home, and their family becomes the early
> warning system. Nearly one in four strokes in the US happen to someone who has already had
> one. Onset is a one-minute check-in you FaceTime. It runs the checks of FAST, the public
> stroke-recognition checklist, and compares you with yourself."

(Source for the statistic: CDC, *Stroke Facts*, see `deck.md`.)

**Fallback:** slides or projector fail → say the hook anyway, then go straight to the phone.

---

## 0:20 to 1:20 — Call 1: a usual check-in (60 s)

**Laptop:** switch to the dashboard (idle, waiting for a call). **Phone:** Caller in frame.

**0:20 Presenter:**

> "Picture someone a few weeks home. A usual morning. They call Onset."

**0:24** Caller taps Onset's line in FaceTime. Chert answers (from a warm room it takes under
a tenth of a second). Dashboard: caller video appears with face landmarks.

**Agent** (caption on the agent's video, voice from the laptop):

| ~Time | Agent line (planned) | Caller does | Dashboard shows |
|---|---|---|---|
| 0:25 | "Hi, this is Onset, your check-in. It takes about a minute." | Looks at the phone | Step: greeting |
| 0:29 | "Look at the camera with a relaxed face." then "Now give me a big smile." | Neutral face 2 s, then a full, even smile | Mouth-corner lift left / right, asymmetry next to baseline |
| 0:38 | "Make sure I can see you down to your waist. Hold both arms straight out in front of you." | Both arms out, steady | Countdown 10 to 1 on the video; left / right wrist drift next to baseline |
| 0:52 | "You can lower your arms." | Lowers arms | Arm result |
| 0:54 | "Please repeat after me: You can't teach an old dog new tricks." | Repeats it at a normal pace | Transcript, word error rate, words per minute, longest pause |
| 1:04 | **"No change detected compared with your usual check-in."** Smaller caption: "If you feel unwell or worried, call 911." | Listens | Decision: no change detected |

**Presenter** *(over the call, short, between agent lines)*:

- During the smile: "Face landmarks, on the laptop's CPU. It measures how far each corner of
  the mouth lifts."
- During the arm hold: "Ten seconds. It tracks the wrists against the shoulders, so a wobbly
  phone doesn't count as drift."
- After the result: "Listen to what it says. Not 'you're fine'. 'No change detected compared
  with your usual check-in.'"

**Fallbacks:**

- **Call doesn't connect, says "call unavailable", or drops:**
  "Live FaceTime on venue Wi-Fi. Redialing once." Redial. If the second try fails:
  "Same pipeline, from a call we recorded this morning." Switch the dashboard to replay mode
  with the usual recording. (Chert's relay connection drops occasionally; this is the known
  cause.)
- **No agent voice from the laptop:** "You can see every instruction on screen. Onset is
  caption-first: everything it says is written on its video." Carry on.
- **The result is a change or unclear** (bad light, a half smile, arms out of frame):
  "It flagged this one. That's the design: when it isn't sure, it says call 911. A false
  alarm costs a phone call." Point at the reason on the dashboard. Do not redo the call;
  move to call 2.
- **Caller video freezes but the call stays up:** let the flow run. If a step comes back
  unclear, use the line above.

---

## 1:20 to 2:20 — Call 2: an acted change (60 s)

**Laptop:** dashboard, reset for a new call. **Phone:** Caller back in frame.

**1:20 Presenter:**

> "Same person, a different morning. We'll act out three changes: a one-sided smile, one arm
> sinking, and a pause in the middle of the sentence."

**1:26** Caller redials Onset.

**Acting notes for the Caller** (rehearse these; pick one side and keep it for both):

- **Smile:** smile with one side of the mouth only. Keep the other corner still.
- **Arms:** both arms out. After about 3 seconds let your left arm sink slowly, about a hand's
  width. Keep the right arm steady. Do not drop both arms, and stay in frame.
- **Sentence:** "You can't teach an old... *(count three seconds in your head)* ...dog new
  tricks." A little slower than usual. The pause rule fires at 1.5 s, so three seconds is a
  clear margin.

| ~Time | Agent line (planned) | Dashboard shows |
|---|---|---|
| 1:27 | Greeting, smile, arms, sentence, as in call 1 | Asymmetry above baseline; left drift larger than right; longest pause above 1.5 s, highlighted |
| 2:05 | **"I noticed a change. Please call 911 now."** (caption, large, stays on screen) | Decision: change. Reasons in plain words |
| 2:08 | If the text was accepted: "I've sent a message to your caregiver." If not: "I could not text your caregiver. Please call them too." | Caregiver text and its send status |

**Caregiver text** (format from `caregiver_summary()` in `onset/decide.py`; the lines under
"What Onset noticed" depend on which rules fired):

```
Onset check-in for <name> on <day date> at <time> <zone>. Checked: smile, arm hold, repeating a sentence.
What Onset noticed:
- The left side of the smile lifted less than usual.
- The left arm drifted down more than usual.
- There was a long pause while saying the sentence.
<name> was told to call 911 now. Please check on them right away. Onset is a research prototype and cannot diagnose.
```

**Presenter** *(as the 911 caption appears)*:

> "Any one of those would have been enough. A change, or anything it couldn't measure
> clearly, gets the same answer: call 911 now. And the caregiver gets this text. It doesn't
> name a condition. It says what it noticed and what it told them to do."

**Fallbacks:**

- **Call fails:** redial once. If it fails again: "Here's the change call we recorded
  earlier." Replay mode with the change recording.
- **The result is "no change detected"** (acting too subtle): "It didn't catch that one.
  The acting was too small for these thresholds, which are still provisional. Here's the
  recorded change call." Replay mode. Do not argue with the result on stage.
- **The text doesn't arrive on the caregiver phone:** point at the dashboard. "The text is
  here. Chert's trial plan can only text a phone that has texted the line in the last day,
  and delivery isn't instant." If the dashboard says dry run, say so: "In this demo it's a
  dry run; this is exactly the text it sends."
- **No voice:** "You can see every instruction on screen."

---

## 2:20 to 2:50 — Safety design and what's next (30 s)

**Laptop:** deck slide 5 (Safety by design), then slide 6 (Limits and roadmap).
**Phone:** call ended, set down.

**Presenter:**

> "Three choices. The decision is fixed rules you can read, with no LLM in the decision
> path. Anything unclear counts as a change, so it leans toward false alarms. And it never
> tells anyone they're fine, and never diagnoses. The thresholds are provisional, tuned on
> synthetic data and one person. Our next step is a clinician-led validation study. Built
> with ElevenLabs, LiveKit and Chert. Thank you."

**Fallback (running long):** cut to: "Fixed rules, no LLM in the decision path, and anything
unclear means call 911. Next is a clinician-led validation study. Thank you."

---

## If asked (short answers)

- **"Does it detect strokes?"** "No. It notices change from your own baseline and
  escalates. Deciding what it is stays with the 911 call and clinicians."
- **"How accurate is it?"** "We don't have accuracy numbers, and we won't quote any until a
  clinical study measures them."
- **"Why no LLM?"** "Every decision has to be explainable and repeatable. Fixed rules give
  the same answer for the same numbers, and you can read why."
- **"Why FaceTime?"** "Many older people already use it. There's no app to install, and the
  agent has a real phone line."
- **"Where does the data go?"** "Recordings and baselines stay on our machine, readable only
  by its owner. The call passes through Chert and LiveKit, and the sentence audio goes to
  ElevenLabs for transcription."
- **"Why couldn't the caller hear it?"** "Agent audio over FaceTime is blocked on Chert's
  side right now; they're looking into it. That's why everything is on screen."
