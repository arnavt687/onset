"""Piece 6 and 8: Chert API client, with a fake transport. No network, no keys, nothing sent."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import pytest

from onset import chert_api as c
from onset.chert_api import ChertConfig, HttpResponse, TransportError

PROJECT = "38e8b4b9-5811-456e-9834-cac1d71bab63"
LINE = "11111111-2222-4333-8444-555555555555"
MSG = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
CALL = "99999999-8888-4777-8666-555555555555"
KEY = "ftk_test_not_a_real_key_0123456789"
PHONE = "+15551234567"
CFG = ChertConfig(api_key=KEY, project_id=PROJECT)


class FakeTransport:
    """Answers each request from a script; records everything it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict, object]] = []

    async def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        assert urlsplit(url).hostname == "app.trychert.com"
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]


def lines(*ids):
    return HttpResponse(200, {"items": [{"id": i, "status": "active"} for i in ids]})


def created(state, msg_id=MSG):
    return HttpResponse(201, {"data": {"id": msg_id, "state": state}, "request_id": "req-1"})


def listing(*records):
    return HttpResponse(200, {"items": list(records)})


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_line_cache():
    c._line_cache.clear()
    yield
    c._line_cache.clear()


# ----------------------------------------------------------------- send_imessage


def test_dry_run_is_the_default_and_touches_nothing():
    t = FakeTransport()
    r = run(c.send_imessage(PHONE, "Onset check-in: a change was detected.", config=CFG, transport=t))
    assert r["status"] == "dry_run" and r["accepted"] is False and r["error"] is None
    assert t.calls == []
    req = r["request"]
    assert req["method"] == "POST" and req["url"] == "https://app.trychert.com/api/v1/shared-messages"
    assert req["headers"]["Authorization"] == "Bearer ***"
    assert req["headers"]["X-Chert-Project-Id"] == PROJECT
    assert req["headers"]["Idempotency-Key"] == r["idempotency_key"]
    assert req["json"]["recipient"] == PHONE
    assert KEY not in repr(r)


def test_dry_run_works_without_any_configuration():
    r = run(c.send_imessage(PHONE, "hello", config=None, transport=FakeTransport()))
    assert r["status"] == "dry_run"
    assert "ftk_" not in repr(r)


def test_real_send_sends_required_headers_and_body():
    t = FakeTransport(lines(LINE), created("queued"))
    r = run(c.send_imessage(PHONE, "hello", dry_run=False, config=CFG, transport=t))
    assert r["status"] == "queued" and r["accepted"] is True and r["message_id"] == MSG
    get_lines, post = t.calls
    assert get_lines[0] == "GET" and get_lines[1].startswith("https://app.trychert.com/api/v1/lines")
    method, url, headers, body = post
    assert (method, url) == ("POST", "https://app.trychert.com/api/v1/shared-messages")
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert headers["X-Chert-Project-Id"] == PROJECT
    assert 8 <= len(headers["Idempotency-Key"]) <= 200
    assert body == {"line_id": LINE, "recipient": PHONE, "text": "hello"}
    assert KEY not in repr(r)


def test_each_message_gets_a_fresh_idempotency_key_and_line_is_cached():
    t = FakeTransport(lines(LINE), created("sent"), created("sent", msg_id=CALL))
    a = run(c.send_imessage(PHONE, "one", dry_run=False, config=CFG, transport=t))
    b = run(c.send_imessage(PHONE, "two", dry_run=False, config=CFG, transport=t))
    keys = [p[2]["Idempotency-Key"] for p in t.posts()]
    assert len(set(keys)) == 2 and a["idempotency_key"] != b["idempotency_key"]
    assert sum(1 for call in t.calls if call[0] == "GET") == 1  # line looked up once


def test_configured_line_skips_lookup():
    t = FakeTransport(created("sent"))
    cfg = ChertConfig(api_key=KEY, project_id=PROJECT, line_id=LINE)
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=cfg, transport=t))
    assert r["status"] == "sent" and len(t.calls) == 1


