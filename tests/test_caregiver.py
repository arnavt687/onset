"""Escalation: caregiver link, tokens, the iMessage, the safety gate, and the flow's caregiver states."""

from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs, unquote, urlsplit

import numpy as np
import pytest

from onset import caregiver, patients, script
from onset.caregiver import EscalationConfig, make_invite, notify_caregiver

KEY, SECRET = "APItestkey", "x" * 40
URL = "wss://example.livekit.cloud"


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def frag(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(link).fragment).items()}


class Sink:
    def __init__(self):
        self.events = []

    def emit(self, type_, **f):
        self.events.append({"type": type_, **f})


# ---------------------------------------------------------------- config and tokens


def test_config_from_env():
    assert EscalationConfig.from_env({}) == EscalationConfig("link", True, "", 900)
    c = EscalationConfig.from_env({"ONSET_CAREGIVER_JOIN": "OFF", "ONSET_CAREGIVER_TALK": "0",
                                   "ONSET_CAREGIVER_PAGE": " https://x.test/cg/ ", "ONSET_CAREGIVER_LINK_TTL_S": "60"})
    assert (c.join, c.talk, c.page, c.ttl_s) == ("off", False, "https://x.test/cg/", 120)
    assert EscalationConfig.from_env({"ONSET_CAREGIVER_JOIN": "facetime"}).join == "link"
    assert EscalationConfig.from_env({"ONSET_CAREGIVER_LINK_TTL_S": "abc"}).ttl_s == 900


def test_page_invite_is_a_short_link_and_the_talk_key_is_not_in_it():
    """Issue #30: a ~1,100-character link arrived cut short and lost the talk key, which came last."""
    cfg = EscalationConfig(page="https://x.test/onset/caregiver/#old")
    inv = make_invite("room-1", "call-abc", livekit_url=URL, api_key=KEY, api_secret=SECRET, cfg=cfg,
                      patient_first="Alex", now=1000)
    parts = urlsplit(inv.link)
    assert parts.netloc == "x.test" and not parts.query, "tokens must never be in the query string"
    mark, host, length, watch = parts.fragment.split("/")
    assert mark == "c" and "wss://" + host == URL and int(length) == len(watch)
    assert not set(parts.fragment) & set("&=%#? "), "nothing a messaging app or a shell splits a link on"
    assert inv.talk_token and inv.talk_token not in inv.link and len(inv.link) < 650
    w, t = claims(watch), claims(inv.talk_token)
    # Watch: the call's room, subscribe only. A second audio publisher would make Chert end the call.
    assert w["sub"] == "caregiver-call-abc" and w["video"]["room"] == "room-1"
    assert w["video"]["canSubscribe"] is True and w["video"].get("canPublish") is False
    assert w["video"].get("canPublishData") is False
    # Talk: only the side room, publish only.
    assert t["sub"].startswith(caregiver.TALK_PREFIX) and t["video"]["room"] == "room-1-cg"
    assert t["video"].get("canSubscribe") is False and t["video"]["canPublish"] is True
    assert set(t["video"]["canPublishSources"]) == {"microphone", "camera"}
    assert t["video"]["canPublishData"] is True  # the page reports "microphone blocked" there
    assert w["exp"] - w["nbf"] <= 900 + 5
    assert inv.side_room == "room-1-cg" and inv.kind == "page"
    assert "eyJ" not in repr(inv)


def test_no_talk_means_no_side_room():
    inv = make_invite("r", "c", livekit_url=URL, api_key=KEY, api_secret=SECRET,
                      cfg=EscalationConfig(page="https://x.test/p", talk=False))
    assert inv.side_room is None and inv.talk_token is None and urlsplit(inv.link).fragment.startswith("c/")


