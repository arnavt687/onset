#!/usr/bin/env python3
"""FaceTime the caregiver and tell them what the check-in found (onset/brief.py).

Chert carries one call per line and cannot add a second person to a call, so this is a second
call from the same line, placed once the person's own call has ended.

    tools/caregiver_call.py --to +15551230002 --patient Alex --caregiver Sam \\
        --reason "The left arm dropped while it was held up" --yes
            place one call now (a real FaceTime call: needs --yes or ONSET_LIVE_ACTIONS=1)
    tools/caregiver_call.py --to +15551230002 --patient Alex
            dry run: show what would be asked of Chert; nothing rings
    tools/caregiver_call.py --to +15551230002 --patient-phone +15551230001 --patient Alex --caregiver Sam --yes
            a GROUP FaceTime call: the person and the caregiver ring together, Onset says what it
            found and leaves them talking. Chert must have switched group calling on for the line,
            and both phones must have joined it.
    tools/caregiver_call.py --fake-bridge --agent-name onset-flowtest --patient Alex --reason "..."
            no Chert at all: this tool plays Chert's side of the call (joins the room, says
            "bridge ready") and reports what the caregiver would hear and see. For testing.
    tools/caregiver_call.py --brief runs/live/caregiver_call.json --wait-room ROOM
            what agent.py runs after an escalation with ONSET_CAREGIVER_CALL=after

What it does, in order (Chert's contract for a call Onset places, docs/outbound.md):
  1. waits for the person's call to end (--wait-room), because the line carries one call;
  2. makes a LiveKit room and sends the Onset agent into it, in briefing mode, silent;
  3. asks Chert to FaceTime the caregiver into that room (`when_busy: queue` if the line is
     still letting go of the last call);
  4. when Chert says the caregiver answered (`bridge_ready`), tells the agent to start talking;
  5. hangs up and deletes the room when the agent has finished or the caregiver hangs up.

Needs LIVEKIT_URL/KEY/SECRET, CHERT_API_KEY, CHERT_PROJECT_ID, and the agent running
(`agent.py dev` or tools/demo.sh). The caregiver's phone must have joined the line
(tools/chert_join.py). Phone numbers are masked in everything this prints.
Exit code: 0 the call was answered and the briefing ran (or a dry run), 1 otherwise, 2 usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from onset import actions, brief, chert_api  # noqa: E402
from onset.patients import mask  # noqa: E402

LIVE = Path(os.environ.get("ONSET_RUNS_DIR", ROOT / "runs")) / "live"
T0 = time.monotonic()


def say(*parts: object) -> None:
    print(f"{time.monotonic() - T0:6.1f}s", *parts, flush=True)


def emit(status: str, detail: str = "", **extra: object) -> None:
    """A `caregiver_call` event for the dashboard and the presentation (appended to the live run)."""
    try:
        path = LIVE / "events.jsonl"
        if not path.is_file():
            return
        last = 0.0
        for line in path.read_text(encoding="utf-8").splitlines()[-3:]:
            try:
                last = max(last, float(json.loads(line).get("t", 0)))
            except (ValueError, TypeError, AttributeError):
                pass
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(last + 0.001, 3), "type": "caregiver_call", "status": status,
                                "detail": detail, **extra}) + "\n")
    except OSError:
        pass


def load_env() -> None:
    try:
        from dotenv import load_dotenv

        for p in (ROOT / ".env.local", Path.home() / "onset" / ".env.local"):
            if p.is_file():
                load_dotenv(p, override=False)
    except ImportError:
        pass


def info_from_args(a: argparse.Namespace) -> tuple[brief.BriefInfo, str]:
    if a.brief:
        data = json.loads(Path(a.brief).read_text(encoding="utf-8"))
        a.patient_phone = str(data.get("patient_to") or getattr(a, "patient_phone", "") or "").strip()
        return brief.BriefInfo.from_meta(data), str(data.get("to") or a.to or "").strip()
    meta = {"patient": a.patient, "caregiver": a.caregiver, "outcome": a.outcome, "reasons": a.reason or [],
            "when": time.strftime("%a %-d %b at %-I:%M %p"), "run_id": f"manual-{uuid.uuid4().hex[:8]}",
            "group": bool(a.patient_phone)}
    return brief.BriefInfo.from_meta(meta), (a.to or "").strip()


def is_agent(p, api) -> bool:  # noqa: ANN001
    try:
        if p.kind == api.ParticipantInfo.Kind.AGENT:
            return True
    except AttributeError:
        pass
    return p.identity.startswith("agent-")


async def participants(lk, api, room: str) -> list:  # noqa: ANN001
    try:
        return list((await lk.room.list_participants(api.ListParticipantsRequest(room=room))).participants)
    except Exception:  # noqa: BLE001 - the room is gone
        return []


async def wait_call_over(lk, api, room: str, seconds: float) -> None:  # noqa: ANN001
    """Until the person's FaceTime call has left `room` (the line is then free, or about to be)."""
    say(f"waiting for the person's call to end (up to {seconds:.0f} s)")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not any(p.identity.startswith("chert-bridge-") for p in await participants(lk, api, room)):
            say("the person's call has ended")
            return
        await asyncio.sleep(2.0)
    say("the person's call is still going: placing the caregiver call anyway (Chert queues it)")