def test_unknown_state_is_reread_never_resent():
    t = FakeTransport(lines(LINE), created("unknown"),
                      listing({"id": MSG, "state": "unknown"}),
                      listing({"id": "other", "state": "sent"}, {"id": MSG, "state": "sent"}))
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=t, reread_delay_s=0))
    assert r["status"] == "sent" and r["accepted"] is True
    assert len(t.posts()) == 1
    assert [call[1] for call in t.calls[2:]] == ["https://app.trychert.com/api/v1/shared-messages"] * 2


def test_unknown_that_stays_unknown_returns_unknown_with_the_key():
    t = FakeTransport(lines(LINE), created("unknown"), *[listing({"id": MSG, "state": "unknown"})] * 3)
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=t, reread_delay_s=0))
    assert r["status"] == "unknown" and r["accepted"] is False and r["error"] == "send_state_unknown"
    assert r["message_id"] == MSG and r["idempotency_key"]
    assert len(t.posts()) == 1


def test_lost_answer_is_retried_with_the_same_key_once():
    t = FakeTransport(lines(LINE), TransportError("ServerTimeoutError"), created("queued"))
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=t))
    assert r["status"] == "queued"
    k1, k2 = (p[2]["Idempotency-Key"] for p in t.posts())
    assert k1 == k2


def test_two_lost_answers_give_unknown_not_error():
    t = FakeTransport(lines(LINE), TransportError("timeout"), HttpResponse(503, None))
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=t))
    assert r["status"] == "unknown" and r["error"] == "send_unconfirmed"
    assert len({p[2]["Idempotency-Key"] for p in t.posts()}) == 1


def test_retry_with_caller_key_reuses_it():
    t = FakeTransport(lines(LINE), created("sent"))
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=t, idempotency_key="onset-msg-retry-1"))
    assert t.posts()[0][2]["Idempotency-Key"] == "onset-msg-retry-1" == r["idempotency_key"]


def test_failed_state_is_reported():
    t = FakeTransport(lines(LINE), created("failed"))
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=t))
    assert r["status"] == "failed" and r["accepted"] is False and r["error"] == "send_failed"


@pytest.mark.parametrize(
    "resp, code",
    [
        (HttpResponse(403, {"error": {"code": "recipient_not_joined", "message": "This phone has not joined"}, "request_id": "r"}), "recipient_not_joined"),
        (HttpResponse(401, {}), "unauthorized"),
        (HttpResponse(429, {"error": {"code": "rate_limited", "message": "slow down"}}, {"Retry-After": "60"}), "rate_limited"),
        (HttpResponse(409, None), "idempotency_conflict"),
        (HttpResponse(400, {"error": {"code": "Bad Code With Spaces <script>", "message": "x"}}), "invalid_request"),
    ],
)
def test_api_errors_become_result_codes(resp, code):
    t = FakeTransport(lines(LINE), resp)
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=t))
    assert r["status"] == "error" and r["error"] == code and r["accepted"] is False
    assert len(t.posts()) == 1  # 4xx is never retried
    if code == "rate_limited":
        assert "60" in r["detail"]


@pytest.mark.parametrize(
    "recipient, text, code",
    [
        ("5551234567", "hi", "invalid_recipient"),
        ("caregiver@example.com", "hi", "invalid_recipient"),
        (PHONE, "   ", "empty_text"),
        (PHONE, "x" * 4001, "text_too_long"),
        (PHONE, "😀" * 2001, "text_too_long"),  # 4002 UTF-16 units
    ],
)
def test_bad_input_is_refused_before_any_request(recipient, text, code):
    t = FakeTransport()
    for dry in (True, False):
        r = run(c.send_imessage(recipient, text, dry_run=dry, config=CFG, transport=t))
        assert r["status"] == "error" and r["error"] == code
    assert t.calls == []


def test_missing_key_is_an_error_not_an_exception(monkeypatch):
    monkeypatch.setattr(c, "_ENV_FILES", ())
    for name in ("CHERT_API_KEY", "CHERT_PROJECT_ID", "CHERT_LINE_ID"):
        monkeypatch.delenv(name, raising=False)
    t = FakeTransport()
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, transport=t))
    assert r["status"] == "error" and r["error"] == "missing_api_key" and t.calls == []


