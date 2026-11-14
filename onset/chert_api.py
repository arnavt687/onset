"""Pieces 6 and 8: Chert's HTTP API for the caregiver iMessage and outbound calls.

Owner: the `chert-api` session. See ROADMAP.md, pieces 6 and 8, and
`docs/chert-messaging.md` / `docs/outbound.md` for what Chert allows on each plan.

API base: https://app.trychert.com/api/v1. The project API key (`ftk_...`) is sent only to
that host: every URL is built from one constant and checked before a request, and redirects
are never followed, so the key cannot be carried somewhere else.

Nothing here raises into the call. Every public coroutine returns a plain dict with at least
`status`, `accepted` and `error`; read `status`, never assume success. Both functions default
to `dry_run=True`, which validates the input and returns the exact request without touching
the network or needing a key.

Configuration comes from the environment (or `.env.local`, read without changing
`os.environ`):

    CHERT_API_KEY     project API key from https://app.trychert.com/developer (facetime:read/write)
    CHERT_PROJECT_ID  project UUID
    CHERT_LINE_ID     optional; otherwise looked up once with GET /api/v1/lines
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote, urlsplit

log = logging.getLogger("onset.chert_api")

API_HOST = "app.trychert.com"
API_BASE = f"https://{API_HOST}/api/v1"
MAX_TEXT_UTF16 = 4000  # Chert's limit for one shared message
TIMEOUT_S = 10.0

# States Chert may report for a shared message. Anything else is treated as not confirmed.
ACCEPTED_STATES = {"queued", "accepted", "pending", "sending", "sent", "delivered", "read"}
FAILED_STATES = {"failed", "rejected", "undeliverable", "error", "cancelled", "canceled", "expired"}

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ENV_FILES = (Path(__file__).resolve().parents[1] / ".env.local", Path.home() / "onset" / ".env.local")


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class ChertConfig:
    api_key: str = field(repr=False)
    project_id: str
    line_id: str | None = None

    def __repr__(self) -> str:  # never show the key, even in a traceback
        return f"ChertConfig(api_key='***', project_id={self.project_id!r}, line_id={self.line_id!r})"


class ConfigError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def load_config(env: Mapping[str, str] | None = None) -> ChertConfig:
    """Read the Chert settings. Raises ConfigError (caught by the public functions)."""
    values: dict[str, str] = {}
    if env is None:
        try:
            from dotenv import dotenv_values

            for path in reversed(_ENV_FILES):  # the worktree's own file wins over ~/onset's
                if path.is_file():
                    values.update({k: v for k, v in dotenv_values(path).items() if v})
        except Exception:  # a missing or unreadable file only means "use the environment"
            pass
        values.update({k: v for k, v in os.environ.items() if k.startswith("CHERT_") and v})
    else:
        values.update(env)

    key = (values.get("CHERT_API_KEY") or "").strip()
    project = (values.get("CHERT_PROJECT_ID") or "").strip()
    line = (values.get("CHERT_LINE_ID") or "").strip() or None
    if not key:
        raise ConfigError("missing_api_key", "CHERT_API_KEY is not set (create one at https://app.trychert.com/developer)")
    if not key.startswith("ftk_"):
        raise ConfigError("invalid_api_key", "CHERT_API_KEY must be a project API key (it starts with ftk_)")
    if not _UUID.match(project):
        raise ConfigError("missing_project_id", "CHERT_PROJECT_ID is not set or is not a UUID")
    if line is not None and not _UUID.match(line):
        raise ConfigError("invalid_line_id", "CHERT_LINE_ID is not a UUID")
    return ChertConfig(api_key=key, project_id=project, line_id=line)


# --------------------------------------------------------------------------- transport


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any  # parsed JSON, or None
    headers: Mapping[str, str] = field(default_factory=dict)


class TransportError(Exception):
    """The request may or may not have reached Chert (timeout, reset, DNS...)."""


# (method, url, headers, json_body) -> HttpResponse. Tests pass a fake; nothing else does.
Transport = Callable[[str, str, Mapping[str, str], Any], Awaitable[HttpResponse]]


async def _aiohttp_transport(method: str, url: str, headers: Mapping[str, str], body: Any) -> HttpResponse:
    import aiohttp

    _check_url(url)
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, headers=dict(headers), json=body, allow_redirects=False) as r:
                try:
                    parsed = await r.json(content_type=None)
                except Exception:
                    parsed = None
                return HttpResponse(status=r.status, body=parsed, headers=dict(r.headers))
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise TransportError(type(e).__name__) from None


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != API_HOST or parts.port not in (None, 443) or parts.username:
        raise ValueError("refusing to send a Chert key to anything but https://app.trychert.com")


def _url(path: str) -> str:
    url = API_BASE + path
    _check_url(url)
    return url


def _headers(cfg: ChertConfig, idempotency_key: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {cfg.api_key}",
        "X-Chert-Project-Id": cfg.project_id,
        "Accept": "application/json",
    }
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
        h["Content-Type"] = "application/json"
    return h


def _redacted(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: ("Bearer ***" if k.lower() == "authorization" else v) for k, v in headers.items()}


def new_idempotency_key(kind: str = "msg") -> str:
    """A fresh key (8-200 characters). Reuse a key only to retry the *same* request."""
    return f"onset-{kind}-{uuid.uuid4()}"


def _valid_key(key: str) -> bool:
    return 8 <= len(key) <= 200 and key.isascii() and key.isprintable()


# --------------------------------------------------------------------------- helpers


def _result(status: str, *, accepted: bool = False, error: str | None = None, detail: str = "", **extra: Any) -> dict:
    out = {"status": status, "accepted": accepted, "error": error, "detail": detail}
    out.update(extra)
    return out


def _data(body: Any) -> Any:
    """Chert wraps records as {"data": ..., "request_id": ...}; older routes return them bare."""
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _items(body: Any) -> list:
    body = _data(body)
    if isinstance(body, dict):
        for k in ("items", "messages", "lines", "data"):
            if isinstance(body.get(k), list):
                return body[k]
        return []
    return body if isinstance(body, list) else []


def _request_id(body: Any) -> str | None:
    rid = body.get("request_id") if isinstance(body, dict) else None
    return rid if isinstance(rid, str) else None


def _safe_text(value: Any, limit: int = 200) -> str:
    """Printable ASCII only, short: server text goes into logs and the result dict."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isascii() and c.isprintable())[:limit]


