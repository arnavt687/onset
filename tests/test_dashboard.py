"""The demo dashboard server: SSE tail, frame and audio endpoints.

Uses aiohttp's own test client inside asyncio.run(), so no pytest plugin is needed.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tools import dashboard
from tools.dashboard import make_app


def run(coro):
    return asyncio.run(coro)


async def _client(runs: Path, baselines: Path | None = None) -> TestClient:
    app = make_app(runs, baselines or runs / "baselines")
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class SSE:
    """Reads one Server-Sent Events response, event by event."""

    def __init__(self, resp) -> None:
        self.resp = resp
        self.buf = ""

    async def next(self, timeout: float = 3.0) -> tuple[str, str]:
        async def read() -> tuple[str, str]:
            while True:
                if "\n\n" in self.buf:
                    block, self.buf = self.buf.split("\n\n", 1)
                    event, data = "message", []
                    for line in block.split("\n"):
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: "):
                            data.append(line[6:])
                    if not data:  # retry: / heartbeat comments
                        continue
                    return event, "\n".join(data)
                chunk = await self.resp.content.readany()
                if not chunk:
                    raise EOFError
                self.buf += chunk.decode()

        return await asyncio.wait_for(read(), timeout)

    async def until(self, event: str, timeout: float = 3.0) -> list[tuple[str, str]]:
        seen = []
        while True:
            ev = await self.next(timeout)
            seen.append(ev)
            if ev[0] == event:
                return seen


def _write(path: Path, *events: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def _live(tmp_path: Path) -> Path:
    live = tmp_path / "live"
    (live / "audio").mkdir(parents=True)
    return live


def test_sse_sends_existing_then_appended_lines(tmp_path):
    live = _live(tmp_path)
    _write(live / "events.jsonl", {"t": 0, "type": "run_started", "run_id": "r1"}, {"t": 1, "type": "step", "name": "greet"})

    async def go():
        client = await _client(tmp_path)
        try:
            resp = await client.get("/events")
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            sse = SSE(resp)
            first = await sse.until("caught_up")
            assert first[0][0] == "reset" and json.loads(first[0][1]) == {"exists": True, "live": False}
            data = [json.loads(d) for e, d in first if e == "message"]
            assert [d["type"] for d in data] == ["run_started", "step"]

            # A half-written line waits for its newline; a bad line is skipped.
            with open(live / "events.jsonl", "a", encoding="utf-8") as fh:
                fh.write("not json\n")
                fh.write('{"t": 2, "type": "say", ')
                fh.flush()
                await asyncio.sleep(0.3)
                fh.write('"text": "hi", "audio": null}\n')
            ev, d = await sse.next()
            assert ev == "message" and json.loads(d)["type"] == "say" and json.loads(d)["text"] == "hi"
        finally:
            await client.close()

    run(go())


def test_sse_survives_file_recreation(tmp_path):
    live = _live(tmp_path)
    _write(live / "events.jsonl", {"t": 0, "type": "run_started", "run_id": "old"}, {"t": 5, "type": "step", "name": "arms_hold"})

    async def go():
        client = await _client(tmp_path)
        try:
            sse = SSE(await client.get("/events"))
            await sse.until("caught_up")
            # A new run: EventSink deletes runs/live and starts a new, shorter file.
            shutil.rmtree(live)
            await asyncio.sleep(0.3)
            (live / "audio").mkdir(parents=True)
            _write(live / "events.jsonl", {"t": 0, "type": "run_started", "run_id": "new"})
            seen = await sse.until("message")
            resets = [json.loads(d) for e, d in seen if e == "reset"]
            assert resets and all(r["live"] for r in resets)
            assert json.loads(seen[-1][1])["run_id"] == "new"
            _write(live / "events.jsonl", {"t": 1, "type": "step", "name": "greet"})
            ev, d = await sse.next()
            assert ev == "message" and json.loads(d)["name"] == "greet"
        finally:
            await client.close()

    run(go())


def test_sse_waits_for_a_file_that_does_not_exist_yet(tmp_path):
    async def go():
        client = await _client(tmp_path)
        try:
            sse = SSE(await client.get("/events"))
            seen = await sse.until("caught_up")
            assert json.loads(seen[0][1])["exists"] is False
            live = _live(tmp_path)
            _write(live / "events.jsonl", {"t": 0, "type": "run_started", "run_id": "r"})
            seen = await sse.until("message")
            assert ("reset", json.dumps({"exists": True, "live": True})) in seen
            assert json.loads(seen[-1][1])["run_id"] == "r"
        finally:
            await client.close()

    run(go())


def test_sse_survives_truncation_in_place(tmp_path):
    live = _live(tmp_path)
    _write(live / "events.jsonl", *({"t": i, "type": "live", "step": "arms_hold", "metrics": {}} for i in range(20)))

    async def go():
        client = await _client(tmp_path)
        try:
            sse = SSE(await client.get("/events"))
            await sse.until("caught_up")
            (live / "events.jsonl").write_text(json.dumps({"t": 0, "type": "run_started", "run_id": "t"}) + "\n")
            seen = await sse.until("message")
            assert any(e == "reset" for e, _ in seen)
            assert json.loads(seen[-1][1])["run_id"] == "t"
        finally:
            await client.close()

    run(go())


def test_audio_serves_wav_and_refuses_path_traversal(tmp_path):
    live = _live(tmp_path)
    (live / "audio" / "001.wav").write_bytes(b"RIFF....WAVEfmt ")
    (live / "events.jsonl").write_text("")
    (live / "secret.wav").write_bytes(b"outside audio/")
    (tmp_path / "top.wav").write_bytes(b"outside live/")
    (live / "audio" / "notes.txt").write_text("not audio")

    async def go():
        client = await _client(tmp_path)
        try:
            ok = await client.get("/audio/001.wav")
            assert ok.status == 200 and await ok.read() == b"RIFF....WAVEfmt "
            assert ok.headers["Content-Type"].startswith("audio/")
            for bad in ("/audio/../secret.wav", "/audio/..%2Fsecret.wav", "/audio/%2E%2E%2Fsecret.wav",
                        "/audio/..%2F..%2Ftop.wav", "/audio/notes.txt", "/audio/missing.wav",
                        "/audio/%2Fetc%2Fpasswd", "/audio/..%2F", "/audio/.wav", "/audio/%00.wav"):
                r = await client.get(bad)
                assert r.status == 404, bad
                assert b"outside" not in await r.read(), bad
        finally:
            await client.close()

    run(go())


def test_audio_refuses_symlink_out_of_audio(tmp_path):
    live = _live(tmp_path)
    (tmp_path / "private.wav").write_bytes(b"outside")
    (live / "audio" / "002.wav").symlink_to(tmp_path / "private.wav")

    async def go():
        client = await _client(tmp_path)
        try:
            r = await client.get("/audio/002.wav")
            assert r.status == 404
        finally:
            await client.close()

    run(go())


def test_frame_is_never_cached(tmp_path):
    live = _live(tmp_path)

    async def go():
        client = await _client(tmp_path)
        try:
            missing = await client.get("/frame.jpg")
            assert missing.status == 404
            assert "no-store" in missing.headers["Cache-Control"]
            (live / "frame.jpg").write_bytes(b"\xff\xd8 first")
            r = await client.get("/frame.jpg")
            assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg"
            assert "no-store" in r.headers["Cache-Control"] and "no-cache" in r.headers["Cache-Control"]
            assert float(r.headers["X-Frame-Age"]) >= 0
            assert await r.read() == b"\xff\xd8 first"
            (live / "frame.jpg").write_bytes(b"\xff\xd8 second")
            r2 = await client.get("/frame.jpg")
            assert await r2.read() == b"\xff\xd8 second"
        finally:
            await client.close()

    run(go())


def test_page_config_and_baseline(tmp_path):
    from onset.decide import save_baseline
    from onset.types import Baseline

    bdir = tmp_path / "baselines"
    save_baseline(Baseline("p1", "2026-11-10T10:00:00+00:00", 0.05, 0.02, 0.0, 140.0, "The sky is blue."), base_dir=bdir)

    async def go():
        client = await _client(tmp_path, bdir)
        try:
            page = await client.get("/")
            assert page.status == 200 and "Click to enable sound" in await page.text()
            cfg = await (await client.get("/config")).json()
            assert cfg["thresholds"]["face_asymmetry_increase"] > 0
            b = await client.get("/baseline/p1")
            assert b.status == 200
            body = await b.json()
            assert body["face_asymmetry"] == 0.05 and "sentence" not in body and "patient_id" not in body
            assert (await client.get("/baseline/nobody")).status == 404
            assert (await client.get("/baseline/..%2Fp1")).status == 404
        finally:
            await client.close()

    run(go())


def test_presentation_is_served_with_what_it_borrows(tmp_path, monkeypatch):
    """/present: the presentation, the pitch deck's three.js and fonts, the browser demo; nothing outside web/."""
    monkeypatch.setenv("ONSET_LINE_NUMBER", " +1 555 010 0199 ")

    async def go():
        client = await _client(tmp_path)
        try:
            r = await client.get("/present", allow_redirects=False)
            assert r.status == 302 and r.headers["Location"] == "/web/present/index.html"
            page = await client.get("/present")
            text = await page.text()
            assert page.status == 200 and "Let's call it." in text and 'src="deck.js"' in text
            for path in ("/web/present/deck.js", "/web/present/world.js", "/web/present/style.css", "/web/present/qr-try.svg",
                         "/web/pitch/vendor/three/three.module.js", "/web/pitch/vendor/fonts/Inter.woff2",
                         "/web/try/index.html", "/web/brand/logo-glyph.svg"):
                assert (await client.get(path)).status == 200, path
            assert (await client.get("/web/")).status == 403  # no directory listing
            for path in ("/web/../tools/doctor.py", "/web/%2e%2e/.env.local", "/web/present/../../.env.local"):
                assert (await client.get(path)).status in (403, 404), path
            cfg = await (await client.get("/config")).json()
            assert cfg["line_number"] == "+1 555 010 0199"
        finally:
            await client.close()

    run(go())


