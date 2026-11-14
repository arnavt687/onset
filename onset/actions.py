"""The safety gate for real outbound actions (iMessages, calls, invitations).

Every action that reaches a real person outside the call goes through `gate()` first:

    live = actions.gate("imessage", target=phone)      # -> Gate(live, reason)
    res = await chert_api.send_imessage(phone, text, dry_run=not live.live)
    actions.record(sink, "imessage", live, target=phone, status=res["status"], detail=...)

Real actions run only when ALL of these hold:
  * ONSET_LIVE_ACTIONS=1 in the environment (or .env.local), set on purpose by the person
    running the line; the default is a dry run that logs exactly what would have happened;
  * a target is configured (a caregiver phone from the patient profile);
  * the action kind is not switched off in ONSET_LIVE_ACTIONS_DENY (comma list, e.g. "call").

Every action, dry or live, is written to the event stream as an `action` event (shown on the
dashboard) with the phone masked: kind, live (bool), status, target (masked), detail.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from onset.patients import mask

log = logging.getLogger("onset.actions")

KINDS = ("imessage", "call", "invite")
FLAG = "ONSET_LIVE_ACTIONS"


@dataclass(frozen=True)
class Gate:
    kind: str
    live: bool
    reason: str  # why it is (not) live, for the event and the log


def live_actions_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(FLAG) or "").strip() == "1"


def gate(kind: str, *, target: str | None, env: Mapping[str, str] | None = None) -> Gate:
    """May this action run for real? Never raises."""
    env = os.environ if env is None else env
    if kind not in KINDS:
        return Gate(kind, False, f"dry run: unknown action kind {kind!r}")
    if not live_actions_enabled(env):
        return Gate(kind, False, f"dry run: {FLAG} is not 1")
    if not target:
        return Gate(kind, False, "dry run: no caregiver phone in the patient profile")
    deny = {x.strip() for x in (env.get(f"{FLAG}_DENY") or "").split(",") if x.strip()}
    if kind in deny:
        return Gate(kind, False, f"dry run: {kind} is listed in {FLAG}_DENY")
    return Gate(kind, True, "live")


def record(sink: Any, kind: str, g: Gate, *, target: str | None, status: str, detail: str = "",
           **extra: Any) -> dict:
    """Log the action and emit it as an `action` event. Returns the event fields."""
    fields = {"kind": kind, "live": g.live, "status": status, "target": mask(target or ""),
              "detail": detail or g.reason, **extra}
    log.info("[onset] action %s (%s): %s %s", kind, "LIVE" if g.live else "dry run", status, fields["detail"])
    emit = getattr(sink, "emit", None) if sink is not None else None
    if callable(emit):
        try:
            emit("action", **fields)
        except Exception:  # noqa: BLE001 - logging an action never breaks the call
            log.exception("[onset] could not emit the action event")
    return fields