def _api_error(resp: HttpResponse) -> tuple[str, str]:
    err = resp.body.get("error") if isinstance(resp.body, dict) else None
    code = message = None
    if isinstance(err, dict):
        code, message = err.get("code"), err.get("message")
    elif isinstance(err, str):
        code = err
    if isinstance(resp.body, dict) and not code:
        code = resp.body.get("code")
    code = _safe_text(code, 64) if isinstance(code, str) and re.fullmatch(r"[a-z0-9_.-]{1,64}", code) else None
    if code is None:
        code = {400: "invalid_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
                409: "idempotency_conflict", 422: "invalid_request", 429: "rate_limited"}.get(resp.status, f"http_{resp.status}")
    return code, _safe_text(message)


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


_line_cache: dict[str, str] = {}


async def get_line_id(cfg: ChertConfig, transport: Transport) -> str:
    """The project's line: CHERT_LINE_ID, else the only line GET /api/v1/lines returns."""
    if cfg.line_id:
        return cfg.line_id
    if cfg.project_id in _line_cache:
        return _line_cache[cfg.project_id]
    resp = await transport("GET", _url("/lines?limit=100"), _headers(cfg), None)
    if resp.status != 200:
        code, msg = _api_error(resp)
        raise ConfigError(f"line_lookup_{code}", msg or f"GET /api/v1/lines answered HTTP {resp.status}")
    lines = [ln for ln in _items(resp.body) if isinstance(ln, dict) and isinstance(ln.get("id"), str)]
    if not lines:
        raise ConfigError("no_line", "the project has no line yet (run chert onboard)")
    if len(lines) > 1:
        raise ConfigError("multiple_lines", "the project has several lines; set CHERT_LINE_ID to the one to use")
    _line_cache[cfg.project_id] = lines[0]["id"]
    return lines[0]["id"]


