"""Onset, step 1: a minimal FaceTime video agent for Chert.

What it does on each call:
  * joins the room Chert dispatches to it (registered under AGENT_NAME),
  * waits for the caller (participant chert-bridge-<call id>),
  * publishes one camera track: the caller's own video sent back with a test
    overlay (a box and a frame counter), letterboxed into OUT_WIDTH x OUT_HEIGHT,
  * logs the caller video's resolution, rotation and frames per second,
  * greets once with OpenAI text-to-speech when Chert signals bridge_ready,
  * says loudly in the log if no caller video track arrives.

Chert's rules this follows (see `chert guide agent`):
  * greet within 10 seconds of bridge_ready, never just because someone joined;
  * publish exactly one unmuted audio track;
  * the participant that speaks also publishes the camera track.

Run:  .venv/bin/python agent.py start
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AutoSubscribe,
    JobContext,
    cli,
)
from livekit.agents.voice.room_io import RoomOptions
from livekit.plugins import elevenlabs, google, openai

load_dotenv(Path(__file__).with_name(".env.local"))

VOICE_STYLE = "Calm, warm and slow. Plain words. Speak to an older adult."

# Calm ElevenLabs voices on this account: Sarah (mature, reassuring; default),
# River (calm, neutral) SAz9YHcvj6GT2YYXdXww, Brian (comforting) nPczCjzI2devNBz1zQrb.
ELEVEN_DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"


def make_tts():
    """The first configured voice: ElevenLabs, then Gemini, then OpenAI."""
    eleven_key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if eleven_key:
        return elevenlabs.TTS(
            api_key=eleven_key,
            voice_id=os.environ.get("ELEVEN_VOICE_ID", ELEVEN_DEFAULT_VOICE),
            model=os.environ.get("ELEVEN_MODEL", "eleven_turbo_v2_5"),
            voice_settings=elevenlabs.VoiceSettings(
                stability=0.75,  # steady, even delivery
                similarity_boost=0.75,
                speed=float(os.environ.get("ELEVEN_SPEED", "0.9")),  # a little slower than normal
            ),
        )
    if os.environ.get("GOOGLE_API_KEY"):
        # Gemini's speech settings exist only in API v1beta; without this the SDK
        # can call v1 and Google answers 400 "Unknown name speechConfig".
        os.environ.setdefault("GOOGLE_GENAI_API_VERSION", "v1beta")
        return google.beta.GeminiTTS(
            model=os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
            voice_name=os.environ.get("GEMINI_VOICE", "Sulafat"),  # a warm voice
            instructions=VOICE_STYLE,
        )
    return openai.TTS(model="gpt-4o-mini-tts", voice="coral", instructions=VOICE_STYLE)

log = logging.getLogger("onset")

AGENT_NAME = os.environ.get("AGENT_NAME", "onset")
OUT_W = int(os.environ.get("OUT_WIDTH", "640"))
OUT_H = int(os.environ.get("OUT_HEIGHT", "360"))
PLACEHOLDER_FPS = 15
# How long after bridge_ready to wait for the caller's camera before declaring it missing.
CALLER_VIDEO_GRACE_S = 10.0

GREETING = (
    "Hello, this is Onset. This is a test call. "
    "I'm checking that I can see your camera."
)
# A second line a few seconds later: tells apart "greeting lost while FaceTime was
# still connecting" (you hear only this) from "no agent audio at all" (you hear neither).
FOLLOW_UP = "You should see yourself on screen, with a green box and a frame counter."
FOLLOW_UP_DELAY_S = 4.0
AUDIO_TEST_REPEATS = int(os.environ.get("ONSET_AUDIO_TEST_REPEATS", "12"))  # 0 turns the test lines off

server = AgentServer()


# --------------------------------------------------------------------------- helpers


def bridge_ready(metadata: str | None) -> bool:
    try:
        return json.loads(metadata or "{}").get("chert_bridge_ready") is True
    except (ValueError, AttributeError):
        return False


def is_caller(p: rtc.RemoteParticipant) -> bool:
    return p.identity.startswith("chert-bridge-")


_ROTATE = {
    rtc.VideoRotation.VIDEO_ROTATION_90: cv2.ROTATE_90_CLOCKWISE,
    rtc.VideoRotation.VIDEO_ROTATION_180: cv2.ROTATE_180,
    rtc.VideoRotation.VIDEO_ROTATION_270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def to_rgba(event: rtc.VideoFrameEvent) -> np.ndarray:
    """The caller's frame as an upright H x W x 4 RGBA array."""
    f = event.frame
    if f.type != rtc.VideoBufferType.RGBA:  # FaceTime frames arrive as I420
        f = f.convert(rtc.VideoBufferType.RGBA)
    img = np.frombuffer(f.data, dtype=np.uint8).reshape(f.height, f.width, 4)
    rot = _ROTATE.get(event.rotation)
    return cv2.rotate(img, rot) if rot is not None else img.copy()


