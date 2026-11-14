"""Onset's voice: synthesize each line once, cache it, save it for the dashboard, play it.

`Speaker.say(text)` never blocks the flow. A worker task takes lines in order:
  1. audio from the cache (runs/tts-cache/, keyed by engine + voice + text) or the TTS engine;
  2. writes the line's WAV into the run directory and emits a `say` event with its path,
     so the laptop dashboard can play it in the room (the caller may not hear Onset);
  3. plays it into the call when a `play` coroutine is given (live calls).

Audio is kept as 24 kHz mono 16-bit PCM, the agent session's output rate.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path

import numpy as np

log = logging.getLogger("onset.voice")

RATE = 24000
CACHE = Path(os.environ.get("ONSET_TTS_CACHE", Path(__file__).resolve().parent.parent / "runs" / "tts-cache"))

PlayFn = Callable[[str, bytes, int], Awaitable[None]]


def _resample(pcm: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or pcm.size == 0:
        return pcm
    n = int(round(pcm.size * dst / src))
    x = np.linspace(0, pcm.size - 1, n)
    return np.interp(x, np.arange(pcm.size), pcm.astype(np.float32)).astype(np.int16)


def tts_identity(tts) -> str:
    parts = [type(tts).__module__, type(tts).__name__]
    for attr in ("model", "voice", "voice_id"):
        v = getattr(tts, attr, None)
        if v is None and hasattr(tts, "_opts"):
            v = getattr(tts._opts, attr, None) or getattr(tts._opts, f"{attr}_id", None)
        if isinstance(v, str):
            parts.append(v)
    return "|".join(parts)


def cache_path(identity: str, text: str) -> Path:
    return CACHE / f"{hashlib.sha1(f'{identity}\n{text}'.encode()).hexdigest()[:20]}.wav"


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


def write_wav(path: Path, pcm: bytes, rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    os.replace(tmp, path)


async def synthesize(tts, text: str) -> bytes:
    """24 kHz mono PCM for `text`, from the cache or the engine."""
    path = cache_path(tts_identity(tts), text)
    if path.is_file():
        return read_wav(path)
    chunks, rate = [], RATE
    async with tts.synthesize(text) as stream:
        async for ev in stream:
            f = ev.frame
            rate = f.sample_rate
            a = np.frombuffer(bytes(f.data), dtype=np.int16)
            if f.num_channels > 1:
                a = a.reshape(-1, f.num_channels).mean(axis=1).astype(np.int16)
            chunks.append(a)
    pcm = _resample(np.concatenate(chunks) if chunks else np.zeros(0, np.int16), rate, RATE).tobytes()
    write_wav(path, pcm)
    return pcm


class Speaker:
    """Serial, non-blocking speech for one run."""

    def __init__(self, tts=None, *, sink=None, play: PlayFn | None = None, enabled: bool = True) -> None:
        self.tts, self.sink, self.play, self.enabled = tts, sink, play, enabled and tts is not None
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="onset-speaker")

    def say(self, text: str) -> None:
        if not text:
            return
        if self._task is None:
            self.start()
        self._queue.put_nowait(text)

    async def warm(self, texts: list[str]) -> None:
        """Fill the cache ahead of the call, so the greeting starts instantly."""
        if not self.enabled:
            return
        for text in texts:
            try:
                await synthesize(self.tts, text)
            except Exception:  # noqa: BLE001
                log.warning("[onset] could not pre-synthesize %r", text[:40], exc_info=True)

    async def _run(self) -> None:
        while True:
            text = await self._queue.get()
            if text is None:
                return
            rel = None
            pcm = b""
            if self.enabled:
                try:
                    pcm = await synthesize(self.tts, text)
                except Exception:  # noqa: BLE001
                    log.exception("[onset] TTS failed for %r", text[:40])
            if pcm and self.sink is not None:
                try:
                    path, rel = self.sink.audio_path()
                    write_wav(path, pcm)
                except Exception:  # noqa: BLE001
                    rel = None
            if self.sink is not None:
                self.sink.emit("say", text=text, audio=rel)
            if pcm and self.play is not None:
                try:
                    await self.play(text, pcm, RATE)
                except Exception:  # noqa: BLE001
                    log.exception("[onset] playback failed")

    async def aclose(self) -> None:
        if self._task is not None:
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._task, 30)
            except Exception:  # noqa: BLE001
                self._task.cancel()
