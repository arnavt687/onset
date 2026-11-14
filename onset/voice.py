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


def chime(seconds: float = 0.7) -> bytes:
    """A soft two-note chime (24 kHz PCM): the last resort when every voice fails, so the call
    still hears Onset within Chert's 10-second greeting deadline (the caption carries the words)."""
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    half = n // 2
    f = np.where(np.arange(n) < half, 660.0, 880.0)
    env = np.minimum(1.0, np.minimum(t, seconds - t) * 20.0)
    return (3000 * env * np.sin(2 * np.pi * f * t)).astype(np.int16).tobytes()


TTS_TIMEOUT_S = float(os.environ.get("ONSET_TTS_TIMEOUT_S", "6"))
# The first line of a call (the greeting) must start within Chert's 10 s of bridge_ready: all
# voices together get this long before the chime plays instead.
FIRST_LINE_BUDGET_S = float(os.environ.get("ONSET_GREETING_BUDGET_S", "5"))


class Speaker:
    """Serial, non-blocking speech for one run.

    `fallbacks` are further TTS engines tried in order when the main one fails or takes longer
    than `timeout_s` for a line; when all fail, a soft chime plays instead (the caption still
    shows the words), so the caller never gets silence where a line should be.
    """

    def __init__(self, tts=None, *, sink=None, play: PlayFn | None = None, enabled: bool = True,
                 fallbacks=(), timeout_s: float | None = None) -> None:
        self.tts, self.sink, self.play, self.enabled = tts, sink, play, enabled and tts is not None
        self.fallbacks = [f for f in fallbacks if f is not None and f is not tts]
        self.timeout_s = TTS_TIMEOUT_S if timeout_s is None else timeout_s
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._first = True
        self._failures: dict[int, int] = {}  # engine id -> failures this run

    # After this many failures in one call an engine is skipped for the rest of it, so a dead
    # provider costs each line nothing instead of a timeout.
    MAX_FAILURES = 2

    async def synth(self, text: str, budget_s: float | None = None) -> bytes:
        """Audio for one line: the cache, the main voice, each fallback, then the chime.
        `budget_s` caps the time for all voices together."""
        loop = asyncio.get_running_loop()
        end = loop.time() + budget_s if budget_s is not None else None
        engines = [self.tts, *self.fallbacks]
        live = [e for e in engines if self._failures.get(id(e), 0) < self.MAX_FAILURES] or engines[-1:]
        for engine in live:
            timeout = self.timeout_s if end is None else min(self.timeout_s, end - loop.time())
            if timeout <= 0.05:
                break
            # Not wait_for: a TTS plugin may keep retrying while it is cancelled, which would
            # hold the line hostage. The abandoned attempt finishes (or fails) on its own.
            task = asyncio.ensure_future(synthesize(engine, text))
            done, _ = await asyncio.wait({task}, timeout=timeout)
            try:
                if not done:
                    task.cancel()
                    task.add_done_callback(lambda t: t.cancelled() or t.exception())
                    raise TimeoutError(f"no audio within {timeout:.1f} s")
                pcm = task.result()
                if pcm:
                    self._failures[id(engine)] = 0
                    return pcm
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - includes the timeout
                self._failures[id(engine)] = self._failures.get(id(engine), 0) + 1
                log.warning("[onset] voice %s failed for %r (%s); trying the next one",
                            type(engine).__module__.split(".")[-2] if "." in type(engine).__module__ else type(engine).__name__,
                            text[:40], type(e).__name__)
        log.error("[onset] every voice failed for %r: playing a chime instead", text[:40])
        return chime()

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
                budget = FIRST_LINE_BUDGET_S if self._first else None
                self._first = False
                pcm = await self.synth(text, budget)
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
