"""tools/doctor.py: every check's logic, offline, with fakes for HTTP, sockets, TTS and the Chert CLI."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from tools import doctor
from tools.doctor import FAIL, PASS, SKIP, WARN, Check

REPO = Path(__file__).resolve().parent.parent
GOOD = {
    "LIVEKIT_URL": "wss://demo-abc.livekit.cloud",
    "LIVEKIT_API_KEY": "APIfakekey123",
    "LIVEKIT_API_SECRET": "fakesecretfakesecretfakesecret",
    "AGENT_NAME": "onset-tt",
    "ELEVENLABS_API_KEY": "sk_test_not_real",
    "CHERT_API_KEY": "ftk_test_not_real",
    "CHERT_PROJECT_ID": "0d1e2f3a-4b5c-4d6e-8f70-8192a3b4c5d6",
}


def by_name(checks, name):
    return [c for c in checks if c.name == name]


# ------------------------------------------------------------------ env parsing, placeholders, scrubbing

@pytest.mark.parametrize("value,placeholder", [
    ("", True), (None, True), ("YOUR_LIVEKIT_API_KEY", True), ("wss://YOUR_PROJECT.livekit.cloud", True),
    ("onset-YOUR_SUFFIX", True), ("<your key>", True), ("changeme", True), ("onset-tt", False),
    ("wss://demo.livekit.cloud", False), ('"quoted"', False),
])
def test_is_placeholder(value, placeholder):
    assert doctor.is_placeholder(value) is placeholder


def test_parse_env_file_and_effective_env(tmp_path):
    f = tmp_path / ".env.local"
    f.write_text('# c\nA=1\nB="two words"\nexport C=3\nEMPTY=\n', encoding="utf-8")
    vals = doctor.parse_env_file(f)
    assert vals["A"] == "1" and vals["B"] == "two words" and vals["C"] == "3" and vals["EMPTY"] == ""
    assert doctor.effective_env(vals, {"A": "env wins", "B": ""})["A"] == "env wins"
    assert doctor.effective_env(vals, {"B": ""})["B"] == "two words"  # empty env values do not erase


def test_scrub_removes_secrets_emails_and_phones():
    env = {"LIVEKIT_API_SECRET": "supersecretvalue", "AGENT_NAME": "onset-tt"}
    out = doctor.scrub("secret supersecretvalue, me@example.com, line +1 (555) 123-0001, agent onset-tt", env)
    assert "supersecretvalue" not in out and "me@example.com" not in out and "123-0001" not in out
    assert "***" in out and "<account>" in out and "+1…01" in out and "onset-tt" in out


# ------------------------------------------------------------------ env file and settings

def test_env_file_missing_world_readable_and_ok(tmp_path):
    f = tmp_path / ".env.local"
    assert doctor.check_env_file(f).status == FAIL
    f.write_text("A=1\n")
    f.chmod(0o644)
    assert doctor.check_env_file(f).status == WARN
    f.chmod(0o600)
    assert doctor.check_env_file(f).status == PASS


def test_settings_from_the_example_file_fail_by_name():
    env = doctor.parse_env_file(REPO / ".env.example")
    checks = doctor.check_settings(env)
    st = {c.name: c.status for c in checks}
    assert st["livekit settings"] == FAIL and st["agent name"] == FAIL
    assert st["voice key"] == FAIL and st["scribe key"] == FAIL and st["chert api settings"] == WARN
    assert "onset-YOUR_SUFFIX" in by_name(checks, "agent name")[0].detail


def test_settings_all_good_and_never_print_values():
    checks = doctor.check_settings(GOOD)
    assert all(c.status == PASS for c in checks), checks
    text = " ".join(c.detail for c in checks)
    for k, v in GOOD.items():
        if k != "AGENT_NAME":
            assert v not in text


def test_settings_specific_problems():
    st = lambda env: {c.name: c for c in doctor.check_settings(env)}  # noqa: E731
    assert st({**GOOD, "LIVEKIT_URL": "https://x.livekit.cloud/path"})["livekit settings"].status == FAIL
    assert st({**GOOD, "AGENT_NAME": "bad name!"})["agent name"].status == FAIL
    assert st({**GOOD, "CHERT_API_KEY": "sk_wrong"})["chert api settings"].status == FAIL
    assert st({**GOOD, "CHERT_PROJECT_ID": "nope"})["chert api settings"].status == FAIL
    assert st({**GOOD, "CHERT_LINE_ID": "nope"})["chert api settings"].status == FAIL
    google_only = {k: v for k, v in GOOD.items() if k != "ELEVENLABS_API_KEY"} | {"GOOGLE_API_KEY": "AIzafake"}
    s = st(google_only)
    assert s["voice key"].status == PASS and s["scribe key"].status == FAIL


def test_packages_with_fake_importer():
    ok = doctor.check_packages(importer=lambda m: None, version=(3, 12))
    assert ok.status == PASS

    def broken(mod):
        if mod == "mediapipe":
            raise ImportError("nope")
    bad = doctor.check_packages(importer=broken, version=(3, 12))
    assert bad.status == FAIL and "mediapipe" in bad.detail
    assert doctor.check_packages(importer=lambda m: None, version=(3, 13)).status == WARN


def test_models(tmp_path):
    assert doctor.check_models(tmp_path, loader=lambda p: None).status == FAIL
    for n in doctor.MODEL_FILES:
        (tmp_path / n).write_bytes(b"x")
    assert doctor.check_models(tmp_path, loader=lambda p: None).status == PASS

    def bad(p):
        raise RuntimeError("corrupt")
    r = doctor.check_models(tmp_path, loader=bad)
    assert r.status == FAIL and "does not load" in r.detail
    assert doctor.models_dir({"ONSET_MODELS": str(tmp_path)}) == tmp_path
    assert doctor.models_dir({}, root=tmp_path) == tmp_path / "models"


# ------------------------------------------------------------------ voice

def test_warm_texts_cover_every_sentence_greeting_first():
    from onset import script
    from onset.speech import SENTENCES

    texts = doctor.warm_texts(doctor.candidate_sentences())
    assert texts[0] == script.GREETING.say
    assert len(texts) == len(set(texts))
    for s in SENTENCES:
        assert script.sentence_prompt(s).say in texts


def test_candidate_sentences_include_profiles_and_baselines():
    from onset.patients import Patient, Profiles

    prof = Profiles((Patient("a", sentence="Custom one."), Patient("b")), "a", "x.json")
    out = doctor.candidate_sentences(prof, lambda pid: "From the baseline." if pid == "b" else None)
    assert "Custom one." in out and "From the baseline." in out


class FakeTTS:
    model = "fake-model"
    voice = "fake-voice"


@pytest.fixture
def tts_cache(tmp_path, monkeypatch):
    from onset import voice

    monkeypatch.setattr(voice, "CACHE", tmp_path)
    return tmp_path


def test_tts_offline_reports_cache_state(tts_cache):
    from onset import voice

    texts = ["Hello there.", "Second."]
    r = doctor.check_tts(texts, offline=True, warm=False, make_tts=FakeTTS)
    assert r.status == WARN and "not cached" in r.detail
    path = voice.cache_path(voice.tts_identity(FakeTTS()), "Hello there.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    assert doctor.check_tts(texts, offline=True, warm=False, make_tts=FakeTTS).status == PASS


def test_tts_online_greeting_or_all_lines(tts_cache):
    asked = []

    async def synth(tts, todo, timeout):
        asked.append(list(todo))
        return len(todo), 0, 0.1, []
    texts = ["Hello there.", "Second.", "Third."]
    assert doctor.check_tts(texts, offline=False, warm=False, make_tts=FakeTTS, synth=synth).status == PASS
    assert doctor.check_tts(texts, offline=False, warm=True, make_tts=FakeTTS, synth=synth).status == PASS
    assert asked == [["Hello there."], texts]


def test_tts_failures(tts_cache):
    async def failing(tts, todo, timeout):
        return 0, 0, 0.1, ["'Hello there.': TimeoutError"]

    async def crash(tts, todo, timeout):
        raise RuntimeError("401 invalid key")

    def no_tts():
        raise ValueError("no key")
    assert doctor.check_tts(["Hello there."], offline=False, warm=False, make_tts=FakeTTS, synth=failing).status == FAIL
    assert doctor.check_tts(["Hello there."], offline=False, warm=False, make_tts=FakeTTS, synth=crash).status == FAIL
    assert doctor.check_tts(["Hello there."], offline=False, warm=False, make_tts=no_tts).status == FAIL


# ------------------------------------------------------------------ ElevenLabs, LiveKit

def fake_http(status, body, calls=None):
    def get(url, headers, timeout):
        if calls is not None:
            calls.append((url, dict(headers)))
        return status, json.dumps(body).encode() if not isinstance(body, bytes) else body
    return get


def test_elevenlabs_key_and_scribe_model():
    models = [{"model_id": "eleven_turbo_v2_5"}, {"model_id": "eleven_multilingual_v2"}]
    calls = []
    r = doctor.check_elevenlabs(GOOD, fake_http(200, models, calls), stt_model="scribe_v2")
    assert r.status == PASS and "scribe_v2" in r.detail
    assert calls[0][0] == "https://api.elevenlabs.io/v1/models"  # free: no transcription
    assert doctor.check_elevenlabs(GOOD, fake_http(200, models), stt_model="scribe_v9").status == WARN
    assert doctor.check_elevenlabs({**GOOD, "ELEVEN_MODEL": "nope"}, fake_http(200, models), stt_model="scribe_v2").status == WARN
    invalid = {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}
    assert doctor.check_elevenlabs(GOOD, fake_http(401, invalid), stt_model="scribe_v2").status == FAIL
    restricted = {"detail": {"status": "missing_permissions"}}
    assert doctor.check_elevenlabs(GOOD, fake_http(401, restricted), stt_model="scribe_v2").status == WARN
    assert doctor.check_elevenlabs({}, fake_http(200, models)).status == SKIP

    def down(url, headers, timeout):
        raise OSError("no route")
    assert doctor.check_elevenlabs(GOOD, down, stt_model="scribe_v2").status == WARN


def test_livekit_list_rooms():
    seen = []

    async def ok(url, key, secret):
        seen.append(url)
        return 2
    assert doctor.check_livekit(GOOD, ok).status == PASS
    assert seen == ["https://demo-abc.livekit.cloud"]

    async def bad(url, key, secret):
        raise RuntimeError("401 unauthorized")
    r = doctor.check_livekit(GOOD, bad)
    assert r.status == FAIL and "wrong key" in r.detail
    assert doctor.check_livekit({}, ok).status == SKIP


# ------------------------------------------------------------------ Chert CLI doctor

CHERT_OUT = json.dumps({"ok": True, "checks": [
    {"name": "session", "status": "pass", "detail": "Signed in as someone@example.com; 29 days left."},
    {"name": "clock", "status": "fail", "detail": "Clock is 1.0 s ahead.", "fix": "Sync the clock."},
    {"name": "project", "status": "pass", "detail": "Someone's project"},
    {"name": "line", "status": "pass", "detail": "Line Name (+1 555-123-0099) is available."},
    {"name": "worker", "status": "pass", "detail": "checked in 3 s ago"},
    {"name": "line_free", "status": "fail", "detail": "Another session holds the line."},
    {"name": "minutes", "status": "warn", "detail": "Trial: 4 minutes left."},
]}) + "\nnot json\n"


def test_chert_doctor_parsing_hides_account_and_masks_phone():
    parsed = doctor.parse_chert_doctor(CHERT_OUT)
    checks = doctor.chert_checks(parsed)
    names = [c.name for c in checks]
    assert names == ["chert session", "chert clock", "chert line", "chert worker", "chert line_free", "chert minutes"]
    text = doctor.scrub(" ".join(c.detail for c in checks))
    assert "example.com" not in text and "Someone" not in text
    assert "555-123-0099" not in text and "+1…99" in text
    st = {c.name: c.status for c in checks}
    assert st["chert clock"] == FAIL and st["chert line_free"] == FAIL and st["chert minutes"] == WARN
    assert "hwclock" in by_name(checks, "chert clock")[0].detail
    assert doctor.line_number(parsed) == "+1 555-123-0099"


def test_chert_doctor_no_output_is_a_fail():
    assert doctor.chert_checks(doctor.parse_chert_doctor(""))[0].status == FAIL


# ------------------------------------------------------------------ line readiness

def lines_body(*lines):
    return {"data": {"items": list(lines)}}


def line(lid="11111111-2222-4333-8444-555555555555", audio=True, video=True, name="Demo line"):
    return {"id": lid, "display_name": name, "status": "available",
            "session_health": {"status": "accepting",
                               "readiness": {"audio_ready": audio, "video_ready": video, "facetime_signed_in": True}}}


def test_line_readiness_parsing():
    assert doctor.line_readiness(lines_body(line()))[0].status == PASS
    silent = doctor.line_readiness(lines_body(line(audio=False)))[0]
    assert silent.status == FAIL and "docs/chert-audio.md" in silent.detail and "audio_ready false" in silent.detail
    assert doctor.line_readiness(lines_body(line(video=False)))[0].status == WARN
    assert doctor.line_readiness(lines_body(line(audio=None)))[0].status == FAIL
    assert doctor.line_readiness(lines_body())[0].status == FAIL
    assert doctor.line_readiness({"weird": 1})[0].status == WARN
    masked = doctor.line_readiness(lines_body(line(name="Kim (+15551230099)")))[0].detail
    assert "5551230099" not in masked


def test_video_framing_fill_needs_an_upright_picture():
    """Issue #27: a "fill" line crops a wide picture to its middle third, captions included."""
    fill, fit = dict(line(), video_framing="fill"), dict(line(), video_framing="fit")
    names = lambda checks: [(c.name, c.status) for c in checks]  # noqa: E731
    assert names(doctor.line_readiness(lines_body(line()), None, (540, 960))) == [("line readiness", PASS)]
    assert names(doctor.line_readiness(lines_body(fit), None, (1280, 720)))[1] == ("video framing", PASS)
    assert names(doctor.line_readiness(lines_body(fill), None, (540, 960)))[1] == ("video framing", PASS)
    wide = doctor.line_readiness(lines_body(fill), None, (1280, 720))[1]
    assert (wide.name, wide.status) == ("video framing", FAIL) and "1280x720" in wide.detail
    assert doctor.line_readiness(lines_body(fill), None, None)[1].status == FAIL
    assert doctor.line_readiness(lines_body(dict(line(), video_framing="stretch")), None, (540, 960))[1].status == WARN
    out = doctor.check_line_readiness(dict(GOOD, ONSET_FRAME="landscape", OUT_WIDTH="640", OUT_HEIGHT="360"),
                                      fake_http(200, lines_body(fill)), session=lambda env: None)
    assert [c.status for c in out] == [PASS, FAIL]
    out = doctor.check_line_readiness(GOOD, fake_http(200, lines_body(fill)), session=lambda env: None)
    assert [c.status for c in out] == [PASS, PASS]


