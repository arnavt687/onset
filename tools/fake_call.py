"""End-to-end test of Onset without FaceTime: plays Chert's part in a private LiveKit room.

  1. creates a room and dispatches the agent to it (by AGENT_NAME), as chert dev does;
  2. joins as "chert-bridge-test", publishing a camera (a video file with --video, else a
     moving test pattern) at 1280x720 15 fps and a microphone (silence);
  3. after a few seconds sets the room metadata {"chert_bridge_ready": true};
  4. with --speak, plays the sentence as the caller's voice (ElevenLabs TTS) once the agent
     shows "Say it now" (read from runs/live/events.jsonl, which the agent writes). The
     sentence is the one the agent announces in its run_started event, unless --sentence;
  5. measures what comes back (agent audio level, agent video size and frame rate), saves a
     few of the agent's frames (--frames DIR), and prints the agent's event timeline;
  6. leaves and deletes the room.

With --assert (implies --speak) it exits 0 only if audio worked both ways (see check_run):
  (a) agent audio is audible within 10 s of bridge_ready (Chert's greet deadline);
  (b) the speech result is quality ok with a transcript matching the sentence (WER <= 0.1);
  (c) run_ended arrives;
  (d) with voice activity on (ONSET_SPEECH_VAD unset or 1), the vad event says speech_end:
      the recording ended because the caller stopped talking, before the timer.

Usage: .venv/bin/python tools/fake_call.py [seconds] [--video FILE] [--speak] [--assert] [--frames DIR]
Keys come from .env.local next to this checkout, else ~/onset/.env.local (never printed).
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

import numpy as np

REPO = Path(__file__).resolve().parent.parent
READY_AFTER = 4.0
AUDIBLE_DBFS = -45.0
GREET_DEADLINE_S = 10.0  # Chert: greet within 10 s of bridge_ready
MAX_WER = 0.1
EVENTS = REPO / "runs" / "live" / "events.jsonl"
CALLER_VOICE = "nPczCjzI2devNBz1zQrb"  # a different voice than Onset's


def load_env() -> None:
    """Keys from this checkout's .env.local, then the main checkout's. Never overrides the
    environment (so AGENT_NAME from fake_call_private.sh wins)."""
    from dotenv import load_dotenv

    for path in (REPO / ".env.local", Path.home() / "onset" / ".env.local"):
        if path.is_file():
            load_dotenv(path, override=False)


def dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = math.sqrt(float(np.mean(samples.astype(np.float64) ** 2)))
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


def read_events(path: Path = EVENTS) -> list[dict]:
    try:
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------------------- pass / fail


def _speech_result(events: list[dict]) -> dict | None:
    return next((e for e in reversed(events) if e.get("type") == "result" and e.get("check") == "speech"), None)


def run_sentence(events: list[dict]) -> str | None:
    """The sentence the agent asked for (its run_started event)."""
    return next((e.get("sentence") for e in events if e.get("type") == "run_started" and e.get("sentence")), None)


def check_run(events: list[dict], audio: dict, *, sentence: str | None = None, expect_vad: bool = True,
              greet_deadline_s: float = GREET_DEADLINE_S, max_wer: float = MAX_WER) -> list[str]:
    """Why this fake call failed (empty list: it passed). Pure: events from runs/live/events.jsonl,
    `audio` = the fake bridge's measurements (agent_joined, ready_at, first_audible: seconds on the
    bridge's clock, or None)."""
    from onset.speech import word_error_rate

    failures: list[str] = []
    if audio.get("agent_joined") is None:
        failures.append("agent never joined the room")

    # (a) agent audio both exists and came in time
    ready, heard = audio.get("ready_at"), audio.get("first_audible")
    if ready is None:
        failures.append("bridge_ready was never set")
    elif heard is None:
        failures.append("no audible agent audio (nothing above %.0f dBFS)" % AUDIBLE_DBFS)
    elif heard < ready:
        failures.append(f"agent audio {ready - heard:.2f}s BEFORE bridge_ready (must wait for it)")
    elif heard - ready > greet_deadline_s:
        failures.append(f"first agent audio {heard - ready:.2f}s after bridge_ready (limit {greet_deadline_s:.0f} s)")

    # (b) the caller's voice reached the agent and was understood
    res = _speech_result(events)
    if res is None:
        failures.append("no speech result event")
    else:
        data = res.get("data") or {}
        quality = res.get("quality", data.get("quality"))
        detail = data.get("detail") or {}
        if quality != "ok":
            why = detail.get("reason") or detail.get("error") or ""
            failures.append(f"speech quality {quality!r}" + (f" ({why})" if why else ""))
        expected = sentence or data.get("expected") or run_sentence(events)
        transcript = data.get("transcript")
        if not transcript:
            failures.append("speech result has no transcript")
        elif not expected:
            failures.append("no expected sentence to compare with")
        else:
            wer = word_error_rate(expected, transcript)
            reported = data.get("word_error_rate")
            if isinstance(reported, (int, float)):
                wer = max(wer, float(reported))
            if wer > max_wer:
                failures.append(f"transcript {transcript!r} vs {expected!r}: WER {wer:.2f} > {max_wer}")

    # (c) the run finished
    if not any(e.get("type") == "run_ended" for e in events):
        failures.append("no run_ended event")

    # (d) voice activity ended the recording
    if expect_vad:
        vad = next((e for e in reversed(events) if e.get("type") == "vad"), None)
        if vad is None:
            failures.append("no vad event (agent without voice activity, or the step never recorded)")
        elif not vad.get("enabled"):
            failures.append("voice activity was off in the agent")
        elif vad.get("reason") != "speech_end":
            failures.append(f"recording ended by {vad.get('reason')!r}, not speech_end "
                            f"(started_at {vad.get('started_at')}, record_s {vad.get('record_s')})")
    return failures


