"""Audio lab: one FaceTime call that steps through several ways of publishing agent audio.

Diagnostic for the "caller hears no agent audio" issue (docs/chert-audio.md). It is a separate
LiveKit agent (registers as LAB_AGENT_NAME) and never runs in a real check-in.

What it does on a call:
  * publishes its audio track as soon as the job starts, before any caller joins, and from that
    moment sends a quiet continuous tone (some bridges only route audio that was flowing before
    the call connected);
  * publishes one camera track that shows the current phase in large type, so the caller can
    match what they hear with what they see;
  * after bridge_ready steps through the phases below, each announced by voice;
  * logs the caller's audio level every second (does the caller's voice reach the agent?).

Phases (seconds after bridge_ready):
  1   0-14  track A: 48 kHz mono, SOURCE_MICROPHONE, published before the caller. Tone -20 dBFS + speech.
  2  14-28  same track, loud: tone -9 dBFS, speech at full scale.
  3  28-44  track A unpublished, track B published: 48 kHz STEREO, DTX and RED off, name "agent-audio".
  4  44-60  track B unpublished, track C published: 24 kHz mono, SOURCE_UNKNOWN, name "audio".
Then silence apart from the tone; the caller hangs up.

Speech is fetched once from ElevenLabs (pcm_24000) and cached in LAB_CACHE (about 600 characters
in total), so warm rooms and repeat calls cost nothing more.

Run:  .venv/bin/python tools/audio_lab.py start
      then chert dev --agent $LAB_AGENT_NAME
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import time
from pathlib import Path

import aiohttp
import cv2
import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentServer, AutoSubscribe, JobContext, cli

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")  # this checkout's settings win
load_dotenv(Path.home() / "onset" / ".env.local")  # then the shared WSL checkout's, if any

log = logging.getLogger("lab")
LAB_AGENT_NAME = os.environ.get("LAB_AGENT_NAME") or f"{os.environ.get('AGENT_NAME', 'onset')}-lab"
CACHE = Path(os.environ.get("LAB_CACHE", Path(__file__).resolve().parent.parent / "runs" / "lab-cache"))
VOICE = os.environ.get("ELEVEN_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
W, H, FPS = 640, 360, 15

PHASES = [
    # (start s after bridge_ready, label, track spec, tone dBFS, speech gain, lines)
    (0, "1: 48k mono, early, quiet", "A", -20, 0.5,
     ["Phase one. Forty eight kilohertz mono, published early, with a quiet tone.",
      "Phase one again. Can you hear me?"]),
    (14, "2: same track, loud", "A", -9, 1.0,
     ["Phase two. The same track, much louder.", "Phase two again. Loud voice and tone."]),
    (28, "3: new track, 48k stereo", "B", -14, 0.9,
     ["Phase three. A new track, forty eight kilohertz stereo.", "Phase three again."]),
    (44, "4: new track, 24k mono", "C", -14, 0.9,
     ["Phase four. A new track, twenty four kilohertz mono.", "Phase four again. That is the last phase."]),
]
TRACKS = {
    "A": dict(name="microphone", rate=48000, ch=1, source=rtc.TrackSource.SOURCE_MICROPHONE, dtx=None),
    "B": dict(name="agent-audio", rate=48000, ch=2, source=rtc.TrackSource.SOURCE_MICROPHONE, dtx=False),
    "C": dict(name="audio", rate=24000, ch=1, source=rtc.TrackSource.SOURCE_UNKNOWN, dtx=None),
}
END_AT = 62

server = AgentServer()


def bridge_ready(metadata: str | None) -> bool:
    try:
        return json.loads(metadata or "{}").get("chert_bridge_ready") is True
    except (ValueError, AttributeError):
        return False


async def speech_pcm(text: str) -> np.ndarray:
    """int16 mono at 24 kHz, from the cache or from ElevenLabs."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(f"{VOICE}|{text}".encode()).hexdigest()[:16] + ".npy")
    if path.exists():
        return np.load(path)
    key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=pcm_24000"
    async with aiohttp.ClientSession() as http:
        async with http.post(url, headers={"xi-api-key": key},
                             json={"text": text, "model_id": "eleven_turbo_v2_5"}) as r:
            r.raise_for_status()
            pcm = np.frombuffer(await r.read(), dtype=np.int16).copy()
    np.save(path, pcm)
    return pcm