def test_load_config_validates():
    with pytest.raises(c.ConfigError) as e:
        c.load_config({"CHERT_API_KEY": "ftu_cli_token", "CHERT_PROJECT_ID": PROJECT})
    assert e.value.code == "invalid_api_key"
    with pytest.raises(c.ConfigError) as e:
        c.load_config({"CHERT_API_KEY": KEY, "CHERT_PROJECT_ID": "nope"})
    assert e.value.code == "missing_project_id"
    cfg = c.load_config({"CHERT_API_KEY": KEY, "CHERT_PROJECT_ID": PROJECT, "CHERT_LINE_ID": LINE})
    assert cfg.line_id == LINE and KEY not in repr(cfg)


def test_line_lookup_problems():
    for answer, code in [(lines(), "no_line"), (lines(LINE, MSG), "multiple_lines"),
                         (HttpResponse(403, {"error": {"code": "forbidden"}}), "line_lookup_forbidden")]:
        c._line_cache.clear()
        t = FakeTransport(answer)
        r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=t))
        assert r["error"] == code and t.posts() == []


def test_network_error_on_lookup_and_unexpected_exception_do_not_raise():
    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=FakeTransport(TransportError("dns"))))
    assert r["status"] == "error" and r["error"] == "network_error"

    async def broken(*_):
        raise RuntimeError(f"boom {KEY}")

    r = run(c.send_imessage(PHONE, "hi", dry_run=False, config=CFG, transport=broken))
    assert r["status"] == "error" and r["error"] == "internal_error" and KEY not in repr(r)


def test_urls_other_than_chert_are_refused():
    for url in ("https://evil.example/api/v1/lines", "http://app.trychert.com/api/v1/lines",
                "https://app.trychert.com.evil.example/x", "https://user@app.trychert.com/x",
                "https://facetime.trychert.com/api/v1/calls"):
        with pytest.raises(ValueError):
            c._check_url(url)
    c._check_url("https://app.trychert.com/api/v1/lines")


def test_get_message_state_matches_by_idempotency_key():
    t = FakeTransport(listing({"id": MSG, "idempotency_key": "onset-msg-k-123", "state": "delivered"}))
    r = run(c.get_message_state(None, idempotency_key="onset-msg-k-123", config=CFG, transport=t))
    assert r["status"] == "delivered" and r["message_id"] == MSG
    t = FakeTransport(listing())
    r = run(c.get_message_state(MSG, config=CFG, transport=t))
    assert r["status"] == "unknown" and r["error"] == "not_found_in_recent"


# ----------------------------------------------------------------- outbound calls (piece 8)

LK_KEY, LK_SECRET = "APItestkey", "test-secret-that-is-long-enough-for-hs256-0123456789"


def bridge_token(ttl=600, room="onset-checkin-1"):
    return c.mint_bridge_token(room, api_key=LK_KEY, api_secret=LK_SECRET, ttl_s=ttl)


def test_minted_bridge_token_meets_chert_rules():
    tok = bridge_token()
    claims = c._jwt_claims(tok)
    assert claims["sub"] == "chert-bridge-onset-checkin-1"
    assert claims["video"]["room"] == "onset-checkin-1"
    assert claims["exp"] - claims["nbf"] <= 7200
    assert c.check_bridge_token(tok, "onset") is None
    assert c.check_bridge_token(tok, "chert-bridge-onset-checkin-1")[0] == "invalid_participant_identity"
    with pytest.raises(ValueError):
        bridge_token(ttl=8000)


def test_expiring_token_is_refused_locally():
    tok = bridge_token(ttl=120)
    assert c.check_bridge_token(tok, "onset", now=time.time() + 60)[0] == "session_expired"
    assert c.check_bridge_token("not.a.jwt", "onset")[0] == "invalid_token_grants"


def test_call_dry_run_redacts_token_and_key():
    tok = bridge_token()
    t = FakeTransport()
    r = run(c.place_check_in_call(PHONE, livekit_url="wss://onset.livekit.cloud", participant_token=tok,
                                  remote_participant_identity="onset", config=CFG, transport=t))
    assert r["status"] == "dry_run" and t.calls == []
    body = r["request"]["json"]
    assert body["direction"] == "outbound" and body["mode"] == "customer_managed"
    assert body["destination_type"] == "phone" and body["media"] == {"audio": True, "video": True}
    assert tok not in repr(r) and KEY not in repr(r)
    assert "when_busy" not in body