async def fake_bridge(lk, api, room: str, seconds: float) -> dict:  # noqa: ANN001
    """Play Chert's side: join as the bridge, say "bridge ready", and record what Onset sends."""
    import numpy as np
    from livekit import rtc

    token = chert_api.mint_bridge_token(room, api_key=os.environ["LIVEKIT_API_KEY"], api_secret=os.environ["LIVEKIT_API_SECRET"])
    r = rtc.Room()
    heard: list[np.ndarray] = []
    frames = {"n": 0, "best": None, "bright": -1.0}
    tasks: list[asyncio.Task] = []

    async def audio(track) -> None:  # noqa: ANN001
        async for ev in rtc.AudioStream(track, sample_rate=16000, num_channels=1):
            heard.append(np.frombuffer(ev.frame.data, dtype=np.int16).copy())

    async def video(track) -> None:  # noqa: ANN001
        async for ev in rtc.VideoStream(track):
            frames["n"] += 1
            if frames["n"] % 8 == 0:  # keep the brightest frame seen: the last one is black, after the hang-up
                rgba = ev.frame.convert(rtc.VideoBufferType.RGBA)
                img = np.frombuffer(rgba.data, dtype=np.uint8).reshape(rgba.height, rgba.width, 4)
                bright = float(img[..., :3].mean())
                if bright > frames["bright"]:
                    frames["bright"], frames["best"] = bright, img.copy()

    @r.on("track_subscribed")
    def _sub(track, pub, p) -> None:  # noqa: ANN001
        tasks.append(asyncio.create_task((audio if track.kind == rtc.TrackKind.KIND_AUDIO else video)(track)))

    await r.connect(os.environ["LIVEKIT_URL"], token)
    say("fake bridge joined as", r.local_participant.identity)
    await asyncio.sleep(3.0)  # the phone "rings"
    await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(
        room=room, metadata=json.dumps({"chert_call": "fake", "chert_bridge_ready": True})))
    say("bridge_ready set: the caregiver has answered")
    t_ready = time.monotonic()
    while time.monotonic() - t_ready < seconds:
        if not any(is_agent(p, api) for p in await participants(lk, api, room)):
            break
        await asyncio.sleep(1.0)
    for t in tasks:
        t.cancel()
    pcm = np.concatenate(heard) if heard else np.zeros(1, np.int16)
    win = 16000 // 10
    loud = sum(1 for i in range(0, len(pcm) - win, win) if np.sqrt(np.mean(pcm[i:i + win].astype(np.float64) ** 2)) > 250)
    out = {"audio_s": round(len(pcm) / 16000, 1), "audible_s": round(loud / 10, 1), "video_frames": frames["n"]}
    img = frames["best"]
    out["picture"] = img is not None and frames["bright"] > 12.0  # not a black frame
    if img is not None:
        try:
            import cv2

            shot = ROOT / "runs" / "brief-frame.png"
            cv2.imwrite(str(shot), cv2.cvtColor(img, cv2.COLOR_RGBA2BGR))
            out["frame"] = f"{img.shape[1]}x{img.shape[0]}, saved to {shot}"
        except Exception as e:  # noqa: BLE001
            out["frame"] = f"not saved ({type(e).__name__})"
    await r.disconnect()
    return out


