# Onset: pitch deck (7 slides)

On-slide text is 25 words or fewer per slide (source lines included). Speaker notes are for
the presenter only. Slide 3 is where the live demo runs (see `run-of-show.md`).

---

## Slide 1 — Problem

**Title:** The first 90 days home

**On slide:**

> First 90 days home: the family does the watching.
> Nearly 1 in 4 US strokes are in people who had one before.
>
> *Source: CDC*

**Speaker notes:**
After a stroke or a TIA, people go home and the checking falls to them and their family.
The CDC's stroke facts page says about 185,000 US strokes a year, nearly one in four, are in
people who have had a previous stroke. Our user is the survivor in those first 90 days home.
Our buyer is the caregiver first, and later hospital discharge programmes.

Source: CDC, *Stroke Facts*, <https://www.cdc.gov/stroke/data-research/facts-stats/index.html>
(read 13 Nov 2026: "About 185,000 strokes—nearly 1 in 4—are in people who have had a
previous stroke.")

---

## Slide 2 — Insight

**Title:** Compare you with yourself

**On slide:**

> FAST checks face, arms and speech. A video call can measure all three. Compare each person
> with their own baseline, over FaceTime.

**Speaker notes:**
FAST is the public stroke-recognition checklist: ask the person to smile, raise both arms,
repeat a simple phrase, and call 911 if any sign shows (CDC, *Signs and Symptoms of Stroke*,
<https://www.cdc.gov/stroke/signs-symptoms/index.html>). Onset runs those checks and compares
the numbers with the same person on well days, because people differ: some smiles are
naturally uneven, some people speak slowly. Why now: landmark models run in real time on a
laptop CPU, an agent can hold a real FaceTime line, and many older patients already know
FaceTime, so there is no app to install.

---

## Slide 3 — Demo

**Title:** Two check-ins, live

**On slide:**

> Call 1: a usual morning.
> Call 2: an acted change.
> Watch the captions, the numbers and the caregiver's text.

**Speaker notes:**
Run `run-of-show.md` from 0:20. The phone is the caller's view; the laptop dashboard shows
what Onset measures, the decision and the caregiver text. Agent audio over FaceTime is blocked
on Chert's side right now, so the agent's voice plays from the laptop and every line is a
caption on the agent's video. If a call fails twice, switch to replay mode.

---

## Slide 4 — How it works

**Title:** How it works

**Visual:** `architecture.svg`

**On slide:**

> FaceTime → Chert → LiveKit → agent. MediaPipe and Scribe measure. Fixed rules decide. No LLM
> in the decision path.

**Speaker notes:**
Chert gives the agent a real Apple FaceTime line; a Mac on Chert's side bridges FaceTime into
a LiveKit Cloud room. The agent is Python on livekit-agents. Smile: MediaPipe Face Landmarker,
how far each mouth corner lifts, as an asymmetry index. Arms: MediaPipe Pose, wrist drift over
10 seconds measured against the shoulders, so phone wobble cancels. Speech: ElevenLabs Scribe
transcribes the repeated sentence; we compute word error rate, speaking rate and the longest
pause. All of this runs on CPU; face landmarks take about 9 ms a frame and pose about 16 ms on
our laptop. The rules in `onset/decide.py` compare today with the person's baseline. The
voice is ElevenLabs.

---

## Slide 5 — Safety by design

**Title:** Safety by design

**On slide:**

> Any change or anything unclear: call 911 now. Never says "you're fine." Never diagnoses.
> Rules you can read. Leans toward false alarms.

**Speaker notes:**
Three outcomes: no change detected, change, unclear. Unclear is treated exactly like a
change. Bad light, a face out of frame, arms held too briefly, no baseline: all of these mean
"call 911 now". A false alarm costs a phone call; a miss can cost far more. The caregiver
text is extra and never the only safety action. Nothing the agent says names a condition or
tells the person they are well; the decision module's tests fail if any of its output or
string literals contains words like "fine", "normal" or "stroke". Recordings
and baselines stay on the machine, owner-only.

---

## Slide 6 — Limits and roadmap

**Title:** What it can't do yet

**On slide:**

> Thresholds are provisional: synthetic data, one person. Next: clinician-led validation,
> scheduled check-in calls, caregiver texts on Chert's Shared plan.

**Speaker notes:**
Be direct. The thresholds were set from synthetic faces, drawn figures, generated speech and
one person. We quote no accuracy numbers; a clinician-led validation study is the next step.
Known limits: lighting and framing affect the measurements; speech recognition quietly
"corrects" mildly slurred words, so word error rate alone misses mild slurring, which is why
rate and pauses are also checked. Roadmap: Onset calls the person at a set time (Chert's
outbound calls API), caregiver texts at any time on Chert's Shared plan, and possibly an LLM
only for off-script replies, never for the decision.

---

## Slide 7 — Team and ask

**Title:** Team and ask

**On slide:**

> [Team names]
> Built with ElevenLabs, LiveKit, Chert.
> Looking for: a stroke clinician to lead validation, and discharge programmes to pilot with.

**Speaker notes:**
Fill in team names and roles. Thank the sponsors by name: ElevenLabs for the voice and Scribe
transcription, LiveKit for real-time media and the agent framework, Chert for the FaceTime
line. The ask: a stroke clinician or research group to design the validation study, and a
hospital discharge programme interested in a supervised pilot after that.
