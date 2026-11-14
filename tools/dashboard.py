"""The laptop demo dashboard: a small local web server for the live event stream.

    ~/onset/.venv/bin/python tools/dashboard.py [--runs DIR] [--port 8765]

Then open http://localhost:8765 (from Windows too: WSL forwards localhost).

It serves web/dashboard.html and reads what the call flow writes (see onset/events.py):

    GET /               the dashboard page
    GET /events         Server-Sent Events: every line of runs/live/events.jsonl, then new lines
                        as they are appended. Control events: "reset" {exists, live} (clear the
                        panels; sent on connect with live=false, and with live=true whenever the
                        file is deleted or re-created, i.e. a new run), "caught_up" (the backlog
                        sent on connect is done; what follows is live).
    GET /frame.jpg      the latest annotated agent frame, never cached; X-Frame-Age: seconds old
    GET /audio/<name>   one spoken line, only plain *.wav names inside runs/live/audio
    GET /config         the decision thresholds, so the meters match onset/decide.py
    GET /baseline/<id>  the saved baseline numbers for a patient id (404 if none)
    GET /api/history?patient=<id>
                        that person's archived check-ins and trends (onset/history.py), for the
                        Trend panel; 400 for an invalid id, an empty history if there are none
    GET /caregiver-link the private caregiver link (runs/live/caregiver_link.txt, written by the agent)
                        for the "Open caregiver view" demo button. Loopback peers with a localhost
                        Host header only, never cached; 404 when there is none. It holds LiveKit
                        tokens, so it never goes into events or logs.
    GET /healthz        "ok"

It binds 127.0.0.1 only: frames and audio can hold a person's face and voice.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import re
import sys
import time
from pathlib import Path

from aiohttp import web

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WEB_DIR = REPO_ROOT / "web"
DEFAULT_RUNS = Path(os.environ.get("ONSET_RUNS_DIR", REPO_ROOT / "runs"))
DEFAULT_BASELINES = REPO_ROOT / "baselines"
POLL_S = 0.1  # how often the SSE tail checks the file
HEARTBEAT_S = 10.0
AUDIO_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.wav$")
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}

RUNS_KEY = web.AppKey("runs", Path)
BASELINES_KEY = web.AppKey("baselines", Path)
STOP_KEY = web.AppKey("stop", asyncio.Event)


def _live(request: web.Request) -> Path:
    return request.app[RUNS_KEY] / "live"


# --------------------------------------------------------------------------- pages


async def index(request: web.Request) -> web.StreamResponse:
    page = WEB_DIR / "dashboard.html"
    if not page.is_file():
        raise web.HTTPNotFound(text="web/dashboard.html is missing")
    return web.FileResponse(page, headers=NO_CACHE)


async def healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok", headers=NO_CACHE)


async def frame(request: web.Request) -> web.Response:
    path = _live(request) / "frame.jpg"
    try:
        data = path.read_bytes()  # replaced atomically by the flow, so never half-written
        age = max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        raise web.HTTPNotFound(text="no frame yet", headers=NO_CACHE) from None
    return web.Response(body=data, content_type="image/jpeg", headers={**NO_CACHE, "X-Frame-Age": f"{age:.2f}"})


async def audio(request: web.Request) -> web.StreamResponse:
    name = request.match_info.get("name", "")
    if not AUDIO_NAME.fullmatch(name):
        raise web.HTTPNotFound()
    base = (_live(request) / "audio").resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={**NO_CACHE, "Content-Type": "audio/wav"})


async def config(request: web.Request) -> web.Response:
    thresholds: dict = {}
    try:
        from onset.decide import DEFAULT_THRESHOLDS

        thresholds = dataclasses.asdict(DEFAULT_THRESHOLDS)
    except Exception:  # noqa: BLE001 - the page has the same defaults built in
        pass
    # ONSET_LINE_NUMBER: the line to FaceTime, shown on the presentation's "Let's call it" scene
    # (tools/demo.sh sets it from the doctor's reading of the Chert line).
    line = (os.environ.get("ONSET_LINE_NUMBER") or "").strip()
    return web.json_response({"thresholds": thresholds, "line_number": line or None}, headers=NO_CACHE)


async def present(request: web.Request) -> web.StreamResponse:
    """The presentation (web/present), with the live dashboard embedded in its middle scene."""
    raise web.HTTPFound("/web/present/index.html")


async def baseline(request: web.Request) -> web.Response:
    pid = request.match_info.get("patient_id", "")
    try:
        from onset.decide import load_baseline

        b = load_baseline(pid, base_dir=request.app[BASELINES_KEY])
    except Exception:  # noqa: BLE001
        b = None
    if b is None:
        raise web.HTTPNotFound(text="no baseline", headers=NO_CACHE)
    keep = ("recorded_at", "face_asymmetry", "arm_drift_difference", "speech_word_error_rate",
            "speech_words_per_minute", "samples")
    return web.json_response({k: getattr(b, k) for k in keep}, headers=NO_CACHE)


async def history(request: web.Request) -> web.Response:
    pid = request.query.get("patient", "")
    from onset.history import load_history, valid_patient_id

    if not valid_patient_id(pid):
        raise web.HTTPBadRequest(text="invalid patient id", headers=NO_CACHE)
    # Reading every archive is file work; keep it off the event loop that feeds the SSE tail.
    h = await asyncio.to_thread(load_history, pid, request.app[RUNS_KEY], baselines_dir=request.app[BASELINES_KEY])
    body = h.to_dict()
    for c in body["checkins"]:
        c.pop("artifacts", None)  # the page has no use for file details
    return web.json_response(body, headers=NO_CACHE)


CAREGIVER_LINK = "caregiver_link.txt"
CAREGIVER_LINK_MAX = 16 * 1024
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
PRIVATE = {**NO_CACHE, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def _from_this_machine(request: web.Request) -> bool:
    """A loopback peer that asked for localhost by name (a DNS-rebinding page sends its own Host)."""
    peer = (request.remote or "").strip()
    if not (peer.startswith("127.") or peer in ("::1", "::ffff:127.0.0.1")):
        return False
    host = (request.host or "").strip().lower()
    host = host[: host.find("]") + 1] if host.startswith("[") else host.split(":", 1)[0]
    return host in LOCAL_HOSTS


async def caregiver_link(request: web.Request) -> web.Response:
    if not _from_this_machine(request):
        raise web.HTTPForbidden(text="local only", headers=PRIVATE)
    path = _live(request) / CAREGIVER_LINK
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > CAREGIVER_LINK_MAX:
            raise OSError
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise web.HTTPNotFound(text="no caregiver link", headers=PRIVATE) from None
    if not text.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise web.HTTPNotFound(text="no caregiver link", headers=PRIVATE)
    return web.Response(text=text, content_type="text/plain", headers=PRIVATE)


# --------------------------------------------------------------------------- SSE tail


def _sse(data: str, event: str | None = None) -> bytes:
    out = f"event: {event}\n" if event else ""
    for line in data.splitlines() or [""]:
        out += f"data: {line}\n"
    return (out + "\n").encode("utf-8")


class Tail:
    """Follows one events.jsonl across deletes and re-creates.

    The open handle keeps the old file's inode alive, so a re-created file always has a
    different inode, even on a file system that reuses inode numbers.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fh = None
        self.buf = b""

    def close(self) -> None:
        if self.fh is not None:
            self.fh.close()
        self.fh, self.buf = None, b""

    def replaced(self) -> bool:
        """True if the file we follow is gone, swapped for another, or truncated."""
        if self.fh is None:
            return False
        try:
            st = os.stat(self.path)
        except OSError:
            return True
        mine = os.fstat(self.fh.fileno())
        return (st.st_ino, st.st_dev) != (mine.st_ino, mine.st_dev) or st.st_size < self.fh.tell()

    def open(self) -> bool:
        try:
            self.fh = open(self.path, "rb")  # noqa: SIM115 - held open on purpose
        except OSError:
            self.fh = None
        self.buf = b""
        return self.fh is not None

    def lines(self) -> list[str]:
        """Complete new lines (a line still being written waits for its newline)."""
        if self.fh is None:
            return []
        chunk = self.fh.read()
        if not chunk:
            return []
        self.buf += chunk
        *done, self.buf = self.buf.split(b"\n")
        out = []
        for raw in done:
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                json.loads(text)
            except ValueError:
                continue
            out.append(text)
        return out