def lag_warnings(events: list[dict], *, sentence: str | None = None) -> list[str]:
    """Not failures: signs that the agent's speech is behind its captions (agent clock, `say`
    is emitted when a line starts playing)."""
    out: list[str] = []
    t = lambda e: float(e.get("t", 0.0))  # noqa: E731
    says = [e for e in events if e.get("type") == "say"]
    started = next((e for e in events if e.get("type") == "run_started"), None)
    if started is not None and says and t(says[0]) - t(started) > 5.0:
        out.append(f"first line started {t(says[0]) - t(started):.1f}s after the check began")
    sentence = sentence or run_sentence(events)
    go = next((e for e in events if e.get("type") == "step" and str(e.get("caption", "")).startswith("Say it now")), None)
    prompt = next((e for e in says if sentence and sentence in str(e.get("text", ""))), None)
    if go is not None and prompt is not None and t(prompt) > t(go):
        out.append(f"the spoken sentence prompt started {t(prompt) - t(go):.1f}s AFTER 'Say it now' "
                   "(a caller who waits to hear it would be cut off)")
    elif go is not None and prompt is None and says:
        out.append("the sentence prompt was never spoken")
    ended = next((e for e in events if e.get("type") == "run_ended"), None)
    late = [e for e in says if ended is not None and t(e) > t(ended)]
    if late:
        out.append(f"{len(late)} spoken line(s) started after run_ended")
    return out


