"""End-to-end test of Onset without FaceTime: plays Chert's part in a private LiveKit room.

  1. creates a room and dispatches the agent to it (by AGENT_NAME), as chert dev does;
  2. joins as "chert-bridge-test", publishing a 1280x720 15 fps test camera and silent audio;
  3. after a few seconds sets the room metadata {"chert_bridge_ready": true};
  4. measures what comes back: when the agent's audio becomes audible, how much audible
     audio there is, and the agent's video size and frame rate;
  5. leaves and deletes the room.

Costs: a few seconds of LiveKit free tier and one greeting's worth of TTS characters.
Usage: .venv/bin/python tools/fake_call.py [seconds]
"""

import asyncio
import json
import math
import os
import sys
import time
import uuid
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
AGENT = os.environ["AGENT_NAME"]
URL = os.environ["LIVEKIT_URL"]
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
READY_AFTER = 4.0          # seconds after the agent joins, like FaceTime connecting
AUDIBLE_DBFS = -45.0       # louder than this counts as audible


def dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = math.sqrt(float(np.mean(samples.astype(np.float64) ** 2)))
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


async def main() -> None:
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
    result = {"agent_joined": None, "ready_at": None, "first_audible": None, "audible_s": 0.0,
              "peak_dbfs": -120.0, "agent_video": None, "agent_video_frames": 0, "agent_tracks": []}
    tasks: list[asyncio.Task] = []

    async def listen_audio(track: rtc.Track) -> None:
        stream = rtc.AudioStream(track, sample_rate=24000, num_channels=1)
        async for ev in stream:
            s = np.frombuffer(ev.frame.data, dtype=np.int16)
            level = dbfs(s)
            result["peak_dbfs"] = max(result["peak_dbfs"], level)
            if level > AUDIBLE_DBFS:
                result["audible_s"] += len(s) / ev.frame.sample_rate
                if result["first_audible"] is None:
                    result["first_audible"] = time.monotonic() - t0
                    print(stamp(), f"first audible agent audio ({level:.1f} dBFS)")

    async def watch_video(track: rtc.Track) -> None:
        stream = rtc.VideoStream(track)
        async for ev in stream:
            result["agent_video_frames"] += 1
            result["agent_video"] = f"{ev.frame.width}x{ev.frame.height}"

    @room.on("participant_connected")
    def _joined(p: rtc.RemoteParticipant) -> None:
        print(stamp(), "joined:", p.identity, rtc.ParticipantKind.Name(p.kind))
        if result["agent_joined"] is None:
            result["agent_joined"] = time.monotonic() - t0

    @room.on("track_subscribed")
    def _sub(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        kind = rtc.TrackKind.Name(track.kind)
        print(stamp(), f"agent published {kind}/{rtc.TrackSource.Name(pub.source)} (muted={pub.muted})")
        result["agent_tracks"].append(kind)
        tasks.append(asyncio.create_task(
            listen_audio(track) if track.kind == rtc.TrackKind.KIND_AUDIO else watch_video(track)))

    await room.connect(URL, token)
    print(stamp(), "connected to", room_name)

    # Our "phone": a moving bar on 1280x720 at 15 fps, plus silent audio, as the bridge publishes.
    vsrc = rtc.VideoSource(1280, 720)
    await room.local_participant.publish_track(
        rtc.LocalVideoTrack.create_video_track("cam", vsrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    asrc = rtc.AudioSource(48000, 1)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    async def feed() -> None:
        n = 0
        frame = np.zeros((720, 1280, 4), dtype=np.uint8)
        silence = rtc.AudioFrame(b"\0" * 960 * 2, 48000, 1, 960)
        while True:
            frame[..., :3] = 60
            x = (n * 20) % 1280
            frame[:, x:x + 40, :3] = 220
            frame[..., 3] = 255
            vsrc.capture_frame(rtc.VideoFrame(1280, 720, rtc.VideoBufferType.RGBA, frame.tobytes()))
            for _ in range(3):  # ~66 ms of silence per video frame
                await asrc.capture_frame(silence)
            n += 1
            await asyncio.sleep(1 / 15 - 0.06)

    tasks.append(asyncio.create_task(feed()))

    while result["agent_joined"] is None and time.monotonic() - t0 < 20:
        await asyncio.sleep(0.2)
    if result["agent_joined"] is None:
        print("AGENT NEVER JOINED (is agent.py running and registered as", AGENT, "?)")
    else:
        await asyncio.sleep(READY_AFTER)
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(
            room=room_name, metadata=json.dumps({"chert_call": "test", "chert_bridge_ready": True})))
        result["ready_at"] = time.monotonic() - t0
        print(stamp(), "set chert_bridge_ready")
        await asyncio.sleep(DURATION)

    for t in tasks:
        t.cancel()
    await room.disconnect()
    await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    await lk.aclose()

    r = result
    after_ready = (r["first_audible"] - r["ready_at"]) if r["first_audible"] and r["ready_at"] else None
    print("\n=== result")
    print("agent tracks:", r["agent_tracks"] or "none")
    print("agent video:", r["agent_video"], f"{r['agent_video_frames']} frames")
    print("greeting audible:", "NO" if r["first_audible"] is None else
          f"yes, {after_ready:.2f}s after bridge_ready (Chert's limit is 10 s)")
    print(f"audible audio: {r['audible_s']:.1f}s, peak {r['peak_dbfs']:.1f} dBFS")


asyncio.run(main())
