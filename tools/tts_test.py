"""Synthesize Onset's greeting once with the configured TTS and report timing.

Checks the key works before a real call; writes greeting.wav so you can listen to it.
"""

import asyncio
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402  (loads .env.local)


async def main() -> None:
    # Plugins that use HTTP (ElevenLabs) need LiveKit's http context outside a real job.
    from livekit.agents.utils import http_context

    async with http_context.open():
        await synth()


async def synth() -> None:
    tts = agent.make_tts()
    print("engine:", type(tts).__module__, type(tts).__name__, "| model:", getattr(tts, "model", "?"))
    t0 = time.monotonic()
    first = None
    frames = []
    async with tts.synthesize(agent.GREETING) as stream:
        async for ev in stream:
            if first is None:
                first = time.monotonic() - t0
            frames.append(ev.frame)
    total = time.monotonic() - t0
    if not frames:
        raise SystemExit("no audio returned")
    rate, ch = frames[0].sample_rate, frames[0].num_channels
    pcm = b"".join(bytes(f.data) for f in frames)
    secs = len(pcm) / 2 / ch / rate
    out = Path(__file__).resolve().parent.parent / "greeting.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    print(f"first audio after {first:.2f}s, done after {total:.2f}s, {secs:.1f}s of audio at {rate} Hz -> {out.name}")
    await tts.aclose()


asyncio.run(main())
