---
title: I built a stroke check-in you FaceTime, and the hardest bug wasn't mine
published: true
tags: hackathon, healthcare, python, ai
cover_image: https://raw.githubusercontent.com/arnavt687/onset/main/docs/demo/screenshots/polish/thumbnail-3x2-v2.jpg
---

The weeks after someone leaves the hospital after a stroke are some of the riskiest of their life. The CDC reports that nearly one in four strokes in the US happen to someone who has already had one. After a TIA (a "mini-stroke"), 10 to 15 percent of people have a major stroke within three months, and a lot of that risk lands in the first few weeks.

In practice, the early-warning system during those weeks is the family. Somebody's daughter notices that Dad's smile looks a little off at breakfast. Or nobody notices.

For Emory Hacks 2026 we built **Onset**: a one-minute check-in that the patient FaceTimes. No app, no login. Their care team prescribes it for the 90 days after discharge, and each morning the patient calls a phone number and does three things on camera.

{% embed https://youtu.be/L58lr_4DPik %}

## What the call actually does

Onset runs the checks from FAST, the public stroke-recognition checklist:

1. **Smile.** MediaPipe Face Landmarker tracks the mouth corners. We measure how far each corner lifts from a neutral face and turn that into an asymmetry index.
2. **Both arms up for ten seconds.** MediaPipe Pose tracks the wrists against the shoulder line, normalized by shoulder width. That normalization matters more than it sounds: a phone held in a shaky hand moves the whole body in the frame, but it doesn't move a wrist *relative to the shoulders*, so camera shake doesn't register as drift.
3. **Repeat a sentence.** ElevenLabs Scribe transcribes it with word timestamps. We score word error rate, words per minute and the longest pause.

The important design choice is that **Onset compares you with yourself**. The first call records your baseline. Every later call is judged against it, not against some population average. Someone whose smile was already asymmetric before the stroke shouldn't trigger an alarm every morning.

The decision is a small set of deterministic rules in one file. Every threshold lives in a single dataclass, for example:

```python
face_asymmetry_increase: float = 0.10      # vs your baseline
arm_drift_difference_absolute: float = 0.20  # shoulder widths
arm_single_drift_absolute: float = 0.50
speech_wer_increase: float = 0.10
speech_rate_drop_fraction: float = 0.20
speech_pause_floor_s: float = 1.0
```

We tuned it toward sensitivity on purpose. A false positive costs a phone call. A false negative costs a lot more.

## What happens when something changes

This is the part we rewrote the most. Our first version simply told the person to call 911 and texted their caregiver. That's the "correct" answer, but it's not what a scared 70-year-old on their own actually does.

So now, on a change, Onset **adds the caregiver to the live call** and strongly recommends calling 911 together. The caregiver gets an iMessage with a summary of what changed against the baseline and a private link. The link drops them into the same video room as a viewer, so they see the patient and Onset's on-screen summary. When they talk, their mic goes to a side room and Onset relays their voice and face into the call, so the patient hears and sees a familiar person.

Anything Onset *can't* measure clearly is treated like a change. But we learned quickly that "your arm left the frame" shouldn't mean "call 911". Now, if a hand or the face is out of view, Onset says exactly what to fix ("I can't see your left hand. Move back so both hands show.") and re-runs that step, up to twice. Only a persistent failure escalates. An arm that is actually *dropping* is never retried, because that's the finding.

Every check-in is also stored and analyzed across sessions. A care-team report and a dashboard trend panel fit a line through each metric over the 90 days and flag a gradual decline even when no single day crossed a threshold.

## The architecture

FaceTime reaches the agent through **Chert**, which gives an agent a real Apple phone line: a Mac on Chert's side bridges the FaceTime call into a **LiveKit** room. Our agent is a Python LiveKit agent (`livekit-agents`). It subscribes to the caller's camera, runs MediaPipe on the CPU, and sends the camera *back* with a caption overlay drawn on top. So the patient sees themselves with instructions like "Big smile! Hold it." and a step counter.

Voice is ElevenLabs, with Gemini and OpenAI TTS as fallbacks, then a chime if all three fail. Chert ends the call if the agent doesn't speak within 10 seconds of the bridge being ready, so the greeting gets its own time budget.

## The bug that wasn't mine

Here's the part I want other hackers to read, because it cost us most of a night.

The first real call: video worked perfectly. My face came back with the overlay. But I heard nothing. No greeting.

I assumed it was my bug, because it usually is. We checked the TTS: the ElevenLabs request succeeded and the audio frames were being published. We swapped to Gemini TTS. Silent. We swapped to a plain 440 Hz test tone with no TTS involved at all. Silent. Then we ran **Chert's own starter agent** and their **hosted demo agent**. Both silent too.

Then we measured the other direction. The caller's audio arriving at our agent was a flat −120 dBFS: digital silence, not a quiet mic. And the room metadata from the bridge reported `audio_ready: false`.

At that point the evidence was conclusive: the Mac on the shared trial line wasn't passing audio either way. We wrote it up with logs and reproduction steps for Chert, and redesigned around it instead of waiting:

- **Caption-first.** Every instruction is drawn on the agent's video, so the check works with the sound off. That turned out to be a better product anyway: many stroke survivors have some hearing loss, and captions don't.
- **A laptop dashboard** that plays the agent's voice in the room and shows every metric live next to the baseline.
- **Code written as if audio works.** We built and tested the full audio path (voice-activity detection to end the sentence recording, the caregiver relay, voice fallbacks) in private LiveKit rooms with a fake caller that publishes real speech. When a line with working audio is connected, it should just work.

Lesson: when the bug survives swapping out every one of your own components, test the platform's reference implementation before you rewrite yours a fourth time.

## Building it with a team of agents

We built Onset with Claude Code acting as an orchestrator. The project was split into pieces (face, arms, speech, decision rules, call flow, caregiver messaging, test harness), each built in parallel by its own agent session in its own git worktree, each landing as a PR with CI. The orchestrator reviewed, resolved merge conflicts and merged.

The main win wasn't speed, though it was fast. It was that each piece had a written contract (shared types, an event stream format) before anyone wrote code, so the pieces actually fit together. By the end, `main` had 640 passing tests.

## What it can't do yet

The thresholds are provisional. They were tuned on synthetic data and one person, so we have no accuracy numbers until a clinician-led study measures them. The mildest speech slurs we simulated slip under our current Scribe-confidence cut-off. And we have yet to run a call on a Chert line where audio works end to end.

## Try it

The code is open: **[github.com/arnavt687/onset](https://github.com/arnavt687/onset)**. You can run the whole check without FaceTime:

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/python tools/demo_events.py --outcome escalate   # a run on the dashboard
.venv/bin/python tools/fake_call.py                        # plays Chert's part in a private room
```

Built at Emory Hacks 2026 with ElevenLabs, LiveKit, MediaPipe and Chert. If you've worked on remote monitoring after stroke, I'd love to hear what we got wrong.