def _message_state(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    for k in ("state", "send_state", "delivery_state", "status"):
        v = record.get(k)
        if isinstance(v, str) and v:
            return v.lower()
    return None


def _classify(state: str | None) -> str:
    if state in ACCEPTED_STATES:
        return "accepted"
    if state in FAILED_STATES:
        return "failed"
    return "unknown"


# --------------------------------------------------------------------------- piece 6: iMessage


async def send_imessage(
    recipient: str,
    text: str,
    *,
    dry_run: bool = True,
    idempotency_key: str | None = None,
    config: ChertConfig | None = None,
    transport: Transport | None = None,
    reread_attempts: int = 3,
    reread_delay_s: float = 1.0,
) -> dict:
    """Send `text` to `recipient` over the project's Shared line. Dry run returns the request instead.

    `recipient` is an E.164 phone (+15551234567) that has joined the project's line; see
    docs/chert-messaging.md for who can be texted on the trial and on Shared.

    Pass `idempotency_key` only to retry the same message (for example after an `unknown`
    result); by default every call gets a fresh one. The result's `status` is one of:

    - `dry_run`: nothing was sent; `request` holds what would have been sent (key redacted).
    - `queued` / `sent` / `delivered` (`accepted` is True): Chert took the message. That is
      not proof the caregiver has read it.
    - `failed`: Chert reported the send failed.
    - `unknown`: Chert kept the message but could not confirm the send, or the answer was
      lost. Do NOT send it again with a new key; call `get_message_state` later or retry
      with the same `idempotency_key`.
    - `error`: rejected before or by Chert (`error` holds the code, e.g. `missing_api_key`,
      `invalid_recipient`, `unauthorized`, `rate_limited`, or Chert's own code).
    """
    key = idempotency_key or new_idempotency_key("msg")
    try:
        recipient = (recipient or "").strip()
        if not _E164.match(recipient):
            return _result("error", error="invalid_recipient", detail="recipient must be an E.164 phone number such as +15551234567", idempotency_key=key)
        if not isinstance(text, str) or not text.strip():
            return _result("error", error="empty_text", detail="nothing to send", idempotency_key=key)
        if _utf16_len(text) > MAX_TEXT_UTF16:
            return _result("error", error="text_too_long", detail=f"Chert allows {MAX_TEXT_UTF16} characters", idempotency_key=key)
        if not _valid_key(key):
            return _result("error", error="invalid_idempotency_key", detail="8 to 200 printable ASCII characters", idempotency_key=key)

        if dry_run:
            try:
                cfg = config or load_config()
                project, line = cfg.project_id, cfg.line_id
            except ConfigError:
                project, line = None, None
            headers = {"Authorization": "Bearer ***", "X-Chert-Project-Id": project or "<CHERT_PROJECT_ID>",
                       "Accept": "application/json", "Idempotency-Key": key, "Content-Type": "application/json"}
            request = {"method": "POST", "url": _url("/shared-messages"), "headers": headers,
                       "json": {"line_id": line or "<from GET /api/v1/lines>", "recipient": recipient, "text": text}}
            log.info("chert dry run: iMessage of %d characters to %s", len(text), _mask(recipient))
            return _result("dry_run", detail="dry run: nothing was sent", idempotency_key=key, request=request)

        cfg = config or load_config()
        transport = transport or _aiohttp_transport
        line_id = await get_line_id(cfg, transport)
        return await _post_message(cfg, transport, line_id, recipient, text, key, reread_attempts, reread_delay_s)
    except ConfigError as e:
        return _result("error", error=e.code, detail=e.detail, idempotency_key=key)
    except TransportError as e:
        # Only reachable from the line lookup, before anything was posted.
        return _result("error", error="network_error", detail=f"could not reach Chert ({e})", idempotency_key=key)
    except Exception as e:  # never raise into the call
        log.exception("send_imessage failed")
        return _result("error", error="internal_error", detail=type(e).__name__, idempotency_key=key)


async def _post_message(cfg, transport, line_id, recipient, text, key, reread_attempts, reread_delay_s) -> dict:
    body = {"line_id": line_id, "recipient": recipient, "text": text}
    url = _url("/shared-messages")
    resp: HttpResponse | None = None
    lost = None
    for attempt in range(2):  # a lost answer is retried once, with the SAME key
        try:
            resp = await transport("POST", url, _headers(cfg, key), body)
        except TransportError as e:
            lost, resp = str(e), None
            continue
        if resp.status >= 500:
            lost = f"HTTP {resp.status}"
            continue
        break

    if resp is None or resp.status >= 500:
        log.warning("chert: iMessage send to %s unconfirmed (%s); key kept for reconcile", _mask(recipient), lost)
        return _result("unknown", error="send_unconfirmed", idempotency_key=key, line_id=line_id,
                       detail=f"Chert's answer was lost ({lost}); retry with the same idempotency_key, never a new one")

    rid = _request_id(resp.body)
    if resp.status not in (200, 201, 202):
        code, msg = _api_error(resp)
        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        detail = msg or f"Chert answered HTTP {resp.status}"
        if retry_after:
            detail += f" (retry after {_safe_text(retry_after, 20)} s)"
        log.warning("chert: iMessage refused: %s (HTTP %s, request %s)", code, resp.status, rid)
        return _result("error", error=code, detail=detail, idempotency_key=key, http_status=resp.status, request_id=rid, line_id=line_id)

    record = _data(resp.body)
    message_id = record.get("id") if isinstance(record, dict) and isinstance(record.get("id"), str) else None
    state = _message_state(record)
    for _ in range(max(0, reread_attempts)):
        if _classify(state) != "unknown":
            break
        await asyncio.sleep(reread_delay_s)
        reread = await get_message_state(message_id, idempotency_key=key, config=cfg, transport=transport)
        if reread.get("state"):
            state = reread["state"]
    return _message_result(state, message_id, key, rid, line_id)


def _message_result(state: str | None, message_id: str | None, key: str, rid: str | None, line_id: str | None) -> dict:
    kind = _classify(state)
    extra = dict(idempotency_key=key, message_id=message_id, state=state, request_id=rid, line_id=line_id)
    if kind == "accepted":
        status = state if state in ("queued", "sent", "delivered") else ("delivered" if state == "read" else "queued")
        return _result(status, accepted=True, detail="Chert accepted the message", **extra)
    if kind == "failed":
        return _result("failed", error="send_failed", detail=f"Chert reports the send {state}", **extra)
    return _result("unknown", error="send_state_unknown", **extra,
                   detail="Chert kept the message but has not confirmed the send; re-read it, never resend with a new key")


async def get_message_state(
    message_id: str | None,
    *,
    idempotency_key: str | None = None,
    config: ChertConfig | None = None,
    transport: Transport | None = None,
) -> dict:
    """Re-read a sent message from GET /api/v1/shared-messages (the newest 50). Never sends anything."""
    try:
        cfg = config or load_config()
        transport = transport or _aiohttp_transport
        resp = await transport("GET", _url("/shared-messages"), _headers(cfg), None)
        if resp.status != 200:
            code, msg = _api_error(resp)
            return _result("error", error=code, detail=msg, message_id=message_id, state=None)
        for item in _items(resp.body):
            if not isinstance(item, dict):
                continue
            if (message_id and item.get("id") == message_id) or (
                idempotency_key and idempotency_key in (item.get("idempotency_key"), item.get("client_reference_id"))
            ):
                state = _message_state(item)
                return _message_result(state, item.get("id") or message_id, idempotency_key or "", _request_id(resp.body), item.get("line_id"))
        return _result("unknown", error="not_found_in_recent", detail="not among the newest 50 messages", message_id=message_id, state=None)
    except ConfigError as e:
        return _result("error", error=e.code, detail=e.detail, message_id=message_id, state=None)
    except TransportError as e:
        return _result("unknown", error="network_error", detail=str(e), message_id=message_id, state=None)
    except Exception as e:
        log.exception("get_message_state failed")
        return _result("error", error="internal_error", detail=type(e).__name__, message_id=message_id, state=None)


def _mask(phone: str) -> str:
    return phone[:2] + "…" + phone[-2:] if len(phone) > 6 else "…"


# --------------------------------------------------------------------------- piece 8: outbound calls
#
# Contract from https://docs.trychert.com/facetime/outbound (see docs/outbound.md). Untested
# against a real line: no key exists yet and no real call may be placed without approval.


def mint_bridge_token(
    room: str,
    *,
    api_key: str,
    api_secret: str,
    identity: str | None = None,
    ttl_s: int = 1800,
) -> str:
    """A room-scoped LiveKit JWT for Chert's FaceTime bridge (join, publish, subscribe; <= 2 h).

    Offline: it only signs a token. The room and the agent dispatch are the caller's job.
    """
    from datetime import timedelta

    from livekit import api

    if not 120 <= ttl_s <= 7200:
        raise ValueError("bridge token lifetime must be 120..7200 s (Chert needs > 90 s left at allocation)")
    identity = identity or f"chert-bridge-{room}"[:120]
    grants = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=False)
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_ttl(timedelta(seconds=ttl_s))
        .with_grants(grants)
        .to_jwt()
    )


