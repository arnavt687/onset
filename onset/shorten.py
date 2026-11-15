"""Make the caregiver's join link short enough to read out or type (ONSET_LINK_SHORTENER).

The join link is about 450 characters: the caregiver page plus the watch key. A public link
shortener turns it into about 20 (`https://is.gd/AbC123`), which is easier to tap, to paste and
to show on a slide.

The cost, and why this is OFF unless asked for: the shortener is someone else's service, and it
stores the whole link, watch key included. That key lets anyone who has it watch and hear this
one call until it expires (ONSET_CAREGIVER_LINK_TTL_S, 15 minutes by default). It cannot talk:
the talk key never travels in the link (onset/caregiver.py). Fine for a demo with your own
phones; think before using it with a real patient.

Rules this module keeps:
  * it never raises and never takes long: any failure, an odd reply or a slow service gives
    None, and the caller sends the long link as before;
  * a short link is used only after the service has been asked what it redirects to and the
    answer is the long link exactly, `#fragment` included (some services drop or rewrite it);
  * neither link is ever logged or put in an event.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

log = logging.getLogger("onset.shorten")

TIMEOUT_S = 3.0
MAX_SHORT = 60


@dataclass(frozen=True)
class Service:
    name: str  # as shown to people: "is.gd"
    create: str  # GET this with ?url=<long link> (+ params); the body is the short link
    params: tuple[tuple[str, str], ...]
    short: str  # what a short link from this service must look like


SERVICES: dict[str, Service] = {
    "isgd": Service("is.gd", "https://is.gd/create.php", (("format", "simple"),), r"https://is\.gd/[A-Za-z0-9_]{4,20}"),
    "dagd": Service("da.gd", "https://da.gd/s", (), r"https://da\.gd/[A-Za-z0-9_\-]{3,20}"),
}
# Tried and left out: tinyurl.com stored our link with %23 in place of #, so it led to a missing page.
ALIASES = {"is.gd": "isgd", "da.gd": "dagd", "1": "isgd", "on": "isgd", "yes": "isgd", "true": "isgd"}
OFF = ("", "0", "off", "no", "false", "none")


def service_from(value: str | None) -> str:
    """ONSET_LINK_SHORTENER -> "off" or a key of SERVICES. Anything unknown is "off", with a warning."""
    v = (value or "").strip().lower()
    if v in OFF:
        return "off"
    v = ALIASES.get(v, v)
    if v not in SERVICES:
        log.warning("[onset] ONSET_LINK_SHORTENER=%r is not off|%s; the long link is used", value, "|".join(SERVICES))
        return "off"
    return v


async def _ask(link: str, svc: Service, session) -> str | None:
    async with session.get(svc.create, params=[*svc.params, ("url", link)]) as r:
        body = (await r.text()).strip()
        if r.status != 200 or len(body) > MAX_SHORT or not re.fullmatch(svc.short, body):
            log.warning("[onset] %s did not give a short link (HTTP %s, %d characters back)", svc.name, r.status, len(body))
            return None
    async with session.get(body, allow_redirects=False) as r:  # where does it really go?
        if r.status not in (301, 302, 303, 307, 308) or r.headers.get("Location") != link:
            log.warning("[onset] %s made a short link that does not lead back to ours exactly (HTTP %s): not using it",
                        svc.name, r.status)
            return None
    return body


async def shorten(link: str, service: str, *, timeout_s: float = TIMEOUT_S, session=None) -> str | None:
    """The short link, or None (use the long one). Never raises; gives up after `timeout_s`."""
    svc = SERVICES.get(service)
    if svc is None or not link.startswith("https://"):
        return None
    import aiohttp

    own = session is None
    try:
        if own:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s))
        short = await asyncio.wait_for(_ask(link, svc, session), timeout_s)
    except asyncio.TimeoutError:
        log.warning("[onset] %s took longer than %.0f s: the long link is used", svc.name, timeout_s)
        return None
    except Exception as e:  # noqa: BLE001 - a link shortener must never cost the caregiver their link
        log.warning("[onset] %s failed (%s): the long link is used", svc.name, type(e).__name__)
        return None
    finally:
        if own and session is not None:
            await session.close()
    if short:
        log.info("[onset] caregiver link shortened by %s: %d -> %d characters", svc.name, len(link), len(short))
    return short
