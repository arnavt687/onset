# Demo video v2

`C:\Users\arnav\Videos\onset-demo-v2.mp4` (2:52.9, 1920x1080, 30 fps, H.264/AAC, subtitles burned in) and `onset-demo-v2.srt` next to it. Built by `tools/video/make.ps1` (see `timeline.cjs`, `narration.json`). Narrator: ElevenLabs Eric (`cjVigY5qzO86Huf0OWal`). Devpost thumbnail: `docs/demo/screenshots/polish/thumbnail-3x2-v2.jpg`.

```
SHOTS
 0  0:00.0-0:06.0  card title
 1  0:06.0-0:18.7  deck slide 2  [The problem: Recurrence risk is front-loaded]
 2  0:18.7-0:35.8  deck slide 1  [What it is: Prescribed 90-day remote monitoring]
 3  0:35.8-0:39.4  phone face_smile
 4  0:39.4-0:44.7  deck slide 8
 5  0:44.7-0:48.1  phone arms_hold
 6  0:48.1-0:53.6  deck slide 9
 7  0:53.6-0:59.2  deck slide 10  [Check 3 of 3: Repeat a sentence]
 8  0:59.2-1:06.4  phone sentence_go
 9  1:06.4-1:11.8  dashboard first_run  [Monitoring dashboard: First check-in: baseline recorded]
10  1:11.8-1:14.8  dashboard no_change (zoom)
11  1:14.8-1:19.0  dashboard no_change  [Usual morning: No change detected]
12  1:19.0-1:26.5  dashboard escalate (zoom)  [A change: One-sided smile · arm drift · pause]
13  1:26.5-1:29.1  dashboard escalate
14  1:29.1-1:34.1  dashboard escalate  [Change noticed: Caregiver added · 911 strongly advised]
15  1:34.1-1:39.6  phone close_escalate
16  1:39.6-1:43.0  dashboard escalate (zoom)  [Caregiver iMessage: What changed against the baseline]
17  1:43.0-1:46.0  dashboard escalate (zoom)
18  1:46.0-1:52.4  phone arms_reframe
19  1:52.4-1:59.4  dashboard unclear  [Persistent failure: Still unclear after a retry: escalate]
20  1:59.4-2:15.0  deck slide 7  [How it works: FaceTime → Chert → LiveKit → agent]
21  2:15.0-2:23.2  deck slide 12  [Safety by design: Sensitivity over specificity]
22  2:23.2-2:33.6  deck slide 12  [Longitudinal record: A 90-day trajectory for the care team]
23  2:33.6-2:46.4  deck slide 13  [Limits and what’s next: Clinician-led validation study]
24  2:46.4-2:52.9  card end

VOICE-OVER
0:00.8-0:18.4  n01_hook
0:19.0-0:35.3  n02_what
0:36.1-0:44.3  n04_smile
0:45.0-0:53.2  n05_arms
0:53.9-1:06.0  n06_speech
1:06.7-1:18.2  n07_usual
1:19.6-1:26.2  n08_change
1:27.5-1:38.8  n09_red
1:40.0-1:45.0  n10_caregiver
1:46.4-1:58.6  n11_unclear
1:59.9-2:14.4  n12_how
2:15.4-2:33.0  n13_safety
2:34.0-2:45.8  n14_limits

total 2:52.9
```

## Narration

The weeks after hospital discharge are a high-risk window for another stroke. The CDC reports that nearly one in four US strokes are recurrent. After a TIA, ten to fifteen percent of people have a major stroke within three months, and much of that risk falls in the first weeks.

Onset is a FaceTime-native agent for stroke and TIA survivors in the ninety days after discharge. The care team prescribes it as part of a remote monitoring plan: a daily one-minute check-in against the patient's own baseline, to catch early signs of recurrence.

Each check-in runs the FAST checks. First, a smile. Face landmarks measure how far each corner of the mouth lifts.

Then both arms up for ten seconds. Each wrist is measured against the shoulder line, so camera shake doesn't register as drift.

Then a sentence to repeat. Onset scores word error rate, speaking rate and the longest pause. Every instruction is also captioned on the agent's video, so the check works with the sound off.

This is the monitoring dashboard. The first check-in records the patient's baseline, and every later metric is shown against it. On a usual morning, the result is: no change detected.

Now a change: a one-sided smile, one arm drifting down, and a pause in the middle of the sentence.

Each metric crosses its threshold. Onset doesn't simply hang up and dial 911. It adds the caregiver to the live call and strongly recommends calling 911 now.

The caregiver also receives an iMessage summarizing what changed against the baseline.

If the face or arms are out of frame, Onset says so, asks the person to reposition, and repeats the step. Only a persistent failure is treated as unclear, and unclear escalates like a change.

FaceTime reaches the agent through Chert and LiveKit. MediaPipe measures the face and arms on the CPU, ElevenLabs Scribe transcribes the sentence, and deterministic rules compare each metric with the baseline.

The thresholds favor sensitivity over specificity on purpose: a false positive costs a phone call. Every check-in's recordings and metrics are stored and analyzed longitudinally against prior sessions, so the care team sees the patient's trajectory across the ninety days.

Onset is a research prototype, not a medical device. The thresholds are provisional, and the next step is a clinician-led validation study. Built with ElevenLabs, LiveKit and Chert.

Sources: CDC, Stroke Facts (nearly 1 in 4 US strokes are in people who had a previous stroke); CDC, About Transient Ischemic Attack (as many as 10% to 15% of people have a major stroke within 3 months of a TIA).