def _jwt_claims(token: str) -> dict | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims if isinstance(claims, dict) else None
    except Exception:
        return None


def check_bridge_token(token: str, remote_participant_identity: str, *, now: float | None = None) -> tuple[str, str] | None:
    """Chert's documented token rules, checked locally. Returns (code, detail) or None if it passes."""
    claims = _jwt_claims(token)
    if claims is None:
        return "invalid_token_grants", "participant_token is not a JWT"
    now = time.time() if now is None else now
    sub, nbf, exp = claims.get("sub"), claims.get("nbf"), claims.get("exp")
    video = claims.get("video") if isinstance(claims.get("video"), dict) else {}
    if not isinstance(sub, str) or not sub.strip():
        return "invalid_token_grants", "bridge token needs a nonempty sub (identity)"
    if sub == remote_participant_identity:
        return "invalid_participant_identity", "bridge identity must differ from the agent's identity"
    if not isinstance(nbf, int) or not isinstance(exp, int) or exp - nbf > 7200:
        return "invalid_token_grants", "bridge token needs integer nbf/exp with exp - nbf <= 7200 s"
    if nbf > now + 5 or exp - now <= 90:
        return "session_expired", "bridge token must be valid now with more than 90 s left; mint a fresh one"
    if not (video.get("roomJoin") and video.get("canPublish") and video.get("canSubscribe") and video.get("room")):
        return "invalid_token_grants", "bridge token needs roomJoin, canPublish, canSubscribe and one room"
    return None