async def events(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(headers={**NO_CACHE, "Content-Type": "text/event-stream",
                                       "X-Accel-Buffering": "no", "Connection": "keep-alive"})
    await resp.prepare(request)
    stop = request.app[STOP_KEY]
    tail = Tail(_live(request) / "events.jsonl")
    last_write = time.monotonic()

    async def send(data: str, event: str | None = None) -> None:
        nonlocal last_write
        await resp.write(_sse(data, event))
        last_write = time.monotonic()

    try:
        await resp.write(b"retry: 1000\n\n")
        # On connect: the whole current file (the page rebuilds its state from it, without
        # replaying old audio), then "caught_up". A reset later on means a new run started
        # while connected: those lines are live ("live": true).
        await send(json.dumps({"exists": tail.open(), "live": False}), "reset")
        for line in tail.lines():
            await send(line)
        await send("{}", "caught_up")
        while not stop.is_set():
            if tail.replaced():
                tail.close()
                await send(json.dumps({"exists": tail.open(), "live": True}), "reset")
            elif tail.fh is None and tail.open():
                await send(json.dumps({"exists": True, "live": True}), "reset")
            for line in tail.lines():
                await send(line)
            if time.monotonic() - last_write > HEARTBEAT_S:
                await resp.write(b": ping\n\n")
                last_write = time.monotonic()
            try:
                await asyncio.wait_for(stop.wait(), POLL_S)
            except asyncio.TimeoutError:
                pass
    except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
        pass
    finally:
        tail.close()
    return resp


# --------------------------------------------------------------------------- app


def make_app(runs: Path = DEFAULT_RUNS, baselines: Path = DEFAULT_BASELINES) -> web.Application:
    app = web.Application()
    app[RUNS_KEY] = Path(runs)
    app[BASELINES_KEY] = Path(baselines)
    app[STOP_KEY] = asyncio.Event()

    async def on_shutdown(app: web.Application) -> None:
        app[STOP_KEY].set()

    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/dashboard.html", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/events", events)
    app.router.add_get("/frame.jpg", frame)
    app.router.add_get("/audio/{name}", audio)
    app.router.add_get("/config", config)
    app.router.add_get("/baseline/{patient_id}", baseline)
    app.router.add_get("/api/history", history)
    app.router.add_get("/caregiver-link", caregiver_link)
    # The presentation and what it borrows (the pitch deck's three.js and fonts, the browser demo,
    # the logo). Static files from web/ only; no directory listings, no symlinks out of web/.
    app.router.add_get("/present", present)
    app.router.add_get("/present/", present)
    app.router.add_static("/web/", WEB_DIR, show_index=False, follow_symlinks=False)
    return app


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Onset demo dashboard (local only).")
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS, help="runs directory holding live/ (default: %(default)s)")
    ap.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES, help="baselines directory (default: %(default)s)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    print(f"Onset dashboard: http://localhost:{args.port}  (runs: {args.runs.resolve()})", flush=True)
    web.run_app(make_app(args.runs, args.baselines), host="127.0.0.1", port=args.port,
                print=None, shutdown_timeout=2.0, access_log=None)


if __name__ == "__main__":
    main()
