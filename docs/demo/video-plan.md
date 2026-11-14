# Onset: demo video plan (about 2:40)

**Rule for the whole video: subtitles on every second of it.** The agent's audio does not
reach the phone over FaceTime yet (a Chert-side issue being investigated), and the phone's own
recording may have no sound at all. So: burned-in subtitles for the voice-over, and the
agent's on-video captions kept readable (zoom in when they are small).

## What to record

| Source | How | Used in |
|---|---|---|
| **Dashboard** | Screen-record the laptop at 1920x1080, 30 fps (OBS or the OS recorder), with laptop audio, so the agent's voice played by the dashboard is captured | Shots 3 to 9 |
| **Phone screen** | iOS screen recording of the FaceTime call. If it does not capture the call, film the phone over the Caller's shoulder with a second phone or camera | Shots 3, 5, 7 |
| **Room camera** | A wide shot of the Caller at the phone stand: shows the arms and the smile from the side | Shots 5, 7 |
| **Caregiver phone** | Screen-record the second iPhone receiving the iMessage | Shot 8 |
| **Terminal (optional)** | `pytest -q tests` passing, `docs/thresholds.md` scrolled | Shot 10 |
| **Voice-over** | Recorded separately, quiet room, read from the script below | All |
| **Stills** | `architecture.svg` and deck slides 5 and 6 | Shots 2, 9, 11 |

Before recording:

- Hide or blur every phone number (Chert's line, the caregiver), contact names, notifications
  and any API key or `.env.local` content. Crop the FaceTime header if it shows the number.
- Only team members who agreed appear on camera.
- Record two real calls: one usual, one acted change. Keep the raw files outside git (the repo
  ignores `recordings/` and `*.mp4`).
- Record each call twice so there is a clean take.

## Shot list and voice-over

Times are cumulative. **VO** is the voice-over, which is also the subtitle text.

| # | Time | On screen | VO / subtitle |
|---|---|---|---|
| 1 | 0:00–0:12 | Black, then the Caller at home picking up an iPhone. Title: "Onset" | "After a stroke or a mini-stroke, people go home, and their family becomes the early warning system." |
| 2 | 0:12–0:25 | Slide 1 text, then a FAST card: smile, arms, speech, call 911. Source line: CDC | "Nearly one in four strokes in the US happen to someone who has had one before. The public checklist is FAST: smile, raise both arms, repeat a phrase." |
| 3 | 0:25–0:35 | Phone: Caller taps Onset in FaceTime. Split screen with the dashboard as the call connects | "Onset is a one-minute check-in you FaceTime. No app to install. It runs the FAST checks and compares you with yourself." |
| 4 | 0:35–0:50 | Dashboard: smile step. Zoom on the mouth-corner landmarks and the left / right lift numbers next to the baseline | "First, a smile. Face landmarks run on the laptop's CPU and measure how far each corner of the mouth lifts." |
| 5 | 0:50–1:05 | Room camera: arms out. Dashboard: 10-second countdown on the agent's video, drift numbers | "Then both arms up for ten seconds. Each wrist is measured against the shoulders, so a wobbly phone doesn't count as drift." |
| 6 | 1:05–1:20 | Dashboard: sentence step, transcript appearing, word error rate, speaking rate, longest pause. Then the caption: "No change detected compared with your usual check-in." | "Then a sentence to repeat. And the result. Not 'you're fine'. No change detected, compared with this person's usual check-in." |
| 7 | 1:20–1:45 | Second call, acted change. Split: room camera (one-sided smile, left arm sinking) and dashboard (numbers past baseline, pause highlighted) | "A different morning. We act out three changes: a one-sided smile, one arm sinking, and a pause mid-sentence." |
| 8 | 1:45–2:00 | Agent's video: "I noticed a change. Please call 911 now." Cut to the caregiver phone receiving the iMessage; hold long enough to read it | "Any change, or anything it can't measure clearly, gets the same answer: call 911 now. The caregiver gets a text saying what it noticed. It never names a condition." |
| 9 | 2:00–2:15 | `architecture.svg`, building box by box | "FaceTime reaches the agent through Chert and LiveKit. MediaPipe and ElevenLabs Scribe measure. Fixed rules decide. There is no LLM in the decision path." |
| 10 | 2:15–2:25 | Slide 5 (Safety by design). Optional: terminal with tests passing | "It leans toward false alarms on purpose. Recordings and baselines stay on the machine." |
| 11 | 2:25–2:40 | Slide 6 (Limits and roadmap), then end card: "Onset. Built with ElevenLabs, LiveKit and Chert. Research prototype, not a medical device." | "The thresholds are provisional, tuned on synthetic data and one person. Next is a clinician-led validation study. Built with ElevenLabs, LiveKit and Chert." |

## Subtitle and caption notes

- Burn in the VO subtitles (bottom, white on a dark band, at least 4.5:1 contrast). Do not
  rely on platform captions alone.
- Where the agent is speaking (from the dashboard audio), subtitle its lines too, labelled
  "Onset:", even though its own caption is in the shot.
- Keep each subtitle up long enough to read at an older viewer's pace: no more than about two
  lines at a time.
- The end card's "not a medical device" line stays on screen for at least 3 seconds.

## If something is missing on the day

- **No phone audio at all:** expected. The dashboard recording carries the agent's voice; the
  subtitles carry everything else.
- **Caregiver text didn't arrive:** use the dashboard's caregiver panel, and if it says dry
  run, say it in the subtitle: "Shown as a dry run: this is the text Onset sends."
- **A live call failed:** use replay mode on a recorded call and label it on screen: "Replay
  of a recorded call, same pipeline."
- **A take came out "unclear" by accident:** keep it as an extra shot. It shows the design:
  unclear means call 911.

## Words to avoid in the edit

"Detects strokes", "diagnoses", "accurate", any percentage for sensitivity or accuracy, and
"you're fine" except when saying Onset never says it.

## Cards to screen-record

`web/video/index.html` (serve `web/` with `python3 -m http.server 8771 --directory web`, open
`http://localhost:8771/video/index.html?rec`, full screen, record at 1920x1080):

- `#title` shot 1: "Onset", the tagline and "Research prototype · not a medical device".
- `#lower-1` … `#lower-7` lower thirds: Call 1, Call 2 (alert), Smile, Arms, Speech, Replay,
  "Shown as a dry run". Add `&bg=green` to key them over footage.
- `#subs&text=…` the burned-in subtitle style (white on a dark band, `<b>` marks a word).
- `#end` shot 11's end card; it holds, so keep it on screen for at least 3 seconds.

Space replays a card's entrance; `←` `→` step through them. `web/pitch/index.html#slide-12`
and `#slide-13` are slides 5 and 6 of this plan (safety, limits).