def test_presentation_scenes_and_live_demo_hooks():
    """The deck is built around the demo: seven scenes, the live call in the middle, opened by run_started."""
    root = Path(dashboard.WEB_DIR) / "present"
    html, deck, world = ((root / n).read_text(encoding="utf-8") for n in ("index.html", "deck.js", "world.js"))
    ids = ["cover", "problem", "measure", "demo", "escalate", "how", "close"]
    assert [html.index(f'id="s-{i}"') for i in ids] == sorted(html.index(f'id="s-{i}"') for i in ids)
    assert ids.index("demo") == len(ids) // 2
    assert all(f"{i}:" in world for i in ids) and "run_started" in deck and "EventSource('/events')" in deck
    # voice: whole phrases only, off until asked for, and it says what it needs
    assert "SpeechRecognition" in deck and "next slide" in deck and "store.get('onset.voice')" in deck
    assert 'id="voice"' in html and "internet connection" in (root / "README.md").read_text(encoding="utf-8")
    # the microphone is picked on the page (AirPods), and an earbud press is a clicker
    assert 'id="voice-mic"' in html and "getUserMedia" in deck and "setActionHandler" in deck and "nexttrack" in deck
    # what we sent back to Chert is a second step of "Under the hood", so the demo stays in the middle
    assert "We hit Chert's limits." in html and "docs/chert-feedback.md" in html and "id: 'how', steps: 2" in deck
    assert "caregiver_call" in deck
    for gone in ("never diagnoses", "No LLM", "synthetic", "dry run"):  # wording the team dropped
        assert gone.lower() not in html.lower(), gone


