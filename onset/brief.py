"""The caregiver briefing call: after a check-in found a change, Onset FaceTimes the caregiver.

Chert cannot add a second person to the patient's FaceTime call (one call per line, no add or
merge), so this is a second call, placed from the same line once the patient's call has ended
(`tools/caregiver_call.py`; `ONSET_CAREGIVER_CALL=after`). The caregiver answers and Onset tells
them, out loud and on screen, what the check-in found and what to do: call the person, and call
911 if they cannot be reached. It never says what is wrong with them, only what it measured.

This module is the words and the picture; agent.py speaks and shows them (`brief_entry`).
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
from PIL import Image, ImageDraw

from onset import overlay
from onset.script import Line

ROLE = "caregiver_brief"          # job metadata: {"onset": ROLE, ...}
ROOM_PREFIX = "onset-brief-"
RING_TIMEOUT_S = 90.0             # how long the agent waits for the caregiver to answer
MAX_REASONS = 3
WAITING = Line(say="", caption="Onset is calling…")
GOODBYE = Line(say="This was Onset. Goodbye.", caption="Call them now. If you can't reach them, call 911 now.")


@dataclass(frozen=True)
class BriefInfo:
    patient: str = ""              # first name
    caregiver: str = ""            # first name
    outcome: str = "escalate"      # "escalate" | "unclear"
    reasons: tuple[str, ...] = ()  # what Onset measured, as short sentences
    when: str = ""                 # e.g. "Sat 14 Nov at 9:02 am"
    run_id: str = ""
    group: bool = False            # a group FaceTime call: the person AND the caregiver are both on it

    @classmethod
    def from_meta(cls, meta: Mapping[str, Any] | None) -> "BriefInfo":
        m = meta or {}

        def text(key: str, limit: int) -> str:
            return re.sub(r"\s+", " ", str(m.get(key) or "")).strip()[:limit]

        reasons = m.get("reasons") if isinstance(m.get("reasons"), (list, tuple)) else []
        clean = tuple(r for r in (re.sub(r"\s+", " ", str(x)).strip()[:160] for x in reasons) if r)[:MAX_REASONS]
        outcome = text("outcome", 20)
        return cls(patient=text("patient", 40), caregiver=text("caregiver", 40),
                   outcome=outcome if outcome in ("escalate", "unclear") else "escalate",
                   reasons=clean, when=text("when", 60), run_id=text("run_id", 64), group=m.get("group") is True)

    def to_meta(self) -> dict:
        return {"onset": ROLE, "patient": self.patient, "caregiver": self.caregiver, "outcome": self.outcome,
                "reasons": list(self.reasons), "when": self.when, "run_id": self.run_id, "group": self.group}

    def to_json(self) -> str:
        return json.dumps(self.to_meta())


def is_brief(meta: Mapping[str, Any] | None) -> bool:
    return isinstance(meta, Mapping) and meta.get("onset") == ROLE


def _who(info: BriefInfo) -> str:
    return info.patient or "the person you care for"


def _poss(name: str) -> str:
    return name + ("'" if name.endswith(("s", "S")) else "'s")


def lines(info: BriefInfo) -> list[Line]:
    """What Onset says to the caregiver, in order. Each line is also the caption while it is said."""
    who = _who(info)
    if info.outcome == "unclear":
        found_say = f"{_poss(who)} check-in just now could not be completed clearly."
        found_cap = f"{_poss(who)} check-in could not be completed clearly."
    else:
        found_say = f"{_poss(who)} check-in just now was different from their usual check-in."
        found_cap = f"{_poss(who)} check-in was different from their usual."
    if info.group:  # both of them are listening
        both = f"{info.patient} and {info.caregiver}, you" if info.patient and info.caregiver else "You"
        first = Line(say=f"Hello. This is Onset. {both} are both on this call.", caption="This is Onset. You are both on this call.")
    else:
        hello = f"Hello{' ' + info.caregiver if info.caregiver else ''}. This is Onset, the check-in helper for {who}."
        first = Line(say=hello, caption=f"This is Onset, calling about {who}.")
    out = [first, Line(say=found_say, caption=found_cap)]
    out += [Line(say=r, caption=r) for r in info.reasons]
    out.append(action_line(info))
    return out


def action_line(info: BriefInfo) -> Line:
    who = _who(info)
    if info.group:
        to = f"{info.caregiver}, please" if info.caregiver else "Please"
        return Line(say=f"{to} check on {who} now. If anything seems wrong, call 9 1 1.",
                    caption=f"Check on {who} now. If anything seems wrong, call 911 now.")
    return Line(say=f"Please call {who} now. If you cannot reach them, call 9 1 1.",
                caption=f"Call {who} now. If you can't reach them, call 911 now.")


GROUP_GOODBYE = Line(say="I am leaving the call now so the two of you can talk. This was Onset.",
                     caption="Onset is leaving. Stay on and talk. If anything seems wrong, call 911 now.")


def script(info: BriefInfo) -> list[Line]:
    """Everything Onset says on the call, in order: the briefing, the action once more, goodbye."""
    return [*lines(info), action_line(info), GROUP_GOODBYE if info.group else GOODBYE]


def spoken(info: BriefInfo) -> list[str]:
    return [ln.say for ln in script(info) if ln.say]


@functools.lru_cache(maxsize=8)
def _card(info: BriefInfo, w: int, h: int) -> np.ndarray:
    """The still part of the picture: whose check-in, when, and what Onset measured."""
    k = overlay.scale(w, h)
    top, bottom = overlay.safe_box(w, h)
    img = Image.fromarray(overlay.blank(w, h), "RGBA")
    d = ImageDraw.Draw(img)
    pad = max(18, round(34 * k))
    width = w - 2 * pad
    y = top + max(14, round(26 * k))

    label = overlay.pill_font(max(13, round(17 * k)))
    d.text((pad, y), "ONSET  ·  CHECK-IN ALERT", font=label, fill=(*overlay.GLOW, 255))
    y += round(label.size * 2.1)

    title_size = max(26, round(46 * k))
    for line in overlay._wrap(f"{_poss(_who(info))} check-in", title_size, width)[:2]:
        d.text((pad, y), line, font=overlay.font(title_size), fill=(*overlay.TEXT, 255))
        y += round(title_size * 1.12)
    sub = overlay.pill_font(max(15, round(22 * k)))
    what = "could not be completed clearly" if info.outcome == "unclear" else "different from their usual"
    d.text((pad, y + round(4 * k)), what + (f"  ·  {info.when}" if info.when else ""), font=sub, fill=(154, 171, 186, 255))
    y += round(sub.size * 2.4)

    body_size = max(17, round(25 * k))
    body = overlay.pill_font(body_size)
    for reason in info.reasons:
        wrapped = overlay._wrap(reason, body_size, width - round(30 * k))[:3]
        if y + len(wrapped) * body_size * 1.3 > bottom - round(150 * k):
            break  # leave room for the caption panel
        r = max(4, round(5 * k))
        cy = y + body_size * 0.62
        d.ellipse([pad + r, cy - r, pad + 3 * r, cy + r], fill=(*overlay.ALERT, 255))
        for line in wrapped:
            d.text((pad + round(30 * k), y), line, font=body, fill=(*overlay.TEXT, 255))
            y += round(body_size * 1.3)
        y += round(body_size * 0.45)
    return np.asarray(img).copy()


def render(info: BriefInfo, w: int, h: int, caption: str, t: float) -> np.ndarray:
    """One RGBA frame of the briefing call: the card, with the current line as its caption."""
    img = _card(info, w, h).copy()
    overlay.draw_caption(img, caption, "bottom", t)
    return img