def test_an_unusual_livekit_url_falls_back_to_the_long_link():
    inv = make_invite("r", "c", livekit_url="wss://lk.example/some/path", api_key=KEY, api_secret=SECRET,
                      cfg=EscalationConfig(page="https://x.test/p"), patient_first="Alex", now=1000)
    f = frag(inv.link)
    assert unquote(f["u"]) == "wss://lk.example/some/path" and f["n"] == "Alex" and f["e"] == "1900" and f["t"] == inv.talk_token
    local = make_invite("r", "c", livekit_url="ws://localhost:7880", api_key=KEY, api_secret=SECRET,
                        cfg=EscalationConfig(page="https://x.test/p"))
    assert urlsplit(local.link).fragment.startswith("c/ws:localhost:7880/")


def test_meet_link_when_no_page_is_watch_only():
    inv = make_invite("r", "c", livekit_url=URL, api_key=KEY, api_secret=SECRET, cfg=EscalationConfig())
    assert inv.kind == "meet" and inv.side_room is None
    q = parse_qs(urlsplit(inv.link).query)
    assert q["liveKitUrl"] == [URL]
    assert claims(q["token"][0])["video"].get("canPublish") is False


def test_relay_token_is_hidden_and_subscribe_only():
    c = claims(caregiver.relay_token("r-cg", "onset-relay-x", api_key=KEY, api_secret=SECRET))
    assert c["video"]["hidden"] is True and c["video"]["canSubscribe"] is True and c["video"].get("canPublish") is False


def test_invite_needs_livekit_credentials():
    with pytest.raises(ValueError):
        make_invite("r", "c", livekit_url="", api_key=KEY, api_secret=SECRET, cfg=EscalationConfig())


def test_invite_text():
    txt = caregiver.invite_text("Onset check-in for Alex. What Onset noticed: x.", "https://x.test/#w=1",
                                caregiver_name="Sam Lee", patient_name="Alex Doe", talk=True)
    assert txt.startswith("Onset check-in for Alex")
    assert "Sam, join the call now to see and talk to them." in txt
    # the link on a line of its own, last, so the phone shows it as a tappable link
    assert txt.endswith("\n\nhttps://x.test/#w=1")
    assert "told Alex to call 911" in txt
    assert caregiver.invite_text("S", None) == "S"
    assert caregiver.first_name("Demo patient") == "" and caregiver.first_name(" Alex  Doe ") == "Alex"
    assert len(caregiver.invite_text("y" * 5000, "https://x")) <= caregiver.MAX_TEXT


# ---------------------------------------------------------------- the gate decides dry run vs live


def run(coro):
    return asyncio.run(coro)


def fake_send(sent, result=None):
    async def send(phone, text, dry):
        sent.append((phone, text, dry))
        return result or {"status": "dry_run" if dry else "sent", "accepted": not dry}
    return send


def test_dry_run_by_default_and_logged_as_action():
    sink, sent = Sink(), []
    res = run(notify_caregiver(sink=sink, summary="S", caregiver_phone="+15551230002", send=fake_send(sent), env={}))
    assert res["status"] == "dry_run" and res["live"] is False
    assert sent[0][2] is True
    (ev,) = [e for e in sink.events if e["type"] == "action"]
    assert ev["kind"] == "imessage" and ev["live"] is False and ev["target"] == "+1…02"
    assert "ONSET_LIVE_ACTIONS" in ev["detail"]


def test_live_only_with_flag_and_phone():
    sink, sent = Sink(), []
    res = run(notify_caregiver(sink=sink, summary="S", caregiver_phone="+15551230002", send=fake_send(sent),
                               env={"ONSET_LIVE_ACTIONS": "1"}))
    assert res == {"status": "sent", "detail": "", "live": True} and sent[0][2] is False
    # flag but no phone: a dry run to the placeholder
    sent.clear()
    res = run(notify_caregiver(sink=Sink(), summary="S", caregiver_phone="", send=fake_send(sent),
                               env={"ONSET_LIVE_ACTIONS": "1"}))
    assert res["status"] == "dry_run" and sent[0] == (caregiver.PLACEHOLDER_PHONE, "S", True)
    # force_dry wins over the flag (replays)
    sent.clear()
    run(notify_caregiver(sink=Sink(), summary="S", caregiver_phone="+15551230002", send=fake_send(sent),
                         force_dry=True, env={"ONSET_LIVE_ACTIONS": "1"}))
    assert sent[0][2] is True


