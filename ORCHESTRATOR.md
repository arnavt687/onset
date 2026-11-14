# Orchestrator status

Read by the resume job (`onset-resume-pieces`) and by the orchestrator itself after a restart.
The orchestrator keeps this file current.

**Project status:** IN PROGRESS   <!-- the resume job stops nudging only when this says COMPLETE -->

## Sessions the resume job watches

| Session id | Role | Watch? |
|---|---|---|
| local_d6306b8b-9726-404e-a561-955849962a28 | Orchestrator (main session) | yes |
| local_e7ebf3e6-c8ac-4d27-923e-74acfa86c28d | chert-voice: why callers hear no agent audio (DONE, PR #16 merged: Chert's Mac audio broken both ways, `audio_ready:false`; see docs/chert-audio.md) | no |
| local_0053261f-bc4f-46be-a326-8055b2809ecc | polish-pizzazz: cinematic HTML deck with 3D + live demo, polish of overlay, dashboard, README, video assets (branch `polish/pizzazz`) | yes |

Finished, no longer watched: onset-face, onset-arms, onset-speech, onset-decide, onset-harness,
onset-chert-api (PRs #10–#15, all merged into `main` on 14 Nov 2026).

## Plan (orchestrator's checklist)

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
7. [ ] Real calls with the user: baseline call + acted "change" call, recorded (needs the line back from chert-voice)
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