FAKE_LINK = "https://example.github.io/onset/caregiver/#u=wss%3A%2F%2Fx.livekit.cloud&w=aaa.bbb.ccc&t=ddd.eee.fff&n=Ana&e=1800000000"


def test_caregiver_link_is_served_to_this_machine_only_and_never_cached(tmp_path):
    live = _live(tmp_path)

    async def go():
        client = await _client(tmp_path)
        try:
            missing = await client.get("/caregiver-link")
            assert missing.status == 404
            assert "no-store" in missing.headers["Cache-Control"]
            (live / "caregiver_link.txt").write_text(FAKE_LINK + "\n")
            (live / "caregiver_link.txt").chmod(0o600)
            r = await client.get("/caregiver-link")
            assert r.status == 200 and await r.text() == FAKE_LINK
            assert r.headers["Content-Type"].startswith("text/plain")
            assert "no-store" in r.headers["Cache-Control"]
            assert r.headers["Referrer-Policy"] == "no-referrer"
            # A DNS-rebinding page reaches 127.0.0.1 but sends its own Host name.
            evil = await client.get("/caregiver-link", headers={"Host": "evil.example:8765"})
            assert evil.status == 403 and FAKE_LINK not in await evil.text()
            ok_host = await client.get("/caregiver-link", headers={"Host": "localhost:8765"})
            assert ok_host.status == 200
        finally:
            await client.close()

    run(go())


