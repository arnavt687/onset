"""Escalation: bring the caregiver into the live call, and text them what was noticed.

What Chert allows (docs/escalation.md has the sources):
  * nobody can be added to the FaceTime call itself, and the line carries one call at a time,
    so a second FaceTime call to the caregiver cannot happen while the caller is on;
  * in our LiveKit room only ONE participant may publish audio (a second publisher makes the
    Mac end the call: `several_audio`), and "silent auxiliary participants may be present".

So on ESCALATE / UNCLEAR (ONSET_CAREGIVER_JOIN=link, the default):
  1. Onset mints two short-lived LiveKit tokens for the caregiver:
       watch  - the call's room, subscribe only (sees the caller and Onset's screen, hears them);
       talk   - a side room `<room>-cg`, publish only (their microphone and camera).
  2. It iMessages the caregiver the summary plus a private link to the caregiver page
     (web/caregiver/, ONSET_CAREGIVER_PAGE; tokens in the #fragment). Without a page the
     link is LiveKit Meet, watch-only.
  3. When the caregiver talks, Onset relays the side room into its own single audio track and
     draws their camera into its video (picture in picture), so the caller hears and sees
     them through Onset (agent.py, `SideRoomRelay`).

Every message goes through the safety gate (onset.actions): a dry run unless
ONSET_LIVE_ACTIONS=1 and the patient has a caregiver phone. With ONSET_CAREGIVER_JOIN=off only
the summary is texted.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote

from onset import actions

log = logging.getLogger("onset.caregiver")

MEET_URL = "https://meet.livekit.io/custom?liveKitUrl={url}&token={token}"
WATCH_PREFIX = "caregiver-"
TALK_PREFIX = "caregiver-talk-"
RELAY_PREFIX = "onset-relay-"
PLACEHOLDER_PHONE = "+15555550100"  # dry runs with no caregiver phone set
MAX_TEXT = 3900  # Chert allows 4000 UTF-16 units; leave room

SendFn = Callable[[str, str, bool], Awaitable[dict]]


@dataclass(frozen=True)
class EscalationConfig:
    join: str = "link"  # "link" | "off"
    talk: bool = True
    page: str = ""  # the caregiver page URL ("" -> LiveKit Meet, watch-only)
    ttl_s: int = 900
    # "after": once the person's call has ended, Onset FaceTimes the caregiver and tells them what
    # it found (onset/brief.py, tools/caregiver_call.py). "group": Onset hangs up and rings the
    # person and the caregiver together in one group FaceTime call, says what it found, and leaves
    # them talking (Chert's way to have two people on a call). "off" (default): link and text only.
    call: str = "off"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EscalationConfig":
        env = os.environ if env is None else env
        join = (env.get("ONSET_CAREGIVER_JOIN") or "link").strip().lower()
        if join not in ("link", "off"):
            log.warning("[onset] ONSET_CAREGIVER_JOIN=%r is not link|off; using link", join)
            join = "link"
        try:
            ttl = int(env.get("ONSET_CAREGIVER_LINK_TTL_S") or 900)
        except ValueError:
            ttl = 900
        call = (env.get("ONSET_CAREGIVER_CALL") or "off").strip().lower()
        if call not in ("off", "after", "group"):
            log.warning("[onset] ONSET_CAREGIVER_CALL=%r is not off|after|group; using off", call)
            call = "off"
        return cls(join=join, talk=(env.get("ONSET_CAREGIVER_TALK") or "1").strip() != "0",
                   page=(env.get("ONSET_CAREGIVER_PAGE") or "").strip(), ttl_s=max(120, min(ttl, 7200)), call=call)


@dataclass(frozen=True)
class Invite:
    link: str = field(repr=False)  # contains tokens: never log it or put it in an event
    room: str
    side_room: str | None
    watch_identity: str
    talk_identity: str | None
    expires_at: int
    kind: str  # "page" | "meet"
    # The side room's key. It is NOT in the link: agent.py hands it to the caregiver inside the
    # call once they have joined, so a link cut short by a messaging app cannot lose it.
    talk_token: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return (f"Invite(kind={self.kind!r}, room={self.room!r}, side_room={self.side_room!r}, "
                f"expires_at={self.expires_at}, link='***')")


def side_room_name(room: str) -> str:
    return f"{room}-cg"


def _token(api_key: str, api_secret: str, identity: str, name: str, ttl_s: int, grants) -> str:
    from livekit import api

    return (api.AccessToken(api_key, api_secret).with_identity(identity).with_name(name)
            .with_ttl(timedelta(seconds=ttl_s)).with_grants(grants).to_jwt())


def watch_token(room: str, identity: str, *, api_key: str, api_secret: str, ttl_s: int = 900) -> str:
    """Subscribe-only: the caregiver sees and hears the call but publishes nothing into it
    (a second audio publisher would make Chert end the call)."""
    from livekit import api

    grants = api.VideoGrants(room_join=True, room=room, can_subscribe=True, can_publish=False,
                             can_publish_data=False, can_update_own_metadata=False)
    return _token(api_key, api_secret, identity, "Caregiver", ttl_s, grants)


def talk_token(side_room: str, identity: str, *, api_key: str, api_secret: str, ttl_s: int = 900) -> str:
    """Publish-only, in the side room Onset relays from."""
    from livekit import api

    # can_publish_data: the page reports its own state there ("microphone blocked"), so the log
    # says why talking never started (issue #30). Onset's relay is the only listener.
    grants = api.VideoGrants(room_join=True, room=side_room, can_subscribe=False, can_publish=True,
                             can_publish_data=True, can_publish_sources=["microphone", "camera"])
    return _token(api_key, api_secret, identity, "Caregiver", ttl_s, grants)


def short_link(base: str, livekit_url: str, watch: str) -> str | None:
    """`<page>#c/<host>/<length of the key>/<key>`: about 450 characters, no `&`, `=` or `%`.

    The old link (`#u=..&w=..&t=..&n=..&e=..`) was about 1,000 characters with the talk key last.
    Sent through Messages it arrived cut short: the caregiver could watch but not talk, and the
    page could not tell. Here the only key is the watch key, its length comes first so a cut link
    is recognised and refused, and the talk key travels inside the call. None for a LiveKit URL
    this form cannot carry (a path, a query): the caller falls back to the long form."""
    m = re.fullmatch(r"(wss?)://([A-Za-z0-9.\-]+(?::\d+)?)/?", livekit_url.strip())
    if not m:
        return None
    host = m.group(2).lower() if m.group(1) == "wss" else f"ws:{m.group(2).lower()}"
    return f"{base}#c/{host}/{len(watch)}/{watch}"


def relay_token(side_room: str, identity: str, *, api_key: str, api_secret: str, ttl_s: int = 3600) -> str:
    """Onset's own subscribe-only token for the side room."""
    from livekit import api

    grants = api.VideoGrants(room_join=True, room=side_room, can_subscribe=True, can_publish=False,
                             can_publish_data=False, hidden=True)
    return _token(api_key, api_secret, identity, "Onset relay", ttl_s, grants)


