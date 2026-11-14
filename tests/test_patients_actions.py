"""Patient profiles (onset/patients.py) and the live-actions safety gate (onset/actions.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onset import actions, patients
from onset.patients import ProfileError

REPO = Path(__file__).resolve().parent.parent


def write(tmp_path: Path, data) -> Path:
    p = tmp_path / "patients.json"
    p.write_text(json.dumps(data))
    return p


PROFILES = {
    "default": "a",
    "patients": [
        {"id": "a", "name": "Alex", "phones": ["+15551230001"], "caregiver": {"name": "Sam", "phone": "+15551230002"}},
        {"id": "b", "name": "Jordan", "phones": ["+15551230003", "Jordan@Example.com"],
         "caregiver": {"phone": "+15551230004"}, "sentence": "The sky is blue."},
    ],
}


def test_example_file_is_valid():
    prof = patients.load_profiles(REPO / "config" / "patients.example.json", env={})
    assert {p.id for p in prof.patients} == {"pat-001", "pat-002"}


def test_lookup_by_phone_in_any_format(tmp_path):
    prof = patients.load_profiles(write(tmp_path, PROFILES), env={})
    for handle in ("+15551230003", "+1 (555) 123-0003", "5551230003", "jordan@example.com"):
        p = prof.lookup(handle)
        assert (p.id, p.matched_by) == ("b", "phone"), handle
    assert prof.lookup("+15551230003").caregiver.phone == "+15551230004"
    assert prof.lookup("+15551230003").sentence == "The sky is blue."


def test_unmatched_caller_uses_default_then_unknown(tmp_path):
    prof = patients.load_profiles(write(tmp_path, PROFILES), env={})
    assert (prof.lookup("+19998887777").id, prof.lookup(None).matched_by) == ("a", "default")
    no_default = dict(PROFILES, default=None)
    prof = patients.load_profiles(write(tmp_path, no_default), env={})
    p = prof.lookup("+19998887777")
    assert p.anonymous and p.matched_by == "unknown" and not p.caregiver.phone


def test_single_profile_without_default_is_used(tmp_path):
    prof = patients.load_profiles(write(tmp_path, {"patients": PROFILES["patients"][:1]}), env={})
    assert prof.lookup(None).id == "a"


@pytest.mark.parametrize("bad, msg", [
    ({"patients": []}, "no patients"),
    ({"x": 1}, "patients"),
    ({"patients": [{"id": "../x"}]}, ".id"),
    ({"patients": [{"id": "unknown"}]}, ".id"),
    ({"patients": [{"id": "a"}, {"id": "a"}]}, "duplicate"),
    ({"patients": [{"id": "a", "phones": ["555-1234"]}]}, "phones[0]"),
    ({"patients": [{"id": "a", "caregiver": {"phone": "sam@example.com"}}]}, "caregiver.phone"),
    ({"patients": [{"id": "a", "phones": ["+15551230001"]}, {"id": "b", "phones": ["+15551230001"]}]}, "also belongs"),
    ({"default": "zz", "patients": [{"id": "a"}]}, "default"),
])
def test_invalid_files_are_rejected_without_printing_phones(tmp_path, bad, msg):
    with pytest.raises(ProfileError) as e:
        patients.load_profiles(write(tmp_path, bad), env={})
    assert msg in str(e.value)
    assert "5551230001" not in str(e.value)


def test_env_profile_when_no_file(tmp_path):
    env = {"ONSET_PATIENT_ID": "p7", "ONSET_PATIENT_NAME": "Pat", "ONSET_CAREGIVER_PHONE": "+15550001111"}
    p = patients.load_profiles(tmp_path / "missing.json", env=env).lookup("+15559990000")
    assert (p.id, p.name, p.caregiver.phone, p.matched_by) == ("p7", "Pat", "+15550001111", "env")


def test_named_file_missing_is_an_error(tmp_path):
    with pytest.raises(ProfileError):
        patients.load_profiles(env={"ONSET_PATIENTS_FILE": str(tmp_path / "nope.json")})


def test_lookup_never_raises_on_a_broken_file(tmp_path):
    p = tmp_path / "patients.json"
    p.write_text("{not json")
    got = patients.lookup("+15551230001", path=p, env={"ONSET_PATIENT_ID": "fallback"})
    assert got.id == "fallback"


def test_repr_and_mask_hide_numbers():
    p = patients.Patient("a", "Alex", ("+15551230001",), patients.Caregiver("Sam", "+15551230002"))
    assert "5551230001" not in repr(p) and "5551230002" not in repr(p)
    assert patients.mask("+15551230002") == "+1…02"
    assert patients.mask("jordan@example.com") == "j…@example.com"


# ---------------------------------------------------------------- actions


class Sink:
    def __init__(self):
        self.events = []

    def emit(self, type_, **f):
        self.events.append({"type": type_, **f})


@pytest.mark.parametrize("env, target, live, why", [
    ({}, "+15551230002", False, "ONSET_LIVE_ACTIONS"),
    ({"ONSET_LIVE_ACTIONS": "true"}, "+15551230002", False, "ONSET_LIVE_ACTIONS"),
    ({"ONSET_LIVE_ACTIONS": "1"}, "", False, "no caregiver phone"),
    ({"ONSET_LIVE_ACTIONS": "1", "ONSET_LIVE_ACTIONS_DENY": "call, invite"}, "+15551230002", False, "DENY"),
    ({"ONSET_LIVE_ACTIONS": "1"}, "+15551230002", True, "live"),
])
def test_gate(env, target, live, why):
    g = actions.gate("call", target=target, env=env)
    assert g.live is live and why in g.reason


def test_unknown_kind_is_never_live():
    assert not actions.gate("wire", target="+15551230002", env={"ONSET_LIVE_ACTIONS": "1"}).live


def test_record_emits_masked_action_event():
    sink = Sink()
    g = actions.gate("imessage", target="+15551230002", env={})
    actions.record(sink, "imessage", g, target="+15551230002", status="dry_run", chars=42)
    (ev,) = sink.events
    assert ev["type"] == "action" and ev["kind"] == "imessage" and ev["live"] is False
    assert ev["target"] == "+1…02" and ev["chars"] == 42 and "ONSET_LIVE_ACTIONS" in ev["detail"]


def test_record_survives_a_broken_sink():
    class Bad:
        def emit(self, *a, **k):
            raise RuntimeError("disk full")

    g = actions.gate("call", target="", env={})
    assert actions.record(Bad(), "call", g, target="", status="dry_run")["status"] == "dry_run"
    assert actions.record(None, "call", g, target="", status="dry_run")["live"] is False