def resample(pcm24: np.ndarray, rate: int) -> np.ndarray:
    if rate == 24000:
        return pcm24.astype(np.float32)
    n = int(len(pcm24) * rate / 24000)
    return np.interp(np.linspace(0, len(pcm24) - 1, n), np.arange(len(pcm24)), pcm24).astype(np.float32)


def dbfs(x: np.ndarray) -> float:
    rms = math.sqrt(float(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


class Voice:
    """One published audio track fed every 10 ms with tone + queued speech."""

    def __init__(self, room: rtc.Room, spec: str) -> None:
        t = TRACKS[spec]
        self.room, self.spec, self.rate, self.ch = room, spec, t["rate"], t["ch"]
        self.source = rtc.AudioSource(self.rate, self.ch, queue_size_ms=200)
        self.track = rtc.LocalAudioTrack.create_audio_track(t["name"], self.source)
        self.opts = rtc.TrackPublishOptions(source=t["source"])
        if t["dtx"] is not None:
            self.opts.dtx = t["dtx"]
            self.opts.red = False
        self.tone_db, self.gain = -20.0, 0.5
        self.queue: list[np.ndarray] = []
        self.phase_n = 0
        self.sent_frames = 0
        self._task: asyncio.Task | None = None
        self.pub: rtc.LocalTrackPublication | None = None

    async def start(self) -> None:
        self.pub = await self.room.local_participant.publish_track(self.track, self.opts)
        log.info("[lab] published audio track %s (%s, %d Hz, %d ch, sid %s)", self.spec,
                 TRACKS[self.spec]["name"], self.rate, self.ch, self.pub.sid)
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.pub:
            await self.room.local_participant.unpublish_track(self.pub.sid)
            log.info("[lab] unpublished audio track %s", self.spec)
        await self.source.aclose()

    async def _loop(self) -> None:
        n = self.rate // 100
        phase = 0.0
        step = 2 * math.pi * 330 / self.rate
        cur, pos = None, 0
        while True:
            amp = 32767 * 10 ** (self.tone_db / 20)
            idx = np.arange(n)
            tone = amp * np.sin(phase + step * idx)
            phase = (phase + step * n) % (2 * math.pi)
            out = tone
            if cur is None and self.queue:
                cur, pos = self.queue.pop(0), 0
            if cur is not None:
                chunk = cur[pos:pos + n] * self.gain
                out = out.copy()
                out[: len(chunk)] += chunk
                pos += n
                if pos >= len(cur):
                    cur = None
            pcm = np.clip(out, -32767, 32767).astype(np.int16)
            if self.ch == 2:
                pcm = np.repeat(pcm, 2)
            await self.source.capture_frame(rtc.AudioFrame(pcm.tobytes(), self.rate, self.ch, n))
            self.sent_frames += 1


def frame(label: str, tick: int, note: str) -> rtc.VideoFrame:
    img = np.zeros((H, W, 4), dtype=np.uint8)
    img[..., :3] = (30, 30, 60)
    img[..., 3] = 255
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, "ONSET AUDIO LAB", (20, 50), font, 1.0, (255, 255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(img, f"phase {label}", (20, 160), font, 1.0, (0, 255, 0, 255), 2, cv2.LINE_AA)
    cv2.putText(img, note, (20, 220), font, 0.7, (255, 255, 0, 255), 1, cv2.LINE_AA)
    cv2.putText(img, f"frame {tick}", (20, 320), font, 0.6, (200, 200, 200, 255), 1, cv2.LINE_AA)
    return rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, img.tobytes())


@server.rtc_session(agent_name=LAB_AGENT_NAME)
async def entry(ctx: JobContext) -> None:
    log.info("[lab] job for room %s, metadata %s", ctx.room.name, ctx.job.metadata or "{}")
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    t_job = time.monotonic()

    # Speech for every phase, fetched (or read from the cache) before any caller exists.
    speech: dict[str, dict[str, np.ndarray]] = {}
    for spec in TRACKS:
        speech[spec] = {}
    for _, _, spec, _, _, lines in PHASES:
        for line in lines:
            speech[spec][line] = resample(await speech_pcm(line), TRACKS[spec]["rate"])

    # Audio from the very start (warm room, before the caller): track A with the tone.
    voice = Voice(ctx.room, "A")
    await voice.start()

    state = {"label": "0: waiting for FaceTime", "note": "tone only", "tick": 0}
    vsrc = rtc.VideoSource(W, H)
    vtrack = rtc.LocalVideoTrack.create_video_track("lab-video", vsrc)
    vsrc.capture_frame(frame(state["label"], 0, state["note"]))
    await ctx.room.local_participant.publish_track(
        vtrack, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    tasks: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    async def video_loop() -> None:
        while True:
            state["tick"] += 1
            vsrc.capture_frame(frame(state["label"], state["tick"], state["note"]))
            await asyncio.sleep(1 / FPS)

    spawn(video_loop())

    async def caller_level(track: rtc.Track, who: str) -> None:
        stream = rtc.AudioStream(track, sample_rate=48000, num_channels=1)
        buf, t0, peak_win = [], time.monotonic(), -120.0
        async for ev in stream:
            a = np.frombuffer(ev.frame.data, dtype=np.int16)
            buf.append(a)
            if time.monotonic() - t0 >= 1.0:
                x = np.concatenate(buf)
                log.info("[lab] caller audio from %s: rms %.1f dBFS, peak %.1f dBFS", who, dbfs(x),
                         20 * math.log10(max(1, int(np.abs(x).max())) / 32768))
                buf, t0 = [], time.monotonic()

    def on_track(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log.info("[lab] subscribed %s/%s from %s", rtc.TrackKind.Name(track.kind),
                 rtc.TrackSource.Name(pub.source), p.identity)
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            spawn(caller_level(track, p.identity))

    ctx.room.on("track_subscribed", on_track)
    for p in ctx.room.remote_participants.values():  # tracks subscribed before the handler existed
        for pub in p.track_publications.values():
            if pub.track is not None:
                on_track(pub.track, pub, p)
    ctx.room.on("local_track_subscribed", lambda pub: log.info(
        "[lab] the bridge SUBSCRIBED to our %s track %s", rtc.TrackKind.Name(pub.kind), pub.sid))
    ctx.room.on("participant_connected", lambda p: log.info("[lab] participant joined: %s", p.identity))
    ctx.room.on("participant_disconnected", lambda p: log.info("[lab] participant left: %s", p.identity))
    log.info("[lab] audio + video published %.2f s after job start; waiting for bridge_ready",
             time.monotonic() - t_job)

    started = asyncio.Event()

    async def run_phases() -> None:
        nonlocal voice
        t0 = time.monotonic()
        log.info("[lab] bridge_ready: starting phases")
        for start, label, spec, tone_db, gain, lines in PHASES:
            await asyncio.sleep(max(0.0, t0 + start - time.monotonic()))
            if spec != voice.spec:
                await voice.stop()
                voice = Voice(ctx.room, spec)
                await voice.start()
            voice.tone_db, voice.gain = tone_db, gain
            state["label"], state["note"] = label, f"tone {tone_db} dBFS, speech x{gain}"
            log.info("[lab] PHASE %s at +%.1f s", label, time.monotonic() - t0)
            for line in lines:
                voice.queue.append(speech[spec][line])
                voice.queue.append(np.zeros(TRACKS[spec]["rate"] // 2, dtype=np.float32))
        await asyncio.sleep(max(0.0, t0 + END_AT - time.monotonic()))
        state["label"], state["note"] = "done: please hang up", "tone only"
        log.info("[lab] phases done at +%.1f s", time.monotonic() - t0)

    def check_ready(*_args) -> None:
        if started.is_set() or not bridge_ready(ctx.room.metadata):
            return
        started.set()
        spawn(run_phases())

    async def shutdown(reason: str = "") -> None:
        for t in list(tasks):
            t.cancel()
        await voice.stop()

    ctx.add_shutdown_callback(shutdown)
    ctx.room.on("room_metadata_changed", check_ready)
    check_ready()


if __name__ == "__main__":
    cli.run_app(server)
