"""The caregiver's join page (web/caregiver/): static checks, plus the link parser under Node if present.

The page gets LiveKit tokens in its URL fragment, so these checks pin what it may do with them:
the only network peer is the LiveKit URL, the fragment leaves the address bar at once, and the
LiveKit client is a pinned, integrity-checked CDN build.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "caregiver"
INDEX = (PAGE / "index.html").read_text(encoding="utf-8")
APP = (PAGE / "app.js").read_text(encoding="utf-8")
LINK = (PAGE / "link.js").read_text(encoding="utf-8")
CSS = (PAGE / "style.css").read_text(encoding="utf-8")


def _scripts(html: str) -> list[str]:
    return re.findall(r"<script\b([^>]*)>", html)


def test_livekit_client_is_pinned_and_integrity_checked():
    tags = [t for t in _scripts(INDEX) if "livekit-client" in t]
    assert len(tags) == 1
    m = re.search(r'src="https://cdn\.jsdelivr\.net/npm/livekit-client@(\d+\.\d+\.\d+)/dist/livekit-client\.umd\.js"', tags[0])
    assert m, "livekit-client must be an exact x.y.z version on cdn.jsdelivr.net"
    assert re.search(r'integrity="sha384-[A-Za-z0-9+/=]{64}"', tags[0])
    assert 'crossorigin="anonymous"' in tags[0]


def test_only_local_scripts_and_styles_besides_livekit():
    for attrs in _scripts(INDEX):
        src = re.search(r'src="([^"]+)"', attrs)
        assert src, "no inline scripts (the CSP forbids them)"
        assert src.group(1) in ("link.js", "app.js") or src.group(1).startswith("https://cdn.jsdelivr.net/npm/livekit-client@")
    for href in re.findall(r'<link\b[^>]*href="([^"]+)"', INDEX):
        assert href == "style.css"
    assert "@import" not in CSS and "url(http" not in CSS


def test_csp_limits_connections_to_livekit():
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', INDEX)
    assert csp
    rules = dict((r.strip().split(" ", 1) + [""])[:2] for r in csp.group(1).split(";") if r.strip())
    assert rules["default-src"] == "'none'"
    assert rules["script-src"] == "'self' https://cdn.jsdelivr.net"
    hosts = rules["connect-src"].split()
    assert "wss://*.livekit.cloud" in hosts
    for h in hosts:
        assert h.endswith(".livekit.cloud") or re.match(r"^(ws|http)://(localhost|127\.0\.0\.1):\*$", h), h
    assert '<meta name="referrer" content="no-referrer">' in INDEX


def test_no_request_carries_the_tokens_except_the_livekit_connect():
    for name, src in (("app.js", APP), ("link.js", LINK)):
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "new WebSocket", "EventSource", "new Image",
                       "importScripts", "console.", "localStorage", "document.cookie", "postMessage", "window.open"):
            assert banned not in src, f"{name} uses {banned}"
    # The keys go into LiveKit connect calls and nowhere else: the watch room, the talk room, and
    # a short visit to the talk room to report that the microphone is blocked.
    assert re.findall(r"\.connect\(link\.url, ([\w.]+)", APP) == ["link.watch", "talkToken", "talkToken"]


def test_fragment_is_wiped_from_the_address_bar_first():
    i_replace = APP.index("history.replaceState(")
    assert i_replace < APP.index("OnsetLink.parse(")
    assert "location.pathname + location.search" in APP
    order = [m.group(1) for m in re.finditer(r'<script src="([^"]+)"', INDEX)]
    assert order[:2] == ["link.js", "app.js"], "the link is read and hidden before the CDN script loads"


def test_page_basics():
    assert 'href="tel:911"' in INDEX
    assert "playsinline" in INDEX and "Tap to join the call" in INDEX
    assert 'name="viewport"' in INDEX
    for state in ("missing", "bad", "expired", "connecting", "reconnecting", "ended", "Waiting for Onset"):
        assert state in APP, state
    # Contract with the agent (agent.py / onset/caregiver.py).
    for key in ("onset.summary", "onset.outcome", "onset.relay", "onset.patient", '"onset-video"', "chert-bridge-", 'topic !== "onset"'):
        assert key in APP, key
    # Issue #30: one tap joins and talks; the talk key arrives in the call; problems are reported.
    for key in ('msg.type === "talk"', "prepareMedia()", "maybeTalk()", '"onset-cg"', '"mic_denied"', '"cut"'):
        assert key in APP, key
    # Watch room never publishes; the talk room never subscribes.
    assert "autoSubscribe: false" in APP
    assert "echoCancellation: true" in APP and "noiseSuppression: true" in APP


def test_agent_audio_is_muted_while_relaying():
    assert re.search(r'audio\[data-from=\'agent\'\]', APP)
    assert 'els[i].muted = S.relay === "on"' in APP


def test_pages_workflow_publishes_only_the_caregiver_page():
    wf = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in wf and "web/caregiver/**" in wf
    assert "actions/upload-pages-artifact@" in wf and "actions/deploy-pages@" in wf
    assert "cp -R web/caregiver/. _site/caregiver/" in wf
    assert "path: _site" in wf


NODE_CASES = r"""
const L = require(process.argv[1]);
const now = 1800000000;
const jwt = (claims) => "eyJhbGciOiJIUzI1NiJ9." + Buffer.from(JSON.stringify(claims)).toString("base64url") + ".c2ln";
const W = jwt({ exp: now + 600, video: { room: "r" } }), T = jwt({ exp: now + 600 });
const u = encodeURIComponent("wss://onset-abc.livekit.cloud");
const out = {
  empty: L.parse("", now),
  hashOnly: L.parse("#", now),
  junk: L.parse("#hello", now),
  noToken: L.parse("#u=" + u + "&e=" + (now + 60), now),
  badUrl: L.parse("#u=" + encodeURIComponent("https://evil.example") + "&w=" + W + "&e=" + (now + 60), now),
  wsRemote: L.parse("#u=" + encodeURIComponent("ws://onset.example") + "&w=" + W + "&e=" + (now + 60), now),
  wsLocal: L.parse("#u=" + encodeURIComponent("ws://localhost:7880") + "&w=" + W + "&e=" + (now + 60), now),
  badJwt: L.parse("#u=" + u + "&w=not-a-jwt&e=" + (now + 60), now),
  expired: L.parse("#u=" + u + "&w=" + W + "&e=" + (now - 1), now),
  good: L.parse("#u=" + u + "&w=" + W + "&t=" + T + "&n=" + encodeURIComponent("Ana María") + "&e=" + (now + 60), now),
  noTalk: L.parse("u=" + u + "&w=" + W + "&e=" + (now + 60), now),
  badTalk: L.parse("#u=" + u + "&w=" + W + "&t=x&e=" + (now + 60), now),
  expFromJwt: L.parse("#u=" + u + "&w=" + W, now),
  expFromJwtExpired: L.parse("#u=" + u + "&w=" + W, now + 601),
  noExp: L.parse("#u=" + u + "&w=" + jwt({ sub: "x" }), now),
  htmlName: L.parse("#u=" + u + "&w=" + W + "&n=" + encodeURIComponent("<img src=x onerror=alert(1)>Bob") + "&e=" + (now + 60), now),
  longName: L.cleanName("A".repeat(100)).length,
  short: L.parse("#c/onset-abc.livekit.cloud/" + W.length + "/" + W, now),
  shortLocal: L.parse("#c/ws:localhost:7880/" + W.length + "/" + W, now),
  shortCut: L.parse("#c/onset-abc.livekit.cloud/" + W.length + "/" + W.slice(0, -9), now),
  shortCutEarly: L.parse("#c/onset-abc.livekit.clo", now),
  shortLonger: L.parse("#c/onset-abc.livekit.cloud/" + W.length + "/" + W + "x", now),
  shortExpired: L.parse("#c/onset-abc.livekit.cloud/" + W.length + "/" + W, now + 601),
  shortBadHost: L.parse("#c/evil.example@x/" + W.length + "/" + W, now),
};
process.stdout.write(JSON.stringify(out));
"""


def test_link_parser_under_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    res = subprocess.run([node, "-e", NODE_CASES, str(PAGE / "link.js")], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    r = json.loads(res.stdout)
    for k in ("empty", "hashOnly", "junk"):
        assert r[k] == {"ok": False, "error": "missing"}, k
    for k in ("noToken", "badUrl", "wsRemote", "badJwt", "noExp"):
        assert r[k] == {"ok": False, "error": "bad"}, k
    assert r["expired"]["error"] == "expired" and r["expFromJwtExpired"]["error"] == "expired"
    g = r["good"]
    assert g["ok"] and g["url"] == "wss://onset-abc.livekit.cloud" and g["name"] == "Ana María" and g["talk"]
    assert g["expires"] == 1800000060
    assert r["noTalk"]["ok"] and r["noTalk"]["talk"] is None
    assert r["badTalk"]["ok"] and r["badTalk"]["talk"] is None
    assert r["wsLocal"]["ok"] and r["wsLocal"]["url"] == "ws://localhost:7880"
    assert r["expFromJwt"]["ok"] and r["expFromJwt"]["expires"] == 1800000600
    # the short form: one key, its length first, so a cut link is refused instead of half-working
    s = r["short"]
    assert s["ok"] and s["url"] == "wss://onset-abc.livekit.cloud" and s["talk"] is None and s["expires"] == 1800000600
    assert r["shortLocal"]["ok"] and r["shortLocal"]["url"] == "ws://localhost:7880"
    assert r["shortCut"] == {"ok": False, "error": "cut"} and r["shortCutEarly"] == {"ok": False, "error": "cut"}
    assert r["shortLonger"]["error"] == "bad" and r["shortBadHost"]["error"] == "bad"
    assert r["shortExpired"]["error"] == "expired"
    assert r["htmlName"]["name"] == "img srcx onerroralertBob"
    assert r["longName"] == 40
