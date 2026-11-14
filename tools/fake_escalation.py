"""End-to-end test of the caregiver joining a live call, in a private LiveKit room (no FaceTime).

It plays Chert's part and the caregiver's:

  1. creates a room, dispatches the agent (AGENT_NAME), joins as `chert-bridge-esctest` with a
     test-pattern camera and a silent microphone, and sets `chert_bridge_ready`;
  2. the check runs; the test pattern has no face, so it ends UNCLEAR, and the agent escalates:
     it writes the caregiver link to runs/live/caregiver_link.txt (the iMessage is a dry run);
  3. joins the call with the link's WATCH token and checks it cannot publish, then reads the
     summary from the agent's attributes;
  4. joins the side room with the TALK token and publishes a 440 Hz tone as the caregiver's
     microphone plus a camera, and checks that the fake caller hears the tone in the AGENT's
     audio track (the relay) and that the agent's events go invited -> joined -> talking;
  5. leaves and deletes the room. Exit code 0 only if every check passed.

The agent must run with a patient that has a baseline (so the result is UNCLEAR, not a first
call) and ONSET_CAREGIVER_PAGE set (so the link has a talk token): see
tools/fake_escalation_private.sh, which sets all of this up.
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
from urllib.parse import parse_qs, unquote, urlsplit

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
RUNS = Path(os.environ.get("ONSET_RUNS_DIR", REPO / "runs"))
LIVE = RUNS / "live"
TONE_HZ = 440.0


def read_events() -> list[dict]:
    try:
        return [json.loads(x) for x in (LIVE / "events.jsonl").read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def tone_power(samples: np.ndarray, rate: int, hz: float = TONE_HZ) -> float:
    """Share of the signal's energy near `hz` (0..1)."""
    if samples.size < rate // 10:
        return 0.0
    x = samples.astype(np.float64) * np.hanning(samples.size)
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(samples.size, 1 / rate)
    total = spec[(freqs > 80) & (freqs < 4000)].sum()
    near = spec[(freqs > hz - 25) & (freqs < hz + 25)].sum()
    return float(near / total) if total > 0 else 0.0


def parse_link(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(link).fragment).items()}


def check(events: list[dict], r: dict) -> list[str]:
    """Failures, from the agent's events and what the fake caller/caregiver observed."""
    fails = []
    dec = [e for e in events if e.get("type") == "decision"]
    if not dec or dec[0].get("outcome") not in ("escalate", "unclear"):
        fails.append(f"no escalate/unclear decision (got {dec[0].get('outcome') if dec else None})")
    st = [e.get("status") for e in events if e.get("type") == "caregiver_join"]
    for need in ("invited", "joined", "talking"):
        if need not in st:
            fails.append(f"caregiver_join never reached {need!r} (saw {st})")
    acts = [e for e in events if e.get("type") == "action"]
    if not acts or any(a.get("live") for a in acts):
        fails.append(f"expected only dry-run action events (saw {[(a.get('kind'), a.get('live')) for a in acts]})")
    if not r.get("link"):
        fails.append("no caregiver link was written")
    if r.get("watch_could_publish"):
        fails.append("the watch token could publish audio into the call (Chert would end it)")
    if not r.get("summary"):
        fails.append("the caregiver never received the summary (attributes or data)")
    if r.get("relay_tone", 0.0) < 0.3:
        fails.append(f"the caller did not hear the caregiver through Onset (tone share {r.get('relay_tone', 0):.2f})")
    if r.get("relay_attr") != "on":
        fails.append(f"agent attribute onset.relay never 'on' (last {r.get('relay_attr')!r})")
    return fails


