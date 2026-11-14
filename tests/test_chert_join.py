"""Issue #29: the caregiver text is refused and nobody can see which phones have joined.

tools/chert_join.py reads what the owner's CLI sign-in can see (plan, trial line, join code,
number to text) and says what is wrong; onset.chert_api explains Chert's refusal codes.
"""

import datetime as dt
import json

from onset import chert_api
from tools import chert_join, doctor
from tools.doctor import FAIL, PASS, SKIP, WARN

NOW = dt.datetime(2026, 11, 14, 22, 0, tzinfo=dt.timezone.utc)
SESSION = ("cli-token-" + "x" * 30, "99999999-8888-4777-8666-555555555555")
NUMBER, CAREGIVER = "+15550100123", "+15550100909"
ENV = {"ONSET_CAREGIVER_PHONE": CAREGIVER}


def api(role="owner", plan="trial", ends="2026-11-15T20:00:00Z", line_state="held", released=None, enabled=True,
        phrase="join 48L27T", rotated="2026-11-14T10:00:00Z", number=NUMBER, join_status=200, line_number=NUMBER):
    answers = {
        "/api/v1/cli/whoami": (200, {"data": {"role": role, "email": "owner@example.com"}}),
        "/api/v1/allowance": (200, {"data": {"plan": plan, "trial_ends_at": ends,
                                              "trial_line": {"state": line_state, "held_until": ends, "released_at": released}}}),
        "/api/v1/shared-join-code": (join_status, {"data": {"code": phrase[5:], "enabled": enabled, "rotated_at": rotated, "phrase": phrase,
                                                            "receiving_number": number, "link": "sms:x"}}
                                     if join_status == 200 else {"error": {"code": "join_code_member_required", "message": "no"}}),
        "/api/v1/lines?limit=100": (200, {"data": {"items": [{"id": "l1", "display_name": "Alex Smith", "phone_number": line_number}]}}),
    }
    calls = []

    def http(url, headers, timeout):
        calls.append((url, dict(headers)))
        status, body = answers[url.split("app.trychert.com", 1)[1]]
        return status, json.dumps(body).encode()
    return http, calls


def rows(http, **kw):
    facts = chert_join.inspect(ENV, http, session=lambda env: SESSION)
    return {c.name: c for c in chert_join.checks(facts, ENV, now=NOW, **kw)}, facts


def test_a_healthy_project_passes_and_says_membership_cannot_be_listed():
    http, calls = api()
    r, _ = rows(http)
    assert r["sign-in"].status == PASS and r["join code"].status == PASS and r["trial line"].status == PASS
    assert r["caregiver phone"].status == PASS
    assert r["plan"].status == WARN and "24 hours" in r["plan"].detail  # the trial's reply-only rule
    assert r["who has joined"].status == WARN and "--send-test" in r["who has joined"].detail
    # the sign-in goes only to Chert, and never into the output
    assert all(url.startswith("https://app.trychert.com/api/v1/") for url, _ in calls)
    assert all(h["Authorization"] == f"Bearer {SESSION[0]}" and h["X-Chert-Project-Id"] == SESSION[1] for _, h in calls)
    assert SESSION[0] not in json.dumps([c.detail for c in r.values()])


def test_nothing_is_printed_in_full_without_show():
    http, _ = api()
    r, _ = rows(http)
    text = " ".join(c.detail for c in r.values())
    assert "48L27T" not in text and NUMBER not in text and CAREGIVER not in text and "join ••••7T" in text
    shown, _ = rows(http, show=True)
    assert "join 48L27T" in shown["join code"].detail and NUMBER in shown["join code"].detail


def test_what_the_caregiver_typed_and_where():
    http, _ = api()
    assert rows(http, typed=" join  48l27t ")[0]["what they typed"].status == PASS  # spacing and case do not matter
    old = rows(http, typed="join ZZZZ99")[0]["what they typed"]
    assert old.status == FAIL and "NOT the phrase now in force" in old.detail and "12 hours ago" in old.detail and "no reply" in old.detail
    misread = rows(http, typed="join 48L27O")[0]["what they typed"]
    assert misread.status == FAIL and "never contain O" in misread.detail
    assert rows(http, texted_to="+1 (555) 010-0123")[0]["number they texted"].status == PASS
    wrong = rows(http, texted_to="+15550100777")[0]["number they texted"]
    assert wrong.status == FAIL and "must go to" in wrong.detail


