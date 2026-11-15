# Orchestrator status

Read by the resume job (`onset-resume-pieces`) and by the orchestrator itself after a restart.
The orchestrator keeps this file current.

**Project status:** IN PROGRESS   <!-- the resume job stops nudging only when this says COMPLETE -->

**Waiting on user:** yes, nothing blocking. Everything asked for on 15 Nov is done (see the 15 Nov entry). The user should run through the deck out loud once (the new voice commands were tested with simulated speech only).

**14 Nov, afternoon:** the friend's Chert line (rangasatvik, "Alex Smith") has working audio. Issue #27
(captions cropped, video 1-2 s behind) fixed on main: upright 540x960 picture with a safe area, bounded
caller-frame queue, cheaper compositing, simulcast off, doctor `video framing` row (docs/video-framing.md).
NOT yet confirmed on a real call: the friend must `git pull`, remove OUT_WIDTH/OUT_HEIGHT from his
.env.local, and call from an iPhone and a Mac. This computer's Gemini TTS answered 429 and ElevenLabs is
out of credits, so local test calls speak with a chime.

**14 Nov, evening:** issue #28 (demo.sh blockers) fixed on main (3baf6c4), commented, left open for the
friend to confirm on his Mac. New presentation `web/present/` (7 scenes, live demo in the middle), served at
http://localhost:8765/present (`tools/demo.sh --present`, `tools/present.sh`); voice commands ("next slide"),
a microphone picker and an AirPod/media-key clicker (df244aa). Waiting on the user: try voice with their real
AirPods and report what the panel's "Heard" and "Listening on" lines say. Known limit: on Windows, AirPods are
a microphone or a clicker, not both. The orchestrator's session keeps a dashboard server on port 8765 for the
user to view the presentation: stop it before `tools/demo.sh`.

**14 Nov, night:** #29 (caregiver text refused): `tools/chert_join.py` + explained refusals; cause on Satvik's line
still unknown. #30 (caregiver joins, cannot talk): fixed (short link, talk key handed over in the call, one tap);
tested with the real page in a browser, not on an iPhone. New: `ONSET_CAREGIVER_CALL=after` makes Onset FaceTime the
caregiver after the patient's call (`tools/caregiver_call.py`, `onset/brief.py`); agent side tested with a fake
bridge, NOT confirmed against Chert (needs Satvik's key; trial outbound unknown). Chert's code is not public: no PR
possible; `docs/chert-feedback.md` is the write-up (not sent: sending is the user's call) and the presentation has
a slide for it (scene 6, step 2). Waiting on Satvik: the commands in the #29 and #30 comments.

**15 Nov, early morning:** final demo video (2:11, the user's own clips cut with the earlier video's graphics, per-line
speed) is public on YouTube: https://youtu.be/SZwhrKOpcSY (file and editor are outside the repo, in the user's Videos
folder). Devpost (project page and the Emory Hacks submission form, which keep separate copies of the video link) now
has that video, a write-up that matches what Onset does today (caregiver joins by link and can talk; audio works on
the second Chert line; the caregiver FaceTime call and group call are described as untested on a live line), and nine
new gallery screenshots with captions; the submission still shows SUBMITTED 5/5. `docs/demo/devpost.md` is an older
copy and was not updated. Presentation voice commands now work inside a sentence, once each, with slide jumps
("go to the second slide") (e96abfc, `web/present/voice.js`, 275 new tests run under Node); not tried with live speech.
Known and left alone at the user's choice: `tests/test_launch.py::test_demo_sh_parses` fails in CI (it failed before
e96abfc too). The onset.health domain was dropped by the user: nothing was changed on Pages or DNS. The dashboard
server on port 8765 is no longer running. Still waiting on Satvik/Chert: the commands in the #29 and #30 comments.

## Sessions the resume job watches