def test_invite_kind_and_link_in_text_but_not_in_events():
    sink, sent = Sink(), []
    inv = make_invite("r", "c", livekit_url=URL, api_key=KEY, api_secret=SECRET,
                      cfg=EscalationConfig(page="https://x.test/p"))
    run(notify_caregiver(sink=sink, summary="S", caregiver_phone="+15551230002", invite=inv, talk=True,
                         send=fake_send(sent), env={}))
    assert inv.link in sent[0][1]
    (ev,) = sink.events
    assert ev["kind"] == "invite" and ev["link"] == "page"
    assert "eyJ" not in json.dumps(sink.events)


def test_unknown_and_exceptions_never_raise():
    res = run(notify_caregiver(sink=Sink(), summary="S", caregiver_phone="+15551230002",
                               send=fake_send([], {"status": "unknown", "accepted": False, "idempotency_key": "k1"}),
                               env={"ONSET_LIVE_ACTIONS": "1"}))
    assert res["status"] == "unknown" and "k1" in res["detail"]

    async def boom(*a):
        raise RuntimeError("x")

    res = run(notify_caregiver(sink=Sink(), summary="S", caregiver_phone="+15551230002", send=boom,
                               env={"ONSET_LIVE_ACTIONS": "1"}))
    assert res["status"] == "failed"


# ---------------------------------------------------------------- flow: caregiver states and the hook


def make_flow(tmp_path, **kw):
    from onset.flow import CheckFlow

    return CheckFlow(patient_id="demo", patient_name="Pat", baseline_dir=tmp_path, caregiver_phone="",
                     caregiver_send=False, face_meter_factory=lambda: None, arm_meter_factory=lambda: None, **kw)


def test_caregiver_states_speak_and_emit(tmp_path):
    said = []
    flow = make_flow(tmp_path, say=said.append)
    flow.outcome_line = script.CLOSE_ESCALATE
    flow.caregiver_update("invited", "page")
    flow.caregiver_update("invited")  # repeated: ignored
    flow.caregiver_update("joined")
    flow.caregiver_update("talking")
    flow.caregiver_update("left")
    st = [e["status"] for e in flow.events if e["type"] == "caregiver_join"]
    assert st == ["invited", "joined", "talking", "left"]
    assert said == [script.CAREGIVER_JOINED.say, script.CAREGIVER_TALKING.say]
    assert flow.caption == script.CLOSE_ESCALATE.caption
    for line in (script.CAREGIVER_JOINED, script.CAREGIVER_TALKING):
        assert "911" in line.caption and "911" in line.say.replace("9 1 1", "911")


def test_caregiver_pip_is_drawn_while_fresh(tmp_path):
    flow = make_flow(tmp_path, out_size=(320, 180))
    flow.outcome_line = script.CLOSE_ESCALATE
    flow.caregiver_update("talking")
    green = np.zeros((90, 160, 4), np.uint8)
    green[..., 1] = 255
    flow.caregiver_frame(green, 10.0)
    img = flow.render(None, 10.5)
    assert (img[:, 320 - 12 - 40, 1] == 255).sum() > 10  # a green PiP column on the right
    img = flow.render(None, 20.0)  # stale: no PiP
    assert (img[:, 320 - 12 - 40, 1] == 255).sum() < 10


def test_unidentified_caller_never_saves_a_baseline(tmp_path):
    from tests.test_flow import run as run_flow  # the shared fake-call driver

    flow = run_flow(tmp_path)  # a first call: saves a baseline
    assert flow.baseline_saved
    from onset.flow import CheckFlow

    orig = CheckFlow.__init__

    def anon(self, *a, **k):
        k["save_baseline"] = False
        orig(self, *a, **k)

    CheckFlow.__init__ = anon
    try:
        flow = run_flow(tmp_path / "anon")
    finally:
        CheckFlow.__init__ = orig
    assert not flow.baseline_saved
    d = [e for e in flow.events if e["type"] == "decision"]
    assert d and d[0]["outcome"].value == "unclear" and d[0]["baseline_used"] is False
    assert flow.outcome_line == script.CLOSE_UNCLEAR
    assert not (tmp_path / "anon" / "baselines").exists() or not any((tmp_path / "anon" / "baselines").iterdir())