def test_the_ways_a_join_fails_silently():
    assert rows(api(enabled=False)[0])[0]["join code"].status == FAIL
    assert "switched OFF" in rows(api(enabled=False)[0])[0]["join code"].detail
    ended = rows(api(ends="2026-11-14T10:00:00Z")[0])[0]["plan"]
    assert ended.status == FAIL and "ended 12 hours ago" in ended.detail
    released = rows(api(line_state="released", released="2026-11-14T19:00:00Z")[0])[0]["trial line"]
    assert released.status == FAIL and "number may be different" in released.detail
    moved = rows(api(line_number="+15550100555")[0])[0]["number to text"]
    assert moved.status == WARN and "FaceTime number" in moved.detail
    member = rows(api(role="member", join_status=403)[0])[0]
    assert member["sign-in"].status == FAIL and member["join code"].status == FAIL
    assert "join_code_member_required" in member["join code"].detail
    assert rows(api(plan="shared", ends=None)[0])[0]["plan"].status == PASS


def test_no_sign_in_and_bad_caregiver_phone():
    facts = chert_join.inspect(ENV, api()[0], session=lambda env: None)
    out = chert_join.checks(facts, ENV, now=NOW)
    assert [c.status for c in out] == [FAIL] and "tools/chert.sh login" in out[0].detail
    facts = chert_join.inspect({}, api()[0], session=lambda env: SESSION)
    by = {c.name: c for c in chert_join.checks(facts, {"ONSET_CAREGIVER_PHONE": "555-0100"}, now=NOW)}
    assert by["caregiver phone"].status == FAIL and "E.164" in by["caregiver phone"].detail
    assert {c.name: c for c in chert_join.checks(facts, {}, now=NOW)}["caregiver phone"].status == WARN


def test_doctor_row_for_real_texts():
    ok = doctor.check_caregiver_text(ENV, api()[0], session=lambda env: SESSION)
    assert (ok.name, ok.status) == ("caregiver text", WARN) and "--send-test" in ok.detail
    off = doctor.check_caregiver_text(ENV, api(enabled=False)[0], session=lambda env: SESSION)
    assert off.status == FAIL and "switched OFF" in off.detail
    assert doctor.check_caregiver_text(ENV, api()[0], session=lambda env: None).status == SKIP


def test_send_test_needs_yes(capsys):
    assert chert_join.main(["--send-test"]) == 2
    assert "real iMessage" in capsys.readouterr().out


def test_refusal_codes_are_explained():
    said = chert_api.explain("recipient_not_verified", "This shared message could not be accepted.")
    assert said.startswith("This phone has not joined the line") and "tools/chert_join.py" in said
    assert "[Chert: This shared message could not be accepted.]" in said
    assert chert_api.explain("recipient_confirmation_pending").startswith("The phone has joined")
    assert chert_api.explain("something_new", "as Chert said it") == "as Chert said it"
    assert chert_api.explain(None, "") == ""


def test_a_refused_send_says_what_to_do():
    import asyncio

    async def transport(method, url, headers, body):
        if method == "GET":
            return chert_api.HttpResponse(200, {"data": {"items": [{"id": "11111111-2222-4333-8444-555555555555"}]}}, {})
        return chert_api.HttpResponse(403, {"error": {"code": "recipient_not_verified",
                                                      "message": "This shared message could not be accepted."},
                                            "request_id": "da5bfb81-393d-4122-8236-d415a8ac3ae7"}, {})

    env = {"CHERT_API_KEY": "ftk_" + "a" * 40, "CHERT_PROJECT_ID": "99999999-8888-4777-8666-555555555555"}
    res = asyncio.run(chert_api.send_imessage(CAREGIVER, "test", dry_run=False, transport=transport,
                                              config=chert_api.load_config(env)))
    assert res["status"] == "error" and res["error"] == "recipient_not_verified" and res["accepted"] is False
    assert res["detail"].startswith("This phone has not joined the line") and res["request_id"].startswith("da5bfb81")
    assert CAREGIVER not in res["detail"] and env["CHERT_API_KEY"] not in json.dumps(res)