def make_invite(room: str, run_id: str, *, livekit_url: str, api_key: str, api_secret: str,
                cfg: EscalationConfig, patient_first: str = "", now: float | None = None) -> Invite:
    """The caregiver's tokens and link. Offline: it only signs tokens."""
    if not (livekit_url and api_key and api_secret):
        raise ValueError("LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are needed for the caregiver link")
    now = time.time() if now is None else now
    tag = "".join(c for c in run_id if c.isalnum() or c in "-_")[:36] or "call"
    watch_id = f"{WATCH_PREFIX}{tag}"
    w = watch_token(room, watch_id, api_key=api_key, api_secret=api_secret, ttl_s=cfg.ttl_s)
    expires = int(now + cfg.ttl_s)
    if cfg.page:
        side = side_room_name(room) if cfg.talk else None
        talk_id = f"{TALK_PREFIX}{tag}" if side else None
        talk = talk_token(side, talk_id, api_key=api_key, api_secret=api_secret, ttl_s=cfg.ttl_s) if side else None
        base = cfg.page.split("#", 1)[0]
        link = short_link(base, livekit_url, w)
        if link is None:  # an unusual LiveKit URL: the long form, with every key in it
            parts = [f"u={quote(livekit_url, safe='')}", f"w={w}"]
            if talk:
                parts.append(f"t={talk}")
            if patient_first:
                parts.append(f"n={quote(patient_first, safe='')}")
            parts.append(f"e={expires}")
            link = f"{base}#{'&'.join(parts)}"
        return Invite(link, room, side, watch_id, talk_id, expires, "page", talk)
    link = MEET_URL.format(url=quote(livekit_url, safe=""), token=w)
    return Invite(link, room, None, watch_id, None, expires, "meet")