def summary(events: list[dict], audio: dict, failures: list[str], *, sentence: str | None = None) -> str:
    """The PASS/FAIL block printed at the end (no secrets: only timings, levels, the transcript)."""
    ready, heard = audio.get("ready_at"), audio.get("first_audible")
    lines = ["PASS" if not failures else "FAIL"]
    lines.append("  agent audio: " + ("none" if heard is None or ready is None else
                                      f"first audible {heard - ready:.2f}s after bridge_ready, "
                                      f"{audio.get('audible_s', 0.0):.1f}s audible, peak {audio.get('peak', -120.0):.1f} dBFS"))
    res = _speech_result(events)
    if res is not None:
        data = res.get("data") or {}
        lines.append(f"  caller speech: quality {res.get('quality')}, transcript {data.get('transcript')!r}, "
                     f"WER {data.get('word_error_rate')}, expected {(sentence or data.get('expected'))!r}")
    vad = next((e for e in reversed(events) if e.get("type") == "vad"), None)
    if vad is not None:
        lines.append(f"  vad: {vad.get('reason')} (enabled {vad.get('enabled')}), speech {vad.get('started_at')}"
                     f"..{vad.get('ended_at')} s after 'Say it now', recording {vad.get('record_s')} s")
    ended = next((e for e in events if e.get("type") == "run_ended"), None)
    lines.append(f"  run_ended: {ended.get('reason') if ended else 'MISSING'}")
    for w in lag_warnings(events, sentence=sentence):
        lines.append(f"  WARN: {w}")
    for f in failures:
        lines.append(f"  FAIL: {f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- the call


async def caller_voice(text: str) -> np.ndarray:
    """The sentence as 48 kHz mono int16, spoken by ElevenLabs (the fake caller)."""
    from livekit.agents.utils import http_context
    from livekit.plugins import elevenlabs

    from onset.voice import _resample

    key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    async with http_context.open():
        tts = elevenlabs.TTS(api_key=key, voice_id=CALLER_VOICE)
        chunks, rate = [], 22050
        async with tts.synthesize(text) as stream:
            async for ev in stream:
                rate = ev.frame.sample_rate
                chunks.append(np.frombuffer(bytes(ev.frame.data), np.int16))
        await tts.aclose()
    return _resample(np.concatenate(chunks), rate, 48000)


async def main(a: argparse.Namespace) -> int:
    import cv2
    from livekit import api, rtc

    sys.path.insert(0, str(REPO))
    load_env()
    agent_name = os.environ["AGENT_NAME"]
    url = os.environ["LIVEKIT_URL"]
    if a.assert_:
        a.speak = True

    try:  # a previous run's events would look like this run's "Say it now" / run_ended
        EVENTS.unlink()
    except OSError:
        pass
    lk = api.LiveKitAPI()
    room_name = f"onset-test-{uuid.uuid4().hex[:8]}"
    await lk.room.create_room(api.CreateRoomRequest(name=room_name, empty_timeout=30))
    await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
        agent_name=agent_name, room=room_name,
        metadata=json.dumps({"chert_call": "test", "media": {"audio": True, "video": True}})))
    token = (api.AccessToken().with_identity("chert-bridge-test").with_name("fake bridge")
             .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())

    room = rtc.Room()
    t0 = time.monotonic()
    stamp = lambda: f"{time.monotonic() - t0:6.2f}s"  # noqa: E731
    r = {"agent_joined": None, "ready_at": None, "ready_ack": None, "first_audible": None, "audible_s": 0.0,
         "peak": -120.0, "video": None, "frames": 0, "saved": 0, "segments": []}
    seen: dict[int, float] = {}  # event index -> when this bridge first saw it (its clock)
    tasks: list[asyncio.Task] = []
    frames_dir = Path(a.frames) if a.frames else None
    if frames_dir:
        frames_dir.mkdir(parents=True, exist_ok=True)

    heard_pcm: list[tuple[float, bytes]] = []  # (bridge time, 24 kHz pcm) with --save-audio

    async def listen_audio(track):
        async for ev in rtc.AudioStream(track, sample_rate=24000, num_channels=1):
            s = np.frombuffer(ev.frame.data, dtype=np.int16)
            if a.save_audio:
                heard_pcm.append((time.monotonic() - t0, bytes(ev.frame.data)))
            lv = dbfs(s)
            r["peak"] = max(r["peak"], lv)
            if lv > AUDIBLE_DBFS:
                now = time.monotonic() - t0
                segs = r["segments"]
                if segs and now - segs[-1][1] <= 0.4:
                    segs[-1][1] = now
                else:
                    segs.append([now, now])
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

    await room.connect(url, token)
    print(stamp(), "connected to", room_name)
    vsrc = rtc.VideoSource(1280, 720)
    await room.local_participant.publish_track(
        rtc.LocalVideoTrack.create_video_track("cam", vsrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    asrc = rtc.AudioSource(48000, 1)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    voice = {"pcm": np.zeros(0, np.int16), "task": None, "sentence": a.sentence}
    speak_at: list[float] = []

    async def make_voice(text: str) -> None:
        try:
            voice["pcm"] = await caller_voice(text)
            print(stamp(), f"caller voice ready: {len(voice['pcm']) / 48000:.1f} s of {text!r}")
        except Exception as e:  # noqa: BLE001
            print("could not make the caller's voice:", type(e).__name__, e)

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
            speech = voice["pcm"]
            if speak_at and pos < len(speech):
                chunk = speech[pos:pos + 960]
                if len(chunk) < 960:
                    chunk = np.pad(chunk, (0, 960 - len(chunk)))
                pos += 960
            await asrc.capture_frame(rtc.AudioFrame(chunk.tobytes(), 48000, 1, 960))

    async def watch_events():
        if a.speak and voice["sentence"]:
            voice["task"] = asyncio.create_task(make_voice(voice["sentence"]))
        while True:
            ev = read_events()
            for i in range(len(seen), len(ev)):
                seen[i] = time.monotonic() - t0
            if a.speak and voice["task"] is None:
                said = run_sentence(ev)
                if said:  # the sentence this run asks for (the baseline's, on a repeat run)
                    voice["sentence"] = said
                    voice["task"] = asyncio.create_task(make_voice(said))
            if a.speak and not speak_at and any(e.get("type") == "step" and str(e.get("caption", "")).startswith("Say it now") for e in ev):
                if voice["task"] is not None and not voice["task"].done():
                    await voice["task"]
                speak_at.append(time.monotonic() - t0)
                print(stamp(), "agent says 'Say it now': the fake caller speaks")
            await asyncio.sleep(0.1)

    tasks += [asyncio.create_task(feed_video()), asyncio.create_task(feed_audio()), asyncio.create_task(watch_events())]

    while r["agent_joined"] is None and time.monotonic() - t0 < 20:
        await asyncio.sleep(0.2)
    if r["agent_joined"] is None:
        print("AGENT NEVER JOINED (is agent.py running and registered as", agent_name, "?)")
    else:
        await asyncio.sleep(READY_AFTER)
        # The deadline counts from when we send it (the API call itself can take seconds).
        r["ready_at"] = time.monotonic() - t0
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(
            room=room_name, metadata=json.dumps({"chert_call": "test", "chert_bridge_ready": True})))
        r["ready_ack"] = time.monotonic() - t0
        print(stamp(), f"set chert_bridge_ready (sent at {r['ready_at']:.2f}s)")
        end = time.monotonic() + a.seconds
        while time.monotonic() < end:
            if any(e.get("type") == "run_ended" for e in read_events()):
                # Let the closing line play out: leave after 3.5 s of agent silence (at most 25 s).
                give_up = time.monotonic() + 25
                await asyncio.sleep(1.0)
                while time.monotonic() < give_up:
                    last = r["segments"][-1][1] if r["segments"] else 0.0
                    if time.monotonic() - t0 - last > 3.5:
                        break
                    await asyncio.sleep(0.25)
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
    print("first agent audio:", "NONE" if after is None else f"{after:.2f}s after bridge_ready (limit 10 s)")
    print(f"audible agent audio: {r['audible_s']:.1f}s, peak {r['peak']:.1f} dBFS")
    if a.save_audio and heard_pcm:
        import wave

        with wave.open(a.save_audio, "wb") as w:  # the agent's audio as this bridge heard it
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"".join(p for _, p in heard_pcm))
        print(f"agent audio saved to {a.save_audio} (starts {heard_pcm[0][0]:.2f}s on this clock)")
    events = read_events()
    print("\n=== agent events (runs/live/events.jsonl)")
    for e in events:
        typ = e.get("type")
        if typ == "live":
            continue
        brief = {k: v for k, v in e.items() if k not in ("t", "type", "data", "baseline")}
        if typ == "result":
            data = e.get("data") or {}
            brief = {"check": e.get("check"), "quality": e.get("quality"),
                     "reason": (data.get("detail") or {}).get("reason")}
            if e.get("check") == "speech":
                brief["transcript"] = data.get("transcript")
        print(f"  {e.get('t', 0):6.2f}  {typ:<12} {json.dumps(brief)[:180]}")

    print("\n=== timeline on this bridge's clock (agent audio as heard here; events when first seen, +-0.1 s)")
    rows = [(s0, f"agent audio {s1 - s0:4.1f}s") for s0, s1 in r["segments"]]
    for i, e in enumerate(events):
        if e.get("type") in ("step", "say", "vad", "result", "run_ended") and i in seen:
            what = e.get("caption") or e.get("text") or e.get("reason") or e.get("check") or ""
            rows.append((seen[i], f"{e['type']:<9} {str(what)[:70]}"))
    for at, what in sorted(rows):
        print(f"  {at:6.2f}  {what}")

    if not a.assert_:
        return 0
    expect_vad = os.environ.get("ONSET_SPEECH_VAD", "1").strip() != "0"
    failures = check_run(events, r, sentence=voice["sentence"], expect_vad=expect_vad)
    print("\n=== fake call --assert")
    print(summary(events, r, failures, sentence=voice["sentence"]))
    return 1 if failures else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("seconds", nargs="?", type=float, default=100.0, help="max seconds after bridge_ready")
    p.add_argument("--video", help="video file to use as the caller's camera (looped)")
    p.add_argument("--speak", action="store_true", help="the fake caller says the sentence when asked")
    p.add_argument("--assert", dest="assert_", action="store_true",
                   help="exit 0 only if audio worked both ways (implies --speak)")
    p.add_argument("--sentence", help="what the fake caller says (default: the sentence the agent asks for)")
    p.add_argument("--frames", help="save the agent's video frames here (PNG)")
    p.add_argument("--save-audio", help="save the agent's audio as heard here to this WAV (24 kHz)")
    p.add_argument("--save-every", type=float, default=2.0, help="seconds between saved frames")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
