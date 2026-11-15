"""The caregiver briefing call: Onset FaceTimes the caregiver after a change (onset/brief.py,
tools/caregiver_call.py, ONSET_CAREGIVER_CALL=after)."""

import asyncio
import json

import numpy as np

from onset import brief, chert_api, overlay
from onset.caregiver import EscalationConfig
from tools import caregiver_call

INFO = brief.BriefInfo(patient="Alex", caregiver="Sam", outcome="escalate",
                       reasons=("The left arm dropped while it was held up", "The smile was less even than usual"),
                       when="Sat 14 Nov at 9:02 am", run_id="run-1")
ENV = {"CHERT_API_KEY": "ftk_" + "a" * 40, "CHERT_PROJECT_ID": "99999999-8888-4777-8666-555555555555",
       "CHERT_LINE_ID": "11111111-2222-4333-8444-555555555555"}
CALL = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def test_what_onset_says_to_the_caregiver():
    said = [ln.say for ln in brief.lines(INFO)]
    assert said[0] == "Hello Sam. This is Onset, the check-in helper for Alex."
    assert said[1] == "Alex's check-in just now was different from their usual check-in."
    assert said[2:4] == list(INFO.reasons)
    assert said[-1] == "Please call Alex now. If you cannot reach them, call 9 1 1."
    assert overlay.is_alert(brief.lines(INFO)[-1].caption)  # the 911 line gets the alert colour
    unclear = brief.lines(brief.BriefInfo(patient="Chris", outcome="unclear"))
    assert "could not be completed clearly" in unclear[1].say and unclear[1].say.startswith("Chris' check-in")
    assert unclear[0].say == "Hello. This is Onset, the check-in helper for Chris."
    nameless = brief.lines(brief.BriefInfo())
    assert "the person you care for" in nameless[0].say and "call 9 1 1" in nameless[-1].say
    # It reports what was measured and what to do; it never names a condition.
    text = " ".join(brief.spoken(INFO)).lower()
    for word in ("stroke", "diagnos", "you are fine", "nothing wrong"):
        assert word not in text


def test_metadata_round_trip_and_cleaning():
    meta = json.loads(INFO.to_json())
    assert brief.is_brief(meta) and brief.BriefInfo.from_meta(meta) == INFO
    assert not brief.is_brief({}) and not brief.is_brief({"onset": "something else"}) and not brief.is_brief(None)
    messy = brief.BriefInfo.from_meta({"patient": "  Alex \n", "outcome": "panic", "reasons": ["a", " ", "b", "c", "d"],
                                       "caregiver": 7, "when": None})
    assert messy.patient == "Alex" and messy.outcome == "escalate" and messy.reasons == ("a", "b", "c") and messy.caregiver == "7"
    assert brief.BriefInfo.from_meta({"reasons": "not a list"}).reasons == ()


def test_card_is_drawn_inside_the_safe_area():
    w, h = overlay.output_size({})
    top, bottom = overlay.safe_box(w, h)
    blank = overlay.blank(w, h)
    img = brief.render(INFO, w, h, brief.lines(INFO)[-1].caption, 1e6)
    assert img.shape == (h, w, 4) and img.dtype == np.uint8
    changed = np.flatnonzero((img[..., :3] != blank[..., :3]).any(axis=(1, 2)))
    text_rows = changed[changed < bottom]
    assert text_rows.size and text_rows[0] >= top, "the card's text starts inside the safe area"
    # the same card in a wide frame, and with no reasons at all
    assert brief.render(INFO, 640, 360, "x", 0.0).shape == (360, 640, 4)
    assert brief.render(brief.BriefInfo(), w, h, "", 0.0).shape == (h, w, 4)


def test_the_switch_is_off_by_default():
    assert EscalationConfig.from_env({}).call == "off"
    assert EscalationConfig.from_env({"ONSET_CAREGIVER_CALL": "after"}).call == "after"
    assert EscalationConfig.from_env({"ONSET_CAREGIVER_CALL": "AFTER "}).call == "after"
    assert EscalationConfig.from_env({"ONSET_CAREGIVER_CALL": "now"}).call == "off"


def test_queue_and_end_call():
    calls = []

    async def transport(method, url, headers, body):
        calls.append((method, url, dict(headers), body))
        if url.endswith("/calls/queue/q-123456"):
            return chert_api.HttpResponse(200, {"data": {"state": "ready", "position": 1}}, {})
        if url.endswith(f"/calls/{CALL}/end"):
            return chert_api.HttpResponse(202, {"data": {}}, {})
        return chert_api.HttpResponse(404, {"error": {"code": "not_found", "message": "no"}}, {})

    cfg = chert_api.load_config(ENV)
    q = asyncio.run(chert_api.get_call_queue("q-123456", config=cfg, transport=transport))
    assert q["status"] == "ok" and q["ready"] is True and q["position"] == 1
    assert asyncio.run(chert_api.get_call_queue("../x", config=cfg, transport=transport))["error"] == "invalid_request"
    e = asyncio.run(chert_api.end_call(CALL, config=cfg, transport=transport))
    assert e["status"] == "ending" and e["accepted"]
    method, url, headers, body = calls[-1]
    assert method == "POST" and url == f"https://app.trychert.com/api/v1/calls/{CALL}/end" and body == {}
    assert headers["Idempotency-Key"].startswith("onset-end-")
    assert asyncio.run(chert_api.end_call("nope", config=cfg, transport=transport))["error"] == "invalid_request"
    assert ENV["CHERT_API_KEY"] not in json.dumps([q, e])


def test_a_refused_call_is_explained():
    async def transport(method, url, headers, body):
        return chert_api.HttpResponse(403, {"error": {"code": "recipient_not_verified", "message": "no"}}, {})

    cfg = chert_api.load_config(ENV)
    token = chert_api.mint_bridge_token("onset-brief-x", api_key="APIabc", api_secret="s" * 40)
    res = asyncio.run(chert_api.place_check_in_call("+15550100909", dry_run=False, livekit_url="wss://x.livekit.cloud",
                                                    participant_token=token, remote_participant_identity="agent-1",
                                                    when_busy="queue", config=cfg, transport=transport))
    assert res["status"] == "error" and res["error"] == "recipient_not_verified"
    assert res["detail"].startswith("This phone has not joined the line")


def test_dry_run_rings_nobody(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("ONSET_LIVE_ACTIONS", raising=False)
    monkeypatch.setattr(caregiver_call, "LIVE", tmp_path)
    (tmp_path / "events.jsonl").write_text(json.dumps({"t": 12.5, "type": "run_ended"}) + "\n")
    monkeypatch.setattr(caregiver_call, "load_env", lambda: None)
    rc = caregiver_call.main(["--to", "+15550100909", "--patient", "Alex", "--caregiver", "Sam",
                              "--reason", "The left arm dropped while it was held up"])
    out = capsys.readouterr().out
    assert rc == 0 and "DRY RUN" in out and "Please call Alex now" in out
    assert "+15550100909" not in out, "phone numbers are masked"
    ev = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    assert ev["type"] == "caregiver_call" and ev["status"] == "dry_run" and ev["t"] > 12.5 and "0909" not in json.dumps(ev)[:60]
    assert caregiver_call.main(["--patient", "Alex"]) == 2  # nobody to call


def test_briefing_file_from_the_agent(tmp_path):
    f = tmp_path / "caregiver_call.json"
    f.write_text(json.dumps({**INFO.to_meta(), "to": "+15550100909"}))
    info, to = caregiver_call.info_from_args(caregiver_call.argparse.Namespace(brief=str(f), to=None))
    assert info == INFO and to == "+15550100909"
