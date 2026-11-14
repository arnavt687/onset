"""Onset: the FaceTime stroke check-in agent for Chert.

On each call it:
  * joins the room Chert dispatches to it (registered under AGENT_NAME) and waits for the
    caller (participant chert-bridge-<call id>);
  * publishes one camera track: the caller's own video sent back with the caption-first
    overlay (instructions, countdowns, landmarks) drawn by onset.flow.CheckFlow;
  * on Chert's bridge_ready signal starts the scripted check (onset/script.py): greet, smile,
    arms up 10 s, repeat a sentence, compare with the person's baseline, closing line;
  * speaks every line with the configured TTS (cached), and writes the event stream and
    spoken audio to runs/live/ for the laptop dashboard;
  * records the call to recordings/ only when ONSET_RECORD=1.

Chert's rules this follows (see `chert guide agent`):
  * greet within 10 seconds of bridge_ready (the greeting is the flow's first line), never
    just because someone joined;
  * publish exactly one unmuted audio track;
  * the participant that speaks also publishes the camera track.

Run:  .venv/bin/python agent.py start
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
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

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))  # tools.record, when run from another directory
load_dotenv(REPO / ".env.local")

from onset import caregiver, overlay, patients, script  # noqa: E402
from onset.events import EventSink  # noqa: E402
from onset.flow import CheckFlow  # noqa: E402
from onset.voice import Speaker  # noqa: E402

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
SEND_FPS = 15
# How long after bridge_ready to wait for the caller's camera before declaring it missing.
CALLER_VIDEO_GRACE_S = 10.0

# ONSET_HEALTH_PORT lets a second agent (tests, a parallel experiment) run beside the live one.
server = AgentServer(**({"port": int(os.environ["ONSET_HEALTH_PORT"])} if os.environ.get("ONSET_HEALTH_PORT") else {}))


# --------------------------------------------------------------------------- helpers


def bridge_ready(metadata: str | None) -> bool:
    try:
        return json.loads(metadata or "{}").get("chert_bridge_ready") is True
    except (ValueError, AttributeError):
        return False


def is_caller(p: rtc.RemoteParticipant) -> bool:
    return p.identity.startswith("chert-bridge-")


def is_caregiver(p: rtc.RemoteParticipant) -> bool:
    return p.identity.startswith(caregiver.WATCH_PREFIX) and not p.identity.startswith(caregiver.TALK_PREFIX)


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
    return overlay.letterbox(img, w, h)


def draw_test_overlay(img: np.ndarray, frame_no: int, src: str, fps: float) -> None:
    """Step-1 test overlay (kept for selftest.py): a box and a frame counter."""
    h, w = img.shape[:2]
    green = (0, 255, 0, 255)
    bw, bh = int(w * 0.4), int(h * 0.5)
    x0, y0 = (w - bw) // 2, (h - bh) // 2
    cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), green, 2)
    for i, line in enumerate((f"frame {frame_no}", f"in {src} @ {fps:.1f} fps")):
        org = (10, 24 + i * 22)
        cv2.putText(img, line, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0, 255), 4, cv2.LINE_AA)
        cv2.putText(img, line, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, green, 1, cv2.LINE_AA)


def placeholder(tick: int) -> np.ndarray:
    """Before the caller's camera arrives."""
    img = overlay.blank(OUT_W, OUT_H)
    layer, _ = overlay.caption_layer("Onset is connecting…", OUT_W, OUT_H, "bottom")
    overlay.composite(img, layer)
    return img


def send(source: rtc.VideoSource, img: np.ndarray) -> None:
    source.capture_frame(rtc.VideoFrame(OUT_W, OUT_H, rtc.VideoBufferType.RGBA, img.tobytes()))


def describe_publications(p: rtc.RemoteParticipant) -> str:
    pubs = [
        f"{rtc.TrackKind.Name(pub.kind)}/{rtc.TrackSource.Name(pub.source)}"
        f"{' muted' if pub.muted else ''}{' subscribed' if pub.subscribed else ''}"
        for pub in p.track_publications.values()
    ]
    return ", ".join(pubs) or "none"


def _recorder(run_id: str):
    """A whole-call recorder (no-op unless ONSET_RECORD=1)."""
    try:
        from tools.record import Recorder

        return Recorder.for_call(run_id, "call")
    except Exception:  # noqa: BLE001
        log.exception("[onset] recorder unavailable")
        return None


# --------------------------------------------------------------------------- the call