def _check_livekit_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        return p.scheme == "wss" and bool(p.hostname) and not p.username and not p.password and not p.fragment and p.port in (None, 443)
    except ValueError:
        return False


async def place_check_in_call(
    destination: str,
    *,
    dry_run: bool = True,
    livekit_url: str | None = None,
    participant_token: str | None = None,
    remote_participant_identity: str | None = None,
    video: bool = True,
    when_busy: str = "fail",
    idempotency_key: str | None = None,
    config: ChertConfig | None = None,
    transport: Transport | None = None,
) -> dict:
    """Ask Chert to FaceTime `destination` and route it to this agent (piece 8, if Chert allows it).

    The caller first creates a LiveKit room, dispatches the agent into it (silent until
    `bridge_ready`), and mints a bridge token with `mint_bridge_token`. This sends
    `POST /api/v1/calls` with `mode: customer_managed`. Result `status`:

    - `dry_run`: the request that would be sent (token and key redacted).
    - `allocated` (HTTP 201, `accepted` True): Chert allocated the call; not dialed or answered
      yet. Watch `get_call(call_id)` for `bridge_ready` before greeting.
    - `queued` (HTTP 202, only with `when_busy="queue"`): waiting for the busy Shared line.
      When `GET /api/v1/calls/queue/{queue_id}` says `ready`, call again with the SAME
      `idempotency_key` and a fresh token within 20 s.
    - `unknown`: the answer was lost; retry with the same `idempotency_key`, never a new one.
    - `error`: refused (`error` holds the code).
    """
    key = idempotency_key or new_idempotency_key("call")
    try:
        destination = (destination or "").strip()
        if _E164.match(destination):
            dtype = "phone"
        elif _EMAIL.match(destination):
            dtype = "email"
        else:
            return _result("error", error="invalid_destination", detail="destination must be an E.164 phone or an Apple ID email", idempotency_key=key)
        if when_busy not in ("fail", "queue"):
            return _result("error", error="invalid_request", detail="when_busy is 'fail' or 'queue'", idempotency_key=key)
        if not _valid_key(key):
            return _result("error", error="invalid_idempotency_key", detail="8 to 200 printable ASCII characters", idempotency_key=key)
        livekit_url = livekit_url or os.environ.get("LIVEKIT_URL")
        if livekit_url and not _check_livekit_url(livekit_url):
            return _result("error", error="invalid_livekit_url", detail="use wss:// on port 443 with no credentials or fragment", idempotency_key=key)
        if remote_participant_identity is not None and not remote_participant_identity.strip():
            return _result("error", error="invalid_participant_identity", detail="agent identity is empty", idempotency_key=key)
        if participant_token:
            bad = check_bridge_token(participant_token, remote_participant_identity or "")
            if bad:
                return _result("error", error=bad[0], detail=bad[1], idempotency_key=key)

        payload = {
            "direction": "outbound",
            "mode": "customer_managed",
            "line_id": None,
            "destination": destination,
            "destination_type": dtype,
            "media": {"audio": True, "video": bool(video)},
            "livekit_url": livekit_url,
            "participant_token": participant_token,
            "remote_participant_identity": remote_participant_identity,
        }
        if when_busy == "queue":
            payload["when_busy"] = "queue"

        if dry_run:
            try:
                cfg = config or load_config()
                project, line = cfg.project_id, cfg.line_id
            except ConfigError:
                project, line = None, None
            shown = dict(payload, line_id=line or "<from GET /api/v1/lines>",
                         livekit_url=livekit_url or "<LIVEKIT_URL>",
                         participant_token=f"<bridge token, {len(participant_token)} chars>" if participant_token else "<bridge token>",
                         remote_participant_identity=remote_participant_identity or "<agent identity>")
            headers = {"Authorization": "Bearer ***", "X-Chert-Project-Id": project or "<CHERT_PROJECT_ID>",
                       "Accept": "application/json", "Idempotency-Key": key, "Content-Type": "application/json"}
            log.info("chert dry run: outbound %s call to %s", "video" if video else "audio", _mask(destination))
            return _result("dry_run", detail="dry run: no call was placed", idempotency_key=key,
                           request={"method": "POST", "url": _url("/calls"), "headers": headers, "json": shown})

        missing = [n for n, v in (("livekit_url", livekit_url), ("participant_token", participant_token),
                                  ("remote_participant_identity", remote_participant_identity)) if not v]
        if missing:
            return _result("error", error="invalid_request", detail="missing " + ", ".join(missing), idempotency_key=key)

        cfg = config or load_config()
        transport = transport or _aiohttp_transport
        payload["line_id"] = await get_line_id(cfg, transport)
        return await _post_call(cfg, transport, payload, key)
    except ConfigError as e:
        return _result("error", error=e.code, detail=e.detail, idempotency_key=key)
    except TransportError as e:
        return _result("error", error="network_error", detail=f"could not reach Chert ({e})", idempotency_key=key)
    except Exception as e:
        log.exception("place_check_in_call failed")
        return _result("error", error="internal_error", detail=type(e).__name__, idempotency_key=key)


