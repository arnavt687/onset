# Onset: safety framing

**Onset is a research prototype. It is not a diagnostic tool and not a medical device.** It
has not been clinically validated, and no patient should rely on it. It runs the checks of
FAST, the public stroke-recognition checklist, compares the person with their own baseline,
notices change and escalates. It does not decide what is happening to anyone.

## How it is built to fail safe

- **Deterministic, auditable rules.** The decision is plain code in `onset/decide.py`, with
  every threshold in one dataclass and explained in `docs/thresholds.md`. No LLM is in the
  decision path. The same numbers always give the same outcome, and every outcome lists its
  reasons in plain words, each traceable to a named threshold.
- **Every unclear result escalates.** There are three outcomes: no change detected, change,
  unclear. Unclear (no face found, too dark, head turned, too much motion, arms not held long
  enough, sentence not heard, a missing value, no valid baseline) is treated exactly like a
  change: "Please call 911 now", and the caregiver is texted. "No change detected" is only
  possible when all three checks were measured well and every value is within its threshold.
  A value exactly at its threshold counts as a change.
- **Biased toward false alarms.** Each threshold was set to err toward escalating. A false
  alarm costs a phone call; a miss can cost far more.
- **Never "you're fine".** The best result is "No change detected compared with your usual
  check-in." The decision module's tests fail if its outputs or string literals contain words
  such as "fine", "normal", "healthy" or "stroke".
- **Never a diagnosis.** Messages say what was noticed ("the left arm drifted down more than
  usual") and what the person was told to do. The caregiver text ends: "Onset is a research
  prototype and cannot diagnose."
- **The text is never the safety action.** The spoken and captioned "call 911 now" is. Chert
  can only text a caregiver whose phone has joined the line, so a failed or impossible text is
  told to the person: call your caregiver too.
- **Caption-first.** Every instruction and result is written on the agent's video, so the
  check works with the sound off or for someone hard of hearing.

## Data

- **Local only:** baselines (`baselines/`) and call recordings (`recordings/`) are
  gitignored, kept on the machine running Onset, in owner-only directories (0700) and files
  (0600). Baselines are written atomically and checked on load; a corrupt or tampered file is
  ignored and the check-in becomes unclear. Recording is off unless `ONSET_RECORD=1` is set.
- **What leaves the machine:** the call's audio and video pass through Chert and LiveKit
  Cloud; the sentence audio goes to ElevenLabs for transcription; the agent's lines go to
  ElevenLabs for speech; the caregiver text goes through Chert. API keys, phone numbers and
  message text are not logged.

## Known limitations

- **Provisional thresholds.** Set from synthetic data (a rendered face mesh, a drawn figure,
  generated speech) and one person. None is clinically validated.
- **Small test set.** No recordings of real patients. Real faces, arms and voices will be
  noisier than the synthetic tests.
- **Lighting and framing.** Dim light, backlight, a face turned more than about 20 degrees, or
  arms leaving the frame make a step unclear. That is safe (it escalates) but will cause false
  alarms at home.
- **The face model evens out one-sided smiles.** MediaPipe moves the still mouth corner along
  with the moving one, so real asymmetry reads smaller than it is.
- **Speech recognition auto-correction.** ElevenLabs Scribe turned mildly slurred words back
  into the correct sentence (word error rate 0) in our tests. Word error rate alone misses mild
  slurring; Onset also checks speaking rate and the longest pause, but mild slurring at a
  normal pace may still pass.
- **Not measured:** the palm turning over during the arm hold, vision, balance, and any FAST
  sign the camera cannot see.
- **People whose baseline already includes weakness, aphasia or slurred speech** have not been
  tested; they may get repeated unclear results.
- **Agent audio over FaceTime** does not reach the caller yet (a Chert-side issue under
  investigation); the check relies on captions until it is fixed.

## What clinical validation would need

1. A clinician-led study with ethics approval and informed consent, with stroke and TIA
   survivors in their first 90 days home, run in parallel with usual care (Onset never
   replacing it).
2. Several well-day check-ins per person to measure day-to-day spread, then thresholds set
   from that spread (the method is in `docs/thresholds.md`), never below each meter's noise
   floor.
3. Check-ins labelled against a clinician's assessment, including real changes, to measure
   how often Onset misses a change and how often it raises a false alarm.
4. Testing across lighting, phones, camera positions, skin tones, ages, accents and
   pre-existing speech or movement difficulties.
5. A review of the 911 and caregiver flow with clinicians and emergency services, including
   what happens when the person cannot act on the instruction.
6. A regulatory assessment of whether, and how, Onset would be regulated as software for a
   medical purpose before any use outside research.