class CallMedia:
    """Caller video in, flow-rendered video out, caller audio in; all on one clock."""

    def __init__(self, source: rtc.VideoSource, flow: CheckFlow) -> None:
        self.source, self.flow = source, flow
        self.latest: np.ndarray | None = None
        self.last_caller_frame = 0.0
        self.got_track = asyncio.Event()
        self.got_frame = asyncio.Event()
        self._busy = False
        self._tasks: set[asyncio.Task] = set()
        self.on_tick = None  # called every send tick with the clock time

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def send_loop(self) -> None:
        """Steady 15 fps: tick the flow, render its frame, send it."""
        period = 1 / SEND_FPS
        next_at = time.monotonic()
        n = 0
        while True:
            now = time.monotonic()
            try:
                if self.on_tick is not None:
                    self.on_tick(now)
                fresh = self.latest is not None and now - self.last_caller_frame <= 1.0
                img = self.flow.render(self.latest if fresh else None, now) if (fresh or self.flow.state != "idle") else placeholder(n)
                send(self.source, img)
            except Exception:  # noqa: BLE001 - keep the video alive whatever happens
                log.exception("[onset] send tick failed")
            n += 1
            next_at += period
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))

    async def read_caller_video(self, track: rtc.RemoteVideoTrack) -> None:
        self.got_track.set()
        stream = rtc.VideoStream(track)
        window_start, window_frames, last_logged = time.monotonic(), 0, None
        try:
            async for event in stream:
                img = to_rgba(event)
                now = time.monotonic()
                raw = f"{event.frame.width}x{event.frame.height}"
                rot = rtc.VideoRotation.Name(event.rotation)
                if not self.got_frame.is_set():
                    log.info("[onset] FIRST CALLER FRAME: raw %s, rotation %s, upright %dx%d",
                             raw, rot, img.shape[1], img.shape[0])
                    self.got_frame.set()
                window_frames += 1
                if now - window_start >= 5.0:
                    fps = window_frames / (now - window_start)
                    state = (raw, rot)
                    log.info("[onset] caller video %dx%d (raw %s, %s) @ %.1f fps%s", img.shape[1], img.shape[0],
                             raw, rot, fps, "" if state == last_logged else "  <- changed")
                    window_start, window_frames, last_logged = now, 0, state
                self.latest, self.last_caller_frame = img, now
                if not self._busy:  # the meters run in a thread; drop frames while they are busy
                    self._busy = True
                    self.spawn(self._measure(img, now))
        finally:
            await stream.aclose()
            log.info("[onset] caller video stream ended")

    async def _measure(self, img: np.ndarray, t: float) -> None:
        try:
            await asyncio.to_thread(self.flow.on_frame, img, t)
        finally:
            self._busy = False

    async def read_caller_audio(self, track: rtc.RemoteAudioTrack) -> None:
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        try:
            async for ev in stream:
                self.flow.on_audio(bytes(ev.frame.data), ev.frame.sample_rate, time.monotonic())
        finally:
            await stream.aclose()

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