def test_call_allocated():
    tok = bridge_token()
    t = FakeTransport(lines(LINE), HttpResponse(201, {"data": {"id": CALL, "status": "pending"}, "request_id": "r"}))
    r = run(c.place_check_in_call(PHONE, dry_run=False, livekit_url="wss://onset.livekit.cloud", participant_token=tok,
                                  remote_participant_identity="onset", config=CFG, transport=t))
    assert r["status"] == "allocated" and r["accepted"] is True and r["call_id"] == CALL
    method, url, headers, body = t.posts()[0]
    assert url == "https://app.trychert.com/api/v1/calls"
    assert headers["Idempotency-Key"].startswith("onset-call-")
    assert body["line_id"] == LINE and body["participant_token"] == tok


def test_call_queued_and_lost_answer_keeps_key():
    tok = bridge_token()
    t = FakeTransport(lines(LINE), TransportError("timeout"),
                      HttpResponse(202, {"data": {"queued": True, "queue_id": "q-1", "state": "waiting", "position": 2}}))
    r = run(c.place_check_in_call("someone@example.com", dry_run=False, livekit_url="wss://onset.livekit.cloud",
                                  participant_token=tok, remote_participant_identity="onset", when_busy="queue",
                                  config=CFG, transport=t))
    assert r["status"] == "queued" and r["queue_id"] == "q-1" and r["accepted"] is False
    k1, k2 = (p[2]["Idempotency-Key"] for p in t.posts())
    assert k1 == k2 and t.posts()[0][3]["when_busy"] == "queue" and t.posts()[0][3]["destination_type"] == "email"


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"livekit_url": "https://onset.livekit.cloud"}, "invalid_livekit_url"),
        ({"livekit_url": "wss://u:p@onset.livekit.cloud"}, "invalid_livekit_url"),
        ({"livekit_url": "wss://onset.livekit.cloud:7880"}, "invalid_livekit_url"),
        ({"when_busy": "wait"}, "invalid_request"),
        ({"remote_participant_identity": " "}, "invalid_participant_identity"),
    ],
)
def test_call_input_checks(kwargs, code):
    args = {"livekit_url": "wss://onset.livekit.cloud", "remote_participant_identity": "onset"} | kwargs
    r = run(c.place_check_in_call(PHONE, dry_run=False, config=CFG, transport=FakeTransport(), **args))
    assert r["status"] == "error" and r["error"] == code


def test_real_call_needs_room_credentials():
    r = run(c.place_check_in_call(PHONE, dry_run=False, livekit_url="wss://onset.livekit.cloud",
                                  config=CFG, transport=FakeTransport()))
    assert r["error"] == "invalid_request" and "participant_token" in r["detail"]


def test_call_refused_by_chert():
    t = FakeTransport(lines(LINE), HttpResponse(403, {"error": {"code": "outbound_not_enabled", "message": "Outbound is not enabled for this line"}}))
    r = run(c.place_check_in_call(PHONE, dry_run=False, livekit_url="wss://onset.livekit.cloud", participant_token=bridge_token(),
                                  remote_participant_identity="onset", config=CFG, transport=t))
    assert r["status"] == "error" and r["error"] == "outbound_not_enabled"


def test_get_call_reports_bridge_ready():
    body = {"data": {"id": CALL, "status": "active", "facetime_call_events": [{"event_type": "bridge_ready", "source": "worker"}]}}
    r = run(c.get_call(CALL, config=CFG, transport=FakeTransport(HttpResponse(200, body))))
    assert r["bridge_ready"] is True and r["terminal"] is False
    body = {"data": {"id": CALL, "status": "ended", "facetime_call_events": []}}
    r = run(c.get_call(CALL, config=CFG, transport=FakeTransport(HttpResponse(200, body))))
    assert r["bridge_ready"] is False and r["terminal"] is True
