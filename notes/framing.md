# framing: warn when the arms (or face) are not in frame, then retry the step

Before this change, if the caller's wrists or shoulders were out of the picture during the arms
step, the arm meter reported PARTIAL or NO_SUBJECT, `decide` turned that into UNCLEAR, and the
call ended with "call 911 + caregiver". Now Onset says what is wrong with the picture and how
to fix it, then runs the step again, at most **2 times per step**. If it still cannot see the
arms or face after that, behaviour is the same as before (UNCLEAR, then caregiver + 911), so
the feature can never hold back an escalation for good.

## Behaviour

**Arms, before the 10 s hold (`arms_wait`).** The new `ArmMeter.framing()` checks both
shoulders and both wrists live. If there is a framing problem, 4 s into the step, and it has
lasted 1.5 s, the flow:
- shows and says a specific line ("I can't see your left hand. Move back so both hands show.");
- emits a `framing` event;
- resets the meter and waits again (up to 15 s for the arms to come up).

A second warning comes no sooner than 8 s after the first. Specific hints:

| Problem | Line |
|---|---|
| Left or right hand missing | "Move back so both hands show" |
| Both hands missing | "Move back so your hands are in the picture" |
| Hands above the top edge | "Hold your arms straight out, at shoulder height" |
| Shoulders below the bottom edge | "Move back, or tilt the phone down" |
| Shoulders above the top edge | "Tilt the phone up a little" |
| Off to the side | "Face the phone, in the middle of the picture" |
| No body found | "Prop the phone so I see you from the waist up" |
| Too dark | "Please turn on a light" |

**During the hold.** If an arm is out of the picture for more than 1 s, the hold stops, Onset
says "Let's start the hold again." plus the hint, and the hold restarts from zero. If the hold
finishes but its result still cannot be read (PARTIAL, NO_SUBJECT or TOO_DARK), it also gets
one more try.

**Face step.** If the face is missing for most of the neutral window, the warning comes right
away. If the smile result is NO_SUBJECT, PARTIAL or TOO_DARK, the step shows a new
`face_reframe` step ("I can't see your face. Hold the phone in front of you.", or "turned" /
"edge" / "too dark" hints) and then runs neutral and smile again.

**Never retried, because these are findings:**
- a wrist below the bottom edge (an arm not raised, or one that came down);
- during the hold, a wrist that was already moving down when it disappeared. This is guarded
  in `framing_of(during_hold=True)`. That hold is never restarted, and its result notes
  `dropped_out_of_view`;
- `early_drop`;
- arms never raised or no smile (NO_RESPONSE);
- MOTION and ERROR results.

**Time cap.** Each retry extends the call's time cap by what it costs, so a retry never cuts
off the sentence step. The worst case is bounded by the retry limit.

**Decision reasons.** `decide`'s UNCLEAR reason mentions the retries, e.g. "The person was only
partly in view during the arm check, even after 2 tries to fix the camera view". A result that
was measured after retries carries `framing_retries` and `framing_problem` in its `detail`.

## Events and dashboard

- **New event type:** `framing` {check, phase, problem, action: retry | give_up, retry,
  max_retries, caption}, documented in `onset/events.py`.
- **Live metrics:** the arms live metrics now include `framing`.
- **New step name:** `face_reframe`.
- **Dashboard:** the face/arms card shows "Reframing: left hand out of view · try 1 of 2" while
  the step runs, and "even after N retries" / "Measured after N framing retries" on the result.

## Files

- `onset/measure/arms.py`: `frame_problem`, `framing_of`, `Framing`, `ArmMeter.framing()`, and
  `framing` added to `live()`.
- `onset/measure/face.py`: `face_problem`, `framing_of`, `FaceFraming`, `FaceMeter.framing()`.
- `onset/flow.py`: only the face and arms steps, plus one-line changes to the time cap and the
  new `Timing` fields. The closing/escalation path is untouched.
- `onset/script.py`: 13 new lines (all in `ALL_LINES`, so they are TTS-warmed and checked for
  banned words), `ARMS_FRAMING`, `FACE_FRAMING`, `framing_line()`.
- `onset/decide.py`: `_quality_line` adds the retry note.
- `onset/events.py`: docstring.
- `web/dashboard.html`
- `tools/render_captions.py`: renders every new caption.
- `docs/thresholds.md`: new "Framing retries" section.
- `README.md`: outcomes paragraph.
- `tests/test_framing.py`: 52 tests, new.

## Tests

- `pytest -q tests`: **462 passed, 6 skipped** (main had 410; 52 new in `tests/test_framing.py`).
  - Pure framing: every problem class; lowered hands are not a problem; trailing-run seconds;
    the drop guard.
  - Flow with scripted meters: wait warning and its timing; hold restart and its timing; grace
    period; retry cap then UNCLEAR + 911 + caregiver; `max_reframes=0` gives exactly the old
    behaviour; a dropped arm is never retried; findings are never retried; escalation after a
    retry; the cap extension and the hard cap; all face cases.
  - The real `ArmMeter` maths fed synthetic pose tracks: hand hidden then fixed, hand lost
    mid-hold then restarted, drifting arm that vanishes is never retried, never framed gives
    UNCLEAR.
- `tools/render_captions.py`: every new caption fits in 2 lines at 640x360 (28–33 px cap height).
- Private fake call (own agent name `onset-framingtest` and its own room, not the Chert line),
  using the agent and the fake caller's no-person test pattern. The run went through 2 face
  retries, give_up, 2 arm retries, give_up, then the old fallback. There were no errors in the
  agent log. This run is why repeat warnings now wait 8 s instead of 4 s: the first spoken
  warning had not finished before the second one fired.

## Not verified

- With a real person on a real FaceTime call. The thresholds (1 s grace, 1.5 s persistence,
  0.25 sw drop guard) are first guesses, like the rest of `docs/thresholds.md`.
- Spoken lines queue behind each other. With audio on, a warning can be heard a few seconds
  after its caption appears. Captions are shown on time.
