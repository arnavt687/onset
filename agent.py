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

from onset import brief, caregiver, overlay, patients, script, worker  # noqa: E402
from onset.events import EventSink  # noqa: E402
from onset.flow import CheckFlow  # noqa: E402
from onset.voice import Speaker  # noqa: E402

VOICE_STYLE = "Calm, warm and slow. Plain words. Speak to an older adult."

# Calm ElevenLabs voices on this account: Sarah (mature, reassuring; default),
# River (calm, neutral) SAz9YHcvj6GT2YYXdXww, Brian (comforting) nPczCjzI2devNBz1zQrb.
ELEVEN_DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"


def _eleven_tts():
    key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    return elevenlabs.TTS(
        api_key=key,
        voice_id=os.environ.get("ELEVEN_VOICE_ID", ELEVEN_DEFAULT_VOICE),
        model=os.environ.get("ELEVEN_MODEL", "eleven_turbo_v2_5"),
        voice_settings=elevenlabs.VoiceSettings(
            stability=0.75,  # steady, even delivery
            similarity_boost=0.75,
            speed=float(os.environ.get("ELEVEN_SPEED", "0.9")),  # a little slower than normal
        ),
    )


def _gemini_tts():
    if not os.environ.get("GOOGLE_API_KEY"):
        return None
    # Gemini's speech settings exist only in API v1beta; without this the SDK
    # can call v1 and Google answers 400 "Unknown name speechConfig".
    os.environ.setdefault("GOOGLE_GENAI_API_VERSION", "v1beta")
    return google.beta.GeminiTTS(
        model=os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
        voice_name=os.environ.get("GEMINI_VOICE", "Sulafat"),  # a warm voice
        instructions=VOICE_STYLE,
    )


def _openai_tts():
    return openai.TTS(model="gpt-4o-mini-tts", voice="coral", instructions=VOICE_STYLE)


_VOICES = {
    "elevenlabs": (_eleven_tts, None),
    "gemini": (_gemini_tts, None),
    "openai": (_openai_tts, "OPENAI_API_KEY"),
}


def _voice_order() -> list:
    """ONSET_VOICE_ORDER, e.g. "gemini,elevenlabs,openai" (default: elevenlabs, gemini, openai).
    Unknown names are ignored; voices left out are appended in the default order."""
    wanted = [v.strip().lower() for v in os.environ.get("ONSET_VOICE_ORDER", "").split(",") if v.strip()]
    order = [v for v in wanted if v in _VOICES]
    return order + [v for v in _VOICES if v not in order]


def make_tts():
    """The first configured voice in ONSET_VOICE_ORDER (default: ElevenLabs, then Gemini, then OpenAI)."""
    for name in _voice_order():
        engine = _VOICES[name][0]()
        if engine is not None:
            return engine
    return None


def make_tts_chain() -> list:
    """Every configured voice, best first: the first speaks, the others take over a line when
    it fails or is slow (onset.voice.Speaker). Never empty."""
    chain = []
    for build, needs in (_VOICES[name] for name in _voice_order()):
        if needs and not os.environ.get(needs):
            continue
        try:
            engine = build()
        except Exception as e:  # noqa: BLE001 - a voice whose plugin cannot start is skipped
            logging.getLogger("onset").warning("[onset] a voice could not start: %s", type(e).__name__)
            continue
        if engine is not None:
            chain.append(engine)
    return chain or [make_tts()]


log = logging.getLogger("onset")

AGENT_NAME = os.environ.get("AGENT_NAME", "onset")
# Upright by default (540x960): a Chert line with video_framing "fill" crops a wide picture
# down to the middle third, captions included. See overlay.output_size and overlay.safe_box.
OUT_W, OUT_H = overlay.output_size()
SEND_FPS = 15
# One encoding, not three: the only subscribers are Chert's Mac and at most one caregiver, and
# simulcast tripled the encoder's work for layers nobody watched. ONSET_SIMULCAST=1 restores it.
SIMULCAST = os.environ.get("ONSET_SIMULCAST", "0").strip() == "1"
VIDEO_BITRATE = int(min(3_000_000, max(600_000, OUT_W * OUT_H * 2.4)))
# How long after bridge_ready to wait for the caller's camera before declaring it missing.
CALLER_VIDEO_GRACE_S = 10.0
# Log loudly when the greeting starts later than this after bridge_ready (Chert's limit: 10 s).
GREET_WARN_S = 6.0