def test_line_readiness_pinned_and_multiple_lines():
    a, b = line("aaaaaaaa-2222-4333-8444-555555555555"), line("bbbbbbbb-2222-4333-8444-555555555555", audio=False)
    both = doctor.line_readiness(lines_body(a, b))
    assert [c.status for c in both] == [PASS, FAIL, WARN]
    assert [c.status for c in doctor.line_readiness(lines_body(a, b), a["id"])] == [PASS]
    assert doctor.line_readiness(lines_body(a), "cccccccc-2222-4333-8444-555555555555")[0].status == FAIL


def test_line_readiness_sends_the_key_only_to_chert():
    calls = []
    out = doctor.check_line_readiness(GOOD, fake_http(200, lines_body(line()), calls), session=lambda env: None)
    assert out[0].status == PASS
    url, headers = calls[0]
    assert url.startswith("https://app.trychert.com/api/v1/lines")
    assert headers["Authorization"] == f"Bearer {GOOD['CHERT_API_KEY']}"
    assert headers["X-Chert-Project-Id"] == GOOD["CHERT_PROJECT_ID"]
    assert doctor.check_line_readiness(GOOD, fake_http(401, {}), session=lambda env: None)[0].status == FAIL


def test_line_readiness_falls_back_to_the_cli_sign_in(tmp_path):
    assert doctor.check_line_readiness({}, fake_http(200, {}), session=lambda env: None)[0].status == SKIP
    calls = []
    sess = ("cli-token-" + "x" * 30, "99999999-8888-4777-8666-555555555555")
    out = doctor.check_line_readiness({}, fake_http(200, lines_body(line()), calls), session=lambda env: sess)
    assert out[0].status == PASS and "CLI sign-in" in out[0].detail
    assert calls[0][1]["X-Chert-Project-Id"] == sess[1]
    (tmp_path / "credentials.json").write_text(json.dumps({"access_token": sess[0], "project_id": sess[1]}))
    assert doctor.cli_session({"CHERT_HOME": str(tmp_path)}) == sess
    assert doctor.cli_session({"CHERT_HOME": str(tmp_path / "none")}) is None


