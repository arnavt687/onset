# Orchestrator status

Read by the resume job (`onset-resume-pieces`) and by the orchestrator itself after a restart.
The orchestrator keeps this file current.

**Project status:** IN PROGRESS   <!-- the resume job stops nudging only when this says COMPLETE -->

**Waiting on user:** yes (Devpost 4/5, ready for review: user ticks rules box + Submit before noon ET. Video https://youtu.be/L58lr_4DPik unlisted, 2:17; Devpost says <2 min, user decides). USER APPROVED (14 Nov): orchestrator uploads video v2 to the user's YouTube as UNLISTED, puts the link in Devpost, makes sure satvikranga60@gmail.com is on the team. Do NOT click Submit; the user submits.   <!-- when "yes", the resume job leaves the orchestrator alone -->

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
