"""End-to-end test of Onset without FaceTime: plays Chert's part in a private LiveKit room.

  1. creates a room and dispatches the agent to it (by AGENT_NAME), as chert dev does;
  2. joins as "chert-bridge-test", publishing a camera (a video file with --video, else a
     moving test pattern) at 1280x720 15 fps and a microphone (silence);
  3. after a few seconds sets the room metadata {"chert_bridge_ready": true};
  4. with --speak, plays the sentence as the caller's voice (ElevenLabs TTS) once the agent
     shows "Say it now" (read from runs/live/events.jsonl, which the agent writes);
  5. measures what comes back (agent audio level, agent video size and frame rate), saves a
     few of the agent's frames (--frames DIR), and prints the agent's event timeline;
  6. leaves and deletes the room.

Usage: .venv/bin/python tools/fake_call.py [seconds] [--video FILE] [--speak] [--frames DIR]
Costs: LiveKit free tier; TTS characters for lines not already cached (and --speak).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv
from livekit import api, rtc

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env.local")
AGENT = os.environ["AGENT_NAME"]
URL = os.environ["LIVEKIT_URL"]
READY_AFTER = 4.0
AUDIBLE_DBFS = -45.0
EVENTS = REPO / "runs" / "live" / "events.jsonl"


def dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = math.sqrt(float(np.mean(samples.astype(np.float64) ** 2)))
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


def read_events() -> list[dict]:
    try:
        return [json.loads(x) for x in EVENTS.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


async def caller_voice(text: str) -> np.ndarray:
    """The sentence as 48 kHz mono int16, spoken by ElevenLabs (the fake caller)."""
    from livekit.agents.utils import http_context
    from livekit.plugins import elevenlabs

    from onset.voice import _resample

    key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    async with http_context.open():
        tts = elevenlabs.TTS(api_key=key, voice_id="nPczCjzI2devNBz1zQrb")  # a different voice than Onset's
        chunks, rate = [], 22050
        async with tts.synthesize(text) as stream:
            async for ev in stream:
                rate = ev.frame.sample_rate
                chunks.append(np.frombuffer(bytes(ev.frame.data), np.int16))
        await tts.aclose()
    return _resample(np.concatenate(chunks), rate, 48000)


async def main(a: argparse.Namespace) -> None:
    lk = api.LiveKitAPI()
    room_name = f"onset-test-{uuid.uuid4().hex[:8]}"
    await lk.room.create_room(api.CreateRoomRequest(name=room_name, empty_timeout=30))
    await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
        agent_name=AGENT, room=room_name,
        metadata=json.dumps({"chert_call": "test", "media": {"audio": True, "video": True}})))
    token = (api.AccessToken().with_identity("chert-bridge-test").with_name("fake bridge")
             .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())

    room = rtc.Room()
    t0 = time.monotonic()
    stamp = lambda: f"{time.monotonic() - t0:6.2f}s"  # noqa: E731
    r = {"agent_joined": None, "ready_at": None, "first_audible": None, "audible_s": 0.0, "peak": -120.0,
         "video": None, "frames": 0, "saved": 0}
    tasks: list[asyncio.Task] = []
    frames_dir = Path(a.frames) if a.frames else None
    if frames_dir:
        frames_dir.mkdir(parents=True, exist_ok=True)

    async def listen_audio(track):
        async for ev in rtc.AudioStream(track, sample_rate=24000, num_channels=1):
            s = np.frombuffer(ev.frame.data, dtype=np.int16)
            lv = dbfs(s)
            r["peak"] = max(r["peak"], lv)
            if lv > AUDIBLE_DBFS:
                r["audible_s"] += len(s) / ev.frame.sample_rate
                if r["first_audible"] is None:
                    r["first_audible"] = time.monotonic() - t0
                    print(stamp(), f"first audible agent audio ({lv:.1f} dBFS)")

    async def watch_video(track):
        last_save = 0.0
        async for ev in rtc.VideoStream(track):
            r["frames"] += 1
            r["video"] = f"{ev.frame.width}x{ev.frame.height}"
            if frames_dir and time.monotonic() - last_save > a.save_every:
                last_save = time.monotonic()
                f = ev.frame.convert(rtc.VideoBufferType.RGBA)
                img = np.frombuffer(f.data, np.uint8).reshape(f.height, f.width, 4)
                cv2.imwrite(str(frames_dir / f"agent_{r['saved']:03d}.png"), cv2.cvtColor(img, cv2.COLOR_RGBA2BGR))
                r["saved"] += 1

    @room.on("participant_connected")
    def _joined(p):
        print(stamp(), "joined:", p.identity, rtc.ParticipantKind.Name(p.kind))
        if r["agent_joined"] is None:
            r["agent_joined"] = time.monotonic() - t0

    @room.on("track_subscribed")
    def _sub(track, pub, p):
        print(stamp(), f"agent published {rtc.TrackKind.Name(track.kind)}/{rtc.TrackSource.Name(pub.source)}")
        tasks.append(asyncio.create_task(listen_audio(track) if track.kind == rtc.TrackKind.KIND_AUDIO else watch_video(track)))

    await room.connect(URL, token)
    print(stamp(), "connected to", room_name)
    vsrc = rtc.VideoSource(1280, 720)
    await room.local_participant.publish_track(
        rtc.LocalVideoTrack.create_video_track("cam", vsrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    asrc = rtc.AudioSource(48000, 1)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    speech = np.zeros(0, np.int16)
    speak_at: list[float] = []
    if a.speak:
        try:
            sentence = a.sentence or __import__("onset.speech", fromlist=["SENTENCES"]).SENTENCES[0]
            speech = await caller_voice(sentence)
            print(stamp(), f"caller voice ready: {len(speech) / 48000:.1f} s")
        except Exception as e:  # noqa: BLE001
            print("could not make the caller's voice:", e)

    cap = cv2.VideoCapture(a.video) if a.video else None

    async def feed_video():
        n = 0
        frame = np.zeros((720, 1280, 4), np.uint8)
        while True:
            img = None
            if cap is not None:
                ok, bgr = cap.read()
                if not ok:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, bgr = cap.read()
                if ok:
                    img = cv2.cvtColor(cv2.resize(bgr, (1280, 720)), cv2.COLOR_BGR2RGBA)
            if img is None:
                frame[..., :3] = 60
                x = (n * 20) % 1280
                frame[:, x:x + 40, :3] = 220
                frame[..., 3] = 255
                img = frame
            vsrc.capture_frame(rtc.VideoFrame(1280, 720, rtc.VideoBufferType.RGBA, img.tobytes()))
            n += 1
            await asyncio.sleep(1 / 15)

    async def feed_audio():
        pos = 0
        silence = np.zeros(960, np.int16)
        while True:
            chunk = silence
            if speak_at and pos < len(speech):
                chunk = speech[pos:pos + 960]
                if len(chunk) < 960:
                    chunk = np.pad(chunk, (0, 960 - len(chunk)))
                pos += 960
            await asrc.capture_frame(rtc.AudioFrame(chunk.tobytes(), 48000, 1, 960))

    async def watch_events():
        while True:
            ev = read_events()
            if a.speak and not speak_at and any(e.get("type") == "step" and str(e.get("caption", "")).startswith("Say it now") for e in ev):
                speak_at.append(time.monotonic() - t0)
                print(stamp(), "agent says 'Say it now': the fake caller speaks")
            await asyncio.sleep(0.2)

    tasks += [asyncio.create_task(feed_video()), asyncio.create_task(feed_audio()), asyncio.create_task(watch_events())]

    while r["agent_joined"] is None and time.monotonic() - t0 < 20:
        await asyncio.sleep(0.2)
    if r["agent_joined"] is None:
        print("AGENT NEVER JOINED (is agent.py running and registered as", AGENT, "?)")
    else:
        await asyncio.sleep(READY_AFTER)
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(
            room=room_name, metadata=json.dumps({"chert_call": "test", "chert_bridge_ready": True})))
        r["ready_at"] = time.monotonic() - t0
        print(stamp(), "set chert_bridge_ready")
        end = time.monotonic() + a.seconds
        while time.monotonic() < end:
            if any(e.get("type") == "run_ended" for e in read_events()):
                await asyncio.sleep(3)  # let the closing line play
                break
            await asyncio.sleep(0.5)

    for t in tasks:
        t.cancel()
    await room.disconnect()
    await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    await lk.aclose()

    after = (r["first_audible"] - r["ready_at"]) if r["first_audible"] and r["ready_at"] else None
    print("\n=== result")
    print("agent video:", r["video"], f"{r['frames']} frames", f"({r['saved']} saved to {frames_dir})" if frames_dir else "")
    print("first agent audio:", "NONE" if r["first_audible"] is None else f"{after:.2f}s after bridge_ready (limit 10 s)")
    print(f"audible agent audio: {r['audible_s']:.1f}s, peak {r['peak']:.1f} dBFS")
    print("\n=== agent events (runs/live/events.jsonl)")
    for e in read_events():
        typ = e.get("type")
        if typ == "live":
            continue
        brief = {k: v for k, v in e.items() if k not in ("t", "type", "data")}
        if typ == "result":
            brief = {"check": e.get("check"), "quality": e.get("quality"),
                     "reason": (e.get("data") or {}).get("detail", {}).get("reason")}
        print(f"  {e.get('t', 0):6.2f}  {typ:<12} {json.dumps(brief)[:180]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("seconds", nargs="?", type=float, default=100.0, help="max seconds after bridge_ready")
    p.add_argument("--video", help="video file to use as the caller's camera (looped)")
    p.add_argument("--speak", action="store_true", help="the fake caller says the sentence when asked")
    p.add_argument("--sentence", help="what the fake caller says (default: the first standard sentence)")
    p.add_argument("--frames", help="save the agent's video frames here (PNG)")
    p.add_argument("--save-every", type=float, default=2.0, help="seconds between saved frames")
    asyncio.run(main(p.parse_args()))