async def run(a: argparse.Namespace) -> int:
    from livekit import api

    info, to = info_from_args(a)
    gate = actions.gate("call", target=to or None)
    real = (a.yes or gate.live) and not a.fake_bridge
    if not a.fake_bridge and not to:
        say("no phone to call: pass --to +15551234567")
        return 2
    group = [a.patient_phone, to] if (a.patient_phone and to) else None
    target = (" and ".join(mask(x) for x in group) + " together") if group else mask(to) if to else "(fake bridge)"
    if a.fake_bridge and info.group:
        target = "(fake bridge, group)"

    if not real and not a.fake_bridge:  # a dry run: show the request, touch nothing
        res = await chert_api.place_check_in_call(to, dry_run=True, when_busy="queue", recipients=group)
        say(f"DRY RUN ({gate.reason}): would FaceTime {target} and say:")
        for ln in brief.script(info):
            print("        ", ln.say)
        if res.get("request"):
            print("        ", res["request"]["method"], res["request"]["url"])
        emit("dry_run", gate.reason, target=target)
        return 0

    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if not os.environ.get(name):
            say(f"{name} is not set")
            return 2
    agent_name = a.agent_name or os.environ.get("AGENT_NAME") or "onset"
    room = f"{brief.ROOM_PREFIX}{uuid.uuid4().hex[:10]}"
    lk = api.LiveKitAPI()
    call_id = None
    answered = False
    try:
        if a.wait_room:
            await wait_call_over(lk, api, a.wait_room, a.wait_seconds)
        await lk.room.create_room(api.CreateRoomRequest(name=room, empty_timeout=120, max_participants=4))
        await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
            agent_name=agent_name, room=room, metadata=info.to_json()))
        say(f"room {room}: waiting for the Onset agent \"{agent_name}\" to join and publish")
        agent = None
        for _ in range(60):
            agent = next((p for p in await participants(lk, api, room) if is_agent(p, api) and len(p.tracks) >= 2), None)
            if agent:
                break
            await asyncio.sleep(0.5)
        if not agent:
            say("the agent never joined: is it running under that name (agent.py dev, tools/demo.sh)?")
            emit("failed", "the Onset agent did not join the briefing room", target=target)
            return 1
        say("the agent is in the room, silent, with its voice and picture ready")

        if a.fake_bridge:
            r = await fake_bridge(lk, api, room, a.seconds)
            say(f"heard {r['audible_s']} s of audible speech in {r['audio_s']} s of audio; {r['video_frames']} video frames"
                + (f" ({r['frame']})" if r.get("frame") else ""))
            ok = r["audible_s"] >= 3.0 and r["video_frames"] > 5 and r["picture"]
            say("PASS: the caregiver would hear and see the briefing" if ok else "FAIL: too little audio or video reached the bridge")
            return 0 if ok else 1

        emit("placing", "asking Chert to FaceTime the caregiver", target=target)
        key = chert_api.new_idempotency_key("call")

        async def place() -> dict:
            token = chert_api.mint_bridge_token(room, api_key=os.environ["LIVEKIT_API_KEY"],
                                                api_secret=os.environ["LIVEKIT_API_SECRET"], ttl_s=900)
            return await chert_api.place_check_in_call(to, dry_run=False, livekit_url=os.environ["LIVEKIT_URL"],
                                                       participant_token=token, remote_participant_identity=agent.identity,
                                                       video=not a.no_video, when_busy="queue", idempotency_key=key,
                                                       recipients=group)

        res = await place()
        waited = 0.0
        while res.get("status") == "queued" and waited < 150:
            say("the line is still busy: queued", f"(position {res.get('position')})" if res.get("position") else "")
            emit("queued", "waiting for the line to be free", target=target)
            q = {}
            while waited < 150:
                await asyncio.sleep(2.0)
                waited += 2.0
                q = await chert_api.get_call_queue(str(res.get("queue_id")))
                if q.get("ready") or q.get("terminal") or q.get("status") == "error":
                    break
            if not q.get("ready"):
                say("the queued call did not become ready:", q.get("queue_state") or q.get("error"))
                emit("failed", f"the queued call did not go through ({q.get('queue_state') or q.get('error')})", target=target)
                return 1
            res = await place()  # the same key, a fresh token
        if res.get("status") != "allocated":
            say(f"Chert did not place the call: {res.get('status')} {res.get('error') or ''}")
            if res.get("detail"):
                print("        ", res["detail"])
            if res.get("request_id"):
                print("         Chert request id:", res["request_id"])
            emit("failed", str(res.get("detail") or res.get("error") or res.get("status")), target=target, error=res.get("error"))
            return 1
        call_id = res["call_id"]
        say(f"Chert is calling {target} (call {str(call_id)[:8]})")
        emit("ringing", "Onset is ringing them both" if group else "Onset is FaceTiming the caregiver", target=target)

        end = time.monotonic() + a.ring_seconds
        while time.monotonic() < end:
            c = await chert_api.get_call(call_id)
            if c.get("bridge_ready"):
                answered = True
                break
            if c.get("terminal"):
                say("the call ended before it was answered:", c.get("call_status"), c.get("terminal_reason") or "")
                emit("failed", f"not answered ({c.get('terminal_reason') or c.get('call_status')})", target=target)
                return 1
            await asyncio.sleep(0.5)
        if not answered:
            say(f"no answer within {a.ring_seconds:.0f} s")
            emit("failed", "the caregiver did not answer", target=target)
            return 1
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(
            room=room, metadata=json.dumps({"chert_call": call_id, "chert_bridge_ready": True})))
        say("the caregiver answered: Onset is briefing them")
        emit("answered", "the caregiver answered: Onset is telling them what it found", target=target)

        end = time.monotonic() + a.seconds
        while time.monotonic() < end:
            if not any(is_agent(p, api) for p in await participants(lk, api, room)):
                break  # the briefing is over (or the caregiver hung up and the agent left)
            c = await chert_api.get_call(call_id)
            if c.get("terminal"):
                break
            await asyncio.sleep(1.5)
        say("Onset has said its part and left; the two of them can keep talking on FaceTime" if group
            else "the briefing call is over")
        emit("ended", "Onset left the group call; they can keep talking" if group else "the caregiver call ended", target=target)
        return 0
    finally:
        if call_id:
            try:
                await chert_api.end_call(call_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room))
        except Exception:  # noqa: BLE001
            pass
        await lk.aclose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="FaceTime the caregiver and tell them what the check-in found.")
    p.add_argument("--to", help="the caregiver's phone, E.164 (+15551234567)")
    p.add_argument("--brief", help="a briefing file written by agent.py (has the phone and what was found)")
    p.add_argument("--patient", default="", help="the person's first name")
    p.add_argument("--caregiver", default="", help="the caregiver's first name")
    p.add_argument("--outcome", default="escalate", choices=("escalate", "unclear"))
    p.add_argument("--reason", action="append", help="what Onset measured, one short sentence (repeatable)")
    p.add_argument("--patient-phone", default="", help="the person's own phone: makes it a GROUP call with both of them")
    p.add_argument("--no-video", action="store_true", help="audio only (group video is enabled separately by Chert)")
    p.add_argument("--yes", action="store_true", help="place a real FaceTime call (otherwise only when ONSET_LIVE_ACTIONS=1)")
    p.add_argument("--wait-room", help="first wait for the person's call in this LiveKit room to end")
    p.add_argument("--wait-seconds", type=float, default=600.0)
    p.add_argument("--ring-seconds", type=float, default=75.0, help="how long to wait for an answer")
    p.add_argument("--seconds", type=float, default=120.0, help="the longest the briefing may take")
    p.add_argument("--agent-name", help="the agent to send into the room (default AGENT_NAME)")
    p.add_argument("--fake-bridge", action="store_true", help="no Chert: play its side and report what would be heard and seen")
    a = p.parse_args(argv)
    load_env()
    try:
        return asyncio.run(run(a))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