# Sized for one line on a laptop, the same in `start` and `dev` (onset/worker.py, issue #28):
# one idle process, never "at full capacity", any free health port (ONSET_HEALTH_PORT pins one).
SERVER_OPTIONS = worker.server_options()
server = AgentServer(**SERVER_OPTIONS)


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
        n = late = n_logged = 0
        late_logged = next_at
        while True:
            now = time.monotonic()
            try:
                if self.on_tick is not None:
                    self.on_tick(now)
                fresh = self.latest is not None and now - self.last_caller_frame <= 1.0
                if fresh or self.flow.state != "idle":
                    # Rendering (captions, landmarks, the dashboard JPEG) runs off the event
                    # loop: on it, it starved the agent's audio playout and speech fell behind.
                    img = await asyncio.to_thread(self.flow.render, self.latest if fresh else None, now)
                else:
                    img = placeholder(n)
                send(self.source, img)
            except Exception:  # noqa: BLE001 - keep the video alive whatever happens
                log.exception("[onset] send tick failed")
            n += 1
            next_at += period
            now = time.monotonic()
            if next_at < now - period:  # fell behind: skip the missed ticks, never send a burst
                late += 1
                next_at = now
            if now - late_logged >= 10.0:
                if late:
                    log.warning("[onset] video fell behind on %d of the last %d ticks (%dx%d @ %d fps)",
                                late, n - n_logged, OUT_W, OUT_H, SEND_FPS)
                late, late_logged, n_logged = 0, now, n
            await asyncio.sleep(max(0.0, next_at - now))

    async def read_caller_video(self, track: rtc.RemoteVideoTrack) -> None:
        self.got_track.set()
        # capacity=1: keep only the newest frame. Unbounded, every frame Onset was too busy to
        # take queued up behind the last one and the picture ran further and further behind.
        stream = rtc.VideoStream(track, capacity=1)
        window_start, window_frames, last_logged = time.monotonic(), 0, None
        try:
            async for event in stream:
                # Converting and rotating a camera frame is milliseconds of work: off the event
                # loop, where it delayed the agent's audio and the outgoing video.
                img = await asyncio.to_thread(to_rgba, event)
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
        self.invite = None
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

    async def welcome(self, identity: str) -> None:
        """The caregiver has joined: show them the summary and hand them the key to talk.

        The talk key goes to that one participant, inside the call, instead of in the link
        (a link cut short by a messaging app lost it: issue #30). Sent a few times, because a
        data packet is not kept for a page that is still setting up."""
        await self.publish_summary()
        token = self.invite.talk_token if self.invite is not None else None
        if not token:
            return
        payload = json.dumps({"type": "talk", "token": token})
        for delay in (0.0, 1.5, 4.0, 9.0):
            await asyncio.sleep(delay)
            try:
                await self.ctx.room.local_participant.publish_data(payload, reliable=True, topic="onset",
                                                                   destination_identities=[identity])
            except Exception as e:  # noqa: BLE001
                log.warning("[onset] could not hand the caregiver the talk key: %s", type(e).__name__)
                return

    async def run(self, decision, summary: str) -> dict:
        flow = self.flow
        self.summary, self.outcome = summary, decision.outcome.value
        first = caregiver.first_name(self.patient.name)
        await self._attrs(outcome=self.outcome, summary=summary, relay="off", patient=first)
        invite = None
        if self.cfg.join == "link" and not (flow is not None and flow.caller_gone):
            try:
                invite = caregiver.make_invite(
                    self.ctx.room.name, self.run_id, livekit_url=os.environ.get("LIVEKIT_URL", ""),
                    api_key=os.environ.get("LIVEKIT_API_KEY", ""), api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
                    cfg=self.cfg, patient_first=first)
            except Exception as e:  # noqa: BLE001 - the text still goes out, without a link
                log.warning("[onset] no caregiver link: %s", e)
                if flow is not None:
                    flow.caregiver_update("failed", f"no link: {type(e).__name__}")
        self.invite = invite
        if invite is not None:
            if invite.side_room:  # listen before anyone can have the link
                await self._start_relay(invite.side_room)
            if flow is not None:
                flow.caregiver_update("invited", "join link: " + (
                    "caregiver page" if invite.kind == "page" else "LiveKit Meet, watch only"))
            self.sink.private_file("caregiver_link.txt", invite.link)  # the dashboard's demo button
        self._start_caregiver_call(decision)
        return await caregiver.notify_caregiver(
            sink=flow if flow is not None else self.sink, summary=summary,
            caregiver_phone=self.patient.caregiver.phone, caregiver_name=self.patient.caregiver.name,
            patient_name=self.patient.name, invite=invite, talk=self.cfg.talk)

    def _start_caregiver_call(self, decision) -> None:
        """ONSET_CAREGIVER_CALL=after: once this call has ended, Onset FaceTimes the caregiver and
        tells them what it found. Chert carries one call per line and cannot add a second person,
        so it has to be a second call. A separate process places it (tools/caregiver_call.py):
        this job ends with the caller's call, and that process has to outlive it. It goes
        through the same safety gate as every other action (a dry run unless ONSET_LIVE_ACTIONS=1)."""
        phone = self.patient.caregiver.phone
        if self.cfg.call not in ("after", "group") or not phone:
            return
        own = self.patient.phones[0] if self.patient.phones else ""
        group = self.cfg.call == "group" and bool(own)
        if self.cfg.call == "group" and not own:
            log.warning("[onset] ONSET_CAREGIVER_CALL=group needs the person's own phone (ONSET_PATIENT_PHONE): "
                        "calling the caregiver alone instead")
        try:
            import subprocess
            from datetime import datetime

            info = brief.BriefInfo(patient=caregiver.first_name(self.patient.name),
                                   caregiver=caregiver.first_name(self.patient.caregiver.name),
                                   outcome=decision.outcome.value, reasons=tuple(decision.reasons or ())[:brief.MAX_REASONS],
                                   when=datetime.now().strftime("%a %-d %b at %-I:%M %p"), run_id=self.run_id, group=group)
            path = self.sink.private_file("caregiver_call.json", json.dumps(
                {**info.to_meta(), "to": phone, **({"patient_to": own} if group else {})}))
            if path is None:
                raise OSError("could not write the briefing file")
            logs = REPO / "runs" / "demo-logs"
            logs.mkdir(parents=True, exist_ok=True)
            with open(logs / f"caregiver-call-{self.run_id[:36]}.log", "ab") as out:
                subprocess.Popen([sys.executable, str(REPO / "tools" / "caregiver_call.py"), "--brief", str(path),
                                  "--wait-room", self.ctx.room.name, "--agent-name", AGENT_NAME],
                                 cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True)
            log.info("[onset] %s when this call ends", "a group FaceTime call follows" if group else "the caregiver will be FaceTimed")
            if self.flow is not None:
                self.flow.caregiver_note("Onset will ring the person and the caregiver together when this call ends" if group
                                         else "Onset will FaceTime the caregiver when this call ends")
            if group:  # the line carries one call: this one has to end before the group call can start
                self._spawn(self._hang_up_for_group())
        except Exception as e:  # noqa: BLE001 - the link and the text still go out
            log.warning("[onset] could not start the caregiver call: %s", type(e).__name__)

    async def _hang_up_for_group(self) -> None:
        """Tell the person Onset is calling straight back with their caregiver, then end this call."""
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        if self.flow is not None:
            self.flow.announce(script.CALLBACK, lambda: loop.call_soon_threadsafe(done.set))
        try:
            await asyncio.wait_for(done.wait(), 25.0)  # the closing line plays first, then this one
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(1.0)
        try:  # hang up through Chert when the room says which call this is; leaving the room does it too
            call_id = json.loads(self.ctx.room.metadata or "{}").get("chert_call")
            if call_id and os.environ.get("CHERT_API_KEY"):
                from onset import chert_api

                await chert_api.end_call(str(call_id))
        except Exception:  # noqa: BLE001
            log.debug("could not end the call through Chert", exc_info=True)
        log.info("[onset] ending this call: the group call follows")
        self.ctx.shutdown(reason="calling back as a group")

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
            if s == "page":  # the caregiver page's own report, e.g. the microphone is blocked
                if flow is not None:
                    flow.caregiver_note(detail)
                return
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
                log.info("[onset] relaying the caregiver's voice to the caller")
                handle = self.session.say(" ", audio=frames(), allow_interruptions=False, add_to_chat_ctx=False)
                await handle.wait_for_playout()
                log.info("[onset] caregiver relay ended")
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


