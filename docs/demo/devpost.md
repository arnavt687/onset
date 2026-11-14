# Onset

**A stroke check-in agent you FaceTime.** One minute: smile, hold your arms up, repeat a
sentence. Onset compares you with your own baseline and, if anything changed or is unclear,
tells you to call 911 now and texts your caregiver.

> Onset is a research prototype, not a medical device. It does not diagnose, and it has not
> been clinically validated.

## Inspiration

After a stroke or a TIA, people go home and the watching falls to them and their family.
According to the CDC, nearly 1 in 4 strokes in the US are in people who have had a previous
stroke ([CDC, Stroke Facts](https://www.cdc.gov/stroke/data-research/facts-stats/index.html)).
The public checklist for spotting a stroke, FAST, is simple: ask the person to smile, raise
both arms and repeat a simple phrase, and call 911 if you see a sign
([CDC, Signs and Symptoms](https://www.cdc.gov/stroke/signs-symptoms/index.html)). But it
depends on someone being there, knowing the person's usual face and voice, and being sure
enough to make the call.

We wanted something a survivor in their first 90 days home could do every day without
installing anything, that knows what *their* usual looks like, and that never talks them out
of calling for help.

## What it does

You FaceTime Onset. It runs a scripted check of about 60 seconds:

1. **Smile.** A relaxed face, then a big smile. Onset measures how far each mouth corner
   lifts and reports an asymmetry index (0 = even, 1 = one side did not move).
2. **Arms up for 10 seconds.** Both arms straight out. Onset tracks each wrist against the
   shoulders and measures how far each one sinks, in shoulder widths. Using the difference
   between the sides cancels the drift both arms share from ordinary tiredness.
3. **Repeat a sentence.** For example "You can't teach an old dog new tricks." Onset
   transcribes it and measures word error rate, speaking rate and the longest pause.

Then it compares each number with the person's own baseline, recorded on well days, using
fixed rules. There are three outcomes:

- **No change detected.** Onset says "No change detected compared with your usual check-in."
  It never says "you're fine".
- **Change**, or **unclear** (bad light, face out of frame, arms not held long enough, no
  baseline). Both get the same answer: "Please call 911 now." The caregiver gets an iMessage
  saying what Onset noticed and that the person was told to call 911. Onset never names a
  condition.

Every instruction and result is a caption on the agent's video, so the person can follow it
with the sound off. A laptop dashboard shows the live measurements next to the baseline, the
decision and the caregiver text.

## How we built it

- **Chert** gives the agent a real Apple FaceTime line. A Mac on Chert's side bridges the
  FaceTime call into a LiveKit room; `chert dev` connects our agent to the line.
- **LiveKit Cloud** carries audio and video between the bridge and the agent.
- **The agent** is Python on `livekit-agents` 1.8. It receives the caller's camera (1280x720,
  about 15 fps), draws captions and landmarks on it, and sends it back as its own video.
- **ElevenLabs** speaks the agent's lines (with Gemini and OpenAI text-to-speech as fallbacks)
  and **ElevenLabs Scribe** (`scribe_v2`) transcribes the sentence, with word timestamps.
- **MediaPipe** on CPU: Face Landmarker for the smile, Pose Landmarker (full model) for the
  arms. On our laptop CPU in WSL, face landmarks take about 8 to 9.5 ms a frame and pose about
  16 ms, well inside the 66 ms between frames at 15 fps.
- **Decision rules** in `onset/decide.py`: plain Python comparisons against thresholds kept in
  one dataclass and documented one by one in `docs/thresholds.md`. No LLM in the decision path.
- **Caregiver text** through Chert's shared-messages API (`onset/chert_api.py`), which never
  raises into the call and never logs the key, the number or the text.

We split the work into pieces with fixed contracts (`onset/types.py`): face, arms, speech,
decision, recorder/replay, Chert API. Each has its own tests on synthetic data: MediaPipe's own
canonical face mesh with the mouth corners moved, a stick figure drawn with OpenCV whose arm
sinks, and text-to-speech clips including slurred and garbled ones. No photos or recordings of
real people are in the repo. 278 tests pass on `main`, and CI runs them with no API keys.

## Challenges we ran into

**The caller can't hear the agent.** On every inbound FaceTime video call the caller sees the
agent's video but hears no agent audio. The bridge subscribes to the agent's audio track, the
same audio is clearly audible to an ordinary LiveKit client, and Chert's own hosted onboarding
demo agent was silent too, so the problem looks like it is on Chert's side. We sent Chert a
report with call IDs and timings, and they are investigating. The demo routes around it:
Onset is caption-first (every instruction, countdown and result is written on its video, which
the caller does see), and the laptop dashboard plays the agent's voice for the room.

**Speech recognition "corrects" mild slurring.** We generated mildly slurred clips ("can't
teash an old dog new twicks") and sent them to Scribe. It returned the correct sentence, with a
word error rate of 0. Both `scribe_v1` and `scribe_v2` did this. Scribe kept a real word swap
("old frog"), so it isn't snapping to the idiom; it pulls sounds toward the nearest plausible
word. No API option turns this off. So word error rate catches wrong, missing or extra words
but not mild slurring, and Onset also checks speaking rate and the longest pause against the
baseline. Heavy garbling did show up in the transcript.

**MediaPipe evens out a one-sided smile.** When only one mouth corner moves, the face mesh
pulls the other corner along, so a completely one-sided smile reads lower than its true size.
That is one more reason to compare with the person's own baseline rather than use a fixed
cut-off.

**Windows and WSL.** The Chert CLI fails on Windows 11 with `windows_private_storage_required`
(its folder-permission script fails the second time it runs), so everything runs in WSL2
Ubuntu. Quoting through PowerShell and Git Bash broke `$` and Linux paths, so every command
runs from a script file via `wsl -d Ubuntu -- bash ...`. And `chert dev` loses its relay
connection now and then, so the demo has a redial-once rule and a replay mode.

## Accomplishments that we're proud of

- A real FaceTime call into a custom agent: answered from a warm room in under a tenth of a
  second, with the caller's camera arriving at the agent and the agent's video on the phone.
- All three measurements run in real time on a laptop CPU, with quality gates (no face, too
  dark, head turned, too much motion, no response) that turn a bad measurement into "unclear"
  instead of a guess.
- Safety rules enforced by tests: unclear or no baseline can never give "no change detected",
  a value exactly at its threshold counts as a change, and the decision module's outputs are
  checked for words like "fine", "normal" and "stroke".
- Baselines and recordings are local only: gitignored, written atomically, owner-only file
  permissions. Recording is off unless explicitly turned on.
- Speech checks are fast and cheap: Scribe answered in 0.28 to 0.58 s, and a 5-second clip
  costs about $0.0003.

## What we learned

- For a safety tool, "unclear" has to be a first-class answer, and it has to lead to the same
  action as "changed".
- Comparing a person with themselves matters more than any absolute number: faces, arms and
  voices differ a lot between people.
- A transcript can look perfect while the speech was not. Measure timing as well as words.
- Caption-first is good design even without the audio bug: people with hearing loss, a quiet
  phone or a noisy room can still follow every step.
- Chert can text only phones that have joined the line (on the free trial, only as a reply
  within 24 hours of that phone's last text). The text has to be an extra, never the safety
  action itself.

## What's next for Onset

- **Clinical validation.** A clinician-led study with consented stroke and TIA survivors:
  well-day baselines, clinically labelled check-ins, and thresholds set from that data instead
  of synthetic data and one person.
- **Scheduled outbound check-ins.** Onset calls the person at a time they chose, through
  Chert's outbound calls API (documented for the Shared plan; not yet tested).
- **Caregiver messaging on Chert's Shared plan**, so a caregiver who joined once at enrolment
  can be texted any time, not only within 24 hours of their last text.
- **An optional LLM only for off-script replies** ("sorry, can you repeat that?"), kept out of
  the measurements and the decision.
- Agent audio over FaceTime once Chert fixes it.

## Built with

Python · livekit-agents · LiveKit Cloud · Chert (FaceTime line, iMessage API) · ElevenLabs
(text-to-speech, Scribe speech-to-text) · MediaPipe (Face Landmarker, Pose Landmarker) ·
OpenCV · NumPy · aiohttp · pytest · GitHub Actions · WSL2 Ubuntu

Thanks to our sponsors **ElevenLabs**, **LiveKit** and **Chert**.