def letterbox(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Fit img inside w x h without distortion, black bars around it."""
    ih, iw = img.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    canvas = np.zeros((h, w, 4), dtype=np.uint8)
    canvas[..., 3] = 255
    x, y = (w - nw) // 2, (h - nh) // 2
    canvas[y : y + nh, x : x + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return canvas


def draw_test_overlay(img: np.ndarray, frame_no: int, src: str, fps: float) -> None:
    h, w = img.shape[:2]
    green = (0, 255, 0, 255)
    bw, bh = int(w * 0.4), int(h * 0.5)
    x0, y0 = (w - bw) // 2, (h - bh) // 2
    cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), green, 2)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for i, line in enumerate((f"frame {frame_no}", f"in {src} @ {fps:.1f} fps")):
        org = (10, 24 + i * 22)
        cv2.putText(img, line, org, font, 0.6, (0, 0, 0, 255), 4, cv2.LINE_AA)
        cv2.putText(img, line, org, font, 0.6, green, 1, cv2.LINE_AA)


def placeholder(tick: int) -> np.ndarray:
    img = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)
    img[..., :3] = 40
    img[..., 3] = 255
    cv2.putText(img, "Onset: waiting for your camera", (20, OUT_H // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(img, f"frame {tick}", (20, OUT_H // 2 + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0, 255), 1, cv2.LINE_AA)
    return img


def send(source: rtc.VideoSource, img: np.ndarray) -> None:
    source.capture_frame(rtc.VideoFrame(OUT_W, OUT_H, rtc.VideoBufferType.RGBA, img.tobytes()))


# --------------------------------------------------------------------------- the call


class CallVideo:
    """Reads the caller's camera, logs its stats, sends it back with the overlay."""

    def __init__(self, source: rtc.VideoSource) -> None:
        self.source = source
        self.frames_out = 0
        self.last_caller_frame = 0.0
        self.latest: np.ndarray | None = None  # caller's latest frame, letterboxed, no overlay
        self.src, self.fps = "?", 0.0
        self.got_track = asyncio.Event()
        self.got_frame = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def send_loop(self) -> None:
        """Send video on a steady clock, as Chert's starter does: the caller's latest
        frame with the overlay, or the placeholder when no caller frame is fresh."""
        period = 1 / PLACEHOLDER_FPS
        next_at = time.monotonic()
        while True:
            fresh = time.monotonic() - self.last_caller_frame <= 1.0 and self.latest is not None
            if fresh:
                out = self.latest.copy()
                draw_test_overlay(out, self.frames_out, self.src, self.fps)
            else:
                out = placeholder(self.frames_out)
            send(self.source, out)
            self.frames_out += 1
            next_at += period
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))

    async def read_caller(self, track: rtc.RemoteVideoTrack) -> None:
        self.got_track.set()
        stream = rtc.VideoStream(track)
        window_start, window_frames, fps = time.monotonic(), 0, 0.0
        src, last_logged = "?", None
        try:
            async for event in stream:
                img = to_rgba(event)
                raw = f"{event.frame.width}x{event.frame.height}"
                rot = rtc.VideoRotation.Name(event.rotation)
                src = f"{img.shape[1]}x{img.shape[0]}"
                if not self.got_frame.is_set():
                    log.info("[onset] FIRST CALLER FRAME: raw %s, rotation %s, upright %s",
                             raw, rot, src)
                    self.got_frame.set()

                window_frames += 1
                now = time.monotonic()
                if now - window_start >= 1.0:
                    fps = window_frames / (now - window_start)
                    window_start, window_frames = now, 0
                    state = (raw, rot)
                    log.info("[onset] caller video %s (raw %s, %s) @ %.1f fps%s",
                             src, raw, rot, fps, "" if state == last_logged else "  <- changed")
                    last_logged = state

                self.latest = letterbox(img, OUT_W, OUT_H)
                self.src, self.fps = src, fps
                self.last_caller_frame = now
        finally:
            await stream.aclose()
            log.info("[onset] caller video stream ended")

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def describe_publications(p: rtc.RemoteParticipant) -> str:
    pubs = [
        f"{rtc.TrackKind.Name(pub.kind)}/{rtc.TrackSource.Name(pub.source)}"
        f"{' muted' if pub.muted else ''}{' subscribed' if pub.subscribed else ''}"
        for pub in p.track_publications.values()
    ]
    return ", ".join(pubs) or "none"