async def _post_call(cfg: ChertConfig, transport: Transport, payload: dict, key: str) -> dict:
    url = _url("/calls")
    resp: HttpResponse | None = None
    lost = None
    for _ in range(2):  # same key: Chert returns the original call instead of placing another
        try:
            resp = await transport("POST", url, _headers(cfg, key), payload)
        except TransportError as e:
            lost, resp = str(e), None
            continue
        if resp.status >= 500:
            lost = f"HTTP {resp.status}"
            continue
        break
    if resp is None or resp.status >= 500:
        return _result("unknown", error="create_unconfirmed", idempotency_key=key, line_id=payload["line_id"],
                       detail=f"Chert's answer was lost ({lost}); retry with the same idempotency_key and a valid token")

    rid = _request_id(resp.body)
    data = _data(resp.body) if isinstance(_data(resp.body), dict) else {}
    if resp.status == 201 or (resp.status == 200 and data.get("id")):
        return _result("allocated", accepted=True, detail="call allocated; wait for bridge_ready before greeting",
                       idempotency_key=key, call_id=data.get("id"), call_status=data.get("status"), request_id=rid,
                       line_id=payload["line_id"])
    if resp.status == 202:
        return _result("queued", detail="waiting for the busy line; repeat with the same key when the queue is ready",
                       idempotency_key=key, queue_id=data.get("queue_id") or data.get("id"),
                       queue_state=data.get("state"), position=data.get("position"), request_id=rid,
                       line_id=payload["line_id"])
    code, msg = _api_error(resp)
    log.warning("chert: outbound call refused: %s (HTTP %s, request %s)", code, resp.status, rid)
    return _result("error", error=code, detail=msg or f"Chert answered HTTP {resp.status}", idempotency_key=key,
                   http_status=resp.status, request_id=rid)