async def main(a: argparse.Namespace) -> int:
    from dotenv import load_dotenv
    from livekit import api, rtc

    load_dotenv(REPO / ".env.local")
    agent_name, url = os.environ["AGENT_NAME"], os.environ["LIVEKIT_URL"]
    lk = api.LiveKitAPI()
    room_name = f"onset-esctest-{uuid.uuid4().hex[:8]}"
    await lk.room.create_room(api.CreateRoomRequest(name=room_name, empty_timeout=60))
    await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
        agent_name=agent_name, room=room_name, metadata=json.dumps({"chert_call": "esctest", "media": {"audio": True, "video": True}})))
    tok = (api.AccessToken().with_identity("chert-bridge-esctest")
           .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())
    t0 = time.monotonic()

    def say(*m):
        print(f"{time.monotonic() - t0:6.1f}s", *m, flush=True)

    r: dict = {"relay_tone": 0.0}
    caller = rtc.Room()
    tasks: list[asyncio.Task] = []
    agent_audio: list[tuple[float, np.ndarray]] = []

    async def hear(track):
        async for ev in rtc.AudioStream(track, sample_rate=16000, num_channels=1):
            agent_audio.append((time.monotonic(), np.frombuffer(ev.frame.data, np.int16).copy()))

    @caller.on("track_subscribed")
    def _sub(track, pub, p):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.create_task(hear(track)))

    await caller.connect(url, tok)
    vsrc = rtc.VideoSource(640, 360)
    await caller.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track("cam", vsrc),
                                                 rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    asrc = rtc.AudioSource(48000, 1)
    await caller.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("mic", asrc),
                                                 rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    async def feed():
        frame = np.full((360, 640, 4), 60, np.uint8)
        frame[..., 3] = 255
        silence = np.zeros(480, np.int16).tobytes()
        n = 0
        while True:
            vsrc.capture_frame(rtc.VideoFrame(640, 360, rtc.VideoBufferType.RGBA, frame.tobytes()))
            for _ in range(7):
                await asrc.capture_frame(rtc.AudioFrame(silence, 48000, 1, 480))
            n += 1
            await asyncio.sleep(0.0)

    tasks.append(asyncio.create_task(feed()))
    say("caller connected to", room_name)
    for _ in range(100):
        if any(p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT for p in caller.remote_participants.values()):
            break
        await asyncio.sleep(0.2)
    else:
        say("AGENT NEVER JOINED")
        return 2
    await asyncio.sleep(3)
    await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(
        room=room_name, metadata=json.dumps({"chert_call": "esctest", "chert_bridge_ready": True})))
    say("bridge_ready set; waiting for the escalation (about a minute)")

    link_file = LIVE / "caregiver_link.txt"
    deadline = time.monotonic() + a.seconds
    while time.monotonic() < deadline and not link_file.exists():
        await asyncio.sleep(0.5)
    if not link_file.exists():
        say("no caregiver link appeared")
        r["link"] = None
    else:
        r["link"] = link = link_file.read_text().strip()
        f = parse_link(link)
        say("caregiver link written (", "page" if "t" in f else "meet", ")")
        watch = rtc.Room()
        await watch.connect(unquote(f["u"]), f["w"])
        say("caregiver watching")
        try:
            src = rtc.AudioSource(48000, 1)
            await asyncio.wait_for(watch.local_participant.publish_track(
                rtc.LocalAudioTrack.create_audio_track("x", src), rtc.TrackPublishOptions()), 5)
            r["watch_could_publish"] = True
        except Exception:  # noqa: BLE001 - expected: no publish permission
            r["watch_could_publish"] = False

        @watch.on("data_received")
        def _data(pkt):
            try:
                if pkt.topic == "onset":
                    r["summary"] = r.get("summary") or json.loads(pkt.data).get("text")
            except Exception:  # noqa: BLE001
                pass

        def agent_attr(name):
            for p in watch.remote_participants.values():
                if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
                    return p.attributes.get(name)
            return None

        for _ in range(20):
            r["summary"] = r.get("summary") or agent_attr("onset.summary")
            if r["summary"]:
                break
            await asyncio.sleep(0.5)
        say("summary seen by the caregiver:", bool(r.get("summary")))

        if "t" in f:
            talk = rtc.Room()
            await talk.connect(unquote(f["u"]), f["t"])
            tsrc = rtc.AudioSource(48000, 1)
            await talk.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("cg-mic", tsrc),
                                                       rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            cvs = rtc.VideoSource(320, 240)
            await talk.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track("cg-cam", cvs),
                                                       rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
            say("caregiver talking: a 440 Hz tone")
            t_talk = time.monotonic()
            k = np.arange(480)
            green = np.zeros((240, 320, 4), np.uint8)
            green[..., 1], green[..., 3] = 200, 255
            n = 0
            while time.monotonic() - t_talk < a.talk_seconds:
                pcm = (8000 * np.sin(2 * math.pi * TONE_HZ * (k + n * 480) / 48000)).astype(np.int16)
                await tsrc.capture_frame(rtc.AudioFrame(pcm.tobytes(), 48000, 1, 480))
                if n % 7 == 0:
                    cvs.capture_frame(rtc.VideoFrame(320, 240, rtc.VideoBufferType.RGBA, green.tobytes()))
                n += 1
                if agent_attr("onset.relay") == "on":
                    r["relay_attr"] = "on"
            heard = [s for t, s in agent_audio if t >= time.monotonic() - a.measure_seconds]
            if heard:
                r["relay_tone"] = tone_power(np.concatenate(heard), 16000)
            say(f"tone share in the agent's audio while the caregiver talked: {r['relay_tone']:.2f}")
            await talk.disconnect()
        await asyncio.sleep(1.5)
        await watch.disconnect()

    for t in tasks:
        t.cancel()
    await caller.disconnect()
    await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    await lk.aclose()

    events = read_events()
    print("\n=== agent events")
    for e in events:
        if e.get("type") in ("decision", "caregiver_join", "action", "caregiver", "run_ended"):
            brief = {k: v for k, v in e.items() if k not in ("t", "type", "text", "reasons")}
            print(f"  {e.get('t', 0):6.2f}  {e['type']:<15} {json.dumps(brief)[:160]}")
    fails = check(events, r)
    print("\n=== " + ("PASS: the caregiver joined, saw the summary, and the caller heard them through Onset"
                      if not fails else "FAIL"))
    for f in fails:
        print("  -", f)
    return 0 if not fails else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=150.0, help="max seconds to wait for the escalation")
    p.add_argument("--talk-seconds", type=float, default=16.0, help="how long the caregiver talks")
    p.add_argument("--measure-seconds", type=float, default=5.0,
                   help="the last N seconds of talking must carry the tone (Onset's own lines play first)")
    sys.exit(asyncio.run(main(p.parse_args())))