| Session id | Role | Watch? |
|---|---|---|
| local_d6306b8b-9726-404e-a561-955849962a28 | Orchestrator (main session) | yes |
| local_e7ebf3e6-c8ac-4d27-923e-74acfa86c28d | chert-voice: why callers hear no agent audio (DONE, PR #16 merged: Chert's Mac audio broken both ways, `audio_ready:false`; see docs/chert-audio.md) | no |
| local_0053261f-bc4f-46be-a326-8055b2809ecc | polish-pizzazz (DONE, merged): 3D deck `web/pitch/` (artifact https://claude.ai/artifact/JDWtXmwJmBWh5cw58z2RmU), overlay, dashboard, README, video assets `web/video/` | no |
| local_55f03ddc-a502-4d31-a46e-315e532ae7bb | onset-framing: arms/face not in frame -> warn, reposition, retry (branch piece/framing, PR) (DONE, merged) | no |
| local_fba7da10-3263-4e19-b829-18a6891b631f | onset-chert-ready: works on a working Chert line; caregiver joins the call on escalation; doctor; docs (piece/chert-ready-*) (DONE, merged) | no |
| local_0283a3c4-3a8c-4991-a5c9-867ad7ced177 | onset-video-v2: video v2 (Eric voice, user edits) + logo; due 10:30 ET -> C:\Users\arnav\Videos\onset-demo-v2.mp4 (DONE, merged) | no |

Finished, no longer watched: onset-face, onset-arms, onset-speech, onset-decide, onset-harness,
onset-chert-api (PRs #10–#15, all merged into `main` on 14 Nov 2026).

## Plan (orchestrator's checklist)

0. [x] 14 Nov sprint (merged: #17-#25; 640 tests; video v2 done). Remaining: YouTube upload + Devpost link (user edits): escalation now = caregiver added to the call + strong 911 (script.py, bc98b90);
   video v2 + logo; framing retry; Chert-ready. Merge PRs, then put the v2 link and updated wording into Devpost
   (drop no-LLM / never-diagnoses / synthetic wording there too).


1. [x] Advisor pathway from Fable 5.2 (caption-first flow; dashboard; record real calls early; replay mode)
2. [x] Chert voice investigator session started (owns the live line until it says the line is free)
3. [x] Review and merge PRs #10–#15 (278 tests pass on main)
4. [x] Piece 5: scripted call flow, caption-first overlay — merged (`onset/flow.py`, `script.py`, `overlay.py`, `voice.py`, `agent.py`); 312 tests pass. Fake call: `tools/fake_call_private.sh` (runs as `onset-flowtest`, never touches the line). Face/arms only tested on synthetic frames so far.
5. [x] Laptop demo dashboard + `tools/demo.sh` + `docs/rehearsal.md` — merged (406 tests). Rehearse: `tools/dashboard.py` + `tools/demo_events.py --outcome escalate`. Demo day: `tools/demo.sh --check`, then `tools/demo.sh --open`.
6. [x] Replay mode: `tools/replay_flow.py <recording>` (merged with the flow). Dashboard must keep playing `say` audio that arrives after `run_ended`.
   - Demo write-ups (run-of-show, deck text, Devpost, video plan, architecture, safety) — subagent on branch `piece/demo-docs`
   - Code review of merged pieces: 13 findings fixed on `fix/review` and merged (396 tests pass). Known limit kept on
     purpose: the -0.5 Scribe confidence cut-off does not catch the mildest synthetic slurs (-0.13 to -0.36); tune only
     with real recordings. Say this in the limits slide / Devpost.
   - CI fixed on main (MediaPipe needs libegl1/libgles2 on the runner)
   - Event stream contract: `onset/events.py` (runs/live/events.jsonl, frame.jpg, audio/)
7. [ ] Real calls with the user: baseline call + acted "change" call, recorded. Line is free. The user will do these TOMORROW (15 Nov); don't nudge before then. Before the calls: resync the Ubuntu clock (it drifts ~1 s fast; Chert declines over 0.25 s), start with `ONSET_RECORD=1 tools/demo.sh`.
   - Devpost: Emory Hacks 2026, track Healthcare, deadline about 15 Nov 2026 (~1 day). DRAFT saved (2/5 steps):
     https://devpost.com/submit-to/32117-emory-hacks-2026/manage/submissions/1301985-onset/project-overview
     Now 3/5 steps (schools = University of North Georgia; story says 406 tests).
     Chert Side-Challenge selected and opted in (user approved). DEADLINE: noon US Eastern, Sat 14 Nov 2026.
     Repo made PUBLIC (user asked; privacy scan of tree + history was clean). Devpost agent: ticking all AI tools used
     and selecting every prize Onset is eligible for (user asked).
     Video v1 DONE: `C:\Users\arnav\Videos\onset-demo-v1.mp4` (2:45, 1080p, narrated, subtitles, synthetic shots
     labelled). Rebuild scripts merged in `tools/video/` (`make.ps1 -Steps build`; swap shots in `timeline.cjs`, edit
     `narration.json`). Next: YouTube upload (user's account) → paste link in Devpost → user submits.
     (Original plan: subagent producing a narrated, subtitled MP4 from the real dashboard on synthetic runs (labelled
     synthetic), caption renders and the deck → `C:\Users\arnav\Videos\onset-demo-v1.mp4`. The user uploads it to YouTube
     (their account) and pastes the link; v2 with real call footage only if the morning calls happen early enough.
     Still needed: video link (required; after tomorrow's recordings), AI tools list (add Anthropic Claude? user), repo is private but required (make
     public or share with judges: user's call), fix "278 tests" → current count, then the user ticks the rules box and
     clicks "Submit project" themselves or approves us doing it.
8. [ ] Threshold sanity check on the real recordings
9. [ ] Caregiver iMessage: real send once the user creates a Chert project API key and the caregiver phone has texted the line
10. [~] Demo deliverables: run-of-show, deck text, Devpost, video plan, architecture, safety in `docs/demo/` (merged).
    Slide deck published as an Artifact: https://claude.ai/artifact/1bBHtjNdPuv1yDpmrJEZxj (8 slides; files in the
    orchestrator's scratchpad `deck/`). Still to do: team names, real screenshots once the dashboard and flow land,
    match the Devpost and run-of-show wording to `onset/script.py`.
11. [~] One-command start script and rehearsal checklist are merged (`tools/demo.sh`, `docs/rehearsal.md`); the cold-start rehearsal needs the line back from chert-voice
12. [ ] Final review, README, mark this file COMPLETE and disable the resume job

## Notes for a resumed orchestrator

- Work happens in WSL: `~/onset` (Windows path `\\wsl.localhost\Ubuntu\home\arnav\onset`). See `WORKING.md`.
- Do not run `agent.py start` or `tools/chert.sh dev` while chert-voice holds the line.
- The user asked: operate end to end without asking permission for routine steps; use subagents to
  speed up; consult Fable 5.2 as an advisor; keep pushing to GitHub as work lands.