async def get_call(call_id: str, *, config: ChertConfig | None = None, transport: Transport | None = None) -> dict:
    """Read a call: its status and whether the bridge is ready (the only signal to greet on)."""
    try:
        if not _UUID.match(call_id or ""):
            return _result("error", error="invalid_request", detail="call_id must be a UUID")
        cfg = config or load_config()
        transport = transport or _aiohttp_transport
        resp = await transport("GET", _url(f"/calls/{quote(call_id)}"), _headers(cfg), None)
        if resp.status != 200:
            code, msg = _api_error(resp)
            return _result("error", error=code, detail=msg, http_status=resp.status)
        data = _data(resp.body) if isinstance(_data(resp.body), dict) else {}
        events = data.get("facetime_call_events") or []
        ready = any(isinstance(e, dict) and e.get("event_type") == "bridge_ready" and e.get("source") == "worker" for e in events)
        status = data.get("status")
        return _result("ok", accepted=True, call_id=data.get("id"), call_status=status,
                       bridge_ready=bool(ready and status == "active"),
                       terminal=status in ("ended", "failed", "rejected"),
                       terminal_reason=data.get("terminal_reason"))
    except ConfigError as e:
        return _result("error", error=e.code, detail=e.detail)
    except TransportError as e:
        return _result("unknown", error="network_error", detail=str(e))
    except Exception as e:
        log.exception("get_call failed")
        return _result("error", error="internal_error", detail=type(e).__name__)