def first_name(name: str) -> str:
    n = " ".join((name or "").split())
    return n.split(" ")[0][:30] if n and n.lower() not in ("demo patient", "the person") else ""


def invite_text(summary: str, link: str | None, *, caregiver_name: str = "", patient_name: str = "",
                talk: bool = False) -> str:
    """The iMessage: summary first (it is what matters), then the link."""
    hello = f"{caregiver_name.strip().split(' ')[0]}, j" if caregiver_name.strip() else "J"
    who = first_name(patient_name) or "them"
    parts = [summary.strip()]
    if link:
        how = "see and talk to them" if talk else "see them"
        parts.append(f"{hello}oin the call now to {how}: {link}\n"
                     f"(The link works for a short time only. Onset has told {who} to call 911.)")
    text = "\n\n".join(parts)
    return text if len(text) <= MAX_TEXT else summary.strip()[:MAX_TEXT]


async def _default_send(recipient: str, text: str, dry_run: bool) -> dict:
    from onset.chert_api import send_imessage

    return await send_imessage(recipient, text, dry_run=dry_run)


def _status(res: Mapping[str, Any]) -> str:
    st = res.get("status", "failed")
    if st == "dry_run":
        return "dry_run"
    if res.get("accepted"):
        return "sent"
    return "unknown" if st == "unknown" else "failed"


async def notify_caregiver(*, sink: Any, summary: str, caregiver_phone: str, caregiver_name: str = "",
                           patient_name: str = "", invite: Invite | None = None, talk: bool = False,
                           send: SendFn | None = None, force_dry: bool = False,
                           env: Mapping[str, str] | None = None) -> dict:
    """Text the caregiver (summary, plus the join link when there is one). Never raises.

    Returns {"status": "dry_run"|"sent"|"failed"|"unknown", "detail": str, "live": bool}.
    An `action` event records it either way.
    """
    kind = "invite" if invite is not None else "imessage"
    g = actions.gate(kind, target=caregiver_phone, env=env)
    if force_dry and g.live:
        g = actions.Gate(kind, False, "dry run: this run never sends")
    text = invite_text(summary, invite.link if invite else None, caregiver_name=caregiver_name,
                       patient_name=patient_name, talk=talk and invite is not None and invite.side_room is not None)
    send = send or _default_send
    try:
        res = await send(caregiver_phone or PLACEHOLDER_PHONE, text, not g.live)
        status = _status(res)
        detail = res.get("detail") or res.get("error") or ""
        if status == "unknown":
            detail = (detail + " " if detail else "") + f"(idempotency key {res.get('idempotency_key', '?')}: never resend with a new key)"
    except Exception as e:  # noqa: BLE001
        status, detail = "failed", type(e).__name__
    if not g.live:
        detail = g.reason
    actions.record(sink, kind, g, target=caregiver_phone, status=status, detail=detail, chars=len(text),
                   link=invite.kind if invite else None)
    return {"status": status, "detail": detail, "live": g.live}