@server.rtc_session(agent_name=AGENT_NAME)
async def entry(ctx: JobContext) -> None:
    log.info("[onset] job for room %s, metadata %s", ctx.room.name, ctx.job.metadata or "{}")
    # Join at once (Chert keeps a warm room ready before any call); subscribe to
    # audio AND video so the caller's camera reaches us.
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    try:
        caller = await ctx.wait_for_participant()
    except RuntimeError as e:  # a waiting room closed before any caller arrived
        log.info("[onset] room closed before a caller joined (%s)", e)
        return
    log.info("[onset] caller joined as %s; tracks: %s", caller.identity, describe_publications(caller))
    if not is_caller(caller):
        log.warning("[onset] participant %s is not a chert-bridge participant", caller.identity)

    # One camera track, from this participant, the same one that speaks.
    source = rtc.VideoSource(OUT_W, OUT_H)
    track = rtc.LocalVideoTrack.create_video_track("onset-video", source)
    video = CallVideo(source)
    send(source, placeholder(0))
    await ctx.room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
    )
    video.spawn(video.send_loop())

    # When the other side (Chert's Mac) starts or stops taking one of our tracks.
    def on_local_sub(*args) -> None:
        log.info("[onset] the caller side SUBSCRIBED to our track: %s",
                 ", ".join(str(getattr(a, "kind", None) and rtc.TrackKind.Name(a.kind) or getattr(a, "sid", a)) for a in args))

    ctx.room.on("local_track_subscribed", on_local_sub)

    def on_track(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log.info("[onset] subscribed %s/%s from %s", rtc.TrackKind.Name(track.kind),
                 rtc.TrackSource.Name(pub.source), p.identity)
        if track.kind == rtc.TrackKind.KIND_VIDEO and p.identity == caller.identity:
            video.spawn(video.read_caller(track))

    def on_published(pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log.info("[onset] %s published %s/%s", p.identity,
                 rtc.TrackKind.Name(pub.kind), rtc.TrackSource.Name(pub.source))

    ctx.room.on("track_subscribed", on_track)
    ctx.room.on("track_published", on_published)
    # Tracks the caller published before we attached the handler.
    for pub in caller.track_publications.values():
        if pub.track is not None:
            on_track(pub.track, pub, caller)

    # Voice: text-to-speech only for step 1 (no speech recognition, no LLM yet).
    session = AgentSession(tts=make_tts())

    # Say what the voice pipeline is doing, so a silent call is never a mystery.
    session.on("agent_state_changed", lambda ev: log.info(
        "[onset] agent state %s -> %s", ev.old_state, ev.new_state))
    session.on("error", lambda ev: log.error(
        "[onset] SESSION ERROR from %s: %r", type(ev.source).__name__, ev.error))
    session.on("close", lambda ev: log.info("[onset] session closed: %s", getattr(ev, "reason", ev)))

    async def shutdown(reason: str = "") -> None:
        await video.aclose()
        await session.aclose()
        await source.aclose()

    ctx.add_shutdown_callback(shutdown)

    await session.start(
        agent=Agent(instructions="You are Onset. Step 1 test agent; you only greet."),
        room=ctx.room,
        room_options=RoomOptions(
            participant_identity=caller.identity,
            audio_input=False,
            video_input=False,
            text_input=False,
            text_output=False,
            audio_output=True,
            close_on_disconnect=True,
            delete_room_on_close=False,
        ),
    )

    # Greet once on Chert's bridge_ready signal (the Mac hangs up after 10 s of silence).
    greeted = asyncio.Event()

    def check_ready(*_args) -> None:
        if greeted.is_set() or not bridge_ready(ctx.room.metadata):
            return
        greeted.set()
        log.info("[onset] FaceTime is ready: greeting")
        video.spawn(watch_for_caller_video())
        try:
            handle = session.say(GREETING, allow_interruptions=False)
        except Exception:
            log.exception("[onset] GREETING FAILED to start")
            return
        t0 = time.monotonic()
        handle.add_done_callback(
            lambda h: log.info("[onset] greeting finished after %.2f s (interrupted=%s)",
                               time.monotonic() - t0, h.interrupted))
        video.spawn(follow_up(handle))

    async def follow_up(greeting) -> None:
        await greeting.wait_for_playout()
        await asyncio.sleep(FOLLOW_UP_DELAY_S)
        log.info("[onset] saying follow-up line")
        await session.say(FOLLOW_UP, allow_interruptions=False).wait_for_playout()
        # Audio test: keep speaking for the whole call, so sound that only starts
        # late (or drops out) is noticed. Bounded so a forgotten call costs little.
        for n in range(1, AUDIO_TEST_REPEATS + 1):
            await asyncio.sleep(2.0)
            log.info("[onset] audio test line %d", n)
            await session.say(f"Audio test. Number {n}.", allow_interruptions=False).wait_for_playout()

    async def watch_for_caller_video() -> None:
        try:
            await asyncio.wait_for(video.got_frame.wait(), CALLER_VIDEO_GRACE_S)
        except asyncio.TimeoutError:
            what = "a video track but no frames" if video.got_track.is_set() else "NO video track"
            media = json.loads(ctx.job.metadata or "{}").get("media")
            log.error(
                "[onset] CALLER VIDEO MISSING: %.0f s after bridge_ready the caller sent %s. "
                "Caller tracks: %s. Job media: %s. Was the call placed with FaceTime Video?",
                CALLER_VIDEO_GRACE_S, what, describe_publications(caller), media,
            )

    ctx.room.on("room_metadata_changed", check_ready)
    check_ready()


if __name__ == "__main__":
    cli.run_app(server)
