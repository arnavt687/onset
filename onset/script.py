"""Every line Onset says or shows, in one place.

Rules for these lines (they are read by older adults on a phone, and heard when audio works):
  * one instruction at a time, plain words, calm;
  * captions are short enough to read in a glance at 640x360 (the flow wraps them);
  * no line ever says "you're fine", names a condition, or claims to diagnose.

`say` is what the voice speaks; `caption` is what the caller sees on the video. The caption
always carries the instruction itself, because the caller may not hear the voice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    say: str
    caption: str


GREETING = Line(
    say="Hello, this is Onset, your check-in helper. This takes about one minute. Let's begin.",
    caption="Hello, this is Onset. A one-minute check-in.",
)

# Face-step captions sit at the top of the picture: keep them to one short line so they
# do not cover the face (the measurement uses the raw camera frame either way).
FACE_NEUTRAL = Line(
    say="Please look at the camera, and relax your face.",
    caption="Look here. Relax your face.",
)

FACE_SMILE = Line(
    say="Now give me a big smile, and hold it.",
    caption="Big smile! Hold it.",
)

ARMS_RAISE = Line(
    say="Now raise both arms straight out in front of you, and keep them up.",
    caption="Raise both arms straight out. Keep them up.",
)

ARMS_HOLD = Line(
    say="Good. Keep both arms up until the bar is full.",
    caption="Keep both arms up.",
)

ARMS_DONE = Line(
    say="Thank you. You can lower your arms.",
    caption="You can lower your arms.",
)

ARMS_TIMEOUT = Line(
    say="That's all right. Let's move on.",
    caption="That's all right. Let's move on.",
)


# ---- framing: the camera cannot see what a step measures. Shown (and said) before the step
# runs again; see CheckFlow and docs/thresholds.md ("Framing retries").

FRAME_DARK = Line(
    say="It's too dark for me to see you. Please turn on a light.",
    caption="It's too dark to see you. Please turn on a light.",
)

FRAME_NO_BODY = Line(
    say="I can't see you. Please prop the phone up so I can see you from the waist up.",
    caption="I can't see you. Prop the phone so I see you from the waist up.",
)

FRAME_MOVE_BACK = Line(
    say="I can't see both of your arms. Please move back so your hands are in the picture.",
    caption="I can't see both arms. Move back so your hands are in the picture.",
)

FRAME_TILT_DOWN = Line(
    say="I can't see your shoulders. Please move back, or tilt the phone down a little.",
    caption="I can't see your shoulders. Move back, or tilt the phone down.",
)

FRAME_TILT_UP = Line(
    say="I can't see your shoulders. Please tilt the phone up a little.",
    caption="I can't see your shoulders. Tilt the phone up a little.",
)

FRAME_CENTER = Line(
    say="Please face the phone, in the middle of the picture.",
    caption="Please face the phone, in the middle of the picture.",
)

FRAME_HANDS_HIGH = Line(
    say="Your hands are above the picture. Please hold your arms straight out, at shoulder height.",
    caption="Hands too high. Hold your arms straight out, at shoulder height.",
)

FRAME_LEFT_HAND = Line(
    say="I can't see your left hand. Please move back so both hands are in the picture.",
    caption="I can't see your left hand. Move back so both hands show.",
)

FRAME_RIGHT_HAND = Line(
    say="I can't see your right hand. Please move back so both hands are in the picture.",
    caption="I can't see your right hand. Move back so both hands show.",
)

ARMS_RESTART = Line(
    say="Let's start the hold again.",
    caption="Let's start the hold again.",
)

FACE_NOT_VISIBLE = Line(
    say="I can't see your face. Please hold the phone in front of you.",
    caption="I can't see your face. Hold the phone in front of you.",
)

FACE_OFF_CENTER = Line(
    say="Your face is at the edge of the picture. Please move it to the middle.",
    caption="Please move your face to the middle of the picture.",
)

FACE_TURNED = Line(
    say="Please look straight at the phone.",
    caption="Please look straight at the phone.",
)

# Problem names from onset.measure.arms.FRAMING_PROBLEMS / face.FACE_FRAMING_PROBLEMS.
ARMS_FRAMING = {
    "too_dark": FRAME_DARK,
    "no_body": FRAME_NO_BODY,
    "shoulders_low": FRAME_TILT_DOWN,
    "shoulders_high": FRAME_TILT_UP,
    "shoulders_side": FRAME_CENTER,
    "both_hands": FRAME_MOVE_BACK,
    "hands_high": FRAME_HANDS_HIGH,
    "left_hand": FRAME_LEFT_HAND,
    "right_hand": FRAME_RIGHT_HAND,
}
FACE_FRAMING = {
    "too_dark": FRAME_DARK,
    "no_face": FACE_NOT_VISIBLE,
    "edge": FACE_OFF_CENTER,
    "turned": FACE_TURNED,
}


def framing_line(check: str, problem: str | None) -> Line:
    """The fix-it line for a framing problem; a generic one for anything unknown."""
    table = FACE_FRAMING if check == "face" else ARMS_FRAMING
    return table.get(problem or "", FACE_NOT_VISIBLE if check == "face" else FRAME_MOVE_BACK)


def sentence_prompt(sentence: str) -> Line:
    return Line(
        say=f"Last one. Please repeat this sentence after me: {sentence}",
        caption=f"Get ready to say: “{sentence}”",
    )


def sentence_go(sentence: str) -> Line:
    return Line(say="", caption=f"Say it now: “{sentence}”")

DECIDING = Line(say="Thank you. One moment.", caption="Thank you. One moment.")

# ---- closings: the caption stays on screen until the caller hangs up

CLOSE_ESCALATE = Line(
    say=(
        "Some of your results were different from your usual check-in. "
        "I am adding your caregiver to this call now. Please call 9 1 1."
    ),
    caption="Change noticed. Adding your caregiver to this call. Please call 911 now.",
)

CLOSE_UNCLEAR = Line(
    say=(
        "I could not check everything clearly this time. "
        "I am adding your caregiver to this call now. To be safe, please call 9 1 1."
    ),
    caption="I could not check clearly. Adding your caregiver. Please call 911 now.",
)

# Said when the caregiver opens the link and joins (they can see the caller).
CAREGIVER_JOINED = Line(
    say="Your caregiver has joined the call. Please still call 9 1 1.",
    caption="Your caregiver has joined. Please call 911 now.",
)

# Said when the caregiver starts talking: from here the caller hears them through Onset.
CAREGIVER_TALKING = Line(
    say="You can talk to your caregiver now. Please still call 9 1 1.",
    caption="Talking with your caregiver. Please call 911 now.",
)

CLOSE_NO_CHANGE = Line(
    say=(
        "I didn't notice any change from your usual check-in. "
        "If you feel unwell at any time, call 9 1 1. Goodbye."
    ),
    caption="No change from your usual check-in. If you feel unwell, call 911.",
)

CLOSE_BASELINE_SAVED = Line(
    say=(
        "Thank you. I've recorded your usual check-in, so I can compare next time. "
        "If you feel unwell at any time, call 9 1 1. Goodbye."
    ),
    caption="Your usual check-in is recorded. If you feel unwell, call 911.",
)

CLOSE_BASELINE_FAILED = Line(
    say=(
        "I couldn't record your usual check-in clearly this time, so we will try again later. "
        "If you feel unwell at any time, call 9 1 1. Goodbye."
    ),
    caption="Let's try again later. If you feel unwell, call 911.",
)

# Step progress labels shown on screen ("Step 1 of 3 · Smile").
PROGRESS = {
    "face_neutral": (1, "Smile"),
    "face_smile": (1, "Smile"),
    "face_reframe": (1, "Smile"),
    "arms_wait": (2, "Arms"),
    "arms_hold": (2, "Arms"),
    "sentence": (3, "Sentence"),
}

ALL_LINES = [
    GREETING, FACE_NEUTRAL, FACE_SMILE, ARMS_RAISE, ARMS_HOLD, ARMS_DONE, ARMS_TIMEOUT,
    FRAME_DARK, FRAME_NO_BODY, FRAME_MOVE_BACK, FRAME_TILT_DOWN, FRAME_TILT_UP, FRAME_CENTER,
    FRAME_HANDS_HIGH, FRAME_LEFT_HAND, FRAME_RIGHT_HAND, ARMS_RESTART,
    FACE_NOT_VISIBLE, FACE_OFF_CENTER, FACE_TURNED,
    DECIDING, CLOSE_ESCALATE, CLOSE_UNCLEAR, CAREGIVER_JOINED, CAREGIVER_TALKING, CLOSE_NO_CHANGE,
    CLOSE_BASELINE_SAVED, CLOSE_BASELINE_FAILED,
]


def all_spoken(sentence: str) -> list[str]:
    """Every non-empty spoken line for a call with this sentence (for warming the TTS cache)."""
    lines = [*ALL_LINES, sentence_prompt(sentence)]
    return [line.say for line in lines if line.say]