class Escalation:
    """agent.py's side of ESCALATE / UNCLEAR (onset/caregiver.py has the rules): mint the
    caregiver's link, listen in their side room, relay their voice and camera to the caller,
    show them the summary, and text them. Never raises into the call."""

    def __init__(self, ctx: JobContext, session: AgentSession, sink: EventSink, run_id: str,
                 patient: patients.Patient) -> None:
        self.ctx, self.session, self.sink, self.run_id, self.patient = ctx, session, sink, run_id, patient
        self.cfg = caregiver.EscalationConfig.from_env()
        self.flow: CheckFlow | None = None
        self.relay = None
        self.summary = ""
        self.outcome = ""
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _attrs(self, **attrs: str) -> None:
        try:
            await self.ctx.room.local_participant.set_attributes({f"onset.{k}": v for k, v in attrs.items()})
        except Exception as e:  # noqa: BLE001 - the page also gets the data packet
            log.warning("[onset] could not set participant attributes: %s", type(e).__name__)

    async def publish_summary(self) -> None:
        if not self.summary:
            return
        payload = json.dumps({"type": "summary", "text": self.summary, "outcome": self.outcome,
                              "patient": caregiver.first_name(self.patient.name)})
        try:
            await self.ctx.room.local_participant.publish_data(payload, reliable=True, topic="onset")
        except Exception as e:  # noqa: BLE001
            log.warning("[onset] could not send the summary to the caregiver: %s", type(e).__name__)

    async def run(self, decision, summary: str) -> dict:
        flow = self.flow
        self.summary, self.outcome = summary, decision.outcome.value
        first = caregiver.first_name(self.patient.name)
        await self._attrs(outcome=self.outcome, summary=summary, relay="off", patient=first)
        invite = None
        if self.cfg.join == "link":
            try:
                invite = caregiver.make_invite(
                    self.ctx.room.name, self.run_id, livekit_url=os.environ.get("LIVEKIT_URL", ""),
                    api_key=os.environ.get("LIVEKIT_API_KEY", ""), api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
                    cfg=self.cfg, patient_first=first)
            except Exception as e:  # noqa: BLE001 - the text still goes out, without a link
                log.warning("[onset] no caregiver link: %s", e)
                if flow is not None:
                    flow.caregiver_update("failed", f"no link: {type(e).__name__}")
        if invite is not None:
            if invite.side_room:  # listen before anyone can have the link
                await self._start_relay(invite.side_room)
            if flow is not None:
                flow.caregiver_update("invited", "join link: " + (
                    "caregiver page" if invite.kind == "page" else "LiveKit Meet, watch only"))
            self.sink.private_file("caregiver_link.txt", invite.link)  # the dashboard's demo button
        return await caregiver.notify_caregiver(
            sink=flow if flow is not None else self.sink, summary=summary,
            caregiver_phone=self.patient.caregiver.phone, caregiver_name=self.patient.caregiver.name,
            patient_name=self.patient.name, invite=invite, talk=self.cfg.talk)

    async def _start_relay(self, side_room: str) -> None:
        from onset.relay import SideRoomRelay

        try:
            token = caregiver.relay_token(side_room, f"{caregiver.RELAY_PREFIX}{self.run_id[:36]}",
                                          api_key=os.environ["LIVEKIT_API_KEY"],
                                          api_secret=os.environ["LIVEKIT_API_SECRET"], ttl_s=self.cfg.ttl_s + 600)
        except Exception as e:  # noqa: BLE001
            log.warning("[onset] no relay token: %s", type(e).__name__)
            return
        flow = self.flow

        def status(s: str, detail: str) -> None:
            if flow is not None:
                flow.caregiver_update(s, detail)
            if s == "left":
                self._spawn(self._attrs(relay="off"))

        self.relay = SideRoomRelay(url=os.environ["LIVEKIT_URL"], token=token, on_audio_track=self._relay_audio,
                                   on_frame=(flow.caregiver_frame if flow is not None else (lambda *_: None)),
                                   on_status=status, clock=time.monotonic)
        await self.relay.start()

    def _relay_audio(self, track: rtc.RemoteAudioTrack) -> None:
        """Play the caregiver's microphone through Onset's one audio track (Chert's rule)."""

        async def go() -> None:
            await self._attrs(relay="on")  # the caregiver page stops playing Onset's audio (no echo)
            stream = rtc.AudioStream(track, sample_rate=24000, num_channels=1)

            async def frames():
                async for ev in stream:
                    yield ev.frame

            try:
                handle = self.session.say(" ", audio=frames(), allow_interruptions=False, add_to_chat_ctx=False)
                await handle.wait_for_playout()
            except Exception:  # noqa: BLE001
                log.exception("[onset] caregiver relay failed")
            finally:
                await stream.aclose()
                await self._attrs(relay="off")

        self._spawn(go())

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.relay is not None:
            await self.relay.aclose()