# ------------------------------------------------------------------ clock (SNTP)

class FakeClock:
    def __init__(self, t=1_800_000_000.25):
        self.t = t

    def __call__(self):
        return self.t


class FakeNtpSocket:
    """Answers like an NTP server whose clock is `offset` s ahead of ours, `delay` s round trip."""

    def __init__(self, clock, offset, delay=0.02, echo=True, mode=4, stratum=2):
        self.clock, self.offset, self.delay, self.echo, self.mode, self.stratum = clock, offset, delay, echo, mode, stratum
        self.closed = False

    def settimeout(self, t):
        pass

    def sendto(self, data, addr):
        assert len(data) == 48 and addr[1] == 123 and data[0] & 0x7 == 3
        self.req = data

    def recvfrom(self, n):
        t1 = doctor.from_ntp(self.req[40:48])
        t2 = t1 + self.delay / 2 + self.offset
        t3 = t2 + 0.001
        self.clock.t = t1 + self.delay + 0.001
        originate = self.req[40:48] if self.echo else b"\0" * 8
        reply = bytes([0x20 | self.mode, self.stratum]) + b"\0" * 22 + originate + doctor.to_ntp(t2) + doctor.to_ntp(t3)
        return reply, ("127.0.0.1", 123)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("offset", [0.0, 0.3, -0.42, 0.0937])