def test_escalate_hook_replaces_plain_text(tmp_path):
    from tests.test_flow import baseline, face, run as run_flow
    from onset.flow import CheckFlow

    calls = []

    async def hook(decision, summary):
        calls.append((decision.outcome.value, summary))
        return {"status": "dry_run", "detail": "hooked"}

    orig = CheckFlow.__init__

    def with_hook(self, *a, **k):
        k["escalate"] = hook
        orig(self, *a, **k)

    CheckFlow.__init__ = with_hook
    try:
        flow = run_flow(tmp_path, base=baseline(), f=face(asym=0.6))
    finally:
        CheckFlow.__init__ = orig
    assert calls and calls[0][0] == "escalate" and "Pat" in calls[0][1]
    cg = [e for e in flow.events if e["type"] == "caregiver"]
    assert cg[-1]["status"] == "dry_run" and cg[-1]["detail"] == "hooked"
    assert not flow.sent  # the hook did the sending
    assert flow.events[-1]["type"] == "run_ended"


def test_flow_without_hook_logs_an_action(tmp_path):
    from tests.test_flow import baseline, face, run as run_flow

    flow = run_flow(tmp_path, base=baseline(), f=face(asym=0.6))
    acts = [e for e in flow.events if e["type"] == "action"]
    assert acts and acts[0]["kind"] == "imessage" and acts[0]["live"] is False


def test_replay_and_tests_stay_dry_even_with_the_flag(tmp_path, monkeypatch):
    from tests.test_flow import baseline, face, run as run_flow

    monkeypatch.setenv("ONSET_LIVE_ACTIONS", "1")
    flow = run_flow(tmp_path, base=baseline(), f=face(asym=0.6))  # caregiver_send=False in the driver
    assert flow.sent and all(dry for _, _, dry in flow.sent)


def test_handle_from_metadata():
    h = patients.handle_from_metadata
    assert h({"chert_call": "x", "media": {}}) is None
    assert h({"from": {"type": "phone_number", "value": "+15551230001"}}) == "+15551230001"
    assert h({"caller": " a@b.co "}) == "a@b.co"
    assert h(None) is None and h({"from": None}) is None


# ---------------------------------------------------------------- tools/fake_escalation.py checks


def test_fake_escalation_check_and_tone():
    import math

    from tools import fake_escalation as fe

    rate = 16000
    k = np.arange(rate)
    tone = (8000 * np.sin(2 * math.pi * fe.TONE_HZ * k / rate)).astype(np.int16)
    noise = (np.random.default_rng(0).normal(0, 3000, rate)).astype(np.int16)
    assert fe.tone_power(tone, rate) > 0.9 and fe.tone_power(noise, rate) < 0.1
    assert fe.tone_power(np.zeros(10, np.int16), rate) == 0.0

    good_events = [{"type": "decision", "outcome": "unclear"},
                   *({"type": "caregiver_join", "status": s} for s in ("invited", "joined", "talking")),
                   {"type": "action", "kind": "invite", "live": False}]
    good = {"link": "x", "watch_could_publish": False, "summary": "s", "relay_tone": 0.9, "relay_attr": "on"}
    assert fe.check(good_events, good) == []
    bad = fe.check(good_events[:2] + [{"type": "action", "live": True}],
                   dict(good, watch_could_publish=True, relay_tone=0.0, summary=None))
    assert len(bad) == 6
    assert fe.parse_link("https://x/#u=wss%3A%2F%2Fa&w=W&t=T") == {"u": "wss://a", "w": "W", "t": "T"}