def test_caregiver_link_refuses_other_peers(tmp_path):
    from unittest import mock

    from aiohttp.test_utils import make_mocked_request

    live = _live(tmp_path)
    (live / "caregiver_link.txt").write_text(FAKE_LINK)
    app = make_app(tmp_path, tmp_path / "baselines")

    async def go():
        for peer, host, ok in ((("127.0.0.1", 5000), "127.0.0.1:8765", True), (("::1", 5000, 0, 0), "[::1]:8765", True),
                               (("192.168.1.20", 5000), "127.0.0.1:8765", False), (("10.0.0.5", 5000), "localhost", False)):
            transport = mock.Mock()
            transport.get_extra_info.side_effect = lambda key, default=None, peer=peer: peer if key == "peername" else default
            req = make_mocked_request("GET", "/caregiver-link", headers={"Host": host}, app=app, transport=transport)
            if ok:
                resp = await dashboard.caregiver_link(req)
                assert resp.status == 200 and resp.text == FAKE_LINK
            else:
                with pytest.raises(dashboard.web.HTTPForbidden):
                    await dashboard.caregiver_link(req)

    run(go())


def test_caregiver_link_refuses_symlinks_and_junk(tmp_path):
    live = _live(tmp_path)
    (tmp_path / "elsewhere.txt").write_text(FAKE_LINK)

    async def go():
        client = await _client(tmp_path)
        try:
            (live / "caregiver_link.txt").symlink_to(tmp_path / "elsewhere.txt")
            assert (await client.get("/caregiver-link")).status == 404
            (live / "caregiver_link.txt").unlink()
            (live / "caregiver_link.txt").write_text("javascript:alert(1)")
            assert (await client.get("/caregiver-link")).status == 404
            (live / "caregiver_link.txt").write_text("https://x/" + "a" * 20000)
            assert (await client.get("/caregiver-link")).status == 404
        finally:
            await client.close()

    run(go())


def test_caregiver_link_never_reaches_the_event_stream(tmp_path):
    live = _live(tmp_path)
    (live / "caregiver_link.txt").write_text(FAKE_LINK)
    _write(live / "events.jsonl", {"t": 0, "type": "run_started", "run_id": "r"},
           {"t": 1, "type": "action", "kind": "invite", "live": False, "status": "dry_run", "target": "+1…02", "detail": "link composed"},
           {"t": 2, "type": "caregiver_join", "status": "invited", "detail": ""},
           {"t": 3, "type": "run_ended", "reason": "complete"},
           {"t": 40, "type": "caregiver_join", "status": "talking", "detail": ""})

    async def go():
        client = await _client(tmp_path)
        try:
            seen = await SSE(await client.get("/events")).until("caught_up")
            body = "\n".join(d for _, d in seen)
            assert "caregiver_join" in body and "aaa.bbb.ccc" not in body and "caregiver_link" not in body
            assert '"talking"' in body.split("run_ended", 1)[1]  # caregiver events can follow run_ended
        finally:
            await client.close()

    run(go())


def test_page_renders_actions_presence_and_caregiver_button(tmp_path):
    async def go():
        client = await _client(tmp_path)
        try:
            html = await (await client.get("/")).text()
            for needle in ('case "action"', 'case "caregiver_join"', "DRY RUN", "LIVE", "Open caregiver view",
                           'fetch("/caregiver-link"', "invited", "joined", "talking", "noopener", "no caregiver phone"):
                assert needle in html, needle
        finally:
            await client.close()

    run(go())


def test_server_binds_localhost_only():
    import inspect

    src = inspect.getsource(dashboard.main)
    assert 'host="127.0.0.1"' in src


def test_demo_events_writes_a_full_run(tmp_path):
    from tools import demo_events

    run_ = demo_events.Run("escalate", tmp_path, speed=200.0, caregiver="dry_run")
    try:
        run_.play(hold_end=0.0)
    finally:
        run_.close(keep_archive=False)
    lines = [json.loads(x) for x in (tmp_path / "live" / "events.jsonl").read_text().splitlines()]
    types = {e["type"] for e in lines}
    assert {"run_started", "step", "say", "live", "result", "decision", "caregiver", "run_ended"} <= types
    decision = next(e for e in lines if e["type"] == "decision")
    assert decision["outcome"] == "escalate" and decision["reasons"]
    says = [e for e in lines if e["type"] == "say"]
    assert all((tmp_path / "live" / e["audio"]).is_file() for e in says)
    assert (tmp_path / "live" / "frame.jpg").is_file()
    assert not (tmp_path / run_.run_id).exists()