def test_sntp_offset_math(offset):
    clock = FakeClock()
    socks = []

    def factory():
        socks.append(FakeNtpSocket(clock, offset))
        return socks[-1]
    off, delay = doctor.query_sntp("ntp.test", samples=2, sock_factory=factory, clock=clock)
    assert off == pytest.approx(offset, abs=1e-6)
    assert delay == pytest.approx(0.02, abs=1e-6)
    assert all(s.closed for s in socks) and len(socks) == 2


def test_sntp_rejects_bad_replies():
    clock = FakeClock()
    for kw in ({"echo": False}, {"mode": 3}, {"stratum": 0}):
        with pytest.raises(ValueError):
            doctor.query_sntp("ntp.test", samples=1, sock_factory=lambda kw=kw: FakeNtpSocket(clock, 0.0, **kw), clock=clock)
    with pytest.raises(ValueError):
        doctor.sntp_offset(b"\x24" * 10, b"\0" * 8, 0, 0)


def test_sntp_timeout_is_an_oserror():
    class Silent(FakeNtpSocket):
        def recvfrom(self, n):
            raise socket.timeout("timed out")
    clock = FakeClock()
    with pytest.raises(OSError):
        doctor.query_sntp("ntp.test", samples=2, sock_factory=lambda: Silent(clock, 0.0), clock=clock)


