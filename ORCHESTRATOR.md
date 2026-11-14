# Orchestrator status

Read by the resume job (`onset-resume-pieces`) and by the orchestrator itself after a restart.
The orchestrator keeps this file current.

**Project status:** IN PROGRESS   <!-- the resume job stops nudging only when this says COMPLETE -->

## Sessions the resume job watches

| Session id | Role | Watch? |
|---|---|---|
| local_d6306b8b-9726-404e-a561-955849962a28 | Orchestrator (main session) | yes |
| local_e7ebf3e6-c8ac-4d27-923e-74acfa86c28d | chert-voice: why callers hear no agent audio | yes |

Finished, no longer watched: onset-face, onset-arms, onset-speech, onset-decide, onset-harness,
onset-chert-api (PRs #10–#15, all merged into `main` on 14 Nov 2026).

## Plan (orchestrator's checklist)

1. [x] Advisor pathway from Fable 5.2 (caption-first flow; dashboard; record real calls early; replay mode)
2. [x] Chert voice investigator session started (owns the live line until it says the line is free)
3. [x] Review and merge PRs #10–#15 (278 tests pass on main)
4. [ ] Piece 5: scripted call flow, caption-first overlay (every instruction, countdown and result on the video) — subagent on branch `piece/flow` (worktree `~/onset-wt/flow`)
5. [ ] Laptop demo dashboard + `tools/demo.sh` + `docs/rehearsal.md` — subagent on branch `piece/dashboard`
6. [ ] Replay mode: run the same pipeline from a recorded call — `tools/replay_flow.py` on `piece/flow`
   - Demo write-ups (run-of-show, deck text, Devpost, video plan, architecture, safety) — subagent on branch `piece/demo-docs`
   - Code review of merged pieces — subagent, report only
   - CI fixed on main (MediaPipe needs libegl1/libgles2 on the runner)
   - Event stream contract: `onset/events.py` (runs/live/events.jsonl, frame.jpg, audio/)
7. [ ] Real calls with the user: baseline call + acted "change" call, recorded (needs the line back from chert-voice)
8. [ ] Threshold sanity check on the real recordings
9. [ ] Caregiver iMessage: real send once the user creates a Chert project API key and the caregiver phone has texted the line
10. [ ] Demo deliverables: run-of-show, 6-slide deck, Devpost write-up, architecture diagram, 2–3 min video plan, safety framing
11. [ ] One-command start script, cold-start rehearsal checklist
12. [ ] Final review, README, mark this file COMPLETE and disable the resume job

## Notes for a resumed orchestrator

- Work happens in WSL: `~/onset` (Windows path `\\wsl.localhost\Ubuntu\home\arnav\onset`). See `WORKING.md`.
- Do not run `agent.py start` or `tools/chert.sh dev` while chert-voice holds the line.
- The user asked: operate end to end without asking permission for routine steps; use subagents to
  speed up; consult Fable 5.2 as an advisor; keep pushing to GitHub as work lands.