@server.rtc_session(agent_name=AGENT_NAME)
async def entry(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}") if ctx.job.metadata else {}
    log.info("[onset] job for room %s, metadata %s", ctx.room.name, meta)
    # Join at once (Chert keeps a warm room ready before any call); subscribe to
    # audio AND video so the caller's camera and microphone reach us.
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    tts = make_tts()
    from onset.decide import load_baseline
    from onset.speech import SENTENCES

    patient = patients.lookup(patients.handle_from_metadata(meta))
    log.info("[onset] patient profile %s (matched by %s)", patient.id, patient.matched_by)
    try:
        b = None if patient.anonymous else load_baseline(patient.id)
    except Exception:  # noqa: BLE001
        b = None
    warm_speaker = Speaker(tts)
    warm = asyncio.create_task(warm_speaker.warm(script.all_spoken(
        b.sentence if b else (patient.sentence or SENTENCES[0]))))

    try:
        caller = await ctx.wait_for_participant()
    except RuntimeError as e:  # a waiting room closed before any caller arrived
        log.info("[onset] room closed before a caller joined (%s)", e)
        warm.cancel()
        return
    log.info("[onset] caller joined as %s; tracks: %s", caller.identity, describe_publications(caller))
    if not is_caller(caller):
        log.warning("[onset] participant %s is not a chert-bridge participant", caller.identity)
    run_id = (caller.identity.removeprefix("chert-bridge-")[:36] if is_caller(caller) else ctx.room.name) or ctx.room.name

    sink = EventSink(run_id, "call")
    recorder = _recorder(run_id)

    # One camera track, from this participant, the same one that speaks.
    source = rtc.VideoSource(OUT_W, OUT_H)
    track = rtc.LocalVideoTrack.create_video_track("onset-video", source)
    send(source, placeholder(0))
    await ctx.room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
    )

    session = AgentSession(tts=tts)
    session.on("agent_state_changed", lambda ev: log.info("[onset] agent state %s -> %s", ev.old_state, ev.new_state))
    session.on("error", lambda ev: log.error("[onset] SESSION ERROR from %s: %r", type(ev.source).__name__, ev.error))

    async def play(text: str, pcm: bytes, rate: int) -> None:
        samples = np.frombuffer(pcm, dtype=np.int16)
        step = rate // 50  # 20 ms frames

        async def frames():
            for i in range(0, len(samples), step):
                chunk = samples[i:i + step]
                yield rtc.AudioFrame(chunk.tobytes(), rate, 1, len(chunk))

        handle = session.say(text, audio=frames(), allow_interruptions=False, add_to_chat_ctx=False)
        await handle.wait_for_playout()

    speaker = Speaker(tts, sink=sink, play=play)
    esc = Escalation(ctx, session, sink, run_id, patient)
    flow = CheckFlow(sink=sink, say=speaker.say, recorder=recorder, run_id=run_id, source="call",
                     out_size=(OUT_W, OUT_H), patient_id=patient.id,
                     patient_name=patient.name or None, sentence=patient.sentence,
                     caregiver_phone=patient.caregiver.phone, caregiver_name=patient.caregiver.name,
                     escalate=esc.run, save_baseline=not patient.anonymous)
    esc.flow = flow
    media = CallMedia(source, flow)

    evaluating = {"task": None}

    def on_tick(now: float) -> None:
        flow.tick(now)
        if flow.needs_evaluation and evaluating["task"] is None:
            evaluating["task"] = media.spawn(flow.evaluate())

    media.on_tick = on_tick
    media.spawn(media.send_loop())

    def on_track(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log.info("[onset] subscribed %s/%s from %s", rtc.TrackKind.Name(track.kind),
                 rtc.TrackSource.Name(pub.source), p.identity)
        if p.identity != caller.identity:
            return
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            media.spawn(media.read_caller_video(track))
        elif track.kind == rtc.TrackKind.KIND_AUDIO:
            media.spawn(media.read_caller_audio(track))

    ctx.room.on("track_subscribed", on_track)

    def on_joined(p: rtc.RemoteParticipant) -> None:
        if is_caregiver(p):
            log.info("[onset] the caregiver joined the call (watching)")
            flow.caregiver_update("joined")
            media.spawn(esc.publish_summary())

    def on_left(p: rtc.RemoteParticipant) -> None:
        if is_caregiver(p) and flow.caregiver_status in ("joined", "invited"):
            flow.caregiver_update("left", "the caregiver closed the call page")

    ctx.room.on("participant_connected", on_joined)
    ctx.room.on("participant_disconnected", on_left)
    ctx.room.on("local_track_subscribed", lambda pub: log.info(
        "[onset] the caller side subscribed to our %s track", rtc.TrackKind.Name(pub.kind) if hasattr(pub, "kind") else pub))
    for pub in caller.track_publications.values():
        if pub.track is not None:
            on_track(pub.track, pub, caller)

    async def shutdown(reason: str = "") -> None:
        flow.abort("caller left" if not flow.done else "complete")
        # An escalation keeps going after the caller hangs up: the caregiver still gets the text.
        await flow.wait_background(timeout=45)
        await esc.aclose()
        await speaker.aclose()
        await media.aclose()
        if recorder is not None:
            await asyncio.to_thread(recorder.close)
        sink.close(reason or "call ended")
        await session.aclose()
        await source.aclose()

    ctx.add_shutdown_callback(shutdown)

    await session.start(
        agent=Agent(instructions="You are Onset. You only speak the scripted check-in lines."),
        room=ctx.room,
        room_options=RoomOptions(
            participant_identity=caller.identity,
            audio_input=False,  # we read the caller's microphone ourselves (CallMedia)
            video_input=False,
            text_input=False,
            text_output=False,
            audio_output=True,
            close_on_disconnect=True,
            delete_room_on_close=False,
        ),
    )
    speaker.start()

    # Start the check on Chert's bridge_ready signal (the Mac hangs up after 10 s of silence).
    def check_ready(*_args) -> None:
        if flow.state != "idle" or not bridge_ready(ctx.room.metadata):
            return
        log.info("[onset] FaceTime is ready: starting the check")
        flow.start(time.monotonic())
        media.spawn(watch_for_caller_video())

    async def watch_for_caller_video() -> None:
        try:
            await asyncio.wait_for(media.got_frame.wait(), CALLER_VIDEO_GRACE_S)
        except asyncio.TimeoutError:
            what = "a video track but no frames" if media.got_track.is_set() else "NO video track"
            log.error(
                "[onset] CALLER VIDEO MISSING: %.0f s after bridge_ready the caller sent %s. "
                "Caller tracks: %s. Job metadata: %s. Was the call placed with FaceTime Video?",
                CALLER_VIDEO_GRACE_S, what, describe_publications(caller), meta,
            )

    ctx.room.on("room_metadata_changed", check_ready)
    check_ready()


if __name__ == "__main__":
    cli.run_app(server)