def test_clock_thresholds():
    q = lambda off: (lambda host: (off, 0.02))  # noqa: E731
    assert doctor.check_clock(q(0.05), ["a"]).status == PASS
    warn = doctor.check_clock(q(-0.15), ["a"])
    assert warn.status == WARN and "ahead of" in warn.detail
    fail = doctor.check_clock(q(-0.3), ["a"])
    assert fail.status == FAIL and "hwclock" in fail.detail and "wsl --shutdown" in fail.detail
    assert doctor.check_clock(q(0.26), ["a"]).status == FAIL  # behind counts too

    def down(host):
        raise OSError("blocked")
    assert doctor.check_clock(down, ["a", "b"]).status == WARN
    calls = []

    def second_works(host):
        calls.append(host)
        if host == "a":
            raise OSError("x")
        return 0.01, 0.01
    assert doctor.check_clock(second_works, ["a", "b"]).status == PASS and calls == ["a", "b"]


# ------------------------------------------------------------------ profiles, gate, escalation, port

def test_profiles_from_env_without_caregiver():
    out = doctor.check_profiles({}, root=Path("/nonexistent"))
    assert out[0].status == PASS and "environment" in out[0].detail
    assert by_name(out, "caregiver phone")[0].status == WARN
    out = doctor.check_profiles({"ONSET_CAREGIVER_PHONE": "+15551230002"}, root=Path("/nonexistent"))
    assert not by_name(out, "caregiver phone")


