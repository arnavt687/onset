"""Replay a recorded call through the real check-in flow, as if it were live.

A call recorded with ONSET_RECORD=1 (agent.py) has one step, `call`, holding every caller
frame and the caller's audio on the flow's clock, plus marks for each step. This tool feeds
them into a fresh `CheckFlow` and writes the same event stream, frames and spoken audio to
runs/live/ that a live call writes, so the dashboard shows the replay exactly like a call.

    .venv/bin/python tools/replay_flow.py recordings/<call-id>            # real-time pacing
    .venv/bin/python tools/replay_flow.py recordings/<call-id> --fast     # as fast as possible
    .venv/bin/python tools/replay_flow.py recordings/<call-id> --no-voice # no TTS
    ... --patient demo-replay   # which baseline to compare with / create (default: ONSET_PATIENT_ID)

The flow re-runs every measurement on the recorded frames, so its timing follows the
recording: steps begin when the flow decides, not when the original call's steps began.
A replay never texts a caregiver: the escalation is always a dry run (logged as an `action`
event), even with ONSET_LIVE_ACTIONS=1.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env.local")

from onset.events import EventSink  # noqa: E402
from onset.flow import CheckFlow  # noqa: E402
from onset.voice import Speaker  # noqa: E402
from tools.replay import load  # noqa: E402

FPS = 15


def _audio_chunks(rec_step, chunk_s: float = 0.02):
    """(t, pcm) chunks of the recorded caller audio, on the recording's clock."""
    clip = rec_step.audio()
    if clip is None:
        return [], 16000
    pcm = np.frombuffer(clip.pcm, np.int16)
    n = int(clip.sample_rate * chunk_s)
    return [(clip.t_start + i / clip.sample_rate, pcm[i:i + n].tobytes()) for i in range(0, len(pcm), n)], clip.sample_rate


async def replay(path: str, *, fast: bool, voice: bool, patient: str | None, step: str = "call",
                 runs_root: Path | None = None, baseline_dir: Path | None = None) -> CheckFlow:
    rec = load(path)
    if step not in rec:
        raise SystemExit(f"{path} has no '{step}' step (has: {', '.join(s.name for s in rec)}); "
                         "record with the current agent.py (ONSET_RECORD=1)")
    s = rec[step]
    frames = list(s.frames())
    audio, rate = _audio_chunks(s)
    run_id = f"replay-{Path(path).name}"
    sink = EventSink(run_id, "replay", **({"root": runs_root} if runs_root else {}))

    tts = None
    if voice:
        try:
            from agent import make_tts

            tts = make_tts()
        except Exception as e:  # noqa: BLE001
            print("voice off:", e)
    speaker = Speaker(tts, sink=sink, enabled=tts is not None)
    flow = CheckFlow(sink=sink, say=speaker.say if tts is not None else None, patient_id=patient,
                     source="replay", run_id=run_id, baseline_dir=baseline_dir,
                     caregiver_phone=os.environ.get("ONSET_CAREGIVER_PHONE", ""), caregiver_send=False)

    # The recording starts at the flow's start (bridge_ready); replay on the same clock.
    marks = {m["name"]: m["t"] for m in s.marks}
    t_start = marks.get("greet", 0.0)
    clock0 = time.monotonic()
    flow.start(t_start)
    speaker.start()
    ai = 0
    last = frames[-1].t if frames else t_start
    t = t_start
    fi = 0
    while not flow.done:
        if fi < len(frames):
            f = frames[fi]
            t = f.t
            fi += 1
        else:  # past the end of the recording: keep the clock running so timers fire
            t += 1 / FPS
            f = None
        if not fast:
            await asyncio.sleep(max(0.0, (t - t_start) - (time.monotonic() - clock0)))
        if f is not None:
            await asyncio.to_thread(flow.on_frame, f.rgb, t)
        while ai < len(audio) and audio[ai][0] <= t:
            flow.on_audio(audio[ai][1], rate, audio[ai][0])
            ai += 1
        flow.tick(t)
        if flow.needs_evaluation:
            await flow.evaluate()
        flow.render(f.rgb if f is not None else None, t)
        if t > last + 120:  # safety: the flow caps itself at 90 s
            flow.abort("replay ran out")
            break
    await flow.wait_background()
    await speaker.aclose()
    sink.close("replay complete")
    return flow


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("recording")
    p.add_argument("--fast", action="store_true", help="do not pace in real time")
    p.add_argument("--no-voice", action="store_true", help="no TTS (no audio files for the dashboard)")
    p.add_argument("--patient", default=os.environ.get("ONSET_PATIENT_ID"), help="patient id / baseline to use")
    p.add_argument("--step", default="call", help="recorded step holding the whole call (default: call)")
    a = p.parse_args(argv)

    async def go():
        if not a.no_voice:
            from livekit.agents.utils import http_context

            async with http_context.open():
                return await replay(a.recording, fast=a.fast, voice=True, patient=a.patient, step=a.step)
        return await replay(a.recording, fast=a.fast, voice=False, patient=a.patient, step=a.step)

    flow = asyncio.run(go())
    for e in flow.events:
        if e["type"] in ("step", "result", "decision", "caregiver", "run_ended"):
            extra = e.get("name") or e.get("check") or e.get("outcome") or e.get("status") or e.get("reason")
            q = e.get("quality")
            print(f"{e['type']:<10} {getattr(extra, 'value', extra)}{'' if q is None else ' ' + getattr(q, 'value', str(q))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
