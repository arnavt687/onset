"""A small energy-based voice-activity detector for the sentence step (numpy only).

Feed it 16-bit mono PCM chunks, at any sample rate, with the clock time of each chunk:

    vad = VoiceActivity()
    vad.feed(pcm, sample_rate, t)      # as audio arrives
    vad.active                         # speech in progress (or about to be confirmed)
    vad.ended                          # speech happened and has now stopped
    vad.started_at, vad.ended_at       # clock times of the speech segment

How it decides:
  * the audio is cut into 20 ms frames; each frame's level is the energy between 250 and
    4000 Hz (a Hann-windowed FFT), in dB relative to full scale. Leaving out everything
    below 250 Hz makes DC offsets and mains hum (50/60 Hz and its first harmonics) invisible;
  * a noise floor follows the quiet frames: it drops quickly when the room gets quieter and
    rises slowly (a few dB a second) when it gets louder, so a constant hum or fan becomes
    the floor instead of "speech";
  * a frame is loud when it is `margin_db` above the floor and above `min_db` (so digital
    silence, -120 dBFS, and very quiet rooms are never speech);
  * speech starts after `start_s` of loud frames (gaps under `gap_reset_s` allowed) and
    ends after `end_silence_s` without a loud frame. A segment shorter than `min_speech_s`
    (a cough, a click) is dropped and the detector waits for real speech again.

Times are on the caller's clock (whatever `t` is). Every chunk is placed at its own `t`, so
a clock that drifts against the sample count does not accumulate error.
"""

from __future__ import annotations

import math

import numpy as np

FRAME_S = 0.02
BAND_HZ = (250.0, 4000.0)
FULL_SCALE = 32768.0
SILENT_DB = -120.0


def band_levels(samples: np.ndarray, sample_rate: int, frame_len: int,
                band: tuple[float, float] = BAND_HZ) -> np.ndarray:
    """dBFS of the in-band energy of each whole frame (samples: 1-D int16 or float)."""
    n = len(samples) // frame_len
    if n == 0:
        return np.zeros(0)
    frames = samples[: n * frame_len].astype(np.float64).reshape(n, frame_len)
    win = np.hanning(frame_len) if frame_len > 1 else np.ones(1)
    spec = np.fft.rfft(frames * win, axis=1)
    freqs = np.fft.rfftfreq(frame_len, 1.0 / sample_rate)
    lo, hi = band[0], min(band[1], sample_rate / 2)
    sel = (freqs >= lo) & (freqs <= hi)
    # Parseval with the window's energy: mean-square power of the in-band part of the frame.
    power = 2.0 * (np.abs(spec[:, sel]) ** 2).sum(axis=1) / (frame_len * float((win**2).sum()))
    with np.errstate(divide="ignore"):
        db = 10.0 * np.log10(power / FULL_SCALE**2)
    return np.maximum(db, SILENT_DB)