def test_profiles_from_files(tmp_path):
    good = tmp_path / "p.json"
    good.write_text((REPO / "config" / "patients.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    out = doctor.check_profiles({"ONSET_PATIENTS_FILE": str(good)}, root=tmp_path)
    assert out[0].status == PASS and "2 profiles" in out[0].detail and "pat-001" in out[0].detail
    assert any("one patient per line" in c.detail for c in out)
    assert not by_name(out, "caregiver phone")
    nodefault = tmp_path / "nd.json"
    nodefault.write_text(json.dumps({"patients": [{"id": "a"}, {"id": "b", "caregiver": {"phone": "+15551230002"}}]}))
    out = doctor.check_profiles({"ONSET_PATIENTS_FILE": "nd.json"}, root=tmp_path)  # relative to the repo
    assert out[0].status == WARN and "default" in out[0].detail
    assert "a" in by_name(out, "caregiver phone")[0].detail
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert doctor.check_profiles({"ONSET_PATIENTS_FILE": str(bad)}, root=tmp_path)[0].status == FAIL
    assert doctor.check_profiles({"ONSET_PATIENTS_FILE": str(tmp_path / "gone.json")}, root=tmp_path)[0].status == FAIL


def test_safety_gate():
    assert doctor.check_gate({}).status == PASS
    assert "DRY RUN" in doctor.check_gate({"ONSET_LIVE_ACTIONS": "0"}).detail
    live_no_chert = doctor.check_gate({"ONSET_LIVE_ACTIONS": "1"})
    assert live_no_chert.status == FAIL
    live = doctor.check_gate({**GOOD, "ONSET_LIVE_ACTIONS": "1", "ONSET_LIVE_ACTIONS_DENY": "call"})
    assert live.status == WARN and "LIVE" in live.detail and "call" in live.detail and "join" in live.detail


def test_escalation_settings():
    st = lambda env: [c.status for c in doctor.check_escalation(env)]  # noqa: E731
    assert st({}) == [WARN]  # link + talk, but no caregiver page: Meet is watch-only
    assert st({"ONSET_CAREGIVER_TALK": "0"}) == [PASS]
    assert st({"ONSET_CAREGIVER_PAGE": "https://me.github.io/onset/caregiver/"}) == [PASS]
    assert st({"ONSET_CAREGIVER_PAGE": "http://insecure.example/"}) == [FAIL]
    assert st({"ONSET_CAREGIVER_JOIN": "off"}) == [PASS]
    assert st({"ONSET_CAREGIVER_JOIN": "LINK", "ONSET_CAREGIVER_TALK": "0"}) == [PASS]
    ft = doctor.check_escalation({"ONSET_CAREGIVER_JOIN": "facetime"})
    assert ft[0].status == FAIL and "cannot add anyone" in ft[0].detail
    assert st({"ONSET_CAREGIVER_JOIN": "both"}) == [FAIL]
    assert FAIL in st({"ONSET_CAREGIVER_LINK_TTL_S": "abc", "ONSET_CAREGIVER_TALK": "0"})
    assert FAIL in st({"ONSET_CAREGIVER_LINK_TTL_S": "5", "ONSET_CAREGIVER_TALK": "0"})


def test_caregiver_page_fetch():
    env = {"ONSET_CAREGIVER_PAGE": "https://me.github.io/onset/caregiver/#ignored"}
    calls = []
    assert doctor.check_caregiver_page(env, fake_http(200, b"<html>", calls)).status == PASS
    assert calls[0][0] == "https://me.github.io/onset/caregiver/"
    assert doctor.check_caregiver_page(env, fake_http(404, b"")).status == FAIL
    assert doctor.check_caregiver_page({}, fake_http(200, b"")).status == SKIP
    assert doctor.check_caregiver_page({**env, "ONSET_CAREGIVER_JOIN": "off"}, fake_http(200, b"")).status == SKIP


def test_port_free_and_busy():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert doctor.check_port(port).status == WARN
    finally:
        s.close()
    assert doctor.check_port(port).status == PASS


# ------------------------------------------------------------------ output and exit code

def test_json_render_and_exit_code():
    checks = [Check(PASS, "a", "fine"), Check(WARN, "b", "hmm"), Check(SKIP, "c", "offline")]
    data = json.loads(doctor.to_json(checks))
    assert data["ok"] is True and data["summary"] == {"pass": 1, "warn": 1, "fail": 0, "skip": 1}
    assert data["checks"][1] == {"status": WARN, "name": "b", "detail": "hmm"}
    assert doctor.exit_code(checks) == 0
    checks.append(Check(FAIL, "d", "broken"))
    assert doctor.exit_code(checks) == 1 and json.loads(doctor.to_json(checks))["ok"] is False
    text = doctor.render(checks, color=False)
    assert text.splitlines()[3].startswith("FAIL  d") and "1 fail" in text.splitlines()[-1]


def test_main_json_exit_code_skip_clock_and_line_file(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "env"
    env_file.write_text("DOCTOR_TEST_SECRET_KEY=topsecretvalue\n")
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k != "DOCTOR_TEST_SECRET_KEY"})
    monkeypatch.chdir(tmp_path)  # main() moves to the repo; the test's cwd is put back afterwards
    seen = {}

    def fake_run(args, env, root=doctor.ROOT):
        seen["args"], seen["env"] = args, env
        checks = [Check(FAIL, "clock (ntp)", "off by topsecretvalue"), Check(PASS, "x", "ok")]
        if args.skip_clock:
            checks[0] = Check(WARN, "clock (ntp)", "off (--skip-clock)")
        return [Check(c.status, c.name, doctor.scrub(c.detail, env)) for c in checks], {"line_number": "+1 555 123 0099"}
    monkeypatch.setattr(doctor, "run_checks", fake_run)
    line_file = tmp_path / "line"
    rc = doctor.main(["--json", "--offline", "--env-file", str(env_file), "--line-file", str(line_file)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and "topsecretvalue" not in json.dumps(out)
    assert seen["args"].offline and seen["env"]["DOCTOR_TEST_SECRET_KEY"] == "topsecretvalue"
    assert line_file.read_text().strip() == "+1 555 123 0099" and (line_file.stat().st_mode & 0o077) == 0
    assert doctor.main(["--skip-clock", "--env-file", str(env_file)]) == 0


def test_skip_clock_downgrades_only_clock_fails(monkeypatch):
    # run_checks' own downgrade, without any real check running
    import argparse

    monkeypatch.setattr(doctor, "check_env_file", lambda p: Check(PASS, "env file", "ok"))
    monkeypatch.setattr(doctor, "check_settings", lambda env: [])
    monkeypatch.setattr(doctor, "check_packages", lambda: Check(PASS, "python packages", "ok"))
    monkeypatch.setattr(doctor, "check_profiles", lambda env, root: [])
    monkeypatch.setattr(doctor, "check_gate", lambda env: Check(FAIL, "safety gate", "live without chert"))
    monkeypatch.setattr(doctor, "check_escalation", lambda env: [])
    monkeypatch.setattr(doctor, "check_port", lambda port: Check(PASS, "dashboard port", "free"))
    monkeypatch.setattr(doctor, "check_models", lambda d: Check(PASS, "mediapipe models", "ok"))
    monkeypatch.setattr(doctor, "check_tts", lambda *a, **k: Check(PASS, "voice (tts)", "ok"))
    monkeypatch.setattr(doctor, "run_chert_doctor", lambda root: CHERT_OUT)
    monkeypatch.setattr(doctor, "check_line_readiness", lambda env: [Check(PASS, "line readiness", "ok")])
    monkeypatch.setattr(doctor, "check_clock", lambda: Check(FAIL, "clock (ntp)", "0.4 s ahead"))
    monkeypatch.setattr(doctor, "check_livekit", lambda env: Check(PASS, "livekit reachable", "ok"))
    monkeypatch.setattr(doctor, "check_elevenlabs", lambda env: Check(PASS, "elevenlabs key", "ok"))
    monkeypatch.setattr(doctor, "check_caregiver_page", lambda env: Check(SKIP, "caregiver page", "none"))
    args = argparse.Namespace(env_file="x", port=1, offline=False, warm=False, skip_clock=True)
    checks, facts = doctor.run_checks(args, {})
    st = {c.name: c.status for c in checks}
    assert st["clock (ntp)"] == WARN and st["chert clock"] == WARN
    assert st["safety gate"] == FAIL and st["chert line_free"] == FAIL
    assert facts["line_number"] == "+1 555-123-0099"
    assert "example.com" not in json.dumps([c._asdict() for c in checks])
    args.skip_clock, args.offline = False, True
    checks, _ = doctor.run_checks(args, {})
    st = {c.name: c.status for c in checks}
    assert st["clock (ntp)"] == SKIP and "chert clock" not in st