async def brief_entry(ctx: JobContext, meta: dict) -> None:
    """The caregiver briefing call (onset/brief.py): no check, no camera reading. Onset waits,
    silent, for the caregiver to answer, then tells them what the check-in found and what to do.

    Chert's contract for a call Onset places (docs/outbound.md): be in the room first with one
    audio track and the camera track, stay silent until `chert_bridge_ready`, then speak."""
    info = brief.BriefInfo.from_meta(meta)
    log.info("[onset] %s for run %s (%s)", "group call with the person and the caregiver" if info.group
             else "caregiver briefing call", info.run_id or "?", info.outcome)
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_NONE)

    vsource = rtc.VideoSource(OUT_W, OUT_H)
    vtrack = rtc.LocalVideoTrack.create_video_track("onset-video", vsource)
    state = {"caption": brief.WAITING.caption}
    t0 = time.monotonic()
    send(vsource, brief.render(info, OUT_W, OUT_H, state["caption"], 0.0))
    await ctx.room.local_participant.publish_track(vtrack, rtc.TrackPublishOptions(
        source=rtc.TrackSource.SOURCE_CAMERA, simulcast=SIMULCAST,
        video_encoding=rtc.VideoEncoding(max_bitrate=VIDEO_BITRATE, max_framerate=SEND_FPS)))
    from onset import voice

    asource = rtc.AudioSource(voice.RATE, 1)
    atrack = rtc.LocalAudioTrack.create_audio_track("onset-voice", asource)
    await ctx.room.local_participant.publish_track(atrack, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    gone = asyncio.Event()

    async def video_loop() -> None:
        while not gone.is_set():
            try:
                img = await asyncio.to_thread(brief.render, info, OUT_W, OUT_H, state["caption"], time.monotonic() - t0)
                send(vsource, img)
            except Exception:  # noqa: BLE001 - keep the picture alive
                log.exception("[onset] briefing frame failed")
            await asyncio.sleep(1 / 8)

    async def play(pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype=np.int16)
        step = voice.RATE // 50  # 20 ms frames; capture_frame paces itself
        for i in range(0, len(samples), step):
            if gone.is_set():
                return
            chunk = samples[i:i + step]
            await asource.capture_frame(rtc.AudioFrame(chunk.tobytes(), voice.RATE, 1, len(chunk)))
        await asource.wait_for_playout()

    video = asyncio.create_task(video_loop())
    voices = make_tts_chain()
    speaker = Speaker(voices[0], fallbacks=voices[1:])
    said = brief.script(info)
    # Synthesize while the phone rings, so the first words follow bridge_ready at once.
    audio = asyncio.gather(*(speaker.synth(ln.say, budget_s=12.0) for ln in said), return_exceptions=True)

    ready = asyncio.Event()

    def check_ready(*_a) -> None:
        if bridge_ready(ctx.room.metadata):
            ready.set()

    ctx.room.on("room_metadata_changed", check_ready)
    ctx.room.on("participant_disconnected", lambda p: gone.set() if is_caller(p) else None)
    check_ready()
    try:
        try:
            await asyncio.wait_for(ready.wait(), brief.RING_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.info("[onset] the caregiver did not answer within %.0f s", brief.RING_TIMEOUT_S)
            return
        log.info("[onset] the caregiver answered: briefing them")
        clips = await audio
        for ln, pcm in zip(said, clips):
            if gone.is_set():
                break
            state["caption"] = ln.caption
            if isinstance(pcm, (bytes, bytearray)) and pcm:
                await play(bytes(pcm))
            else:
                await asyncio.sleep(2.5)  # no voice for this line: the caption carries it
            await asyncio.sleep(0.45)
        log.info("[onset] briefing %s", "cut short: the caregiver hung up" if gone.is_set() else "finished")
        await asyncio.sleep(1.5)
    finally:
        gone.set()
        video.cancel()
        if not audio.done():
            audio.cancel()
        await asyncio.gather(video, return_exceptions=True)
        await speaker.aclose()
        ctx.shutdown(reason="briefing finished")


@server.rtc_session(agent_name=AGENT_NAME)
async def entry(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}") if ctx.job.metadata else {}
    log.info("[onset] job for room %s, metadata %s", ctx.room.name, {k: v for k, v in meta.items() if k != "reasons"})
    if brief.is_brief(meta):
        await brief_entry(ctx, meta)
        return
    # Join at once (Chert keeps a warm room ready before any call); subscribe to
    # audio AND video so the caller's camera and microphone reach us.
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    voices = make_tts_chain()
    tts, fallbacks = voices[0], voices[1:]
    from onset.decide import load_baseline
    from onset.speech import SENTENCES

    patient = patients.lookup(patients.handle_from_metadata(meta))
    log.info("[onset] patient profile %s (matched by %s)", patient.id, patient.matched_by)
    try:
        b = None if patient.anonymous else load_baseline(patient.id)
    except Exception:  # noqa: BLE001
        b = None
    warm_speaker = Speaker(tts, fallbacks=fallbacks)
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
        track,
        rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA,
            simulcast=SIMULCAST,
            video_encoding=rtc.VideoEncoding(max_bitrate=VIDEO_BITRATE, max_framerate=SEND_FPS),
        ),
    )
    log.info("[onset] worker: %s", worker.describe(SERVER_OPTIONS))
    log.info("[onset] sending %dx%d video @ %d fps, %d kbps, simulcast %s", OUT_W, OUT_H, SEND_FPS,
             VIDEO_BITRATE // 1000, "on" if SIMULCAST else "off")

    # No TTS in the session: Onset synthesizes every line itself (onset.voice, with fallbacks)
    # and hands the session finished audio. A TTS shared with the session would let three
    # provider errors close the session (max_unrecoverable_errors) and silence the call.
    session = AgentSession()
    session.on("agent_state_changed", lambda ev: log.info("[onset] agent state %s -> %s", ev.old_state, ev.new_state))
    session.on("error", lambda ev: log.error("[onset] SESSION ERROR from %s: %r", type(ev.source).__name__, ev.error))

    timing = {"ready": None, "greeted": False}

    async def play(text: str, pcm: bytes, rate: int) -> None:
        if not timing["greeted"] and timing["ready"] is not None:
            # Chert hangs up if the agent is silent for 10 s after bridge_ready.
            timing["greeted"] = True
            latency = time.monotonic() - timing["ready"]
            (log.warning if latency > GREET_WARN_S else log.info)(
                "[onset] greeting starts %.2f s after bridge_ready (Chert's limit is 10 s)", latency)
            sink.emit("greeting", latency_s=round(latency, 3), limit_s=10.0)
        samples = np.frombuffer(pcm, dtype=np.int16)
        step = rate // 50  # 20 ms frames

        async def frames():
            for i in range(0, len(samples), step):
                chunk = samples[i:i + step]
                yield rtc.AudioFrame(chunk.tobytes(), rate, 1, len(chunk))

        handle = session.say(text, audio=frames(), allow_interruptions=False, add_to_chat_ctx=False)
        await handle.wait_for_playout()

    speaker = Speaker(tts, sink=sink, play=play, fallbacks=fallbacks)
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
            media.spawn(esc.welcome(p.identity))

    def on_left(p: rtc.RemoteParticipant) -> None:
        if is_caregiver(p) and flow.caregiver_status in ("joined", "invited"):
            flow.caregiver_update("left", "the caregiver closed the call page")

    ctx.room.on("participant_connected", on_joined)
    ctx.room.on("participant_disconnected", on_left)

    # Network blips: LiveKit resumes the connection by itself; the flow keeps its clock and
    # state, and the caller's tracks are read again when they are re-subscribed.
    def on_conn(status: str):
        def handler(*_a) -> None:
            log.warning("[onset] connection %s", status) if status != "reconnected" else log.info("[onset] connection reconnected")
            sink.emit("connection", status=status)
        return handler

    ctx.room.on("reconnecting", on_conn("reconnecting"))
    ctx.room.on("reconnected", on_conn("reconnected"))
    ctx.room.on("local_track_subscribed", lambda pub: log.info(
        "[onset] the caller side subscribed to our %s track", rtc.TrackKind.Name(pub.kind) if hasattr(pub, "kind") else pub))
    for pub in caller.track_publications.values():
        if pub.track is not None:
            on_track(pub.track, pub, caller)

    async def shutdown(reason: str = "") -> None:
        # Hung up during "One moment": every check is in, so decide anyway. On a change the
        # caregiver must still hear about it, even though the caller can no longer.
        flow.hangup(time.monotonic())
        if not flow.done and (flow.needs_evaluation or evaluating["task"] is not None):
            log.info("[onset] the caller left before the decision: finishing it")
            try:
                task = evaluating["task"] or asyncio.ensure_future(flow.evaluate())
                await asyncio.wait_for(asyncio.shield(task), 30)
            except Exception:  # noqa: BLE001
                log.exception("[onset] the decision after hang-up failed")
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
        timing["ready"] = time.monotonic()
        flow.start(timing["ready"])
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