class VoiceActivity:
    """Speech start / speech end from a stream of 16-bit mono PCM chunks."""

    def __init__(
        self,
        *,
        min_speech_s: float = 0.6,  # a segment shorter than this is not "speech ended"
        end_silence_s: float = 0.9,  # this much silence after speech ends it
        start_s: float = 0.1,  # loud time needed to call it speech
        gap_reset_s: float = 0.25,  # a quiet gap this long before start_s forgets the candidate
        margin_db: float = 9.0,  # loud = this far above the noise floor ...
        min_db: float = -55.0,  # ... and above this absolute level (in-band dBFS)
        floor_down_tau_s: float = 0.08,  # the floor follows a quieter room quickly
        floor_rise_db_s: float = 4.0,  # and a louder one slowly, while it is quiet
        floor_rise_speech_db_s: float = 1.0,  # and slower still during loud frames
        frame_s: float = FRAME_S,
    ) -> None:
        self.min_speech_s, self.end_silence_s = min_speech_s, end_silence_s
        self.start_s, self.gap_reset_s = start_s, gap_reset_s
        self.margin_db, self.min_db = margin_db, min_db
        self.floor_down_tau_s = floor_down_tau_s
        self.floor_rise_db_s, self.floor_rise_speech_db_s = floor_rise_db_s, floor_rise_speech_db_s
        self.frame_s = frame_s
        self.floor_db: float | None = None
        self.level_db = SILENT_DB
        self.frames = 0
        self._rate: int | None = None
        self._rest = np.zeros(0, np.int16)
        self.reset()

    # ------------------------------------------------------------------ state

    def reset(self) -> None:
        """Forget the current speech segment; keep the learned noise floor."""
        self.state = "idle"  # idle | speech | ended
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.voiced_s = 0.0
        self._cand_start: float | None = None
        self._cand_loud = 0.0
        self._last_loud_end: float | None = None

    @property
    def in_speech(self) -> bool:
        return self.state == "speech"

    @property
    def active(self) -> bool:
        """Speech in progress, or loud frames that may become speech."""
        return self.state == "speech" or (self.state == "idle" and self._cand_start is not None)

    @property
    def ended(self) -> bool:
        return self.state == "ended"

    @property
    def speech_s(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.ended_at if self.ended_at is not None else self._last_loud_end
        return None if end is None else max(0.0, end - self.started_at)

    # ------------------------------------------------------------------ input

    def feed(self, pcm: bytes, sample_rate: int, t: float) -> None:
        """One chunk of 16-bit mono PCM that starts at clock time `t`."""
        rate = int(sample_rate)
        if rate <= 0:
            return
        samples = np.frombuffer(bytes(pcm)[: len(pcm) // 2 * 2], dtype="<i2")
        if rate != self._rate:
            self._rate, self._rest = rate, np.zeros(0, np.int16)
        frame_len = max(1, int(round(rate * self.frame_s)))
        buf = np.concatenate([self._rest, samples]) if len(self._rest) else samples
        start = t - len(self._rest) / rate
        levels = band_levels(buf, rate, frame_len)
        self._rest = np.array(buf[len(levels) * frame_len:], dtype=np.int16)
        fd = frame_len / rate
        for i, db in enumerate(levels):
            self._frame(float(db), start + i * fd, fd)

    def _frame(self, db: float, ft: float, fd: float) -> None:
        self.frames += 1
        self.level_db = db
        if self.floor_db is None:
            self.floor_db = db
        loud = db >= max(self.floor_db + self.margin_db, self.min_db)

        # The noise floor: fast down, slow up (slower still while someone may be talking).
        if db < self.floor_db:
            self.floor_db += (db - self.floor_db) * min(1.0, fd / self.floor_down_tau_s)
        else:
            rise = self.floor_rise_speech_db_s if loud else self.floor_rise_db_s
            self.floor_db += min(db - self.floor_db, rise * fd)

        end = ft + fd
        if self.state == "idle":
            if loud:
                if self._cand_start is None:
                    self._cand_start, self._cand_loud = ft, 0.0
                self._cand_loud += fd
                self._last_loud_end = end
                if self._cand_loud >= self.start_s - 1e-9:
                    self.state, self.started_at, self.voiced_s = "speech", self._cand_start, self._cand_loud
            elif self._cand_start is not None and self._last_loud_end is not None \
                    and end - self._last_loud_end >= self.gap_reset_s:
                self._cand_start, self._cand_loud = None, 0.0
        elif self.state == "speech":
            if loud:
                self._last_loud_end = end
                self.voiced_s += fd
            elif self._last_loud_end is not None and end - self._last_loud_end >= self.end_silence_s - 1e-9:
                if self._last_loud_end - (self.started_at or 0.0) >= self.min_speech_s - 1e-9:
                    self.state, self.ended_at = "ended", self._last_loud_end
                else:  # a cough or a click: wait for real speech
                    self.reset()

    def summary(self) -> dict:
        """Plain numbers for logs and events."""
        def r(x):
            return None if x is None or not math.isfinite(x) else round(x, 3)

        return {"state": self.state, "started_at": r(self.started_at), "ended_at": r(self.ended_at),
                "speech_s": r(self.speech_s), "voiced_s": r(self.voiced_s), "floor_db": r(self.floor_db)}
