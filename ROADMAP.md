# Onset development map

Each piece has an owner session, the files only it may change, what it depends on, and what
"done" means. Contracts live in [`onset/types.py`](onset/types.py). Status is tracked in the
piece's GitHub issue (label `piece`) and its pull request.

```
                 ┌──────────────── caller video ────────────────┐
 FaceTime ─ Chert ─ LiveKit ─ agent.py ─ 5 flow ─┬─ 1 face  ─┐  │
                                     │           ├─ 2 arms  ─┼─ 4 decide ─ 6 caregiver text
                                     │           └─ 3 speech ┘      (911 instruction spoken by 5)
                                     └─ 7 recorder/replay + CI (tests every piece offline)
 0 Chert audio (blocked on Chert)          8 outbound check-in calls (research)
```

| # | Piece | Owner | Only touches | Needs | Done when |
|---|-------|-------|--------------|-------|-----------|
| 0 | Agent audio reaches the caller | Chert support | (none: [report](CHERT_SUPPORT_REPORT.md)) | Chert | You hear Onset on a call |
| 1 | Smile asymmetry | `face` | `onset/measure/face.py`, `tests/test_face.py` | MediaPipe face model | `FaceMeter` returns a `FaceResult` with tests on synthetic and recorded frames; quality gates for no face, dark, motion, no smile |
| 2 | Arm drift (10 s hold) | `arms` | `onset/measure/arms.py`, `tests/test_arms.py` | MediaPipe pose model | `ArmMeter` returns an `ArmResult`; detects arms-up start, drift per side, quality gates |
| 3 | Sentence repetition | `speech` | `onset/speech.py`, `tests/test_speech.py` | ElevenLabs Scribe | `check_sentence` returns a `SpeechResult` (transcript, WER, rate, pauses); tested with TTS-generated clips, including slurred/garbled ones |
| 4 | Baseline and decision rules | `decide` | `onset/decide.py`, `tests/test_decide.py`, `docs/thresholds.md` | types only | `decide()` and `caregiver_summary()` tested; unclear or no baseline never gives NO_CHANGE_DETECTED; thresholds documented with their reasoning |
| 5 | Scripted call flow + overlay | integration (main session) | `agent.py`, `onset/flow.py`, `onset/script.py` | 1–4 | One call runs greet → smile → arms → sentence → decision → closing line, with live overlay; fake-call test passes |
| 6 | Caregiver iMessage | `chert-api` | `onset/chert_api.py`, `tests/test_chert_api.py`, `docs/chert-messaging.md` | Chert API | Sends (or dry-runs) the summary via `POST /api/v1/shared-messages`; documents trial limits on who can be texted |
| 7 | Recorder, replay and CI | `harness` | `tools/record.py`, `tools/replay.py`, `tests/conftest.py`, `.github/workflows/ci.yml` | types only | Opt-in call recording to `recordings/` (gitignored); replay feeds 1–3 offline; CI runs `pytest` with no keys |
| 8 | Outbound check-in calls | `chert-api` (after 6) | `onset/chert_api.py`, `docs/outbound.md` | Chert API | Documents whether `POST /api/v1/calls` works on the trial/Shared plan; implements it if it does |

## Rules for every session

- Work in your own git worktree and branch (see [WORKING.md](WORKING.md)); never in `~/onset` itself.
- Change only the files in your row. Need a contract change in `onset/types.py`? Make it a
  separate small commit and say so in the PR title.
- Never run `agent.py start` or `tools/chert.sh dev`: the line and the live agent belong to the
  main session. Test offline (`pytest`, replays, `tools/fake_call.py` is also main-session only).
- Commit and push at least every 30 minutes, and keep `notes/<piece>.md` current: what works,
  what is left, what to try next. That is how a resumed session picks up after a usage limit.
- Open a draft PR early; mark it ready when your "done" column is met. Do not merge it yourself.
- Safety: no output path may tell the person "you're fine" or name a diagnosis. When unsure,
  the result is UNCLEAR, which the flow treats like a detected change.
